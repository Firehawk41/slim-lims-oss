"""SQLAlchemy models for app-owned tables — persistence only, no business logic."""

import uuid
from datetime import UTC, date, datetime
from datetime import time as time_type

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
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
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # A demo persona (personas.py): chosen, not logged in; never signs in through /login.
    is_persona: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
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
# Stands in for the invoicing module until it is rewired to read SLIM: one row per invoice sent for a
# TR, paid when the payment arrives. The invoicing program will write these instead of staff clicks.


class TRInvoiceRow(Base):
    __tablename__ = "tr_invoices"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    submission_id: Mapped[int] = mapped_column(
        ForeignKey("tr_submissions.id", ondelete="CASCADE"), index=True
    )
    sent_on: Mapped[date] = mapped_column(Date)
    sent_by: Mapped[str] = mapped_column(String(254))
    paid_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    paid_by: Mapped[str] = mapped_column(String(254), default="")
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))


class ReportFileRow(Base):
    """An issued report's file (spec §13, slim-data D-79): the HTML exactly as issued, by its sha256 --
    the hash slim-data's report issue records."""

    __tablename__ = "report_files"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tr_number: Mapped[str] = mapped_column(String(20), index=True)
    sha256: Mapped[str] = mapped_column(String(64), unique=True)
    html: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
