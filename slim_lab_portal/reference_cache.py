"""Periodically refreshed cache of SLIM reference data (spec §2, "Reference cache").

Same strategy as slim-domain / slim-lims: load everything into memory, serve lookups from
dicts. A refresh happens on the first access after `ttl_seconds`, or on demand. If a refresh
fails, the previous snapshot keeps being served and the error is reported in `status()`;
after a failure the next attempt waits `retry_seconds` so a down database isn't hammered.
"""

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import Engine, select

from slim_lab_portal import slim_tables as t
from slim_lab_portal.domain import RequestType
from slim_lab_portal.models import AnalysisCatalogRow
from slim_lab_portal.reference import (
    AnalysisRef,
    ChemicalRef,
    CustomerRef,
    ElementRef,
    StaticReferenceData,
)

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class CacheStatus:
    loaded_at: datetime | None
    last_error: str | None
    last_error_at: datetime | None
    counts: dict[str, int]

    @property
    def healthy(self) -> bool:
        return self.loaded_at is not None and self.last_error is None


def load_reference(slim_engine: Engine, portal_engine: Engine) -> StaticReferenceData:
    """Reads SLIM's reference tables plus the portal's analysis_catalog overlay."""
    with slim_engine.connect() as conn:
        customers = [
            CustomerRef(
                id=r.id, name=r.customer_name, street_address=r.street_address or "", city=r.city or "",
                state=r.state or "", postal_code=r.postal_code or "", country=r.country or "",
                parent_customer_id=r.parent_customer_id, is_group=bool(r.is_group),
            )
            for r in conn.execute(select(t.customers))
        ]
        chemicals = [ChemicalRef(id=r.id, name=r.chemical_name) for r in conn.execute(select(t.chemicals))]
        elements = [ElementRef(id=r.id, symbol=r.element_symbol, name=r.element_name)
                    for r in conn.execute(select(t.elements))]
        analysis_rows = list(conn.execute(select(t.analyses)))
    with portal_engine.connect() as conn:
        catalog = {r.analysis_id: r for r in conn.execute(select(AnalysisCatalogRow.__table__))}

    analyses = []
    for r in analysis_rows:
        entry = catalog.get(r.id)
        if entry is None:
            # In SLIM but not yet described for the portal: kept for lookups, never offered.
            analyses.append(AnalysisRef(id=r.id, name=r.analysis_name, code="", description=r.analysis_description or "",
                                        sort_order=r.sort_order or 0, request_types=frozenset(),
                                        portal_selectable=False))
            continue
        analyses.append(AnalysisRef(
            id=r.id,
            name=r.analysis_name,
            code=entry.code,
            group=entry.group_name,
            description=r.analysis_description or "",
            sort_order=entry.sort_order,
            request_types=frozenset(RequestType(int(v)) for v in entry.request_types.split(",") if v),
            portal_selectable=entry.portal_selectable,
        ))
    return StaticReferenceData.of(customers=customers, chemicals=chemicals, elements=elements, analyses=analyses)


class ReferenceCache:
    """ReferenceData backed by the SLIM tables. Thread-safe; never blocks readers on a refresh."""

    def __init__(
        self,
        slim_engine: Engine,
        portal_engine: Engine | None = None,
        *,
        ttl_seconds: float = 300,
        retry_seconds: float = 30,
        clock: Callable[[], float] = time.monotonic,
        loader: Callable[[Engine, Engine], StaticReferenceData] = load_reference,
    ) -> None:
        self._slim_engine = slim_engine
        self._portal_engine = portal_engine or slim_engine
        self._ttl = ttl_seconds
        self._retry = retry_seconds
        self._clock = clock
        self._loader = loader
        self._lock = threading.Lock()
        self._data = StaticReferenceData()
        self._next_refresh = 0.0  # refresh on first use
        self._loaded_at: datetime | None = None
        self._last_error: str | None = None
        self._last_error_at: datetime | None = None

    # ------------------------------------------------------------ refresh

    def refresh(self) -> bool:
        """Loads a fresh snapshot now. Returns False (and keeps the old one) on failure."""
        with self._lock:
            return self._refresh_locked()

    def _refresh_locked(self) -> bool:
        try:
            data = self._loader(self._slim_engine, self._portal_engine)
        except Exception as exc:  # noqa: BLE001 -- any failure: keep serving the last good copy
            log.exception("reference cache refresh failed")
            self._last_error = f"{type(exc).__name__}: {exc}".splitlines()[0][:500]
            self._last_error_at = datetime.now(timezone.utc)
            self._next_refresh = self._clock() + self._retry
            return False
        self._data = data
        self._loaded_at = datetime.now(timezone.utc)
        self._last_error = None
        self._last_error_at = None
        self._next_refresh = self._clock() + self._ttl
        return True

    def snapshot(self) -> StaticReferenceData:
        if self._clock() >= self._next_refresh and self._lock.acquire(blocking=self._loaded_at is None):
            # Only the first load makes callers wait; later refreshes run in whichever request
            # gets the lock while everyone else keeps reading the current snapshot.
            try:
                if self._clock() >= self._next_refresh:
                    self._refresh_locked()
            finally:
                self._lock.release()
        return self._data

    def status(self) -> CacheStatus:
        data = self._data
        return CacheStatus(
            loaded_at=self._loaded_at,
            last_error=self._last_error,
            last_error_at=self._last_error_at,
            counts={
                "customers": len(data.customers),
                "chemicals": len(data.chemicals),
                "elements": len(data.elements),
                "analyses": len(data.analyses),
                "portal_analyses": sum(1 for a in data.analyses.values() if a.portal_selectable),
            },
        )

    # ------------------------------------------------------------ ReferenceData

    def customer(self, customer_id: int) -> CustomerRef | None:
        return self.snapshot().customer(customer_id)

    def chemical(self, chemical_id: int) -> ChemicalRef | None:
        return self.snapshot().chemical(chemical_id)

    def chemical_by_name(self, name: str) -> ChemicalRef | None:
        return self.snapshot().chemical_by_name(name)

    def element(self, element_id: int) -> ElementRef | None:
        return self.snapshot().element(element_id)

    def analysis(self, analysis_id: int) -> AnalysisRef | None:
        return self.snapshot().analysis(analysis_id)

    def locations(self, customer_id: int) -> tuple[str, ...]:
        return self.snapshot().locations(customer_id)

    def customer_choices(self) -> list[CustomerRef]:
        return self.snapshot().customer_choices()

    def chemical_choices(self) -> list[ChemicalRef]:
        return self.snapshot().chemical_choices()

    def element_choices(self) -> list[ElementRef]:
        return self.snapshot().element_choices()

    def analyses_for(self, request_type: RequestType) -> list[AnalysisRef]:
        return self.snapshot().analyses_for(request_type)

    def catalog_analyses(self) -> list[AnalysisRef]:
        return self.snapshot().catalog_analyses()
