from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


API = "/api/v1/workspaces/{wid}/tasking/projects"


# AOI that comfortably contains all task polygons below.
AOI_UNIT_SQUARE = {
    "type": "Polygon",
    "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]],
}

# Small (~1 km²) task polygons that fit inside the AOI and stay below the
# default grid-size warning threshold (5000 m → 25 km²).
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
TASK_C = {
    "type": "Polygon",
    "coordinates": [
        [[0.04, 0.04], [0.05, 0.04], [0.05, 0.05], [0.04, 0.05], [0.04, 0.04]]
    ],
}

# A "fat" polygon (1°×1° ≈ 12 000 km²) — triggers the grid-size warning.
TASK_FAT = AOI_UNIT_SQUARE


def _fc(*polys: dict) -> dict:
    return {
        "type": "FeatureCollection",
        "features": [{"type": "Feature", "geometry": p} for p in polys],
    }


# ---------------------------------------------------------------------------
# Common setup helper — used by most test classes to land a project in the
# ``open`` state with N tasks saved and a contributor + validator allocated.
# ---------------------------------------------------------------------------


async def _create_open_project(
    client,
    workspace_id: int,
    *,
    contributor_uuid,
    validator_uuid,
    task_polygons: list[dict],
    review_required: bool = True,
    name_suffix: str = "",
) -> int:
    """Create -> AOI -> save tasks -> activate. Returns the project id.

    Caller must already be acting as a LEAD (the route is LEAD-only).
    The two role UUIDs satisfy the activation pre-check (≥1 contributor
    or validator). Both users must exist in the OSM ``users`` table.
    """
    name = f"flow-{id(task_polygons)}{name_suffix}"
    r = await client.post(
        API.format(wid=workspace_id),
        json={
            "name": name,
            "review_required": review_required,
            "role_assignments": [
                {"user_id": str(contributor_uuid), "role": "contributor"},
                {"user_id": str(validator_uuid), "role": "validator"},
            ],
        },
    )
    assert r.status_code == 201, r.text
    pid = r.json()["id"]

    r = await client.post(
        f"{API.format(wid=workspace_id)}/{pid}/aoi",
        json=AOI_UNIT_SQUARE,
    )
    assert r.status_code == 200, r.text

    r = await client.post(
        f"{API.format(wid=workspace_id)}/{pid}/tasks/save",
        json={"source": "import", "feature_collection": _fc(*task_polygons)},
    )
    assert r.status_code == 201, r.text

    r = await client.post(f"{API.format(wid=workspace_id)}/{pid}/activate")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "open"
    return pid


# ---------------------------------------------------------------------------
# Workflow 0 — Server-side grid generation.
# ---------------------------------------------------------------------------


