"""Accounts (slim-data D-142): password reset links, changing one's password, and MFA.

* A reset link is made by someone who manages users, for a user they may manage: shown once on screen and
  kept as a message in the outbox (not transmitted, D-124). One use, 24 hours, only its token's sha256
  kept. Setting the new password bumps the user's `auth_version`, which signs every older session out.
* MFA is an authenticator app (TOTP, `totp.py`) with ten single-use recovery codes (bcrypt hashes). Optional
  for everyone; required for supervisors and quality managers (`REQUIRE_MFA`, on by default): they are sent
  to set it up after signing in. Personas and the API token skip it. A code never counts twice.
* Losing the phone: a recovery code, or someone who manages users resets the user's MFA, with a reason.
* Everything that happens to an account is kept in `account_events` (append-only).
Modules flush; routes commit.
"""

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from slim_lims import capabilities, totp
from slim_lims.models import (
    LAB_ROLE_SUPERVISOR,
    AccountEventRow,
    AccountMessageRow,
    MfaRecoveryCodeRow,
    PasswordResetRow,
    User,
    UserMfaRow,
)
from slim_lims.security import hash_password, verify_password

RESET_HOURS = 24
MIN_PASSWORD = 12
ISSUER = "SlimLIMS"
OUTBOX = "outbox"


class AccountRefused(ValueError):
    pass


def _now() -> datetime:
    return datetime.now(UTC)


def _aware(at: datetime) -> datetime:
    return at if at.tzinfo is not None else at.replace(tzinfo=UTC)  # SQLite hands naive datetimes back


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def log(db: Session, user: User, kind: str, by: str, reason: str = "") -> None:
    db.add(AccountEventRow(user_id=user.id, kind=kind, by=by, reason=reason))
    db.flush()


def events(db: Session, user: User) -> list[AccountEventRow]:
    return list(
        db.scalars(
            select(AccountEventRow).where(AccountEventRow.user_id == user.id).order_by(AccountEventRow.id)
        )
    )


def _signed_out_everywhere(user: User) -> None:
    user.auth_version = (user.auth_version or 0) + 1


def _new_password(password: str, again: str) -> str:
    if password != again:
        raise AccountRefused("The two passwords differ.")
    if len(password) < MIN_PASSWORD:
        raise AccountRefused(f"Passwords must be at least {MIN_PASSWORD} characters.")
    try:
        return hash_password(password)
    except ValueError as too_long:
        raise AccountRefused(str(too_long)) from too_long


# ---------------------------------------------------------------- reset links and password changes


def make_reset(db: Session, target: User, by: User, link_for) -> str:
    """A one-time reset link for `target` (24 hours); the message is kept in the outbox. `link_for(token)`
    makes the absolute URL. Refused for a persona or someone `by` may not manage."""
    if target.is_persona:
        raise AccountRefused("Demo personas have no password.")
    if capabilities.outranks(target, by):
        raise AccountRefused("Nobody acts on someone with a higher lab role.")
    token = secrets.token_urlsafe(32)
    db.add(
        PasswordResetRow(
            user_id=target.id,
            token_sha256=_hash(token),
            created_by=by.email,
            expires_at=_now() + timedelta(hours=RESET_HOURS),
        )
    )
    url = link_for(token)
    # The copy kept in the outbox never holds the link: every staff member reads the outbox, and the link
    # is the account (review 2026-11-09 A2). It is shown once, to whoever made it.
    hidden = url.rsplit("/", 1)[0] + "/[the link, shown once to " + by.email + "; not kept]"
    db.add(
        AccountMessageRow(
            to_address=target.email,
            subject="Set a new password for SlimLIMS",
            body=(
                "Hello,\n\nA new password was asked for on your account. Open this link to set one "
                f"(it works once, for {RESET_HOURS} hours):\n\n{hidden}\n\n"
                "If you didn't expect this, tell the lab."
            ),
            sent_by=by.email,
            transport=OUTBOX,
        )
    )
    log(db, target, "reset_link_made", by.email)
    return url


def reset_for(db: Session, token: str) -> PasswordResetRow | None:
    """The live reset a token opens: unknown, used or expired gives None."""
    if not token or len(token) > 100:
        return None
    row = db.scalars(select(PasswordResetRow).where(PasswordResetRow.token_sha256 == _hash(token))).first()
    if row is None or row.used_at is not None or _aware(row.expires_at) < _now():
        return None
    return row


def use_reset(db: Session, token: str, password: str, again: str) -> User:
    row = reset_for(db, token)
    if row is None:
        raise AccountRefused("This link has expired or was already used: ask for a new one.")
    user = db.get(User, row.user_id)
    if user is None or not user.is_active:
        raise AccountRefused("This account is not active.")
    user.password_hash = _new_password(password, again)
    row.used_at = _now()
    _signed_out_everywhere(user)
    log(db, user, "password_reset", user.email)
    return user


