from __future__ import annotations

from datetime import UTC, datetime
import json
from typing import Annotated
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse
import yaml

from geocatalog.db import connection
from geocatalog.stac_sync import (
    stac_item_id_for_dataset,
)
from geocatalog.repository import (
    count_datasets,
    get_catalog_status,
    get_dataset,
    get_boundary_geojson,
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
        description="Read-only satellite and geospatial data catalog API.",
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

    @app.get("/api/v1/services")
    async def services():
        async with connection() as conn:
            catalog_status = await get_catalog_status(conn)
            runs = await list_scan_runs(conn, limit=1)
        return {
            "items": serialize_service_statuses(
                catalog_status,
                runs[0] if runs else None,
            )
        }

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

    return app


def serialize_dataset(item: dict) -> dict:
    return {
        "id": item["id"],
        "stac_item_id": stac_item_id_for_dataset(
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


def serialize_service_statuses(catalog_status: dict, latest_run: dict | None) -> list[dict]:
    now = datetime.now(UTC)
    datasets = catalog_status["datasets"]
    items = [
        service_status(
            "catalog-api",
            "Catalog API",
            "running",
            "Read-only catalog API is responding.",
            now,
        ),
        service_status(
            "catalog-db",
            "Catalog Database",
            "running",
            f"{datasets.get('total_datasets', 0):,} indexed records.",
            datasets.get("latest_indexed_at") or now,
        ),
        worker_service_status(latest_run),
        stac_sync_service_status(),
        service_status(
            "footprint-backfill-service",
            "Footprint Backfill",
            "available",
            "Background footprint extraction service is configured.",
            now,
        ),
    ]
    return items


def worker_service_status(latest_run: dict | None) -> dict:
    if not latest_run:
        return service_status(
            "worker-service",
            "Worker Service",
            "unknown",
            "No scan run has been recorded yet.",
            None,
        )
    detail = (
        f"{latest_run['scanned_files']:,} scanned, "
        f"{latest_run['indexed_files']:,} new, "
        f"{latest_run['updated_files']:,} updated, "
        f"{latest_run['unchanged_files']:,} unchanged, "
        f"{latest_run.get('removed_files', 0):,} removed, "
        f"{latest_run['skipped_files']:,} skipped."
    )
    return service_status(
        "worker-service",
        "Worker Service",
        latest_run["status"],
        detail,
        latest_run["finished_at"] or latest_run["started_at"],
        progress={
            "scanned": latest_run["scanned_files"],
            "indexed": latest_run["indexed_files"],
            "updated": latest_run["updated_files"],
            "unchanged": latest_run["unchanged_files"],
            "removed": latest_run.get("removed_files", 0),
            "skipped": latest_run["skipped_files"],
        },
    )


def stac_sync_service_status() -> dict:
    state_path = Path("/app/logs/stac_sync_state.json")
    if not state_path.exists():
        return service_status(
            "stac-sync-service",
            "STAC Sync",
            "pending",
            "No STAC sync checkpoint has been written yet.",
            None,
        )
    try:
        state = json.loads(state_path.read_text())
    except (OSError, json.JSONDecodeError):
        return service_status(
            "stac-sync-service",
            "STAC Sync",
            "unknown",
            "STAC sync checkpoint could not be read.",
            None,
        )
    try:
        last_sync = parse_query_datetime(state.get("last_sync_at"))
    except ValueError:
        last_sync = None
    return service_status(
        "stac-sync-service",
        "STAC Sync",
        "synced",
        "Last incremental PgSTAC sync checkpoint is available.",
        last_sync,
    )


def service_status(
    service: str,
    label: str,
    status: str,
    detail: str,
    updated_at: datetime | None,
    *,
    progress: dict | None = None,
) -> dict:
    item = {
        "service": service,
        "label": label,
        "status": status,
        "detail": detail,
        "updated_at": iso(updated_at),
    }
    if progress is not None:
        item["progress"] = progress
    return item


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
