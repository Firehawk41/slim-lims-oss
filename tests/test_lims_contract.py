"""The portal <-> slim-lims contract (docs/SLIM_DOMAIN_V2.md §12).

slim-lims will be written against §12. If one of these tests fails, the portal has broken
that contract: fix the portal, or change §12 and slim-lims together -- never just the test.
"""

import pytest
from fastapi.testclient import TestClient

from slim_lims.main import create_app
from tests.conftest import login_api, make_settings
from tests.test_api_submissions import payload

TOKEN = "t" * 40
BEARER = {"Authorization": f"Bearer {TOKEN}"}

# The JSON slim-lims reads. Adding a key is fine (update §12 and this list); removing or
# renaming one breaks slim-lims.
SUBMISSION_KEYS = {
    "id", "tr_number", "source", "status", "customer_id", "location", "request_type",
    "submitted_at", "submitted_by", "customer_snapshot", "customer_contact", "customer_phone",
    "payment_method", "po_number", "expected_arrival_date", "results_to", "results_cc",
    "invoice_to", "invoice_cc", "receipt", "samples", "status_history",
    "service_date_override", "file_name", "effective_service_date", "estimated_service_date", "is_rush",
}
SAMPLE_KEYS = {
    "id", "position", "sample_name", "chemical_id", "chemical_name", "processing_time", "requested_time",
    "additional_notes", "analysis_ids", "additional_element_ids", "wafer_size", "reporting_unit",
    "water_package",
}
RECEIPT_KEYS = {"date_received", "received_by", "recorded_at", "recorded_by"}


@pytest.fixture
def app(engine, reference):
    return create_app(make_settings(str(engine.url), lims_api_token=TOKEN), engine=engine, reference=reference)


@pytest.fixture
def lims(app):
    """slim-lims: no cookies, no CSRF token, just the bearer token."""
    with TestClient(app, headers=BEARER) as client:
        yield client


@pytest.fixture
def received(client, staff_user):
    """TR00001 submitted and checked in by staff (all chemicals match exactly); TR00002 only submitted."""
    login_api(client, "staff@lab.example")
    for _ in range(2):
        assert client.post("/api/submissions", json=payload(customer_id=1)).status_code == 201
    response = client.post("/api/submissions/TR00001/check-in", json={"date_received": "2026-10-07"})
    assert response.status_code == 200, response.text
    return client


def test_lims_lists_requests_ready_for_work(lims, received):
    response = lims.get("/api/submissions", params={"status": 2})
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"items", "total", "page", "per_page"}
    assert [s["tr_number"] for s in body["items"]] == ["TR00001"]


def test_submission_json_shape(lims, received):
    body = lims.get("/api/submissions/TR00001").json()
    assert set(body) == SUBMISSION_KEYS
    assert set(body["samples"][0]) == SAMPLE_KEYS
    assert set(body["receipt"]) == RECEIPT_KEYS
    assert body["status"] == 2 and body["request_type"] == 1  # enums are ints
    assert all(s["chemical_id"] is not None for s in body["samples"])  # checked in => matched


def test_lims_moves_work_forward(lims, received):
    for to_status in (3, 4, 4, 5):  # in progress, partial report (repeatable), complete report
        response = lims.post("/api/submissions/TR00001/status", json={"to_status": to_status, "note": "report R-1"})
        assert response.status_code == 200, response.text
    last = response.json()["status_history"][-1]
    assert last["to_status"] == 5
    assert last["actor"] == {"type": 3, "id": "slim-lims", "display": "slim-lims"}  # ActorType.SYSTEM


@pytest.mark.parametrize("to_status", [2, 6, 7])  # received (check-in only), invoiced, cancelled
def test_lims_cannot_set_staff_statuses(lims, received, to_status):
    if to_status == 6:
        for s in (3, 5):
            assert lims.post("/api/submissions/TR00001/status", json={"to_status": s}).status_code == 200
    assert lims.post("/api/submissions/TR00001/status", json={"to_status": to_status}).status_code == 409


def test_lims_cannot_start_work_before_check_in(lims, received):
    assert lims.post("/api/submissions/TR00002/status", json={"to_status": 3}).status_code == 409


@pytest.mark.parametrize("method, path, body", [
    ("post", "/api/submissions", payload(customer_id=1)),
    ("post", "/api/submissions/TR00002/check-in", {"date_received": "2026-10-07"}),
    ("post", "/api/submissions/TR00001/samples/1/chemical", {"chemical_id": 1}),
    ("get", "/api/reference", None),
    ("get", "/api/me", None),
    ("get", "/admin", None),
    ("get", "/requests/TR00001", None),
])
def test_lims_is_limited_to_its_routes(lims, received, method, path, body):
    response = lims.request(method.upper(), path, json=body)
    assert response.status_code == 403, (path, response.status_code, response.text)


def test_wrong_or_missing_token_is_401(app, received):
    with TestClient(app) as anon:
        assert anon.get("/api/submissions").status_code == 401
        for header in (f"Bearer {'x' * 40}", "Bearer ", f"Basic {TOKEN}", TOKEN):
            assert anon.get("/api/submissions", headers={"Authorization": header}).status_code == 401, header


def test_a_bad_token_does_not_fall_back_to_the_session(received):
    """A logged-in browser that also sends a bad Authorization header is not logged in --
    otherwise the CSRF exemption for token requests would cover cookie requests."""
    received.headers["Authorization"] = "Bearer wrong"
    assert received.get("/api/submissions").status_code == 401
    del received.headers["X-CSRF-Token"]
    assert received.post("/api/submissions/TR00001/status", json={"to_status": 3}).status_code == 401


def test_no_token_configured_means_no_machine_access(engine, reference):
    app = create_app(make_settings(str(engine.url)), engine=engine, reference=reference)  # no LIMS_API_TOKEN
    with TestClient(app, headers=BEARER) as lims:
        assert lims.get("/api/submissions").status_code == 401
