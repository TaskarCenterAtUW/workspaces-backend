"""Integration tests for the /workspaces routes.

Covers the @test comments in api/src/workspaces/routes.py across all
endpoints (list, get, bbox, create, update, delete, quest get/settings,
imagery get/settings). Each test drives a real HTTP request through the
real route + repository code, queueing simulated rows on the fake sessions.

Note: read endpoints (get, bbox, quest, imagery) gate access via
``getById``, which raises 404 when the workspace is missing or inaccessible
-- so "no access" surfaces as 404 there rather than 403. The mutating
endpoints additionally require ``isWorkspaceLead`` and return 403 otherwise.
"""

from datetime import datetime
from uuid import UUID

import pytest

import api.src.workspaces.routes as ws_routes
from api.src.users.schemas import WorkspaceUserRoleType
from api.src.workspaces.jobs.schemas import Job
from api.src.workspaces.schemas import QuestDefinitionType, WorkspaceLongQuest
from tests.support import factories, fakes

API = "/api/v1/workspaces"


@pytest.fixture
def lead():
    return factories.make_user_info(
        osm_workspace_roles={1: [WorkspaceUserRoleType.LEAD]}
    )


@pytest.fixture
def no_schema_validation(monkeypatch):
    """Make schema validation a no-op (avoid network in update endpoints)."""

    async def ok(_definition):
        return None

    monkeypatch.setattr(ws_routes, "validate_quest_definition_schema", ok)
    monkeypatch.setattr(ws_routes, "validate_imagery_definition_schema", ok)


@pytest.fixture
def evictions(monkeypatch):
    calls = []
    monkeypatch.setattr(ws_routes, "evict_user_from_cache", calls.append)
    return calls


# === GET /mine =============================================================


async def test_list_my_workspaces(client, login, task_session, osm_session):
    login()
    task_session.queue(
        fakes.rows(
            factories.make_workspace(id=1, title="One"),
            factories.make_workspace(id=2, title="Two"),
        ),
    )
    # tasking_projects and user_workspace_roles both live in the OSM DB, so
    # both batched counts are queued on osm_session, in the order the route
    # calls them: projects counts, then member counts.
    osm_session.queue(
        fakes.rows((1, 4), (2, 2)),  # get_projects_counts
        fakes.rows((1, 3), (2, 1)),  # get_member_counts
    )

    response = await client.get(f"{API}/mine")

    assert response.status_code == 200
    body = response.json()
    assert [w["id"] for w in body] == [1, 2]
    assert body[0]["role"] == "contributor"
    assert body[0]["projectsCount"] == 4
    assert body[0]["membersCount"] == 3
    assert body[1]["projectsCount"] == 2
    assert body[1]["membersCount"] == 1
    assert "updatedAt" in body[0]


async def test_list_my_workspaces_empty(client, login, task_session):
    login()
    task_session.queue(fakes.rows())
    response = await client.get(f"{API}/mine")
    assert response.status_code == 200
    assert response.json() == []


async def test_list_my_workspaces_unexpected_error_500(
    error_client, login, task_session
):
    login()
    task_session.queue(fakes.raises(RuntimeError("db")))
    response = await error_client.get(f"{API}/mine")
    assert response.status_code == 500


async def test_list_matches_get_by_id(client, login, task_session, osm_session):
    # The same workspace serialized via /mine and via /{id} agree on shared fields.
    login(
        factories.make_user_info(osm_workspace_roles={1: [WorkspaceUserRoleType.LEAD]})
    )
    task_session.queue(
        fakes.rows(factories.make_workspace(id=1, title="Shared")),  # /mine getAll
        fakes.rows(factories.make_workspace(id=1, title="Shared")),  # /1 getById
    )
    osm_session.queue(
        fakes.rows((1, 0)),  # get_projects_counts for /mine
        fakes.rows((1, 0)),  # get_member_counts for /mine
    )

    listed = (await client.get(f"{API}/mine")).json()[0]
    fetched = (await client.get(f"{API}/1")).json()

    shared = ["id", "title", "type", "tdeiProjectGroupId", "role"]
    assert {k: listed[k] for k in shared} == {k: fetched[k] for k in shared}


# === GET /{id} =============================================================


async def test_get_workspace_by_id(client, login, task_session):
    login()
    task_session.queue(fakes.rows(factories.make_workspace(id=7, title="Lucky")))
    response = await client.get(f"{API}/7")
    assert response.status_code == 200
    assert response.json()["title"] == "Lucky"


