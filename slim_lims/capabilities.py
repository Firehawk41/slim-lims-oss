"""What a staff member may do beyond the results workflow (D-100): capabilities, as lab data.

Each lab role has default capabilities; a supervisor may grant one to a particular person (an analyst
who is also the lab's LIMS specialist manages users). Two rules hold whatever is granted:
  - nobody acts on someone with a higher lab role, or gives anyone a role above their own;
  - demo personas never manage users (the public demo; see D-74).
Only someone who holds a capability by role default may grant it to others.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from slim_lims.models import LAB_ROLE_SUPERVISOR, User, UserCapability

MANAGE_USERS = "manage_users"
CAPABILITIES = {MANAGE_USERS: "Manage users: add users, deactivate them, set lab roles"}
ROLE_DEFAULTS: dict[int, frozenset[str]] = {LAB_ROLE_SUPERVISOR: frozenset({MANAGE_USERS})}


def granted(db: Session, user: User) -> frozenset[str]:
    rows = db.scalars(select(UserCapability.capability).where(UserCapability.user_id == user.id))
    return frozenset(rows)


def effective(db: Session, user: User) -> frozenset[str]:
    """Everything `user` may do here: their role's defaults and their grants."""
    if not user.is_staff or user.is_persona or getattr(user, "is_service", False):
        return frozenset()
    return ROLE_DEFAULTS.get(user.lab_role, frozenset()) | granted(db, user)


def can(db: Session, user: User, capability: str) -> bool:
    return capability in effective(db, user)


def may_grant(user: User, capability: str) -> bool:
    return not user.is_persona and capability in ROLE_DEFAULTS.get(user.lab_role, frozenset())


def outranks(target: User, user: User) -> bool:
    """`target` has a higher lab role than `user` (customers have none)."""
    return target.is_staff and (target.lab_role or 0) > (user.lab_role or 0)
