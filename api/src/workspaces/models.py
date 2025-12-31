from enum import Enum
import json
from typing import Any, Optional
from uuid import UUID

import requests
from geoalchemy2 import WKBElement
from jsonschema import ValidationError, validate
from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, Json, field_serializer, field_validator
from typing_extensions import Annotated

from api.core.config import Settings
from api.src.workspaces.schemas import ExternalAppsDefinitionType, QuestDefinitionTypeDB

#
# These are DTOs used by the API to pass values to/from the client, NOT the schema definitions
#

class QuestDefinitionTypeModel(Enum):
    NONE = "NONE"
    JSON = "JSON"
    URL = "URL"

# map the value from the database enum to the model enum if necessary
# FIXME: this hack is necessary because the UI and the DB don't use the same values, fix that?
def map_db_to_model(value) -> QuestDefinitionTypeModel:
    if value in QuestDefinitionTypeDB:
        return QuestDefinitionTypeModel[QuestDefinitionTypeDB(value).name]
    else:
        return value
    
# Create a type alias using Annotated and the validator
QuestDefinitionType = Annotated[str, BeforeValidator(map_db_to_model)]

class WorkspaceLongQuestBase(BaseModel):

    workspace_id: int

    definition: Optional[str]
    type: QuestDefinitionType
    url: Optional[str]

    model_config = ConfigDict(from_attributes=True)

    def validate_definition(self, data, value):
        if data["type"] == QuestDefinitionTypeModel.NONE:
            if not value:
                return None
            raise ValidationError("'definition' field not allowed.")

        if data["type"] != QuestDefinitionTypeModel.JSON:
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
        if data["type"] == QuestDefinitionTypeModel.NONE:
            if not value:
                return None
            raise ValidationError("'url' field not allowed.")

        if data["type"] != QuestDefinitionTypeModel.URL:
            return value

        if not value:
            raise ValidationError("This field is required.")
        if data["definition"]:
            raise ValidationError("'definition' field not allowed.")

        return value

class WorkspaceLongQuestResponse(WorkspaceLongQuestBase):
    pass

class WorkspaceLongQuestCreate(WorkspaceLongQuestBase):
    pass

class WorkspaceLongQuestUpdate(BaseModel):
    definition: Optional[str] = Field(None)
    type: Optional[QuestDefinitionType]  = Field(None)
    url: Optional[str]  = Field(None)

    model_config = ConfigDict(from_attributes=True)

class WorkspaceImageryBase(BaseModel):

    workspace_id: int

    # Note the below column is of the JSON *database* type vs string type, so we're not
    # using pydantic's JSON mapping, hence this is not defined as Optional[Json[Any]]
    definition: Optional[list[Any]]

    model_config = ConfigDict(from_attributes=True)
    
class WorkspaceImageryResponse(WorkspaceImageryBase):
    pass

class WorkspaceImageryCreate(WorkspaceImageryBase):
    pass

class WorkspaceImageryUpdate(BaseModel):
    definition: Optional[list[Any]] = Field(None)

class WorkspaceBase(BaseModel):

    id: int
    type: str = Field(...)

    title: str = Field(...)
    description: Optional[str]

    tdeiProjectGroupId: UUID
    tdeiRecordId: Optional[UUID]
    tdeiServiceId: Optional[UUID]

    tdeiMetadata: Optional[Json[Any]]

    geometry: Optional[Annotated[str, WKBElement]]

    externalAppAccess: ExternalAppsDefinitionType

    kartaViewToken: Optional[str]

    longFormQuestDef: Optional[WorkspaceLongQuestBase]

    imageryListDef: Optional[WorkspaceImageryBase]


    model_config = ConfigDict(from_attributes=True)

    # emulate the prior Tasking Manager's behavior of dumping JSON as a string and not an object (FIXME?)
    @field_serializer('tdeiMetadata', when_used='json')
    def serialize_data_as_string(self, data: dict[str, Any], _info) -> str:
        """Serializes the dictionary data into a JSON string."""
        return json.dumps(data)

    # there are some legacy records with '', which is not valid JSON, so map those to None
    @field_validator("*", mode="before")
    @classmethod
    def empty_str_to_none(cls, v):
        if v == "":
            return None
        return v
    
class WorkspaceCreate(WorkspaceBase):
    pass

class WorkspaceResponse(WorkspaceBase):
    pass

class WorkspaceUpdate(BaseModel):
    title: Optional[str] = Field(None)
    description: Optional[str] = Field(None)
    externalAppAccess: Optional[ExternalAppsDefinitionType] = Field(None)


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
