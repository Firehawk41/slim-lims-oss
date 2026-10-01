import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app as app_module
from app import app
from slim_report_engine.cli import (
    ReportEngineError,
    UnresolvedChemicalReportError,
    UnresolvedCustomerReportError,
    UnresolvedReportFolderReportError,
)

client = TestClient(app)


class _FakeCustomer:
    def __init__(self, id: int, name: str) -> None:
        self.id = id
        self.name = name


class _FakeChemical:
    def __init__(self, id: int, name: str) -> None:
        self.id = id
        self.name = name


class _FakeElement:
    def __init__(self, id: int, name: str, symbol: str) -> None:
        self.id = id
        self.name = name
        self.symbol = symbol


class _FakeElementService:
    def __init__(self) -> None:
        self.elements = [_FakeElement(1, "Copper", "Cu"), _FakeElement(2, "Titanium", "Ti")]

    def all_elements(self):
        return self.elements


class _FakeCustomerService:
    metals_prep_options = ()  # unused, present for interface parity

    def __init__(self) -> None:
        self.customers = [_FakeCustomer(1, "Customer 130"), _FakeCustomer(2, "Beta Inc")]
        self.created_calls: list[tuple] = []
        self.form_key_calls: list[tuple] = []

    def all_customers(self):
        return self.customers

    def get_by_name(self, name):
        return next((c for c in self.customers if c.name.lower() == name.lower()), None)

    def create_customer(self, name, street_address, city, state, postal_code, country, created_by=""):
        if not name.strip():
            raise ValueError("Customer name cannot be blank")
        if not created_by.strip():
            raise ValueError("Initials cannot be blank")
        customer = _FakeCustomer(99, name)
        self.customers.append(customer)
        self.created_calls.append((name, street_address, city, state, postal_code, country, created_by))
        return customer

    def add_form_key(self, form_customer, form_address, form_address2, customer_id, created_by=""):
        if not created_by.strip():
            raise ValueError("Initials cannot be blank")
        self.form_key_calls.append((form_customer, form_address, form_address2, customer_id, created_by))


class _FakeChemicalService:
    metals_prep_options = ("N/A", "Evaporation")
    silicon_prep_options = ("N/A", "Evaporation")
    ions_prep_options = ("N/A", "Evaporation")

    def __init__(self) -> None:
        self.chemicals = [_FakeChemical(1, "Test Acid")]
        self.created_calls: list[tuple] = []
        self.form_name_calls: list[tuple] = []

    def all_chemicals(self):
        return self.chemicals

    def get_by_name(self, name):
        return next((c for c in self.chemicals if c.name.lower() == name.lower()), None)

    def create_chemical(self, name, metals_prep, silicon_prep, ions_prep, ked_element_ids=frozenset(), created_by=""):
        if not name.strip():
            raise ValueError("Chemical name cannot be blank")
        if not created_by.strip():
            raise ValueError("Initials cannot be blank")
        chemical = _FakeChemical(88, name)
        self.chemicals.append(chemical)
        self.created_calls.append((name, metals_prep, silicon_prep, ions_prep, ked_element_ids, created_by))
        return chemical

    def add_form_name(self, form_name, chemical_id, created_by=""):
        if not created_by.strip():
            raise ValueError("Initials cannot be blank")
        self.form_name_calls.append((form_name, chemical_id, created_by))


class _FakeReportFolderService:
    def __init__(self) -> None:
        self.folders: dict[tuple, str] = {}
        self.set_folder_calls: list[tuple] = []
        self.test_root = None

    def set_folder(self, customer_id, folder_path, created_by="", location=""):
        if not created_by.strip():
            raise ValueError("Initials cannot be blank")
        if not folder_path.strip():
            raise ValueError("Not a real, accessible folder: (blank)")
        self.folders[(customer_id, location)] = folder_path
        self.set_folder_calls.append((customer_id, folder_path, created_by, location))


class _FakeServices:
    def __init__(self) -> None:
        self.customer_svc = _FakeCustomerService()
        self.chemical_svc = _FakeChemicalService()
        self.report_folder_svc = _FakeReportFolderService()
        self.element_svc = _FakeElementService()


@pytest.fixture
def fake_services(monkeypatch):
    services = _FakeServices()
    monkeypatch.setattr(app_module, "_services", services)
    return services


@pytest.fixture(autouse=True)
def _auth_disabled_by_default(monkeypatch):
    """Real .env (picked up by the same load_dotenv() that finds DB_URL)
    now sets a real API_KEY -- without this, every test below would
    suddenly need the header just because it happens to run from this
    directory. Tests that actually want to exercise auth override this
    themselves (see test_generate_report_requires_api_key_when_configured)."""
    monkeypatch.setattr(app_module, "API_KEY", None)


@pytest.fixture(autouse=True)
def _testing_requests_root_is_tmp_path(monkeypatch, tmp_path):
    """Every test's input file lives under pytest's own tmp_path, not the
    real S:\\Testing_Requests -- without this, the real-path-containment
    check added for the "arbitrary file read" fix would reject every
    single test's input file. Tests that specifically want to exercise
    the containment check override this themselves."""
    monkeypatch.setattr(app_module, "TESTING_REQUESTS_ROOT", tmp_path)


def test_health():
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert "test_mode" in body
    assert body["version"] == app_module.APP_VERSION


def test_health_reports_test_mode_from_services(fake_services):
    fake_services.report_folder_svc.test_root = Path("/some/sandbox")
    assert client.get("/health").json()["test_mode"] is True


def test_generate_report_missing_input_file(tmp_path):
    missing = tmp_path / "does_not_exist.xlsx"
    response = client.post("/generate-report", json={"input_path": str(missing)})
    assert response.status_code == 422
    assert "not found" in response.json()["detail"]


