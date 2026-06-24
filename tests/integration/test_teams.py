"""Integration tests for the /workspaces/{id}/teams routes.

Covers the @test comments in api/src/teams/routes.py across all eight
endpoints. Team routes touch both DBs: the access guard / workspace lookup
hits the task DB (``workspace_repo.getById``) and team/user operations hit
the OSM DB. Queue results on the matching fake session in call order.

Note on access errors: for endpoints with no explicit lead check, access is
enforced by ``getById``, which raises 404 (NotFound) when the workspace is
missing or inaccessible -- so "not a member" surfaces as 404, not 403, there.
"""

import pytest

from api.src.users.schemas import WorkspaceUserRoleType
from tests.support import factories, fakes


def base(workspace_id=1):
    return f"/api/v1/workspaces/{workspace_id}/teams"


@pytest.fixture
def lead():
    return factories.make_user_info(
        osm_workspace_roles={1: [WorkspaceUserRoleType.LEAD]}
    )


def member():
    # A non-lead user (default factory grants no lead/validator role).
    return factories.make_user_info()


def ws_ok(task_session):
    """Queue a successful workspace access guard on the task DB."""
    task_session.queue(fakes.rows(factories.make_workspace(id=1)))


# === GET "" list teams =====================================================


async def test_list_teams_returns_items(client, login, task_session, osm_session):
    login(member())
    ws_ok(task_session)
    osm_session.queue(
        fakes.rows(
            factories.make_team(id=1, name="Alpha", users=[factories.make_user()]),
            factories.make_team(id=2, name="Beta", users=[]),
        )
    )

    response = await client.get(base())

    assert response.status_code == 200
    body = response.json()
    assert body[0] == {"id": 1, "name": "Alpha", "member_count": 1}
    assert body[1]["member_count"] == 0


async def test_list_teams_inaccessible_workspace_404(client, login, task_session):
    login(member())
    task_session.queue(fakes.empty())  # getById guard -> NotFound
    response = await client.get(base(42))
    assert response.status_code == 404


async def test_list_teams_unexpected_error_500(
    error_client, login, task_session, osm_session
):
    login(member())
    ws_ok(task_session)
    osm_session.queue(fakes.raises(RuntimeError("boom")))
    response = await error_client.get(base())
    assert response.status_code == 500


# === POST "" create team ===================================================


async def test_create_team_requires_lead(client, login):
    login(factories.make_user_info())
    response = await client.post(base(), json={"name": "New"})
    assert response.status_code == 403


async def test_create_team_as_lead(client, login, lead, task_session, osm_session):
    login(lead)
    ws_ok(task_session)  # getById guard
    # create -> add + commit + refresh assigns the id
    response = await client.post(base(), json={"name": "New Team"})
    assert response.status_code == 201
    assert isinstance(response.json(), int)
    assert osm_session.commits == 1


async def test_create_team_workspace_missing_404(client, login, lead, task_session):
    login(lead)
    task_session.queue(fakes.empty())
    response = await client.post(base(), json={"name": "New"})
    assert response.status_code == 404


async def test_create_team_rejects_blank_name(client, login, lead):
    login(lead)
    response = await client.post(base(), json={"name": ""})
    assert response.status_code == 422


# === GET "/{team_id}" get one team =========================================


async def test_get_team_returns_item(client, login, task_session, osm_session):
    login(member())
    ws_ok(task_session)
    osm_session.queue(
        fakes.scalar(True),  # assert_team_in_workspace
        fakes.rows(factories.make_team(id=3, name="Gamma", users=[])),  # get_item
    )

    response = await client.get(f"{base()}/3")

    assert response.status_code == 200
    assert response.json()["id"] == 3


async def test_get_team_not_in_workspace_404(client, login, task_session, osm_session):
    login(member())
    ws_ok(task_session)
    osm_session.queue(fakes.scalar(False))  # assert_team_in_workspace -> False
    response = await client.get(f"{base()}/999")
    assert response.status_code == 404


async def test_get_team_workspace_missing_404(client, login, task_session):
    login(member())
    task_session.queue(fakes.empty())
    response = await client.get(f"{base()}/3")
    assert response.status_code == 404


# === PUT "/{team_id}" update team ==========================================


async def test_update_team_requires_lead(client, login):
    login(factories.make_user_info())
    response = await client.put(f"{base()}/1", json={"name": "Renamed"})
    assert response.status_code == 403


async def test_update_team_as_lead(client, login, lead, task_session, osm_session):
    login(lead)
    ws_ok(task_session)
    osm_session.queue(
        fakes.scalar(True),  # assert_team_in_workspace
        fakes.rows(factories.make_team(id=1, name="Old")),  # update -> get(id)
    )

    response = await client.put(f"{base()}/1", json={"name": "Renamed"})

    assert response.status_code == 204
    assert osm_session.commits == 1


async def test_update_team_not_in_workspace_404(
    client, login, lead, task_session, osm_session
):
    login(lead)
    ws_ok(task_session)
    osm_session.queue(fakes.scalar(False))
    response = await client.put(f"{base()}/1", json={"name": "Renamed"})
    assert response.status_code == 404


# === DELETE "/{team_id}" delete team =======================================


async def test_delete_team_requires_lead(client, login):
    login(factories.make_user_info())
    response = await client.delete(f"{base()}/1")
    assert response.status_code == 403