async def test_get_workspace_missing_404(client, login, task_session):
    login()
    task_session.queue(fakes.empty())
    response = await client.get(f"{API}/404")
    assert response.status_code == 404


async def test_get_workspace_non_integer_id_422(client, login):
    login()
    response = await client.get(f"{API}/not-a-number")
    assert response.status_code == 422


async def test_get_workspace_unexpected_error_500(error_client, login, task_session):
    login()
    task_session.queue(fakes.raises(RuntimeError("db")))
    response = await error_client.get(f"{API}/7")
    assert response.status_code == 500


# === GET /{id}/bbox ========================================================


async def test_get_bbox_returns_values(client, login, task_session, osm_session):
    login()
    task_session.queue(fakes.rows(factories.make_workspace(id=1)))
    osm_session.queue(
        fakes.mappings({"max_lat": 1.5, "max_lon": 2.5, "min_lat": 0.5, "min_lon": 1.5})
    )

    response = await client.get(f"{API}/1/bbox")

    assert response.status_code == 200
    assert response.json()["max_lat"] == 1.5


async def test_get_bbox_workspace_missing_404(client, login, task_session):
    login()
    task_session.queue(fakes.empty())
    response = await client.get(f"{API}/1/bbox")
    assert response.status_code == 404


async def test_get_bbox_no_nodes_404(client, login, task_session, osm_session):
    login()
    task_session.queue(fakes.rows(factories.make_workspace(id=1)))
    osm_session.queue(fakes.mappings())  # no rows -> bbox None -> NotFound
    response = await client.get(f"{API}/1/bbox")
    assert response.status_code == 404


# === POST "" create ========================================================


async def test_create_workspace(
    client, app, login, task_session, osm_session, evictions, monkeypatch
):
    class _FakeMessenger:
        def send_message(self, _message):
            return None

    class _FakeJobsRepository:
        async def create(self, _current_user, job_data):
            return Job(
                id=1,
                job_type=job_data.job_type,
                status=job_data.status,
                request=job_data.request,
                created_at=datetime(2026, 1, 1),
                updated_at=datetime(2026, 1, 1),
                current_task=job_data.current_task,
                current_task_status=job_data.current_task_status,
                response=job_data.response,
                workspace_id=job_data.workspace_id,
            )

        async def update(self, _current_user, job_id, job_data, **_kwargs):
            return Job(
                id=job_id,
                job_type="workspace-import",
                status="requested",
                request=job_data.request if job_data.request is not None else {},
                created_at=datetime(2026, 1, 1),
                updated_at=datetime(2026, 1, 1),
                current_task=None,
                current_task_status=None,
                response=None,
                workspace_id=1,
            )

    monkeypatch.setattr(ws_routes, "Messenger", _FakeMessenger)
    app.dependency_overrides[ws_routes.get_jobs_repository] = _FakeJobsRepository

    login(factories.make_user_info(project_group_ids=[factories.DEFAULT_PG_ID]))
    osm_session.queue(
        fakes.scalar(1),  # assign_member_role: user exists
        fakes.affected(1),  # assign_member_role: role upsert execute
    )

    response = await client.post(
        f"{API}",
        json={
            "type": "osw",
            "title": "Fresh",
            "tdeiProjectGroupId": factories.DEFAULT_PG_ID,
            "tdeiRecordId": "33333333-3333-3333-3333-333333333333",
            "tdeiServiceId": "44444444-4444-4444-4444-444444444444",
        },
    )

    assert response.status_code == 201
    assert response.json()["workspaceId"] is not None
    assert response.json()["importJobId"] is not None
    assert task_session.commits == 1  # workspace insert
    assert osm_session.commits == 1  # role insert
    assert evictions == [UUID(factories.DEFAULT_USER_ID)]  # creator's cache evicted


async def test_create_in_unauthorized_group_403(client, login):
    # User belongs to DEFAULT_PG_ID only; creating in another group is forbidden.
    login(factories.make_user_info(project_group_ids=[factories.DEFAULT_PG_ID]))
    response = await client.post(
        f"{API}",
        json={
            "type": "osw",
            "title": "Nope",
            "tdeiProjectGroupId": "99999999-9999-9999-9999-999999999999",
        },
    )
    assert response.status_code == 403


