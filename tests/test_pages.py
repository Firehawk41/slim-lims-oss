"""Server-rendered pages: request list/detail, staff actions, users."""

import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from tests.conftest import csrf_from, login, login_api
from tests.factories import BOTTLES
from tests.test_api_submissions import payload


def lab_today():
    """The lab's date (LAB_TIMEZONE default), which the app checks receipt dates against; the
    machine's own date runs a day ahead of it each evening in Chicago."""
    return datetime.now(ZoneInfo("America/Chicago")).date()


@pytest.fixture
def submitted(client, staff_user, customer_user):
    """TR00001 for customer A (id 1), submitted by the customer; client left logged out."""
    login_api(client, "jane@acme.example")
    assert client.post("/api/submissions", json=payload()).status_code == 201
    client.post("/logout", data={"csrf_token": client.headers.pop("X-CSRF-Token")})
    return client


def _post_status(client, page_path, **data):
    token = csrf_from(client.get(page_path).text)
    return client.post(f"{page_path}/status", data={"csrf_token": token, **data})


def test_customer_list_and_detail(submitted):
    login(submitted, "jane@acme.example")
    listing = submitted.get("/").text
    assert "TR00001" in listing and "Not arrived yet" in listing  # the board (spec §13)
    detail = submitted.get("/requests/TR00001").text
    assert "S-001" in detail and "36 Elements" in detail and "Fe" in detail  # names, not IDs
    assert "Report by 15:00" in detail.replace("report by", "Report by")
    assert "Cancel request" in detail and "Mark received" not in detail


def test_new_request_page_loads_form_script(client, customer_user):
    login(client, "jane@acme.example")
    page = client.get("/requests/new").text
    assert 'id="request-form"' in page
    assert "/static/js/request_form.js" in page
    assert "<script>" not in page  # CSP: no inline scripts


def test_customer_cannot_open_other_customers_request(client, staff_user, customer_user, db):
    login_api(client, "staff@lab.example")
    client.post("/api/submissions", json=payload(customer_id=2, location="SX5N"))
    client.post("/logout", data={"csrf_token": client.headers.pop("X-CSRF-Token")})
    login(client, "jane@acme.example")
    assert client.get("/requests/TR00001").status_code == 404


def test_staff_receives_and_progresses(submitted):
    login(submitted, "staff@lab.example")
    detail = submitted.get("/requests/TR00001").text
    assert "Check in" in detail and "Mark received" not in detail
    page = submitted.get("/requests/TR00001/check-in").text
    assert 'name="received_by" value="JM"' in page and "Accept into LIMS" in page
    assert 'name="chemical_1"' in page and 'value="2" selected' in page  # exact name pre-matched
    response = submitted.post(
        "/requests/TR00001/check-in",
        data={
            **BOTTLES,
            "csrf_token": csrf_from(page),
            "date_received": lab_today().isoformat(),
            "received_by": "JM",
            "chemical_1": "2",
            "chemical_2": "3",
            "note": "2 bottles",
        },
    )
    assert "TR00001 checked in: 2 sample(s)" in response.text
    assert "2 bottles" in response.text
    response = _post_status(submitted, "/requests/TR00001", to_status="3")
    assert "is now in progress" in response.text


def test_bad_status_change_is_flashed_not_500(submitted):
    login(submitted, "staff@lab.example")
    future = (lab_today() + timedelta(days=3)).isoformat()
    page = submitted.get("/requests/TR00001/check-in").text
    response = submitted.post(
        "/requests/TR00001/check-in",
        data={
            **BOTTLES,
            "csrf_token": csrf_from(page),
            "date_received": future,
            "received_by": "JM",
            "chemical_1": "2",
            "chemical_2": "3",
        },
    )
    assert response.status_code == 200 and "future" in response.text and "Accept into LIMS" in response.text
    response = _post_status(submitted, "/requests/TR00001", to_status="3")  # still submitted
    assert "cannot move" in response.text
    response = _post_status(submitted, "/requests/TR00001", to_status="6")
    assert "by sending its invoice, not by hand" in response.text  # D-99


def test_customer_cancels_from_page(submitted):
    login(submitted, "jane@acme.example")
    response = _post_status(submitted, "/requests/TR00001", to_status="7", note="sent twice")
    assert "is now cancelled" in response.text and "Cancel request" not in response.text


def test_staff_home_filters(submitted):
    login(submitted, "staff@lab.example")
    assert "TR00001" in submitted.get("/admin?status=1").text
    assert "TR00001" not in submitted.get("/admin?status=7").text
    assert "TR00001" in submitted.get("/admin?status=&request_type=bogus").text  # junk filters ignored


