"""SQLAlchemy models for app-owned tables — persistence only, no business logic."""

import uuid
from datetime import UTC, date, datetime
from datetime import time as time_type

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    Time,
    UniqueConstraint,
    Uuid,
    event,
    false,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from slim_lims.db import Base

ROLE_CUSTOMER = "customer"
ROLE_STAFF = "staff"
VALID_ROLES = (ROLE_CUSTOMER, ROLE_STAFF)

# Staff lab roles, stored as slim-data's Role values (tiered: a higher role can do what a lower can).
LAB_ROLE_ANALYST, LAB_ROLE_REVIEWER, LAB_ROLE_SUPERVISOR = 1, 2, 3
LAB_ROLES = {LAB_ROLE_ANALYST: "Analyst", LAB_ROLE_REVIEWER: "Reviewer", LAB_ROLE_SUPERVISOR: "Supervisor"}


def _utcnow() -> datetime:
    return datetime.now(UTC)


class User(Base):
    """A login. Customer users belong to one SLIM customer; staff users have initials."""

    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("role IN ('customer', 'staff')", name="role_valid"),
        CheckConstraint("role <> 'customer' OR customer_id IS NOT NULL", name="customer_has_customer_id"),
        CheckConstraint(
            "role <> 'staff' OR (initials IS NOT NULL AND initials <> '')", name="staff_has_initials"
        ),
        CheckConstraint("role <> 'staff' OR lab_role IS NOT NULL", name="staff_has_lab_role"),
        CheckConstraint("lab_role IS NULL OR lab_role IN (1, 2, 3)", name="lab_role_valid"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(254), unique=True)  # stored lower-cased
    password_hash: Mapped[str] = mapped_column(String(100))
    role: Mapped[str] = mapped_column(String(20))
    customer_id: Mapped[int | None] = mapped_column(Integer, index=True)  # SLIM customers."ID"
    # Staff ID in result files (the `analyst` column), so unique among staff (D-70).
    initials: Mapped[str | None] = mapped_column(String(10), unique=True)
    lab_role: Mapped[int | None] = mapped_column(Integer)  # staff only: LAB_ROLES
    # As printed under a signed report (D-122): "Sue Example", "Laboratory Supervisor". Signing needs
    # the name; the title falls back to the lab role.
    full_name: Mapped[str | None] = mapped_column(String(100))
    job_title: Mapped[str | None] = mapped_column(String(100))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # A demo persona (personas.py): chosen, not logged in; never signs in through /login.
    is_persona: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    # Bumped when the password is reset or changed, or MFA reset (D-142): a session made before carries the
    # old number and is signed out.
    auth_version: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    @property
    def is_staff(self) -> bool:
        return self.role == ROLE_STAFF

    is_service = False  # see auth.ServiceClient


class UserCapability(Base):
    """A capability granted to one person beyond their lab role's defaults (D-100), e.g. an analyst who
    is also the lab's LIMS specialist manages users. Who granted it is kept; removing it deletes the row
    (the audit log records both on Postgres)."""

    __tablename__ = "user_capabilities"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    capability: Mapped[str] = mapped_column(String(50), primary_key=True)
    granted_by: Mapped[uuid.UUID] = mapped_column(Uuid)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


# ---------------------------------------------------------------- Testing Requests
# Spec §7. These tables move to slim-domain later: same names, same columns. Enums are stored
# as their integer values; customer/chemical/analysis/element IDs are SLIM IDs (no FKs).


class TRNumberCounter(Base):
    """Single row (id=1). Incremented in the same transaction as the insert, so no gaps."""

    __tablename__ = "tr_number_counter"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    last_value: Mapped[int] = mapped_column(Integer)