def test_generate_report_rejects_path_outside_testing_requests_root(tmp_path, monkeypatch):
    """Confirmed real gap (direct user question, 2026-09-17): without
    this, /generate-report would happily try to open any path the caller
    supplies, not just something under the real Testing Requests tree."""
    real_root = tmp_path / "Testing_Requests"
    real_root.mkdir()
    monkeypatch.setattr(app_module, "TESTING_REQUESTS_ROOT", real_root)

    outside = tmp_path / "elsewhere" / "not_a_tr_form.xlsx"
    outside.parent.mkdir()
    outside.write_text("stub")

    response = client.post("/generate-report", json={"input_path": str(outside)})
    assert response.status_code == 422
    assert "must be under" in response.json()["detail"]


def test_generate_report_rejects_sibling_path_that_merely_shares_a_prefix(tmp_path, monkeypatch):
    """Guards against a naive string-prefix check: "Testing_Requests-evil"
    starts with the same characters as "Testing_Requests" but is NOT
    nested under it."""
    real_root = tmp_path / "Testing_Requests"
    real_root.mkdir()
    monkeypatch.setattr(app_module, "TESTING_REQUESTS_ROOT", real_root)

    evil = tmp_path / "Testing_Requests-evil" / "form.xlsx"
    evil.parent.mkdir()
    evil.write_text("stub")

    response = client.post("/generate-report", json={"input_path": str(evil)})
    assert response.status_code == 422
    assert "must be under" in response.json()["detail"]


def test_generate_report_accepts_path_under_testing_requests_root(tmp_path, monkeypatch):
    real_root = tmp_path / "Testing_Requests"
    real_root.mkdir()
    monkeypatch.setattr(app_module, "TESTING_REQUESTS_ROOT", real_root)

    inside = real_root / "Customer 130" / "form.xlsx"
    inside.parent.mkdir()
    inside.write_text("stub")

    def fake_run(input_path: Path, output_path, db_url=None, services=None, info=None) -> Path:
        return inside

    monkeypatch.setattr(app_module, "run", fake_run)

    response = client.post("/generate-report", json={"input_path": str(inside)})
    assert response.status_code == 200


def test_generate_report_logs_hook_version_when_provided(tmp_path, monkeypatch, caplog):
    import logging

    real_root = tmp_path / "Testing_Requests"
    real_root.mkdir()
    monkeypatch.setattr(app_module, "TESTING_REQUESTS_ROOT", real_root)

    inside = real_root / "Customer 130" / "form.xlsx"
    inside.parent.mkdir()
    inside.write_text("stub")

    monkeypatch.setattr(app_module, "run", lambda input_path, output_path, db_url=None, services=None, info=None: inside)

    with caplog.at_level(logging.INFO):
        response = client.post(
            "/generate-report", json={"input_path": str(inside), "hook_version": "1.0.0"}
        )

    assert response.status_code == 200
    assert any("hook v1.0.0" in r.getMessage() for r in caplog.records)


def test_generate_report_missing_hook_version_logs_unknown_not_an_error(tmp_path, monkeypatch, caplog):
    # An older, not-yet-updated VBA hook won't send hook_version at all --
    # must not break the request, just log "unknown" for that field.
    import logging

    real_root = tmp_path / "Testing_Requests"
    real_root.mkdir()
    monkeypatch.setattr(app_module, "TESTING_REQUESTS_ROOT", real_root)

    inside = real_root / "Customer 130" / "form.xlsx"
    inside.parent.mkdir()
    inside.write_text("stub")

    monkeypatch.setattr(app_module, "run", lambda input_path, output_path, db_url=None, services=None, info=None: inside)

    with caplog.at_level(logging.INFO):
        response = client.post("/generate-report", json={"input_path": str(inside)})

    assert response.status_code == 200
    assert any("hook vunknown" in r.getMessage() for r in caplog.records)


def test_generate_report_translates_mapped_drive_before_containment_check(tmp_path, monkeypatch):
    """Confirmed real, not theoretical (2026-09-22): VBA always sends
    whatever ActiveWorkbook.FullName resolves to on the technician's own
    machine -- a mapped-drive-letter path -- but a Windows Service (even
    one running under a real interactive user's own account) doesn't see
    that account's drive mappings, since they're tied to the interactive
    logon session, not the account. The server must translate the
    incoming path to its real UNC form before the containment check (and
    before run() ever tries to open it), or every real request 404s."""
    real_root = tmp_path / "Testing_Requests"
    real_root.mkdir()
    monkeypatch.setattr(app_module, "TESTING_REQUESTS_ROOT", real_root)
    monkeypatch.setenv("MAPPED_DRIVE_X_UNC", str(real_root))

    inside = real_root / "Customer 130" / "form.xlsx"
    inside.parent.mkdir()
    inside.write_text("stub")

    def fake_run(input_path: Path, output_path, db_url=None, services=None, info=None) -> Path:
        assert input_path == inside  # confirms translation happened before run() was called
        return inside

    monkeypatch.setattr(app_module, "run", fake_run)

    response = client.post("/generate-report", json={"input_path": r"X:\Customer 130\form.xlsx"})
    assert response.status_code == 200


def test_generate_report_success(tmp_path, monkeypatch):
    input_file = tmp_path / "TR_form.xlsx"
    input_file.write_text("stub")
    # run() now picks its own destination (the customer's mapped Reports
    # folder) whenever the caller passes output_path=None -- app.py no
    # longer precomputes a path next to the input file, so this just
    # confirms that None is what actually gets passed through.
    output_file = tmp_path / "TR Form Results.xlsx"

    def fake_run(input_path: Path, output_path: Path | None, db_url=None, services=None, info=None) -> Path:
        assert input_path == input_file
        assert output_path is None
        return output_file

    monkeypatch.setattr(app_module, "run", fake_run)

    response = client.post("/generate-report", json={"input_path": str(input_file)})
    assert response.status_code == 200
    body = response.json()
    assert body["path"] == str(output_file)
    assert isinstance(body["duration_seconds"], float)
    assert body["duration_seconds"] >= 0
    assert body["app_version"] == app_module.APP_VERSION


