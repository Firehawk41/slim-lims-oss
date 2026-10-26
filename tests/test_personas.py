"""Demo personas (slim-data D-50, D-74): chosen, recorded as ASSERTED, no user admin."""

import pytest
from fastapi.testclient import TestClient
from slim_data.domain.enums import IdentitySource, Role
from sqlalchemy import select

from slim_lims.main import create_app
from slim_lims.models import ROLE_CUSTOMER, User
from slim_lims.personas import Persona, load_personas, persona_choices, sync_personas
from slim_lims.results import actor_for
from slim_lims.security import hash_password
from tests.conftest import PASSWORD, csrf_from, login, make_settings


@pytest.fixture
def personas(db):
    sync_personas(db)
    db.commit()
    return {u.email.split("@")[0]: u for u in db.scalars(select(User).where(User.is_persona.is_(True)))}


@pytest.fixture
def demo_client(engine, reference):
    app = create_app(make_settings(str(engine.url), demo_personas=True), engine=engine, reference=reference)
    with TestClient(app) as client:
        yield client


def continue_as(client, user):
    token = csrf_from(client.get("/demo/personas").text)
    return client.post(
        "/demo/continue-as", data={"csrf_token": token, "user_id": str(user.id)}, follow_redirects=False
    )


def test_the_data_file_defines_the_personas(personas):
    assert {p.role for p in load_personas()} == {"customer", "staff"}
    assert personas["ana"].initials == "ANA" and personas["sue"].lab_role == Role.SUPERVISOR
    assert personas["customer"].customer_id == 1 and personas["customer"].initials is None


def test_sync_is_idempotent_and_never_takes_over_a_real_user(db, personas, customer_user):
    assert sync_personas(db) == len(load_personas())
    clash = Persona(
        email="jane@acme.example", label="x", role=ROLE_CUSTOMER, lab_role=None, initials=None, customer_id=1
    )
    with pytest.raises(ValueError, match="real user"):
        sync_personas(db, [clash])


def test_continue_as_signs_in_and_actions_are_asserted(demo_client, personas):
    response = continue_as(demo_client, personas["rob"])
    assert response.status_code == 303
    home = demo_client.get("/admin")
    assert "you are acting as" in home.text and "ROB" in home.text and "Switch persona" in home.text
    actor = actor_for(personas["rob"])
    assert actor.identity_source == IdentitySource.ASSERTED and actor.roles == {Role.REVIEWER}


def test_personas_are_off_unless_configured(client, personas):
    assert client.get("/demo/personas").status_code == 404
    token = csrf_from(client.get("/login").text)
    assert (
        client.post(
            "/demo/continue-as", data={"csrf_token": token, "user_id": str(personas["rob"].id)}
        ).status_code
        == 404
    )
    assert "Continue as a demo persona" not in client.get("/login").text


def test_personas_cannot_use_the_password_login(demo_client, personas, db):
    db.get(User, personas["sue"].id).password_hash = hash_password(PASSWORD)  # even with a known password
    db.commit()
    assert login(demo_client, "sue@demo.example").status_code == 401


def test_only_personas_can_be_continued_as(demo_client, personas, staff_user):
    assert continue_as(demo_client, staff_user).status_code == 404
    token = csrf_from(demo_client.get("/demo/personas").text)
    assert (
        demo_client.post("/demo/continue-as", data={"csrf_token": token, "user_id": "nope"}).status_code
        == 404
    )


def test_personas_cannot_manage_users_or_roles(demo_client, personas, staff_user):
    continue_as(demo_client, personas["sue"])  # a supervisor persona
    token = csrf_from(demo_client.get("/admin/users").text)
    created = demo_client.post(
        "/admin/users",
        data={
            "csrf_token": token,
            "email": "x@lab.example",
            "role": "staff",
            "password": "p" * 12,
            "initials": "XX",
        },
    )
    assert "Demo personas can&#39;t manage users" in created.text
    assert (
        "can&#39;t change lab roles"
        in demo_client.post(
            f"/admin/users/{personas['ana'].id}/lab-role", data={"csrf_token": token, "lab_role": 3}
        ).text
    )
    assert (
        "can&#39;t manage users"
        in demo_client.post(f"/admin/users/{staff_user.id}/active", data={"csrf_token": token}).text
    )


def test_persona_choices_follow_the_file_order(db, personas):
    assert [label for _, label in persona_choices(db)] == [p.label for p in load_personas()]
