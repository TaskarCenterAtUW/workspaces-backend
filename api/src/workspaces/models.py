import json
from datetime import datetime
from typing import Any, Optional
from uuid import UUID

import requests
from geoalchemy2 import WKBElement
from jsonschema import ValidationError, validate
from pydantic import BaseModel, ConfigDict, Field, Json, field_validator
from typing_extensions import Annotated

from api.core.config import Settings
from api.src.workspaces.schemas import ExternalAppsDefinitionType, QuestDefinitionType

class WorkspaceLongQuestBase(BaseModel):

    workspace_id: int

    definition: Optional[str]
    type: QuestDefinitionType
    url: Optional[str]

    modifiedAt: datetime
    modifiedBy: UUID
    modifiedByName: str

    model_config = ConfigDict(from_attributes=True)

    def validate_definition(self, data, value):
        if QuestDefinitionType[data["type"]] == QuestDefinitionType.NONE:
            if not value:
                return None
            raise ValidationError("'definition' field not allowed.")

        if QuestDefinitionType[data["type"]] != QuestDefinitionType.JSON:
            return value

        if not value:
            raise ValidationError("This field is required.")
        if data["url"]:
            raise ValidationError("'url' field not allowed.")

        try:
            parsed = json.loads(value)
            if not parsed or not isinstance(parsed, dict):
                raise ValidationError("must be a JSON object.")
            validate_json_against_schema(parsed, Settings.WS_LONGFORM_SCHEMA_URL)
        except json.JSONDecodeError as e:
            return ValidationError(f"{e}")
        except ValidationError as e:
            raise ValidationError(f"{e}")

        return value

    def validate_url(self, data, value):
        if QuestDefinitionType[data["type"]] == QuestDefinitionType.NONE:
            if not value:
                return None
            raise ValidationError("'url' field not allowed.")

        if QuestDefinitionType[data["type"]] != QuestDefinitionType.URL:
            return value

        if not value:
            raise ValidationError("This field is required.")
        if data["definition"]:
            raise ValidationError("'definition' field not allowed.")

        return value

class WorkspaceLongQuestUpdate(BaseModel):
    definition: Optional[str]
    url: Optional[str]

class WorkspaceImageryBase(BaseModel):

    workspace_id: int

    # Note the below column is of the JSON *database* type vs string type, so we're not
    # using pydantic's JSON mapping, hence this is not defined as Optional[Json[Any]]
    definition: Optional[list[Any]]

    modifiedAt: datetime
    modifiedBy: UUID
    modifiedByName: str

    model_config = ConfigDict(from_attributes=True)

class WorkspaceImageryUpdate(BaseModel):
    definition: Optional[str]

class WorkspaceBase(BaseModel):

    id: int
    type: str = Field(...)

    title: str = Field(...)
    description: Optional[str]

    tdeiProjectGroupId: UUID
    tdeiRecordId: Optional[UUID]
    tdeiServiceId: Optional[UUID]

    tdeiMetadata: Optional[Json[Any]]

    createdAt: datetime
    createdBy: UUID
    createdByName: str

    geometry: Optional[Annotated[str, WKBElement]]

    externalAppAccess: ExternalAppsDefinitionType

    kartaViewToken: Optional[str]

    longFormQuestDef: Optional[WorkspaceLongQuestBase]

    imageryListDef: Optional[WorkspaceImageryBase]

    model_config = ConfigDict(from_attributes=True)

    # there are some legacy records with '', which is not valid JSON, so map those to None
    @field_validator("*", mode="before")
    @classmethod
    def empty_str_to_none(cls, v):
        if v == "":
            return None
        return v


class WorkspaceCreate(WorkspaceBase):
    pass

class WorkspaceUpdate(BaseModel):
    title: Optional[str] = None 
    description: Optional[str] = None 
    externalAppAccess: Optional[ExternalAppsDefinitionType] = None 
    longFormQuestDef: Optional[WorkspaceLongQuestBase] = None 
    imageryListDef: Optional[WorkspaceImageryBase] = None 

class WorkspaceResponse(WorkspaceBase):
    pass


def validate_json_against_schema(json, schema_url) -> bool:
    """
    Validate a JSON string against a JSON schema from a URL.
    Returns True if valid, raises ValidationError if not.
    """
    # Fetch the schema
    response = requests.get(schema_url)
    response.raise_for_status()
    schema = response.json()

    # Validate
    validate(instance=json, schema=schema)
    return True
