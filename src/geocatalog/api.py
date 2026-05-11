from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
import json
from typing import Annotated, Any
from pathlib import Path
from urllib.parse import urlencode

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse
import yaml

from geocatalog.db import connection
from geocatalog.stac_sync import (
    LANDSAT_PLATFORMS,
    SENTINEL2_PLATFORMS,
    MODIS_PLATFORMS,
    VIIRS_PLATFORMS,
    _landsat_scene_parts,
    _sentinel2_scene_parts,
    _modis_viirs_granule_key,
)
from geocatalog.repository import (
    count_datasets,
    get_catalog_status,
    get_dataset,
    get_boundary_geojson,
    list_collections,
    list_locations,
    list_platform_status,
    list_recent_sources,
    list_scan_runs,
    search_datasets,
)


STAC_CONFORMANCE_CLASSES = [
    "https://api.stacspec.org/v1.0.0/core",
    "https://api.stacspec.org/v1.0.0/collections",
    "https://api.stacspec.org/v1.0.0/ogcapi-features",
    "https://api.stacspec.org/v1.0.0/item-search",
    "https://api.stacspec.org/v1.0.0/item-search#fields",
    "https://api.stacspec.org/v1.0.0/item-search#query",
    "https://api.stacspec.org/v1.0.0/item-search#sort",
    "https://api.stacspec.org/v1.0.0/item-search#filter",
    "https://api.stacspec.org/v1.0.0/item-search#context",
]

STAC_QUERYABLES = {
    "$schema": "https://json-schema.org/draft/2019-09/schema",
    "$id": "/stac/queryables",
    "type": "object",
    "title": "GeoCatalog STAC API queryables",
    "properties": {
        "id": {"title": "Item ID", "type": "string"},
        "collection": {"title": "Collection ID", "type": "string"},
        "datetime": {"title": "Acquisition datetime", "type": "string", "format": "date-time"},
        "platform": {"title": "Satellite platform", "type": "string"},
        "instruments": {"title": "Sensor or instrument", "type": "string"},
        "type": {"title": "Dataset type", "type": "string", "enum": ["raster", "vector"]},
        "product": {"title": "Product", "type": "string"},
        "file_extension": {"title": "File extension", "type": "string"},
    },
    "additionalProperties": True,
}