async def test_create_invalid_body_422(client, login):
    login()
    # Missing required 'title' and 'tdeiProjectGroupId'.
    response = await client.post(f"{API}", json={"type": "osw"})
    assert response.status_code == 422


# === PATCH /{id} update ====================================================


async def test_update_requires_lead(client, login):
    login(factories.make_user_info())
    response = await client.patch(f"{API}/1", json={"title": "X"})
    assert response.status_code == 403


async def test_update_empty_patch_400(client, login, lead):
    login(lead)
    response = await client.patch(f"{API}/1", json={})
    assert response.status_code == 400


async def test_update_success(client, login, lead, task_session):
    login(lead)
    task_session.queue(
        fakes.affected(1),  # UPDATE
        fakes.rows(factories.make_workspace(id=1, title="Renamed")),  # getById
    )
    response = await client.patch(f"{API}/1", json={"title": "Renamed"})
    assert response.status_code == 204


async def test_update_missing_workspace_404(client, login, lead, task_session):
    login(lead)
    task_session.queue(fakes.affected(0))  # UPDATE matched nothing -> NotFound
    response = await client.patch(f"{API}/1", json={"title": "X"})
    assert response.status_code == 404


async def test_update_invalid_body_422(client, login, lead):
    login(lead)
    response = await client.patch(f"{API}/1", json={"externalAppAccess": 999})
    assert response.status_code == 422


# === DELETE /{id} ==========================================================


async def test_delete_requires_lead(client, login):
    login(factories.make_user_info())
    response = await client.delete(f"{API}/1")
    assert response.status_code == 403


async def test_delete_success_no_members(
    client, login, lead, task_session, osm_session, evictions
):
    login(lead)
    osm_session.queue(fakes.rows())  # get_privileged_workspace_members: none
    task_session.queue(fakes.affected(1))  # delete

    response = await client.delete(f"{API}/1")

    assert response.status_code == 204
    assert task_session.commits == 1
    assert evictions == []  # no members to evict


async def test_delete_evicts_member_caches(
    client, login, lead, task_session, osm_session, evictions
):
    login(lead)
    member = factories.make_user(id=9, auth_uid=factories.DEFAULT_USER_ID)
    osm_session.queue(fakes.rows((member, "lead")))  # privileged members
    task_session.queue(fakes.affected(1))

    response = await client.delete(f"{API}/1")

    assert response.status_code == 204
    assert evictions == [UUID(factories.DEFAULT_USER_ID)]


async def test_delete_missing_workspace_404(
    client, login, lead, task_session, osm_session
):
    login(lead)
    osm_session.queue(fakes.rows())  # no members
    task_session.queue(fakes.affected(0))  # delete matched nothing -> NotFound
    response = await client.delete(f"{API}/1")
    assert response.status_code == 404


# === GET /{id}/quests/long =================================================


async def test_get_long_quest_def_none_returns_204(client, login, task_session):
    login()
    task_session.queue(fakes.rows(factories.make_workspace(id=1)))  # no quest def
    response = await client.get(f"{API}/1/quests/long")
    assert response.status_code == 204


async def test_get_long_quest_def_json(client, login, task_session):
    login()
    ws = factories.make_workspace(id=1)
    ws.longFormQuestDef = WorkspaceLongQuest(
        workspace_id=1,
        type=QuestDefinitionType.JSON,
        definition='{"quest": true}',
        modifiedAt=datetime(2026, 1, 1),
        modifiedBy=UUID(factories.DEFAULT_USER_ID),
        modifiedByName="x",
    )
    task_session.queue(fakes.rows(ws))

    response = await client.get(f"{API}/1/quests/long")

    assert response.status_code == 200
    assert response.json() == {"quest": True}


async def test_get_long_quest_def_workspace_missing_404(client, login, task_session):
    login()
    task_session.queue(fakes.empty())
    response = await client.get(f"{API}/1/quests/long")
    assert response.status_code == 404


# === GET /{id}/quests/long/settings ========================================


async def test_get_long_quest_settings_default_when_unset(client, login, task_session):
    login()
    task_session.queue(fakes.rows(factories.make_workspace(id=1)))
    response = await client.get(f"{API}/1/quests/long/settings")
    assert response.status_code == 200
    body = response.json()
    assert body["type"] == "NONE"
    assert body["workspace_id"] == 1


