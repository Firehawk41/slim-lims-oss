from tests.conftest import PASSWORD, csrf_from, login


def test_index_redirects_anonymous_to_login(client):
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_login_page_sets_csrf_token(client):
    response = client.get("/login")
    assert response.status_code == 200
    assert csrf_from(response.text)


def test_staff_login_lands_on_the_board(client, staff_user):
    response = login(client, "staff@lab.example")
    assert response.status_code == 303
    assert "Lab board" in client.get("/").text  # spec §13
    assert "Testing requests" in client.get("/admin").text


def test_email_is_case_insensitive(client, staff_user):
    assert login(client, "  STAFF@Lab.Example ").status_code == 303


def test_customer_login_sees_their_board_and_list(client, customer_user):
    login(client, "jane@acme.example")
    response = client.get("/")
    assert response.status_code == 200 and "My requests" in response.text and "At the lab" in response.text
    assert "My testing requests" in client.get("/requests").text


def test_wrong_password_is_rejected(client, staff_user):
    response = login(client, "staff@lab.example", "wrong password")
    assert response.status_code == 401
    assert "Invalid email or password." in response.text


def test_unknown_email_gets_same_message(client):
    response = login(client, "nobody@lab.example")
    assert response.status_code == 401
    assert "Invalid email or password." in response.text


def test_inactive_user_cannot_log_in(client, db, staff_user):
    staff_user.is_active = False
    db.commit()
    assert login(client, "staff@lab.example").status_code == 401


def test_deactivated_user_is_logged_out_on_next_request(client, db, staff_user):
    login(client, "staff@lab.example")
    staff_user.is_active = False
    db.commit()
    assert client.get("/admin", follow_redirects=False).status_code == 303


def test_login_without_csrf_token_is_forbidden(client, staff_user):
    client.get("/login")
    response = client.post("/login", data={"email": "staff@lab.example", "password": PASSWORD})
    assert response.status_code == 403


def test_login_rotates_csrf_token(client, staff_user):
    before = csrf_from(client.get("/login").text)
    login(client, "staff@lab.example")
    after = csrf_from(client.get("/admin").text)
    assert before != after


def test_logout_requires_post_with_csrf(client, staff_user):
    login(client, "staff@lab.example")
    assert client.post("/logout").status_code == 403
    token = csrf_from(client.get("/admin").text)
    response = client.post("/logout", data={"csrf_token": token}, follow_redirects=False)
    assert response.status_code == 303
    assert client.get("/admin", follow_redirects=False).status_code == 303


def test_customer_cannot_open_admin(client, customer_user):
    login(client, "jane@acme.example")
    response = client.get("/admin")
    assert response.status_code == 403


def test_next_parameter_round_trip(client, staff_user):
    response = client.get("/admin", follow_redirects=False)
    assert response.headers["location"] == "/login?next=%2Fadmin"
    page = client.get(response.headers["location"]).text
    token = csrf_from(page)
    response = client.post(
        "/login",
        data={"email": "staff@lab.example", "password": PASSWORD, "csrf_token": token, "next": "/admin"},
        follow_redirects=False,
    )
    assert response.headers["location"] == "/admin"


def test_next_parameter_rejects_other_sites(client, staff_user):
    token = csrf_from(client.get("/login").text)
    for evil in ("//evil.example", "https://evil.example", "/\\evil.example"):
        response = client.post(
            "/login",
            data={"email": "staff@lab.example", "password": PASSWORD, "csrf_token": token, "next": evil},
            follow_redirects=False,
        )
        assert response.headers["location"] == "/"
        token = csrf_from(client.get("/admin").text)


def test_api_returns_401_json_when_anonymous(client):
    response = client.get("/api/me")
    assert response.status_code == 401
    assert response.json() == {"detail": "Not authenticated."}


def test_api_me(client, customer_user):
    login(client, "jane@acme.example")
    assert client.get("/api/me").json()["customer_id"] == 1


def test_admin_shows_reference_status_and_refreshes(engine, db, staff_user):
    from fastapi.testclient import TestClient

    from slim_lims.catalog import sync_analysis_catalog
    from slim_lims.main import create_app
    from tests.conftest import make_settings
    from tests.slim_fixtures import create_slim_tables

    create_slim_tables(engine)
    sync_analysis_catalog(engine, db)
    db.commit()
    app = create_app(make_settings(str(engine.url)), engine=engine)  # real ReferenceCache
    with TestClient(app) as client:
        login(client, "staff@lab.example")
        page = client.get("/admin").text
        assert "2 customers" in page and "offered in the portal" in page
        token = csrf_from(page)
        response = client.post("/admin/reference/refresh", data={"csrf_token": token})
        assert "Reference data reloaded from SLIM." in response.text