def create_app() -> FastAPI:
    app = FastAPI(
        title="GeoCatalog API",
        version="0.1.0",
        description="Read-only satellite and geospatial data catalog API with STAC-compatible endpoints.",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
    )

    @app.get("/api/v1/health")
    async def health():
        return {"status": "ok", "service": "geocatalog-api"}

    @app.get("/api/v1/status")
    async def status():
        async with connection() as conn:
            catalog_status = await get_catalog_status(conn)
        return serialize_status(catalog_status)

    @app.get("/api/v1/platforms")
    async def platforms():
        async with connection() as conn:
            rows = await list_platform_status(conn)
        return {"items": [serialize_platform(row) for row in rows]}

    @app.get("/api/v1/source-files")
    async def source_files(limit: int = Query(40, ge=1, le=200)):
        async with connection() as conn:
            rows = await list_recent_sources(conn, limit=limit)
        return {"items": [serialize_source(row) for row in rows], "limit": limit}

    @app.get("/api/v1/scan-runs")
    async def scan_runs(limit: int = Query(20, ge=1, le=200)):
        async with connection() as conn:
            runs = await list_scan_runs(conn, limit=limit)
        return {"items": [serialize_scan_run(run) for run in runs], "limit": limit}

    @app.get("/api/v1/datasets")
    async def datasets(
        q: str | None = None,
        collection_id: str | None = None,
        dataset_type: str | None = None,
        platform: str | None = None,
        sensor: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        province: str | None = None,
        kabupaten: str | None = None,
        kecamatan: str | None = None,
        bbox: Annotated[list[float] | None, Query()] = None,
        limit: int = Query(100, ge=1, le=1000),
        offset: int = Query(0, ge=0),
    ):
        parsed_date_from = parse_query_datetime(date_from, end_of_day=False)
        parsed_date_to = parse_query_datetime(date_to, end_of_day=True)
        async with connection() as conn:
            total = await count_datasets(
                conn,
                q=q,
                collection_id=collection_id,
                dataset_type=dataset_type,
                platform=platform,
                sensor=sensor,
                date_from=parsed_date_from,
                date_to=parsed_date_to,
                province=province,
                kabupaten=kabupaten,
                kecamatan=kecamatan,
                bbox=bbox,
            )
            footprint_total = await count_datasets(
                conn,
                q=q,
                collection_id=collection_id,
                dataset_type=dataset_type,
                platform=platform,
                sensor=sensor,
                date_from=parsed_date_from,
                date_to=parsed_date_to,
                province=province,
                kabupaten=kabupaten,
                kecamatan=kecamatan,
                bbox=bbox,
                footprint_only=True,
            )
            items = await search_datasets(
                conn,
                q=q,
                collection_id=collection_id,
                dataset_type=dataset_type,
                platform=platform,
                sensor=sensor,
                date_from=parsed_date_from,
                date_to=parsed_date_to,
                province=province,
                kabupaten=kabupaten,
                kecamatan=kecamatan,
                bbox=bbox,
                limit=limit,
                offset=offset,
            )
        return {
            "items": [serialize_dataset(item) for item in items],
            "total": total,
            "footprint_total": footprint_total,
            "limit": limit,
            "offset": offset,
        }

    @app.get("/api/v1/search")
    async def search_alias(
        q: str | None = None,
        bbox: Annotated[list[float] | None, Query()] = None,
        limit: int = Query(100, ge=1, le=1000),
    ):
        async with connection() as conn:
            items = await search_datasets(conn, q=q, bbox=bbox, limit=limit)
        return {"items": [serialize_dataset(item) for item in items], "limit": limit}

    @app.get("/api/v1/datasets/{dataset_id}")
    async def dataset_detail(dataset_id: str):
        async with connection() as conn:
            item = await get_dataset(conn, dataset_id)
        if not item:
            raise HTTPException(status_code=404, detail="Dataset not found")
        return serialize_dataset(item)

    @app.get("/api/v1/datasets/{dataset_id}/odc", response_class=PlainTextResponse)
    async def dataset_odc(dataset_id: str):
        async with connection() as conn:
            item = await get_dataset(conn, dataset_id)
        if not item:
            raise HTTPException(status_code=404, detail="Dataset not found")
        return yaml.safe_dump(to_odc_dataset(item), sort_keys=False)

    @app.get("/api/v1/datasets/{dataset_id}/download")
    async def dataset_download(dataset_id: str):
        async with connection() as conn:
            item = await get_dataset(conn, dataset_id)
        if not item:
            raise HTTPException(status_code=404, detail="Dataset not found")
        path = Path(item["source_path"])
        if not path.exists() or not path.is_file():
            raise HTTPException(status_code=404, detail="Source file not found")
        return FileResponse(path, filename=item["file_name"])

    @app.get("/api/v1/locations")
    async def locations(province: str | None = None, kabupaten: str | None = None):
        async with connection() as conn:
            return await list_locations(conn, province=province, kabupaten=kabupaten)

    @app.get("/api/v1/boundary")
    async def boundary(level: str, name: str | None = None, code: str | None = None):
        async with connection() as conn:
            feature = await get_boundary_geojson(conn, level=level, name=name, code=code)
        if not feature:
            raise HTTPException(status_code=404, detail="Boundary not found")
        return feature

    @app.get("/stac")
    async def stac_root():
        return {
            "type": "Catalog",
            "id": "geocatalog",
            "stac_version": "1.0.0",
            "description": "GeoCatalog STAC-compatible catalog.",
            "conformsTo": STAC_CONFORMANCE_CLASSES,
            "links": [
                {"rel": "self", "href": "/stac"},
                {"rel": "root", "href": "/stac"},
                {"rel": "conformance", "href": "/stac/conformance"},
                {"rel": "data", "href": "/stac/collections"},
                {"rel": "search", "href": "/stac/search", "method": "GET"},
                {"rel": "search", "href": "/stac/search", "method": "POST"},
            ],
        }

    @app.get("/stac/conformance")
    async def stac_conformance():
        return {"conformsTo": STAC_CONFORMANCE_CLASSES}

    @app.get("/stac/queryables")
    async def stac_queryables():
        return STAC_QUERYABLES

    @app.get("/stac/collections")
    async def stac_collections():
        async with connection() as conn:
            collections = await list_collections(conn)
        return {
            "collections": [to_stac_collection(collection) for collection in collections],
            "links": [
                {"rel": "self", "href": "/stac/collections"},
                {"rel": "root", "href": "/stac"},
            ],
        }

    @app.get("/stac/collections/{collection_id}")
    async def stac_collection(collection_id: str):
        async with connection() as conn:
            collections = await list_collections(conn)
        for collection in collections:
            if collection["collection_id"] == collection_id:
                return to_stac_collection(collection)
        raise HTTPException(status_code=404, detail="Collection not found")

    @app.get("/stac/collections/{collection_id}/queryables")
    async def stac_collection_queryables(collection_id: str):
        async with connection() as conn:
            collections = await list_collections(conn)
        if collection_id not in {collection["collection_id"] for collection in collections}:
            raise HTTPException(status_code=404, detail="Collection not found")
        queryables = deepcopy(STAC_QUERYABLES)
        queryables["$id"] = f"/stac/collections/{collection_id}/queryables"
        queryables["title"] = f"GeoCatalog queryables for {collection_id}"
        return queryables

    @app.get("/stac/collections/{collection_id}/items")
    async def stac_items(
        collection_id: str,
        limit: int = Query(100, ge=1, le=1000),
        offset: int = Query(0, ge=0),
    ):
        async with connection() as conn:
            total = await count_datasets(conn, collection_id=collection_id)
            items = await search_datasets(
                conn, collection_id=collection_id, limit=limit, offset=offset
            )
        return to_stac_feature_collection(
            items,
            matched=total,
            limit=limit,
            offset=offset,
            self_href=stac_href(
                f"/stac/collections/{collection_id}/items",
                {"limit": limit, "offset": offset},
            ),
            next_href=stac_page_href(
                f"/stac/collections/{collection_id}/items",
                limit=limit,
                offset=offset,
                matched=total,
            ),
            prev_href=stac_prev_page_href(
                f"/stac/collections/{collection_id}/items",
                limit=limit,
                offset=offset,
            ),
            extra_links=[
                {"rel": "root", "href": "/stac"},
                {"rel": "collection", "href": f"/stac/collections/{collection_id}"},
            ],
        )

    @app.get("/stac/collections/{collection_id}/items/{item_id}")
    async def stac_item(collection_id: str, item_id: str):
        async with connection() as conn:
            item = await get_dataset(conn, item_id)
        if not item or item["collection_id"] != collection_id:
            raise HTTPException(status_code=404, detail="Item not found")
        return to_stac_item(item)

    @app.get("/stac/search")
    async def stac_search_get(
        collections: str | None = None,
        ids: str | None = None,
        bbox: str | None = None,
        datetime: str | None = None,
        fields: str | None = None,
        limit: int = Query(100, ge=1, le=1000),
        offset: int = Query(0, ge=0),
    ):
        payload = {
            "collections": comma_list(collections),
            "ids": comma_list(ids),
            "bbox": parse_bbox_query(bbox),
            "datetime": datetime,
            "fields": comma_list(fields),
            "limit": limit,
            "offset": offset,
        }
        return await stac_search(payload)

    @app.post("/stac/search")
    async def stac_search(payload: dict[str, Any]):
        params = parse_stac_search_payload(payload)
        async with connection() as conn:
            total = await count_datasets(conn, **params["repository_filters"])
            items = await search_datasets(
                conn,
                **params["repository_filters"],
                limit=params["limit"],
                offset=params["offset"],
                sortby=params["sortby"],
            )
        next_body = stac_next_body(payload, params["limit"], params["offset"], total)
        prev_body = stac_prev_body(payload, params["limit"], params["offset"])
        return to_stac_feature_collection(
            items,
            matched=total,
            limit=params["limit"],
            offset=params["offset"],
            self_href="/stac/search",
            next_href="/stac/search" if next_body else None,
            next_body=next_body,
            prev_href="/stac/search" if prev_body else None,
            prev_body=prev_body,
            fields=params["fields"],
            extra_links=[{"rel": "root", "href": "/stac"}],
        )

    return app


