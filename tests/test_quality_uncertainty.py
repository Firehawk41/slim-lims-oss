"""Uncertainty from pooled QC in the app (slim-data D-131): slim-data's QC history and slim-quality's PT
scores make the estimate; a quality manager signs it."""

from datetime import timedelta
from decimal import Decimal

from slim_quality.domain.pt import Assigned, LabValue, PtEntry, PtEntryKind
from slim_quality.service import pt
from slim_quality.service import uncertainty as unc

from slim_lims.quality import SlimDataQc
from slim_lims.quality_records import SEED_QUALITY_ACTOR
from tests.test_quality import NOW, continue_as, reference, seeded, token_of, visitor  # noqa: F401 (fixtures)


def zn_rounds(db):
    for i, (lab, assigned) in enumerate((("20.4", "20"), ("19.8", "20"), ("20.2", "20"))):
        r = pt.register_round(
            db, "Demo PT", f"U{i}", "HISTORY", f"S-{i}", ("Zn",), NOW.date(), SEED_QUALITY_ACTOR
        )
        sent = (LabValue(analyte="Zn", value=Decimal(lab), unit="ppb", result_ref=f"h:{i}"),)
        pt.add_entry(db, r.id, PtEntry(kind=PtEntryKind.SUBMITTED, values=sent, by=SEED_QUALITY_ACTOR))
        scored = (Assigned(analyte="Zn", assigned=Decimal(assigned), sigma=Decimal(1)),)
        day = (NOW - timedelta(days=5 * (i + 1))).date()
        entry = PtEntry(kind=PtEntryKind.SCORED, assigned=scored, report_date=day, by=SEED_QUALITY_ACTOR)
        pt.add_entry(db, r.id, entry)
    db.commit()


def test_an_estimate_is_worked_out_on_a_get_and_signed_by_the_manager(visitor, db):  # noqa: F811
    zn_rounds(db)
    slug = next(s.key.slug for s in unc.precision_series(SlimDataQc(db).series()) if s.key.analyte == "Zn")
    db.rollback()
    continue_as(visitor, db, "uma@demo.example")
    params = {"series": slug, "start": (NOW - timedelta(days=40)).date().isoformat(),
              "end": NOW.date().isoformat(), "u_cref": "0.5", "basis": "PT report"}  # fmt: skip
    page = visitor.get("/quality/uncertainty", params=params).text
    assert "U =" in page and "A quality manager signs it" in page and "None signed yet" in page
    token = token_of(page)
    assert visitor.post("/quality/uncertainty", data=params | {"csrf_token": token}).status_code == 409
    continue_as(visitor, db, "quinn@demo.example")
    page = visitor.get("/quality/uncertainty", params=params).text
    assert "Sign this estimate" in page
    signed = visitor.post("/quality/uncertainty", data=params | {"csrf_token": token_of(page)})
    assert signed.status_code == 200 and "signed." in signed.text and "None signed yet" not in signed.text
    few = visitor.get("/quality/uncertainty", params=params | {"start": NOW.date().isoformat()}).text
    assert "check points in the window" in few
    assert "between 0 and 100" in visitor.get("/quality/uncertainty", params=params | {"u_cref": "200"}).text
    assert "not a date" in visitor.get("/quality/uncertainty", params=params | {"end": "x"}).text