class TRSubmissionRow(Base):
    __tablename__ = "tr_submissions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tr_number: Mapped[str] = mapped_column(String(20), unique=True)
    source: Mapped[int] = mapped_column(Integer)
    file_name: Mapped[str] = mapped_column(String(260), default="")
    status: Mapped[int] = mapped_column(Integer, index=True)
    customer_id: Mapped[int] = mapped_column(Integer, index=True)
    location: Mapped[str] = mapped_column(String(20), default="")
    request_type: Mapped[int] = mapped_column(Integer)
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    submitted_by_type: Mapped[int] = mapped_column(Integer)
    submitted_by_id: Mapped[str] = mapped_column(String(64), default="")
    submitted_by_display: Mapped[str] = mapped_column(String(254))
    snapshot_name: Mapped[str] = mapped_column(String(200))
    snapshot_address_1: Mapped[str] = mapped_column(String(200), default="")
    snapshot_address_2: Mapped[str] = mapped_column(String(200), default="")
    customer_contact: Mapped[str] = mapped_column(String(200))
    customer_phone: Mapped[str] = mapped_column(String(50))
    payment_method: Mapped[int] = mapped_column(Integer)
    po_number: Mapped[str] = mapped_column(String(100), default="")
    expected_arrival_date: Mapped[date | None] = mapped_column(Date)
    date_received: Mapped[date | None] = mapped_column(Date)
    received_by: Mapped[str | None] = mapped_column(String(10))
    receipt_recorded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    receipt_recorded_by_type: Mapped[int | None] = mapped_column(Integer)
    receipt_recorded_by_id: Mapped[str | None] = mapped_column(String(64))
    receipt_recorded_by_display: Mapped[str | None] = mapped_column(String(254))
    service_date_override: Mapped[date | None] = mapped_column(Date)

    samples: Mapped[list["TRSampleRow"]] = relationship(
        back_populates="submission", cascade="all, delete-orphan", order_by="TRSampleRow.position"
    )
    emails: Mapped[list["TRSubmissionEmailRow"]] = relationship(
        cascade="all, delete-orphan", order_by="TRSubmissionEmailRow.position"
    )
    status_events: Mapped[list["TRStatusEventRow"]] = relationship(
        cascade="all, delete-orphan", order_by="TRStatusEventRow.id"
    )


class TRSampleRow(Base):
    __tablename__ = "tr_samples"
    __table_args__ = (UniqueConstraint("submission_id", "position"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    submission_id: Mapped[int] = mapped_column(
        ForeignKey("tr_submissions.id", ondelete="CASCADE"), index=True
    )
    position: Mapped[int] = mapped_column(Integer)
    sample_name: Mapped[str] = mapped_column(String(100))
    chemical_id: Mapped[int | None] = mapped_column(Integer)
    chemical_name: Mapped[str] = mapped_column(String(200), default="")
    processing_time: Mapped[int] = mapped_column(Integer)
    requested_time: Mapped[time_type | None] = mapped_column(Time)
    additional_notes: Mapped[str] = mapped_column(Text, default="")
    wafer_size: Mapped[int | None] = mapped_column(Integer)
    reporting_unit: Mapped[int | None] = mapped_column(Integer)
    water_package: Mapped[int | None] = mapped_column(Integer)

    submission: Mapped[TRSubmissionRow] = relationship(back_populates="samples")
    analyses: Mapped[list["TRSampleAnalysisRow"]] = relationship(
        cascade="all, delete-orphan", order_by="TRSampleAnalysisRow.position"
    )
    additional_elements: Mapped[list["TRSampleAdditionalElementRow"]] = relationship(
        cascade="all, delete-orphan", order_by="TRSampleAdditionalElementRow.position"
    )


class TRSampleAnalysisRow(Base):
    __tablename__ = "tr_sample_analyses"

    sample_id: Mapped[int] = mapped_column(ForeignKey("tr_samples.id", ondelete="CASCADE"), primary_key=True)
    analysis_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    position: Mapped[int] = mapped_column(Integer)  # keeps the customer's order


class TRSampleAdditionalElementRow(Base):
    __tablename__ = "tr_sample_additional_elements"

    sample_id: Mapped[int] = mapped_column(ForeignKey("tr_samples.id", ondelete="CASCADE"), primary_key=True)
    element_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    position: Mapped[int] = mapped_column(Integer)


class TRSubmissionEmailRow(Base):
    __tablename__ = "tr_submission_emails"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('results_to', 'results_cc', 'invoice_to', 'invoice_cc')", name="kind_valid"
        ),
    )

    submission_id: Mapped[int] = mapped_column(
        ForeignKey("tr_submissions.id", ondelete="CASCADE"), primary_key=True
    )
    kind: Mapped[str] = mapped_column(String(10), primary_key=True)
    position: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(254))


