"""Password hashing, CSRF protection, security headers and the request-size cap."""

import hmac
import math
import secrets
import threading
import time
from collections import defaultdict, deque
from collections.abc import Callable

import bcrypt
from fastapi import HTTPException, Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

# ---------------------------------------------------------------- login attempts


class LoginThrottle:
    """Failed logins counted per account and per client address (review A4). Past the limit within the
    window, a login is refused without checking the password until the oldest failure ages out.
    In memory, per process: enough for the demo's one process; a restart forgets it."""

    def __init__(
        self,
        per_account: int = 5,
        per_address: int = 20,
        window_seconds: float = 900,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.limits = {"account": per_account, "address": per_address}
        self.window = window_seconds
        self.clock = clock
        self._failures: dict[tuple[str, str], deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def _recent(self, key: tuple[str, str], now: float) -> deque[float]:
        times = self._failures[key]
        while times and times[0] <= now - self.window:
            times.popleft()
        if not times:
            del self._failures[key]
            return deque()
        return times

    def wait_seconds(self, account: str, address: str) -> int:
        """0 when a login may be tried; otherwise the seconds until it may."""
        with self._lock:
            now = self.clock()
            wait = 0.0
            for kind, value in (("account", account), ("address", address)):
                times = self._recent((kind, value), now)
                if len(times) >= self.limits[kind]:
                    wait = max(wait, times[0] + self.window - now)
            return math.ceil(wait)

    def failed(self, account: str, address: str) -> None:
        with self._lock:
            now = self.clock()
            for key in (("account", account), ("address", address)):
                self._failures[key].append(now)

    def succeeded(self, account: str) -> None:
        with self._lock:
            self._failures.pop(("account", account), None)


# ---------------------------------------------------------------- passwords

BCRYPT_MAX_BYTES = 72  # bcrypt ignores (bcrypt>=5: rejects) anything longer
_DUMMY_HASH = bcrypt.hashpw(b"timing-equaliser", bcrypt.gensalt()).decode()


def hash_password(password: str) -> str:
    raw = password.encode()
    if len(raw) > BCRYPT_MAX_BYTES:
        raise ValueError(f"Password must be at most {BCRYPT_MAX_BYTES} bytes.")
    return bcrypt.hashpw(raw, bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str | None) -> bool:
    """Constant-ish time: always runs one bcrypt check, even for unknown users."""
    raw = password.encode()
    if len(raw) > BCRYPT_MAX_BYTES:
        bcrypt.checkpw(b"x", _DUMMY_HASH.encode())
        return False
    if password_hash is None:
        bcrypt.checkpw(raw, _DUMMY_HASH.encode())
        return False
    return bcrypt.checkpw(raw, password_hash.encode())


# ---------------------------------------------------------------- CSRF

CSRF_SESSION_KEY = "csrf"
CSRF_HEADER = "X-CSRF-Token"
CSRF_FORM_FIELD = "csrf_token"
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def csrf_token(request: Request) -> str:
    """Returns the session's CSRF token, creating one if needed. Exposed to templates."""
    token = request.session.get(CSRF_SESSION_KEY)
    if not token:
        token = secrets.token_urlsafe(32)
        request.session[CSRF_SESSION_KEY] = token
    return token


async def csrf_protect(request: Request) -> None:
    """Dependency for every router with state-changing routes.

    Accepts the token from the X-CSRF-Token header (fetch/JSON) or the csrf_token form field
    (HTML forms). Starlette caches the parsed form, so the endpoint can still read it.
    """
    if request.method in _SAFE_METHODS:
        return
    if "authorization" in request.headers:
        # Bearer-token clients (slim-lims, spec §12). Such requests are authenticated by the
        # token only -- auth.current_user ignores the session cookie for them -- and a browser
        # cannot add this header to a cross-site request without a CORS preflight the app
        # never allows. So there is no cookie to ride on and nothing for CSRF to protect.
        return
    expected = request.session.get(CSRF_SESSION_KEY)
    supplied = request.headers.get(CSRF_HEADER)
    if not supplied:
        content_type = request.headers.get("content-type", "")
        if content_type.startswith(("application/x-www-form-urlencoded", "multipart/form-data")):
            form = await request.form()
            value = form.get(CSRF_FORM_FIELD)
            supplied = value if isinstance(value, str) else None
    if not expected or not supplied or not hmac.compare_digest(expected, supplied):
        raise HTTPException(status_code=403, detail="CSRF token missing or invalid.")


# ---------------------------------------------------------------- headers

CONTENT_SECURITY_POLICY = (
    "default-src 'self'; "
    "script-src 'self' https://code.jquery.com https://cdn.jsdelivr.net; "
    "style-src 'self' https://cdn.jsdelivr.net; "
    "font-src 'self'; "
    "img-src 'self' data:; "
    "form-action 'self'; "
    "frame-ancestors 'none'; "
    "base-uri 'self'"
)


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp, *, hsts: bool) -> None:
        self.app = app
        self.hsts = hsts

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers += [
                    (b"x-content-type-options", b"nosniff"),
                    (b"x-frame-options", b"DENY"),
                    (b"referrer-policy", b"strict-origin-when-cross-origin"),
                    (b"content-security-policy", CONTENT_SECURITY_POLICY.encode()),
                ]
                if self.hsts:
                    headers.append((b"strict-transport-security", b"max-age=31536000; includeSubDomains"))
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_with_headers)


# ---------------------------------------------------------------- request size


class BodySizeLimitMiddleware:
    """Rejects request bodies over `max_bytes` with 413, whether or not Content-Length is sent."""

    def __init__(self, app: ASGIApp, *, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        for name, value in scope.get("headers", []):
            if name == b"content-length" and value.isdigit() and int(value) > self.max_bytes:
                await _send_413(send)
                return

        received = 0
        rejected = False

        async def limited_receive() -> Message:
            nonlocal received, rejected
            if rejected:
                return {"type": "http.disconnect"}
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    # Answer 413 ourselves, then tell the app the client went away. Raising
                    # instead would be turned into a 400 by FastAPI's body parsing.
                    rejected = True
                    await _send_413(send)
                    return {"type": "http.disconnect"}
            return message

        async def guarded_send(message: Message) -> None:
            if not rejected:
                await send(message)

        try:
            await self.app(scope, limited_receive, guarded_send)
        except Exception:
            if not rejected:
                raise


async def _send_413(send: Send) -> None:
    body = b'{"detail":"Request body too large."}'
    await send(
        {
            "type": "http.response.start",
            "status": 413,
            "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())],
        }
    )
    await send({"type": "http.response.body", "body": body})
