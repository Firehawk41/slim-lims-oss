"""What a staff member may do beyond the results workflow (D-100): capabilities, as lab data.

Each lab role has default capabilities; a supervisor may grant one to a particular person (an analyst
who is also the lab's LIMS specialist manages users). Two rules hold whatever is granted:
  - nobody acts on someone with a higher lab role, or gives anyone a role above their own;
  - demo personas never manage users (the public demo; see D-74).
Only someone who holds a capability by role default may grant it to others; the quality roles
(slim-quality, D-109) belong to no lab role, so whoever may manage users grants them.
A demo persona holds only what its row in data/demo_personas.csv gives it, never managing users.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from slim_lims.models import LAB_ROLE_SUPERVISOR, User, UserCapability

MANAGE_USERS = "manage_users"
QUALITY = "quality"
QUALITY_MANAGER = "quality_manager"
CAPABILITIES = {
    MANAGE_USERS: "Manage users: add users, deactivate them, set lab roles",
    QUALITY: "Quality: see the QC charts and tools, record events and notes, propose changes",
    QUALITY_MANAGER: "Quality manager: also approve changes to the quality rules and sign QC reviews",
}
ROLE_DEFAULTS: dict[int, frozenset[str]] = {LAB_ROLE_SUPERVISOR: frozenset({MANAGE_USERS})}
GRANTED_BY_USER_MANAGERS = frozenset({QUALITY, QUALITY_MANAGER})
PERSONA_GRANTABLE = frozenset({QUALITY, QUALITY_MANAGER})


def granted(db: Session, user: User) -> frozenset[str]:
    rows = db.scalars(select(UserCapability.capability).where(UserCapability.user_id == user.id))
    return frozenset(rows)


def effective(db: Session, user: User) -> frozenset[str]:
    """Everything `user` may do here: their role's defaults and their grants."""
    if not user.is_staff or getattr(user, "is_service", False):
        return frozenset()
    if user.is_persona:
        return granted(db, user) & PERSONA_GRANTABLE
    return ROLE_DEFAULTS.get(user.lab_role, frozenset()) | granted(db, user)


def can(db: Session, user: User, capability: str) -> bool:
    return capability in effective(db, user)


def may_grant(user: User, capability: str) -> bool:
    if user.is_persona:
        return False
    own = ROLE_DEFAULTS.get(user.lab_role, frozenset())
    return capability in own or (capability in GRANTED_BY_USER_MANAGERS and MANAGE_USERS in own)


def outranks(target: User, user: User) -> bool:
    """`target` has a higher lab role than `user` (customers have none)."""
    return target.is_staff and (target.lab_role or 0) > (user.lab_role or 0)
