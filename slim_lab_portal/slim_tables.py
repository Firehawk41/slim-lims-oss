"""Read-only Core definitions of the SLIM reference tables the portal reads (specifications: for
slim-data's spec limits).

These live on their own MetaData, NOT on db.Base, so Alembic and create_all never touch them —
slim-domain owns them. Only the columns the portal reads are declared. tests/test_slim_tables.py
checks them against slim-domain's own row classes when slim-domain is installed.
"""

from sqlalchemy import Boolean, Column, Float, Integer, MetaData, String, Table

slim_metadata = MetaData()

customers = Table(
    "customers", slim_metadata,
    Column("ID", Integer, primary_key=True, key="id"),
    Column("customer_name", String),
    Column("street_address", String),
    Column("city", String),
    Column("state", String),
    Column("postal_code", String),
    Column("country", String),
    Column("parent_customer_id", Integer),
    Column("is_group", Boolean),
)

chemicals = Table(
    "chemicals", slim_metadata,
    Column("ID", Integer, primary_key=True, key="id"),
    Column("chemical_name", String),
)

elements = Table(
    "elements", slim_metadata,
    Column("ID", Integer, primary_key=True, key="id"),
    Column("element_symbol", String),
    Column("element_name", String),
)

analyses = Table(
    "analyses", slim_metadata,
    Column("ID", Integer, primary_key=True, key="id"),
    Column("analysis_name", String),
    Column("analysis_description", String),
    Column("sort_order", Integer),
)

specifications = Table(
    "specifications", slim_metadata,
    Column("ID", Integer, primary_key=True, key="id"),
    Column("kind", String),
    Column("customer_id", Integer),
    Column("chemical_id", Integer),
    Column("element_symbol", String),
    Column("value", Float),
    Column("location", String),
)
