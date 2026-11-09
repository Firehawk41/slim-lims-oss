"""Session login: dependencies that identify the user, and the /login and /logout routes.

current_user   -- the logged-in User (or the slim-lims ServiceClient for a valid bearer token) or
                  None; also stored on request.state.user for templates
require_user   -- any active user; HTML requests redirect to /login, /api requests get 401
require_staff  -- staff only; 403 otherwise
"""

import hmac
import time
import uuid
from dataclasses import dataclass
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from slim_lims import capabilities
from slim_lims.db import LAB_SESSION_KEY, get_db
from slim_lims.models import User
from slim_lims.personas import persona_choices
from slim_lims.security import csrf_protect, verify_password
from slim_lims.web import flash, render

SESSION_USER_KEY = "user_id"


class LoginRequired(Exception):
    """Raised by require_user; handled in main.py (redirect for HTML, 401 for /api)."""


class StaffRequired(Exception):
    """Raised by require_staff; handled in main.py (403)."""


class MfaSetupRequired(Exception):
    """Raised by require_user for someone whose role requires MFA and who has none yet (D-142); handled in
    main.py (to the setup page)."""


SESSION_AUTH_KEY = "auth_v"  # the account's auth_version when the session began (D-142)
MFA_PENDING_KEY = "mfa_pending"  # password checked, code not yet
MFA_PENDING_SECONDS = 300
# Where someone who must set up MFA may still go (D-142).
MFA_SETUP_ROUTES = frozenset({"account_mfa", "account_mfa_start", "account_mfa_confirm", "logout"})


# The API routes slim-lims may call with its token (spec §12); everything else is refused.
LIMS_ROUTES = frozenset({"list_submissions", "get_submission", "change_status"})


@dataclass(frozen=True)
class ServiceClient:
    """Another SLIM module calling the API with a bearer token -- not a person, no session.
    Sees every customer's requests (like staff) but may only use LIMS_ROUTES, and the
    workflow only lets it set the statuses in domain.workflow.SYSTEM_STATUSES."""

    name: str
    is_staff: bool = True
    is_service: bool = True
    is_persona: bool = False
    is_active: bool = True
    customer_id: None = None
    initials: None = None

    @property
    def id(self) -> str:
        return self.name


def _service_client(request: Request) -> ServiceClient | None:
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    expected = request.app.state.settings.lims_api_token
    if scheme.lower() != "bearer" or not expected or not token.strip():
        return None
    if not hmac.compare_digest(token.strip().encode(), expected.encode()):
        return None
    route = request.scope.get("route")
    if getattr(route, "name", None) not in LIMS_ROUTES or not getattr(route, "path", "").startswith("/api/"):
        raise HTTPException(status_code=403, detail="slim-lims may not use this endpoint.")
    return ServiceClient(name="slim-lims")


def current_user(request: Request, db: Annotated[Session, Depends(get_db)]) -> User | None:
    if "authorization" in request.headers:
        # Token requests never fall back to the session cookie (that is what lets
        # csrf_protect skip them). A wrong or missing token is simply "not logged in".
        user = _service_client(request)
        request.state.user = user
        return user
    user = None
    raw_id = request.session.get(SESSION_USER_KEY)
    if raw_id:
        try:
            user = db.get(User, uuid.UUID(raw_id))
        except ValueError:
            user = None
        if user is None or not user.is_active:
            request.session.pop(SESSION_USER_KEY, None)
            user = None
        elif request.session.get(SESSION_AUTH_KEY, 0) != (user.auth_version or 0):
            request.session.clear()  # the password was reset or changed, or MFA reset (D-142)
            user = None
    request.state.user = user
    request.state.capabilities = capabilities.effective(db, user) if user is not None else frozenset()
    if user is not None and request.app.state.settings.require_mfa:
        _hold_for_mfa_setup(request, db, user)
    return user


