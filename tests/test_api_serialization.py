from datetime import UTC, datetime
from pathlib import Path

from geocatalog.api import (
    asset_accel_redirect_path,
    content_disposition,
    create_download_ticket,
    create_app,
    parse_query_datetime,
    serialize_dataset,
    to_odc_dataset,
    verify_download_ticket,
)
from geocatalog.repository import add_cloud_cover_filter, wildcard_to_ilike_pattern
from geocatalog.stac_sync import stac_item_id_for_dataset


def dataset_row(**overrides):
    row = {
        "id": "728ee40a-86f6-59f9-9467-25a710e51463",
        "collection_id": "landsat-8-oli-tirs",
        "title": "LC08_L1TP_124064_20250707_20250707_02_RT_SAA",
        "dataset_type": "raster",
        "source_path": "/data/geomimo/example.TIF",
        "file_name": "LC08_L1TP_124064_20250707_20250707_02_RT_SAA.TIF",
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
    }
    row.update(overrides)
    return row


def test_catalog_api_does_not_register_transitional_stac_routes():
    app = create_app()

    assert any(route.path == "/api/v1/health" for route in app.routes)
    assert any(route.path == "/api/v1/access/roles" for route in app.routes)
    assert any(route.path == "/api/v1/access/login" for route in app.routes)
    assert not any(route.path.startswith("/stac") for route in app.routes)


def test_serialize_dataset_normalizes_properties_and_exposes_links():
    row = dataset_row()

    serialized = serialize_dataset(row)

    assert serialized["properties"] == {"indexed_by": "geocatalog"}
    assert serialized["download_url"] == f"/api/v1/datasets/{row['id']}/download"
    assert serialized["stac_item_id"] == "LC08_L1TP_124064_20250707_20250707_02_RT"
    assert serialized["acquisition_start"] == "2025-07-07T00:00:00+00:00"
    assert serialized["modified_at"] == "2025-07-07T05:03:08+00:00"


def test_stac_item_id_matches_grouping_rules():
    assert (
        stac_item_id_for_dataset(
            "sentinel-2a",
            "S2A_MSIL2A_20260401T030541_N0511_R075_T47MBU_20260401T072012.jp2",
            "fallback",
        )
        == "T47MBU_20260401T030541"
    )
    assert (
        stac_item_id_for_dataset(
            "aqua", "MOD09GA.A2024105.h28v08.061.2024107035000.hdf", "fallback"
        )
        == "MOD09GA.A2024105.h28v08"
    )
    assert (
        stac_item_id_for_dataset("aqua", "a1.21001.1751.geo.hdf", "fallback")
        == "a1.21001.1751"
    )
    assert (
        stac_item_id_for_dataset("aqua", "a1.21014.0457.mod14.hdf", "fallback")
        == "a1.21014.0457"
    )
    assert stac_item_id_for_dataset("spot-6", "SPOT6_FILE.TIF", "fallback") == "fallback"


def test_parse_query_datetime_accepts_dates_and_zulu_timestamps():
    assert parse_query_datetime("2025-07-01") == datetime(2025, 7, 1, tzinfo=UTC)
    assert parse_query_datetime("2025-07-01", end_of_day=True) == datetime(
        2025, 7, 1, 23, 59, 59, tzinfo=UTC
    )
    assert parse_query_datetime("2025-07-01T10:30:00Z") == datetime(
        2025, 7, 1, 10, 30, tzinfo=UTC
    )


def test_wildcard_search_pattern_uses_sql_wildcards_and_escapes_literals():
    assert wildcard_to_ilike_pattern("LC08*QA?PIXEL") == "LC08%QA_PIXEL"
    assert wildcard_to_ilike_pattern("100%_ready~*") == "100~%~_ready~~%"


def test_cloud_cover_filter_full_range_does_not_exclude_missing_metadata():
    clauses = []
    args = []

    def add(value):
        args.append(value)
        return f"${len(args)}"

    add_cloud_cover_filter(clauses, add, 0, 100)

    assert clauses == []
    assert args == []


def test_cloud_cover_filter_partial_range_requires_cloud_metadata():
    clauses = []
    args = []

    def add(value):
        args.append(value)
        return f"${len(args)}"

    add_cloud_cover_filter(clauses, add, 0, 30)

    assert "properties ? 'cloud_cover'" in clauses
    assert "(properties->>'cloud_cover')::double precision <= $1" in clauses
    assert args == [30]


def test_to_odc_dataset_uses_catalog_metadata():
    row = dataset_row()

    odc = to_odc_dataset(row)

    assert odc["id"] == row["id"]
    assert odc["product"] == {"name": "landsat-8-oli-tirs"}
    assert odc["properties"]["platform"] == "landsat-8"
    assert odc["properties"]["instrument"] == "oli-tirs"
    assert odc["measurements"]["data"]["path"] == row["source_path"]


def test_asset_accel_redirect_path_is_internal_and_url_encoded():
    path = Path("/data/geomimo/folder with spaces/LC08_B1.TIF")

    assert asset_accel_redirect_path(path) == "/protected-assets/folder%20with%20spaces/LC08_B1.TIF"


def test_content_disposition_includes_ascii_fallback_and_utf8_filename():
    header = content_disposition("scene ä.tif")

    assert 'filename="scene _.tif"' in header
    assert "filename*=UTF-8''scene%20%C3%A4.tif" in header


def test_download_ticket_round_trip_identifies_user_subject():
    ticket = create_download_ticket("dataset-1", "mage@geocatalog.local")

    assert verify_download_ticket(ticket, "dataset-1") == "mage@geocatalog.local"
