"""Account pages (slim-data D-142): /account (change the password; MFA on or off), /account/mfa (set up an
authenticator), /reset/{token} (a one-time link: no login), and the Users page's reset link and MFA reset.
"""

import time
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from slim_lims import accounts, capabilities
from slim_lims.auth import RequireStaff, RequireUser
from slim_lims.db import get_db
from slim_lims.models import User
from slim_lims.security import csrf_protect
from slim_lims.web import flash, render

router = APIRouter(dependencies=[Depends(csrf_protect)])
DB = Annotated[Session, Depends(get_db)]
Text = Annotated[str, Form(max_length=2000)]
PENDING_SECRET = "mfa_setup_secret"  # the secret being set up, until a code confirms it


def _to(request: Request, name: str, **params) -> RedirectResponse:
    return RedirectResponse(request.url_for(name, **params).path, status_code=303)


@router.get("/account", name="account_home")
def account_home(request: Request, user: RequireUser, db: DB):
    return render(
        request,
        "account/index.html",
        {
            "mfa": accounts.mfa_of(db, user),
            "required": accounts.mfa_required(db, user),
            "left": accounts.recovery_left(db, user),
            "events": accounts.events(db, user)[-10:],
        },
    )


@router.post("/account/password", name="account_password")
def account_password(
    request: Request, user: RequireUser, db: DB, current: Text = "", new: Text = "", again: Text = ""
):
    try:
        accounts.change_password(db, user, current, new, again)
    except accounts.AccountRefused as refused:
        flash(request, str(refused), "error")
        return _to(request, "account_home")
    db.commit()
    request.session["auth_v"] = user.auth_version  # this session stays; every other one is signed out
    flash(request, "Password changed. Any other session on this account is signed out.")
    return _to(request, "account_home")


@router.get("/account/mfa", name="account_mfa")
def account_mfa(request: Request, user: RequireUser, db: DB):
    if accounts.mfa_of(db, user) is not None:
        return _to(request, "account_home")
    return render(request, "account/mfa.html", {"required": accounts.mfa_required(db, user), "setup": None})


@router.post("/account/mfa/start", name="account_mfa_start")
def account_mfa_start(request: Request, user: RequireUser, db: DB):
    if user.is_persona:
        raise HTTPException(status_code=403, detail="Demo personas don't use MFA.")
    if accounts.mfa_of(db, user) is not None:
        return _to(request, "account_home")
    setup = accounts.start(user)
    request.session[PENDING_SECRET] = setup.secret
    return render(
        request,
        "account/mfa.html",
        {"required": accounts.mfa_required(db, user), "setup": setup, "qr": accounts.qr_svg(setup.uri)},
    )


@router.post("/account/mfa/confirm", name="account_mfa_confirm")
def account_mfa_confirm(
    request: Request, user: RequireUser, db: DB, code: Annotated[str, Form(max_length=20)] = ""
):
    secret = request.session.get(PENDING_SECRET)
    if not secret:
        flash(request, "Start the setup again.", "error")
        return _to(request, "account_mfa")
    try:
        codes = accounts.confirm(db, user, secret, code, request.app.state.settings.secret_key, time.time())
    except accounts.AccountRefused as refused:
        uri = accounts.totp.otpauth_uri(secret, user.email, accounts.ISSUER)
        setup = accounts.Enrolment(secret, uri)  # the same secret: the app may already have it
        return render(
            request,
            "account/mfa.html",
            {
                "required": accounts.mfa_required(db, user),
                "setup": setup,
                "qr": accounts.qr_svg(setup.uri),
                "error": str(refused),
            },
            status_code=422,
        )
    db.commit()
    request.session.pop(PENDING_SECRET, None)
    return render(request, "account/recovery_codes.html", {"codes": codes})


@router.post("/account/mfa/off", name="account_mfa_off")
def account_mfa_off(request: Request, user: RequireUser, db: DB, current: Text = ""):
    try:
        accounts.turn_off(db, user, current)
    except accounts.AccountRefused as refused:
        flash(request, str(refused), "error")
    else:
        db.commit()
        flash(request, "MFA is off for this account.")
    return _to(request, "account_home")


# ---------------------------------------------------------------- a reset link (no login)


@router.get("/reset/{token}", name="password_reset")
def password_reset_page(request: Request, token: str, db: DB):
    row = accounts.reset_for(db, token)
    return render(request, "account/reset.html", {"ok": row is not None, "token": token}, 200 if row else 404)


@router.post("/reset/{token}", name="password_reset_form")
def password_reset(request: Request, token: str, db: DB, new: Text = "", again: Text = ""):
    try:
        user = accounts.use_reset(db, token, new, again)
    except accounts.AccountRefused as refused:
        return render(
            request,
            "account/reset.html",
            {"ok": accounts.reset_for(db, token) is not None, "token": token, "error": str(refused)},
            422,
        )
    db.commit()
    request.session.clear()
    flash(request, f"Password set for {user.email}: sign in with it now.")
    return _to(request, "login")


# ---------------------------------------------------------------- from the Users page


def _managed(db: Session, user: User, user_id: str) -> User:
    if not capabilities.can(db, user, capabilities.MANAGE_USERS) or user.is_persona:
        raise HTTPException(status_code=403, detail="Managing users needs the capability.")
    try:
        target = db.get(User, uuid.UUID(user_id))
    except ValueError:
        target = None
    if target is None:
        raise HTTPException(status_code=404, detail="User not found.")
    return target


@router.post("/admin/users/{user_id}/reset-link", name="make_reset_link")
def make_reset_link(request: Request, user_id: str, user: RequireStaff, db: DB):
    target = _managed(db, user, user_id)
    try:
        url = accounts.make_reset(db, target, user, lambda t: str(request.url_for("password_reset", token=t)))
    except accounts.AccountRefused as refused:
        flash(request, str(refused), "error")
    else:
        db.commit()
        flash(request, f"Reset link for {target.email} (shown once; also in the outbox): {url}")
    return _to(request, "admin_users")


@router.post("/admin/users/{user_id}/reset-mfa", name="reset_user_mfa")
def reset_user_mfa(request: Request, user_id: str, user: RequireStaff, db: DB, reason: Text = ""):
    target = _managed(db, user, user_id)
    try:
        accounts.reset_mfa(db, target, user, reason)
    except accounts.AccountRefused as refused:
        flash(request, str(refused), "error")
    else:
        db.commit()
        flash(request, f"MFA reset for {target.email}: they set it up again at their next sign-in.")
    return _to(request, "admin_users")