async def test_get_long_quest_settings_from_def(client, login, task_session):
    login()
    ws = factories.make_workspace(id=1)
    ws.longFormQuestDef = WorkspaceLongQuest(
        workspace_id=1,
        type=QuestDefinitionType.URL,
        url="https://q.example",
        modifiedAt=datetime(2026, 1, 1),
        modifiedBy=UUID(factories.DEFAULT_USER_ID),
        modifiedByName="Editor",
    )
    task_session.queue(fakes.rows(ws))

    response = await client.get(f"{API}/1/quests/long/settings")

    assert response.status_code == 200
    body = response.json()
    assert body["type"] == "URL"
    assert body["url"] == "https://q.example"


async def test_get_long_quest_settings_missing_404(client, login, task_session):
    login()
    task_session.queue(fakes.empty())
    response = await client.get(f"{API}/1/quests/long/settings")
    assert response.status_code == 404


# === PATCH /{id}/quests/long/settings ======================================


async def test_update_quest_settings_requires_lead(client, login):
    login(factories.make_user_info())
    response = await client.patch(
        f"{API}/1/quests/long/settings", json={"type": "NONE"}
    )
    assert response.status_code == 403


async def test_update_quest_settings_none_success(client, login, lead, task_session):
    login(lead)
    task_session.queue(fakes.affected(1))  # save_longform_quest UPDATE
    response = await client.patch(
        f"{API}/1/quests/long/settings", json={"type": "NONE"}
    )
    assert response.status_code == 204


async def test_update_quest_settings_json_validates_schema(
    client, login, lead, task_session, no_schema_validation
):
    login(lead)
    task_session.queue(fakes.affected(1))
    response = await client.patch(
        f"{API}/1/quests/long/settings",
        json={"type": "JSON", "definition": '{"a": 1}'},
    )
    assert response.status_code == 204


async def test_update_quest_settings_invalid_schema_400(
    client, login, lead, monkeypatch
):
    from fastapi import HTTPException

    async def reject(_definition):
        raise HTTPException(status_code=400, detail="bad schema")

    monkeypatch.setattr(ws_routes, "validate_quest_definition_schema", reject)
    login(lead)

    response = await client.patch(
        f"{API}/1/quests/long/settings",
        json={"type": "JSON", "definition": '{"a": 1}'},
    )
    assert response.status_code == 400


async def test_update_quest_settings_json_without_definition_422(client, login, lead):
    login(lead)
    # QuestSettingsPatch validator rejects JSON type with no definition.
    response = await client.patch(
        f"{API}/1/quests/long/settings", json={"type": "JSON"}
    )
    assert response.status_code == 422


# === GET /{id}/imagery/settings ============================================


async def test_get_imagery_settings_default_when_unset(client, login, task_session):
    login()
    task_session.queue(fakes.rows(factories.make_workspace(id=1)))
    response = await client.get(f"{API}/1/imagery/settings")
    assert response.status_code == 200
    assert response.json()["definition"] == []


async def test_get_imagery_settings_missing_404(client, login, task_session):
    login()
    task_session.queue(fakes.empty())
    response = await client.get(f"{API}/1/imagery/settings")
    assert response.status_code == 404


# === PATCH /{id}/imagery/settings ==========================================


async def test_update_imagery_requires_lead(client, login):
    login(factories.make_user_info())
    response = await client.patch(f"{API}/1/imagery/settings", json={"definition": []})
    assert response.status_code == 403


async def test_update_imagery_success(
    client, login, lead, task_session, no_schema_validation
):
    login(lead)
    task_session.queue(fakes.affected(1))  # save_imagery_def UPDATE
    response = await client.patch(
        f"{API}/1/imagery/settings",
        json={"definition": [{"name": "layer", "url": "https://x"}]},
    )
    assert response.status_code == 204


async def test_update_imagery_invalid_schema_400(client, login, lead, monkeypatch):
    from fastapi import HTTPException

    async def reject(_definition):
        raise HTTPException(status_code=400, detail="bad imagery")

    monkeypatch.setattr(ws_routes, "validate_imagery_definition_schema", reject)
    login(lead)

    response = await client.patch(
        f"{API}/1/imagery/settings", json={"definition": [{"bad": True}]}
    )
    assert response.status_code == 400


async def test_update_imagery_empty_skips_validation_success(
    client, login, lead, task_session
):
    # Empty definition is falsy -> validation skipped, save proceeds.
    login(lead)
    task_session.queue(fakes.affected(1))
    response = await client.patch(f"{API}/1/imagery/settings", json={"definition": []})
    assert response.status_code == 204