def _hold_for_mfa_setup(request: Request, db: Session, user: User) -> None:
    """Someone whose role requires MFA and who has none yet goes nowhere but its setup (D-142)."""
    from slim_lims import accounts

    route = getattr(request.scope.get("route"), "name", None)
    if (
        route not in MFA_SETUP_ROUTES
        and accounts.mfa_required(db, user)
        and accounts.mfa_of(db, user) is None
    ):
        raise MfaSetupRequired


def require_user(user: Annotated[User | None, Depends(current_user)]) -> User:
    if user is None:
        raise LoginRequired
    return user


def require_staff(user: Annotated[User, Depends(require_user)]) -> User:
    if not user.is_staff:
        raise StaffRequired
    return user


CurrentUser = Annotated[User | None, Depends(current_user)]
RequireUser = Annotated[User, Depends(require_user)]
RequireStaff = Annotated[User, Depends(require_staff)]

router = APIRouter(dependencies=[Depends(csrf_protect)])


def _safe_next(next_url: str | None) -> str:
    # Only same-site absolute paths; "//evil.example" and "/\evil" are protocol-relative tricks.
    if next_url and next_url.startswith("/") and not next_url.startswith(("//", "/\\")):
        return next_url
    return "/"


@router.get("/login", name="login")
def login_form(request: Request, user: CurrentUser, next: str | None = None):
    if user is not None:
        return RedirectResponse(_safe_next(next), status_code=303)
    return render(request, "login.html", {"next": _safe_next(next)})


