"""The host side of slim-samples (S-01…S-11): the bottles a sample arrives in, where they are, how long
they are kept and what happened to them.

slim-samples is a library like slim-prep: this app gives it people (`samples_actor_for`) and the TR facts
it doesn't own (`TrSampleLookup`: each lab sample, the analyses ordered for it, the customer's key).
slim-samples flushes; the routes commit.

Check-in is composed here (S-10): the app's check-in (receipt, chemical matching, the status) and the
bottles logged for each sample are one transaction. The screen requires bottles for every sample; the
machine contract (`POST /api/submissions/{tr}/check-in`, spec §12) is unchanged, so a check-in without
them leaves samples without bottles, and `/samples/log/{tr}` is where they are logged afterwards.
"""

from collections.abc import Mapping
from datetime import date

from slim_data.domain.values import SampleRef
from slim_samples.demo import DEMO_RULES
from slim_samples.domain.actor import Actor as SamplesActor
from slim_samples.domain.actor import IdentitySource, Role
from slim_samples.domain.containers import Container, ContainerIn, SampleInfo, flags
from slim_samples.domain.problems import Problem, Refused
from slim_samples.persistence import migrate as samples_migrate
from slim_samples.persistence.rules import PublishedRules, current_rules, publish_rules
from slim_samples.service import custody
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session, selectinload

from slim_lims.domain import TRSubmission
from slim_lims.models import LAB_ROLE_SUPERVISOR, TRSampleRow, TRSubmissionRow, User
from slim_lims.reference import ReferenceData
from slim_lims.results import SEED_ACTOR
from slim_lims.schemas import CheckInIn
from slim_lims.services import FieldError, SubmissionRejected, SubmissionService

RULES_NAME = "lab"  # the lab's rules, by name: the latest published version is current
# The demo lab's lists. The demo has no customer override: customers are the host's, and a lab sets
# one per customer when it publishes (a customer's key is str(customer ID)).
HOST_RULES = DEMO_RULES.model_copy(update={"name": RULES_NAME, "retention_overrides": ()})
BOTTLE_ROWS = 4  # rows offered per sample on the check-in screen; unused rows are left blank


def migrate_slim_samples(engine: Engine) -> None:
    """slim-samples' own tables, in the shared database, with its own Alembic version table."""
    with engine.begin() as connection:
        samples_migrate.upgrade(connection)


def samples_actor_for(user: User) -> SamplesActor:
    """A staff user as slim-samples sees them: a supervisor lifts holds and publishes the lab's lists
    (S-03). A persona is a claim, not a login (slim-data D-50)."""
    role = Role.SUPERVISOR if user.lab_role == LAB_ROLE_SUPERVISOR else Role.ANALYST
    source = IdentitySource.ASSERTED if user.is_persona else IdentitySource.AUTHENTICATED
    return SamplesActor(
        id=str(user.id), display=user.initials or user.email, roles=frozenset({role}), identity_source=source
    )


SEED_SAMPLES_ACTOR = SamplesActor(
    id=SEED_ACTOR.id,
    display=SEED_ACTOR.display,
    roles=frozenset({Role.QUALITY}),
    identity_source=IdentitySource.ASSERTED,
)


def rules(session: Session) -> PublishedRules:
    """The lab's current lists. A lab that has published none yet starts from the defaults (editable at
    /samples/rules): a fresh install can check samples in before anyone has set anything up."""
    published = current_rules(session, RULES_NAME)
    return published if published is not None else publish_demo_samples(session)


def publish_demo_samples(session: Session) -> PublishedRules:
    """The demo lab's container types, conditions, locations and retention. Idempotent: content-hashed."""
    return publish_rules(session, HOST_RULES, SEED_SAMPLES_ACTOR)


class TrSampleLookup:
    """slim-samples' `SampleLookup` over the app's TR tables: what each lab sample is, which analyses were
    ordered for it and the customer's key (for a retention override). Answers are cached per request."""

    def __init__(self, session: Session, reference: ReferenceData) -> None:
        self._session = session
        self._codes = {a.id: a.code for a in reference.catalog_analyses()}
        self._found: dict[str, SampleInfo | None] = {}

    def sample(self, sample_id: str) -> SampleInfo | None:
        if sample_id not in self._found:
            try:
                ref = SampleRef.parse(sample_id)
            except ValueError:
                self._found[sample_id] = None
            else:
                found = self._rows(
                    TRSubmissionRow.tr_number == ref.tr_number, TRSampleRow.position == ref.position
                )
                self._found[sample_id] = found[0] if found else None
        return self._found[sample_id]

    def for_tr(self, tr_number: str) -> list[SampleInfo]:
        return self._rows(TRSubmissionRow.tr_number == tr_number)

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
                analyses=frozenset(
                    self._codes[a.analysis_id] for a in row.analyses if a.analysis_id in self._codes
                ),
                customer=str(row.submission.customer_id),
            )
            self._found[sample_id] = info
            out.append(info)
        return out


def sample_id(tr_number: str, position: int) -> str:
    return f"{tr_number}-{position:02d}"


