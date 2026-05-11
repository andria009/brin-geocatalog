from datetime import UTC, datetime

from geocatalog.api import (
    STAC_QUERYABLES,
    apply_stac_fields,
    parse_stac_search_payload,
    serialize_dataset,
    stac_next_body,
    stac_prev_body,
    to_stac_feature_collection,
    to_stac_item,
)
from fastapi import HTTPException
import pytest


def test_to_stac_item_accepts_json_string_fields():
    item = {
        "id": "728ee40a-86f6-59f9-9467-25a710e51463",
        "collection_id": "landsat-8-oli-tirs",
        "title": "LC08_L1TP_124064_20250707_20250707_02_RT_SAA",
        "dataset_type": "raster",
        "source_path": "/data/geomimo/example.TIF",
        "file_name": "example.TIF",
        "file_extension": ".tif",
        "platform": "landsat-8",
        "sensor": "oli-tirs",
        "product": None,
        "acquisition_start": datetime(2025, 7, 7, tzinfo=UTC),
        "acquisition_end": datetime(2025, 7, 7, tzinfo=UTC),
        "file_size_bytes": 1881258,
        "modified_at": datetime(2025, 7, 7, 5, 3, 8, tzinfo=UTC),
        "bbox": [103.1113, -6.8396, 105.1721, -4.7362],
        "properties": '{"indexed_by": "geocatalog"}',
        "stac_item": '{"type": "Feature", "geometry": null, "properties": {}, "assets": "bad"}',
    }

    stac = to_stac_item(item)
    serialized = serialize_dataset(item)

    assert stac["id"] == item["id"]
    assert stac["collection"] == "landsat-8-oli-tirs"
    assert stac["properties"]["datetime"] == "2025-07-07T00:00:00+00:00"
    assert stac["bbox"] == item["bbox"]
    assert stac["geometry"]["type"] == "Polygon"
    assert stac["assets"]["data"]["href"] == f"/api/v1/datasets/{item['id']}/download"
    assert serialized["download_url"] == f"/api/v1/datasets/{item['id']}/download"
    assert serialized["properties"] == {"indexed_by": "geocatalog"}


def test_parse_stac_search_payload_filters_and_pagination():
    payload = {
        "collections": ["landsat-8-oli-tirs"],
        "ids": ["728ee40a-86f6-59f9-9467-25a710e51463"],
        "bbox": [103, -7, 106, -4],
        "datetime": "2025-07-01T00:00:00Z/2025-07-31T23:59:59Z",
        "query": {
            "platform": {"eq": "landsat-8"},
            "instruments": {"contains": "oli-tirs"},
            "type": {"eq": "raster"},
        },
        "sortby": [{"field": "datetime", "direction": "asc"}],
        "fields": {"include": ["id", "properties.datetime"], "exclude": ["assets"]},
        "limit": 250,
        "offset": 100,
    }

    parsed = parse_stac_search_payload(payload)

    assert parsed["limit"] == 250
    assert parsed["offset"] == 100
    assert parsed["sortby"] == [{"field": "datetime", "direction": "asc"}]
    assert parsed["repository_filters"]["collection_ids"] == ["landsat-8-oli-tirs"]
    assert parsed["repository_filters"]["ids"] == ["728ee40a-86f6-59f9-9467-25a710e51463"]
    assert parsed["repository_filters"]["bbox"] == [103, -7, 106, -4]
    assert parsed["repository_filters"]["platform"] == "landsat-8"
    assert parsed["repository_filters"]["sensor"] == "oli-tirs"
    assert parsed["repository_filters"]["dataset_type"] == "raster"
    assert parsed["repository_filters"]["date_from"] == datetime(2025, 7, 1, tzinfo=UTC)
    assert parsed["repository_filters"]["date_to"] == datetime(
        2025, 7, 31, 23, 59, 59, tzinfo=UTC
    )
    assert parsed["fields"] == {"include": ["id", "properties.datetime"], "exclude": ["assets"]}


def test_stac_feature_collection_includes_context_and_next_link():
    collection = to_stac_feature_collection(
        [],
        matched=250,
        limit=100,
        offset=0,
        self_href="/stac/search",
        next_href="/stac/search",
        next_body={"limit": 100, "offset": 100},
    )

    assert collection["type"] == "FeatureCollection"
    assert collection["numberMatched"] == 250
    assert collection["numberReturned"] == 0
    assert collection["context"] == {"returned": 0, "limit": 100, "matched": 250, "offset": 0}
    assert collection["links"][1]["rel"] == "next"
    assert collection["links"][1]["method"] == "POST"
    assert collection["links"][1]["body"] == {"limit": 100, "offset": 100}


def test_stac_fields_include_and_exclude_nested_values():
    feature = {
        "id": "scene-1",
        "type": "Feature",
        "bbox": [1, 2, 3, 4],
        "properties": {"datetime": "2025-07-07T00:00:00+00:00", "platform": "landsat-8"},
        "assets": {"data": {"href": "/api/v1/datasets/scene-1/download"}},
    }

    filtered = apply_stac_fields(
        feature,
        {"include": ["id", "properties.datetime", "assets.data.href"], "exclude": ["assets"]},
    )

    assert filtered == {"id": "scene-1", "properties": {"datetime": "2025-07-07T00:00:00+00:00"}}


def test_stac_queryables_describe_supported_filters():
    assert STAC_QUERYABLES["properties"]["platform"]["type"] == "string"
    assert STAC_QUERYABLES["properties"]["datetime"]["format"] == "date-time"


def test_parse_stac_search_payload_accepts_cql2_json_filter():
    payload = {
        "filter-lang": "cql2-json",
        "filter": {
            "op": "and",
            "args": [
                {"op": "in", "args": [{"property": "collection"}, ["landsat-8-oli-tirs"]]},
                {"op": "=", "args": [{"property": "id"}, "728ee40a-86f6-59f9-9467-25a710e51463"]},
                {"op": ">=", "args": [{"property": "datetime"}, "2025-07-01T00:00:00Z"]},
            ],
        },
    }

    parsed = parse_stac_search_payload(payload)

    assert parsed["repository_filters"]["collection_ids"] == ["landsat-8-oli-tirs"]
    assert parsed["repository_filters"]["ids"] == ["728ee40a-86f6-59f9-9467-25a710e51463"]
    assert parsed["repository_filters"]["date_from"] == datetime(2025, 7, 1, tzinfo=UTC)


def test_stac_page_bodies_clean_empty_values_and_include_prev():
    payload = {
        "collections": ["landsat-8-oli-tirs"],
        "ids": [],
        "bbox": None,
        "datetime": None,
        "fields": [],
        "limit": 2,
        "offset": 0,
    }

    assert stac_next_body(payload, limit=2, offset=0, matched=10) == {
        "collections": ["landsat-8-oli-tirs"],
        "limit": 2,
        "offset": 2,
    }
    assert stac_prev_body(payload, limit=2, offset=4) == {
        "collections": ["landsat-8-oli-tirs"],
        "limit": 2,
        "offset": 2,
    }


def test_stac_search_payload_rejects_unsupported_filter_lang_and_cql2_operator():
    with pytest.raises(HTTPException) as unsupported_lang:
        parse_stac_search_payload({"filter-lang": "cql2-text", "filter": "platform = 'landsat-8'"})
    assert unsupported_lang.value.status_code == 400

    with pytest.raises(HTTPException) as unsupported_operator:
        parse_stac_search_payload(
            {
                "filter-lang": "cql2-json",
                "filter": {"op": "like", "args": [{"property": "platform"}, "landsat%"]},
            }
        )
    assert unsupported_operator.value.status_code == 400
