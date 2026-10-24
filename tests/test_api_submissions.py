import pytest

from slim_lab_portal.models import ROLE_CUSTOMER, User
from slim_lab_portal.security import hash_password
from tests.conftest import PASSWORD, login_api


def payload(**overrides):
    body = {
        "request_type": 1,
        "customer_contact": "Jane Doe",
        "customer_phone": "555-1234",
        "payment_method": 1,
        "po_number": "PO-9876",
        "results_to": ["jane@acme.example"],
        "invoice_to": ["ap@acme.example"],
        "samples": [
            {"sample_name": "S-001", "chemical_name": "Chemical 01", "processing_time": 7, "analysis_ids": [1, 2]},
            {"sample_name": "S-002", "chemical_name": "chemical 02", "processing_time": 3, "requested_time": "15:00",
             "analysis_ids": [1], "additional_element_ids": [26]},
        ],
    }
    body.update(overrides)
    return body


@pytest.fixture
def as_customer(client, customer_user):
    login_api(client, "jane@acme.example")
    return client


@pytest.fixture
def as_staff(client, staff_user):
    login_api(client, "staff@lab.example")
    return client


def messages(response):
    return [d["msg"] for d in response.json()["detail"]]


def test_customer_submits(as_customer):
    response = as_customer.post("/api/submissions", json=payload())
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["tr_number"] == "TR00001"
    assert body["status"] == 1
    assert body["customer_id"] == 1
    assert body["customer_snapshot"] == {"name": "A", "address_line_1": "1 A Street",
                                         "address_line_2": "Anytown, TX 75000, USA"}
    assert body["submitted_by"]["display"] == "jane@acme.example"
    assert body["samples"][0]["chemical_name"] == "Chemical 01"
    assert body["samples"][1]["requested_time"] == "15:00:00"
    assert body["effective_service_date"] is None


def test_submission_needs_csrf(client, customer_user):
    login_api(client, "jane@acme.example")
    del client.headers["X-CSRF-Token"]
    assert client.post("/api/submissions", json=payload()).status_code == 403


def test_anonymous_gets_401(client):
    assert client.post("/api/submissions", json=payload()).status_code in (401, 403)
    assert client.get("/api/submissions").status_code == 401


def test_shape_errors_are_422(as_customer):
    response = as_customer.post("/api/submissions", json=payload(results_to=["not-an-email"], samples=[]))
    assert response.status_code == 422
    locs = {tuple(d["loc"]) for d in response.json()["detail"]}
    assert ("body", "samples") in locs


def test_unknown_fields_rejected(as_customer):
    assert as_customer.post("/api/submissions", json=payload(cc_number="4111")).status_code == 422


def test_business_rule_errors_point_at_fields(as_customer):
    bad = payload(samples=[{"sample_name": "S-1", "processing_time": 7, "analysis_ids": [3, 4, 50]}])
    response = as_customer.post("/api/submissions", json=bad)
    assert response.status_code == 422
    detail = {tuple(d["loc"]): d["msg"] for d in response.json()["detail"]}
    assert detail[("body", "samples", 0, "chemical_name")] == "enter the chemical or matrix"
    assert "TOC is not offered for Chemical" in detail[("body", "samples", 0, "analysis_ids", 0)]
    assert detail[("body", "samples", 0, "analysis_ids", 1)] == "unknown analysis 4"  # not portal-selectable
    assert detail[("body", "samples", 0, "analysis_ids", 2)] == "unknown analysis 50"


def test_domain_rule_errors_are_422(as_customer):
    response = as_customer.post("/api/submissions", json=payload(payment_method=2))  # card + PO number
    assert response.status_code == 422
    assert any("po_number" in m for m in messages(response))


def test_water_samples_get_the_water_chemical(as_customer):
    body = payload(request_type=2, samples=[{"sample_name": "W-1", "processing_time": 8, "analysis_ids": [3]}])
    response = as_customer.post("/api/submissions", json=body)
    assert response.status_code == 201, response.text
    assert response.json()["samples"][0]["chemical_id"] == 1
    assert response.json()["samples"][0]["chemical_name"] == "Water"


