"""The demo's QC history flags the demo's metals batches (slim-data D-144)."""

from datetime import UTC, datetime

from slim_quality.domain.enums import Severity

from slim_lims import qc_warnings
from slim_lims.demo_scenarios import seed_scenarios
from slim_lims.personas import sync_personas
from slim_lims.quality import seed_quality_history
from slim_lims.reports import plain_renderer
from slim_lims.results import publish_demo_rules
from tests.test_demo_scenarios import reference, specs  # noqa: F401 (fixture)


def test_the_demo_history_flags_the_demo_batches(db, reference):  # noqa: F811
    publish_demo_rules(db, reference)
    sync_personas(db)
    seed_scenarios(db, reference, specs, renderer=plain_renderer)
    db.commit()
    seed_quality_history(db, reference, lambda *_: {}, datetime.now(UTC), days=30)
    db.commit()
    found = qc_warnings.batch_warnings(db, "PB-00002", {"Fe", "Zn"})
    assert set(found) == {"Fe", "Zn"} and qc_warnings.worst(found["Fe"]) == Severity.ACTION
    assert any(w.code == "LIMIT_AHEAD" for w in found["Fe"])  # the forecast is advice too
    groups = qc_warnings.by_series(found)
    assert groups[0].level == "action" and len({g.slug for g in groups}) == len(groups)
    assert all(w.at <= max(x.at for x in found["Fe"]) for g in groups for w in g.warnings)
    assert qc_warnings.batch_warnings(db, "PB-00002", set()) == {}
