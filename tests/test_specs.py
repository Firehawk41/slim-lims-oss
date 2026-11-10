"""Spec limit changes (slim-data D-143): quality staff and quality managers propose a change to one limit
set; a quality manager or a lab supervisor who didn't propose it approves (written through slim-domain,
unreleased results re-evaluated, released ones that would now read differently listed) or rejects it."""

import pytest
from sqlalchemy import select, text, update
from sqlalchemy.exc import DBAPIError

from slim_lims import spec_changes as sc
from slim_lims.main import create_app
from slim_lims.models import (
    LAB_ROLE_ANALYST,
    ROLE_STAFF,
    SpecDecisionRow,
    SpecProposalRow,
    User,
    UserCapability,
)
from slim_lims.security import hash_password
from tests.conftest import PASSWORD, csrf_from, login, make_settings
from tests.test_demo_scenarios import SPECS, reference, seeded, specs  # noqa: F401 (fixtures)

B, IPA = 2, 2  # customer B's IPA: Zn 0.2, Fe 0.3 in the demo data


@pytest.fixture
def spec_table(engine, db):
    """slim-domain's own specifications table, holding the demo's set (what the results were judged by)."""
    import slim_domain.domain.specification.specification_repository  # noqa: F401 (registers it)
    from infrastructure.database import Base

    tables = [Base.metadata.tables["specifications"], Base.metadata.tables["chemical_report_settings"]]
    for table in tables:
        table.drop(engine, checkfirst=True)
        table.create(engine)
    with engine.begin() as conn:
        for element, value in SPECS[(B, IPA, "")].items():
            conn.execute(
                text(
                    "INSERT INTO specifications (kind, customer_id, customer_name_raw, chemical_id,"
                    " chemical_name_raw, element_symbol, value, unit, category_label, parameter_text,"
                    " location)"
                    " VALUES ('specification', :c, 'B', :ch, 'IPA', :e, :v, 'ppb', 'Trace Metals', :e, '')"
                ),
                {"c": B, "ch": IPA, "e": element, "v": value},
            )
    yield engine
    db.rollback()
    for table in tables:
        table.drop(engine, checkfirst=True)


def staff(db, email, initials, lab_role=LAB_ROLE_ANALYST, grant=None):
    user = User(email=email, password_hash=hash_password(PASSWORD), role=ROLE_STAFF, initials=initials,
                lab_role=lab_role, full_name=email)  # fmt: skip
    db.add(user)
    db.flush()
    if grant:
        db.add(UserCapability(user_id=user.id, capability=grant, granted_by=user.id))
    db.commit()
    return user


@pytest.fixture
def people(db, staff_user):
    return {
        "uma": staff(db, "uma@lab.example", "UMA", grant="quality"),
        "quinn": staff(db, "quinn@lab.example", "QN", grant="quality_manager"),
        "ana": staff(db, "ana@lab.example", "AN"),
        "sue": staff_user,  # a supervisor
    }


def test_who_proposes_and_who_decides(db, people):
    assert sc.may_propose(db, people["uma"]) and sc.may_propose(db, people["quinn"])
    assert not sc.may_propose(db, people["ana"]) and not sc.may_propose(db, people["sue"])
    assert sc.may_decide(db, people["quinn"]) and sc.may_decide(db, people["sue"])
    assert not sc.may_decide(db, people["uma"]) and not sc.may_decide(db, people["ana"])


def test_limits_typed_on_the_form_are_checked():
    known = {"Fe", "Zn", "Cu"}
    assert sc.parse_limits([("Fe", "0.30"), ("", ""), ("Zn", "1e-1")], known) == {"Fe": "0.3", "Zn": "0.1"}
    with pytest.raises(sc.SpecRefused) as refused:
        sc.parse_limits([("Xx", "1"), ("Fe", "lots"), ("Zn", "0"), ("Cu", "1"), ("Cu", "2")], known)
    assert str(refused.value) == (
        "Xx: not an element; Fe: 'lots' isn't a number; Zn: a limit is a positive number; Cu appears twice"
    )


@pytest.fixture
def app(engine, reference):  # noqa: F811
    return create_app(make_settings(str(engine.url)), engine=engine, reference=reference)


