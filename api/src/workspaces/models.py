from datetime import datetime
import json
from typing import Any, Optional
from uuid import UUID

import requests
from geoalchemy2 import WKBElement
from jsonschema import ValidationError, validate
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    Json,
    field_serializer,
    field_validator,
)
from typing_extensions import Annotated

from api.core.config import Settings
from api.src.workspaces.schemas import ExternalAppsDefinitionType, QuestDefinitionType, WorkspaceType

#
# These are DTOs used by the API to pass values to/from the client, NOT the schema definitions
#
class WorkspaceLongQuestBase(BaseModel):

    workspace_id: int

    definition: Optional[str]
    type: QuestDefinitionType
    url: Optional[str]

    model_config = ConfigDict(from_attributes=True)


class WorkspaceLongQuestResponse(WorkspaceLongQuestBase):
    pass


class WorkspaceLongQuestCreate(WorkspaceLongQuestBase):

    model_config = ConfigDict(from_attributes=True)

    @field_validator('definition')
    @classmethod
    def validate_definition(cls, value, data):
        if data.data.get("type") == QuestDefinitionType.JSON:
            if not value:
                raise ValidationError("This field is required when type is set to JSON.")
            else:
                try:
                    parsedValue = json.loads(value)
                    
                    if not parsedValue or not isinstance(parsedValue, dict):
                        raise ValidationError("must be a JSON object.")
                    
                    validate_json_against_schema(parsedValue, Settings.WS_LONGFORM_SCHEMA_URL)
                except json.JSONDecodeError as e:
                    raise ValidationError(f"Error parsing JSON: {e}")
                except ValidationError as e:
                    raise ValidationError(f"Error parsing JSON: {e}")

                return value
        else:
            if not value:
                return None
            raise ValidationError("'definition' field not allowed.")

    @field_validator('url')
    @classmethod
    def validate_url(cls, value, data):
        if data.data.get("type") == QuestDefinitionType.URL:
            if not value:
                raise ValidationError("This field is required when type is set to URL.")
            else:
                return value
        else:
            if not value:
                return None
            raise ValidationError("'url' field not allowed.") 

class WorkspaceLongQuestUpdate(WorkspaceLongQuestCreate):
    definition: Optional[str] = Field(None)
    type: QuestDefinitionType
    url: Optional[str] = Field(None)


class WorkspaceImageryBase(BaseModel):

    workspace_id: int

    # Note the below column is of the JSON *database* type vs string type, so we're not
    # using pydantic's JSON mapping, hence this is not defined as Optional[Json[Any]]
    definition: Optional[list[Any]]

    model_config = ConfigDict(from_attributes=True)


class WorkspaceImageryResponse(WorkspaceImageryBase):
    pass


class WorkspaceImageryCreate(WorkspaceImageryBase):

    model_config = ConfigDict(from_attributes=True)

    @field_validator('definition')
    @classmethod
    def validate_definition(cls, value, data):
        try:
            parsedValue = json.loads(value)
            
            if not parsedValue or not isinstance(parsedValue, dict):
                raise ValidationError("must be a JSON object.")
            
            validate_json_against_schema(parsedValue, Settings.WS_LONGFORM_SCHEMA_URL)
        except json.JSONDecodeError as e:
            raise ValidationError(f"Error parsing JSON: {e}")
        except ValidationError as e:
            raise ValidationError(f"Error parsing JSON: {e}")

        return value

class WorkspaceImageryUpdate(WorkspaceImageryCreate):
    # optional to be able to unset it
    definition: Optional[list[Any]] = Field(None)


class WorkspaceBase(BaseModel):

    id: int
    type: WorkspaceType = Field(...)

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

    # emulate the prior Tasking Manager's behavior of dumping JSON as a string and not an object (FIXME?)
    @field_serializer("tdeiMetadata", when_used="json")
    def serialize_data_as_string(self, data: dict[str, Any], _info) -> str:
        """Serializes the dictionary data into a JSON string."""
        return json.dumps(data)

    # there are some legacy records with '', which is not valid JSON, so map those to None (FIXME?)
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
    response = requests.get(schema_url, timeout=10)
    response.raise_for_status()
    schema = response.json()

    # Validate
    validate(instance=json, schema=schema)

    return True