@router.post("/login")
def login(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    email: Annotated[str, Form()],
    password: Annotated[str, Form()],
    next: Annotated[str, Form()] = "/",
):
    account = email.strip().lower()
    address = request.client.host if request.client else "unknown"
    throttle = request.app.state.login_throttle
    if wait := throttle.wait_seconds(account, address):  # the password isn't even checked
        minutes = -(-wait // 60)
        refused = render(
            request,
            "login.html",
            {"error": f"Too many failed logins. Try again in {minutes} minute{'s' * (minutes != 1)}.",
             "next": _safe_next(next), "email": email},
            status_code=429,
        )  # fmt: skip
        refused.headers["Retry-After"] = str(wait)
        return refused
    user = db.scalars(select(User).where(User.email == account)).first()
    if user is not None and user.is_persona:
        user = None  # personas are chosen on /demo/personas, never signed in with a password
    ok = verify_password(password, user.password_hash if user else None)
    if not ok or user is None or not user.is_active:
        throttle.failed(account, address)
        return render(
            request,
            "login.html",
            {"error": "Invalid email or password.", "next": _safe_next(next), "email": email},
            status_code=401,
        )
    from slim_lims import accounts

    # New session on login: drops any pre-login state and rotates the CSRF token.
    request.session.clear()
    if accounts.mfa_of(db, user) is not None:  # the password was right; now the code (D-142)
        request.session[MFA_PENDING_KEY] = {"id": str(user.id), "at": time.time(), "next": _safe_next(next)}
        return RedirectResponse(request.url_for("login_code").path, status_code=303)
    throttle.succeeded(account)
    _sign_in(request, user)
    return RedirectResponse(_safe_next(next), status_code=303)


def _sign_in(request: Request, user: User) -> None:
    request.session[SESSION_USER_KEY] = str(user.id)
    request.session[SESSION_AUTH_KEY] = user.auth_version or 0


def _pending(request: Request, db: Session) -> User | None:
    pending = request.session.get(MFA_PENDING_KEY)
    if not isinstance(pending, dict) or time.time() - pending.get("at", 0) > MFA_PENDING_SECONDS:
        return None
    try:
        user = db.get(User, uuid.UUID(pending.get("id", "")))
    except ValueError:
        return None
    return user if user is not None and user.is_active and not user.is_persona else None


@router.get("/login/code", name="login_code")
def login_code_form(request: Request, db: Annotated[Session, Depends(get_db)]):
    if _pending(request, db) is None:
        request.session.pop(MFA_PENDING_KEY, None)
        return RedirectResponse(request.url_for("login").path, status_code=303)
    return render(request, "login_code.html", {})


@router.post("/login/code", name="login_code_form")
def login_code(request: Request, db: Annotated[Session, Depends(get_db)], code: Annotated[str, Form()] = ""):
    """The second step (D-142): an authenticator code or a recovery code. A wrong one counts toward the same
    lockout as a wrong password."""
    from slim_lims import accounts

    user = _pending(request, db)
    if user is None:
        request.session.pop(MFA_PENDING_KEY, None)
        flash(request, "Sign in again: the code wasn't entered in time.", "error")
        return RedirectResponse(request.url_for("login").path, status_code=303)
    address = request.client.host if request.client else "unknown"
    throttle = request.app.state.login_throttle
    if wait := throttle.wait_seconds(user.email, address):
        minutes = -(-wait // 60)
        refused = render(
            request,
            "login_code.html",
            {"error": f"Too many failed attempts. Try again in {minutes} minute{'s' * (minutes != 1)}."},
            status_code=429,
        )
        refused.headers["Retry-After"] = str(wait)
        return refused
    secret_key = request.app.state.settings.secret_key
    if not accounts.check_code(db, user, code[:40], secret_key, time.time()):
        throttle.failed(user.email, address)
        return render(request, "login_code.html", {"error": "That code doesn't match."}, status_code=401)
    db.commit()
    throttle.succeeded(user.email)
    next_url = request.session[MFA_PENDING_KEY].get("next", "/")
    request.session.clear()
    _sign_in(request, user)
    return RedirectResponse(_safe_next(next_url), status_code=303)


@router.get("/demo/personas", name="demo_personas")
def demo_personas_page(request: Request, db: Annotated[Session, Depends(get_db)], user: CurrentUser):
    """Continue as a demo persona (DEMO_PERSONAS only): who you are is your claim, recorded as ASSERTED."""
    if not request.app.state.settings.demo_personas:
        raise HTTPException(status_code=404, detail="Not Found")
    return render(request, "demo_personas.html", {"personas": persona_choices(db)})


@router.post("/demo/continue-as", name="continue_as")
def continue_as(request: Request, db: Annotated[Session, Depends(get_db)], user_id: Annotated[str, Form()]):
    if not request.app.state.settings.demo_personas:
        raise HTTPException(status_code=404, detail="Not Found")
    try:
        persona = db.get(User, uuid.UUID(user_id))
    except ValueError:
        persona = None
    if persona is None or not persona.is_persona or not persona.is_active:
        raise HTTPException(status_code=404, detail="No such persona.")
    labs = request.app.state.labs
    lab = request.session.get(LAB_SESSION_KEY)
    if labs is not None and lab is None:  # their first persona: a lab of their own (D-102)
        lab = labs.claim()
        with labs.enter(lab)() as lab_db:
            persona = lab_db.scalars(select(User).where(User.email == persona.email)).one()
    request.session.clear()  # like a login: new session, new CSRF token
    if lab is not None:
        request.session[LAB_SESSION_KEY] = lab
    _sign_in(request, persona)
    return RedirectResponse("/", status_code=303)


@router.post("/demo/fresh-lab", name="fresh_lab")
def fresh_lab(request: Request):
    """Leaves this lab (it is dropped) for a fresh one at the next persona (D-102)."""
    labs = request.app.state.labs
    if labs is None:
        raise HTTPException(status_code=404, detail="Not Found")
    if lab := request.session.get(LAB_SESSION_KEY):
        labs.drop(lab)
    request.session.clear()
    return RedirectResponse(request.url_for("demo_personas").path, status_code=303)


@router.post("/logout", name="logout")
def logout(request: Request):
    lab = request.session.get(LAB_SESSION_KEY)
    request.session.clear()
    if lab is not None:  # a visitor's lab stays theirs until it idles out (D-102)
        request.session[LAB_SESSION_KEY] = lab
    return RedirectResponse(request.url_for("login").path, status_code=303)


def login_redirect(request: Request) -> RedirectResponse:
    target = request.url.path
    if request.url.query:
        target += "?" + request.url.query
    return RedirectResponse(f"/login?next={quote(target, safe='')}", status_code=303)