def test_staff_creates_and_deactivates_users(client, staff_user):
    login(client, "staff@lab.example")
    page = client.get("/admin/users").text
    token = csrf_from(page)
    form = {"csrf_token": token, "email": "New@B.example", "role": "customer", "password": "x" * 12}
    assert (
        "must already exist in SLIM" in client.post("/admin/users", data={**form, "customer_id": "99"}).text
    )
    assert (
        "Created customer user new@b.example"
        in client.post("/admin/users", data={**form, "customer_id": "2"}).text
    )
    assert "already exists" in client.post("/admin/users", data={**form, "customer_id": "2"}).text
    assert (
        "need initials"
        in client.post("/admin/users", data={**form, "email": "s@lab.example", "role": "staff"}).text
    )
    page = client.get("/admin/users").text
    assert "new@b.example" in page and ">B<" in page.replace("<td>B</td>", ">B<")

    user_id = re.search(r"/admin/users/([0-9a-f-]{36})/active", page).group(1)
    assert "deactivated" in client.post(f"/admin/users/{user_id}/active", data={"csrf_token": token}).text


def test_customers_cannot_open_staff_pages(client, customer_user):
    login(client, "jane@acme.example")
    assert client.get("/admin/users").status_code == 403


def _unmatched_request(client):
    login_api(client, "jane@acme.example")
    body = payload(
        samples=[
            {
                "sample_name": "S-1",
                "chemical_name": "IPA semi grade",
                "processing_time": 7,
                "analysis_ids": [1],
            }
        ]
    )
    assert client.post("/api/submissions", json=body).status_code == 201
    client.post("/logout", data={"csrf_token": client.headers.pop("X-CSRF-Token")})


def test_staff_matches_free_text_chemical(client, staff_user, customer_user):
    _unmatched_request(client)
    login(client, "staff@lab.example")
    assert "match chemical" in client.get("/admin").text
    page = client.get("/requests/TR00001").text
    assert "Not matched to SLIM" in page and "IPA semi grade" in page
    response = client.post(
        "/requests/TR00001/samples/1/chemical", data={"csrf_token": csrf_from(page), "chemical_id": "3"}
    )
    assert "Sample 1 (S-1) matched." in response.text
    assert "→ SLIM: Chemical 02" in response.text and "IPA semi grade" in response.text  # customer text kept
    assert "match chemical" not in client.get("/admin").text


def test_matching_rejects_unknown_chemical_and_customers(client, staff_user, customer_user):
    _unmatched_request(client)
    login(client, "staff@lab.example")
    page = client.get("/requests/TR00001").text
    response = client.post(
        "/requests/TR00001/samples/1/chemical", data={"csrf_token": csrf_from(page), "chemical_id": "99"}
    )
    assert "unknown chemical 99" in response.text
    response = client.post(
        "/requests/TR00001/samples/9/chemical", data={"csrf_token": csrf_from(page), "chemical_id": "2"}
    )
    assert "sample 9 does not exist" in response.text
    client.post("/logout", data={"csrf_token": csrf_from(client.get("/admin").text)})
    login_api(client, "jane@acme.example")
    assert (
        client.post("/api/submissions/TR00001/samples/1/chemical", json={"chemical_id": 2}).status_code == 403
    )
    assert "Not matched" not in client.get("/requests/TR00001").text  # customers don't see matching UI


def test_check_in_page_blocks_unmatched_and_is_staff_only(client, staff_user, customer_user):
    _unmatched_request(client)
    login(client, "jane@acme.example")
    assert client.get("/requests/TR00001/check-in").status_code == 403
    client.post("/logout", data={"csrf_token": csrf_from(client.get("/").text)})
    login(client, "staff@lab.example")
    page = client.get("/requests/TR00001/check-in").text
    response = client.post(
        "/requests/TR00001/check-in",
        data={
            **BOTTLES,
            "csrf_token": csrf_from(page),
            "date_received": lab_today().isoformat(),
            "received_by": "JM",
            "chemical_1": "",
        },
    )
    assert "match the chemical for sample(s) 1" in response.text
    response = client.post(
        "/requests/TR00001/check-in",
        data={
            **BOTTLES,
            "csrf_token": csrf_from(page),
            "date_received": lab_today().isoformat(),
            "received_by": "JM",
            "chemical_1": "3",
        },
    )
    assert "checked in" in response.text
    assert "is already received" in client.get("/requests/TR00001/check-in").text