def _stac_item_id(platform: str | None, file_name: str, fallback_id: str) -> str:
    """Compute the PgSTAC item ID for a dataset row.

    Grouped platforms (Landsat, Sentinel-2, MODIS/VIIRS) use a scene-group
    key derived from the filename — matching the logic in stac_sync.py.
    All other platforms use the geocatalog UUID (one file → one STAC item).
    Returns fallback_id when the regex doesn't match (ungrouped scene).
    """
    if platform in LANDSAT_PLATFORMS:
        scene_id, _ = _landsat_scene_parts(file_name)
        return scene_id or fallback_id
    if platform in SENTINEL2_PLATFORMS:
        scene_id, _ = _sentinel2_scene_parts(file_name)
        return scene_id or fallback_id
    if platform in MODIS_PLATFORMS | VIIRS_PLATFORMS:
        return _modis_viirs_granule_key(file_name) or fallback_id
    return fallback_id


def serialize_dataset(item: dict) -> dict:
    return {
        "id": item["id"],
        "stac_item_id": _stac_item_id(
            item.get("platform"), item["file_name"], item["id"]
        ),
        "collection_id": item["collection_id"],
        "title": item["title"],
        "dataset_type": item["dataset_type"],
        "source_path": item["source_path"],
        "file_name": item["file_name"],
        "file_extension": item["file_extension"],
        "platform": item["platform"],
        "sensor": item["sensor"],
        "product": item["product"],
        "acquisition_start": iso(item["acquisition_start"]),
        "acquisition_end": iso(item["acquisition_end"]),
        "file_size_bytes": item["file_size_bytes"],
        "modified_at": iso(item["modified_at"]),
        "bbox": item["bbox"],
        "properties": normalize_json_object(item["properties"]),
        "download_url": dataset_download_url(item),
    }