async def test_delete_team_as_lead(client, login, lead, task_session, osm_session):
    login(lead)
    ws_ok(task_session)
    osm_session.queue(fakes.scalar(True))  # assert_team_in_workspace; delete follows
    response = await client.delete(f"{base()}/1")
    assert response.status_code == 204
    assert osm_session.commits == 1


async def test_delete_team_not_in_workspace_404(
    client, login, lead, task_session, osm_session
):
    login(lead)
    ws_ok(task_session)
    osm_session.queue(fakes.scalar(False))
    response = await client.delete(f"{base()}/1")
    assert response.status_code == 404


# === GET "/{team_id}/members" list members =================================


async def test_get_members_returns_users(client, login, task_session, osm_session):
    login(member())
    ws_ok(task_session)
    osm_session.queue(
        fakes.scalar(True),  # assert_team_in_workspace
        fakes.rows(factories.make_user(id=1, display_name="Mem")),  # get_members
    )

    response = await client.get(f"{base()}/1/members")

    assert response.status_code == 200
    assert response.json()[0]["display_name"] == "Mem"


async def test_get_members_team_not_in_workspace_404(
    client, login, task_session, osm_session
):
    login(member())
    ws_ok(task_session)
    osm_session.queue(fakes.scalar(False))
    response = await client.get(f"{base()}/1/members")
    assert response.status_code == 404


# === POST "/{team_id}/members" join team ===================================


async def test_join_team_adds_current_user(client, login, task_session, osm_session):
    login(member())
    joining = factories.make_user(id=7, display_name="Joiner")
    joining_again = factories.make_user(id=7, display_name="Joiner")
    joining_again.teams = []  # not yet on the team
    ws_ok(task_session)
    osm_session.queue(
        fakes.scalar(True),  # assert_team_in_workspace
        fakes.rows(joining),  # get_current_user (scalar_one)
        fakes.rows(joining_again),  # add_member: load user w/ teams
        fakes.rows(factories.make_team(id=1, name="T")),  # add_member: get(team)
    )

    response = await client.post(f"{base()}/1/members")

    assert response.status_code == 200
    assert response.json()["id"] == 7
    assert osm_session.commits == 1


async def test_join_team_not_in_workspace_404(client, login, task_session, osm_session):
    login(member())
    ws_ok(task_session)
    osm_session.queue(fakes.scalar(False))
    response = await client.post(f"{base()}/1/members")
    assert response.status_code == 404


# === PUT "/{team_id}/members/{user_id}" add member =========================


async def test_add_member_requires_lead(client, login):
    login(factories.make_user_info())
    response = await client.put(f"{base()}/1/members/5")
    assert response.status_code == 403


async def test_add_member_as_lead(client, login, lead, task_session, osm_session):
    login(lead)
    target = factories.make_user(id=5)
    target.teams = []
    ws_ok(task_session)
    osm_session.queue(
        fakes.scalar(True),  # assert_team_in_workspace
        fakes.rows(target),  # add_member: load user
        fakes.rows(factories.make_team(id=1, name="T")),  # add_member: get(team)
    )

    response = await client.put(f"{base()}/1/members/5")

    assert response.status_code == 204
    assert osm_session.commits == 1


async def test_add_member_missing_user_404(
    client, login, lead, task_session, osm_session
):
    login(lead)
    ws_ok(task_session)
    osm_session.queue(
        fakes.scalar(True),  # assert_team_in_workspace
        fakes.empty(),  # add_member: user not found
    )
    response = await client.put(f"{base()}/1/members/5")
    assert response.status_code == 404


async def test_add_member_team_not_in_workspace_404(
    client, login, lead, task_session, osm_session
):
    login(lead)
    ws_ok(task_session)
    osm_session.queue(fakes.scalar(False))
    response = await client.put(f"{base()}/1/members/5")
    assert response.status_code == 404


# === DELETE "/{team_id}/members/{user_id}" remove member ===================


async def test_remove_member_requires_lead(client, login):
    login(factories.make_user_info())
    response = await client.delete(f"{base()}/1/members/5")
    assert response.status_code == 403


async def test_remove_member_as_lead(client, login, lead, task_session, osm_session):
    login(lead)
    team = factories.make_team(id=1, name="T")
    target = factories.make_user(id=5)
    target.teams = [team]  # currently a member
    ws_ok(task_session)
    osm_session.queue(
        fakes.scalar(True),  # assert_team_in_workspace
        fakes.rows(target),  # remove_member: load user
        fakes.rows(team),  # remove_member: get(team)
    )

    response = await client.delete(f"{base()}/1/members/5")

    assert response.status_code == 204
    assert osm_session.commits == 1


async def test_remove_member_missing_user_404(
    client, login, lead, task_session, osm_session
):
    login(lead)
    ws_ok(task_session)
    osm_session.queue(
        fakes.scalar(True),  # assert_team_in_workspace
        fakes.empty(),  # remove_member: user not found
    )
    response = await client.delete(f"{base()}/1/members/5")
    assert response.status_code == 404


async def test_remove_member_team_not_in_workspace_404(
    client, login, lead, task_session, osm_session
):
    login(lead)
    ws_ok(task_session)
    osm_session.queue(fakes.scalar(False))
    response = await client.delete(f"{base()}/1/members/5")
    assert response.status_code == 404
