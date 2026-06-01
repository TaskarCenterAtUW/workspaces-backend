

from __future__ import annotations

import pytest
from pydantic import ValidationError

from api.src.tasking.projects.dtos import (
    ProjectCreateRequest,
    ProjectRoleAssignment,
    ProjectUpdateRequest,
)


# ---------------------------------------------------------------------------
# ProjectCreateRequest
# ---------------------------------------------------------------------------


class TestProjectCreateRequest:
    def test_minimal_body_accepted(self):
        """A body with just `name` populates defaults (review_required=True, lock_timeout=8h)."""
        m = ProjectCreateRequest(name="hello")
        assert m.name == "hello"
        assert m.review_required is True
        assert m.lock_timeout_hours == 8
        assert m.aoi is None
        assert m.role_assignments == []

    def test_name_blank_rejected(self):
        """Whitespace-only project names are rejected with a clear 'blank' error."""
        with pytest.raises(ValidationError) as exc:
            ProjectCreateRequest(name="   ")
        assert "blank" in str(exc.value).lower()

    def test_name_too_long_rejected(self):
        """Project names longer than 255 characters are rejected."""
        with pytest.raises(ValidationError):
            ProjectCreateRequest(name="x" * 256)

    @pytest.mark.parametrize("hours", [0, -1, 721])
    def test_lock_timeout_out_of_range_rejected(self, hours):
        """lock_timeout_hours must be in [1, 720]; out-of-range values are rejected."""
        with pytest.raises(ValidationError):
            ProjectCreateRequest(name="ok", lock_timeout_hours=hours)

    @pytest.mark.parametrize("hours", [1, 8, 720])
    def test_lock_timeout_in_range_accepted(self, hours):
        """lock_timeout_hours boundary values (1, default, 720) are accepted."""
        m = ProjectCreateRequest(name="ok", lock_timeout_hours=hours)
        assert m.lock_timeout_hours == hours

    def test_instructions_too_long_rejected(self):
        """Instructions over 10,000 characters are rejected."""
        with pytest.raises(ValidationError):
            ProjectCreateRequest(name="ok", instructions="x" * 10_001)


# ---------------------------------------------------------------------------
# ProjectUpdateRequest
# ---------------------------------------------------------------------------


class TestProjectUpdateRequest:
    def test_all_fields_optional(self):
        """An empty PATCH body is valid — every field is optional."""
        m = ProjectUpdateRequest()
        assert m.name is None
        assert m.instructions is None
        assert m.lock_timeout_hours is None
        assert m.review_required is None

    def test_partial_update(self):
        """Only specified fields are populated; the rest stay None."""
        m = ProjectUpdateRequest(name="x")
        assert m.name == "x"
        assert m.review_required is None


# ---------------------------------------------------------------------------
# ProjectRoleAssignment
# ---------------------------------------------------------------------------


class TestProjectRoleAssignment:
    def test_valid_roles(self):
        """Each of the three role strings ('lead', 'validator', 'contributor') is accepted."""
        from uuid import uuid4

        for role in ("lead", "validator", "contributor"):
            m = ProjectRoleAssignment(user_id=uuid4(), role=role)
            assert m.role == role

    def test_invalid_role_rejected(self):
        """Unknown role strings (e.g. 'admin') are rejected by the Literal."""
        from uuid import uuid4

        with pytest.raises(ValidationError):
            ProjectRoleAssignment(user_id=uuid4(), role="admin")