class TestGridGeneration:
    """LEAD: upload AOI, generate a grid, post the grid back through /tasks/save."""

    async def test_grid_then_save_round_trip(
        self, client, as_lead, seeded_workspace_id, reset_tasking
    ):
        """Grid generates clipped cells over the AOI; same payload commits via /tasks/save."""
        r = await client.post(
            API.format(wid=seeded_workspace_id),
            json={"name": "grid-flow"},
        )
        pid = r.json()["id"]
        await client.post(
            f"{API.format(wid=seeded_workspace_id)}/{pid}/aoi",
            json=AOI_UNIT_SQUARE,
        )

        # 1 km² cells over a 1° × 1° AOI ≈ 110 × 110 grid → ~12 000 cells.
        # Use 25 km cells (~5x5 grid) so the test stays fast.
        r = await client.post(
            f"{API.format(wid=seeded_workspace_id)}/{pid}/tasks/grid?cell_size_meters=25000"
        )
        assert r.status_code == 200, r.text
        fc = r.json()
        assert fc["type"] == "FeatureCollection"
        assert len(fc["features"]) > 0
        # Every cell is a Polygon and inside the unit-square AOI bounds.
        for feat in fc["features"]:
            assert feat["geometry"]["type"] == "Polygon"
            for ring in feat["geometry"]["coordinates"]:
                for lon, lat in ring:
                    assert 0 <= lon <= 1 and 0 <= lat <= 1

        # Round-trip: hand the same payload to /tasks/save with source=grid.
        r = await client.post(
            f"{API.format(wid=seeded_workspace_id)}/{pid}/tasks/save",
            json={"source": "grid", "feature_collection": fc},
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["task_count"] == len(fc["features"])
        assert body["task_boundary_type"] == "grid"

    async def test_grid_blocked_without_aoi(
        self, client, as_lead, seeded_workspace_id, reset_tasking
    ):
        """Project AOI must be set before /tasks/grid will produce cells."""
        r = await client.post(
            API.format(wid=seeded_workspace_id),
            json={"name": "grid-no-aoi"},
        )
        pid = r.json()["id"]

        r = await client.post(f"{API.format(wid=seeded_workspace_id)}/{pid}/tasks/grid")
        assert r.status_code == 422, r.text
        assert "aoi" in r.json()["detail"].lower()

    async def test_grid_blocked_outside_draft(
        self,
        client,
        as_lead,
        seeded_workspace_id,
        extra_user_factory,
        reset_tasking,
    ):
        """/tasks/grid is rejected once the project leaves draft."""
        contributor = await extra_user_factory("contributor")
        validator = await extra_user_factory("validator")
        pid = await _create_open_project(
            client,
            seeded_workspace_id,
            contributor_uuid=contributor.user_uuid,
            validator_uuid=validator.user_uuid,
            task_polygons=[TASK_A],
            name_suffix="-grid-state",
        )

        r = await client.post(f"{API.format(wid=seeded_workspace_id)}/{pid}/tasks/grid")
        assert r.status_code == 422, r.text
        assert "draft" in r.json()["detail"].lower()

    async def test_grid_multipolygon_straddling_cell_splits(
        self, client, as_lead, seeded_workspace_id, reset_tasking
    ):
        """A cell that straddles two disjoint AOI lobes is split into one Polygon per lobe.

        AOI is two 0.1°×0.1° lobes separated by a 0.1° east-west gap
        (total east-west extent 0.3° ≈ 33 km at the equator). With a
        50 km cell (~0.45°), the entire AOI fits inside a single grid
        cell whose intersection with the AOI is a MultiPolygon of two
        pieces — the endpoint must surface those as two separate
        Polygon features (one per lobe), not a single MultiPolygon.
        """
        two_lobe_aoi = {
            "type": "MultiPolygon",
            "coordinates": [
                # Lobe A — west.
                [[[0.0, 0.0], [0.1, 0.0], [0.1, 0.1], [0.0, 0.1], [0.0, 0.0]]],
                # Lobe B — same latitude band, east of the gap.
                [[[0.2, 0.0], [0.3, 0.0], [0.3, 0.1], [0.2, 0.1], [0.2, 0.0]]],
            ],
        }

        r = await client.post(
            API.format(wid=seeded_workspace_id),
            json={"name": "grid-multipolygon"},
        )
        pid = r.json()["id"]
        r = await client.post(
            f"{API.format(wid=seeded_workspace_id)}/{pid}/aoi",
            json=two_lobe_aoi,
        )
        assert r.status_code == 200

        # 50 km cell ≈ 0.45° — large enough to envelop both lobes
        # (0.3° east-west extent) in a single straddling cell, forcing
        # the MultiPolygon split path.
        r = await client.post(
            f"{API.format(wid=seeded_workspace_id)}/{pid}/tasks/grid?cell_size_meters=50000"
        )
        assert r.status_code == 200, r.text
        fc = r.json()

        assert fc["type"] == "FeatureCollection"
        # The straddling cell produces exactly one Polygon per lobe.
        assert len(fc["features"]) == 2, [f["geometry"] for f in fc["features"]]
        for feat in fc["features"]:
            assert feat["geometry"]["type"] == "Polygon", (
                "straddling cell was not split — got " f"{feat['geometry']['type']}"
            )

        # The two output polygons should align with the two lobes —
        # check each lies entirely within one lobe's x-range.
        lobe_a_xs, lobe_b_xs = [], []
        for feat in fc["features"]:
            xs = [pt[0] for ring in feat["geometry"]["coordinates"] for pt in ring]
            if max(xs) <= 0.11:
                lobe_a_xs.append(xs)
            elif min(xs) >= 0.19:
                lobe_b_xs.append(xs)
        assert len(lobe_a_xs) == 1 and len(lobe_b_xs) == 1, (
            "expected one polygon per lobe, got "
            f"{len(lobe_a_xs)} in lobe A, {len(lobe_b_xs)} in lobe B"
        )

        # Round-trip: the split features must persist cleanly via /tasks/save.
        r = await client.post(
            f"{API.format(wid=seeded_workspace_id)}/{pid}/tasks/save",
            json={"source": "grid", "feature_collection": fc},
        )
        assert r.status_code == 201, r.text
        assert r.json()["task_count"] == 2

    async def test_grid_contributor_forbidden(
        self,
        client,
        as_contributor,
        seeded_workspace_id,
        reset_tasking,
    ):
        """/tasks/grid is LEAD-only — contributors get 403 (no project needed for the gate)."""
        r = await client.post(
            f"{API.format(wid=seeded_workspace_id)}/999999/tasks/grid"
        )
        assert r.status_code == 403, r.text


# ---------------------------------------------------------------------------
# Workflow 1 — Validate + Save round-trip.
# ---------------------------------------------------------------------------


class TestValidateAndSave:
    """LEAD: upload AOI, validate, save, list, get a task."""

    project_id: int | None = None

    async def test_01_create_draft_with_aoi(self, client, as_lead, seeded_workspace_id):
        """Create a draft project and upload the project AOI."""
        r = await client.post(
            API.format(wid=seeded_workspace_id),
            json={"name": "validate-save"},
        )
        assert r.status_code == 201, r.text
        TestValidateAndSave.project_id = r.json()["id"]

        r = await client.post(
            f"{API.format(wid=seeded_workspace_id)}/{self.project_id}/aoi",
            json=AOI_UNIT_SQUARE,
        )
        assert r.status_code == 200, r.text

    async def test_02_validate_inside_aoi(self, client, as_lead, seeded_workspace_id):
        """Two in-AOI polygons validate cleanly with no warnings."""
        r = await client.post(
            f"{API.format(wid=seeded_workspace_id)}/{self.project_id}/tasks/validate",
            json=_fc(TASK_A, TASK_B),
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["valid"] is True
        assert body["warnings"] == []
        assert body["source"] == "import"

    async def test_03_validate_polygon_outside_aoi_rejected(
        self, client, as_lead, seeded_workspace_id
    ):
        """A polygon outside the project AOI is rejected with 422."""
        outside = {
            "type": "Polygon",
            "coordinates": [[[5, 5], [5.1, 5], [5.1, 5.1], [5, 5.1], [5, 5]]],
        }
        r = await client.post(
            f"{API.format(wid=seeded_workspace_id)}/{self.project_id}/tasks/validate",
            json=_fc(outside),
        )
        assert r.status_code == 422, r.text

    async def test_04_validate_oversize_warns(
        self, client, as_lead, seeded_workspace_id
    ):
        """A polygon larger than the grid-size threshold emits a warning but stays valid."""
        r = await client.post(
            f"{API.format(wid=seeded_workspace_id)}/{self.project_id}/tasks/validate",
            json=_fc(TASK_FAT),
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["valid"] is True
        assert len(body["warnings"]) == 1
        assert body["warnings"][0]["issue"] == "polygon_exceeds_grid_size"
        assert body["warnings"][0]["task_index"] == 0

    async def test_05_save_persists_two_tasks(
        self, client, as_lead, seeded_workspace_id
    ):
        """Save round-trips: returns task count, sequential numbering, sets boundary type."""
        r = await client.post(
            f"{API.format(wid=seeded_workspace_id)}/{self.project_id}/tasks/save",
            json={"source": "import", "feature_collection": _fc(TASK_A, TASK_B)},
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["task_count"] == 2
        assert body["task_boundary_type"] == "import"
        assert [t["task_number"] for t in body["tasks"]] == [1, 2]
        # First task starts in to_map with no lock and no last_mapper.
        assert body["tasks"][0]["status"] == "to_map"
        assert body["tasks"][0]["lock"] is None
        assert body["tasks"][0]["last_mapper"] is None

    async def test_06_double_save_rejected(self, client, as_lead, seeded_workspace_id):
        """A second save into a project that already has tasks 409s."""
        r = await client.post(
            f"{API.format(wid=seeded_workspace_id)}/{self.project_id}/tasks/save",
            json={"source": "import", "feature_collection": _fc(TASK_A)},
        )
        assert r.status_code == 409, r.text

    async def test_07_list_tasks_returns_geometry(
        self, client, as_lead, seeded_workspace_id
    ):
        """GET /tasks paginates and always includes geometry."""
        r = await client.get(
            f"{API.format(wid=seeded_workspace_id)}/{self.project_id}/tasks"
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["pagination"]["total"] == 2
        assert len(body["tasks"]) == 2
        assert body["tasks"][0]["geometry"]["type"] == "Polygon"

    async def test_08_get_single_task(self, client, as_lead, seeded_workspace_id):
        """GET /tasks/{n} returns one task with geometry + metadata."""
        r = await client.get(
            f"{API.format(wid=seeded_workspace_id)}/{self.project_id}/tasks/1"
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["task_number"] == 1
        assert body["status"] == "to_map"

    async def test_09_aoi_replace_wipes_tasks(
        self, client, as_lead, seeded_workspace_id
    ):
        """Re-uploading the AOI hard-deletes existing tasks (matches spec)."""
        r = await client.post(
            f"{API.format(wid=seeded_workspace_id)}/{self.project_id}/aoi",
            json=AOI_UNIT_SQUARE,
        )
        assert r.status_code == 200

        r = await client.get(
            f"{API.format(wid=seeded_workspace_id)}/{self.project_id}/tasks"
        )
        assert r.status_code == 200
        assert r.json()["pagination"]["total"] == 0


# ---------------------------------------------------------------------------
# Workflow 2 — Idempotent save.
# ---------------------------------------------------------------------------


class TestSaveIdempotency:
    """Idempotency-Key header: replay vs. key-reuse-with-different-body."""

    async def test_idempotent_save_lifecycle(
        self, client, as_lead, seeded_workspace_id, reset_tasking
    ):
        """Same key + body → 200 replayed; same key + different body → 409."""
        r = await client.post(
            API.format(wid=seeded_workspace_id),
            json={"name": "idem"},
        )
        pid = r.json()["id"]
        await client.post(
            f"{API.format(wid=seeded_workspace_id)}/{pid}/aoi",
            json=AOI_UNIT_SQUARE,
        )

        body_a = {"source": "import", "feature_collection": _fc(TASK_A)}
        body_b = {"source": "import", "feature_collection": _fc(TASK_B)}
        key = "idem-key-001"

        r1 = await client.post(
            f"{API.format(wid=seeded_workspace_id)}/{pid}/tasks/save",
            json=body_a,
            headers={"Idempotency-Key": key},
        )
        assert r1.status_code == 201
        first_tasks = r1.json()["tasks"]

        # Replay: same key + same body returns 200 + replayed=true + same payload.
        r2 = await client.post(
            f"{API.format(wid=seeded_workspace_id)}/{pid}/tasks/save",
            json=body_a,
            headers={"Idempotency-Key": key},
        )
        assert r2.status_code == 200, r2.text
        assert r2.json()["replayed"] is True
        assert r2.json()["tasks"] == first_tasks

        # Same key with a different body → 409.
        r3 = await client.post(
            f"{API.format(wid=seeded_workspace_id)}/{pid}/tasks/save",
            json=body_b,
            headers={"Idempotency-Key": key},
        )
        assert r3.status_code == 409, r3.text


# ---------------------------------------------------------------------------
# Workflow 3 — Lock acquire / release / extend / force-release / one-per-user.
# ---------------------------------------------------------------------------


class TestLockLifecycle:
    """Lock acquire, extend, release; one-active-lock-per-user-per-project."""

    project_id: int | None = None
    contributor = None
    validator = None

    async def test_01_setup_open_project(
        self,
        client,
        as_lead,
        seeded_workspace_id,
        extra_user_factory,
    ):
        """Set up a project with 3 tasks + contributor + validator, then activate."""
        contributor = await extra_user_factory("contributor")
        validator = await extra_user_factory("validator")
        TestLockLifecycle.contributor = contributor
        TestLockLifecycle.validator = validator

        TestLockLifecycle.project_id = await _create_open_project(
            client,
            seeded_workspace_id,
            contributor_uuid=contributor.user_uuid,
            validator_uuid=validator.user_uuid,
            task_polygons=[TASK_A, TASK_B, TASK_C],
            name_suffix="-lock",
        )

    async def test_02_contributor_locks_task_1(
        self, client, override_user, seeded_workspace_id
    ):
        """Contributor acquires the lock on task 1 — task now reports the lock."""
        override_user(self.contributor)
        r = await client.post(
            f"{API.format(wid=seeded_workspace_id)}/{self.project_id}/tasks/1/lock"
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["lock"] is not None
        assert body["lock"]["user_id"] == str(self.contributor.user_uuid)

    async def test_03_contributor_cannot_lock_second_task(
        self, client, override_user, seeded_workspace_id
    ):
        """One-active-lock-per-user-per-project: locking task 2 returns 409 with existing-lock summary."""
        override_user(self.contributor)
        r = await client.post(
            f"{API.format(wid=seeded_workspace_id)}/{self.project_id}/tasks/2/lock"
        )
        assert r.status_code == 409, r.text
        detail = r.json()["detail"]
        assert detail["existing_lock"]["task_number"] == 1

    async def test_04_another_contributor_cannot_lock_task_1(
        self,
        client,
        override_user,
        seeded_workspace_id,
        extra_user_factory,
    ):
        """A different contributor cannot lock a task that is already locked (409)."""
        other = await extra_user_factory("contributor")
        override_user(other)
        r = await client.post(
            f"{API.format(wid=seeded_workspace_id)}/{self.project_id}/tasks/1/lock"
        )
        assert r.status_code == 409, r.text

    async def test_05_extend_slides_expiry(
        self, client, override_user, seeded_workspace_id
    ):
        """The lock holder can extend; expires_at moves forward."""
        override_user(self.contributor)
        before = await client.get(
            f"{API.format(wid=seeded_workspace_id)}/{self.project_id}/tasks/1"
        )
        expires_before = before.json()["lock"]["expires_at"]

        r = await client.post(
            f"{API.format(wid=seeded_workspace_id)}/{self.project_id}/tasks/1/extend"
        )
        assert r.status_code == 200, r.text
        assert r.json()["lock"]["expires_at"] > expires_before

    async def test_06_release_own_lock(
        self, client, override_user, seeded_workspace_id
    ):
        """Caller releases their own lock — 204, lock gone on the task."""
        override_user(self.contributor)
        r = await client.delete(
            f"{API.format(wid=seeded_workspace_id)}/{self.project_id}/tasks/1/lock"
        )
        assert r.status_code == 204

        r = await client.get(
            f"{API.format(wid=seeded_workspace_id)}/{self.project_id}/tasks/1"
        )
        assert r.json()["lock"] is None

    async def test_07_force_release_requires_lead(
        self,
        client,
        override_user,
        seeded_workspace_id,
    ):
        """force=true is LEAD-only; contributor gets 403 even for someone else's lock."""
        # Contributor re-locks task 1 first.
        override_user(self.contributor)
        r = await client.post(
            f"{API.format(wid=seeded_workspace_id)}/{self.project_id}/tasks/1/lock"
        )
        assert r.status_code == 200

        # Validator tries to force-release (not a workspace LEAD) → 403.
        override_user(self.validator)
        r = await client.delete(
            f"{API.format(wid=seeded_workspace_id)}/{self.project_id}/tasks/1/lock?force=true"
        )
        assert r.status_code == 403, r.text

    async def test_08_lead_force_release_succeeds(
        self, client, as_lead, seeded_workspace_id
    ):
        """LEAD force-releases the contributor's lock; release_reason='lead_release'."""
        r = await client.delete(
            f"{API.format(wid=seeded_workspace_id)}/{self.project_id}/tasks/1/lock?force=true"
        )
        assert r.status_code == 204
        r = await client.get(
            f"{API.format(wid=seeded_workspace_id)}/{self.project_id}/tasks/1"
        )
        assert r.json()["lock"] is None

    async def test_09_unlock_without_active_lock_is_409(
        self, client, override_user, seeded_workspace_id
    ):
        """Releasing an unlocked task returns 409."""
        override_user(self.contributor)
        r = await client.delete(
            f"{API.format(wid=seeded_workspace_id)}/{self.project_id}/tasks/1/lock"
        )
        assert r.status_code == 409, r.text


# ---------------------------------------------------------------------------
# Workflow 4 — Submit "Done?" flow through review.
# ---------------------------------------------------------------------------


class TestSubmitReviewFlow:
    """to_map -> contributor submit -> to_review -> validator submit -> completed."""

    project_id: int | None = None
    contributor = None
    validator = None

    async def test_01_setup_open_project(
        self,
        client,
        as_lead,
        seeded_workspace_id,
        extra_user_factory,
    ):
        """Set up an open project with one task + a contributor and validator."""
        contributor = await extra_user_factory("contributor")
        validator = await extra_user_factory("validator")
        TestSubmitReviewFlow.contributor = contributor
        TestSubmitReviewFlow.validator = validator
        TestSubmitReviewFlow.project_id = await _create_open_project(
            client,
            seeded_workspace_id,
            contributor_uuid=contributor.user_uuid,
            validator_uuid=validator.user_uuid,
            task_polygons=[TASK_A],
            name_suffix="-submit",
        )

    async def test_02_contributor_lock_and_submit_done(
        self, client, override_user, seeded_workspace_id
    ):
        """Contributor locks task 1 then submits done=true → status becomes to_review, lock auto-released."""
        override_user(self.contributor)
        r = await client.post(
            f"{API.format(wid=seeded_workspace_id)}/{self.project_id}/tasks/1/lock"
        )
        assert r.status_code == 200

        r = await client.post(
            f"{API.format(wid=seeded_workspace_id)}/{self.project_id}/tasks/1/submit",
            json={"osm_changeset_id": 1001, "done": True},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "to_review"
        assert body["lock"] is None
        assert body["last_mapper"]["user_id"] == str(self.contributor.user_uuid)

    async def test_03_contributor_cannot_lock_for_review(
        self, client, override_user, seeded_workspace_id
    ):
        """to_review tasks reject contributor-role lock attempts (validator/lead only)."""
        override_user(self.contributor)
        r = await client.post(
            f"{API.format(wid=seeded_workspace_id)}/{self.project_id}/tasks/1/lock"
        )
        assert r.status_code == 403, r.text

    async def test_04_validator_locks_to_review(
        self, client, override_user, seeded_workspace_id
    ):
        """Validator can lock a to_review task they did not last map."""
        override_user(self.validator)
        r = await client.post(
            f"{API.format(wid=seeded_workspace_id)}/{self.project_id}/tasks/1/lock"
        )
        assert r.status_code == 200, r.text

    async def test_05_validator_submit_done_no_feedback_completes(
        self, client, override_user, seeded_workspace_id
    ):
        """Validator submit done=true + no feedback → status=completed."""
        override_user(self.validator)
        r = await client.post(
            f"{API.format(wid=seeded_workspace_id)}/{self.project_id}/tasks/1/submit",
            json={"osm_changeset_id": 1002, "done": True},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "completed"
        assert body["lock"] is None


# ---------------------------------------------------------------------------
# Workflow 5 — Submit done=false slides the lock; status unchanged.
# ---------------------------------------------------------------------------


class TestSubmitDoneFalseSlides:
    async def test_submit_done_false_slides_expiry(
        self,
        client,
        as_lead,
        seeded_workspace_id,
        extra_user_factory,
        override_user,
        reset_tasking,
    ):
        """done=false: state unchanged, lock expires_at slides to NOW + lock_timeout_hours."""
        contributor = await extra_user_factory("contributor")
        validator = await extra_user_factory("validator")
        pid = await _create_open_project(
            client,
            seeded_workspace_id,
            contributor_uuid=contributor.user_uuid,
            validator_uuid=validator.user_uuid,
            task_polygons=[TASK_A],
            name_suffix="-slide",
        )

        override_user(contributor)
        r = await client.post(
            f"{API.format(wid=seeded_workspace_id)}/{pid}/tasks/1/lock"
        )
        assert r.status_code == 200
        expiry_before = r.json()["lock"]["expires_at"]

        r = await client.post(
            f"{API.format(wid=seeded_workspace_id)}/{pid}/tasks/1/submit",
            json={"osm_changeset_id": 5001, "done": False},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "to_map"  # unchanged
        assert body["lock"] is not None  # still locked
        # New expiry is at-or-after submitted_at + lock_timeout, so > original.
        assert body["lock"]["expires_at"] >= expiry_before


# ---------------------------------------------------------------------------
# Workflow 6 — Validator-feedback remap loop.
# ---------------------------------------------------------------------------


class TestRemapFlow:
    async def test_remap_loop(
        self,
        client,
        as_lead,
        seeded_workspace_id,
        extra_user_factory,
        override_user,
        reset_tasking,
    ):
        """to_review + feedback → to_remap; feedback row is persisted."""
        contributor = await extra_user_factory("contributor")
        validator = await extra_user_factory("validator")
        pid = await _create_open_project(
            client,
            seeded_workspace_id,
            contributor_uuid=contributor.user_uuid,
            validator_uuid=validator.user_uuid,
            task_polygons=[TASK_A],
            name_suffix="-remap",
        )

        # Contributor maps → to_review.
        override_user(contributor)
        await client.post(f"{API.format(wid=seeded_workspace_id)}/{pid}/tasks/1/lock")
        r = await client.post(
            f"{API.format(wid=seeded_workspace_id)}/{pid}/tasks/1/submit",
            json={"osm_changeset_id": 7001, "done": True},
        )
        assert r.json()["status"] == "to_review"

        # Validator validates with feedback → to_remap.
        override_user(validator)
        await client.post(f"{API.format(wid=seeded_workspace_id)}/{pid}/tasks/1/lock")
        r = await client.post(
            f"{API.format(wid=seeded_workspace_id)}/{pid}/tasks/1/submit",
            json={
                "osm_changeset_id": 7002,
                "done": True,
                "feedback": {
                    "reason_category": "incomplete_mapping",
                    "notes": "Please finish the missing footways on the north side.",
                },
            },
        )
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "to_remap"
        assert r.json()["lock"] is None

        # Contributor re-locks the remapped task (allowed on to_remap).
        override_user(contributor)
        r = await client.post(
            f"{API.format(wid=seeded_workspace_id)}/{pid}/tasks/1/lock"
        )
        assert r.status_code == 200, r.text


# ---------------------------------------------------------------------------
# Workflow 7 — Self-validation guard.
# ---------------------------------------------------------------------------


class TestSelfValidationGuard:
    async def test_validator_cannot_validate_own_last_mapping(
        self,
        client,
        as_lead,
        seeded_workspace_id,
        extra_user_factory,
        override_user,
        reset_tasking,
    ):
        """A validator who is also the task's last_mapper cannot lock to_review."""
        validator = await extra_user_factory("validator")
        # Need a second worker to satisfy the activation pre-check; the
        # validator is doing double-duty (validator + mapper).
        contributor = await extra_user_factory("contributor")
        pid = await _create_open_project(
            client,
            seeded_workspace_id,
            contributor_uuid=contributor.user_uuid,
            validator_uuid=validator.user_uuid,
            task_polygons=[TASK_A],
            name_suffix="-self",
        )

        # Validator maps the task themselves (validators can also lock to_map).
        override_user(validator)
        await client.post(f"{API.format(wid=seeded_workspace_id)}/{pid}/tasks/1/lock")
        await client.post(
            f"{API.format(wid=seeded_workspace_id)}/{pid}/tasks/1/submit",
            json={"osm_changeset_id": 9001, "done": True},
        )

        # Now they try to validate their own work → 403.
        r = await client.post(
            f"{API.format(wid=seeded_workspace_id)}/{pid}/tasks/1/lock"
        )
        assert r.status_code == 403, r.text


# ---------------------------------------------------------------------------
# Workflow 8 — Per-task reset by LEAD.
# ---------------------------------------------------------------------------


class TestTaskReset:
    async def test_reset_releases_lock_and_resets_status(
        self,
        client,
        as_lead,
        seeded_workspace_id,
        extra_user_factory,
        override_user,
        reset_tasking,
    ):
        """LEAD reset on a locked to_review task: lock cleared, status back to to_map."""
        contributor = await extra_user_factory("contributor")
        validator = await extra_user_factory("validator")
        pid = await _create_open_project(
            client,
            seeded_workspace_id,
            contributor_uuid=contributor.user_uuid,
            validator_uuid=validator.user_uuid,
            task_polygons=[TASK_A],
            name_suffix="-rst",
        )

        # Contributor maps → to_review.
        override_user(contributor)
        await client.post(f"{API.format(wid=seeded_workspace_id)}/{pid}/tasks/1/lock")
        await client.post(
            f"{API.format(wid=seeded_workspace_id)}/{pid}/tasks/1/submit",
            json={"osm_changeset_id": 11001, "done": True},
        )
        # Validator picks it up.
        override_user(validator)
        await client.post(f"{API.format(wid=seeded_workspace_id)}/{pid}/tasks/1/lock")

        # Switch back to a LEAD token to invoke /reset. The integration
        # `as_lead` fixture already inserted a lead users row, so the
        # helper here just builds a UserInfo to bind to the override.
        from api.core.security import validate_token
        from api.main import app
        from tests.conftest import SEED_PROJECT_GROUP_ID, _make_user

        lead = _make_user(
            role="lead",
            workspace_id=seeded_workspace_id,
            pg_id=SEED_PROJECT_GROUP_ID,
        )
        app.dependency_overrides[validate_token] = lambda: lead

        r = await client.post(
            f"{API.format(wid=seeded_workspace_id)}/{pid}/tasks/1/reset"
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "to_map"
        assert body["lock"] is None
        assert body["last_mapper"] is None
