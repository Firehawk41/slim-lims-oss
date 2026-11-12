"""QC trend warnings at review and release (slim-data D-144): shown on the batch page, never a block;
releasing over an ACTION flag asks for a reason, kept host-side with the flags as shown."""

from datetime import UTC, datetime

import pytest
from slim_quality.domain.enums import Severity
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from slim_lims import qc_warnings
from slim_lims.models import QcAcknowledgementRow
from tests.conftest import csrf_from
from tests.test_results import SAMPLE_1, TEN, app, csv, lab, reference, staff_api, upload  # noqa: F401

AT = datetime(2026, 10, 10, 8, 0, tzinfo=UTC)


def fake(severity: Severity, code="WG_1_3S"):
    def batch_warnings(db, batch, analytes, charts=None):
        return {
            a: [
                qc_warnings.Warning(
                    "ICP-MS 1",
                    a,
                    f"ICP-MS 1 · {a} CCV",
                    f"icpms1-{a}",
                    "westgard",
                    code,
                    severity,
                    "beyond 3 SD",
                    AT,
                ),  # fmt: skip
                qc_warnings.Warning(
                    "ICP-MS 1",
                    a,
                    "ICP-MS 1 · In ISTD",
                    "icpms1-in",
                    "ewma",
                    "EWMA_HIGH",
                    Severity.WARN,
                    "drifting high",
                    AT,
                ),  # fmt: skip
            ]
            for a in sorted(analytes)
        }

    return batch_warnings


def reviewed(app):  # noqa: F811
    with staff_api(app, "AN") as an:
        upload(an, csv())
    with staff_api(app, "RV") as rv:
        assert rv.post("/api/results/batches/PB-1/review", json={}).json() == {"reviewed": 2}


def test_an_action_flag_needs_a_reason_to_release_and_keeps_the_flags(app, lab, monkeypatch):  # noqa: F811
    monkeypatch.setattr(qc_warnings, "batch_warnings", fake(Severity.ACTION))
    reviewed(app)
    with staff_api(app, "RV") as rv:
        page = rv.get("/results/batches/PB-1").text
        assert "QC trends on these runs" in page and "ICP-MS 1 · In ISTD" in page
        assert page.count("ICP-MS 1 · In ISTD") == 1  # each chart once, however many analytes
        assert 'name="qc_ack"' in page and "QC action" in page
        refused = rv.post(f"{SAMPLE_1}/release", json={})
        assert refused.status_code == 409 and "action level" in refused.json()["detail"]
        assert rv.post(f"{SAMPLE_1}/release", json={"qc_ack": "   "}).status_code == 409
        assert lab.scalars(select(QcAcknowledgementRow)).all() == []  # a refusal writes nothing
        lab.rollback()  # end the read: on SQLite it would lock the app's write
        released = rv.post(f"{SAMPLE_1}/release", json={"qc_ack": "CCV re-run in limits"})
        assert released.status_code == 200 and released.json()["released"] == ["Fe"]
    lab.rollback()
    (ack,) = lab.scalars(select(QcAcknowledgementRow)).all()
    assert ack.sample == "TR00001-01" and ack.analysis_id == TEN
    assert ack.note == "CCV re-run in limits" and ack.by == "rv@lab.example"
    assert [f["code"] for f in ack.flags] == ["WG_1_3S"] and ack.flags[0]["severity"] == "action"
    lab.rollback()
    with pytest.raises(DBAPIError):  # append-only
        lab.execute(text("UPDATE qc_acknowledgements SET note = 'changed'"))
        lab.flush()
    lab.rollback()


def test_the_batch_page_form_asks_for_the_reason(app, lab, monkeypatch):  # noqa: F811
    monkeypatch.setattr(qc_warnings, "batch_warnings", fake(Severity.ACTION))
    reviewed(app)
    with staff_api(app, "RV") as rv:
        token = csrf_from(rv.get("/results/batches/PB-1").text)
        form = {"csrf_token": token, "sample": "TR00001-01", "analysis_id": TEN}
        assert "action level" in rv.post("/results/batches/PB-1/release", data=form).text
        done = rv.post("/results/batches/PB-1/release", data=form | {"qc_ack": "checked the CCV"})
        assert "Released TR00001-01" in done.text


@pytest.mark.parametrize("severity", [Severity.INFO, Severity.WARN])
def test_info_and_warn_flags_are_shown_only(app, lab, monkeypatch, severity):  # noqa: F811
    monkeypatch.setattr(qc_warnings, "batch_warnings", fake(severity, code="WG_1_2S"))
    reviewed(app)
    with staff_api(app, "RV") as rv:
        page = rv.get("/results/batches/PB-1").text
        assert "WG_1_2S" in page and 'name="qc_ack"' not in page
        assert rv.post(f"{SAMPLE_1}/release", json={}).json()["released"] == ["Fe"]
    lab.rollback()
    assert lab.scalars(select(QcAcknowledgementRow)).all() == []


def test_no_runs_or_no_history_means_no_panel(app, lab):  # noqa: F811
    reviewed(app)
    with staff_api(app, "RV") as rv:
        page = rv.get("/results/batches/PB-1").text
        assert "QC trends on these runs" not in page
        assert rv.post(f"{SAMPLE_1}/release", json={}).json()["released"] == ["Fe"]
