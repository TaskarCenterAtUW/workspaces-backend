from __future__ import annotations

import pytest
from fastapi import HTTPException
from shapely.geometry import MultiPolygon as ShapelyMultiPolygon

from api.src.tasking.projects.repository import _aoi_to_shapely
from api.src.tasking.projects.schemas import (
    _Feature,
    _FeatureCollection,
    _MultiPolygon,
    _Polygon,
)

SQUARE = [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]
TWO_SQUARES = [
    [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]],
    [[[2, 2], [3, 2], [3, 3], [2, 3], [2, 2]]],
]


class TestAoiUpcast:
    def test_polygon_upcasts_to_multipolygon(self):
        """A bare GeoJSON Polygon is upcast to a single-member MultiPolygon for storage."""
        out = _aoi_to_shapely(_Polygon(type="Polygon", coordinates=SQUARE))
        assert isinstance(out, ShapelyMultiPolygon)
        assert len(out.geoms) == 1

    def test_multipolygon_passes_through(self):
        """A GeoJSON MultiPolygon round-trips with all its constituent polygons intact."""
        out = _aoi_to_shapely(
            _MultiPolygon(type="MultiPolygon", coordinates=TWO_SQUARES)
        )
        assert isinstance(out, ShapelyMultiPolygon)
        assert len(out.geoms) == 2

    def test_feature_unwrapped(self):
        """A GeoJSON Feature wrapping a Polygon is unwrapped and upcast."""
        feat = _Feature(
            type="Feature",
            geometry=_Polygon(type="Polygon", coordinates=SQUARE),
            properties={"k": "v"},
        )
        out = _aoi_to_shapely(feat)
        assert isinstance(out, ShapelyMultiPolygon)

    def test_feature_collection_unwrapped(self):
        """A single-Feature FeatureCollection is unwrapped (collections with >1 feature are rejected upstream)."""
        fc = _FeatureCollection(
            type="FeatureCollection",
            features=[
                _Feature(
                    type="Feature",
                    geometry=_Polygon(type="Polygon", coordinates=SQUARE),
                )
            ],
        )
        out = _aoi_to_shapely(fc)
        assert isinstance(out, ShapelyMultiPolygon)


class TestInvalidGeometryRejected:
    def test_self_intersecting_polygon_rejected(self):
        """A self-intersecting 'bowtie' polygon is rejected with HTTP 422."""
        bowtie = _Polygon(
            type="Polygon",
            coordinates=[[[0, 0], [1, 1], [1, 0], [0, 1], [0, 0]]],
        )
        with pytest.raises(HTTPException) as exc:
            _aoi_to_shapely(bowtie)
        assert exc.value.status_code == 422
