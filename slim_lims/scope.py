"""Whose requests a listing may show (review A6): fail closed. A customer always sees only their own
customer's; "every customer" is a scope only staff (and the slim-lims token) get. Every listing takes a
Scope, with no default, so a path that forgets to scope doesn't run, and one that loses a customer's
ID raises instead of showing everything."""

from dataclasses import dataclass
from typing import Any


class ScopeError(Exception):
    """A customer user without a customer: refused rather than shown everything."""


@dataclass(frozen=True)
class Scope:
    customer_id: int | None  # None: every customer (staff only, via for_user)

    @classmethod
    def for_user(cls, user: Any, customer_id: int | None = None) -> "Scope":
        """Staff may narrow to one customer; a customer gets their own, whatever they asked for."""
        if user.is_staff:
            return cls(customer_id)
        if user.customer_id is None:
            raise ScopeError(f"user {user.id} is a customer user without a customer")
        return cls(user.customer_id)

    def allows(self, customer_id: int) -> bool:
        return self.customer_id is None or self.customer_id == customer_id
