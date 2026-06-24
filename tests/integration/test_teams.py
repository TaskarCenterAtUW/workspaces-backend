"""Integration tests for the /workspaces/{id}/teams routes.

Team routes touch BOTH databases: the workspace access guard hits the task
DB (``workspace_repo.getById``) and the team operations hit the OSM DB.
Queue results on the matching fake session in call order.
"""

from tests.support import factories, fakes


def teams_url(workspace_id: int = 1) -> str:
    return f"/api/v1/workspaces/{workspace_id}/teams"


async def test_list_teams(client, login, task_session, osm_session):
    login()
    # getById (task DB) guards access...
    task_session.queue(fakes.rows(factories.make_workspace(id=1)))
    # ...then team_repo.get_all (OSM DB) returns teams with members.
    osm_session.queue(
        fakes.rows(
            factories.make_team(id=1, name="Alpha", users=[factories.make_user()]),
            factories.make_team(id=2, name="Beta", users=[]),
        )
    )

    response = await client.get(teams_url())

    assert response.status_code == 200
    body = response.json()
    assert body[0] == {"id": 1, "name": "Alpha", "member_count": 1}
    assert body[1]["member_count"] == 0


async def test_create_team_requires_lead(client, login, task_session, osm_session):
    login()  # plain contributor
    response = await client.post(teams_url(), json={"name": "New Team"})
    assert response.status_code == 403


async def test_create_team_as_lead(client, login, task_session, osm_session):
    from api.src.users.schemas import WorkspaceUserRoleType

    login(
        factories.make_user_info(osm_workspace_roles={1: [WorkspaceUserRoleType.LEAD]})
    )
    # lead check -> getById (task) guard -> team_repo.create (OSM): add+commit+refresh
    task_session.queue(fakes.rows(factories.make_workspace(id=1)))

    response = await client.post(teams_url(), json={"name": "New Team"})

    assert response.status_code == 201
    assert isinstance(response.json(), int)
    assert osm_session.commits == 1


async def test_get_team_not_in_workspace_returns_404(
    client, login, task_session, osm_session
):
    login()
    task_session.queue(fakes.rows(factories.make_workspace(id=1)))
    # assert_team_in_workspace -> session.scalar(EXISTS) returns False
    osm_session.queue(fakes.scalar(False))

    response = await client.get(f"{teams_url()}/999")

    assert response.status_code == 404


async def test_list_teams_for_inaccessible_workspace_is_404(
    client, login, task_session
):
    login()
    task_session.queue(fakes.empty())  # getById guard finds no workspace

    response = await client.get(teams_url(42))

    assert response.status_code == 404
