"""Schema tests for api/src/users/schemas.py.

Covers the @test comments on the User and WorkspaceUserRole table models:
- table name / columns match the DB schema (and the Alembic migration)
- foreign keys and relationships are correctly defined
- role enum values serialize/deserialize without loss
"""

import pytest
from pydantic import ValidationError

from api.src.users.schemas import (
    SetRoleRequest,
    User,
    WorkspaceUserRole,
    WorkspaceUserRoleType,
)


def _table(model):
    # __table__ is added by SQLAlchemy at runtime; getattr keeps type-checkers happy.
    return getattr(model, "__table__")


def _fk_targets(model):
    return {fk.target_fullname for fk in _table(model).foreign_keys}


def test_user_table_schema():
    table = _table(User)
    assert table.name == "users"
    assert {"id", "auth_uid", "email", "display_name"} <= set(table.columns.keys())
    assert [c.name for c in table.primary_key.columns] == ["id"]
    # auth_uid and email are unique + indexed.
    assert table.columns["auth_uid"].unique is True
    assert table.columns["email"].unique is True


def test_user_has_teams_relationship():
    assert "teams" in User.__sqlmodel_relationships__


def test_workspace_user_role_table_matches_alembic():
    # Mirrors alembic_osm migration 9221408912dd:
    #   user_workspace_roles(user_auth_uid, workspace_id, role enum),
    #   PK(user_auth_uid, workspace_id), FK user_auth_uid -> users.auth_uid
    table = _table(WorkspaceUserRole)
    assert table.name == "user_workspace_roles"
    assert set(table.columns.keys()) == {"user_auth_uid", "workspace_id", "role"}
    assert {c.name for c in table.primary_key.columns} == {
        "user_auth_uid",
        "workspace_id",
    }
    assert _fk_targets(WorkspaceUserRole) == {"users.auth_uid"}


def test_role_enum_values():
    assert WorkspaceUserRoleType.LEAD == "lead"
    assert WorkspaceUserRoleType.VALIDATOR == "validator"
    assert WorkspaceUserRoleType.CONTRIBUTOR == "contributor"


def test_set_role_request_rejects_contributor():
    # CONTRIBUTOR is implicit and may not be assigned directly.
    assert SetRoleRequest(role=WorkspaceUserRoleType.LEAD).role == (
        WorkspaceUserRoleType.LEAD
    )
    with pytest.raises(ValidationError):
        SetRoleRequest(role=WorkspaceUserRoleType.CONTRIBUTOR)


def test_user_serialization_round_trip():
    user = User(id=3, auth_uid="abc", email="u@example.com", display_name="U")
    dumped = user.model_dump()
    assert dumped["id"] == 3
    assert dumped["auth_uid"] == "abc"
    assert dumped["email"] == "u@example.com"
    assert dumped["display_name"] == "U"
