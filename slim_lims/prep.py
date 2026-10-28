"""The host side of slim-prep, the prep book (slim-prep P-11…P-21, P6).

slim-prep is a library like slim-data: this app gives it people (`prep_actor_for`), the TR data it
doesn't own (`TrSamples`: each sample's state, analyses ordered and chemical) and the analysis facts
it hands to slim-data (analysis ID, the elements it measures, the unit spike amounts are given in).
slim-prep flushes; the routes commit.

A prep batch reaches slim-data through `send` (P-18, P-20): first send registers it, a later one sends
the correction; either way slim-data then re-evaluates whatever readings the batch already has.
Results rulesets are bound to (analysis, prep method), so the demo publishes the metals rules under
slim-prep's prep methods too.
"""

import json
from datetime import date, timedelta

from slim_data.domain.values import AnalyteRef, SampleRef
from slim_data.engine.ruleset import Ruleset
from slim_data.persistence.observations import batch_readings
from slim_data.persistence.observations import get_batch as results_batch
from slim_data.persistence.rulesets import publish_ruleset
from slim_data.rulesets import ruleset_text
from slim_data.service.workflow import evaluate_batch
from slim_prep.demo import DEMO_RULES, demo_stock
from slim_prep.domain.actor import Actor as PrepActor
from slim_prep.domain.actor import IdentitySource
from slim_prep.domain.actor import Role as PrepRole
from slim_prep.domain.ports import SampleInfo, SampleState
from slim_prep.persistence import books
from slim_prep.persistence import migrate as prep_migrate
from slim_prep.persistence._rows import NotFound
from slim_prep.persistence.rules import PublishedRules, current_rules, publish_rules
from slim_prep.service.handoff import send_correction, send_to_results, sent_version
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session, selectinload

from slim_lims.domain import TRStatus
from slim_lims.models import LAB_ROLE_SUPERVISOR, TRSampleAnalysisRow, TRSampleRow, TRSubmissionRow, User
from slim_lims.reference import ReferenceData
from slim_lims.results import SEED_ACTOR, actor_for, reference_lookup

RULES_NAME = DEMO_RULES.name  # the lab's prep rules, by name: the latest published version is current
# The unit an analysis's readings (and so its spike amounts) are in, by catalog code. Data, per lab.
PREP_ANALYSES = {"10_elements_icpms": "ppb", "36_elements_icpms": "ppb"}


def migrate_slim_prep(engine: Engine) -> None:
    """slim-prep's own tables, in the shared database, with its own Alembic version table."""
    with engine.begin() as connection:
        prep_migrate.upgrade(connection)


def prep_actor_for(user: User) -> PrepActor:
    """A staff user as slim-prep sees them. Until the app has a quality role, a supervisor publishes
    the prep rules (slim-prep P-17). A persona is a claim, not a login (P-27, as slim-data's D-50)."""
    role = PrepRole.SUPERVISOR if user.lab_role == LAB_ROLE_SUPERVISOR else PrepRole.ANALYST
    source = IdentitySource.ASSERTED if user.is_persona else IdentitySource.AUTHENTICATED
    return PrepActor(
        id=str(user.id), display=user.initials or user.email, roles=frozenset({role}), identity_source=source
    )


SEED_PREP_ACTOR = PrepActor(
    id=SEED_ACTOR.id, display=SEED_ACTOR.display, roles=frozenset({PrepRole.QUALITY}),
    identity_source=IdentitySource.ASSERTED,
)  # fmt: skip


def rules(session: Session) -> PublishedRules:
    published = current_rules(session, RULES_NAME)
    if published is None:
        raise NotFound(f"no published prep rules {RULES_NAME!r}: run the demo seed")
    return published


def publish_demo_prep(session: Session, reference: ReferenceData, today: date) -> PublishedRules:
    """The demo's prep rules and stock, and the metals results rules under each prep method. Idempotent:
    rules are content-hashed and the stock is recorded once."""
    published = publish_rules(session, DEMO_RULES, SEED_PREP_ACTOR)
    stock = demo_stock(today + timedelta(days=365))
    try:
        books.get_stock(session, stock.name, stock.lot)
    except NotFound:
        books.save_stock(session, stock, SEED_PREP_ACTOR)
    by_code = {a.code: a.id for a in reference.catalog_analyses()}
    body = json.loads(ruleset_text("demo_metals"))
    for code in PREP_ANALYSES:
        if code not in by_code:
            continue
        for method in DEMO_RULES.methods:
            ruleset = Ruleset.model_validate(
                {**body, "analysis_id": by_code[code], "prep_method": method.name}
            )
            publish_ruleset(session, ruleset, SEED_ACTOR)
    return published


