"""A TR's history (slim-data D-104; review A5): every change kept whole, append-only, readable as of
any time."""

from datetime import timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from slim_lims.models import TRSubmissionRow, TRVersionRow
from slim_lims.repositories import TRSubmissionRepository, _to_row
from tests.conftest import login, login_api
from tests.factories import submission
from tests.test_api_submissions import as_staff, payload  # noqa: F401 (fixture)

TWO_CHEMICALS = [
    {"sample_name": "S-1", "chemical_name": "Chemical 01", "processing_time": 7, "analysis_ids": [1]},
    {"sample_name": "S-2", "chemical_name": "IPA semi grade", "processing_time": 7, "analysis_ids": [1]},
]


def _make_history(client):
    client.post("/api/submissions", json=payload(customer_id=1, samples=TWO_CHEMICALS))
    client.post("/api/submissions/TR00001/samples/2/chemical", json={"chemical_id": 3})
    client.post("/api/submissions/TR00001/check-in", json={"date_received": "2026-10-07"})
    return client.post("/api/submissions/TR00001/status", json={"to_status": 3}).json()


def test_every_change_is_a_version_and_the_last_is_the_tr_as_it_is(as_staff, db):  # noqa: F811
    now = _make_history(as_staff)
    repo = TRSubmissionRepository(db)
    versions = repo.versions(now["id"])
    assert [(v.version, v.change, v.actor_display) for v in versions] == [
        (1, "submitted", versions[0].actor_display),
        (2, "sample 2 matched to SLIM chemical 3", "JM"),
        (3, "checked in", versions[2].actor_display),
        (4, "in progress", versions[3].actor_display),
    ]
    # Replaying the history gives exactly the current state.
    assert repo.version(now["id"], 4) == repo.get(now["id"])
    assert repo.get(now["id"]).model_dump(mode="json") == now  # what the API said
    assert repo.version(now["id"], 1).status_history[-1].to_status == 1
    assert (
        repo.version(now["id"], 2).samples[1].chemical_id == 3
        and repo.version(now["id"], 1).samples[1].chemical_id is None
    )


def test_as_of_reads_the_tr_as_it_stood(as_staff, db):  # noqa: F811
    now = _make_history(as_staff)
    repo = TRSubmissionRepository(db)
    first, *_, last = repo.versions(now["id"])
    assert repo.as_of(now["id"], first.recorded_at - timedelta(seconds=1)) is None
    assert repo.as_of(now["id"], first.recorded_at).status == 1
    assert repo.as_of(now["id"], last.recorded_at + timedelta(days=1)).status == 3


def test_versions_are_append_only(as_staff, db):  # noqa: F811
    _make_history(as_staff)
    db.rollback()
    for statement in ("UPDATE tr_versions SET change = 'x'", "DELETE FROM tr_versions"):
        with pytest.raises(DBAPIError, match="append-only"):
            db.execute(text(statement))
        db.rollback()


def test_a_tr_from_before_the_history_gets_its_state_as_version_one(db):
    row = _to_row(submission(), "TR00042")
    db.add(row)
    db.flush()
    repo = TRSubmissionRepository(db)
    assert repo.backfill_versions() == 1 and repo.backfill_versions() == 0
    assert [v.change for v in repo.versions(row.id)] == ["before the history began"]
    assert db.query(TRVersionRow).count() == 1 and db.query(TRSubmissionRow).count() == 1


def test_the_request_page_lists_versions_and_shows_one_read_only(client, staff_user, customer_user, db):
    login_api(client, "staff@lab.example")
    _make_history(client)
    login(client, "staff@lab.example")
    page = client.get("/requests/TR00001")
    assert "Versions" in page.text and "checked in" in page.text
    old = client.get("/requests/TR00001/versions/1")
    assert old.status_code == 200 and "As it stood at version 1 of 4: read-only." in old.text
    assert "Actions" not in old.text and "Back to the request as it is now" in old.text
    assert client.get("/requests/TR00001/versions/9").status_code == 404


def test_a_customer_sees_only_their_own_requests_history(client, staff_user, customer_user, db):
    login_api(client, "staff@lab.example")
    client.post("/api/submissions", json=payload(customer_id=2, samples=TWO_CHEMICALS[:1]))
    login(client, "jane@acme.example")
    assert client.get("/requests/TR00001/versions/1").status_code == 404
