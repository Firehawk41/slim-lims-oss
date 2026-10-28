"""slim-prep's demo scenario on a real TR (P7): prepped, sent, run, released, corrected."""

from datetime import date
from decimal import Decimal

import pytest
from slim_data.domain.enums import ReadingRole, ResultStatus
from slim_data.domain.values import AnalyteRef, SampleRef
from slim_data.persistence import migrate
from slim_data.persistence.observations import evaluation_input
from slim_data.persistence.results import load_result
from slim_data.service.verify import verify
from slim_prep.persistence import books
from slim_prep.persistence import migrate as prep_migrate
from slim_prep.service.handoff import sent_version
from slim_prep.service.verify import verify as verify_prep

from slim_lims import cli
from slim_lims.demo_prep import CODE, seed_prep_scenario
from slim_lims.personas import sync_personas
from slim_lims.prep import publish_demo_prep
from slim_lims.results import publish_demo_rules
from tests.test_demo_scenarios import TEN, reference, specs  # noqa: F401 (fixture)


@pytest.fixture
def prepped(engine, db, reference):  # noqa: F811
    with engine.begin() as connection:
        prep_migrate.downgrade(connection)
        migrate.downgrade(connection)
        migrate.upgrade(connection)
        prep_migrate.upgrade(connection)
    publish_demo_rules(db, reference)
    publish_demo_prep(db, reference, date.today())
    sync_personas(db)
    tr = seed_prep_scenario(db, reference, specs)
    db.commit()
    return tr


def test_the_scenario_preps_runs_releases_and_corrects(db, prepped):
    assert prepped == "TR00001" and seed_prep_scenario(db, None, specs) is None  # once only
    recorded = books.get_batch(db, CODE)
    assert (
        recorded.version == 2 and recorded.reason.startswith("Sample 1 cup 2") and sent_version(db, CODE) == 2
    )
    readings = evaluation_input(db, CODE).readings
    cup = next(r for r in readings if r.sample == SampleRef.parse("TR00001-01") and r.role == ReadingRole.CUP
               and r.replicate == 2 and r.analyte.code == "Cu")  # fmt: skip
    assert cup.dilution == Decimal("0.066")  # (2.66 − 2) ÷ 10: the correction reached the readings
    cu = load_result(db, SampleRef.parse("TR00001-01"), AnalyteRef(analysis_id=TEN, code="Cu"))
    assert cu.status == ResultStatus.RELEASED  # released before the correction: it waits for its own
    assert verify(db).ok and verify_prep(db).ok


def test_cli_verify_checks_the_prep_book_too(engine, prepped, monkeypatch, capsys):
    monkeypatch.setenv("DATABASE_URL", engine.url.render_as_string(hide_password=False))
    assert cli.main(["verify"]) == 0
    assert "Prep book: 2 standard and 2 batch versions, 1 sent: all hold." in capsys.readouterr().out