def test_generate_report_success_merges_run_infos_gates_into_the_response(tmp_path, monkeypatch):
    """2026-09-29, direct user request: "the big gates the code
    successfully clears" -- run()'s own `info` out-param (populated with
    customer/type/sample-count, sheet-count, per-phase timings, and
    engine/domain versions) should reach the VBA hook via this response,
    not just cli.py's own log file, so a technician's local log can show
    them without needing server access."""
    input_file = tmp_path / "TR_form.xlsx"
    input_file.write_text("stub")
    output_file = tmp_path / "TR Form Results.xlsx"

    def fake_run(input_path: Path, output_path: Path | None, db_url=None, services=None, info=None) -> Path:
        if info is not None:
            info.update(
                customer_name="Customer 130", request_type="CHEMICAL", sample_count=2,
                sheet_count=2, parse_seconds=0.01, build_seconds=0.02, write_seconds=0.03,
                engine_version="1.2.3", domain_version="4.5.6",
            )
        return output_file

    monkeypatch.setattr(app_module, "run", fake_run)

    response = client.post("/generate-report", json={"input_path": str(input_file)})
    assert response.status_code == 200
    body = response.json()
    assert body["customer_name"] == "Customer 130"
    assert body["request_type"] == "CHEMICAL"
    assert body["sample_count"] == 2
    assert body["sheet_count"] == 2
    assert body["engine_version"] == "1.2.3"
    assert body["domain_version"] == "4.5.6"
    # The pre-existing fields must still be there too, not replaced.
    assert body["path"] == str(output_file)
    assert "test_mode" in body
    assert body["app_version"] == app_module.APP_VERSION


def test_generate_report_recognized_data_error(tmp_path, monkeypatch):
    input_file = tmp_path / "TR_form.xlsx"
    input_file.write_text("stub")

    def fake_run(input_path: Path, output_path: Path, db_url=None, services=None, info=None) -> Path:
        raise ReportEngineError("unresolved customer 'Acme' -- add it before generating a report.")

    monkeypatch.setattr(app_module, "run", fake_run)

    response = client.post("/generate-report", json={"input_path": str(input_file)})
    assert response.status_code == 422
    body = response.json()
    assert "unresolved customer" in body["detail"]
    assert isinstance(body["duration_seconds"], float)


def test_generate_report_passes_cached_services_to_run(tmp_path, monkeypatch):
    input_file = tmp_path / "TR_form.xlsx"
    input_file.write_text("stub")
    sentinel_services = object()
    monkeypatch.setattr(app_module, "_services", sentinel_services)

    received = {}

    def fake_run(input_path: Path, output_path: Path, db_url=None, services=None, info=None) -> Path:
        received["services"] = services
        return output_path

    monkeypatch.setattr(app_module, "run", fake_run)

    response = client.post("/generate-report", json={"input_path": str(input_file)})
    assert response.status_code == 200
    assert received["services"] is sentinel_services


def test_refresh_cache_rebuilds_and_swaps_services(monkeypatch):
    sentinel_services = object()
    monkeypatch.setattr(app_module, "build_services", lambda db_url=None: sentinel_services)

    response = client.post("/refresh-cache")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert isinstance(body["duration_seconds"], float)
    assert app_module._services is sentinel_services


def test_refresh_cache_returns_a_clean_message_on_db_failure(monkeypatch):
    """2026-09-29 failure-path audit follow-up: _refresh_services() had no
    try/except -- a DB outage (the most plausible real reason to call this
    at all, e.g. right after a Postgres restart) used to raise straight to
    FastAPI's default handler, a bare 500 with no body at all."""
    def flaky_build_services(db_url=None):
        raise RuntimeError("could not connect to Postgres")

    monkeypatch.setattr(app_module, "build_services", flaky_build_services)

    response = client.post("/refresh-cache")
    assert response.status_code == 500
    assert "could not connect to Postgres" in response.json()["detail"]


def test_generate_report_rejects_missing_or_wrong_key_when_configured(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "API_KEY", "real-key")
    input_file = tmp_path / "TR_form.xlsx"
    input_file.write_text("stub")

    no_header = client.post("/generate-report", json={"input_path": str(input_file)})
    assert no_header.status_code == 401

    wrong_key = client.post(
        "/generate-report", json={"input_path": str(input_file)}, headers={"X-API-Key": "wrong-key"}
    )
    assert wrong_key.status_code == 401


def test_generate_report_accepts_correct_key_when_configured(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "API_KEY", "real-key")
    input_file = tmp_path / "TR_form.xlsx"
    input_file.write_text("stub")
    output_file = tmp_path / "TR_form_report.xlsx"

    def fake_run(input_path: Path, output_path: Path, db_url=None, services=None, info=None) -> Path:
        return output_path

    monkeypatch.setattr(app_module, "run", fake_run)

    response = client.post(
        "/generate-report", json={"input_path": str(input_file)}, headers={"X-API-Key": "real-key"}
    )
    assert response.status_code == 200


def test_refresh_cache_also_requires_api_key_when_configured(monkeypatch):
    monkeypatch.setattr(app_module, "API_KEY", "real-key")
    response = client.post("/refresh-cache")
    assert response.status_code == 401


def test_health_never_requires_api_key(monkeypatch):
    monkeypatch.setattr(app_module, "API_KEY", "real-key")
    response = client.get("/health")
    assert response.status_code == 200


def test_generate_report_unexpected_error(tmp_path, monkeypatch):
    input_file = tmp_path / "TR_form.xlsx"
    input_file.write_text("stub")

    def fake_run(input_path: Path, output_path: Path, db_url=None, services=None, info=None) -> Path:
        raise ValueError("boom")

    monkeypatch.setattr(app_module, "run", fake_run)

    response = client.post("/generate-report", json={"input_path": str(input_file)})
    assert response.status_code == 500
    body = response.json()
    assert "boom" in body["detail"]
    assert isinstance(body["duration_seconds"], float)


