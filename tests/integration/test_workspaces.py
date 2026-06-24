"""Integration tests for the /workspaces routes.

Each test drives a real HTTP request through the real route + repository
code, queueing simulated rows on the fake DB sessions. Comments note the
query sequence each route issues (= the order to queue results).
"""

from api.src.users.schemas import WorkspaceUserRoleType
from tests.support import factories, fakes

API = "/api/v1/workspaces"


async def test_get_my_workspaces(client, login, task_session):
    login()
    # getAll -> one SELECT on the task DB
    task_session.queue(
        fakes.rows(
            factories.make_workspace(id=1, title="WS One"),
            factories.make_workspace(id=2, title="WS Two"),
        )
    )

    response = await client.get(f"{API}/mine")

    assert response.status_code == 200
    body = response.json()
    assert [w["id"] for w in body] == [1, 2]
    assert body[0]["title"] == "WS One"
    assert body[0]["role"] == "contributor"  # WorkspaceResponse includes role


async def test_get_workspace_by_id(client, login, task_session):
    login()
    # getById -> one SELECT
    task_session.queue(fakes.rows(factories.make_workspace(id=7, title="Lucky")))

    response = await client.get(f"{API}/7")

    assert response.status_code == 200
    assert response.json()["title"] == "Lucky"


async def test_get_workspace_not_found(client, login, task_session):
    login()
    task_session.queue(fakes.empty())  # getById finds nothing

    response = await client.get(f"{API}/404")

    assert response.status_code == 404


async def test_create_workspace(client, login, task_session, osm_session):
    login(factories.make_user_info(project_group_ids=[factories.DEFAULT_PG_ID]))
    # create (task DB) -> add + commit + refresh; then assign_member_role
    # (OSM DB) checks the user exists via session.scalar(...).
    osm_session.queue(fakes.scalar(1))

    response = await client.post(
        f"{API}",
        json={
            "type": "osw",
            "title": "Fresh Workspace",
            "tdeiProjectGroupId": factories.DEFAULT_PG_ID,
        },
    )

    assert response.status_code == 201
    assert response.json()["workspaceId"] is not None
    assert task_session.commits == 1
    assert osm_session.commits == 1


async def test_update_workspace_requires_lead(client, login, task_session):
    # Plain contributor, not a lead -> 403 before any DB write.
    login(factories.make_user_info())

    response = await client.patch(f"{API}/1", json={"title": "Renamed"})

    assert response.status_code == 403
    assert task_session.commits == 0


async def test_update_workspace_as_lead(client, login, task_session):
    login(
        factories.make_user_info(osm_workspace_roles={1: [WorkspaceUserRoleType.LEAD]})
    )
    # update -> UPDATE (rowcount) ... commit ... then getById -> SELECT
    task_session.queue(
        fakes.affected(1),
        fakes.rows(factories.make_workspace(id=1, title="Renamed")),
    )

    response = await client.patch(f"{API}/1", json={"title": "Renamed"})

    assert response.status_code == 204
    assert task_session.commits == 1


async def test_update_workspace_rejects_empty_patch(client, login):
    login(
        factories.make_user_info(osm_workspace_roles={1: [WorkspaceUserRoleType.LEAD]})
    )

    response = await client.patch(f"{API}/1", json={})

    assert response.status_code == 400


async def test_delete_workspace_as_lead(client, login, task_session, osm_session):
    login(
        factories.make_user_info(osm_workspace_roles={1: [WorkspaceUserRoleType.LEAD]})
    )
    # delete flow: get_privileged_workspace_members (OSM) -> delete (task) ->
    # remove_all_member_roles (OSM)
    osm_session.queue(fakes.rows())  # no privileged members
    task_session.queue(fakes.affected(1))

    response = await client.delete(f"{API}/1")

    assert response.status_code == 204
    assert task_session.commits == 1


async def test_delete_workspace_forbidden_for_non_lead(client, login):
    login(factories.make_user_info())

    response = await client.delete(f"{API}/1")

    assert response.status_code == 403
