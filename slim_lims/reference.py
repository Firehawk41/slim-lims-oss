"""Read-only view of SLIM reference data (customers, chemicals, elements, analyses).

The submission service and the form depend on the `ReferenceData` protocol only.
`StaticReferenceData` is an immutable in-memory snapshot; `reference_cache.ReferenceCache`
builds snapshots from the SLIM tables and refreshes them periodically.
"""

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Protocol

from slim_lims.domain import RequestType


@dataclass(frozen=True)
class CustomerRef:
    id: int
    name: str
    street_address: str = ""
    city: str = ""
    state: str = ""
    postal_code: str = ""
    country: str = ""
    parent_customer_id: int | None = None
    is_group: bool = False

    @property
    def address_line_1(self) -> str:
        return self.street_address

    @property
    def address_line_2(self) -> str:
        region = " ".join(part for part in (self.state, self.postal_code) if part)
        return ", ".join(part for part in (self.city, region, self.country) if part)


@dataclass(frozen=True)
class ChemicalRef:
    id: int
    name: str


@dataclass(frozen=True)
class ElementRef:
    id: int
    symbol: str
    name: str


@dataclass(frozen=True)
class AnalysisRef:
    id: int
    name: str
    code: str
    group: str = ""
    description: str = ""
    sort_order: int = 0
    request_types: frozenset[RequestType] = frozenset(RequestType)
    portal_selectable: bool = True


WATER_CHEMICAL_NAME = "Water"

# Display order of analysis groups in the form; unknown groups sort last, alphabetically.
GROUP_ORDER = ("Metals", "Anions", "Cations", "Organics", "Physical", "Silicon", "Microbiology", "Assay & titration")


class ReferenceData(Protocol):
    def customer(self, customer_id: int) -> CustomerRef | None: ...
    def chemical(self, chemical_id: int) -> ChemicalRef | None: ...
    def chemical_by_name(self, name: str) -> ChemicalRef | None: ...
    def element(self, element_id: int) -> ElementRef | None: ...
    def analysis(self, analysis_id: int) -> AnalysisRef | None: ...
    def locations(self, customer_id: int) -> tuple[str, ...]: ...
    def customer_choices(self) -> list[CustomerRef]: ...
    def chemical_choices(self) -> list[ChemicalRef]: ...
    def element_choices(self) -> list[ElementRef]: ...
    def analyses_for(self, request_type: RequestType) -> list[AnalysisRef]: ...
    def catalog_analyses(self) -> list[AnalysisRef]: ...


@dataclass
class StaticReferenceData:
    """In-memory ReferenceData. Name lookups are case-insensitive and trimmed, like slim-domain's caches."""

    customers: dict[int, CustomerRef] = field(default_factory=dict)
    chemicals: dict[int, ChemicalRef] = field(default_factory=dict)
    elements: dict[int, ElementRef] = field(default_factory=dict)
    analyses: dict[int, AnalysisRef] = field(default_factory=dict)
    customer_locations: dict[int, tuple[str, ...]] = field(default_factory=dict)

    @classmethod
    def of(
        cls,
        customers: Iterable[CustomerRef] = (),
        chemicals: Iterable[ChemicalRef] = (),
        elements: Iterable[ElementRef] = (),
        analyses: Iterable[AnalysisRef] = (),
        customer_locations: dict[int, tuple[str, ...]] | None = None,
    ) -> "StaticReferenceData":
        return cls(
            customers={c.id: c for c in customers},
            chemicals={c.id: c for c in chemicals},
            elements={e.id: e for e in elements},
            analyses={a.id: a for a in analyses},
            customer_locations=dict(customer_locations or {}),
        )

    def customer(self, customer_id: int) -> CustomerRef | None:
        return self.customers.get(customer_id)

    def chemical(self, chemical_id: int) -> ChemicalRef | None:
        return self.chemicals.get(chemical_id)

    def chemical_by_name(self, name: str) -> ChemicalRef | None:
        key = name.strip().lower()
        return next((c for c in self.chemicals.values() if c.name.strip().lower() == key), None)

    def element(self, element_id: int) -> ElementRef | None:
        return self.elements.get(element_id)

    def analysis(self, analysis_id: int) -> AnalysisRef | None:
        return self.analyses.get(analysis_id)

    def locations(self, customer_id: int) -> tuple[str, ...]:
        return self.customer_locations.get(customer_id, ())

    # ------------------------------------------------------------ choices for the form

    def customer_choices(self) -> list[CustomerRef]:
        """Customers that can submit (groups are invoicing umbrellas, not submitters)."""
        return sorted((c for c in self.customers.values() if not c.is_group), key=lambda c: c.name.lower())

    def chemical_choices(self) -> list[ChemicalRef]:
        """Chemicals a Chemical sample can be; Water samples get the Water chemical automatically."""
        return sorted(
            (c for c in self.chemicals.values() if c.name.strip().lower() != WATER_CHEMICAL_NAME.lower()),
            key=lambda c: c.name.lower(),
        )

    def element_choices(self) -> list[ElementRef]:
        return sorted(self.elements.values(), key=lambda e: e.id)

    def catalog_analyses(self) -> list[AnalysisRef]:
        """Every analysis with an analysis_catalog code, offered in the form or not (slim-data's codes)."""
        return sorted((a for a in self.analyses.values() if a.code), key=lambda a: a.code)

    def analyses_for(self, request_type: RequestType) -> list[AnalysisRef]:
        def key(a: AnalysisRef):
            group = GROUP_ORDER.index(a.group) if a.group in GROUP_ORDER else len(GROUP_ORDER)
            return group, a.group, a.sort_order, a.name.lower()

        return sorted(
            (a for a in self.analyses.values() if a.portal_selectable and request_type in a.request_types),
            key=key,
        )
