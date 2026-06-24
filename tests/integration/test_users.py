"""Integration tests for the /workspaces/{id}/users routes.

Covers the @test comments in api/src/users/routes.py:
- listing members requires contributor-or-above (403 otherwise)
- assign/remove role require workspace lead (403 otherwise)
- workspace-missing -> 404, user-missing -> 404
- unexpected errors -> 500
- role can be set to lead/validator and unset back to contributor
- POC / contributor cannot be assigned directly (422)
- the affected user's cache is evicted after a change
"""

from uuid import UUID

import pytest

import api.src.users.routes as users_routes
from api.src.users.schemas import WorkspaceUserRoleType
from tests.support import factories, fakes

TARGET_USER = "33333333-3333-3333-3333-333333333333"


def url(workspace_id=1):
    return f"/api/v1/workspaces/{workspace_id}/users"


@pytest.fixture
def lead():
    return factories.make_user_info(
        osm_workspace_roles={1: [WorkspaceUserRoleType.LEAD]}
    )


@pytest.fixture
def evictions(monkeypatch):
    """Record evict_user_from_cache(...) calls made by the routes."""
    calls = []
    monkeypatch.setattr(users_routes, "evict_user_from_cache", calls.append)
    return calls


# --- GET members -----------------------------------------------------------


async def test_list_members_requires_membership(client, login):
    login(factories.make_user_info(accessible_workspace_ids={}))
    response = await client.get(url())
    assert response.status_code == 403


async def test_list_members_returns_privileged_users(client, login, osm_session):
    login(factories.make_user_info(accessible_workspace_ids={"pg": [1]}))
    user = factories.make_user(id=5, auth_uid=TARGET_USER, display_name="Lead Lou")
    # repo joins User + role -> rows of (user, role) tuples
    osm_session.queue(fakes.rows((user, "lead")))

    response = await client.get(url())

    assert response.status_code == 200
    body = response.json()
    assert body == [
        {"id": 5, "auth_uid": TARGET_USER, "display_name": "Lead Lou", "role": "lead"}
    ]


async def test_list_members_unexpected_error_returns_500(
    error_client, login, osm_session
):
    login(factories.make_user_info(accessible_workspace_ids={"pg": [1]}))
    osm_session.queue(fakes.raises(RuntimeError("db exploded")))

    response = await error_client.get(url())
    assert response.status_code == 500


# --- PUT assign role -------------------------------------------------------


async def test_assign_role_requires_lead(client, login):
    login(factories.make_user_info(accessible_workspace_ids={"pg": [1]}))
    response = await client.put(f"{url()}/{TARGET_USER}/role", json={"role": "lead"})
    assert response.status_code == 403


async def test_assign_role_workspace_missing_returns_404(
    client, login, lead, task_session
):
    login(lead)
    task_session.queue(fakes.empty())  # getById finds nothing
    response = await client.put(f"{url()}/{TARGET_USER}/role", json={"role": "lead"})
    assert response.status_code == 404


async def test_assign_role_user_missing_returns_404(
    client, login, lead, task_session, osm_session
):
    login(lead)
    task_session.queue(fakes.rows(factories.make_workspace(id=1)))
    osm_session.queue(fakes.scalar(None))  # user has never signed in
    response = await client.put(f"{url()}/{TARGET_USER}/role", json={"role": "lead"})
    assert response.status_code == 404


async def test_assign_lead_role_succeeds_and_evicts(
    client, login, lead, task_session, osm_session, evictions
):
    login(lead)
    task_session.queue(fakes.rows(factories.make_workspace(id=1)))
    osm_session.queue(fakes.scalar(1))  # user exists

    response = await client.put(f"{url()}/{TARGET_USER}/role", json={"role": "lead"})

    assert response.status_code == 204
    assert osm_session.commits == 1
    assert evictions == [UUID(TARGET_USER)]


async def test_assign_validator_role_succeeds(
    client, login, lead, task_session, osm_session, evictions
):
    login(lead)
    task_session.queue(fakes.rows(factories.make_workspace(id=1)))
    osm_session.queue(fakes.scalar(1))

    response = await client.put(
        f"{url()}/{TARGET_USER}/role", json={"role": "validator"}
    )
    assert response.status_code == 204


async def test_assign_contributor_role_rejected(client, login, lead):
    login(lead)
    # CONTRIBUTOR is implicit and cannot be assigned directly -> 422.
    response = await client.put(
        f"{url()}/{TARGET_USER}/role", json={"role": "contributor"}
    )
    assert response.status_code == 422


async def test_assign_poc_role_rejected(client, login, lead):
    login(lead)
    # "poc" is a TDEI role, not a workspace role -> validation error.
    response = await client.put(f"{url()}/{TARGET_USER}/role", json={"role": "poc"})
    assert response.status_code == 422


# --- DELETE remove role ----------------------------------------------------


async def test_remove_role_requires_lead(client, login):
    login(factories.make_user_info(accessible_workspace_ids={"pg": [1]}))
    response = await client.delete(f"{url()}/{TARGET_USER}")
    assert response.status_code == 403


async def test_remove_role_unsets_to_contributor_and_evicts(
    client, login, lead, task_session, osm_session, evictions
):
    login(lead)
    task_session.queue(fakes.rows(factories.make_workspace(id=1)))
    osm_session.queue(fakes.affected(1))  # one role row removed

    response = await client.delete(f"{url()}/{TARGET_USER}")

    assert response.status_code == 204
    assert evictions == [UUID(TARGET_USER)]


async def test_remove_role_when_not_assigned_returns_404(
    client, login, lead, task_session, osm_session
):
    login(lead)
    task_session.queue(fakes.rows(factories.make_workspace(id=1)))
    osm_session.queue(fakes.affected(0))  # nothing to delete
    response = await client.delete(f"{url()}/{TARGET_USER}")
    assert response.status_code == 404


async def test_remove_role_workspace_missing_returns_404(
    client, login, lead, task_session
):
    login(lead)
    task_session.queue(fakes.empty())
    response = await client.delete(f"{url()}/{TARGET_USER}")
    assert response.status_code == 404
