"""Demo personas (slim-data D-50, D-74): a second sign-in method for the public sandbox.

A persona is a real user row flagged `is_persona`, so everything else (four-eyes on the user ID,
analyst initials in result files, customers seeing only their own requests) works unchanged. A
visitor signs in by choosing one ("Continue as..."), only when DEMO_PERSONAS is on; slim-data
records their actions as ASSERTED, never AUTHENTICATED. Personas have no usable password, can't sign
in through /login, and can't manage users or lab roles. The list is data (data/demo_personas.csv).
"""

import csv
import secrets
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from slim_lab_portal.models import ROLE_STAFF, User
from slim_lab_portal.security import hash_password

PERSONAS_CSV = Path(__file__).resolve().parent / "data" / "demo_personas.csv"


@dataclass(frozen=True)
class Persona:
    email: str
    label: str
    role: str
    lab_role: int | None
    initials: str | None
    customer_id: int | None


def load_personas(path: Path = PERSONAS_CSV) -> list[Persona]:
    with path.open(newline="", encoding="utf-8") as f:
        return [
            Persona(
                email=row["email"].strip().lower(),
                label=row["label"].strip(),
                role=row["role"].strip(),
                lab_role=int(row["lab_role"]) if row["lab_role"].strip() else None,
                initials=row["initials"].strip().upper() or None,
                customer_id=int(row["customer_id"]) if row["customer_id"].strip() else None,
            )
            for row in csv.DictReader(f)
        ]


def sync_personas(session: Session, personas: list[Persona] | None = None) -> int:
    """Creates or updates the persona rows (by email); the caller commits. Returns how many."""
    personas = load_personas() if personas is None else personas
    for p in personas:
        user = session.scalars(select(User).where(User.email == p.email)).first()
        if user is None:
            # A random password nobody knows; /login refuses personas anyway.
            user = User(email=p.email, password_hash=hash_password(secrets.token_urlsafe(32)), role=p.role)
            session.add(user)
        elif not user.is_persona:
            raise ValueError(f"{p.email} is a real user; a persona can't take it over")
        user.role, user.is_persona, user.is_active = p.role, True, True
        user.lab_role = p.lab_role if p.role == ROLE_STAFF else None
        user.initials = p.initials if p.role == ROLE_STAFF else None
        user.customer_id = None if p.role == ROLE_STAFF else p.customer_id
    session.flush()
    return len(personas)


def persona_choices(session: Session) -> list[tuple[User, str]]:
    """The personas a visitor may continue as, with their labels, in the data file's order."""
    users = {u.email: u for u in session.scalars(select(User).where(User.is_persona.is_(True), User.is_active.is_(True)))}
    return [(users[p.email], p.label) for p in load_personas() if p.email in users]