# --- Unresolved customer/chemical resolution flow ---


def test_generate_report_unresolved_customer_returns_resolve_url(tmp_path, monkeypatch):
    input_file = tmp_path / "TR_form.xlsx"
    input_file.write_text("stub")

    def fake_run(input_path: Path, output_path: Path, db_url=None, services=None, info=None) -> Path:
        raise UnresolvedCustomerReportError("Customer 130", "123 Main St", "Springfield, IL")

    monkeypatch.setattr(app_module, "run", fake_run)

    response = client.post("/generate-report", json={"input_path": str(input_file)})
    assert response.status_code == 422
    body = response.json()
    assert body["error_type"] == "unresolved_customer"
    assert "token" in body
    assert body["resolve_url"].endswith(f"resolve/customer?token={body['token']}")

    pending = app_module._pending_resolutions[body["token"]]
    assert pending["kind"] == "customer"
    assert pending["fields"] == {
        "form_customer": "Customer 130", "form_address": "123 Main St", "form_address2": "Springfield, IL",
    }
    assert pending["resolved"] is False


def test_generate_report_unresolved_chemical_returns_resolve_url(tmp_path, monkeypatch):
    input_file = tmp_path / "TR_form.xlsx"
    input_file.write_text("stub")

    def fake_run(input_path: Path, output_path: Path, db_url=None, services=None, info=None) -> Path:
        raise UnresolvedChemicalReportError("Mystery Acid")

    monkeypatch.setattr(app_module, "run", fake_run)

    response = client.post("/generate-report", json={"input_path": str(input_file)})
    assert response.status_code == 422
    body = response.json()
    assert body["error_type"] == "unresolved_chemical"
    pending = app_module._pending_resolutions[body["token"]]
    assert pending["kind"] == "chemical"
    assert pending["fields"] == {"form_name": "Mystery Acid"}


def test_generate_report_unresolved_report_folder_returns_resolve_url(tmp_path, monkeypatch):
    input_file = tmp_path / "TR_form.xlsx"
    input_file.write_text("stub")

    def fake_run(input_path: Path, output_path: Path | None, db_url=None, services=None, info=None) -> Path:
        raise UnresolvedReportFolderReportError(1, "Customer 130")

    monkeypatch.setattr(app_module, "run", fake_run)

    response = client.post("/generate-report", json={"input_path": str(input_file)})
    assert response.status_code == 422
    body = response.json()
    assert body["error_type"] == "unresolved_report_folder"
    assert body["resolve_url"].endswith(f"resolve/folder?token={body['token']}")
    pending = app_module._pending_resolutions[body["token"]]
    assert pending["kind"] == "folder"
    assert pending["fields"] == {"customer_id": 1, "customer_name": "Customer 130", "location": ""}
    assert pending["resolved"] is False


def test_resolve_customer_page_invalid_token():
    response = client.get("/resolve/customer", params={"token": "does-not-exist"})
    assert response.status_code == 200
    assert "invalid or has expired" in response.text


def test_resolve_customer_page_lists_existing_customers(fake_services):
    token = app_module._new_pending_resolution(
        "customer", {"form_customer": "Customer 130", "form_address": "123 Main St", "form_address2": ""}
    )
    response = client.get("/resolve/customer", params={"token": token})
    assert response.status_code == 200
    assert "Customer 130" in response.text
    assert "Beta Inc" in response.text


def test_resolve_customer_submit_existing_maps_form_key(fake_services):
    token = app_module._new_pending_resolution(
        "customer", {"form_customer": "Customer 130 Ltd", "form_address": "123 Main St", "form_address2": ""}
    )
    response = client.post(
        "/resolve/customer",
        data={"token": token, "mode": "existing", "existing_customer_id": "1", "created_by": "JT"},
    )
    assert response.status_code == 200
    assert "Done" in response.text
    assert fake_services.customer_svc.form_key_calls == [("Customer 130 Ltd", "123 Main St", "", 1, "JT")]
    assert app_module._pending_resolutions[token]["resolved"] is True


def test_resolve_customer_submit_new_creates_and_maps(fake_services):
    token = app_module._new_pending_resolution(
        "customer", {"form_customer": "New Co", "form_address": "1 New St", "form_address2": ""}
    )
    response = client.post(
        "/resolve/customer",
        data={
            "token": token, "mode": "new", "name": "New Co", "street_address": "1 New St",
            "city": "Metropolis", "state": "IL", "postal_code": "60000", "country": "USA",
            "created_by": "JT",
        },
    )
    assert response.status_code == 200
    assert fake_services.customer_svc.created_calls == [
        ("New Co", "1 New St", "Metropolis", "IL", "60000", "USA", "JT")
    ]
    assert fake_services.customer_svc.form_key_calls == [("New Co", "1 New St", "", 99, "JT")]
    assert app_module._pending_resolutions[token]["resolved"] is True


def test_resolve_customer_submit_new_self_heals_to_existing_case_insensitive_match(fake_services):
    """Confirmed real ask (2026-09-24): a technician moving quickly can type
    a name that's an exact case-insensitive match for a customer that
    already exists ("Customer 130" is seeded by the fake). Previously this
    would have hit create_customer()'s own uniqueness error and dead-ended
    on a "Could not resolve" page. Now it links to the existing record
    instead of erroring, and says so plainly rather than silently."""
    token = app_module._new_pending_resolution(
        "customer", {"form_customer": "Customer 130", "form_address": "1 New St", "form_address2": ""}
    )
    response = client.post(
        "/resolve/customer",
        data={
            "token": token, "mode": "new", "name": "Customer 130", "street_address": "1 New St",
            "city": "Metropolis", "state": "IL", "postal_code": "60000", "country": "USA",
            "created_by": "JT",
        },
    )
    assert response.status_code == 200
    assert fake_services.customer_svc.created_calls == []  # no duplicate created
    assert fake_services.customer_svc.form_key_calls == [
        ("Customer 130", "1 New St", "", 1, "JT")  # linked to the existing customer_id=1
    ]
    assert "already existed in the system" in response.text
    assert "Customer 130" in response.text  # the EXISTING record's real name, not the raw typed input