class TRVersionRow(Base):
    """Every change to a TR, kept whole (slim-data D-104; review A5): version n is the TR as it stood after
    its n-th change. Append-only (triggers, migration 0010). The other TR tables are the current state."""

    __tablename__ = "tr_versions"
    __table_args__ = (UniqueConstraint("submission_id", "version"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    submission_id: Mapped[int] = mapped_column(ForeignKey("tr_submissions.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    change: Mapped[str] = mapped_column(String(100))
    actor_display: Mapped[str] = mapped_column(String(254))
    document: Mapped[dict] = mapped_column(JSON)


class TRStatusEventRow(Base):
    __tablename__ = "tr_status_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    submission_id: Mapped[int] = mapped_column(
        ForeignKey("tr_submissions.id", ondelete="CASCADE"), index=True
    )
    from_status: Mapped[int | None] = mapped_column(Integer)
    to_status: Mapped[int] = mapped_column(Integer)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    actor_type: Mapped[int] = mapped_column(Integer)
    actor_id: Mapped[str] = mapped_column(String(64), default="")
    actor_display: Mapped[str] = mapped_column(String(254))
    note: Mapped[str] = mapped_column(Text, default="")


@event.listens_for(TRNumberCounter.__table__, "after_create")
def _seed_counter(target, connection, **_kw) -> None:
    # Same row the 0002 migration inserts; keeps metadata.create_all (tests, demos) equivalent.
    connection.execute(target.insert().values(id=1, last_value=0))


def append_only(connection, table: str) -> None:
    """Refuses UPDATE and DELETE on `table` (and TRUNCATE on Postgres): the triggers migrations 0010 and
    0011 make, so metadata.create_all (tests, demos) is equivalent."""
    if connection.dialect.name == "postgresql":
        connection.exec_driver_sql(
            f"CREATE OR REPLACE FUNCTION {table}_append_only() RETURNS trigger LANGUAGE plpgsql AS $$ "
            f"BEGIN RAISE EXCEPTION '{table} is append-only'; END $$"
        )
        connection.exec_driver_sql(
            f"CREATE TRIGGER {table}_append_only BEFORE UPDATE OR DELETE ON {table} "
            f"FOR EACH ROW EXECUTE FUNCTION {table}_append_only()"
        )
        connection.exec_driver_sql(
            f"CREATE TRIGGER {table}_no_truncate BEFORE TRUNCATE ON {table} "
            f"FOR EACH STATEMENT EXECUTE FUNCTION {table}_append_only()"
        )
    else:
        for operation in ("UPDATE", "DELETE"):
            connection.exec_driver_sql(
                f"CREATE TRIGGER {table}_no_{operation.lower()} BEFORE {operation} ON {table} "
                f"BEGIN SELECT RAISE(ABORT, '{table} is append-only'); END"
            )


# ---------------------------------------------------------------- reference overlays
# Spec §8: what SLIM's analyses table doesn't hold yet (stable code, group, request types).
# Moves to slim-domain as analyses.code + analysis_request_types later.


class AnalysisCatalogRow(Base):
    __tablename__ = "analysis_catalog"

    analysis_id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=False
    )  # SLIM analyses."ID"
    code: Mapped[str] = mapped_column(String(60), unique=True)
    group_name: Mapped[str] = mapped_column(String(60))
    request_types: Mapped[str] = mapped_column(String(20))  # comma-separated RequestType values, e.g. "1,2"
    portal_selectable: Mapped[bool] = mapped_column(Boolean, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)


# ---------------------------------------------------------------- invoices (spec §13)
# One row per invoice sent for a TR (V5 prices it), one row per payment recorded against it. Both
# append-only (slim-data D-119): a payment is a fact added, never a field changed.


class TRInvoiceRow(Base):
    __tablename__ = "tr_invoices"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    submission_id: Mapped[int] = mapped_column(
        ForeignKey("tr_submissions.id", ondelete="CASCADE"), index=True
    )
    sent_on: Mapped[date] = mapped_column(Date)
    sent_by: Mapped[str] = mapped_column(String(254))
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    payments: Mapped[list["TRInvoicePaymentRow"]] = relationship(
        lazy="selectin", order_by="TRInvoicePaymentRow.id", viewonly=True
    )

    @property
    def paid_on(self) -> date | None:
        return self.payments[0].paid_on if self.payments else None

    @property
    def paid_by(self) -> str:
        return self.payments[0].paid_by if self.payments else ""


class TRInvoicePaymentRow(Base):
    """A payment recorded against an invoice (slim-data D-119). Append-only (triggers, migration 0011)."""

    __tablename__ = "tr_invoice_payments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("tr_invoices.id"), index=True)
    paid_on: Mapped[date] = mapped_column(Date)
    paid_by: Mapped[str] = mapped_column(String(254))
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))


