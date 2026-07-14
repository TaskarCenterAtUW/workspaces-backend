from __future__ import annotations

import pytest

API = "/api/v1/workspaces/{wid}/tasking/projects"


# ---------------------------------------------------------------------------
# Permission gates
# ---------------------------------------------------------------------------


class TestPermissionGates:
    async def test_outsider_404s_on_list(
        self, client, as_outsider, seeded_workspace_id, fake_repos
    ):
        """Outsider (no project-group membership) gets 404 on list — tenancy gate hides existence."""
        r = await client.get(API.format(wid=seeded_workspace_id))
        assert r.status_code == 404

    async def test_contributor_can_list(
        self, client, as_contributor, seeded_workspace_id, fake_repos
    ):
        """Contributor can list projects in their workspace (read access for any role)."""
        r = await client.get(API.format(wid=seeded_workspace_id))
        assert r.status_code == 200
        body = r.json()
        assert body["results"] == []
        assert body["pagination"]["total"] == 0

    async def test_contributor_cannot_create(
        self, client, as_contributor, seeded_workspace_id, fake_repos
    ):
        """Contributor cannot create projects — write endpoints require LEAD."""
        r = await client.post(
            API.format(wid=seeded_workspace_id),
            json={"name": "x"},
        )
        assert r.status_code == 403


# ---------------------------------------------------------------------------
# CRUD happy-path
# ---------------------------------------------------------------------------


class TestProjectCrud:
    async def test_create_returns_201(
        self, client, as_lead, seeded_workspace_id, fake_repos
    ):
        """LEAD creates a draft project — 201 with the persisted body."""
        r = await client.post(
            API.format(wid=seeded_workspace_id),
            json={"name": "Pilot", "review_required": False},
        )
        assert r.status_code == 201
        body = r.json()
        assert body["name"] == "Pilot"
        assert body["status"] == "draft"
        assert body["review_required"] is False
        assert len(fake_repos.project._projects) == 1

    async def test_create_blank_name_422(
        self, client, as_lead, seeded_workspace_id, fake_repos
    ):
        """Whitespace-only name is rejected by the Pydantic validator (422)."""
        r = await client.post(
            API.format(wid=seeded_workspace_id),
            json={"name": "   "},
        )
        assert r.status_code == 422

    @pytest.mark.parametrize("bad_value", ["not-an-object", 123, True, ["x"]])
    async def test_create_rejects_non_object_custom_imagery_422(
        self, client, as_lead, seeded_workspace_id, fake_repos, bad_value
    ):
        """custom_imagery must be a JSON object; scalar/array values are rejected (422)."""
        r = await client.post(
            API.format(wid=seeded_workspace_id),
            json={"name": "Pilot", "custom_imagery": bad_value},
        )
        assert r.status_code == 422

    async def test_get_404_when_missing(
        self, client, as_lead, seeded_workspace_id, fake_repos
    ):
        """GET on a non-existent project id returns 404."""
        r = await client.get(f"{API.format(wid=seeded_workspace_id)}/9999")
        assert r.status_code == 404

    async def test_create_then_get_round_trip(
        self, client, as_lead, seeded_workspace_id, fake_repos
    ):
        """A project created via POST is readable via GET with the same id."""
        r = await client.post(
            API.format(wid=seeded_workspace_id),
            json={"name": "RT"},
        )
        pid = r.json()["id"]

        r2 = await client.get(f"{API.format(wid=seeded_workspace_id)}/{pid}")
        assert r2.status_code == 200
        assert r2.json()["id"] == pid

    async def test_patch_name(self, client, as_lead, seeded_workspace_id, fake_repos):
        """PATCH updates only specified fields — name change is reflected on GET."""
        pid = (
            await client.post(
                API.format(wid=seeded_workspace_id),
                json={"name": "before"},
            )
        ).json()["id"]

        r = await client.patch(
            f"{API.format(wid=seeded_workspace_id)}/{pid}",
            json={"name": "after"},
        )
        assert r.status_code == 200
        assert r.json()["name"] == "after"

    @pytest.mark.parametrize("bad_value", ["not-an-object", 123, False, ["x"]])
    async def test_patch_rejects_non_object_custom_imagery_422(
        self, client, as_lead, seeded_workspace_id, fake_repos, bad_value
    ):
        """PATCH rejects custom_imagery when it is not a JSON object (422)."""
        pid = (
            await client.post(
                API.format(wid=seeded_workspace_id),
                json={"name": "before"},
            )
        ).json()["id"]

        r = await client.patch(
            f"{API.format(wid=seeded_workspace_id)}/{pid}",
            json={"custom_imagery": bad_value},
        )
        assert r.status_code == 422

    async def test_soft_delete_204_then_404(
        self, client, as_lead, seeded_workspace_id, fake_repos
    ):
        """DELETE soft-removes the project; subsequent GET returns 404."""
        pid = (
            await client.post(
                API.format(wid=seeded_workspace_id),
                json={"name": "to-delete"},
            )
        ).json()["id"]

        r = await client.delete(f"{API.format(wid=seeded_workspace_id)}/{pid}")
        assert r.status_code == 204

        r = await client.get(f"{API.format(wid=seeded_workspace_id)}/{pid}")
        assert r.status_code == 404

    async def test_duplicate_name_409(
        self, client, as_lead, seeded_workspace_id, fake_repos
    ):
        """Creating a project with a duplicate name in the same workspace returns 409."""
        await client.post(
            API.format(wid=seeded_workspace_id),
            json={"name": "dup"},
        )
        r = await client.post(
            API.format(wid=seeded_workspace_id),
            json={"name": "dup"},
        )
        assert r.status_code == 409


