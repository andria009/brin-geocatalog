from __future__ import annotations

from typing import Annotated
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse
import yaml

from geocatalog.db import connection
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
        async with connection() as conn:
            total = await count_datasets(
                conn,
                q=q,
                collection_id=collection_id,
                dataset_type=dataset_type,
                platform=platform,
                sensor=sensor,
                date_from=date_from,
                date_to=date_to,
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
                date_from=date_from,
                date_to=date_to,
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
                date_from=date_from,
                date_to=date_to,
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
            "links": [
                {"rel": "self", "href": "/stac"},
                {"rel": "data", "href": "/stac/collections"},
                {"rel": "search", "href": "/stac/search", "method": "POST"},
            ],
        }

    @app.get("/stac/collections")
    async def stac_collections():
        async with connection() as conn:
            collections = await list_collections(conn)
        return {
            "collections": [to_stac_collection(collection) for collection in collections],
            "links": [{"rel": "self", "href": "/stac/collections"}],
        }

    @app.get("/stac/collections/{collection_id}")
    async def stac_collection(collection_id: str):
        async with connection() as conn:
            collections = await list_collections(conn)
        for collection in collections:
            if collection["collection_id"] == collection_id:
                return to_stac_collection(collection)
        raise HTTPException(status_code=404, detail="Collection not found")

    @app.get("/stac/collections/{collection_id}/items")
    async def stac_items(collection_id: str, limit: int = Query(100, ge=1, le=1000)):
        async with connection() as conn:
            items = await search_datasets(conn, collection_id=collection_id, limit=limit)
        return {
            "type": "FeatureCollection",
            "features": [to_stac_item(item) for item in items],
            "links": [{"rel": "self", "href": f"/stac/collections/{collection_id}/items"}],
        }

    @app.get("/stac/collections/{collection_id}/items/{item_id}")
    async def stac_item(collection_id: str, item_id: str):
        async with connection() as conn:
            item = await get_dataset(conn, item_id)
        if not item or item["collection_id"] != collection_id:
            raise HTTPException(status_code=404, detail="Item not found")
        return to_stac_item(item)

    @app.post("/stac/search")
    async def stac_search(payload: dict):
        bbox = payload.get("bbox")
        limit = int(payload.get("limit", 100))
        async with connection() as conn:
            items = await search_datasets(conn, bbox=bbox, limit=min(limit, 1000))
        return {
            "type": "FeatureCollection",
            "features": [to_stac_item(item) for item in items],
            "links": [{"rel": "self", "href": "/stac/search"}],
        }

    return app


def serialize_dataset(item: dict) -> dict:
    return {
        "id": item["id"],
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
        "properties": item["properties"],
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
    stac = dict(item["stac_item"] or {})
    stac["id"] = item["id"]
    stac["collection"] = item["collection_id"]
    stac.setdefault("type", "Feature")
    stac.setdefault("stac_version", "1.0.0")
    stac.setdefault("properties", {})
    stac["properties"]["datetime"] = iso(item["acquisition_start"])
    stac["properties"]["platform"] = item["platform"]
    stac["properties"]["instruments"] = [item["sensor"]] if item["sensor"] else []
    stac.setdefault("assets", {})
    stac["assets"]["data"] = {"href": item["source_path"], "title": item["file_name"], "roles": ["data"]}
    stac["links"] = [
        {"rel": "self", "href": f"/stac/collections/{item['collection_id']}/items/{item['id']}"},
        {"rel": "collection", "href": f"/stac/collections/{item['collection_id']}"},
    ]
    return stac


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


app = create_app()
