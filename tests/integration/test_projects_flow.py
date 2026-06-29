from __future__ import annotations

import pytest

# Mark the whole module — every test in this file requires the
# testcontainer + migrated DB + seeded workspace fixtures.
pytestmark = pytest.mark.integration


API = "/api/v1/workspaces/{wid}/tasking/projects"

# A simple unit-square polygon in WGS84 — well-formed, non-self-intersecting.
SQUARE_POLY = {
    "type": "Polygon",
    "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]],
}

SQUARE_MULTI = {
    "type": "MultiPolygon",
    "coordinates": [
        [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]],
        [[[2, 2], [3, 2], [3, 3], [2, 3], [2, 2]]],
    ],
}


# ---------------------------------------------------------------------------
# Workflow 1 — happy path through the full lifecycle.
# ---------------------------------------------------------------------------


class TestProjectLifecycle:
    """draft -> upload AOI -> patch -> activate (still 422 w/o tasks)."""

    project_id: int | None = None

    async def test_01_create_draft(self, client, as_lead, seeded_workspace_id):
        """Create a draft project — fresh row, status=draft, no AOI, no tasks."""
        r = await client.post(
            API.format(wid=seeded_workspace_id),
            json={"name": "Pilot project", "review_required": True},
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["status"] == "draft"
        assert body["has_aoi"] is False
        assert body["task_count"] == 0
        TestProjectLifecycle.project_id = body["id"]

    async def test_02_get_round_trip(self, client, as_lead, seeded_workspace_id):
        """GET round-trips the project just created (same id)."""
        r = await client.get(f"{API.format(wid=seeded_workspace_id)}/{self.project_id}")
        assert r.status_code == 200
        assert r.json()["id"] == self.project_id

    async def test_03_patch_name(self, client, as_lead, seeded_workspace_id):
        """PATCH the project name and confirm the update is reflected."""
        r = await client.patch(
            f"{API.format(wid=seeded_workspace_id)}/{self.project_id}",
            json={"name": "Pilot project (renamed)"},
        )
        assert r.status_code == 200
        assert r.json()["name"] == "Pilot project (renamed)"

    async def test_04_upload_polygon_aoi_is_upcast(
        self, client, as_lead, seeded_workspace_id
    ):
        """Upload a Polygon AOI — storage column is MULTIPOLYGON so server upcasts."""
        r = await client.post(
            f"{API.format(wid=seeded_workspace_id)}/{self.project_id}/aoi",
            json=SQUARE_POLY,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["geometry"]["type"] == "MultiPolygon"
        assert body["geometry"]["coordinates"][0] == SQUARE_POLY["coordinates"]

    async def test_05_activate_blocked_without_tasks(
        self, client, as_lead, seeded_workspace_id
    ):
        """Activate must fail with 422 when the project has no tasks yet."""
        r = await client.post(
            f"{API.format(wid=seeded_workspace_id)}/{self.project_id}/activate"
        )
        assert r.status_code == 422
        assert "task" in r.json()["detail"].lower()

    async def test_06_aoi_get_returns_feature(
        self, client, as_lead, seeded_workspace_id
    ):
        """GET /aoi returns a GeoJSON Feature wrapping a MultiPolygon."""
        r = await client.get(
            f"{API.format(wid=seeded_workspace_id)}/{self.project_id}/aoi"
        )
        assert r.status_code == 200
        body = r.json()
        assert body["type"] == "Feature"
        assert body["geometry"]["type"] == "MultiPolygon"

    async def test_07_soft_delete_clears_listing(
        self, client, as_lead, seeded_workspace_id
    ):
        """DELETE soft-removes the project; it vanishes from list and direct GET 404s."""
        r = await client.delete(
            f"{API.format(wid=seeded_workspace_id)}/{self.project_id}"
        )
        assert r.status_code == 204

        r = await client.get(API.format(wid=seeded_workspace_id))
        ids = {row["id"] for row in r.json()["results"]}
        assert self.project_id not in ids

        r = await client.get(f"{API.format(wid=seeded_workspace_id)}/{self.project_id}")
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# Workflow 2 — AOI input variants on a fresh project.
# ---------------------------------------------------------------------------


class TestAoiInputShapes:
    """Polygon / MultiPolygon / Feature / FeatureCollection all accepted."""

    @pytest.fixture
    async def fresh_project(self, client, as_lead, seeded_workspace_id):
        r = await client.post(
            API.format(wid=seeded_workspace_id),
            json={"name": f"AOI shapes {id(self)}"},
        )
        assert r.status_code == 201, r.text
        return r.json()["id"]

    @pytest.mark.parametrize(
        "shape",
        [
            SQUARE_POLY,
            SQUARE_MULTI,
            {
                "type": "Feature",
                "geometry": SQUARE_POLY,
                "properties": {"note": "wrapped"},
            },
            {
                "type": "FeatureCollection",
                "features": [{"type": "Feature", "geometry": SQUARE_POLY}],
            },
        ],
        ids=["polygon", "multipolygon", "feature", "feature_collection"],
    )
    async def test_aoi_shape_accepted(
        self, client, as_lead, seeded_workspace_id, fresh_project, shape
    ):
        """Each of Polygon / MultiPolygon / Feature / FeatureCollection is accepted and normalised."""
        r = await client.post(
            f"{API.format(wid=seeded_workspace_id)}/{fresh_project}/aoi",
            json=shape,
        )
        assert r.status_code == 200, r.text
        assert r.json()["geometry"]["type"] == "MultiPolygon"

    async def test_invalid_aoi_rejected(
        self, client, as_lead, seeded_workspace_id, fresh_project
    ):
        """Self-intersecting bowtie polygon is rejected with 422 (Shapely is_valid=False)."""
        bowtie = {
            "type": "Polygon",
            "coordinates": [[[0, 0], [1, 1], [1, 0], [0, 1], [0, 0]]],
        }
        r = await client.post(
            f"{API.format(wid=seeded_workspace_id)}/{fresh_project}/aoi",
            json=bowtie,
        )
        assert r.status_code == 422


# ---------------------------------------------------------------------------
# Workflow 3 — permission + tenancy gates.
# ---------------------------------------------------------------------------


class TestProjectPermissions:
    async def test_contributor_cannot_create(
        self, client, as_contributor, seeded_workspace_id
    ):
        """Contributor is forbidden from creating projects (403, LEAD-only endpoint)."""
        r = await client.post(
            API.format(wid=seeded_workspace_id),
            json={"name": "should not exist"},
        )
        assert r.status_code == 403

    async def test_contributor_can_list(
        self, client, as_contributor, seeded_workspace_id
    ):
        """Contributor can list projects in their workspace (read access)."""
        r = await client.get(API.format(wid=seeded_workspace_id))
        assert r.status_code == 200
        assert "results" in r.json()

    async def test_outsider_404s_on_list(
        self, client, as_outsider, seeded_workspace_id
    ):
        """Outsider with no project-group membership gets 404 — workspace existence hidden."""
        r = await client.get(API.format(wid=seeded_workspace_id))
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# Workflow 3b — error mapping (constraint violations → precise HTTP status).
# ---------------------------------------------------------------------------


class TestProjectCreateErrors:
    async def test_role_assignment_with_unknown_user_returns_422(
        self, client, as_lead, seeded_workspace_id
    ):
        """A uuid that TDEI does not list as a PG member → 422 + missing list."""
        bogus = "ffffffff-ffff-ffff-ffff-ffffffffffff"
        r = await client.post(
            API.format(wid=seeded_workspace_id),
            json={
                "name": "role-fk-error",
                "role_assignments": [{"user_id": bogus, "role": "contributor"}],
            },
        )
        assert r.status_code == 422, r.text
        body = r.json()
        # FastAPI nests structured `detail` payloads under the `detail` key.
        assert "missing_user_ids" in body["detail"]
        assert bogus in body["detail"]["missing_user_ids"]
        assert "project group" in body["detail"]["message"].lower()

    async def test_role_assignment_auto_provisions_from_tdei(
        self,
        client,
        as_lead,
        seeded_workspace_id,
        tdei_project_group_users,
    ):
        """A uuid that's not in `users` but IS a TDEI PG member is auto-provisioned + the project is created."""
        from api.core.security import TdeiProjectGroupUser

        new_user_uuid = "1abfdb85-54c0-449b-965c-0abfd835d6fa"
        tdei_project_group_users.append(
            TdeiProjectGroupUser(
                auth_uid=new_user_uuid,
                email=f"{new_user_uuid}@test.local",
                display_name="Auto Provisioned",
            )
        )

        r = await client.post(
            API.format(wid=seeded_workspace_id),
            json={
                "name": "role-auto-provision",
                "role_assignments": [
                    {"user_id": new_user_uuid, "role": "validator"},
                ],
            },
        )
        assert r.status_code == 201, r.text
        pid = r.json()["id"]

        # Confirm the role assignment landed.
        r = await client.get(
            f"{API.format(wid=seeded_workspace_id)}/{pid}/roles/{new_user_uuid}"
        )
        assert r.status_code == 200, r.text
        assert r.json()["role"] == "validator"

    async def test_duplicate_project_name_returns_409_with_specific_message(
        self, client, as_lead, seeded_workspace_id
    ):
        """A duplicate name surfaces as 409 with the name-conflict message — NOT the generic constraint hint."""
        name = "duplicate-name-test"
        r = await client.post(
            API.format(wid=seeded_workspace_id),
            json={"name": name},
        )
        assert r.status_code == 201, r.text

        r = await client.post(
            API.format(wid=seeded_workspace_id),
            json={"name": name},
        )
        assert r.status_code == 409, r.text
        assert "already exists" in r.json()["detail"].lower()


# ---------------------------------------------------------------------------
# Workflow 4 — AOI delete / replace clears tasks.
# ---------------------------------------------------------------------------


class TestAoiReplaceSemantics:
    async def test_aoi_replace_resets_boundary_type(
        self, client, as_lead, seeded_workspace_id
    ):
        """Replacing the AOI clears task_boundary_type (per spec — geometry no longer matches)."""
        # Create + first AOI.
        r = await client.post(
            API.format(wid=seeded_workspace_id),
            json={"name": "replace-aoi"},
        )
        pid = r.json()["id"]
        r1 = await client.post(
            f"{API.format(wid=seeded_workspace_id)}/{pid}/aoi",
            json=SQUARE_POLY,
        )
        assert r1.status_code == 200

        # Replace AOI with a different one.
        r2 = await client.post(
            f"{API.format(wid=seeded_workspace_id)}/{pid}/aoi",
            json=SQUARE_MULTI,
        )
        assert r2.status_code == 200
        assert r2.json()["geometry"]["coordinates"] == SQUARE_MULTI["coordinates"]

        # Boundary type should have been cleared (per spec).
        proj = (await client.get(f"{API.format(wid=seeded_workspace_id)}/{pid}")).json()
        assert proj["task_boundary_type"] is None

    async def test_aoi_delete_round_trip(self, client, as_lead, seeded_workspace_id):
        """DELETE /aoi removes the AOI; subsequent GET returns 404."""
        r = await client.post(
            API.format(wid=seeded_workspace_id),
            json={"name": "delete-aoi"},
        )
        pid = r.json()["id"]
        await client.post(
            f"{API.format(wid=seeded_workspace_id)}/{pid}/aoi",
            json=SQUARE_POLY,
        )

        r = await client.delete(f"{API.format(wid=seeded_workspace_id)}/{pid}/aoi")
        assert r.status_code == 204

        # Subsequent GET 404s.
        r = await client.get(f"{API.format(wid=seeded_workspace_id)}/{pid}/aoi")
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# Workflow 5 — project role management
#   - LEAD can list/add/update/remove role assignments
#   - workspace-LEAD passes the manage-roles gate even without a project row
#   - contributor cannot manage roles (403)
#   - 422 mapping for unknown user_id, duplicate (409), missing assignment (404)
#   - last-LEAD guard blocks the demote / delete that would orphan the project
# ---------------------------------------------------------------------------


class TestProjectRoles:
    async def test_list_includes_creator_auto_lead(
        self, client, as_lead, seeded_workspace_id
    ):
        """Project creator is auto-seeded as LEAD and appears in GET /roles."""
        r = await client.post(
            API.format(wid=seeded_workspace_id),
            json={"name": "roles-list-1"},
        )
        pid = r.json()["id"]
        r = await client.get(f"{API.format(wid=seeded_workspace_id)}/{pid}/roles")
        assert r.status_code == 200, r.text
        rows = r.json()["results"]
        assert len(rows) == 1
        assert rows[0]["role"] == "lead"
        assert rows[0]["user_id"] == str(as_lead.user_uuid)

    async def test_add_role_round_trip(
        self,
        client,
        as_lead,
        seeded_workspace_id,
        extra_user_factory,
    ):
        """POST /roles inserts a row; GET reflects it."""
        contrib = await extra_user_factory("contributor")
        r = await client.post(
            API.format(wid=seeded_workspace_id),
            json={"name": "roles-add"},
        )
        pid = r.json()["id"]

        r = await client.post(
            f"{API.format(wid=seeded_workspace_id)}/{pid}/roles",
            json={"user_id": str(contrib.user_uuid), "role": "contributor"},
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["user_id"] == str(contrib.user_uuid)
        assert body["role"] == "contributor"

        r = await client.get(f"{API.format(wid=seeded_workspace_id)}/{pid}/roles")
        ids = {row["user_id"] for row in r.json()["results"]}
        assert str(contrib.user_uuid) in ids

    async def test_add_duplicate_returns_409(
        self,
        client,
        as_lead,
        seeded_workspace_id,
        extra_user_factory,
    ):
        """Re-adding the same user returns 409 with an actionable hint."""
        contrib = await extra_user_factory("contributor")
        r = await client.post(
            API.format(wid=seeded_workspace_id),
            json={"name": "roles-dup"},
        )
        pid = r.json()["id"]

        path = f"{API.format(wid=seeded_workspace_id)}/{pid}/roles"
        await client.post(
            path,
            json={"user_id": str(contrib.user_uuid), "role": "contributor"},
        )
        r2 = await client.post(
            path,
            json={"user_id": str(contrib.user_uuid), "role": "validator"},
        )
        assert r2.status_code == 409, r2.text
        assert "patch" in r2.json()["detail"].lower()

    async def test_add_unknown_user_returns_422(
        self, client, as_lead, seeded_workspace_id
    ):
        """An unknown user_id surfaces as 422 + missing_user_ids list."""
        bogus = "ffffffff-ffff-ffff-ffff-ffffffffffff"
        r = await client.post(
            API.format(wid=seeded_workspace_id),
            json={"name": "roles-unknown"},
        )
        pid = r.json()["id"]

        r = await client.post(
            f"{API.format(wid=seeded_workspace_id)}/{pid}/roles",
            json={"user_id": bogus, "role": "contributor"},
        )
        assert r.status_code == 422, r.text
        assert bogus in r.json()["detail"]["missing_user_ids"]

    async def test_update_role_promotes_contributor_to_validator(
        self,
        client,
        as_lead,
        seeded_workspace_id,
        extra_user_factory,
    ):
        """PATCH /roles/{uid} changes the stored role."""
        contrib = await extra_user_factory("contributor")
        r = await client.post(
            API.format(wid=seeded_workspace_id),
            json={"name": "roles-patch"},
        )
        pid = r.json()["id"]
        await client.post(
            f"{API.format(wid=seeded_workspace_id)}/{pid}/roles",
            json={"user_id": str(contrib.user_uuid), "role": "contributor"},
        )

        r = await client.patch(
            f"{API.format(wid=seeded_workspace_id)}/{pid}/roles/"
            f"{contrib.user_uuid}",
            json={"role": "validator"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["role"] == "validator"

    async def test_update_unknown_user_returns_404(
        self, client, as_lead, seeded_workspace_id
    ):
        """PATCH on a user without a role row returns 404."""
        r = await client.post(
            API.format(wid=seeded_workspace_id),
            json={"name": "roles-patch-404"},
        )
        pid = r.json()["id"]
        absent = "deadbeef-dead-dead-dead-deadbeefdead"
        r = await client.patch(
            f"{API.format(wid=seeded_workspace_id)}/{pid}/roles/{absent}",
            json={"role": "validator"},
        )
        assert r.status_code == 404

    async def test_remove_role_round_trip(
        self,
        client,
        as_lead,
        seeded_workspace_id,
        extra_user_factory,
    ):
        """DELETE /roles/{uid} removes the row; subsequent PATCH 404s."""
        contrib = await extra_user_factory("contributor")
        r = await client.post(
            API.format(wid=seeded_workspace_id),
            json={"name": "roles-delete"},
        )
        pid = r.json()["id"]
        await client.post(
            f"{API.format(wid=seeded_workspace_id)}/{pid}/roles",
            json={"user_id": str(contrib.user_uuid), "role": "contributor"},
        )

        r = await client.delete(
            f"{API.format(wid=seeded_workspace_id)}/{pid}/roles/" f"{contrib.user_uuid}"
        )
        assert r.status_code == 204

        # Subsequent PATCH is a 404 — row is gone.
        r = await client.patch(
            f"{API.format(wid=seeded_workspace_id)}/{pid}/roles/"
            f"{contrib.user_uuid}",
            json={"role": "validator"},
        )
        assert r.status_code == 404

    async def test_last_lead_demote_blocked(self, client, as_lead, seeded_workspace_id):
        """Cannot demote the only LEAD — projects must always have one."""
        r = await client.post(
            API.format(wid=seeded_workspace_id),
            json={"name": "roles-last-lead-demote"},
        )
        pid = r.json()["id"]

        r = await client.patch(
            f"{API.format(wid=seeded_workspace_id)}/{pid}/roles/"
            f"{as_lead.user_uuid}",
            json={"role": "contributor"},
        )
        assert r.status_code == 422, r.text
        assert "last lead" in r.json()["detail"].lower()

    async def test_last_lead_delete_blocked(self, client, as_lead, seeded_workspace_id):
        """Cannot delete the only LEAD — would orphan the project."""
        r = await client.post(
            API.format(wid=seeded_workspace_id),
            json={"name": "roles-last-lead-delete"},
        )
        pid = r.json()["id"]

        r = await client.delete(
            f"{API.format(wid=seeded_workspace_id)}/{pid}/roles/"
            f"{as_lead.user_uuid}",
        )
        assert r.status_code == 422
        assert "last lead" in r.json()["detail"].lower()

    async def test_demote_lead_works_when_two_leads_exist(
        self,
        client,
        as_lead,
        seeded_workspace_id,
        extra_user_factory,
    ):
        """With two LEADs, demoting one is allowed."""
        lead2 = await extra_user_factory("lead")
        r = await client.post(
            API.format(wid=seeded_workspace_id),
            json={"name": "roles-two-leads"},
        )
        pid = r.json()["id"]
        await client.post(
            f"{API.format(wid=seeded_workspace_id)}/{pid}/roles",
            json={"user_id": str(lead2.user_uuid), "role": "lead"},
        )

        # Now demote the second lead — first lead is still there.
        r = await client.patch(
            f"{API.format(wid=seeded_workspace_id)}/{pid}/roles/" f"{lead2.user_uuid}",
            json={"role": "contributor"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["role"] == "contributor"

    async def test_get_single_role(
        self,
        client,
        as_lead,
        seeded_workspace_id,
        extra_user_factory,
    ):
        """GET /roles/{uid} returns just that user's row."""
        contrib = await extra_user_factory("contributor")
        r = await client.post(
            API.format(wid=seeded_workspace_id),
            json={"name": "roles-get-single"},
        )
        pid = r.json()["id"]
        await client.post(
            f"{API.format(wid=seeded_workspace_id)}/{pid}/roles",
            json={"user_id": str(contrib.user_uuid), "role": "contributor"},
        )

        r = await client.get(
            f"{API.format(wid=seeded_workspace_id)}/{pid}/roles/" f"{contrib.user_uuid}"
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["user_id"] == str(contrib.user_uuid)
        assert body["role"] == "contributor"

    async def test_get_single_role_404_when_absent(
        self, client, as_lead, seeded_workspace_id
    ):
        """GET /roles/{uid} 404s when the user has no row on the project."""
        r = await client.post(
            API.format(wid=seeded_workspace_id),
            json={"name": "roles-get-404"},
        )
        pid = r.json()["id"]
        absent = "feedf00d-feed-feed-feed-feedfeedfeed"
        r = await client.get(
            f"{API.format(wid=seeded_workspace_id)}/{pid}/roles/{absent}"
        )
        assert r.status_code == 404

    async def test_put_upsert_inserts_with_201(
        self,
        client,
        as_lead,
        seeded_workspace_id,
        extra_user_factory,
    ):
        """PUT on a fresh user creates the row and returns 201."""
        contrib = await extra_user_factory("contributor")
        r = await client.post(
            API.format(wid=seeded_workspace_id),
            json={"name": "roles-put-create"},
        )
        pid = r.json()["id"]

        r = await client.put(
            f"{API.format(wid=seeded_workspace_id)}/{pid}/roles/"
            f"{contrib.user_uuid}",
            json={"role": "validator"},
        )
        assert r.status_code == 201, r.text
        assert r.json()["role"] == "validator"

    async def test_put_upsert_updates_with_200(
        self,
        client,
        as_lead,
        seeded_workspace_id,
        extra_user_factory,
    ):
        """PUT on an existing user updates the role and returns 200."""
        contrib = await extra_user_factory("contributor")
        r = await client.post(
            API.format(wid=seeded_workspace_id),
            json={"name": "roles-put-update"},
        )
        pid = r.json()["id"]
        await client.post(
            f"{API.format(wid=seeded_workspace_id)}/{pid}/roles",
            json={"user_id": str(contrib.user_uuid), "role": "contributor"},
        )

        r = await client.put(
            f"{API.format(wid=seeded_workspace_id)}/{pid}/roles/"
            f"{contrib.user_uuid}",
            json={"role": "validator"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["role"] == "validator"

    async def test_put_unknown_user_returns_422(
        self, client, as_lead, seeded_workspace_id
    ):
        """PUT for a uuid with no `users` row returns 422 + missing list."""
        bogus = "ffffffff-ffff-ffff-ffff-ffffffffffff"
        r = await client.post(
            API.format(wid=seeded_workspace_id),
            json={"name": "roles-put-unknown"},
        )
        pid = r.json()["id"]

        r = await client.put(
            f"{API.format(wid=seeded_workspace_id)}/{pid}/roles/{bogus}",
            json={"role": "contributor"},
        )
        assert r.status_code == 422, r.text
        assert bogus in r.json()["detail"]["missing_user_ids"]

    async def test_put_last_lead_demote_blocked(
        self, client, as_lead, seeded_workspace_id
    ):
        """PUT cannot demote the only LEAD any more than PATCH can."""
        r = await client.post(
            API.format(wid=seeded_workspace_id),
            json={"name": "roles-put-last-lead"},
        )
        pid = r.json()["id"]

        r = await client.put(
            f"{API.format(wid=seeded_workspace_id)}/{pid}/roles/"
            f"{as_lead.user_uuid}",
            json={"role": "contributor"},
        )
        assert r.status_code == 422
        assert "last lead" in r.json()["detail"].lower()

    async def test_self_project_roles_falls_back_to_workspace_role(
        self,
        client,
        as_lead,
        seeded_workspace_id,
        extra_user_factory,
        override_user,
    ):
        """`/me/.../roles` returns override where present, workspace role elsewhere."""
        # Project A with no explicit override — caller will get workspace role.
        r = await client.post(
            API.format(wid=seeded_workspace_id),
            json={"name": "self-roles-A"},
        )
        pid_a = r.json()["id"]
        # Project B where the contributor gets an explicit validator role.
        r = await client.post(
            API.format(wid=seeded_workspace_id),
            json={"name": "self-roles-B"},
        )
        pid_b = r.json()["id"]
        contributor = await extra_user_factory("contributor")
        await client.post(
            f"{API.format(wid=seeded_workspace_id)}/{pid_b}/roles",
            json={"user_id": str(contributor.user_uuid), "role": "validator"},
        )

        # Switch to the contributor and call /me/.../roles.
        override_user(contributor)
        r = await client.get(
            f"/api/v1/me/workspaces/{seeded_workspace_id}/tasking/projects/roles"
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["workspace_role"] == "contributor"
        by_pid = {p["project_id"]: p for p in body["projects"]}
        # Project B has the explicit validator override.
        assert by_pid[pid_b]["role"] == "validator"
        # Project A falls back to workspace-level contributor.
        assert by_pid[pid_a]["role"] == "contributor"

    async def test_contributor_cannot_manage_roles(
        self,
        client,
        as_lead,
        seeded_workspace_id,
        extra_user_factory,
        override_user,
    ):
        """A workspace contributor with no project-LEAD role is denied 403."""
        # Create project as LEAD, then act-as a contributor.
        r = await client.post(
            API.format(wid=seeded_workspace_id),
            json={"name": "roles-contrib-denied"},
        )
        pid = r.json()["id"]

        contributor = await extra_user_factory("contributor")
        override_user(contributor)

        r = await client.post(
            f"{API.format(wid=seeded_workspace_id)}/{pid}/roles",
            json={
                "user_id": str(contributor.user_uuid),
                "role": "contributor",
            },
        )
        assert r.status_code == 403
