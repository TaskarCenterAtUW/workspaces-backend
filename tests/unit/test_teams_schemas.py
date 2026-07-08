"""Schema tests for api/src/teams/schemas.py.

Covers the @test comments on the table models:
- table name / columns match the DB schema
- foreign keys and relationships are correctly defined
- values serialize/deserialize without loss
"""

import pytest
from pydantic import ValidationError

from api.src.teams.schemas import (
    WorkspaceTeam,
    WorkspaceTeamCreate,
    WorkspaceTeamItem,
    WorkspaceTeamUser,
)
from tests.support import factories


def _table(model):
    # __table__ is added by SQLAlchemy at runtime; getattr keeps type-checkers happy.
    return getattr(model, "__table__")


def _fk_targets(model):
    return {fk.target_fullname for fk in _table(model).foreign_keys}


def test_team_user_link_table_schema():
    table = _table(WorkspaceTeamUser)
    assert table.name == "team_user"
    assert set(table.columns.keys()) == {"team_id", "user_id"}
    assert {c.name for c in table.primary_key.columns} == {"team_id", "user_id"}
    assert _fk_targets(WorkspaceTeamUser) == {"teams.id", "users.id"}


def test_team_table_schema():
    table = _table(WorkspaceTeam)
    assert table.name == "teams"
    assert {"id", "name", "workspace_id"} <= set(table.columns.keys())
    assert [c.name for c in table.primary_key.columns] == ["id"]
    # workspace_id is indexed for lookups by workspace.
    assert table.columns["workspace_id"].index is True


def test_team_has_users_relationship():
    # The many-to-many to User goes through the team_user link model.
    rel = WorkspaceTeam.__sqlmodel_relationships__
    assert "users" in rel


def test_team_item_from_team_round_trip():
    user = factories.make_user(id=1)
    team = factories.make_team(id=7, name="Alpha", users=[user])

    item = WorkspaceTeamItem.from_team(team)

    assert item.id == 7
    assert item.name == "Alpha"
    assert item.member_count == 1
    # Serializes cleanly to a dict for API responses.
    assert item.model_dump() == {"id": 7, "name": "Alpha", "member_count": 1}


def test_team_create_requires_nonempty_name():
    assert WorkspaceTeamCreate(name="ok").name == "ok"
    with pytest.raises(ValidationError):
        WorkspaceTeamCreate(name="")
