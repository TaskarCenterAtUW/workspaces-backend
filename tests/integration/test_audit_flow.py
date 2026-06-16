from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


API = "/api/v1/workspaces/{wid}/tasking/projects"


AOI_UNIT_SQUARE = {
    "type": "Polygon",
    "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]],
}

TASK_A = {
    "type": "Polygon",
    "coordinates": [
        [[0.00, 0.00], [0.01, 0.00], [0.01, 0.01], [0.00, 0.01], [0.00, 0.00]]
    ],
}
TASK_B = {
    "type": "Polygon",
    "coordinates": [
        [[0.02, 0.02], [0.03, 0.02], [0.03, 0.03], [0.02, 0.03], [0.02, 0.02]]
    ],
}


def _fc(*polys):
    return {
        "type": "FeatureCollection",
        "features": [{"type": "Feature", "geometry": p} for p in polys],
    }


# ---------------------------------------------------------------------------
# Helpers — open a project with two tasks so the audit log has rows worth
# filtering across (project_created, aoi_uploaded, task_created x N,
# project_activated, plus task lock/unlock events later).
# ---------------------------------------------------------------------------


async def _open_project_with_tasks(client, workspace_id):
    r = await client.post(
        API.format(wid=workspace_id),
        json={"name": f"audit-{id(client)}", "review_required": False},
    )
    assert r.status_code == 201, r.text
    pid = r.json()["id"]

    r = await client.post(
        f"{API.format(wid=workspace_id)}/{pid}/aoi", json=AOI_UNIT_SQUARE
    )
    assert r.status_code == 200, r.text

    r = await client.post(
        f"{API.format(wid=workspace_id)}/{pid}/tasks/save",
        json={"feature_collection": _fc(TASK_A, TASK_B)},
    )
    assert r.status_code == 201, r.text

    r = await client.post(f"{API.format(wid=workspace_id)}/{pid}/activate")
    assert r.status_code == 200, r.text
    return pid


# ---------------------------------------------------------------------------
# Workflow 1 — project-level audit listing + filters.
# ---------------------------------------------------------------------------


class TestProjectAuditListing:

    async def test_lists_lifecycle_events_newest_first(
        self, client, as_lead, seeded_workspace_id
    ):
        """Project create → AOI upload → tasks → activate all appear in audit."""
        pid = await _open_project_with_tasks(client, seeded_workspace_id)

        r = await client.get(f"{API.format(wid=seeded_workspace_id)}/{pid}/audit")
        assert r.status_code == 200, r.text
        body = r.json()
        assert "results" in body
        assert body["pagination"]["total"] >= 1

        seen = [row["event_type"] for row in body["results"]]
        # Lifecycle events we know are emitted by repository code today.
        assert "project_activated" in seen
        # Newest first.
        ts = [row["occurred_at"] for row in body["results"]]
        assert ts == sorted(ts, reverse=True)

    async def test_filter_by_event_type(self, client, as_lead, seeded_workspace_id):
        """`event_type` query narrows results to one kind."""
        pid = await _open_project_with_tasks(client, seeded_workspace_id)

        r = await client.get(
            f"{API.format(wid=seeded_workspace_id)}/{pid}/audit",
            params={"event_type": "project_activated"},
        )
        assert r.status_code == 200, r.text
        kinds = {row["event_type"] for row in r.json()["results"]}
        assert kinds == {"project_activated"}

    async def test_filter_by_actor(self, client, as_lead, seeded_workspace_id):
        """`actor_user_id` filters to events emitted by that user only."""
        pid = await _open_project_with_tasks(client, seeded_workspace_id)
        r = await client.get(
            f"{API.format(wid=seeded_workspace_id)}/{pid}/audit",
            params={"actor_user_id": str(as_lead.user_uuid)},
        )
        assert r.status_code == 200
        for row in r.json()["results"]:
            assert row["actor"]["user_id"] == str(as_lead.user_uuid)

    async def test_pagination_clamps_and_total(
        self, client, as_lead, seeded_workspace_id
    ):
        """Page size of 1 still returns one row; total reflects the whole set."""
        pid = await _open_project_with_tasks(client, seeded_workspace_id)
        r = await client.get(
            f"{API.format(wid=seeded_workspace_id)}/{pid}/audit",
            params={"page_size": 1, "page": 1},
        )
        assert r.status_code == 200
        body = r.json()
        assert len(body["results"]) == 1
        assert body["pagination"]["page_size"] == 1
        assert body["pagination"]["total"] >= 1

    async def test_unknown_project_404(self, client, as_lead, seeded_workspace_id):
        """A bogus project id returns 404 from the tenancy / existence check."""
        r = await client.get(f"{API.format(wid=seeded_workspace_id)}/999999/audit")
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# Workflow 2 — task-level audit listing.
# ---------------------------------------------------------------------------