class TestProjectNameValidation:
    async def test_validate_name_returns_false_when_missing(
        self, client, as_contributor, seeded_workspace_id, fake_repos
    ):
        """Validation returns exists=false when no active project uses the name."""
        r = await client.get(
            f"{API.format(wid=seeded_workspace_id)}/validate-name",
            params={"name": "new-project"},
        )
        assert r.status_code == 200
        assert r.json() == {"exists": False}

    async def test_validate_name_returns_true_when_exists(
        self, client, as_lead, seeded_workspace_id, fake_repos
    ):
        """Validation returns exists=true when an active project with same name exists."""
        await client.post(
            API.format(wid=seeded_workspace_id),
            json={"name": "pilot-check"},
        )

        r = await client.get(
            f"{API.format(wid=seeded_workspace_id)}/validate-name",
            params={"name": "pilot-check"},
        )
        assert r.status_code == 200
        assert r.json() == {"exists": True}

    async def test_validate_name_blank_rejected_422(
        self, client, as_contributor, seeded_workspace_id, fake_repos
    ):
        """Whitespace-only name is rejected with 422."""
        r = await client.get(
            f"{API.format(wid=seeded_workspace_id)}/validate-name",
            params={"name": "   "},
        )
        assert r.status_code == 422

    async def test_validate_name_outsider_404(
        self, client, as_outsider, seeded_workspace_id, fake_repos
    ):
        """Tenancy gate hides workspace existence for outsiders."""
        r = await client.get(
            f"{API.format(wid=seeded_workspace_id)}/validate-name",
            params={"name": "pilot-check"},
        )
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# Lifecycle gates
# ---------------------------------------------------------------------------


class TestLifecycle:
    async def test_activate_fake_always_422(
        self, client, as_lead, seeded_workspace_id, fake_repos
    ):
        """Activate is gated — without tasks it returns 422 (FakeRepo mirrors the real rule)."""
        pid = (
            await client.post(
                API.format(wid=seeded_workspace_id),
                json={"name": "act"},
            )
        ).json()["id"]

        r = await client.post(f"{API.format(wid=seeded_workspace_id)}/{pid}/activate")
        assert r.status_code == 422


# ---------------------------------------------------------------------------
# AOI sub-resource
# ---------------------------------------------------------------------------


class TestAoiRoutes:
    async def test_upload_aoi_polygon(
        self, client, as_lead, seeded_workspace_id, fake_repos
    ):
        """Uploading a Polygon AOI returns a Feature wrapping a MultiPolygon (upcast)."""
        pid = (
            await client.post(
                API.format(wid=seeded_workspace_id),
                json={"name": "aoi"},
            )
        ).json()["id"]

        r = await client.post(
            f"{API.format(wid=seeded_workspace_id)}/{pid}/aoi",
            json={
                "type": "Polygon",
                "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]],
            },
        )
        assert r.status_code == 200
        assert r.json()["geometry"]["type"] == "MultiPolygon"

    async def test_get_aoi_404_when_unset(
        self, client, as_lead, seeded_workspace_id, fake_repos
    ):
        """GET /aoi on a project with no AOI yet returns 404."""
        pid = (
            await client.post(
                API.format(wid=seeded_workspace_id),
                json={"name": "no-aoi"},
            )
        ).json()["id"]

        r = await client.get(f"{API.format(wid=seeded_workspace_id)}/{pid}/aoi")
        assert r.status_code == 404

    async def test_delete_aoi_round_trip(
        self, client, as_lead, seeded_workspace_id, fake_repos
    ):
        """DELETE /aoi clears the AOI; subsequent GET /aoi returns 404."""
        pid = (
            await client.post(
                API.format(wid=seeded_workspace_id),
                json={"name": "to-clear"},
            )
        ).json()["id"]
        await client.post(
            f"{API.format(wid=seeded_workspace_id)}/{pid}/aoi",
            json={
                "type": "Polygon",
                "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]],
            },
        )

        r = await client.delete(f"{API.format(wid=seeded_workspace_id)}/{pid}/aoi")
        assert r.status_code == 204

        r = await client.get(f"{API.format(wid=seeded_workspace_id)}/{pid}/aoi")
        assert r.status_code == 404