# ---------------------------------------------------------------- report PDFs and deliveries (D-123, D-124)


class ReportPdfRow(Base):
    """An issued report as PDF (D-123): made once, at issue, from the stored HTML (`html_sha256`), and
    kept with its own sha256. What a delivery attaches and a customer downloads. Append-only."""

    __tablename__ = "report_pdfs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tr_number: Mapped[str] = mapped_column(String(20), index=True)
    html_sha256: Mapped[str] = mapped_column(String(64), unique=True)
    sha256: Mapped[str] = mapped_column(String(64), unique=True)
    pdf: Mapped[bytes] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))


class ReportDeliveryRow(Base):
    """One sending of an issued report to the customer (D-124): to whom, the message, the PDF it
    attached (by hash), who sent it and when, and what the transport did with it ("outbox": stored,
    not transmitted, the demo's only transport). Append-only: a re-send is another row."""

    __tablename__ = "report_deliveries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tr_number: Mapped[str] = mapped_column(String(20), index=True)
    issue_id: Mapped[int] = mapped_column(
        Integer, index=True
    )  # slim-data's report issue; no FK (module rule 1)
    to_addresses: Mapped[list] = mapped_column(JSON)
    cc_addresses: Mapped[list] = mapped_column(JSON)
    subject: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text)
    pdf_sha256: Mapped[str] = mapped_column(String(64))
    sent_by: Mapped[str] = mapped_column(String(254))  # the sender's email
    transport: Mapped[str] = mapped_column(String(20))
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))


# ---------------------------------------------------------------- the day's board (D-138)
# Sections are the lab's own: each owns some catalog analyses, an analysis belongs to one section at most.
# A display link shows one section (or the whole lab) on a screen without a login. Whether the screens
# show customer names is a lab-wide switch, kept as rows (the latest wins; none: names hidden).


class LabSectionRow(Base):
    __tablename__ = "lab_sections"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(60), unique=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    analyses: Mapped[list["LabSectionAnalysisRow"]] = relationship(
        cascade="all, delete-orphan", lazy="selectin", order_by="LabSectionAnalysisRow.analysis_id"
    )


class LabSectionAnalysisRow(Base):
    """An analysis in a section. The analysis is the key: one section each (SLIM analyses."ID", no FK)."""

    __tablename__ = "lab_section_analyses"

    analysis_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    section_id: Mapped[int] = mapped_column(ForeignKey("lab_sections.id", ondelete="CASCADE"), index=True)


class DisplayLinkRow(Base):
    """A screen's link: only the token's sha256 is kept (the link is shown once). No section: the whole
    lab. Revoking stamps the row; a revoked link shows nothing."""

    __tablename__ = "display_links"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    token_sha256: Mapped[str] = mapped_column(String(64), unique=True)
    label: Mapped[str] = mapped_column(String(60))
    section_id: Mapped[int | None] = mapped_column(ForeignKey("lab_sections.id", ondelete="CASCADE"))
    created_by: Mapped[str] = mapped_column(String(254))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_by: Mapped[str | None] = mapped_column(String(254))


class DisplayModeRow(Base):
    """The lab-wide switch for the screens: customer names shown (no visitors) or hidden. Append-only."""

    __tablename__ = "display_modes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    names_shown: Mapped[bool] = mapped_column(Boolean)
    set_by: Mapped[str] = mapped_column(String(254))
    set_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


# ---------------------------------------------------------------- quotes (D-139)
# Quotes themselves are invoicing V5's (`inv_quotes`): never edited. The host keeps what V5 doesn't: a
# customer's request for a quote and what became of it, each quote's PDF and its sendings, and which quote
# a TR named. All append-only; IDs of V5's quotes are plain integers (no FK into another module).