def change_password(db: Session, user: User, current: str, password: str, again: str) -> None:
    if user.is_persona:
        raise AccountRefused("Demo personas have no password.")
    if not verify_password(current, user.password_hash):
        raise AccountRefused("The current password is wrong.")
    user.password_hash = _new_password(password, again)
    _signed_out_everywhere(user)
    log(db, user, "password_changed", user.email)


def messages(db: Session) -> list[AccountMessageRow]:
    return list(db.scalars(select(AccountMessageRow).order_by(AccountMessageRow.id.desc())))


# ---------------------------------------------------------------- MFA


def mfa_of(db: Session, user: User) -> UserMfaRow | None:
    return db.get(UserMfaRow, user.id) if not getattr(user, "is_service", False) else None


def mfa_required(db: Session, user: User) -> bool:
    """Supervisors and quality managers must use MFA; personas and the API token never."""
    if user.is_persona or getattr(user, "is_service", False) or not user.is_staff:
        return False
    return user.lab_role == LAB_ROLE_SUPERVISOR or capabilities.QUALITY_MANAGER in capabilities.granted(
        db, user
    )


@dataclass(frozen=True)
class Enrolment:
    secret: str
    uri: str


def start(user: User) -> Enrolment:
    secret = totp.new_secret()
    return Enrolment(secret, totp.otpauth_uri(secret, user.email, ISSUER))


def qr_svg(uri: str) -> str:
    import segno

    return segno.make(uri, error="m").svg_inline(scale=4, dark="#111", light="#fff")


def confirm(db: Session, user: User, secret: str, code: str, secret_key: str, now: float) -> list[str]:
    """Turns MFA on once a code from the app matches; returns the recovery codes (shown once)."""
    if user.is_persona:
        raise AccountRefused("Demo personas don't use MFA.")
    if mfa_of(db, user) is not None:
        raise AccountRefused("MFA is already on for this account.")
    step = totp.verify(secret, code, now, None)
    if step is None:
        raise AccountRefused("That code doesn't match: check the phone's time, and type the code shown now.")
    db.add(UserMfaRow(user_id=user.id, secret_sealed=totp.seal(secret_key, secret), last_step=step))
    codes = totp.recovery_codes()
    for c in codes:
        db.add(MfaRecoveryCodeRow(user_id=user.id, code_hash=hash_password(c)))
    log(db, user, "mfa_on", user.email)
    return codes


def check_code(db: Session, user: User, code: str, secret_key: str, now: float) -> bool:
    """A login's second step: an authenticator code (never one already used), or an unused recovery code."""
    mfa = mfa_of(db, user)
    if mfa is None:
        return False
    try:
        secret = totp.unseal(secret_key, mfa.secret_sealed)
    except ValueError:  # sealed under another SECRET_KEY: only a recovery code (or an MFA reset) helps (A1)
        secret = None
    step = totp.verify(secret, code, now, mfa.last_step) if secret is not None else None
    if step is not None:
        mfa.last_step = step
        db.flush()
        return True
    typed = totp.normalise_recovery(code)
    if len(typed) != 9:
        return False
    for row in db.scalars(
        select(MfaRecoveryCodeRow).where(
            MfaRecoveryCodeRow.user_id == user.id, MfaRecoveryCodeRow.used_at.is_(None)
        )
    ):
        if verify_password(typed, row.code_hash):
            row.used_at = _now()
            log(db, user, "recovery_code_used", user.email)
            return True
    return False


def recovery_left(db: Session, user: User) -> int:
    return len(
        db.scalars(
            select(MfaRecoveryCodeRow.id).where(
                MfaRecoveryCodeRow.user_id == user.id, MfaRecoveryCodeRow.used_at.is_(None)
            )
        ).all()
    )


def _remove(db: Session, user: User) -> None:
    db.execute(delete(MfaRecoveryCodeRow).where(MfaRecoveryCodeRow.user_id == user.id))
    db.execute(delete(UserMfaRow).where(UserMfaRow.user_id == user.id))


def turn_off(db: Session, user: User, current: str) -> None:
    """A user turns their own MFA off (their password again); refused when their role requires it."""
    if mfa_required(db, user):
        raise AccountRefused("Your role requires MFA: it can't be turned off.")
    if not verify_password(current, user.password_hash):
        raise AccountRefused("The password is wrong.")
    _remove(db, user)
    log(db, user, "mfa_off", user.email)


def reset_mfa(db: Session, target: User, by: User, reason: str) -> None:
    """Someone who manages users resets a user's MFA (a lost phone): with a reason; signs them out."""
    reason = " ".join(reason.split())
    if not reason:
        raise AccountRefused("Say why the MFA is reset.")
    if capabilities.outranks(target, by):
        raise AccountRefused("Nobody acts on someone with a higher lab role.")
    if mfa_of(db, target) is None:
        raise AccountRefused(f"{target.email} has no MFA to reset.")
    _remove(db, target)
    _signed_out_everywhere(target)
    log(db, target, "mfa_reset", by.email, reason[:2000])
