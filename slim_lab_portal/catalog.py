"""Fills the portal's analysis_catalog from data/analysis_catalog.csv (spec §8).

Rows are matched to SLIM's analyses table by analysis_name (case-insensitive, trimmed), so the
same file works against any SLIM database — the OSS demo one or, later, the lab's. Only the
portal-owned analysis_catalog table is written; SLIM's tables are read.
"""

import csv
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import Engine, delete, select
from sqlalchemy.orm import Session

from slim_lab_portal import slim_tables as t
from slim_lab_portal.domain import RequestType
from slim_lab_portal.models import AnalysisCatalogRow

CATALOG_CSV = Path(__file__).resolve().parent / "data" / "analysis_catalog.csv"
_TYPE_LETTERS = {"C": RequestType.CHEMICAL, "W": RequestType.WATER, "F": RequestType.WAFER}


@dataclass(frozen=True)
class CatalogEntry:
    code: str
    slim_name: str
    group: str
    request_types: tuple[RequestType, ...]
    portal_selectable: bool
    sort_order: int


@dataclass(frozen=True)
class SyncReport:
    matched: int
    not_in_slim: list[str]  # catalog names SLIM doesn't have (skipped)
    not_in_catalog: list[str]  # SLIM analyses with no catalog entry (never offered)


def read_catalog(path: Path = CATALOG_CSV) -> list[CatalogEntry]:
    with path.open(newline="", encoding="utf-8") as f:
        return [
            CatalogEntry(
                code=row["code"].strip(),
                slim_name=row["slim_name"].strip(),
                group=row["group"].strip(),
                request_types=tuple(_TYPE_LETTERS[c] for c in row["request_types"].strip()),
                portal_selectable=row["portal_selectable"].strip() == "1",
                sort_order=i,
            )
            for i, row in enumerate(csv.DictReader(f), start=1)
        ]


def sync_analysis_catalog(slim_engine: Engine, portal_session: Session, path: Path = CATALOG_CSV) -> SyncReport:
    """Replaces analysis_catalog with the CSV's entries that exist in SLIM. The caller commits."""
    with slim_engine.connect() as conn:
        slim_ids = {r.analysis_name.strip().lower(): r.id for r in conn.execute(select(t.analyses))}
    entries = read_catalog(path)
    portal_session.execute(delete(AnalysisCatalogRow))
    matched, not_in_slim = 0, []
    for entry in entries:
        analysis_id = slim_ids.get(entry.slim_name.lower())
        if analysis_id is None:
            not_in_slim.append(entry.slim_name)
            continue
        portal_session.add(AnalysisCatalogRow(
            analysis_id=analysis_id,
            code=entry.code,
            group_name=entry.group,
            request_types=",".join(str(int(rt)) for rt in entry.request_types),
            portal_selectable=entry.portal_selectable,
            sort_order=entry.sort_order,
        ))
        matched += 1
    portal_session.flush()
    catalog_names = {e.slim_name.lower() for e in entries}
    not_in_catalog = sorted(name for name in slim_ids if name not in catalog_names)
    return SyncReport(matched=matched, not_in_slim=not_in_slim, not_in_catalog=not_in_catalog)
