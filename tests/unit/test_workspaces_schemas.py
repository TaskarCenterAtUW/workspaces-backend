"""Schema tests for api/src/workspaces/schemas.py.

Covers the @test comments on the table models and WorkspaceResponse:
- table names / columns / PKs / FKs match the DB schema
- relationships are correctly defined
- enum TypeDecorators serialize to the DB and back without loss
- UUID / datetime values round-trip without precision loss
- WorkspaceResponse.from_workspace serializes for API responses incl. role
"""

from datetime import datetime
from uuid import UUID

import pytest
from pydantic import ValidationError
from sqlalchemy.engine.default import DefaultDialect

from api.src.users.schemas import WorkspaceUserRoleType
from api.src.workspaces.schemas import (
    ExternalAppsDefinitionType,
    IntEnumType,
    QuestDefinitionType,
    QuestDefinitionTypeName,
    QuestSettingsPatch,
    StrEnumType,
    Workspace,
    WorkspaceImagery,
    WorkspaceLongQuest,
    WorkspaceResponse,
    WorkspaceType,
)
from tests.support import factories

_DIALECT = DefaultDialect()


def _table(model):
    # __table__ is added by SQLAlchemy at runtime; getattr keeps type-checkers happy.
    return getattr(model, "__table__")


def _fk_targets(model):
    return {fk.target_fullname for fk in _table(model).foreign_keys}


# --- table schemas ---------------------------------------------------------


def test_workspace_table_schema():
    table = _table(Workspace)
    assert table.name == "workspaces"
    expected = {
        "id",
        "type",
        "title",
        "description",
        "tdeiProjectGroupId",
        "tdeiRecordId",
        "tdeiServiceId",
        "tdeiMetadata",
        "createdAt",
        "createdBy",
        "createdByName",
        "updatedAt",
        "geometry",
        "externalAppAccess",
        "kartaViewToken",
    }
    assert expected <= set(table.columns.keys())
    assert [c.name for c in table.primary_key.columns] == ["id"]


def test_workspace_relationships_defined():
    rels = Workspace.__sqlmodel_relationships__
    assert "longFormQuestDef" in rels
    assert "imageryListDef" in rels


def test_long_quest_table_schema():
    table = _table(WorkspaceLongQuest)
    assert table.name == "workspaces_long_quests"
    assert {"workspace_id", "type", "definition", "url", "modifiedAt"} <= set(
        table.columns.keys()
    )
    assert [c.name for c in table.primary_key.columns] == ["workspace_id"]
    assert _fk_targets(WorkspaceLongQuest) == {"workspaces.id"}


def test_imagery_table_schema():
    table = _table(WorkspaceImagery)
    assert table.name == "workspaces_imagery"
    assert {"workspace_id", "definition", "modifiedAt"} <= set(table.columns.keys())
    assert [c.name for c in table.primary_key.columns] == ["workspace_id"]
    assert _fk_targets(WorkspaceImagery) == {"workspaces.id"}


# --- enum TypeDecorator round-trips (Python <-> DB) ------------------------


def test_int_enum_type_round_trip():
    deco = IntEnumType(QuestDefinitionType)
    # bind: enum -> int
    assert deco.process_bind_param(QuestDefinitionType.JSON, _DIALECT) == 1
    # result: int -> enum
    assert deco.process_result_value(1, _DIALECT) == QuestDefinitionType.JSON
    # None passes through both directions
    assert deco.process_bind_param(None, _DIALECT) is None
    assert deco.process_result_value(None, _DIALECT) is None


def test_str_enum_type_round_trip():
    deco = StrEnumType(WorkspaceType)
    assert deco.process_bind_param(WorkspaceType.OSW, _DIALECT) == "osw"
    assert deco.process_result_value("osw", _DIALECT) == WorkspaceType.OSW
    assert deco.process_bind_param(None, _DIALECT) is None
    assert deco.process_result_value(None, _DIALECT) is None


