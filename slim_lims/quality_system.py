"""slim-quality's batch 5c on the host side (slim-data D-134…D-137): where it meets the other modules.

* `involved_in_clause`: who had a hand in a clause's lab work in the year before a day, from the acts the
  modules recorded (D-132's `lab_acts`), so an internal auditor doesn't audit their own work (D-135).
  slim-quality adds whoever kept the clause's records in it.
* `installed_versions` / `validation_pack`: the §7.11 pack (D-137): each module as installed (its commit,
  from pip's record of the install), its migrations as the database stands, slim-data's steps,
  slim-quality's tools, `verify` run now (read-only), and a link to CI's results for each commit.
* `readiness_facts`: slim-quality's readiness inputs with what only the host knows (D-136).
* `seed_demo_quality_system`: the demo's risks and audit programme, once.
"""

import json
from datetime import date, timedelta
from importlib import metadata

from slim_data.engine.registry import default_registry
from slim_quality.domain.clauses import CLAUSES, clause_order
from slim_quality.domain.competence import Act, Person
from slim_quality.domain.internal_audits import (
    AuditEntry,
    AuditEntryKind,
    ChecklistLine,
    InternalAudit,
    LineOutcome,
)
from slim_quality.domain.readiness import Facts
from slim_quality.domain.risks import Risk, RiskEntry, RiskEntryKind, RiskKind, RiskSource
from slim_quality.monitors.registry import GROUP_TOOLS, SERIES_TOOLS, TOOLS
from slim_quality.persistence import store
from slim_quality.service import internal_audits, risks
from slim_quality.service import readiness as rd
from sqlalchemy import inspect, select, text
from sqlalchemy.orm import Session

from slim_lims.audit import chain_check
from slim_lims.labtime import LabClock
from slim_lims.models import User
from slim_lims.quality import QUALITY_PERSONAS, quality_actor
from slim_lims.quality_insights import TrDates, lab_acts
from slim_lims.reference import ReferenceData

# Which of the modules' acts are a clause's lab work (D-135). The quality clauses' record keepers are
# slim-quality's to say.
CLAUSE_ACTS: dict[str, frozenset[Act]] = {
    "7.2": frozenset({Act.PREP, Act.ENTER, Act.REVIEW, Act.RELEASE}),
    "7.4": frozenset({Act.PREP}),
    "7.5": frozenset({Act.ENTER, Act.REVIEW, Act.RELEASE}),
    "7.7": frozenset({Act.REVIEW, Act.RELEASE}),
    "7.8": frozenset({Act.RELEASE, Act.SIGN}),
}

# The modules the app installs (D-117): the distribution and its repository.
MODULES = {
    "slim-data": "slim-data-oss",
    "slim-prep": "slim-prep-oss",
    "slim-samples": "slim-samples-oss",
    "slim-quality": "slim-quality-oss",
    "slim-domain": "slim-domain-oss",
    "slim-invoicing": "SLIM-Invoicing-Program",
}
GITHUB = "https://github.com/Firehawk41"
NEVER_AUDITED = ("6.6", "7.3", "8.6")  # the demo's gaps


def involved_in_clause(
    session: Session, reference: ReferenceData, clock: LabClock, clause: str, on: date
) -> frozenset[str]:
    acts = CLAUSE_ACTS.get(clause)
    if not acts:
        return frozenset()
    start = on - timedelta(days=365)
    return frozenset(
        a.person.id for a in lab_acts(session, reference, clock) if a.act in acts and start <= a.on <= on
    )


# --- the validation pack (D-137) ----------------------------------------------------------------------


def _installed(name: str) -> dict:
    """What pip recorded of the install: the commit for a git install, the path for an editable one."""
    try:
        dist = metadata.distribution(name)
    except metadata.PackageNotFoundError:
        return {"version": "not installed", "commit": None}
    found = {"version": dist.version, "commit": None}
    raw = dist.read_text("direct_url.json")
    if raw:
        record = json.loads(raw)
        commit = record.get("vcs_info", {}).get("commit_id")
        if commit:
            found["commit"] = commit
        elif record.get("dir_info", {}).get("editable"):
            found["version"] = f"{dist.version} (editable, a local checkout)"
    return found