def serialize_status(status: dict) -> dict:
    datasets = status["datasets"]
    return {
        "datasets": {
            "total": datasets.get("total_datasets", 0),
            "raster": datasets.get("raster_datasets", 0),
            "vector": datasets.get("vector_datasets", 0),
            "collections": datasets.get("collections", 0),
            "latest_indexed_at": iso(datasets.get("latest_indexed_at")),
        },
        "latest_scan_run": serialize_scan_run(status["latest_scan_run"])
        if status["latest_scan_run"]
        else None,
    }


def serialize_platform(row: dict) -> dict:
    return {
        "platform": row["platform"],
        "total": row["total"],
        "raster": row["raster"],
        "vector": row["vector"],
        "latest_indexed_at": iso(row["latest_indexed_at"]),
    }


def serialize_source(row: dict) -> dict:
    return {
        "id": row["id"],
        "platform": row["platform"],
        "sensor": row["sensor"],
        "collection_id": row["collection_id"],
        "source_path": row["source_path"],
        "folder": row["folder"],
        "file_name": row["file_name"],
        "file_size_bytes": row["file_size_bytes"],
        "updated_at": iso(row["updated_at"]),
    }


def serialize_scan_run(run: dict) -> dict:
    return {
        "id": run["id"],
        "root_path": run["root_path"],
        "started_at": iso(run["started_at"]),
        "finished_at": iso(run["finished_at"]),
        "status": run["status"],
        "scanned_files": run["scanned_files"],
        "indexed_files": run["indexed_files"],
        "updated_files": run["updated_files"],
        "unchanged_files": run["unchanged_files"],
        "removed_files": run.get("removed_files", 0),
        "skipped_files": run["skipped_files"],
        "message": run["message"],
    }


def to_stac_item(item: dict) -> dict:
    stac = normalize_json_object(item.get("stac_item"))
    stac["id"] = item["id"]
    stac["collection"] = item["collection_id"]
    stac.setdefault("type", "Feature")
    stac.setdefault("stac_version", "1.0.0")
    stac["properties"] = normalize_json_object(stac.get("properties"))
    stac["properties"]["datetime"] = iso(item["acquisition_start"])
    stac["properties"]["platform"] = item["platform"]
    stac["properties"]["instruments"] = [item["sensor"]] if item["sensor"] else []
    bbox = item.get("bbox")
    if bbox:
        stac["bbox"] = list(bbox)
        if not stac.get("geometry"):
            stac["geometry"] = bbox_geometry(bbox)
    else:
        stac.setdefault("geometry", None)
    stac["assets"] = normalize_json_object(stac.get("assets"))
    stac["assets"]["data"] = {
        "href": dataset_download_url(item),
        "title": item["file_name"],
        "roles": ["data"],
    }
    stac["links"] = [
        {"rel": "self", "href": f"/stac/collections/{item['collection_id']}/items/{item['id']}"},
        {"rel": "root", "href": "/stac"},
        {"rel": "parent", "href": f"/stac/collections/{item['collection_id']}"},
        {"rel": "collection", "href": f"/stac/collections/{item['collection_id']}"},
    ]
    return stac