def propose(client, kind="specification", **values):
    page = client.get(f"/specs/edit?customer={B}&chemical={IPA}&location=&kind={kind}")
    assert page.status_code == 200
    rows = list(values.items())
    body = {
        "customer": str(B), "chemical": str(IPA), "location": "", "kind": kind, "unit": "ppb",
        "element": [e for e, _ in rows], "value": [v for _, v in rows],
        "reason": "the customer's specification sheet, rev. C", "csrf_token": csrf_from(page.text),
    }  # fmt: skip
    return client.post("/specs/proposals", data=body, follow_redirects=True)


def test_a_change_is_proposed_approved_by_another_and_bears_on_the_results(
    client, db, seeded, spec_table, people
):
    login(client, "uma@lab.example")
    assert client.get("/specs").status_code == 200
    unchanged = propose(client, Fe="0.3", Zn="0.2")
    assert "Nothing changes" in unchanged.text
    made = propose(client, Fe="0.29", Zn="0.2", Cu="5")  # Fe tightened, Cu added
    assert "Proposal 1 made" in made.text and "changed" in made.text and "added" in made.text
    assert "already has a proposal waiting" in propose(client, Fe="1").text
    csrf = csrf_from(made.text)
    own = client.post("/specs/proposals/1/approve", data={"csrf_token": csrf}, follow_redirects=True)
    assert "Not approved" in own.text  # a quality user doesn't approve, nor does the proposer
    db.rollback()
    with sc_session(spec_table) as slim:
        assert sc.current_set(slim, B, IPA, "", "specification")[0] == {"Fe": "0.3", "Zn": "0.2"}  # not yet

    client.cookies.clear()
    login(client, "staff@lab.example")  # a lab supervisor
    page = client.get("/specs/proposals/1")
    assert "Approve: put these limits on file" in page.text
    done = client.post(
        "/specs/proposals/1/approve", data={"csrf_token": csrf_from(page.text)}, follow_redirects=True
    )
    assert "Approved: the limits are on file" in done.text
    db.rollback()
    with sc_session(spec_table) as slim:
        assert sc.current_set(slim, B, IPA, "", "specification") == (
            {"Cu": "5", "Fe": "0.29", "Zn": "0.2"},
            "ppb",
        )
    decision = db.scalars(select(SpecDecisionRow)).one()
    assert decision.outcome == "approved" and decision.by == "staff@lab.example" and decision.reevaluated
    # the released Fe of 0.295 ppb passed 0.3 and fails 0.29: listed, left as released
    assert decision.now_differ == ["TR00002-01 Fe 0.295 ppb: passes → fails"]
    assert "Raise a nonconformity for them" in done.text
    db.rollback()
    assert client.post("/specs/proposals/1/approve", data={"csrf_token": csrf_from(done.text)},
                       follow_redirects=True).text.count("already decided") == 1  # fmt: skip


def test_a_quality_manager_rejects_with_a_reason_and_nothing_changes(client, db, seeded, spec_table, people):
    login(client, "uma@lab.example")
    propose(client, Fe="9")
    client.cookies.clear()
    login(client, "quinn@lab.example")
    csrf = csrf_from(client.get("/specs/proposals/1").text)
    blank = client.post(
        "/specs/proposals/1/reject", data={"reason": " ", "csrf_token": csrf}, follow_redirects=True
    )
    assert "Not rejected" in blank.text
    done = client.post(
        "/specs/proposals/1/reject", data={"reason": "old sheet", "csrf_token": csrf}, follow_redirects=True
    )
    assert "Rejected" in done.text and "old sheet" in done.text
    db.rollback()
    with sc_session(spec_table) as slim:
        assert sc.current_set(slim, B, IPA, "", "specification")[0] == {"Fe": "0.3", "Zn": "0.2"}


def test_a_proposal_goes_stale_if_the_set_changed_since(client, db, seeded, spec_table, people):
    login(client, "uma@lab.example")
    propose(client, Fe="0.3", Zn="0.25")
    db.rollback()
    with spec_table.begin() as conn:
        conn.execute(text("UPDATE specifications SET value = 0.5 WHERE element_symbol = 'Fe'"))
    client.cookies.clear()
    login(client, "quinn@lab.example")
    page = client.get("/specs/proposals/1")
    done = client.post(
        "/specs/proposals/1/approve", data={"csrf_token": csrf_from(page.text)}, follow_redirects=True
    )
    assert "changed since this was proposed" in done.text
    db.rollback()
    assert db.scalars(select(SpecDecisionRow)).all() == []