class QuoteRequestRow(Base):
    """A customer asks for a quote: the analyses, processing times, a rough sample count, a note."""

    __tablename__ = "quote_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    customer_id: Mapped[int] = mapped_column(Integer, index=True)  # SLIM customers."ID"
    requested_by: Mapped[str] = mapped_column(String(254))  # the customer user's email
    analysis_ids: Mapped[list] = mapped_column(JSON)
    processing_times: Mapped[list] = mapped_column(JSON)  # ProcessingTime values
    samples_estimate: Mapped[str] = mapped_column(String(100), default="")  # "about 20 a month"
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class QuoteRequestEventRow(Base):
    """What became of a request: answered with a quote (`quote_id`), or declined (`reason`)."""

    __tablename__ = "quote_request_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    request_id: Mapped[int] = mapped_column(ForeignKey("quote_requests.id"), index=True)
    kind: Mapped[str] = mapped_column(String(20))  # "answered" | "declined"
    quote_id: Mapped[int | None] = mapped_column(Integer)
    reason: Mapped[str] = mapped_column(Text, default="")
    by: Mapped[str] = mapped_column(String(254))
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class QuotePdfRow(Base):
    """A quote as PDF, made once (a quote never changes) and kept with its sha256."""

    __tablename__ = "quote_pdfs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    quote_id: Mapped[int] = mapped_column(Integer, unique=True)
    sha256: Mapped[str] = mapped_column(String(64), unique=True)
    pdf: Mapped[bytes] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class QuoteDeliveryRow(Base):
    """One sending of a quote to the customer, kept in the outbox like a report's (D-124)."""

    __tablename__ = "quote_deliveries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    quote_id: Mapped[int] = mapped_column(Integer, index=True)
    to_addresses: Mapped[list] = mapped_column(JSON)
    subject: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text)
    pdf_sha256: Mapped[str] = mapped_column(String(64))
    sent_by: Mapped[str] = mapped_column(String(254))
    transport: Mapped[str] = mapped_column(String(20))
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class TRQuoteRow(Base):
    """The quote a customer named on their request, for reference (pricing still takes the cheapest valid
    line). Kept beside the TR, not in it: the TR record and its machine contract (spec §12) are unchanged."""

    __tablename__ = "tr_quotes"

    submission_id: Mapped[int] = mapped_column(
        ForeignKey("tr_submissions.id", ondelete="CASCADE"), primary_key=True
    )
    quote_id: Mapped[int] = mapped_column(Integer)


# ---------------------------------------------------------------- reference data upkeep (D-141)


class ReferenceChangeRow(Base):
    """One change to the lab's reference data made in the app: a customer added or edited, a chemical
    added (both written through slim-domain's services), or an analysis's catalog entry edited. What it
    was and what it became, why, who and when. Append-only."""

    __tablename__ = "reference_changes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(20), index=True)  # "customer" | "chemical" | "analysis"
    ref_id: Mapped[int] = mapped_column(Integer, index=True)  # the SLIM ID it changed
    action: Mapped[str] = mapped_column(String(20))  # "added" | "edited"
    before: Mapped[dict | None] = mapped_column(JSON)
    after: Mapped[dict] = mapped_column(JSON)
    reason: Mapped[str] = mapped_column(Text)
    by: Mapped[str] = mapped_column(String(254))
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


# ---------------------------------------------------------------- accounts (D-142)


class UserMfaRow(Base):
    """A user's authenticator (TOTP): the secret, sealed with a key from SECRET_KEY, and the last time
    step used (a code never counts twice). Removed when MFA is reset or turned off."""

    __tablename__ = "user_mfa"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    secret_sealed: Mapped[str] = mapped_column(String(200))
    enabled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    last_step: Mapped[int | None] = mapped_column(Integer)


class MfaRecoveryCodeRow(Base):
    """A single-use recovery code, kept only as a bcrypt hash."""

    __tablename__ = "mfa_recovery_codes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    code_hash: Mapped[str] = mapped_column(String(100))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PasswordResetRow(Base):
    """A one-time reset link made by someone who manages users: only its token's sha256 is kept."""

    __tablename__ = "password_resets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_sha256: Mapped[str] = mapped_column(String(64), unique=True)
    created_by: Mapped[str] = mapped_column(String(254))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AccountEventRow(Base):
    """What happened to an account: a reset link made or used, the password changed, MFA set up, turned
    off or reset, a recovery code used. Who, why, when. Append-only."""

    __tablename__ = "account_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)  # no FK: kept if the user row goes
    kind: Mapped[str] = mapped_column(String(30))
    by: Mapped[str] = mapped_column(String(254))
    reason: Mapped[str] = mapped_column(Text, default="")
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class AccountMessageRow(Base):
    """A message to a user about their account (a reset link), kept in the outbox, not transmitted (D-124).
    Append-only."""

    __tablename__ = "account_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    to_address: Mapped[str] = mapped_column(String(254))
    subject: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text)
    sent_by: Mapped[str] = mapped_column(String(254))
    transport: Mapped[str] = mapped_column(String(20))
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