def to_stac_feature_collection(
    items: list[dict],
    *,
    matched: int,
    limit: int,
    offset: int,
    self_href: str,
    next_href: str | None = None,
    next_body: dict[str, Any] | None = None,
    prev_href: str | None = None,
    prev_body: dict[str, Any] | None = None,
    fields: dict[str, list[str]] | None = None,
    extra_links: list[dict[str, Any]] | None = None,
) -> dict:
    links = [{"rel": "self", "href": self_href}]
    links.extend(extra_links or [])
    if next_href:
        link: dict[str, Any] = {"rel": "next", "href": next_href}
        if next_body is not None:
            link["method"] = "POST"
            link["body"] = next_body
        links.append(link)
    if prev_href:
        link = {"rel": "prev", "href": prev_href}
        if prev_body is not None:
            link["method"] = "POST"
            link["body"] = prev_body
        links.append(link)
    returned = len(items)
    return {
        "type": "FeatureCollection",
        "features": [apply_stac_fields(to_stac_item(item), fields) for item in items],
        "links": links,
        "numberMatched": matched,
        "numberReturned": returned,
        "context": {
            "returned": returned,
            "limit": limit,
            "matched": matched,
            "offset": offset,
        },
    }


def to_stac_collection(collection: dict) -> dict:
    collection_id = collection["collection_id"]
    return {
        "type": "Collection",
        "id": collection_id,
        "stac_version": "1.0.0",
        "description": f"GeoCatalog collection {collection_id}",
        "license": "proprietary",
        "extent": {
            "spatial": {"bbox": [[-180, -90, 180, 90]]},
            "temporal": {
                "interval": [[iso(collection["temporal_start"]), iso(collection["temporal_end"])]]
            },
        },
        "summaries": {"item_count": [collection["item_count"]]},
        "links": [
            {"rel": "self", "href": f"/stac/collections/{collection_id}"},
            {"rel": "root", "href": "/stac"},
            {"rel": "parent", "href": "/stac"},
            {"rel": "items", "href": f"/stac/collections/{collection_id}/items"},
        ],
    }


def to_odc_dataset(item: dict) -> dict:
    return {
        "id": item["id"],
        "product": {"name": item["collection_id"]},
        "crs": "EPSG:4326",
        "grids": {"default": {"shape": None, "transform": None}},
        "properties": {
            "datetime": iso(item["acquisition_start"]),
            "platform": item["platform"],
            "instrument": item["sensor"],
            "odc:file_format": item["file_extension"].lstrip(".").upper(),
        },
        "measurements": {
            "data": {
                "path": item["source_path"],
            }
        },
    }


def iso(value):
    return value.isoformat() if value else None


def dataset_download_url(item: dict) -> str:
    return f"/api/v1/datasets/{item['id']}/download"


def parse_stac_search_payload(payload: dict[str, Any]) -> dict[str, Any]:
    limit = min(max(int(payload.get("limit", 100) or 100), 1), 1000)
    offset = min(max(int(payload.get("offset", payload.get("page", 0)) or 0), 0), 10_000_000)
    date_from, date_to = parse_stac_datetime_interval(payload.get("datetime"))
    filter_lang = payload.get("filter-lang")
    if filter_lang and str(filter_lang).lower() != "cql2-json":
        raise HTTPException(status_code=400, detail="Only filter-lang=cql2-json is supported")
    filters: dict[str, Any] = {
        "collection_ids": normalize_string_list(payload.get("collections")),
        "ids": normalize_string_list(payload.get("ids")),
        "bbox": normalize_bbox(payload.get("bbox")),
        "date_from": date_from,
        "date_to": date_to,
    }
    filters.update(parse_stac_query(payload.get("query")))
    filters.update(parse_cql2_filter(payload.get("filter")))
    filters = {key: value for key, value in filters.items() if value not in (None, [], "")}
    return {
        "repository_filters": filters,
        "limit": limit,
        "offset": offset,
        "sortby": normalize_stac_sortby(payload.get("sortby")),
        "fields": normalize_stac_fields(payload.get("fields")),
    }


