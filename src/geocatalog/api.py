from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta
import hashlib
import hmac
import json
import time
from typing import Annotated
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse, Response
from pydantic import BaseModel
import yaml

from geocatalog.access import has_role_at_least, policy_for_role, serialize_access_policy
from geocatalog.config import get_settings
from geocatalog.db import connection
from geocatalog.stac_sync import (
    stac_item_id_for_dataset,
)
from geocatalog.repository import (
    count_datasets,
    authenticate_access_user,
    get_access_user,
    get_catalog_status,
    get_dataset,
    get_boundary_geojson,
    list_access_activity,
    list_access_users,
    list_locations,
    list_platform_status,
    list_recent_sources,
    list_scan_runs,
    record_access_activity,
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

    @app.get("/api/v1/access/roles")
    async def access_roles():
        return serialize_access_policy()

    @app.get("/api/v1/access/users")
    async def access_users(x_geocatalog_user: str | None = Header(default=None)):
        async with connection() as conn:
            user = await resolve_access_user(conn, x_geocatalog_user)
            require_role(user, "god")
            users = await list_access_users(conn)
        return {"items": [serialize_access_user(user) for user in users]}

    @app.get("/api/v1/access/me")
    async def access_me(x_geocatalog_user: str | None = Header(default=None)):
        async with connection() as conn:
            user = await resolve_access_user(conn, x_geocatalog_user)
        return serialize_current_access_user(user)

    @app.post("/api/v1/access/login")
    async def access_login(payload: AccessLoginRequest):
        async with connection() as conn:
            user = await authenticate_access_user(conn, payload.username, payload.password)
        if not user:
            raise HTTPException(status_code=401, detail="Invalid username or password")
        expires_at = datetime.now(UTC) + timedelta(days=get_settings().access_session_timeout_days)
        return {
            "authenticated": True,
            "user": serialize_access_user(user),
            "expires_at": expires_at.isoformat(),
            "session_timeout_seconds": get_settings().access_session_timeout_days * 24 * 60 * 60,
            "development_header": {
                "name": "X-GeoCatalog-User",
                "value": user["username"] or user["sso_subject"],
            },
            "note": "Temporary local login for pre-SSO testing.",
        }

    @app.get("/api/v1/access/activity")
    async def access_activity(
        x_geocatalog_user: str | None = Header(default=None),
        mine: bool = Query(False),
        limit: int = Query(50, ge=1, le=200),
    ):
        async with connection() as conn:
            user = await resolve_access_user(conn, x_geocatalog_user)
            if not mine:
                require_role(user, "god")
            rows = await list_access_activity(
                conn,
                user_id=user["id"] if user and mine else None,
                limit=limit,
            )
        return {"items": [serialize_access_activity(row) for row in rows], "limit": limit}

    @app.get("/api/v1/datasets")
    async def datasets(
        q: str | None = None,
        collection_id: str | None = None,
        dataset_type: str | None = None,
        platform: str | None = None,
        sensor: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        cloud_min: float | None = Query(default=None, ge=0, le=100),
        cloud_max: float | None = Query(default=None, ge=0, le=100),
        province: str | None = None,
        kabupaten: str | None = None,
        kecamatan: str | None = None,
        bbox: Annotated[list[float] | None, Query()] = None,
        limit: int = Query(100, ge=1, le=1000),
        offset: int = Query(0, ge=0),
        x_geocatalog_user: str | None = Header(default=None),
    ):
        parsed_date_from = parse_query_datetime(date_from, end_of_day=False)
        parsed_date_to = parse_query_datetime(date_to, end_of_day=True)
        async with connection() as conn:
            user = await resolve_access_user(conn, x_geocatalog_user)
            total = await count_datasets(
                conn,
                q=q,
                collection_id=collection_id,
                dataset_type=dataset_type,
                platform=platform,
                sensor=sensor,
                date_from=parsed_date_from,
                date_to=parsed_date_to,
                cloud_min=cloud_min,
                cloud_max=cloud_max,
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
                cloud_min=cloud_min,
                cloud_max=cloud_max,
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
                cloud_min=cloud_min,
                cloud_max=cloud_max,
                province=province,
                kabupaten=kabupaten,
                kecamatan=kecamatan,
                bbox=bbox,
                limit=limit,
                offset=offset,
            )
            if x_geocatalog_user:
                try:
                    await record_access_activity(
                        conn,
                        user=user,
                        activity="search",
                        metadata={
                            "q": q,
                            "collection_id": collection_id,
                            "dataset_type": dataset_type,
                            "platform": platform,
                            "sensor": sensor,
                            "date_from": date_from,
                            "date_to": date_to,
                            "cloud_min": cloud_min,
                            "cloud_max": cloud_max,
                            "province": province,
                            "kabupaten": kabupaten,
                            "kecamatan": kecamatan,
                            "bbox": bbox,
                            "limit": limit,
                            "offset": offset,
                            "total": total,
                            "footprint_total": footprint_total,
                        },
                    )
                except ValueError as exc:
                    raise HTTPException(status_code=402, detail=str(exc)) from exc
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
    async def dataset_odc(dataset_id: str, x_geocatalog_user: str | None = Header(default=None)):
        async with connection() as conn:
            user = await resolve_access_user(conn, x_geocatalog_user)
            require_asset_access(user)
            item = await get_dataset(conn, dataset_id)
            if item:
                try:
                    await record_access_activity(
                        conn,
                        user=user,
                        activity="odc_asset",
                        dataset_id=dataset_id,
                        metadata={"file_name": item["file_name"]},
                    )
                except ValueError as exc:
                    raise HTTPException(status_code=402, detail=str(exc)) from exc
        if not item:
            raise HTTPException(status_code=404, detail="Dataset not found")
        return yaml.safe_dump(to_odc_dataset(item), sort_keys=False)

    @app.get("/api/v1/datasets/{dataset_id}/download")
    async def dataset_download(
        dataset_id: str,
        x_geocatalog_user: str | None = Header(default=None),
        ticket: str | None = None,
    ):
        async with connection() as conn:
            ticket_user = verify_download_ticket(ticket, dataset_id) if ticket else None
            user = await resolve_access_user(conn, ticket_user or x_geocatalog_user)
            require_asset_access(user)
            item = await get_dataset(conn, dataset_id)
        if not item:
            raise HTTPException(status_code=404, detail="Dataset not found")
        path = resolve_asset_path(item["source_path"])
        if not path.exists() or not path.is_file():
            raise HTTPException(status_code=404, detail="Source file not found")
        async with connection() as conn:
            try:
                await record_access_activity(
                    conn,
                    user=user,
                    activity="download",
                    dataset_id=dataset_id,
                    metadata={"file_name": item["file_name"], "source_path": item["source_path"]},
                )
            except ValueError as exc:
                raise HTTPException(status_code=402, detail=str(exc)) from exc
        return Response(
            status_code=200,
            media_type="application/octet-stream",
            headers={
                "X-Accel-Redirect": asset_accel_redirect_path(path),
                "Content-Disposition": content_disposition(item["file_name"]),
            },
        )

    @app.get("/api/v1/datasets/{dataset_id}/download-ticket")
    async def dataset_download_ticket(
        dataset_id: str,
        x_geocatalog_user: str | None = Header(default=None),
    ):
        async with connection() as conn:
            user = await resolve_access_user(conn, x_geocatalog_user)
            require_asset_access(user)
            item = await get_dataset(conn, dataset_id)
        if not item:
            raise HTTPException(status_code=404, detail="Dataset not found")
        path = resolve_asset_path(item["source_path"])
        if not path.exists() or not path.is_file():
            raise HTTPException(status_code=404, detail="Source file not found")
        subject = user["username"] or user["sso_subject"]
        ticket = create_download_ticket(dataset_id, subject)
        return {
            "download_url": f"/api/v1/datasets/{dataset_id}/download?ticket={quote(ticket)}",
            "expires_in_seconds": get_settings().download_ticket_ttl_seconds,
        }

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


async def resolve_access_user(conn, subject_or_username: str | None) -> dict | None:
    if not subject_or_username:
        return None
    user = await get_access_user(conn, subject_or_username)
    if not user:
        raise HTTPException(status_code=401, detail="GeoCatalog access user not found")
    return user


def require_asset_access(user: dict | None) -> None:
    role = user["role"] if user else "explorer"
    if not policy_for_role(role).can_access_assets:
        raise HTTPException(status_code=403, detail="Role is not allowed to access assets")


def require_role(user: dict | None, minimum_role: str) -> None:
    role = user["role"] if user else "explorer"
    if not has_role_at_least(role, minimum_role):  # type: ignore[arg-type]
        raise HTTPException(status_code=403, detail=f"Role must be at least {minimum_role}")


def resolve_asset_path(source_path: str) -> Path:
    path = Path(source_path).resolve()
    allowed_roots = [Path(root).resolve() for root in get_settings().asset_root_paths]
    if not any(path == root or root in path.parents for root in allowed_roots):
        raise HTTPException(status_code=403, detail="Source path is outside allowed asset roots")
    return path


def asset_accel_redirect_path(path: Path) -> str:
    root = Path("/data/geomimo").resolve()
    try:
        relative_path = path.resolve().relative_to(root)
    except ValueError as exc:
        raise HTTPException(
            status_code=500,
            detail="Resolved asset path is not mounted for nginx protected delivery",
        ) from exc
    encoded_path = quote(relative_path.as_posix(), safe="/")
    return f"/protected-assets/{encoded_path}"


def content_disposition(file_name: str) -> str:
    ascii_fallback = "".join(char if 32 <= ord(char) < 127 and char not in '"\\' else "_" for char in file_name)
    encoded = quote(file_name)
    return f'attachment; filename="{ascii_fallback}"; filename*=UTF-8\'\'{encoded}'


def create_download_ticket(dataset_id: str, subject: str) -> str:
    issued_at = str(int(time.time()))
    payload = f"{dataset_id}:{subject}:{issued_at}"
    signature = sign_download_ticket(payload)
    raw = f"{payload}:{signature}".encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def verify_download_ticket(ticket: str | None, dataset_id: str) -> str | None:
    if not ticket:
        return None
    try:
        padded = ticket + "=" * (-len(ticket) % 4)
        decoded = base64.urlsafe_b64decode(padded.encode()).decode()
        ticket_dataset_id, subject, issued_at_text, signature = decoded.rsplit(":", 3)
        issued_at = int(issued_at_text)
    except Exception as exc:
        raise HTTPException(status_code=401, detail="Invalid download ticket") from exc
    if ticket_dataset_id != dataset_id:
        raise HTTPException(status_code=401, detail="Download ticket does not match dataset")
    max_age = get_settings().download_ticket_ttl_seconds
    if int(time.time()) - issued_at > max_age:
        raise HTTPException(status_code=401, detail="Download ticket has expired")
    payload = f"{ticket_dataset_id}:{subject}:{issued_at_text}"
    expected = sign_download_ticket(payload)
    if not hmac.compare_digest(signature, expected):
        raise HTTPException(status_code=401, detail="Invalid download ticket")
    return subject


def sign_download_ticket(payload: str) -> str:
    secret = get_settings().download_ticket_secret.encode()
    digest = hmac.new(secret, payload.encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


def serialize_access_user(user: dict) -> dict:
    return {
        "id": user["id"],
        "sso_subject": user["sso_subject"],
        "username": user["username"],
        "display_name": user["display_name"],
        "email": user["email"],
        "role": user["role"],
        "policy": serialize_role_policy(user["role"]),
        "token_balance": user["token_balance"],
        "has_password": user.get("has_password", False),
        "password_updated_at": iso(user.get("password_updated_at")),
        "is_active": user["is_active"],
        "registered_at": iso(user["registered_at"]),
        "approved_at": iso(user["approved_at"]),
        "last_seen_at": iso(user["last_seen_at"]),
    }


def serialize_current_access_user(user: dict | None) -> dict:
    if not user:
        return {
            "authenticated": False,
            "role": "explorer",
            "policy": serialize_role_policy("explorer"),
            "token_balance": None,
        }
    serialized = serialize_access_user(user)
    serialized["authenticated"] = True
    return serialized


def serialize_role_policy(role: str) -> dict:
    policy = policy_for_role(role)
    return {
        "rank": policy.rank,
        "can_filter": policy.can_filter,
        "can_view_dataset_rail": policy.can_view_dataset_rail,
        "can_view_dataset_detail": policy.can_view_dataset_detail,
        "can_view_status_by_platform": policy.can_view_status_by_platform,
        "can_view_full_detail_rail": policy.can_view_full_detail_rail,
        "can_access_assets": policy.can_access_assets,
        "uses_tokens": policy.uses_tokens,
        "default_tokens": policy.default_tokens,
    }


def serialize_access_activity(row: dict) -> dict:
    return {
        "id": row["id"],
        "user_id": row["user_id"],
        "username": row["username"],
        "role": row["role"],
        "activity": row["activity"],
        "token_delta": row["token_delta"],
        "dataset_id": row["dataset_id"],
        "metadata": normalize_json_object(row["metadata"]),
        "created_at": iso(row["created_at"]),
        "created_by": row["created_by"],
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


class AccessLoginRequest(BaseModel):
    username: str
    password: str


app = create_app()
