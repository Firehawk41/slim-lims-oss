"""The demo's price list (slim-data D-83): one default quote for every catalog analysis, so any demo TR
can be invoiced. All prices are invented (D-05), never a real lab's.

A base price per analysis group at five-day turnaround, scaled by turnaround; 10 or more samples on a
line get 15 % off. One bundle, 36 Elements + 7 Anions, is cheaper than its parts, to show the engine
picking it (D-84).
"""

from datetime import date
from decimal import ROUND_HALF_UP, Decimal

import slim_invoicing
from slim_invoicing.persistence.repositories import default_quotes
from sqlalchemy.orm import Session

from slim_lims.reference import ReferenceData

PT = slim_invoicing.ProcessingTime
BASE = {  # five-day price per sample, by catalog group
    "Metals": Decimal(60), "Anions": Decimal(40), "Cations": Decimal(35), "Assay & titration": Decimal(30),
    "Physical": Decimal(15), "Organics": Decimal(70), "Silicon": Decimal(45), "Microbiology": Decimal(25),
}
TURNAROUND = {
    PT.FIVE_DAYS: Decimal("1"), PT.EXTENDED_TIME: Decimal("1"), PT.THREE_DAYS: Decimal("1.1"),
    PT.TWO_DAYS: Decimal("1.25"), PT.NEXT_DAY: Decimal("1.5"), PT.TIME_LIMITED: Decimal("1.75"),
    PT.NEXT_DAY_RUSH: Decimal("1.75"), PT.SAME_DAY_RUSH: Decimal("2"), PT.CALL_IN_RUSH: Decimal("2.5"),
}
BULK_MIN, BULK = 10, Decimal("0.85")
BUNDLE = ("36_elements_icpms", "7_anions"), Decimal("0.85")  # vs the two bought apart


def _prices(base: Decimal) -> tuple[slim_invoicing.QuotePrice, ...]:
    def money(x: Decimal) -> Decimal:
        return x.quantize(Decimal(1), rounding=ROUND_HALF_UP)

    return tuple(
        slim_invoicing.QuotePrice(processing_time=pt, regular_price=money(base * k), bulk_price=money(base * k * BULK))
        for pt, k in TURNAROUND.items()
    )


def seed_demo_quotes(session: Session, reference: ReferenceData, today: date) -> bool:
    """Publishes the default price list once; False if a default quote exists already. The caller commits."""
    if default_quotes(session):
        return False
    priced = [a for a in reference.catalog_analyses() if a.group in BASE]
    lines = [slim_invoicing.QuoteLineItem(analysis_ids=(a.id,), bulk_price_min=BULK_MIN, prices=_prices(BASE[a.group]))
             for a in priced]
    by_code = {a.code: a for a in priced}
    codes, discount = BUNDLE
    if all(c in by_code for c in codes):
        parts = sum(BASE[by_code[c].group] for c in codes)
        lines.append(slim_invoicing.QuoteLineItem(analysis_ids=tuple(by_code[c].id for c in codes),
                                                  bulk_price_min=BULK_MIN, prices=_prices(parts * discount)))
    slim_invoicing.save_quote(session, slim_invoicing.Quote(
        effective_date=today.replace(month=1, day=1), expire_date=today.replace(year=today.year + 2, month=12, day=31),
        is_default=True, line_items=tuple(lines), created_by="DEMO", comments="Demo price list (invented)",
    ))
    return True