def parse_stac_datetime_interval(value: Any) -> tuple[datetime | None, datetime | None]:
    if not value:
        return None, None
    text = str(value).strip()
    if "/" not in text:
        return (
            parse_query_datetime(text, end_of_day=False),
            parse_query_datetime(text, end_of_day=True),
        )
    start_text, end_text = text.split("/", 1)
    start = None if start_text in ("", "..") else parse_query_datetime(start_text, end_of_day=False)
    end = None if end_text in ("", "..") else parse_query_datetime(end_text, end_of_day=True)
    return start, end


def parse_stac_query(query: Any) -> dict[str, Any]:
    if not isinstance(query, dict):
        return {}
    filters = {}
    for field, condition in query.items():
        target = stac_property_to_filter(field)
        if not target:
            continue
        value = stac_query_value(condition)
        if value is not None:
            filters[target] = value
    return filters


def parse_cql2_filter(filter_expression: Any) -> dict[str, Any]:
    if filter_expression is None:
        return {}
    if not isinstance(filter_expression, dict):
        raise HTTPException(status_code=400, detail="CQL2 filter must be a JSON object")
    op = str(filter_expression.get("op", "")).lower()
    args = filter_expression.get("args", [])
    if op == "and" and isinstance(args, list):
        filters = {}
        for item in args:
            filters.update(parse_cql2_filter(item))
        return filters
    if not isinstance(args, list) or len(args) != 2:
        raise HTTPException(status_code=400, detail=f"Unsupported CQL2 filter shape for op '{op}'")
    field = cql2_property_name(args[0])
    if field in ("datetime", "properties.datetime") and op in (">=", ">", "gte", "gt"):
        return {"date_from": parse_query_datetime(str(args[1]), end_of_day=False)}
    if field in ("datetime", "properties.datetime") and op in ("<=", "<", "lte", "lt"):
        return {"date_to": parse_query_datetime(str(args[1]), end_of_day=True)}
    if op in ("=", "eq", "in"):
        field = cql2_property_name(args[0])
        target = stac_property_to_filter(field)
        if not target:
            raise HTTPException(status_code=400, detail=f"Unsupported CQL2 property '{field}'")
        value = args[1]
        if op == "in" and isinstance(value, list) and target in ("collection_id", "collection_ids"):
            return {"collection_ids": normalize_string_list(value)}
        if op == "in" and isinstance(value, list) and target == "ids":
            return {target: normalize_string_list(value)}
        if target == "ids":
            return {target: [str(value)]}
        if op == "in" and isinstance(value, list) and value:
            return {target: str(value[0])}
        return {target: str(value)}
    raise HTTPException(status_code=400, detail=f"Unsupported CQL2 operator '{op}'")


def stac_property_to_filter(field: Any) -> str | None:
    normalized = str(field or "").strip()
    return {
        "collection": "collection_id",
        "collection_id": "collection_id",
        "id": "ids",
        "platform": "platform",
        "instruments": "sensor",
        "instrument": "sensor",
        "sensor": "sensor",
        "type": "dataset_type",
        "dataset_type": "dataset_type",
        "product": "product",
        "file_extension": "file_extension",
    }.get(normalized)


def stac_query_value(condition: Any) -> str | None:
    if not isinstance(condition, dict):
        return str(condition)
    for operator in ("eq", "contains"):
        if operator in condition:
            return str(condition[operator])
    if "in" in condition and isinstance(condition["in"], list) and condition["in"]:
        return str(condition["in"][0])
    return None


def cql2_property_name(value: Any) -> str | None:
    if isinstance(value, dict):
        return value.get("property")
    return str(value) if value else None


def normalize_stac_sortby(value: Any) -> list[dict[str, str]]:
    if isinstance(value, str):
        return [
            {
                "field": item[1:] if item.startswith(("+", "-")) else item,
                "direction": "desc" if item.startswith("-") else "asc",
            }
            for item in value.split(",")
            if item
        ]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    return []


def normalize_stac_fields(value: Any) -> dict[str, list[str]]:
    if isinstance(value, str):
        return {"include": comma_list(value), "exclude": []}
    if isinstance(value, list):
        return {"include": [str(item) for item in value], "exclude": []}
    if isinstance(value, dict):
        return {
            "include": normalize_string_list(value.get("include")),
            "exclude": normalize_string_list(value.get("exclude")),
        }
    return {"include": [], "exclude": []}