def test_external_apps_enum_round_trip():
    deco = IntEnumType(ExternalAppsDefinitionType)
    assert deco.process_bind_param(ExternalAppsDefinitionType.PUBLIC, _DIALECT) == 1
    assert (
        deco.process_result_value(2, _DIALECT)
        == ExternalAppsDefinitionType.PROJECT_GROUP
    )


# --- value preservation ----------------------------------------------------


def test_workspace_preserves_uuid_and_datetime():
    pg = UUID("11111111-1111-1111-1111-111111111111")
    created = datetime(2026, 1, 2, 3, 4, 5)
    ws = Workspace(
        id=1,
        type=WorkspaceType.OSW,
        title="T",
        tdeiProjectGroupId=pg,
        createdBy=pg,
        createdByName="N",
        createdAt=created,
        updatedAt=created,
    )
    assert ws.tdeiProjectGroupId == pg
    assert ws.createdAt == created  # no truncation
    assert ws.type == WorkspaceType.OSW


# --- WorkspaceResponse serialization --------------------------------------


def test_workspace_response_includes_effective_role():
    user = factories.make_user_info(
        osm_workspace_roles={1: [WorkspaceUserRoleType.LEAD]}
    )
    ws = factories.make_workspace(id=1, title="Mappy")

    resp = WorkspaceResponse.from_workspace(ws, user)

    assert resp.id == 1
    assert resp.title == "Mappy"
    assert resp.role == WorkspaceUserRoleType.LEAD
    assert resp.type == WorkspaceType.OSW
    assert resp.updatedAt == ws.updatedAt
    assert resp.projectsCount == 0
    assert resp.membersCount == 0


def test_workspace_response_includes_counts():
    user = factories.make_user_info()
    ws = factories.make_workspace(id=3)

    resp = WorkspaceResponse.from_workspace(ws, user, projects_count=5, members_count=2)

    assert resp.projectsCount == 5
    assert resp.membersCount == 2


def test_workspace_response_updated_at_falls_back_to_created_at():
    # Rows written before the updatedAt column existed read back as None --
    # the response should report createdAt for those rather than null.
    user = factories.make_user_info()
    created = datetime(2026, 1, 2, 3, 4, 5)
    ws = factories.make_workspace(id=4, updatedAt=None, createdAt=created)

    resp = WorkspaceResponse.from_workspace(ws, user)

    assert resp.updatedAt == created


def test_workspace_response_passes_through_defs():
    user = factories.make_user_info()
    ws = factories.make_workspace(id=2)

    resp = WorkspaceResponse.from_workspace(
        ws, user, imagery_list_def=[{"a": 1}], long_form_quest_def={"q": 2}
    )

    assert resp.imageryListDef == [{"a": 1}]
    assert resp.longFormQuestDef == {"q": 2}
    assert resp.role == WorkspaceUserRoleType.CONTRIBUTOR


# --- QuestSettingsPatch validation ----------------------------------------


def test_quest_settings_json_requires_object_definition():
    ok = QuestSettingsPatch(type=QuestDefinitionTypeName.JSON, definition='{"a": 1}')
    assert ok.type == QuestDefinitionTypeName.JSON

    with pytest.raises(ValidationError):
        QuestSettingsPatch(type=QuestDefinitionTypeName.JSON, definition=None)
    with pytest.raises(ValidationError):
        QuestSettingsPatch(type=QuestDefinitionTypeName.JSON, definition="not-json")


def test_quest_settings_url_requires_url():
    ok = QuestSettingsPatch(type=QuestDefinitionTypeName.URL, url="https://x")
    assert ok.url == "https://x"
    with pytest.raises(ValidationError):
        QuestSettingsPatch(type=QuestDefinitionTypeName.URL, url=None)


def test_quest_settings_none_rejects_payload():
    assert QuestSettingsPatch(type=QuestDefinitionTypeName.NONE).type == (
        QuestDefinitionTypeName.NONE
    )
    with pytest.raises(ValidationError):
        QuestSettingsPatch(type=QuestDefinitionTypeName.NONE, definition='{"a":1}')
