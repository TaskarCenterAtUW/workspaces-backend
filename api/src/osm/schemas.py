from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel


class AdiffElement(BaseModel):
    """A particular OSM element version in an augmented diff action."""

    type: str  # 'node' | 'way' | 'relation'
    id: int
    version: int
    changeset: int
    timestamp: datetime
    user: Optional[str] = None
    uid: Optional[int] = None
    visible: bool
    tags: dict[str, str]
    # node-only:
    lat: Optional[float] = None
    lon: Optional[float] = None
    # way-only: [{ref, lat, lon}]
    nodes: Optional[list[dict[str, Any]]] = None
    # relation-only: [{type, ref, role}]
    members: Optional[list[dict[str, Any]]] = None


class AdiffAction(BaseModel):
    type: str  # 'create' | 'modify' | 'delete'
    new: AdiffElement
    old: Optional[AdiffElement] = None


class AugmentedDiffResponse(BaseModel):
    actions: list[AdiffAction]

    @classmethod
    def from_rows(cls, rows: list) -> "AugmentedDiffResponse":
        def make_element(row: Any, prefix: str) -> Optional[AdiffElement]:
            if row[f"{prefix}_id"] is None:
                return None

            return AdiffElement(
                type=row["element_type"],
                id=row[f"{prefix}_id"],
                version=row[f"{prefix}_version"],
                changeset=row[f"{prefix}_changeset_id"],
                timestamp=row[f"{prefix}_timestamp"],
                user=row[f"{prefix}_user"],
                uid=row[f"{prefix}_uid"],
                visible=row[f"{prefix}_visible"],
                tags=row[f"{prefix}_tags"] or {},
                lat=row[f"{prefix}_lat"],
                lon=row[f"{prefix}_lon"],
                nodes=row[f"{prefix}_nodes"],
                members=row[f"{prefix}_members"],
            )

        def require_new(row: Any) -> AdiffElement:
            element = make_element(row, "new")
            if element is None:
                raise ValueError(f"adiff row missing new_id: {dict(row)}")
            return element

        actions = [
            AdiffAction(
                type=row["action_type"],
                new=require_new(row),
                old=make_element(row, "old"),
            )
            for row in rows
        ]

        return cls(actions=actions)