def apply_stac_fields(
    feature: dict[str, Any], fields: dict[str, list[str]] | None
) -> dict[str, Any]:
    if not fields:
        return feature
    include = fields.get("include", [])
    exclude = fields.get("exclude", [])
    filtered = include_stac_fields(feature, include) if include else deepcopy(feature)
    for path in exclude:
        delete_nested_value(filtered, path)
    return filtered


def include_stac_fields(feature: dict[str, Any], paths: list[str]) -> dict[str, Any]:
    included: dict[str, Any] = {}
    for path in paths:
        value = get_nested_value(feature, path)
        if value is not None:
            set_nested_value(included, path, value)
    return included


def get_nested_value(value: dict[str, Any], path: str) -> Any:
    current: Any = value
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return deepcopy(current)


def set_nested_value(target: dict[str, Any], path: str, value: Any) -> None:
    current = target
    parts = path.split(".")
    for part in parts[:-1]:
        current = current.setdefault(part, {})
    current[parts[-1]] = value


def delete_nested_value(target: dict[str, Any], path: str) -> None:
    current = target
    parts = path.split(".")
    for part in parts[:-1]:
        if not isinstance(current, dict) or part not in current:
            return
        current = current[part]
    if isinstance(current, dict):
        current.pop(parts[-1], None)


def normalize_string_list(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, str):
        return comma_list(value)
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    return []


def normalize_bbox(value: Any) -> list[float] | None:
    if not value:
        return None
    if not isinstance(value, list) or len(value) != 4:
        raise HTTPException(status_code=400, detail="bbox must contain four coordinates")
    return [float(item) for item in value]


def comma_list(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def parse_bbox_query(value: str | None) -> list[float] | None:
    if not value:
        return None
    parts = [item.strip() for item in value.split(",") if item.strip()]
    if len(parts) != 4:
        raise HTTPException(status_code=400, detail="bbox must contain four coordinates")
    return [float(item) for item in parts]


def stac_href(path: str, params: dict[str, Any]) -> str:
    clean_params = {key: value for key, value in params.items() if value not in (None, [], "")}
    return f"{path}?{urlencode(clean_params, doseq=True)}" if clean_params else path


def stac_page_href(path: str, *, limit: int, offset: int, matched: int) -> str | None:
    next_offset = offset + limit
    if next_offset >= matched:
        return None
    return stac_href(path, {"limit": limit, "offset": next_offset})


def stac_prev_page_href(path: str, *, limit: int, offset: int) -> str | None:
    if offset <= 0:
        return None
    return stac_href(path, {"limit": limit, "offset": max(0, offset - limit)})


def stac_next_body(
    payload: dict[str, Any], limit: int, offset: int, matched: int
) -> dict[str, Any] | None:
    next_offset = offset + limit
    if next_offset >= matched:
        return None
    return stac_page_body(payload, limit=limit, offset=next_offset)


def stac_prev_body(payload: dict[str, Any], limit: int, offset: int) -> dict[str, Any] | None:
    if offset <= 0:
        return None
    return stac_page_body(payload, limit=limit, offset=max(0, offset - limit))


def stac_page_body(payload: dict[str, Any], *, limit: int, offset: int) -> dict[str, Any]:
    body = clean_empty_values(payload)
    body["limit"] = limit
    body["offset"] = offset
    return body


def clean_empty_values(value: Any) -> Any:
    if isinstance(value, dict):
        cleaned = {}
        for key, item in value.items():
            cleaned_item = clean_empty_values(item)
            if cleaned_item not in (None, "", [], {}):
                cleaned[key] = cleaned_item
        return cleaned
    if isinstance(value, list):
        return [clean_empty_values(item) for item in value if item not in (None, "", [], {})]
    return value


def normalize_json_object(value) -> dict:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str) and value:
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return dict(parsed) if isinstance(parsed, dict) else {}
    return {}


def bbox_geometry(bbox) -> dict | None:
    if not bbox or len(bbox) != 4:
        return None
    west, south, east, north = bbox
    return {
        "type": "Polygon",
        "coordinates": [[
            [west, south],
            [east, south],
            [east, north],
            [west, north],
            [west, south],
        ]],
    }


def parse_query_datetime(value: str | None, *, end_of_day: bool = False) -> datetime | None:
    if not value:
        return None
    text = value.strip()
    if not text:
        return None
    if len(text) == 10:
        suffix = "T23:59:59" if end_of_day else "T00:00:00"
        text = f"{text}{suffix}"
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed


app = create_app()
