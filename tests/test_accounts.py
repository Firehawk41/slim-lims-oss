"""Accounts (slim-data D-142): TOTP against RFC 6238's test values, the secret box, MFA at login, reset
links, changing a password, resetting MFA, and who must use MFA."""

import re
import time
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update
from sqlalchemy.exc import DBAPIError

from slim_lims import accounts, totp
from slim_lims.main import create_app
from slim_lims.models import (
    LAB_ROLE_ANALYST,
    ROLE_STAFF,
    AccountEventRow,
    PasswordResetRow,
    User,
    UserCapability,
)
from slim_lims.security import hash_password
from tests.conftest import PASSWORD, csrf_from, login, make_settings

NEW_PASSWORD = "a much longer new password"


# ---------------------------------------------------------------- TOTP and the secret box


@pytest.mark.parametrize(
    ("unix_time", "expected"),
    [(59, "94287082"), (1111111109, "07081804"), (1234567890, "89005924"), (2000000000, "69279037")],
)
def test_hotp_matches_rfc_6238s_sha1_test_values(unix_time, expected):
    assert totp.hotp(b"12345678901234567890", totp.step_at(unix_time), digits=8) == expected


def test_a_code_is_good_for_one_step_either_side_and_never_twice():
    secret, now = totp.new_secret(), 1_700_000_000
    code = totp.code_at(secret, now)
    step = totp.verify(secret, code, now, None)
    assert step == totp.step_at(now) and totp.verify(secret, code, now + 30, None) == step
    assert totp.verify(secret, code, now + 90, None) is None  # too late
    assert totp.verify(secret, code, now, step) is None  # already used
    assert (
        totp.verify(secret, "12 34", now, None) is None and totp.verify(secret, "abcdef", now, None) is None
    )
    assert totp.otpauth_uri(secret, "sue@lab.example", "SlimLIMS").startswith("otpauth://totp/SlimLIMS%3Asue")


def test_the_secret_box_opens_only_with_its_key_and_untouched():
    sealed = totp.seal("key one", "JBSWY3DPEHPK3PXP")
    assert "JBSWY3DP" not in sealed and totp.unseal("key one", sealed) == "JBSWY3DPEHPK3PXP"
    with pytest.raises(ValueError):
        totp.unseal("key two", sealed)
    tampered = sealed[:-4] + ("AAAA" if not sealed.endswith("AAAA") else "BBBB")
    with pytest.raises(ValueError):
        totp.unseal("key one", tampered)
    codes = totp.recovery_codes()
    assert len(codes) == len(set(codes)) == 10 and all(
        re.fullmatch(r"[A-Z2-9]{4}-[A-Z2-9]{4}", c) for c in codes
    )
    assert totp.normalise_recovery(" 7kq4m2xp ") == "7KQ4-M2XP"


# ---------------------------------------------------------------- at the door


@pytest.fixture
def mfa_app(engine, reference):
    return create_app(make_settings(str(engine.url), require_mfa=True), engine=engine, reference=reference)


def set_up_mfa(client) -> tuple[str, list[str]]:
    """Through the pages: start, scan (read the key), confirm. Returns the secret and recovery codes."""
    page = client.get("/account/mfa")
    started = client.post("/account/mfa/start", data={"csrf_token": csrf_from(page.text)})
    secret = re.search(r"<code>([A-Z2-7]+)</code>", started.text).group(1)
    assert "<svg" in started.text  # the QR code, inline
    csrf = csrf_from(started.text)
    wrong = client.post("/account/mfa/confirm", data={"code": "000000", "csrf_token": csrf})
    assert wrong.status_code == 422 and secret in wrong.text  # the same key stays on screen
    done = client.post(
        "/account/mfa/confirm", data={"code": totp.code_at(secret, time.time()), "csrf_token": csrf}
    )
    codes = re.findall(r"<code>([A-Z2-9]{4}-[A-Z2-9]{4})</code>", done.text)
    assert done.status_code == 200 and len(codes) == 10
    return secret, codes


def sign_out(client) -> None:
    client.post("/logout", data={"csrf_token": csrf_from(client.get("/account").text)})