def test_failed_logins_lock_the_account_for_a_while(client, staff_user):  # review A4
    for _ in range(5):
        assert login(client, "staff@lab.example", "wrong password").status_code == 401
    locked = login(client, "staff@lab.example")  # even the right password waits
    assert locked.status_code == 429 and "Too many failed logins. Try again in 15 minutes." in locked.text
    assert int(locked.headers["Retry-After"]) > 0


def test_the_throttle_counts_per_account_and_per_address_and_forgets_with_time():
    from slim_lims.security import LoginThrottle

    now = [0.0]
    throttle = LoginThrottle(per_account=2, per_address=3, window_seconds=60, clock=lambda: now[0])
    throttle.failed("a", "1.2.3.4")
    assert throttle.wait_seconds("a", "1.2.3.4") == 0
    throttle.failed("a", "1.2.3.4")
    assert throttle.wait_seconds("a", "5.6.7.8") == 60  # the account, from anywhere
    throttle.failed("b", "1.2.3.4")
    assert throttle.wait_seconds("c", "1.2.3.4") == 60  # the address, for any account
    now[0] = 30.5
    assert throttle.wait_seconds("a", "5.6.7.8") == 30  # rounded up
    now[0] = 60.0
    assert throttle.wait_seconds("a", "5.6.7.8") == 0 and throttle.wait_seconds("c", "1.2.3.4") == 0


def test_a_successful_login_clears_the_accounts_failures(client, staff_user):
    for _ in range(4):
        login(client, "staff@lab.example", "wrong password")
    assert login(client, "staff@lab.example").status_code == 303
    client.post("/logout", data={"csrf_token": csrf_from(client.get("/").text)})
    assert login(client, "staff@lab.example", "wrong password").status_code == 401  # counting afresh


def _try(client, email: str, headers: dict):
    """A failed login, as the proxy passes it on (its X-Forwarded-For)."""
    token = csrf_from(client.get("/login", headers=headers).text)
    data = {"email": email, "password": "wrong password", "csrf_token": token, "next": "/"}
    return client.post("/login", data=data, headers=headers, follow_redirects=False)


def test_t1_a_forged_forwarded_for_neither_dodges_nor_frames_the_login_limit(engine):
    """Review T-1: behind one proxy (Render), the throttle counts the address the proxy appended. A client
    that writes a new X-Forwarded-For each try is still held after 20 failures; the address it wrote, a
    bystander's, is never locked."""
    from fastapi.testclient import TestClient
    from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

    from slim_lims.main import create_app
    from tests.conftest import demo_reference, make_settings

    app = create_app(
        make_settings(str(engine.url), trusted_proxy_hops=1), engine=engine, reference=demo_reference()
    )
    served = ProxyHeadersMiddleware(app, trusted_hosts="*")  # as Render runs it: --forwarded-allow-ips='*'
    with TestClient(served) as attacker, TestClient(served) as bystander:
        for n in range(20):  # a new forged address each time; the proxy appends the real one
            forged = {"X-Forwarded-For": f"198.51.100.{n}, 203.0.113.9"}
            assert _try(attacker, f"user{n}@example.com", forged).status_code == 401
        held = _try(attacker, "user99@example.com", {"X-Forwarded-For": "198.51.100.200, 203.0.113.9"})
        assert held.status_code == 429
        framed = _try(bystander, "user98@example.com", {"X-Forwarded-For": "198.51.100.1"})
        assert framed.status_code == 401  # the forged address isn't held


def test_t1_the_address_is_the_proxys_entry():
    from types import SimpleNamespace

    from slim_lims.security import client_address

    def request(header):
        return SimpleNamespace(client=SimpleNamespace(host="10.0.0.1"), headers={"x-forwarded-for": header})

    assert client_address(request("1.1.1.1, 2.2.2.2"), 0) == "10.0.0.1"  # no proxy: the peer
    assert client_address(request("1.1.1.1, 2.2.2.2"), 1) == "2.2.2.2"
    assert client_address(request("1.1.1.1, 2.2.2.2, 3.3.3.3"), 2) == "2.2.2.2"
    assert client_address(request("2.2.2.2"), 2) == "2.2.2.2"  # fewer than the hops: the leftmost
    assert client_address(request(""), 1) == "10.0.0.1"
