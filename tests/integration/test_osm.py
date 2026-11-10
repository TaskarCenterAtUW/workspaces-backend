"""Integration tests for the /workspaces OSM routes (api/src/osm/routes.py).

Each test drives a real HTTP request through the real route + repository,
queueing simulated rows on the fake sessions.

Focus: PUT /{id}/changesets/{cid}/resolve is gated to workspace leads and
validators (403 otherwise). The gate is enforced both in the route and,
defensively, inside ``OSMRepository.resolveChangeset`` -- so a contributor is
rejected before any DB work, and the repository would reject a bad call site
even if the route check were bypassed.
"""

import pytest

from api.src.users.schemas import WorkspaceUserRoleType
from tests.support import factories, fakes

API = "/api/v1/workspaces"


def _resolve_url(workspace_id=1, changeset_id=99):
    return f"{API}/{workspace_id}/changesets/{changeset_id}/resolve"


def _user_with_role(role, workspace_id=1):
    return factories.make_user_info(osm_workspace_roles={workspace_id: [role]})


# === PUT /{id}/changesets/{cid}/resolve ====================================


async def test_resolve_changeset_validator_204(
    client, login, task_session, osm_session
):
    login(_user_with_role(WorkspaceUserRoleType.VALIDATOR))
    task_session.queue(fakes.rows(factories.make_workspace(id=1)))  # getById
    osm_session.queue(fakes.affected(1), fakes.affected(1))  # DELETE, INSERT

    response = await client.put(_resolve_url())

    assert response.status_code == 204
    assert osm_session.commits == 1  # resolveChangeset ran to completion


async def test_resolve_changeset_lead_204(client, login, task_session, osm_session):
    login(_user_with_role(WorkspaceUserRoleType.LEAD))
    task_session.queue(fakes.rows(factories.make_workspace(id=1)))
    osm_session.queue(fakes.affected(1), fakes.affected(1))

    response = await client.put(_resolve_url())

    assert response.status_code == 204
    assert osm_session.commits == 1


async def test_resolve_changeset_poc_204(client, login, task_session, osm_session):
    # POC on the owning project group satisfies isWorkspaceLead.
    login(
        factories.make_user_info(
            accessible_workspace_ids={factories.DEFAULT_PG_ID: [1]},
            poc_group_ids=(factories.DEFAULT_PG_ID,),
        )
    )
    task_session.queue(fakes.rows(factories.make_workspace(id=1)))
    osm_session.queue(fakes.affected(1), fakes.affected(1))

    response = await client.put(_resolve_url())

    assert response.status_code == 204
    assert osm_session.commits == 1


async def test_resolve_changeset_contributor_403(
    client, login, task_session, osm_session
):
    # A contributor (PG association, no validator/lead grant) is rejected
    # before any DB work -- neither session should be touched.
    login(
        factories.make_user_info(
            accessible_workspace_ids={factories.DEFAULT_PG_ID: [1]}
        )
    )

    response = await client.put(_resolve_url())

    assert response.status_code == 403
    assert osm_session.commits == 0


async def test_resolve_changeset_no_access_403(client, login):
    # A user with no association to the workspace is likewise forbidden.
    login()

    response = await client.put(_resolve_url())

    assert response.status_code == 403


async def test_resolve_changeset_validator_of_other_workspace_403(client, login):
    # Validator rights on workspace 2 do not authorize resolving on workspace 1.
    login(_user_with_role(WorkspaceUserRoleType.VALIDATOR, workspace_id=2))

    response = await client.put(_resolve_url(workspace_id=1))

    assert response.status_code == 403


async def test_resolve_changeset_unexpected_error_500(
    error_client, login, task_session, osm_session
):
    login(_user_with_role(WorkspaceUserRoleType.VALIDATOR))
    task_session.queue(fakes.rows(factories.make_workspace(id=1)))
    osm_session.queue(fakes.raises(RuntimeError("db")))  # DELETE blows up

    response = await error_client.put(_resolve_url())

    assert response.status_code == 500


# === GET /{id}/fixmes ======================================================


def _fixme_row(**fields):
    row = {
        "type": "node",
        "id": 5,
        "version": 2,
        "changeset": 9,
        "timestamp": "2026-10-23T22:30:51",
        "key": "fixme",
        "note": "set stop_id to the agency's platform stop",
        "lat": 47.58,
        "lon": -122.32,
        "tags": {
            "stop_name": "Platform",
            "fixme": "set stop_id to the agency's platform stop",
        },
    }
    return {**row, **fields}


async def test_get_fixmes_200(client, login, task_session, osm_session):
    login(
        factories.make_user_info(
            accessible_workspace_ids={factories.DEFAULT_PG_ID: [1]}
        )
    )
    task_session.queue(fakes.rows(factories.make_workspace(id=1)))  # getById
    osm_session.queue(
        fakes.mappings(
            _fixme_row(),
            _fixme_row(type="way", id=8, key="FIXME", note="which way?", tags=None),
            _fixme_row(type="relation", id=3, lat=None, lon=None),
        )
    )

    response = await client.get(f"{API}/1/fixmes")

    assert response.status_code == 200
    body = response.json()
    assert [(f["type"], f["id"], f["key"]) for f in body] == [
        ("node", 5, "fixme"),
        ("way", 8, "FIXME"),
        ("relation", 3, "fixme"),
    ]
    assert body[0]["tags"]["stop_name"] == "Platform"
    assert body[1]["tags"] == {}  # no tags row: an empty dict, not null
    assert body[2]["lat"] is None


async def test_get_fixmes_inaccessible_workspace_404(
    client, login, task_session, osm_session
):
    login(
        factories.make_user_info(
            accessible_workspace_ids={factories.DEFAULT_PG_ID: [1]}
        )
    )
    task_session.queue(fakes.empty())  # getById finds nothing the user can see

    response = await client.get(f"{API}/2/fixmes")

    assert response.status_code == 404