def test_a_supervisor_must_set_up_mfa_and_then_signs_in_with_a_code(mfa_app, db, staff_user):
    with TestClient(mfa_app) as client:
        assert login(client, "staff@lab.example").status_code == 303
        held = client.get("/", follow_redirects=False)
        assert held.status_code == 303 and held.headers["location"] == "/account/mfa"  # nothing else first
        assert client.get("/api/me").status_code == 403
        secret, codes = set_up_mfa(client)
        assert client.get("/", follow_redirects=False).status_code == 200  # set up: through
        sign_out(client)

        assert login(client, "staff@lab.example").headers["location"] == "/login/code"
        assert client.get("/", follow_redirects=False).status_code == 303  # password alone isn't signed in
        csrf = csrf_from(client.get("/login/code").text)
        assert client.post("/login/code", data={"code": "000000", "csrf_token": csrf}).status_code == 401
        code = totp.code_at(
            secret, time.time() + 30
        )  # the setup used this step's code; a code never counts twice
        assert (
            client.post(
                "/login/code", data={"code": code, "csrf_token": csrf}, follow_redirects=False
            ).status_code
            == 303
        )
        assert client.get("/", follow_redirects=False).status_code == 200
        sign_out(client)

        login(client, "staff@lab.example")
        csrf = csrf_from(client.get("/login/code").text)
        assert client.post("/login/code", data={"code": code, "csrf_token": csrf}).status_code == 401  # used
        assert (
            client.post(
                "/login/code", data={"code": codes[0].lower(), "csrf_token": csrf}, follow_redirects=False
            ).status_code
            == 303
        )
        sign_out(client)
        login(client, "staff@lab.example")
        csrf = csrf_from(client.get("/login/code").text)
        assert (
            client.post("/login/code", data={"code": codes[0], "csrf_token": csrf}).status_code == 401
        )  # once
    db.expire_all()
    assert accounts.recovery_left(db, staff_user) == 9
    kinds = [e.kind for e in accounts.events(db, staff_user)]
    assert kinds == ["mfa_on", "recovery_code_used"]


def test_wrong_codes_count_toward_the_lockout(mfa_app, db, staff_user):
    with TestClient(mfa_app) as client:
        login(client, "staff@lab.example")
        set_up_mfa(client)
        sign_out(client)
        login(client, "staff@lab.example")
        csrf = csrf_from(client.get("/login/code").text)
        for _ in range(5):
            client.post("/login/code", data={"code": "000000", "csrf_token": csrf})
        assert client.post("/login/code", data={"code": "000000", "csrf_token": csrf}).status_code == 429


def test_who_must_use_mfa(db, staff_user):
    analyst = User(
        email="a@lab.example", password_hash="x", role=ROLE_STAFF, initials="AA", lab_role=LAB_ROLE_ANALYST
    )
    db.add(analyst)
    db.flush()
    assert accounts.mfa_required(db, staff_user) and not accounts.mfa_required(db, analyst)
    db.add(UserCapability(user_id=analyst.id, capability="quality_manager", granted_by=staff_user.id))
    db.flush()
    assert accounts.mfa_required(db, analyst)
    analyst.is_persona = True
    assert not accounts.mfa_required(db, analyst)  # personas are chosen, not signed in


def test_without_require_mfa_it_is_optional_and_a_user_turns_it_off(client, db, staff_user):
    login(client, "staff@lab.example")
    assert client.get("/", follow_redirects=False).status_code == 200  # REQUIRE_MFA off (tests' default)
    staff_user.lab_role = LAB_ROLE_ANALYST  # an analyst: MFA optional
    db.commit()
    set_up_mfa(client)
    csrf = csrf_from(client.get("/account").text)
    client.post("/account/mfa/off", data={"current": "wrong", "csrf_token": csrf})
    db.expire_all()
    assert accounts.mfa_of(db, staff_user) is not None
    db.rollback()
    client.post("/account/mfa/off", data={"current": PASSWORD, "csrf_token": csrf})
    db.expire_all()
    assert accounts.mfa_of(db, staff_user) is None


# ---------------------------------------------------------------- reset links and passwords


