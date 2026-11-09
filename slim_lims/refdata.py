"""Reference data upkeep (slim-data D-141): the lab adds and edits customers, adds chemicals, and edits
the analysis catalog, without leaving the app.

SLIM's tables are slim-domain's: the app writes them only through slim-domain's own services
(`CustomerService`, `ChemicalService`), which validate (names unique, addresses complete, prep methods
from their lists) and commit on their own; on Postgres the audit log records each write. The analysis
catalog (`analysis_catalog`: group, request types, offered on the form) is the app's own table.

Every change is also kept in the app's `reference_changes` (append-only): before, after, why, who.
slim-domain writes and commits first (refused: nothing is recorded); then the row is flushed and the
caller commits the app's side. The caller ends its own read first (`db.commit()`): SQLite lets a write
through only when no other connection is reading. Nothing is
deleted here: requests and results point at these rows, and a request keeps its own snapshot of the
customer, so renaming one leaves old requests as they were. Specifications (spec limits) are not edited
here: slim-domain has no write for them yet (parked, D-141).
"""

from dataclasses import dataclass

from slim_domain.domain.chemical.chemical_service import IONS_PREP, METALS_PREP, SILICON_PREP, ChemicalService
from slim_domain.domain.customer.customer_service import CustomerService
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from slim_lims.domain import RequestType
from slim_lims.models import AnalysisCatalogRow, ReferenceChangeRow, User

CUSTOMER, CHEMICAL, ANALYSIS = "customer", "chemical", "analysis"
ADDED, EDITED = "added", "edited"
CUSTOMER_FIELDS = ("name", "street_address", "city", "state", "postal_code", "country")
INSTRUMENTS = ("ICPMS", "ICPOES")
PREP_OPTIONS = {"metals_prep": METALS_PREP, "silicon_prep": SILICON_PREP, "ions_prep": IONS_PREP}


class RefRefused(ValueError):
    pass


def _by(user: User) -> str:
    return user.initials or user.email


def _reason(text: str) -> str:
    reason = " ".join(text.split())
    if not reason:
        raise RefRefused("Say why: the change is kept with its reason.")
    return reason[:2000]


def _record(db: Session, kind: str, ref_id: int, action: str, before, after, reason: str, user: User) -> None:
    db.add(
        ReferenceChangeRow(
            kind=kind, ref_id=ref_id, action=action, before=before, after=after, reason=reason, by=user.email
        )
    )
    db.flush()


def changes(db: Session, kind: str | None = None, ref_id: int | None = None) -> list[ReferenceChangeRow]:
    query = select(ReferenceChangeRow).order_by(ReferenceChangeRow.id.desc())
    if kind is not None:
        query = query.where(ReferenceChangeRow.kind == kind)
    if ref_id is not None:
        query = query.where(ReferenceChangeRow.ref_id == ref_id)
    return list(db.scalars(query))


# ---------------------------------------------------------------- customers and chemicals (slim-domain's)


def customers(slim: Session) -> list:
    return sorted(CustomerService(slim).all_customers(), key=lambda c: c.name.casefold())


def customer(slim: Session, customer_id: int):
    return CustomerService(slim).load_customer(customer_id)


def _customer_fields(c) -> dict:
    return {f: getattr(c, f) for f in CUSTOMER_FIELDS}


def add_customer(db: Session, slim: Session, fields: dict, reason: str, user: User):
    reason = _reason(reason)
    values = {f: " ".join(fields.get(f, "").split()) for f in CUSTOMER_FIELDS}
    try:
        made = CustomerService(slim).create_customer(**values, created_by=_by(user))
    except ValueError as refused:
        raise RefRefused(str(refused)) from refused
    _record(db, CUSTOMER, made.id, ADDED, None, values, reason, user)
    return made


def edit_customer(db: Session, slim: Session, customer_id: int, fields: dict, reason: str, user: User):
    reason = _reason(reason)
    service = CustomerService(slim)
    old = service.load_customer(customer_id)
    if old is None:
        raise RefRefused(f"No customer {customer_id}.")
    values = {f: " ".join(fields.get(f, "").split()) for f in CUSTOMER_FIELDS}
    before = _customer_fields(old)
    if values == before:
        raise RefRefused("Nothing changed.")
    try:
        saved = service.update_customer(old.model_copy(update=values))
    except ValueError as refused:
        raise RefRefused(str(refused)) from refused
    _record(db, CUSTOMER, customer_id, EDITED, before, values, reason, user)
    return saved


def chemicals(slim: Session) -> list:
    return sorted(ChemicalService(slim).all_chemicals(), key=lambda c: c.name.casefold())


def add_chemical(db: Session, slim: Session, fields: dict, ked_ids: set[int], reason: str, user: User):
    reason = _reason(reason)
    name = " ".join(fields.get("name", "").split())
    preps = {key: fields.get(key, "N/A") for key in PREP_OPTIONS}
    instrument = fields.get("metals_instrument", "ICPMS")
    if instrument not in INSTRUMENTS:
        raise RefRefused(f"Metals instrument: one of {', '.join(INSTRUMENTS)}.")
    try:
        made = ChemicalService(slim).create_chemical(
            name,
            **preps,
            ked_element_ids=frozenset(ked_ids),
            metals_instrument=instrument,
            created_by=_by(user),
        )
    except ValueError as refused:
        raise RefRefused(str(refused)) from refused
    after = {"name": name, **preps, "metals_instrument": instrument, "ked_element_ids": sorted(ked_ids)}
    _record(db, CHEMICAL, made.id, ADDED, None, after, reason, user)
    return made


# ---------------------------------------------------------------- the analysis catalog (the app's own)


@dataclass(frozen=True)
class CatalogEdit:
    group_name: str
    request_types: frozenset[RequestType]
    portal_selectable: bool


def _catalog_fields(row: AnalysisCatalogRow) -> dict:
    return {
        "group_name": row.group_name,
        "request_types": sorted(int(t) for t in row.request_types.split(",") if t),
        "portal_selectable": row.portal_selectable,
    }


def edit_catalog(
    db: Session, analysis_id: int, edit: CatalogEdit, reason: str, user: User
) -> AnalysisCatalogRow:
    reason = _reason(reason)
    row = db.get(AnalysisCatalogRow, analysis_id)
    if row is None:
        raise RefRefused(f"Analysis {analysis_id} is not in the catalog.")
    group = " ".join(edit.group_name.split())
    if not group or len(group) > 60:
        raise RefRefused("Group: a name of up to 60 characters.")
    if edit.portal_selectable and not edit.request_types:
        raise RefRefused("Request types: an analysis the form offers needs at least one.")
    before = _catalog_fields(row)
    row.group_name = group
    row.request_types = ",".join(str(int(t)) for t in sorted(edit.request_types))
    row.portal_selectable = edit.portal_selectable
    after = _catalog_fields(row)
    if after == before:
        raise RefRefused("Nothing changed.")
    _record(db, ANALYSIS, analysis_id, EDITED, before, after, reason, user)
    return row


def edited_in_app(db: Session) -> dict[int, dict]:
    """analysis ID -> its catalog fields as last edited in the app: what a catalog sync keeps."""
    kept: dict[int, dict] = {}
    for change in reversed(changes(db, ANALYSIS)):  # oldest first: the newest wins
        kept[change.ref_id] = change.after
    return kept


# ---------------------------------------------------------------- where the writes go


def slim_session(engine: Engine) -> Session:
    """A session on SLIM's tables for slim-domain's services (they commit on it themselves)."""
    return Session(engine, expire_on_commit=False)