def test_resolve_customer_submit_replay_after_resolved_is_a_no_op(fake_services):
    # A token is only meant to be submitted once. Reachable via a stale
    # browser back-button or a double-click, not malicious -- but
    # replaying it must not call create_customer()/add_form_key() a
    # second time for the same mapping.
    token = app_module._new_pending_resolution(
        "customer", {"form_customer": "Customer 130 Ltd", "form_address": "123 Main St", "form_address2": ""}
    )
    client.post(
        "/resolve/customer",
        data={"token": token, "mode": "existing", "existing_customer_id": "1", "created_by": "JT"},
    )

    response = client.post(
        "/resolve/customer",
        data={"token": token, "mode": "existing", "existing_customer_id": "1", "created_by": "JT"},
    )

    assert response.status_code == 200
    assert "Already resolved" in response.text
    assert fake_services.customer_svc.form_key_calls == [("Customer 130 Ltd", "123 Main St", "", 1, "JT")]


def test_resolve_customer_submit_concurrent_double_submit_creates_exactly_once(fake_services):
    """Real stress-test finding (2026-09-24): the check-then-act on
    pending["resolved"] used to happen without holding the lock across
    both the check and the claim, so two near-simultaneous submits (a
    double-click, a browser resubmit) could both pass the check before
    either claimed it -- create_customer isn't idempotent, so that raced
    into two real duplicate rows. Claiming inside the same lock as the
    check closes the window: fire two REAL concurrent requests (not
    sequential) at the same token and confirm only one actually creates
    a customer."""
    import concurrent.futures

    token = app_module._new_pending_resolution(
        "customer", {"form_customer": "Concurrent Co", "form_address": "1 St", "form_address2": ""}
    )

    def submit():
        return client.post(
            "/resolve/customer",
            data={
                "token": token, "mode": "new", "name": "Concurrent Co", "street_address": "1 St",
                "city": "Metropolis", "state": "IL", "postal_code": "60000", "country": "USA",
                "created_by": "JT",
            },
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as ex:
        responses = [f.result() for f in [ex.submit(submit) for _ in range(5)]]

    assert all(r.status_code == 200 for r in responses)
    assert len(fake_services.customer_svc.created_calls) == 1
    resolved_count = sum(1 for r in responses if "Already resolved" not in r.text)
    assert resolved_count == 1


def test_resolve_customer_submit_unexpected_error_releases_claim_for_retry(fake_services, monkeypatch):
    """The claim-before-write change (2026-09-24) must release the claim
    on ANY exception, not just the ValueError create_customer/add_form_key
    normally raise -- otherwise a genuinely unexpected failure (a real
    bug, a transient DB hiccup) would leave the token permanently
    "resolved" with nothing actually saved, and a legitimate retry would
    wrongly see "already resolved" forever.

    Updated 2026-09-29 (failure-path audit): an unexpected exception used
    to be bare-`raise`d here, which -- since this app registers no
    exception_handler and runs without debug=True -- reached the
    technician as a bodyless 500 with literally no message at all. Now
    it's logged server-side and answered with a real "Something went
    wrong" page instead, same as every other failure mode in this flow."""
    token = app_module._new_pending_resolution(
        "customer", {"form_customer": "Flaky Co", "form_address": "1 St", "form_address2": ""}
    )

    real_create = fake_services.customer_svc.create_customer
    calls = {"n": 0}

    def flaky_create(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("transient failure, not a ValueError")
        return real_create(*args, **kwargs)

    monkeypatch.setattr(fake_services.customer_svc, "create_customer", flaky_create)

    submit_data = {
        "token": token, "mode": "new", "name": "Flaky Co", "street_address": "1 St",
        "city": "Metropolis", "state": "IL", "postal_code": "60000", "country": "USA",
        "created_by": "JT",
    }
    first = client.post("/resolve/customer", data=submit_data)
    assert first.status_code == 200
    assert "Something went wrong" in first.text

    # The claim must have been released -- a retry succeeds, not "already resolved".
    assert app_module._pending_resolutions[token]["resolved"] is False
    retry = client.post("/resolve/customer", data=submit_data)
    assert retry.status_code == 200
    assert "Already resolved" not in retry.text
    assert len(fake_services.customer_svc.created_calls) == 1


def test_resolve_customer_submit_existing_requires_initials(fake_services):
    # Confirmed real (2026-09-22): mapping ambiguous form text onto an
    # EXISTING customer is just as consequential as creating a new one --
    # initials are required either way now, not just for "add new".
    token = app_module._new_pending_resolution(
        "customer", {"form_customer": "Customer 130 Ltd", "form_address": "123 Main St", "form_address2": ""}
    )
    response = client.post(
        "/resolve/customer",
        data={"token": token, "mode": "existing", "existing_customer_id": "1", "created_by": "  "},
    )
    assert response.status_code == 200
    assert "Could not resolve" in response.text
    assert fake_services.customer_svc.form_key_calls == []


def test_resolve_customer_submit_missing_existing_selection_shows_error(fake_services):
    token = app_module._new_pending_resolution(
        "customer", {"form_customer": "Customer 130", "form_address": "", "form_address2": ""}
    )
    response = client.post("/resolve/customer", data={"token": token, "mode": "existing", "existing_customer_id": ""})
    assert response.status_code == 200
    assert "Could not resolve" in response.text
    assert app_module._pending_resolutions[token]["resolved"] is False


def test_resolve_chemical_submit_existing_requires_initials(fake_services):
    token = app_module._new_pending_resolution("chemical", {"form_name": "Test Acid Matrix"})
    response = client.post(
        "/resolve/chemical",
        data={"token": token, "mode": "existing", "existing_chemical_id": "1", "created_by": "  "},
    )
    assert response.status_code == 200
    assert "Could not resolve" in response.text
    assert fake_services.chemical_svc.form_name_calls == []


def test_resolve_chemical_submit_existing_maps_form_name(fake_services):
    token = app_module._new_pending_resolution("chemical", {"form_name": "Test Acid Matrix"})
    response = client.post(
        "/resolve/chemical",
        data={"token": token, "mode": "existing", "existing_chemical_id": "1", "created_by": "JT"},
    )
    assert response.status_code == 200
    assert fake_services.chemical_svc.form_name_calls == [("Test Acid Matrix", 1, "JT")]
    assert app_module._pending_resolutions[token]["resolved"] is True


def test_resolve_chemical_submit_new_creates_and_maps(fake_services):
    token = app_module._new_pending_resolution("chemical", {"form_name": "New Acid"})
    response = client.post(
        "/resolve/chemical",
        data={
            "token": token, "mode": "new", "name": "New Acid",
            "metals_prep": "N/A", "silicon_prep": "N/A", "ions_prep": "N/A",
            "created_by": "JT",
        },
    )
    assert response.status_code == 200
    assert fake_services.chemical_svc.created_calls == [("New Acid", "N/A", "N/A", "N/A", frozenset(), "JT")]
    assert fake_services.chemical_svc.form_name_calls == [("New Acid", 88, "JT")]


def test_resolve_chemical_submit_new_self_heals_to_existing_case_insensitive_match(fake_services):
    """See the matching customer test's docstring -- same reasoning. "Test
    Acid" (id=1) is seeded by the fake."""
    token = app_module._new_pending_resolution("chemical", {"form_name": "TEST ACID"})
    response = client.post(
        "/resolve/chemical",
        data={
            "token": token, "mode": "new", "name": "test acid",
            "metals_prep": "N/A", "silicon_prep": "N/A", "ions_prep": "N/A",
            "created_by": "JT",
        },
    )
    assert response.status_code == 200
    assert fake_services.chemical_svc.created_calls == []  # no duplicate created
    assert fake_services.chemical_svc.form_name_calls == [("TEST ACID", 1, "JT")]
    assert "already existed in the system" in response.text
    assert "Test Acid" in response.text  # the EXISTING record's real name


def test_resolve_chemical_submit_new_self_heal_flags_dropped_ked_elements(fake_services):
    """KED elements typed for what the technician thought was a NEW
    chemical can't be silently applied to the EXISTING one they got
    linked to instead -- there's still no supported way to add KED
    elements to an already-existing chemical (a known, separate gap).
    Must say so, not drop them with no explanation."""
    token = app_module._new_pending_resolution("chemical", {"form_name": "TEST ACID"})
    response = client.post(
        "/resolve/chemical",
        data={
            "token": token, "mode": "new", "name": "test acid",
            "metals_prep": "N/A", "silicon_prep": "N/A", "ions_prep": "N/A",
            "ked_element_ids": ["101"], "created_by": "JT",
        },
    )
    assert response.status_code == 200
    assert "were NOT applied" in response.text


def test_resolve_chemical_submit_new_with_ked_elements_passes_them_through(fake_services):
    token = app_module._new_pending_resolution("chemical", {"form_name": "New Acid"})
    response = client.post(
        "/resolve/chemical",
        data={
            "token": token, "mode": "new", "name": "New Acid",
            "metals_prep": "N/A", "silicon_prep": "N/A", "ions_prep": "N/A",
            "ked_element_ids": ["1", "2"], "created_by": "JT",
        },
    )
    assert response.status_code == 200
    assert fake_services.chemical_svc.created_calls == [("New Acid", "N/A", "N/A", "N/A", frozenset({1, 2}), "JT")]


def test_resolve_chemical_submit_replay_after_resolved_is_a_no_op(fake_services):
    token = app_module._new_pending_resolution("chemical", {"form_name": "Test Acid Matrix"})
    client.post(
        "/resolve/chemical",
        data={"token": token, "mode": "existing", "existing_chemical_id": "1", "created_by": "JT"},
    )

    response = client.post(
        "/resolve/chemical",
        data={"token": token, "mode": "existing", "existing_chemical_id": "1", "created_by": "JT"},
    )

    assert response.status_code == 200
    assert "Already resolved" in response.text
    assert fake_services.chemical_svc.form_name_calls == [("Test Acid Matrix", 1, "JT")]


def test_resolve_folder_page_invalid_token():
    response = client.get("/resolve/folder", params={"token": "does-not-exist"})
    assert response.status_code == 200
    assert "invalid or has expired" in response.text


def test_resolve_folder_page_shows_customer_name(fake_services):
    token = app_module._new_pending_resolution("folder", {"customer_id": 1, "customer_name": "Customer 130"})
    response = client.get("/resolve/folder", params={"token": token})
    assert response.status_code == 200
    assert "Customer 130" in response.text


def test_resolve_folder_page_no_banner_outside_test_mode(fake_services):
    token = app_module._new_pending_resolution("folder", {"customer_id": 1, "customer_name": "Customer 130"})
    response = client.get("/resolve/folder", params={"token": token})
    assert "TEST MODE" not in response.text
    assert "S:\\Reports\\Customer 130" in response.text


def test_resolve_folder_page_shows_banner_and_sandboxed_default_in_test_mode(fake_services, tmp_path):
    fake_services.report_folder_svc.test_root = tmp_path
    token = app_module._new_pending_resolution("folder", {"customer_id": 1, "customer_name": "Customer 130"})
    response = client.get("/resolve/folder", params={"token": token})
    assert "TEST MODE" in response.text
    assert str(tmp_path / "Customer 130") in response.text
    assert "S:\\Reports" not in response.text


def test_resolve_customer_page_banner_warns_catalog_writes_are_real(fake_services, tmp_path):
    # Confirmed real (2026-09-22): REPORT_FOLDER_TEST_ROOT has never
    # sandboxed customer/chemical catalog writes, only report files --
    # the banner must say so on these pages specifically, not just the
    # generic "Reports" wording that implies broader protection.
    fake_services.report_folder_svc.test_root = tmp_path
    token = app_module._new_pending_resolution(
        "customer", {"form_customer": "Customer 130", "form_address": "123 Main St", "form_address2": ""}
    )
    response = client.get("/resolve/customer", params={"token": token})
    assert "TEST MODE" in response.text
    assert "does not apply to what you do on this page" in response.text


def test_resolve_chemical_page_banner_warns_catalog_writes_are_real(fake_services, tmp_path):
    fake_services.report_folder_svc.test_root = tmp_path
    token = app_module._new_pending_resolution("chemical", {"form_name": "Test Acid Matrix"})
    response = client.get("/resolve/chemical", params={"token": token})
    assert "TEST MODE" in response.text
    assert "does not apply to what you do on this page" in response.text


def test_resolve_chemical_page_lists_ked_elements_for_new_chemical(fake_services):
    token = app_module._new_pending_resolution("chemical", {"form_name": "New Acid"})
    response = client.get("/resolve/chemical", params={"token": token})
    assert 'name="ked_element_ids"' in response.text
    assert "Copper (Cu)" in response.text
    assert "Titanium (Ti)" in response.text


def test_resolve_folder_page_banner_unchanged(fake_services, tmp_path):
    # The folder page's original wording is accurate as-is -- no extra
    # sentence should be added there.
    fake_services.report_folder_svc.test_root = tmp_path
    token = app_module._new_pending_resolution("folder", {"customer_id": 1, "customer_name": "Customer 130"})
    response = client.get("/resolve/folder", params={"token": token})
    assert "does not apply to what you do on this page" not in response.text


def test_resolve_folder_page_shows_sx5_location(fake_services):
    # SX5 North/South is the same real customer record -- location comes
    # from the TR filename, not the customer, and needs to be visible on
    # the page so the technician resolves the RIGHT folder.
    token = app_module._new_pending_resolution(
        "folder", {"customer_id": 21, "customer_name": "SX5", "location": "SX5N"}
    )
    response = client.get("/resolve/folder", params={"token": token})
    assert "SX5 (North)" in response.text
    assert "SX5_North" in response.text  # suggested default path


def test_resolve_folder_submit_sets_the_right_sx5_location(fake_services, tmp_path):
    north_token = app_module._new_pending_resolution(
        "folder", {"customer_id": 21, "customer_name": "SX5", "location": "SX5N"}
    )
    south_token = app_module._new_pending_resolution(
        "folder", {"customer_id": 21, "customer_name": "SX5", "location": "SX5S"}
    )
    north_dir = tmp_path / "SX5_North"
    south_dir = tmp_path / "SX5_South"

    client.post("/resolve/folder", data={"token": north_token, "folder_path": str(north_dir), "created_by": "JT"})
    client.post("/resolve/folder", data={"token": south_token, "folder_path": str(south_dir), "created_by": "JT"})

    assert fake_services.report_folder_svc.folders[(21, "SX5N")] == str(north_dir)
    assert fake_services.report_folder_svc.folders[(21, "SX5S")] == str(south_dir)


def test_resolve_folder_submit_sets_folder(fake_services, tmp_path):
    token = app_module._new_pending_resolution("folder", {"customer_id": 1, "customer_name": "Customer 130"})
    response = client.post(
        "/resolve/folder", data={"token": token, "folder_path": str(tmp_path), "created_by": "JT"}
    )
    assert response.status_code == 200
    assert "Done" in response.text
    assert fake_services.report_folder_svc.set_folder_calls == [(1, str(tmp_path), "JT", "")]
    assert app_module._pending_resolutions[token]["resolved"] is True


def test_resolve_folder_submit_translates_mapped_drive_before_set_folder(fake_services, tmp_path, monkeypatch):
    """A technician can type a drive-letter path (e.g. S:\\...) here even
    when REPORT_FOLDER_TEST_ROOT is configured as -- and thus stored
    internally as -- the real UNC form (see resolve_mapped_drive() at
    import time). Without translating here too, set_folder()'s
    containment check would reject a real, correct answer just because
    it's spelled differently than test_root itself."""
    monkeypatch.setenv("MAPPED_DRIVE_Q_UNC", str(tmp_path))

    token = app_module._new_pending_resolution("folder", {"customer_id": 1, "customer_name": "Customer 130"})
    response = client.post(
        "/resolve/folder",
        data={"token": token, "folder_path": r"Q:\Customer 130", "created_by": "JT"},
    )

    assert response.status_code == 200
    assert "Done" in response.text
    assert fake_services.report_folder_svc.set_folder_calls == [(1, str(tmp_path / "Customer 130"), "JT", "")]


def test_resolve_folder_submit_invalid_path_shows_error(fake_services):
    token = app_module._new_pending_resolution("folder", {"customer_id": 1, "customer_name": "Customer 130"})
    response = client.post(
        "/resolve/folder", data={"token": token, "folder_path": "   ", "created_by": "JT"}
    )
    assert response.status_code == 200
    assert "Could not set report folder" in response.text
    assert app_module._pending_resolutions[token]["resolved"] is False


def test_resolve_folder_submit_blank_initials_shows_error(fake_services, tmp_path):
    token = app_module._new_pending_resolution("folder", {"customer_id": 1, "customer_name": "Customer 130"})
    response = client.post(
        "/resolve/folder", data={"token": token, "folder_path": str(tmp_path), "created_by": "   "}
    )
    assert response.status_code == 200
    assert "Could not set report folder" in response.text
    assert app_module._pending_resolutions[token]["resolved"] is False


def test_resolve_folder_submit_replay_after_resolved_is_a_no_op(fake_services, tmp_path):
    token = app_module._new_pending_resolution("folder", {"customer_id": 1, "customer_name": "Customer 130"})
    client.post("/resolve/folder", data={"token": token, "folder_path": str(tmp_path), "created_by": "JT"})

    response = client.post(
        "/resolve/folder", data={"token": token, "folder_path": str(tmp_path), "created_by": "JT"}
    )

    assert response.status_code == 200
    assert "Already resolved" in response.text
    assert fake_services.report_folder_svc.set_folder_calls == [(1, str(tmp_path), "JT", "")]


def test_resolution_status_reflects_pending_then_resolved(fake_services):
    token = app_module._new_pending_resolution("customer", {"form_customer": "X", "form_address": "", "form_address2": ""})
    assert client.get(f"/resolution-status/{token}").json() == {
        "resolved": False, "cancelled": False, "expired": False, "abandoned": False,
    }

    client.post(
        "/resolve/customer",
        data={"token": token, "mode": "existing", "existing_customer_id": "1", "created_by": "JT"},
    )

    assert client.get(f"/resolution-status/{token}").json() == {
        "resolved": True, "cancelled": False, "expired": False, "abandoned": False,
    }


def test_resolution_status_unknown_token_reports_expired():
    response = client.get("/resolution-status/no-such-token")
    assert response.json() == {"resolved": False, "cancelled": False, "expired": True, "abandoned": False}


def test_resolve_cancel_marks_pending_cancelled_and_status_reflects_it(fake_services):
    token = app_module._new_pending_resolution("chemical", {"form_name": "RAW MATERIALS"})
    response = client.post("/resolve/cancel", data={"token": token})

    assert response.status_code == 200
    assert "Cancelled" in response.text
    assert app_module._pending_resolutions[token]["cancelled"] is True
    assert client.get(f"/resolution-status/{token}").json() == {
        "resolved": False, "cancelled": True, "expired": False, "abandoned": False,
    }
    # Cancelling must never call any create_*/add_form_*/set_folder write path.
    assert fake_services.chemical_svc.created_calls == []
    assert fake_services.chemical_svc.form_name_calls == []


def test_resolution_status_reports_abandoned_after_heartbeat_silence(fake_services, monkeypatch):
    token = app_module._new_pending_resolution("customer", {"form_customer": "X", "form_address": "", "form_address2": ""})
    assert client.get(f"/resolution-status/{token}").json()["abandoned"] is False

    real_time = time.time
    monkeypatch.setattr(app_module.time, "time", lambda: real_time() + app_module._ABANDONED_AFTER_SECONDS + 1)

    assert client.get(f"/resolution-status/{token}").json()["abandoned"] is True


def test_resolution_heartbeat_resets_the_abandoned_clock(fake_services, monkeypatch):
    token = app_module._new_pending_resolution("customer", {"form_customer": "X", "form_address": "", "form_address2": ""})

    real_time = time.time
    monkeypatch.setattr(app_module.time, "time", lambda: real_time() + app_module._ABANDONED_AFTER_SECONDS + 1)
    assert client.get(f"/resolution-status/{token}").json()["abandoned"] is True

    client.post(f"/resolution-heartbeat/{token}")
    monkeypatch.setattr(app_module.time, "time", real_time)
    assert client.get(f"/resolution-status/{token}").json()["abandoned"] is False


def test_resolution_heartbeat_on_unknown_token_does_not_error(fake_services):
    response = client.post("/resolution-heartbeat/no-such-token")
    assert response.status_code == 200


def test_resolution_heartbeat_does_not_flag_resolved_token_as_abandoned(fake_services, monkeypatch):
    # A resolved token's page has already navigated away/stopped -- no
    # heartbeats is expected, not evidence of anything gone wrong.
    token = app_module._new_pending_resolution("customer", {"form_customer": "X", "form_address": "", "form_address2": ""})
    client.post(
        "/resolve/customer",
        data={"token": token, "mode": "existing", "existing_customer_id": "1", "created_by": "JT"},
    )

    real_time = time.time
    monkeypatch.setattr(app_module.time, "time", lambda: real_time() + app_module._ABANDONED_AFTER_SECONDS + 1)

    assert client.get(f"/resolution-status/{token}").json()["abandoned"] is False


def test_resolve_customer_page_includes_heartbeat_script(fake_services):
    token = app_module._new_pending_resolution("customer", {"form_customer": "X", "form_address": "", "form_address2": ""})
    response = client.get(f"/resolve/customer?token={token}")
    assert f"/resolution-heartbeat/{token}" in response.text


def test_link_expired_page_does_not_include_heartbeat_script():
    response = client.get("/resolve/customer?token=no-such-token")
    assert "resolution-heartbeat" not in response.text


def test_resolve_cancel_invalid_token():
    response = client.post("/resolve/cancel", data={"token": "does-not-exist"})
    assert response.status_code == 200
    assert "invalid or has expired" in response.text


def test_resolve_cancel_replay_after_cancelled_is_a_no_op(fake_services):
    token = app_module._new_pending_resolution("chemical", {"form_name": "RAW MATERIALS"})
    client.post("/resolve/cancel", data={"token": token})

    response = client.post("/resolve/cancel", data={"token": token})
    assert response.status_code == 200
    assert "Already resolved" in response.text


def test_resolve_submit_after_cancelled_is_blocked(fake_services):
    # Once cancelled, the technician must not be able to still submit a
    # resolution for the same token through a stale open tab.
    token = app_module._new_pending_resolution("chemical", {"form_name": "RAW MATERIALS"})
    client.post("/resolve/cancel", data={"token": token})

    response = client.post(
        "/resolve/chemical",
        data={"token": token, "mode": "existing", "existing_chemical_id": "1", "created_by": "JT"},
    )
    assert response.status_code == 200
    assert "Already resolved" in response.text
    assert fake_services.chemical_svc.form_name_calls == []
