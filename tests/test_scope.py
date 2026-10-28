"""Access scoping fails closed (review A6): every listing needs a Scope, and only staff get "everyone"."""

import inspect
from types import SimpleNamespace

import pytest

from slim_lims.repositories import TRSubmissionRepository
from slim_lims.scope import Scope, ScopeError
from tests.conftest import login, login_api


def test_a_customer_gets_their_own_whatever_they_ask_and_staff_may_narrow():
    customer = SimpleNamespace(id=1, is_staff=False, customer_id=2)
    assert Scope.for_user(customer, customer_id=7) == Scope(2)
    staff = SimpleNamespace(id=2, is_staff=True, customer_id=None)
    assert Scope.for_user(staff) == Scope(None) and Scope.for_user(staff, 7) == Scope(7)
    assert Scope(None).allows(5) and Scope(2).allows(2) and not Scope(2).allows(5)


def test_a_customer_without_a_customer_sees_nothing_rather_than_everything():
    with pytest.raises(ScopeError):
        Scope.for_user(SimpleNamespace(id=3, is_staff=False, customer_id=None))


def test_listing_requires_a_scope():
    scope = inspect.signature(TRSubmissionRepository.list).parameters["scope"]
    assert scope.default is inspect.Parameter.empty


@pytest.mark.parametrize("path", ["/", "/requests", "/api/submissions"])
def test_every_listing_a_customer_reaches_is_scoped_to_them(client, customer_user, monkeypatch, path):
    seen = []
    listed = TRSubmissionRepository.list

    def spy(self, scope, **filters):
        seen.append(scope)
        return listed(self, scope, **filters)

    monkeypatch.setattr(TRSubmissionRepository, "list", spy)
    (login_api if path.startswith("/api") else login)(client, customer_user.email)
    assert client.get(path, params={"customer": 99}).status_code == 200
    assert seen and set(seen) == {Scope(customer_user.customer_id)}
