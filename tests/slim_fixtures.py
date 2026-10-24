"""Creates SLIM reference tables (from the portal's read-only definitions) with tiny data."""

from slim_lab_portal import slim_tables as t


def create_slim_tables(engine, *, analyses=None) -> None:
    t.slim_metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(t.customers.insert(), [
            {"id": 1, "customer_name": "A", "street_address": "1 A Street", "city": "Anytown", "state": "TX",
             "postal_code": "75000", "country": "USA", "parent_customer_id": None, "is_group": False},
            {"id": 7, "customer_name": "G", "street_address": "", "city": "", "state": "", "postal_code": "",
             "country": "", "parent_customer_id": None, "is_group": True},
        ])
        conn.execute(t.chemicals.insert(), [{"id": 1, "chemical_name": "Water"},
                                            {"id": 2, "chemical_name": "Chemical 01"}])
        conn.execute(t.elements.insert(), [{"id": 26, "element_symbol": "Fe", "element_name": "Iron"}])
        conn.execute(t.analyses.insert(), analyses or [
            {"id": 1, "analysis_name": "36 Elements", "analysis_description": "ICP-MS panel", "sort_order": 0},
            {"id": 2, "analysis_name": "pH", "analysis_description": None, "sort_order": 0},
            {"id": 3, "analysis_name": "Brand New Test", "analysis_description": None, "sort_order": 0},
            {"id": 4, "analysis_name": "additional element", "analysis_description": None, "sort_order": 0},
        ])