def test_wafer_submission(as_customer):
    body = payload(request_type=3, samples=[{"sample_name": "Lot 7", "processing_time": 9, "analysis_ids": [1],
                                             "wafer_size": 2, "reporting_unit": 2}])
    response = as_customer.post("/api/submissions", json=body)
    assert response.status_code == 201, response.text
    assert response.json()["is_rush"] is True
    no_size = payload(request_type=3, samples=[{"sample_name": "Lot 7", "processing_time": 9, "analysis_ids": [1]}])
    assert as_customer.post("/api/submissions", json=no_size).status_code == 422


def test_customer_cannot_submit_for_another_company(as_customer):
    assert as_customer.post("/api/submissions", json=payload(customer_id=2)).status_code == 403


def test_customer_with_locations_must_pick_one(client, db):
    db.add(User(email="bob@b.example", password_hash=hash_password(PASSWORD), role=ROLE_CUSTOMER, customer_id=2))
    db.commit()
    login_api(client, "bob@b.example")
    assert client.post("/api/submissions", json=payload()).status_code == 422
    response = client.post("/api/submissions", json=payload(location="SX5S"))
    assert response.status_code == 201
    assert response.json()["location"] == "SX5S"


def test_staff_submits_on_behalf(as_staff):
    assert as_staff.post("/api/submissions", json=payload()).status_code == 422  # must choose customer
    response = as_staff.post("/api/submissions", json=payload(customer_id=1))
    assert response.status_code == 201
    assert response.json()["submitted_by"] == {"type": 1, "id": response.json()["submitted_by"]["id"], "display": "JM"}


def test_group_cannot_submit(as_staff):
    assert as_staff.post("/api/submissions", json=payload(customer_id=7)).status_code == 422


def test_customers_only_see_their_own(client, db, staff_user, customer_user):
    login_api(client, "staff@lab.example")
    client.post("/api/submissions", json=payload(customer_id=1))
    client.post("/api/submissions", json=payload(customer_id=2, location="SX5N"))
    assert client.get("/api/submissions").json()["total"] == 2
    client.post("/logout", data={"csrf_token": client.headers["X-CSRF-Token"]})

    login_api(client, "jane@acme.example")
    listing = client.get("/api/submissions").json()
    assert [s["tr_number"] for s in listing["items"]] == ["TR00001"]
    assert client.get("/api/submissions/TR00001").status_code == 200
    assert client.get("/api/submissions/TR00002").status_code == 404  # exists, but not theirs
    assert client.get("/api/submissions/TR99999").status_code == 404