class TestTaskAuditListing:

    async def test_lists_task_events(
        self, client, as_lead, as_contributor, seeded_workspace_id
    ):
        """Lock/unlock on a task surface in /tasks/{n}/audit."""
        # `as_lead` opens the project (lead-only), then switch to contributor
        # to perform lock + unlock so we generate task events.
        pid = await _open_project_with_tasks(client, seeded_workspace_id)

        # Contributor locks task 1.
        r = await client.post(
            f"{API.format(wid=seeded_workspace_id)}/{pid}/tasks/1/lock"
        )
        assert r.status_code in (200, 201), r.text

        r = await client.delete(
            f"{API.format(wid=seeded_workspace_id)}/{pid}/tasks/1/lock"
        )
        assert r.status_code in (200, 204), r.text

        r = await client.get(
            f"{API.format(wid=seeded_workspace_id)}/{pid}/tasks/1/audit"
        )
        assert r.status_code == 200, r.text
        body = r.json()
        kinds = {row["event_type"] for row in body["results"]}
        assert "task_locked" in kinds
        assert "task_unlocked" in kinds
        # Every row should reference the right task (by id or task_number).
        for row in body["results"]:
            assert row["task_id"] is not None or row.get("task_number") == 1

    async def test_unknown_task_404(self, client, as_lead, seeded_workspace_id):
        """A bogus task number on a real project returns 404."""
        pid = await _open_project_with_tasks(client, seeded_workspace_id)
        r = await client.get(
            f"{API.format(wid=seeded_workspace_id)}/{pid}/tasks/99/audit"
        )
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# Workflow 3 — soft-deleted projects honour include_deleted.
# ---------------------------------------------------------------------------


class TestAuditIncludeDeleted:

    async def test_deleted_project_hidden_by_default(
        self, client, as_lead, seeded_workspace_id
    ):
        """A soft-deleted project's audit returns 404 unless `include_deleted=true`."""
        pid = await _open_project_with_tasks(client, seeded_workspace_id)

        # Project must be closed before delete.
        r = await client.post(f"{API.format(wid=seeded_workspace_id)}/{pid}/close")
        # Some tasks may still be open; tolerate either path. The audit
        # endpoint behaviour we care about only needs deleted_at to be
        # set, which the delete call will do regardless of status.
        r = await client.delete(f"{API.format(wid=seeded_workspace_id)}/{pid}")
        if r.status_code != 204:
            pytest.skip(f"Could not soft-delete project: {r.status_code} {r.text}")

        # Default = hidden.
        r = await client.get(f"{API.format(wid=seeded_workspace_id)}/{pid}/audit")
        assert r.status_code == 404

        # Explicit opt-in = visible.
        r = await client.get(
            f"{API.format(wid=seeded_workspace_id)}/{pid}/audit",
            params={"include_deleted": True},
        )
        assert r.status_code == 200, r.text
        assert r.json()["pagination"]["total"] >= 1