def test_warning_limits_change_without_re_evaluating(client, db, seeded, spec_table, people):
    login(client, "quinn@lab.example")  # a quality manager proposes; a supervisor approves
    propose(client, kind="warning_limit", Fe="0.25")
    client.cookies.clear()
    login(client, "staff@lab.example")
    page = client.get("/specs/proposals/1")
    client.post("/specs/proposals/1/approve", data={"csrf_token": csrf_from(page.text)})
    db.rollback()
    decision = db.scalars(select(SpecDecisionRow)).one()
    assert decision.outcome == "approved" and decision.reevaluated == [] and decision.now_differ == []


def test_others_are_kept_out_and_the_record_is_append_only(engine, client, db, seeded, spec_table, people):
    login(client, "ana@lab.example")
    assert client.get("/specs").status_code == 403
    assert client.get(f"/specs/edit?customer={B}&chemical={IPA}&kind=specification").status_code == 403
    db.add(
        SpecProposalRow(
            customer_id=B, chemical_id=IPA, location="", kind="specification", unit="ppb", before={},
            after={"Fe": "1"}, reason="x", proposed_by="x", proposed_by_id="x",
        )
    )  # fmt: skip
    db.commit()
    with pytest.raises(DBAPIError), engine.begin() as connection:
        connection.execute(update(SpecProposalRow).values(reason="y"))


def sc_session(engine):
    from slim_lims.refdata import slim_session

    return slim_session(engine)


def test_s1_a_batch_that_cant_be_re_evaluated_doesnt_undo_the_approval(
    client, db, seeded, spec_table, people, monkeypatch
):
    """The limits are on file once slim-domain writes them; a batch that can't be evaluated (no ruleset)
    is named on the decision, never a 500 that leaves the set changed and the decision unrecorded."""

    def no_ruleset(*_args, **_kwargs):
        raise LookupError("no published ruleset for analysis 1 / dry ash")

    monkeypatch.setattr(sc, "evaluate_batch", no_ruleset)
    login(client, "uma@lab.example")
    propose(client, Fe="0.29", Zn="0.2")
    client.cookies.clear()
    login(client, "staff@lab.example")
    page = client.get("/specs/proposals/1")
    done = client.post(
        "/specs/proposals/1/approve", data={"csrf_token": csrf_from(page.text)}, follow_redirects=True
    )
    assert done.status_code == 200 and "Not re-evaluated, do it by hand: PB-" in done.text
    db.rollback()
    decision = db.scalars(select(SpecDecisionRow)).one()
    assert (
        decision.outcome == "approved"
        and decision.reevaluated == []
        and "no published ruleset" in decision.reason
    )


def test_r7_the_set_the_decision_and_the_new_versions_commit_together(
    client, db, seeded, spec_table, people, monkeypatch
):
    """slim-domain's tables in the app's database: approval is one transaction. A write slim-domain
    refuses leaves no decision and no new result version behind (and the proposal still waiting)."""
    from slim_data.persistence.results import results_for_tr
    from slim_domain.domain.specification.specification_service import SpecificationService

    def versions():
        return sorted(
            (str(r.sample), r.analyte.code, r.current.version) for r in results_for_tr(db, "TR00002")
        )

    before = versions()
    db.rollback()

    def refuse(*_args, **_kwargs):
        raise ValueError("refused by slim-domain")

    monkeypatch.setattr(SpecificationService, "set_limits", refuse)
    login(client, "uma@lab.example")
    propose(client, Fe="0.29", Zn="0.2")
    client.cookies.clear()
    login(client, "staff@lab.example")
    page = client.get("/specs/proposals/1")
    done = client.post(
        "/specs/proposals/1/approve", data={"csrf_token": csrf_from(page.text)}, follow_redirects=True
    )
    assert (
        "Not approved: refused by slim-domain" in done.text
        and "Approve: put these limits on file" in done.text
    )
    db.rollback()
    assert db.scalars(select(SpecDecisionRow)).all() == [] and versions() == before
    with sc_session(spec_table) as slim:
        assert sc.current_set(slim, B, IPA, "", "specification")[0] == {"Fe": "0.3", "Zn": "0.2"}