def test_status_workflow_over_http(client, staff_user, customer_user):
    login_api(client, "staff@lab.example")
    client.post("/api/submissions", json=payload(customer_id=1))

    assert client.post("/api/submissions/TR00001/status", json={"to_status": 2}).status_code == 409  # check-in only
    response = client.post("/api/submissions/TR00001/check-in", json={"date_received": "2026-10-07"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["receipt"]["received_by"] == "JM"  # defaults to the staff user's initials
    assert body["effective_service_date"] == "2026-10-12"

    assert client.post("/api/submissions/TR00001/status", json={"to_status": 6}).status_code == 409
    for status in (3, 4, 4, 5, 6):
        assert client.post("/api/submissions/TR00001/status", json={"to_status": status}).status_code == 200
    final = client.get("/api/submissions/TR00001").json()
    assert final["status"] == 6
    assert len(final["status_history"]) == 7


def test_customer_cancels_own_submitted_request(client, staff_user, customer_user):
    login_api(client, "jane@acme.example")
    client.post("/api/submissions", json=payload())
    client.post("/api/submissions", json=payload())
    response = client.post("/api/submissions/TR00001/status", json={"to_status": 7, "note": "duplicate"})
    assert response.status_code == 200
    assert response.json()["status_history"][-1]["note"] == "duplicate"
    assert client.post("/api/submissions/TR00002/status",
                       json={"to_status": 2, "date_received": "2026-10-07"}).status_code == 409


def test_reference_for_customer(as_customer):
    body = as_customer.get("/api/reference").json()
    assert "customers" not in body  # customers don't see the customer list
    assert body["locations"] == []
    assert [c["name"] for c in body["chemicals"]] == ["Chemical 01", "Chemical 02"]
    wafer = next(rt for rt in body["request_types"] if rt["value"] == 3)
    assert [a["code"] for a in wafer["analyses"]] == ["36_elements_icpms"]
    assert "Next Day RUSH" in [p["label"] for p in wafer["processing_times"]]
    chemical = next(rt for rt in body["request_types"] if rt["value"] == 1)
    assert "Call-in RUSH (outside business hours)" in [p["label"] for p in chemical["processing_times"]]


def test_reference_for_staff_lists_customers(as_staff):
    body = as_staff.get("/api/reference").json()
    assert [(c["name"], c["locations"]) for c in body["customers"]] == [("A", []), ("B", ["SX5N", "SX5S"])]


def test_reference_needs_login(client):
    assert client.get("/api/reference").status_code == 401


def test_expected_arrival_date(as_customer):
    from datetime import date, timedelta

    future = (date.today() + timedelta(days=10)).isoformat()
    body = as_customer.post("/api/submissions", json=payload(expected_arrival_date=future)).json()
    assert body["expected_arrival_date"] == future
    assert body["estimated_service_date"] is not None
    past = (date.today() - timedelta(days=10)).isoformat()
    response = as_customer.post("/api/submissions", json=payload(expected_arrival_date=past))
    assert response.status_code == 422
    assert ["body", "expected_arrival_date"] in [d["loc"] for d in response.json()["detail"]]


def test_future_receipt_date_is_refused(as_staff):
    from datetime import date, timedelta

    as_staff.post("/api/submissions", json=payload(customer_id=1))
    tomorrow = (date.today() + timedelta(days=2)).isoformat()
    response = as_staff.post("/api/submissions/TR00001/check-in", json={"date_received": tomorrow})
    assert response.status_code == 409
    assert "future" in response.json()["detail"]


def test_chemical_is_free_text_matched_when_known(as_customer):
    body = payload(samples=[
        {"sample_name": "S-1", "chemical_name": "  CHEMICAL 01 ", "processing_time": 7, "analysis_ids": [1]},
        {"sample_name": "S-2", "chemical_name": "IPA 99.9% semi grade", "processing_time": 7, "analysis_ids": [1]},
    ])
    samples = as_customer.post("/api/submissions", json=body).json()["samples"]
    assert (samples[0]["chemical_id"], samples[0]["chemical_name"]) == (2, "CHEMICAL 01")  # as typed, linked
    assert (samples[1]["chemical_id"], samples[1]["chemical_name"]) == (None, "IPA 99.9% semi grade")


def test_chemical_ids_are_not_accepted_from_customers(as_customer):
    body = payload(samples=[{"sample_name": "S-1", "chemical_id": 2, "processing_time": 7, "analysis_ids": [1]}])
    assert as_customer.post("/api/submissions", json=body).status_code == 422


def test_check_in_requires_every_chemical_matched(client, staff_user, customer_user):
    login_api(client, "jane@acme.example")
    body = payload(samples=[
        {"sample_name": "S-1", "chemical_name": "Chemical 01", "processing_time": 7, "analysis_ids": [1]},
        {"sample_name": "S-2", "chemical_name": "IPA semi grade", "processing_time": 7, "analysis_ids": [1]},
    ])
    client.post("/api/submissions", json=body)
    assert client.post("/api/submissions/TR00001/check-in", json={"date_received": "2026-10-07"}).status_code == 403
    client.post("/logout", data={"csrf_token": client.headers.pop("X-CSRF-Token")})

    login_api(client, "staff@lab.example")
    response = client.post("/api/submissions/TR00001/check-in", json={"date_received": "2026-10-07"})
    assert response.status_code == 409 and "sample(s) 2" in response.json()["detail"]
    bad = client.post("/api/submissions/TR00001/check-in", json={"date_received": "2026-10-07", "chemicals": {"2": 99}})
    assert bad.status_code == 422
    ok = client.post("/api/submissions/TR00001/check-in",
                     json={"date_received": "2026-10-07", "chemicals": {"2": 3}, "note": "cooler at 4 C"})
    assert ok.status_code == 200, ok.text
    body = ok.json()
    assert body["status"] == 2 and body["receipt"]["received_by"] == "JM"
    assert [(s["chemical_name"], s["chemical_id"]) for s in body["samples"]] == [("Chemical 01", 2), ("IPA semi grade", 3)]
    assert body["status_history"][-1]["note"] == "cooler at 4 C"


def test_water_and_wafer_check_in_needs_no_matching(as_staff):
    as_staff.post("/api/submissions", json=payload(customer_id=1, request_type=2, samples=[
        {"sample_name": "W-1", "processing_time": 8, "analysis_ids": [3]}]))
    assert as_staff.post("/api/submissions/TR00001/check-in", json={"date_received": "2026-10-07"}).status_code == 200
