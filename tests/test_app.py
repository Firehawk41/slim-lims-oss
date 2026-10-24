import re

from tests.conftest import login

# Routes that change state but are deliberately not CSRF-protected (e.g. API-key machine
# endpoints, once they exist). Every entry needs a reason.
CSRF_EXEMPT: set[str] = set()


def test_health(client):
    assert client.get("/health").json()["status"] == "ok"


def test_security_headers_on_every_response(client):
    for path in ("/health", "/login", "/does-not-exist"):
        headers = client.get(path).headers
        assert headers["x-content-type-options"] == "nosniff"
        assert headers["x-frame-options"] == "DENY"
        assert "'unsafe-inline'" not in headers["content-security-policy"]
        assert "frame-ancestors 'none'" in headers["content-security-policy"]


def test_no_hsts_outside_production(client):
    assert "strict-transport-security" not in client.get("/health").headers


def test_session_cookie_flags(client):
    response = client.get("/login")
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie
    assert "samesite=lax" in cookie


def test_oversized_body_is_rejected(client):
    response = client.post("/login", content=b"x" * (1024 * 1024 + 1),
                           headers={"content-type": "application/x-www-form-urlencoded"})
    assert response.status_code == 413
    assert response.headers["x-frame-options"] == "DENY"


def test_oversized_chunked_body_is_rejected(client):
    def chunks():
        for _ in range(20):
            yield b"x" * 100_000

    response = client.post("/login", content=chunks(), headers={"content-type": "application/x-www-form-urlencoded"})
    assert response.status_code == 413


def test_every_state_changing_route_is_csrf_protected(client, staff_user):
    """Behavioural, so it doesn't depend on FastAPI's internal route classes: every
    non-GET operation in the schema must refuse a logged-in request that has no token."""
    login(client, "staff@lab.example")
    unprotected = []
    for path, operations in client.app.openapi()["paths"].items():
        if path in CSRF_EXEMPT:
            continue
        url = re.sub(r"\{[^}]+\}", "1", path)
        for method in operations:
            if method.upper() in {"GET", "HEAD", "OPTIONS"}:
                continue
            response = client.request(method.upper(), url, follow_redirects=False)
            if response.status_code != 403:
                unprotected.append(f"{method.upper()} {path} -> {response.status_code}")
    assert unprotected == []


def test_404_renders_html_page(client):
    response = client.get("/does-not-exist", headers={"accept": "text/html"})
    assert response.status_code == 404
    assert "Error 404" in response.text