_STATE = {
    TRStatus.SUBMITTED: SampleState.NOT_RECEIVED,
    TRStatus.RECEIVED: SampleState.OPEN,
    TRStatus.IN_PROGRESS: SampleState.OPEN,
    TRStatus.PARTIAL_REPORT: SampleState.OPEN,
    TRStatus.COMPLETE_REPORT: SampleState.OPEN,
    TRStatus.INVOICED: SampleState.CLOSED,
    TRStatus.CANCELLED: SampleState.CANCELLED,
}


class TrSamples:
    """slim-prep's `SampleLookup` over the app's TR tables (P-23; review B1): what each lab sample is,
    whether it may be prepped, and what was ordered for it. One per request; answers are cached."""

    def __init__(self, session: Session, reference: ReferenceData) -> None:
        self._session = session
        self._codes = {a.id: a.code for a in reference.catalog_analyses()}
        self._ids = {code: id_ for id_, code in self._codes.items()}
        self._found: dict[str, SampleInfo | None] = {}

    def sample(self, sample_id: str) -> SampleInfo | None:
        if sample_id not in self._found:
            try:
                ref = SampleRef.parse(sample_id)
            except ValueError:
                self._found[sample_id] = None
            else:
                rows = self._rows(
                    TRSubmissionRow.tr_number == ref.tr_number, TRSampleRow.position == ref.position
                )
                self._found[sample_id] = rows[0] if rows else None
        return self._found[sample_id]

    def open_samples(self, analysis: str) -> tuple[SampleInfo, ...]:
        """The received samples that ordered this analysis, oldest request first: the picker's list."""
        if analysis not in self._ids:
            return ()
        open_statuses = [int(s) for s, state in _STATE.items() if state == SampleState.OPEN]
        ordered = select(TRSampleAnalysisRow.sample_id).where(
            TRSampleAnalysisRow.analysis_id == self._ids[analysis]
        )
        return tuple(self._rows(TRSubmissionRow.status.in_(open_statuses), TRSampleRow.id.in_(ordered)))

    def _rows(self, *where) -> list[SampleInfo]:
        rows = self._session.scalars(
            select(TRSampleRow)
            .join(TRSubmissionRow, TRSampleRow.submission_id == TRSubmissionRow.id)
            .where(*where)
            .options(selectinload(TRSampleRow.analyses), selectinload(TRSampleRow.submission))
            .order_by(TRSubmissionRow.tr_number, TRSampleRow.position)
        ).all()
        out = []
        for row in rows:
            sample_id = f"{row.submission.tr_number}-{row.position:02d}"
            info = SampleInfo(
                sample=sample_id,
                state=_STATE[TRStatus(row.submission.status)],
                analyses=frozenset(
                    self._codes[a.analysis_id] for a in row.analyses if a.analysis_id in self._codes
                ),
                chemical=row.chemical_name,
                chemical_id=row.chemical_id,
            )
            self._found[sample_id] = info
            out.append(info)
        return out


def send(session: Session, code: str, user: User, reference: ReferenceData, specs) -> dict:
    """Sends the batch to slim-data (first time) or its correction (after), then re-evaluates the batch
    there if it has readings. Raises slim-prep's PrepRefused / NotFound, or LookupError."""
    batch = books.get_batch(session, code).item
    analysis = next((a for a in reference.catalog_analyses() if a.code == batch.analysis), None)
    if analysis is None or batch.analysis not in PREP_ANALYSES:
        raise LookupError(f"analysis {batch.analysis!r} can't take prep batches here")
    lookup = reference_lookup(session, reference, specs)
    analytes = {
        e.symbol
        for e in reference.element_choices()
        if lookup.analyte_valid(AnalyteRef(analysis_id=analysis.id, code=e.symbol))
    }
    actor = actor_for(user)
    # slim-prep checks every sample against its TR again: a cancelled one takes no new work (D-99, P-23)
    samples = TrSamples(session, reference)
    args = (session, code, actor, samples, analysis.id, analytes, PREP_ANALYSES[batch.analysis])
    if sent_version(session, code) is None:
        send_to_results(*args)
        changed = None
    else:
        _, changed = send_correction(*args)
    evaluated = None
    if batch_readings(session, results_batch(session, code).id):  # readings came before the correction
        receipt = evaluate_batch(session, code, actor, lookup, reason=f"prep batch {code} corrected")
        evaluated = {
            "new_versions": len(receipt.new_versions),
            "released_affected": [str(k) for k in receipt.released_affected],
        }
    return {
        "code": code,
        "sent_version": sent_version(session, code),
        "changed": changed,
        "evaluated": evaluated,
    }