# ---------------------------------------------------------------- the check-in section


def bottle_field(position: int, row: int, name: str) -> str:
    return f"b{position}_{row}_{name}"


def parse_bottles(
    form: Mapping[str, str], samples: list[SampleInfo], published: PublishedRules
) -> dict[int, list[ContainerIn]]:
    """The check-in grid: per sample (by position) up to BOTTLE_ROWS rows, `b<pos>_<row>_<field>`. A row
    with no type is blank. Conditions and analyses are checkboxes (`…_cond_<code>`, `…_an_<code>`).
    Raises Refused for a count that isn't a number."""
    problems: list[Problem] = []
    out: dict[int, list[ContainerIn]] = {}
    for info in samples:
        position = int(info.sample.rsplit("-", 1)[1])
        rows = []
        for row in range(BOTTLE_ROWS):
            kind = form.get(bottle_field(position, row, "type"), "").strip()
            if not kind:
                continue
            where = f"sample {position}, row {row + 1}"
            count_text = form.get(bottle_field(position, row, "count"), "1").strip() or "1"
            if not count_text.isdigit():
                problems.append(Problem(where=where, message=f"how many? {count_text!r} isn't a number"))
                continue
            picked = frozenset(
                c.code
                for c in published.rules.conditions
                if form.get(bottle_field(position, row, f"cond_{c.code}"))
            )
            serves = frozenset(a for a in info.analyses if form.get(bottle_field(position, row, f"an_{a}")))
            try:
                rows.append(
                    ContainerIn(
                        container_type=kind,
                        count=int(count_text),
                        conditions=picked,
                        location=form.get(bottle_field(position, row, "location"), "").strip()
                        or published.rules.locations[0].code,
                        analyses=serves if serves != info.analyses else frozenset(),
                        note=form.get(bottle_field(position, row, "note"), "").strip(),
                    )
                )
            except ValueError as error:
                problems.append(Problem(where=where, message=str(error).splitlines()[0]))
        out[position] = rows
    if problems:
        raise Refused(problems)
    return out


def receive_bottles(
    db: Session,
    lookup: TrSampleLookup,
    tr_number: str,
    by_position: Mapping[int, list[ContainerIn]],
    user: User,
    received_on: date,
    *,
    only: set[int] | None = None,
) -> list[Container]:
    """Logs the bottles of each sample of the request (`only`: just those positions). Every sample
    needs at least one row; the first refusal names the sample. The caller rolls back on Refused."""
    published = rules(db)
    actor = samples_actor_for(user)
    made: list[Container] = []
    for info in lookup.for_tr(tr_number):
        position = int(info.sample.rsplit("-", 1)[1])
        if only is not None and position not in only:
            continue
        try:
            made += custody.receive(
                db,
                lookup,
                info.sample,
                by_position.get(position, []),
                actor=actor,
                rules=published,
                received_on=received_on,
            )
        except Refused as error:
            raise Refused(
                [
                    Problem(where=f"sample {position}", message=f"{p.where}: {p.message}")
                    for p in error.problems
                ]
            ) from None
    return made


def check_in_with_bottles(
    db: Session,
    service: SubmissionService,
    reference: ReferenceData,
    tr_number: str,
    data: CheckInIn,
    by_position: Mapping[int, list[ContainerIn]],
    user: User,
) -> TRSubmission:
    """The app's check-in and the bottles of every sample, one transaction (S-10). Raises what either
    raises; the caller rolls back."""
    submission = service.check_in(tr_number, data, user)
    try:
        receive_bottles(db, TrSampleLookup(db, reference), tr_number, by_position, user, data.date_received)
    except Refused as error:
        raise SubmissionRejected(
            [FieldError(("containers",), f"{p.where}: {p.message}") for p in error.problems]
        ) from None
    return submission


def unlogged_positions(db: Session, lookup: TrSampleLookup, tr_number: str) -> set[int]:
    """Positions of the request's samples that have no bottle logged yet."""
    infos = lookup.for_tr(tr_number)
    have = custody.containers_for(db, [i.sample for i in infos])
    return {int(i.sample.rsplit("-", 1)[1]) for i in infos if not have[i.sample]}


# ---------------------------------------------------------------- what the other screens show


def condition_notes(
    db: Session, reference: ReferenceData, tr_number: str
) -> dict[tuple[int, int], tuple[str, ...]]:
    """(sample position, analysis ID) -> what is wrong with the bottles that served it: the report says so
    (S-04). Finished bottles count: the result they gave stays what it is."""
    found: dict[str, list[Container]] = {}
    for c in custody.containers_with_prefix(db, f"{tr_number}-"):
        found.setdefault(c.sample, []).append(c)
    if not found:
        return {}
    ids = {a.code: a.id for a in reference.catalog_analyses()}
    notes: dict[tuple[int, int], tuple[str, ...]] = {}
    for sample, bottles in found.items():
        position = int(sample.rsplit("-", 1)[1])
        for analysis, items in flags(bottles).items():
            if analysis in ids:
                notes[(position, ids[analysis])] = tuple(dict.fromkeys(f.text for f in items))
    return notes