def installed_versions() -> dict[str, str]:
    """Module → its commit, else its version as installed: what a signature covers."""
    found = {}
    for name in MODULES:
        info = _installed(name)
        found[name] = info["commit"] or info["version"]
    return found


def _migrations(session: Session) -> dict[str, str | None]:
    bind = session.get_bind()
    found = {}
    for table in sorted(t for t in inspect(bind).get_table_names() if t.startswith("alembic_version")):
        found[table] = session.scalar(text(f'SELECT version_num FROM "{table}"'))  # noqa: S608 (names from the schema)
    return found


def _verify(session: Session) -> dict:
    from slim_data.service.verify import verify
    from slim_prep.service.verify import verify as verify_prep
    from slim_quality.service.verify import verify as verify_quality
    from slim_samples.service import custody

    data = verify(session, chain_check(session.get_bind()))
    prep = verify_prep(session)
    quality = verify_quality(session)
    bottles = custody.verify(session)
    return {
        "slim-data": {
            "ok": data.ok,
            "detail": f"{data.versions} result versions replayed, {len(data.mismatches)} differ",
        },
        "audit chain": {
            "ok": data.chain_checked and not data.broken_entries,
            "detail": "not checked (no audit log on this database)"
            if not data.chain_checked
            else ("intact" if not data.broken_entries else f"broken at {data.broken_entries}"),
        },
        "slim-prep": {
            "ok": prep.ok,
            "detail": f"{prep.standards} standards, {prep.batches} batches; {len(prep.problems)} problems",
        },
        "slim-quality": {
            "ok": quality.ok,
            "detail": f"{quality.nonconformities} NCs, {quality.risks} risks, "
            f"{quality.internal_audits} internal audits; "
            f"{len(quality.problems)} problems",
        },
        "slim-samples": {"ok": not bottles, "detail": f"{len(bottles)} problems"},
    }


def validation_pack(session: Session, clock: LabClock) -> dict:
    """The §7.11 pack, as JSON-ready data: what a quality manager signs (D-137)."""
    modules = []
    for name, repo in MODULES.items():
        info = _installed(name)
        commit = info["commit"]
        modules.append(
            {
                "module": name,
                "repository": f"{GITHUB}/{repo}",
                "version": info["version"],
                "commit": commit,
                "ci": f"{GITHUB}/{repo}/commit/{commit}" if commit else None,
            }
        )
    return {
        "generated": clock.now().isoformat(timespec="seconds"),
        "versions": installed_versions(),
        "modules": modules,
        "migrations": _migrations(session),
        "steps": default_registry().keys(),
        "quality_tools": sorted({*TOOLS, *SERIES_TOOLS, *GROUP_TOOLS}),
        "verify": _verify(session),
    }


# --- readiness (D-136) --------------------------------------------------------------------------------


def readiness_inputs(session: Session, reference: ReferenceData, clock: LabClock) -> dict:
    return {
        "turnaround_source": TrDates(session, reference, clock),
        "acts": lab_acts(session, reference, clock),
        "day_of": lambda at: clock.local(at).date(),
    }


def readiness_facts(session: Session, reference: ReferenceData, clock: LabClock) -> Facts:
    return rd.gather(
        session, clock.today(), versions=installed_versions(), **readiness_inputs(session, reference, clock)
    )


# --- the demo -----------------------------------------------------------------------------------------


