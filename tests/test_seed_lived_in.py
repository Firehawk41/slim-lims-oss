"""The Render outage of 2026-11-17: a demo step added later met data the older steps had already written.

A deploy seeds a database that has lived: every step but the new one ran long ago. So each one-time demo step
is run here after all the others' data exists, and must go through without a failure. Needs the OSS demo.
"""

import logging

import pytest

from slim_lims.demo_seed import DEFAULT_OSS_DIR, seed_demo

pytestmark = pytest.mark.skipif(not DEFAULT_OSS_DIR.exists(), reason="the OSS demo isn't checked out")

ONE_TIME_STEPS = [
    "equipment register",
    "authorisations",
    "risks and audits",
    "the day's board",
    "a quote request",
    "bottles",
    "QC history",
    "a nonconformity",
    "quality records",
]


@pytest.mark.parametrize("new_step", ONE_TIME_STEPS)
def test_a_step_added_later_seeds_a_lived_in_database(tmp_path, new_step, caplog):
    url = f"sqlite:///{tmp_path / 'lived_in.db'}"
    seed_demo(url, leave_out=frozenset({new_step}))  # the older seed, without the new step
    with caplog.at_level(logging.ERROR, logger="slim_lims.demo_seed"):
        report = seed_demo(url)  # the deploy that brings it
    assert report.skipped == [], caplog.text


def test_a_failing_demo_step_is_left_out_and_the_seed_goes_on(tmp_path, monkeypatch, capfd):
    import slim_lims.demo_seed as ds

    def broken(*_a, **_k):
        raise RuntimeError("boom")

    monkeypatch.setattr(ds, "seed_demo_quality_system", broken)
    report = seed_demo(f"sqlite:///{tmp_path / 'demo.db'}")
    assert report.skipped == ["risks and audits"]
    err = capfd.readouterr().err  # Render's log: Alembic's logging config must not silence the seed's
    assert "risks and audits failed" in err and "boom" in err
    assert report.personas and report.scenarios  # the steps after it still ran
