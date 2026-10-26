"""slim-data's named demo scenarios, seeded into the app (slim-data D-75). Demo databases only.

slim-data's scenarios are host-agnostic: this module is the app's `DemoHost`. Each scenario
gets a real TR (submitted and checked in by the supervisor persona, ordering its demo analysis,
D-78: customer A's IPA, or customer B's when the scenario needs a spec; an assay is of HNO3), readings
uploaded by the analyst persona and decisions by the reviewer and supervisor personas, so a first visitor
already sees history. Afterwards each TR's status follows D-71: in progress once it has readings,
partial or complete report once a report was issued; one complete TR is then invoiced and one paid.

Needs the demo personas (personas.py), the demo rules (results.publish_demo_rules) and the SLIM
demo reference data. Runs once: skipped when any prep batch exists.
"""

from datetime import UTC, date, datetime, time

from slim_data.demo.scenarios import run_scenarios
from slim_data.persistence.results import issues_for_tr
from slim_data.service.views import batches, report_view
from sqlalchemy import select
from sqlalchemy.orm import Session

from slim_lims.demo_prices import seed_demo_quotes
from slim_lims.domain import (
    CustomerSnapshot,
    PaymentMethod,
    ProcessingTime,
    RequestType,
    SubmissionSource,
    TRSample,
    TRStatus,
    TRStatusEvent,
    TRSubmission,
    transition,
)
from slim_lims.invoices import mark_paid, mark_sent
from slim_lims.models import User
from slim_lims.reference import ReferenceData
from slim_lims.reports import engine_renderer, store
from slim_lims.repositories import TRSubmissionRepository
from slim_lims.results import (
    StaffDirectory,
    actor_for,
    demo_analyses,
    reference_lookup,
    report_complete,
    start_work,
)
from slim_lims.schemas import StatusChangeIn
from slim_lims.services import SubmissionService
from slim_lims.services import actor_for as tr_actor_for

CHEMICAL, ACID = "IPA", "HNO3"  # an assay is of an acid; everything else is measured in IPA
CUSTOMER, SPEC_CUSTOMER = "A", "B"  # in the OSS demo data, B has specs for IPA (Zn among them)
PERSONAS = {"analyst": "ana@demo.example", "reviewer": "rob@demo.example", "supervisor": "sue@demo.example"}


class PortalDemoHost:
    def __init__(
        self, session: Session, reference: ReferenceData, specs, today: date, renderer=engine_renderer
    ) -> None:
        self.session, self.reference, self.today, self.renderer = session, reference, today, renderer
        self.analyses = demo_analyses(reference)
        self._assay_id = self.analyses["assay"][1]
        people = {
            role: session.scalars(select(User).where(User.email == email, User.is_persona.is_(True))).first()
            for role, email in PERSONAS.items()
        }
        if None in people.values():
            raise LookupError("the demo personas are missing; run `cli sync-personas` first")
        self.users = people
        self.analyst, self.reviewer, self.supervisor = (actor_for(people[r]) for r in PERSONAS)
        self.analyst_code = people["analyst"].initials
        self.lookup = reference_lookup(session, reference, specs)
        self.staff = StaffDirectory(session)
        self.repo = TRSubmissionRepository(session)

    def _customer(self, name: str):
        customer = next((c for c in self.reference.customer_choices() if c.name == name), None)
        if customer is None:
            raise LookupError(f"SLIM customer {name!r} not found (seed the OSS demo data first)")
        return customer

    def new_tr(self, scenario: str, samples: int, spec: bool, analysis_id: int) -> str:
        customer = self._customer(SPEC_CUSTOMER if spec else CUSTOMER)
        name = ACID if analysis_id == self._assay_id else CHEMICAL
        chemical = self.reference.chemical_by_name(name)
        if chemical is None:
            raise LookupError(f"SLIM chemical {name!r} not found (seed the OSS demo data first)")
        by = tr_actor_for(self.users["supervisor"])
        at = datetime.combine(self.today, time(8, 0), tzinfo=UTC)
        submission = self.repo.add(
            TRSubmission(
                source=SubmissionSource.PORTAL,
                status=TRStatus.SUBMITTED,
                customer_id=customer.id,
                request_type=RequestType.CHEMICAL,
                submitted_at=at,
                submitted_by=by,
                customer_snapshot=CustomerSnapshot(
                    name=customer.name,
                    address_line_1=customer.address_line_1,
                    address_line_2=customer.address_line_2,
                ),
                customer_contact=f"Demo contact ({scenario.replace('_', ' ')})",
                customer_phone="555-0100",
                payment_method=PaymentMethod.PURCHASE_ORDER,
                po_number=f"DEMO-{scenario.upper()}",
                results_to=("results@demo.example",),
                invoice_to=("ap@demo.example",),
                samples=tuple(
                    TRSample(
                        position=p,
                        sample_name=f"{scenario}-{p}",
                        chemical_id=chemical.id,
                        chemical_name=chemical.name,
                        processing_time=ProcessingTime.THREE_DAYS,
                        analysis_ids=(analysis_id,),
                    )
                    for p in range(1, samples + 1)
                ),
                status_history=(
                    TRStatusEvent(from_status=None, to_status=TRStatus.SUBMITTED, at=at, actor=by),
                ),
            )
        )
        received = transition(
            submission,
            TRStatus.RECEIVED,
            actor=by,
            at=at,
            date_received=self.today,
            received_by=self.users["supervisor"].initials,
            note="demo scenario",
        )
        return self.repo.record_transition(submission, received).tr_number

    def report_sha256(self, session: Session, tr_number: str, lines) -> str:
        # What "Issue report" would record (slim-data D-79): the rendered report, stored; the
        # scenarios report every released result.
        submission = self.repo.get_by_tr_number(tr_number)
        return store(
            session,
            tr_number,
            self.renderer(session, submission, report_view(session, tr_number), self.reference),
        )


def seed_scenarios(
    session: Session, reference: ReferenceData, specs, now: datetime | None = None, renderer=engine_renderer
) -> dict[str, str]:
    """Runs slim-data's scenarios on the app and settles each TR's status; returns scenario ->
    TR number, or {} when results exist already. The caller commits."""
    if batches(session):
        return {}
    now = now or datetime.now(UTC)
    host = PortalDemoHost(session, reference, specs, now.date(), renderer)
    trs = run_scenarios(session, host, now)
    service = SubmissionService(TRSubmissionRepository(session), reference)
    sue = host.users["supervisor"]
    for tr_number in trs.values():
        start_work(service, [tr_number], sue)
        issues = issues_for_tr(session, tr_number)
        if issues:
            complete = report_complete(report_view(session, tr_number), service.get(tr_number, sue))
            target = TRStatus.COMPLETE_REPORT if complete else TRStatus.PARTIAL_REPORT
            service.change_status(
                tr_number, StatusChangeIn(to_status=target, note=f"report issue {issues[-1].id}"), sue
            )
    # The board's invoice columns (spec §13): one invoice sent, one paid, priced from the demo price list.
    seed_demo_quotes(session, reference, now.date())
    mark_sent(session, service, trs["rerun_exception"], sue, reference)
    mark_sent(session, service, trs["clean_pass"], sue, reference)
    mark_paid(session, service, trs["clean_pass"], sue)
    return trs