def seed_demo_quality_system(session: Session, today: date) -> int:
    """The demo's risk register (as Jamie was shown it) and this year's audit programme: 7.8 audited
    with a finding left for a visitor to raise, 6.4 overdue, 8.5 and 6.2 to come, and the year before's.
    No management review
    and no validation signature: readiness shows them red until a visitor signs. Once."""
    if store.list_risks(session) or store.list_internal_audits(session):
        return 0
    users = {
        email: session.scalars(select(User).where(User.email == email)).first()
        for email in QUALITY_PERSONAS.values()
    }
    manager = quality_actor(session, users[QUALITY_PERSONAS["manager"]])
    officer = quality_actor(session, users[QUALITY_PERSONAS["user"]])
    sue = session.scalars(select(User).where(User.email == "sue@demo.example")).first()
    if manager is None or officer is None or sue is None:
        return 0
    uma = Person(id=officer.id, display=users[QUALITY_PERSONAS["user"]].full_name or officer.display)
    sue_p = Person(id=str(sue.id), display=sue.full_name or sue.initials)
    review = today + timedelta(days=120)
    register = [
        Risk(
            title="The ICP-MS is the only one; a breakdown stops all metals work",
            source=RiskSource.EQUIPMENT,
            owner="Sue",
            likelihood=2,
            severity=5,
            review_by=review,
            raised_by=manager,
        ),
        Risk(
            title="A supervisor reviews results for a customer they consult for",
            source=RiskSource.IMPARTIALITY,
            owner="Quinn",
            likelihood=1,
            severity=4,
            review_by=today + timedelta(days=60),
            raised_by=manager,
        ),
        Risk(
            title="Rush requests come in after the cut-off and skip the review queue",
            source=RiskSource.PROCESS,
            owner="Sue",
            likelihood=3,
            severity=3,
            review_by=today - timedelta(days=7),
            raised_by=officer,
        ),
        Risk(
            kind=RiskKind.OPPORTUNITY,
            title="Customers ask for a mercury method we don't run",
            source=RiskSource.CUSTOMER,
            owner="Sue",
            review_by=today + timedelta(days=200),
            raised_by=manager,
        ),
    ]
    created = [risks.raise_risk(session, r) for r in register]
    risks.add_entry(
        session,
        created[1].id,
        RiskEntry(
            kind=RiskEntryKind.ACTION_PLANNED,
            action="A declaration of interests each year",
            owner="Quinn",
            due=today + timedelta(days=30),
            by=manager,
        ),
    )
    risks.add_entry(
        session,
        created[3].id,
        RiskEntry(
            kind=RiskEntryKind.ACTION_PLANNED,
            action="Validate a mercury method in Q2",
            owner="Sue",
            due=today + timedelta(days=180),
            by=manager,
        ),
    )

    def plan(clause: str, day: date, auditor: Person, area: str = "") -> InternalAudit:
        audit = InternalAudit(clause=clause, area=area, planned_for=day, auditor=auditor, planned_by=manager)
        return internal_audits.plan(session, audit)  # seeded before anyone acts: nobody is involved yet

    year_start = date(today.year, 1, 1)
    reporting = plan("7.8", max(year_start, today - timedelta(days=25)), uma, "issued reports")
    lines = (
        ChecklistLine(
            clause="7.8.2",
            checked="Five issued reports carry every item §7.8.2 lists",
            outcome=LineOutcome.CONFORMING,
        ),
        ChecklistLine(
            clause="7.8.6",
            checked="Statements of conformity name their decision rule",
            outcome=LineOutcome.FINDING,
            evidence="Two of five reports state 'meets spec' with no decision rule given",
        ),
        ChecklistLine(
            clause="7.8.8",
            checked="Amended reports are identified as such",
            outcome=LineOutcome.OBSERVATION,
            evidence="No amendment in the sample; the procedure covers it",
        ),
    )
    internal_audits.add_entry(
        session,
        reporting.id,
        AuditEntry(
            kind=AuditEntryKind.CONDUCTED,
            on=max(year_start, today - timedelta(days=20)),
            lines=lines,
            note="Reporting broadly in order; one finding on decision rules.",
            by=officer,
        ),
    )
    plan("6.4", max(year_start, today - timedelta(days=10)), uma, "balances and pipettes")
    # The year before: most clauses audited and conforming, 4.2 longer ago than the profile allows, and a few
    # never audited, so readiness shows a lab part-way through its cycle rather than a wall of red.
    earlier = [c for c in sorted(CLAUSES, key=clause_order) if c not in NEVER_AUDITED]
    for n, clause in enumerate(earlier):
        day = today - timedelta(days=380 if clause == "4.2" else 330 - n * 10)
        audit = plan(clause, day, sue_p if clause == "8.5" else uma)
        line = ChecklistLine(
            clause=clause, checked="Walked through against the procedure", outcome=LineOutcome.CONFORMING
        )
        entry = AuditEntry(
            kind=AuditEntryKind.CONDUCTED, on=day, lines=(line,), note="Conforming.", by=officer
        )
        internal_audits.add_entry(session, audit.id, entry)
    plan("8.5", today + timedelta(days=30), sue_p)
    plan("6.2", today + timedelta(days=45), sue_p)
    return len(created) + 4 + len(earlier)