# ---------------------------------------------------------------- spec limit changes (D-143)


class SpecProposalRow(Base):
    """A proposed change to one limit set (customer × chemical × site × kind): the set as it stood and
    as proposed, why, who. Proposed by quality or a quality manager. Append-only."""

    __tablename__ = "spec_proposals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    customer_id: Mapped[int] = mapped_column(Integer, index=True)  # SLIM IDs, no FKs
    chemical_id: Mapped[int] = mapped_column(Integer)
    location: Mapped[str] = mapped_column(String(20), default="")
    kind: Mapped[str] = mapped_column(String(20))  # "specification" | "warning_limit"
    unit: Mapped[str] = mapped_column(String(20))
    before: Mapped[dict] = mapped_column(JSON)  # element -> value (strings), as it stood
    after: Mapped[dict] = mapped_column(JSON)  # element -> value (strings), as proposed
    reason: Mapped[str] = mapped_column(Text)
    proposed_by: Mapped[str] = mapped_column(String(254))
    proposed_by_id: Mapped[str] = mapped_column(String(64))
    proposed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class SpecDecisionRow(Base):
    """A proposal approved (written through slim-domain) or rejected, by a quality manager or a lab
    supervisor who didn't propose it; what was re-evaluated and which released results would now judge
    differently. Append-only; one per proposal."""

    __tablename__ = "spec_decisions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    proposal_id: Mapped[int] = mapped_column(ForeignKey("spec_proposals.id"), unique=True)
    outcome: Mapped[str] = mapped_column(String(20))  # "approved" | "rejected"
    reason: Mapped[str] = mapped_column(Text, default="")
    by: Mapped[str] = mapped_column(String(254))
    reevaluated: Mapped[list] = mapped_column(JSON, default=list)  # batch codes
    now_differ: Mapped[list] = mapped_column(JSON, default=list)  # released results, judged otherwise now
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


# ---------------------------------------------------------------- QC warnings at release (D-144)


class QcAcknowledgementRow(Base):
    """A release of a sample + analysis despite an ACTION-severity QC trend flag: the flags as shown then
    (frozen: later points can change a chart), why the result still stands, who, when. Append-only."""

    __tablename__ = "qc_acknowledgements"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sample: Mapped[str] = mapped_column(String(20), index=True)  # "TR00002-01"
    analysis_id: Mapped[int] = mapped_column(Integer)
    flags: Mapped[list] = mapped_column(JSON)
    note: Mapped[str] = mapped_column(Text)
    by: Mapped[str] = mapped_column(String(254))
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


def _append_only_after_create(target, connection, **_kw) -> None:
    append_only(connection, target.name)


for _table in (
    TRVersionRow.__table__,
    TRInvoiceRow.__table__,
    TRInvoicePaymentRow.__table__,
    ReportPdfRow.__table__,
    ReportDeliveryRow.__table__,
    DisplayModeRow.__table__,
    QuoteRequestRow.__table__,
    QuoteRequestEventRow.__table__,
    QuotePdfRow.__table__,
    QuoteDeliveryRow.__table__,
    TRQuoteRow.__table__,
    ReferenceChangeRow.__table__,
    AccountEventRow.__table__,
    AccountMessageRow.__table__,
    SpecProposalRow.__table__,
    SpecDecisionRow.__table__,
    QcAcknowledgementRow.__table__,
):
    event.listen(_table, "after_create", _append_only_after_create)


class ReportFileRow(Base):
    """An issued report's file (spec §13, slim-data D-79): the HTML exactly as issued, by its sha256 --
    the hash slim-data's report issue records."""

    __tablename__ = "report_files"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tr_number: Mapped[str] = mapped_column(String(20), index=True)
    sha256: Mapped[str] = mapped_column(String(64), unique=True)
    html: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