def test_a_reset_link_sets_a_new_password_once_and_signs_the_user_out(client, db, staff_user, customer_user):
    with TestClient(client.app) as theirs:  # the customer's own session, signed in before the reset
        login(theirs, "jane@acme.example")
        assert theirs.get("/", follow_redirects=False).status_code == 200

        login(client, "staff@lab.example")
        csrf = csrf_from(client.get("/admin/users").text)
        made = client.post(
            f"/admin/users/{customer_user.id}/reset-link", data={"csrf_token": csrf}, follow_redirects=True
        )
        url = re.search(r"(http://testserver/reset/[\w-]+)", made.text).group(1)
        token = url.rsplit("/", 1)[1]
        assert f"/reset/{token}" in client.get("/outbox").text  # kept as a message, not transmitted
        assert token not in db.scalars(select(PasswordResetRow.token_sha256)).one()
        db.rollback()

        with TestClient(client.app) as fresh:
            form = fresh.get(f"/reset/{token}")
            assert form.status_code == 200
            csrf = csrf_from(form.text)
            short = fresh.post(f"/reset/{token}", data={"new": "short", "again": "short", "csrf_token": csrf})
            assert short.status_code == 422 and "at least 12" in short.text
            differ = fresh.post(
                f"/reset/{token}", data={"new": NEW_PASSWORD, "again": "x" * 20, "csrf_token": csrf}
            )
            assert differ.status_code == 422
            done = fresh.post(
                f"/reset/{token}",
                data={"new": NEW_PASSWORD, "again": NEW_PASSWORD, "csrf_token": csrf},
                follow_redirects=False,
            )
            assert done.status_code == 303
            assert fresh.get(f"/reset/{token}").status_code == 404  # once
            assert login(fresh, "jane@acme.example", NEW_PASSWORD).status_code == 303

        assert theirs.get("/", follow_redirects=False).status_code == 303  # the old session: signed out
    db.expire_all()
    assert [e.kind for e in accounts.events(db, customer_user)] == ["reset_link_made", "password_reset"]


def test_an_expired_or_unknown_link_opens_nothing(client, db, staff_user, customer_user):
    url = accounts.make_reset(db, customer_user, staff_user, lambda t: f"/reset/{t}")
    db.execute(update(PasswordResetRow).values(expires_at=datetime.now(UTC) - timedelta(minutes=1)))
    db.commit()
    assert client.get(url).status_code == 404 and client.get("/reset/nope").status_code == 404


def test_a_user_changes_their_own_password_and_other_sessions_end(client, db, customer_user):
    with TestClient(client.app) as other:
        login(other, "jane@acme.example")
        login(client, "jane@acme.example")
        csrf = csrf_from(client.get("/account").text)
        wrong = {"current": "nope", "new": NEW_PASSWORD, "again": NEW_PASSWORD, "csrf_token": csrf}
        assert (
            "current password is wrong"
            in client.post("/account/password", data=wrong, follow_redirects=True).text
        )
        right = {**wrong, "current": PASSWORD}
        assert "Password changed" in client.post("/account/password", data=right, follow_redirects=True).text
        assert client.get("/", follow_redirects=False).status_code == 200  # this session stays
        assert other.get("/", follow_redirects=False).status_code == 303  # that one is signed out


def test_whoever_manages_users_resets_mfa_with_a_reason(client, db, staff_user, customer_user):
    login(client, "jane@acme.example")
    set_up_mfa(client)
    with TestClient(client.app) as admin:
        login(admin, "staff@lab.example")
        csrf = csrf_from(admin.get("/admin/users").text)
        page = admin.post(
            f"/admin/users/{customer_user.id}/reset-mfa",
            data={"reason": " ", "csrf_token": csrf},
            follow_redirects=True,
        )
        assert "Say why" in page.text
        reason = {"reason": "lost phone", "csrf_token": csrf}
        assert (
            "MFA reset"
            in admin.post(
                f"/admin/users/{customer_user.id}/reset-mfa", data=reason, follow_redirects=True
            ).text
        )
    assert client.get("/", follow_redirects=False).status_code == 303  # signed out
    db.expire_all()
    assert accounts.mfa_of(db, customer_user) is None
    assert accounts.events(db, customer_user)[-1].reason == "lost phone"


def test_personas_have_no_password_to_reset_and_the_log_is_append_only(engine, db, staff_user):
    persona = User(
        email="p@demo.example",
        password_hash=hash_password(PASSWORD),
        role=ROLE_STAFF,
        initials="PP",
        lab_role=LAB_ROLE_ANALYST,
        is_persona=True,
    )
    db.add(persona)
    db.flush()
    with pytest.raises(accounts.AccountRefused):
        accounts.make_reset(db, persona, staff_user, str)
    accounts.log(db, staff_user, "mfa_on", staff_user.email)
    db.commit()
    with pytest.raises(DBAPIError), engine.begin() as connection:
        connection.execute(update(AccountEventRow).values(reason="x"))
