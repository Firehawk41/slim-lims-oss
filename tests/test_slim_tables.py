"""Contract: the portal's read-only SLIM table definitions match slim-domain's own tables.

Runs when slim-domain (the OSS package) is installed; skipped otherwise.
"""

import pytest

from slim_lab_portal import slim_tables

analysis_repo = pytest.importorskip("slim_domain.domain.analysis.analysis_repository")
from slim_domain.domain.chemical.chemical_repository import _ChemicalRow  # noqa: E402
from slim_domain.domain.customer.customer_repository import _CustomerRow  # noqa: E402
from slim_domain.domain.element.element_repository import _ElementRow  # noqa: E402
from slim_domain.domain.specification.specification_repository import _SpecificationRow  # noqa: E402

SLIM_TABLES = {
    "customers": _CustomerRow.__table__,
    "chemicals": _ChemicalRow.__table__,
    "elements": _ElementRow.__table__,
    "analyses": analysis_repo._AnalysisRow.__table__,
    "specifications": _SpecificationRow.__table__,
}


@pytest.mark.parametrize("name", sorted(SLIM_TABLES))
def test_portal_reads_only_columns_slim_domain_has(name):
    portal = slim_tables.slim_metadata.tables[name]
    slim = SLIM_TABLES[name]
    assert portal.name == slim.name
    slim_columns = {c.name: c for c in slim.columns}
    for column in portal.columns:
        assert column.name in slim_columns, f"{name}.{column.name} is not in slim-domain"
        assert column.type._type_affinity is slim_columns[column.name].type._type_affinity, (
            f"{name}.{column.name}: {column.type} vs {slim_columns[column.name].type}"
        )
    assert [c.name for c in portal.primary_key] == [c.name for c in slim.primary_key]


def test_portal_never_declares_slim_tables_on_its_own_metadata():
    from slim_lab_portal.db import Base

    assert not set(SLIM_TABLES) & set(Base.metadata.tables)
