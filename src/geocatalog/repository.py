from __future__ import annotations

import json
from typing import Any

import asyncpg

from geocatalog.scanner import DatasetCandidate


async def upsert_dataset(conn: asyncpg.Connection, candidate: DatasetCandidate) -> str:
    result = await conn.fetchrow(
        """
        WITH existing AS (
          SELECT id, checksum
          FROM datasets
          WHERE source_path = $5
        ),
        inserted AS (
        INSERT INTO datasets (
          id, collection_id, title, dataset_type, source_path, file_name,
          file_extension, platform, sensor, product, acquisition_start,
          acquisition_end, file_size_bytes, modified_at, checksum, bbox, footprint,
          properties, stac_item
        )
        SELECT
          $1, $2, $3, $4, $5, $6,
          $7, $8, $9, $10, $11,
          $12, $13, $14, $15, $16::double precision[],
          CASE WHEN $17::jsonb IS NULL THEN NULL
               ELSE ST_Multi(ST_SetSRID(ST_GeomFromGeoJSON($17::text), 4326))
          END,
          $18::jsonb, $19::jsonb
          WHERE NOT EXISTS (SELECT 1 FROM existing)
          RETURNING 'indexed'::text AS action
        ),
        updated AS (
          UPDATE datasets
          SET collection_id = $2,
              title = $3,
              dataset_type = $4,
              file_name = $6,
              file_extension = $7,
              platform = $8,
              sensor = $9,
              product = $10,
              acquisition_start = $11,
              acquisition_end = $12,
              file_size_bytes = $13,
              modified_at = $14,
              checksum = $15,
              bbox = $16::double precision[],
              footprint = CASE WHEN $17::jsonb IS NULL THEN NULL
                               ELSE ST_Multi(ST_SetSRID(ST_GeomFromGeoJSON($17::text), 4326))
                          END,
              properties = $18::jsonb,
              stac_item = $19::jsonb,
              updated_at = now()
          WHERE source_path = $5
            AND (
              checksum IS DISTINCT FROM $15
              OR bbox IS DISTINCT FROM $16::double precision[]
              OR (footprint IS NULL AND $17::jsonb IS NOT NULL)
            )
          RETURNING 'updated'::text AS action
        )
        SELECT action FROM inserted
        UNION ALL
        SELECT action FROM updated
        UNION ALL
        SELECT 'unchanged'::text AS action
        WHERE EXISTS (SELECT 1 FROM existing)
          AND NOT EXISTS (SELECT 1 FROM updated)
          AND NOT EXISTS (SELECT 1 FROM inserted)
        """,
        candidate.id,
        candidate.collection_id,
        candidate.title,
        candidate.dataset_type,
        candidate.source_path,
        candidate.file_name,
        candidate.file_extension,
        candidate.platform,
        candidate.sensor,
        candidate.product,
        candidate.acquisition_start,
        candidate.acquisition_end,
        candidate.file_size_bytes,
        candidate.modified_at,
        candidate.checksum,
        candidate.bbox,
        json.dumps(candidate.footprint_geojson) if candidate.footprint_geojson else None,
        json.dumps(candidate.properties),
        json.dumps(candidate.stac_item),
    )
    return result["action"] if result else "unchanged"


async def remove_missing_files_in_folder(
    conn: asyncpg.Connection, folder: str, current_source_paths: list[str]
) -> int:
    rows = await conn.fetch(
        """
        DELETE FROM datasets
        WHERE regexp_replace(source_path, '/[^/]+$', '') = $1
          AND NOT (source_path = ANY($2::text[]))
        RETURNING id
        """,
        folder,
        current_source_paths,
    )
    return len(rows)


async def list_datasets_without_footprint(
    conn: asyncpg.Connection,
    *,
    limit: int = 1000,
    platform: str | None = None,
) -> list[dict[str, Any]]:
    if platform:
        rows = await conn.fetch(
            """
            SELECT id::text, source_path
            FROM datasets
            WHERE footprint IS NULL
              AND dataset_type = 'raster'
              AND platform = $1
            ORDER BY updated_at DESC
            LIMIT $2
            """,
            platform,
            limit,
        )
    else:
        rows = await conn.fetch(
            """
            SELECT id::text, source_path
            FROM datasets
            WHERE footprint IS NULL
              AND dataset_type = 'raster'
            ORDER BY updated_at DESC
            LIMIT $1
            """,
            limit,
        )
    return [dict(row) for row in rows]


async def update_dataset_footprint(
    conn: asyncpg.Connection,
    *,
    dataset_id: str,
    bbox: list[float],
    footprint_geojson: dict[str, Any],
) -> None:
    await conn.execute(
        """
        UPDATE datasets
        SET bbox = $2::double precision[],
            footprint = ST_Multi(ST_SetSRID(ST_GeomFromGeoJSON($3), 4326)),
            stac_item = jsonb_set(
              jsonb_set(stac_item, '{bbox}', to_jsonb($2::double precision[]), true),
              '{geometry}', $4::jsonb, true
            ),
            updated_at = now()
        WHERE id = $1::uuid
        """,
        dataset_id,
        bbox,
        json.dumps(footprint_geojson),
        json.dumps(footprint_geojson),
    )


async def search_datasets(
    conn: asyncpg.Connection,
    *,
    q: str | None = None,
    collection_id: str | None = None,
    collection_ids: list[str] | None = None,
    ids: list[str] | None = None,
    dataset_type: str | None = None,
    platform: str | None = None,
    sensor: str | None = None,
    product: str | None = None,
    file_extension: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    province: str | None = None,
    kabupaten: str | None = None,
    kecamatan: str | None = None,
    bbox: list[float] | None = None,
    limit: int = 100,
    offset: int = 0,
    sortby: list[dict[str, str]] | None = None,
) -> list[dict[str, Any]]:
    clauses = []
    args: list[Any] = []

    def add(value: Any) -> str:
        args.append(value)
        return f"${len(args)}"

    if q:
        clauses.append(f"search_text @@ plainto_tsquery('simple', {add(q)})")
    if collection_id:
        clauses.append(f"collection_id = {add(collection_id)}")
    if collection_ids:
        clauses.append(f"collection_id = ANY({add(collection_ids)}::text[])")
    if ids:
        clauses.append(f"id = ANY({add(ids)}::uuid[])")
    if dataset_type:
        clauses.append(f"dataset_type = {add(dataset_type)}")
    if platform:
        clauses.append(f"platform = {add(platform)}")
    if sensor:
        clauses.append(f"sensor = {add(sensor)}")
    if product:
        clauses.append(f"product = {add(product)}")
    if file_extension:
        clauses.append(f"file_extension = {add(file_extension)}")
    if date_from:
        date_ref = add(date_from)
        clauses.append(
            f"(acquisition_start >= {date_ref}::timestamptz OR modified_at >= {date_ref}::timestamptz)"
        )
    if date_to:
        date_ref = add(date_to)
        clauses.append(
            f"(acquisition_start <= {date_ref}::timestamptz OR modified_at <= {date_ref}::timestamptz)"
        )
    add_admin_boundary_filter(clauses, add, province, kabupaten, kecamatan)
    if bbox:
        clauses.append(
            "footprint IS NOT NULL AND footprint && ST_MakeEnvelope("
            f"{add(bbox[0])}, {add(bbox[1])}, {add(bbox[2])}, {add(bbox[3])}, 4326)"
        )

    where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    order_sql = build_dataset_order_sql(sortby)
    args.extend([limit, offset])
    rows = await conn.fetch(
        f"""
        SELECT id::text, collection_id, title, dataset_type, source_path, file_name,
               file_extension, platform, sensor, product, acquisition_start,
               acquisition_end, file_size_bytes, modified_at, bbox, properties, stac_item
        FROM datasets
        {where_sql}
        ORDER BY {order_sql}
        LIMIT ${len(args) - 1} OFFSET ${len(args)}
        """,
        *args,
    )
    return [dict(row) for row in rows]


async def count_datasets(
    conn: asyncpg.Connection,
    *,
    q: str | None = None,
    collection_id: str | None = None,
    collection_ids: list[str] | None = None,
    ids: list[str] | None = None,
    dataset_type: str | None = None,
    platform: str | None = None,
    sensor: str | None = None,
    product: str | None = None,
    file_extension: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    province: str | None = None,
    kabupaten: str | None = None,
    kecamatan: str | None = None,
    bbox: list[float] | None = None,
    footprint_only: bool = False,
) -> int:
    clauses = []
    args: list[Any] = []

    def add(value: Any) -> str:
        args.append(value)
        return f"${len(args)}"

    if q:
        clauses.append(f"search_text @@ plainto_tsquery('simple', {add(q)})")
    if collection_id:
        clauses.append(f"collection_id = {add(collection_id)}")
    if collection_ids:
        clauses.append(f"collection_id = ANY({add(collection_ids)}::text[])")
    if ids:
        clauses.append(f"id = ANY({add(ids)}::uuid[])")
    if dataset_type:
        clauses.append(f"dataset_type = {add(dataset_type)}")
    if platform:
        clauses.append(f"platform = {add(platform)}")
    if sensor:
        clauses.append(f"sensor = {add(sensor)}")
    if product:
        clauses.append(f"product = {add(product)}")
    if file_extension:
        clauses.append(f"file_extension = {add(file_extension)}")
    if date_from:
        date_ref = add(date_from)
        clauses.append(
            f"(acquisition_start >= {date_ref}::timestamptz OR modified_at >= {date_ref}::timestamptz)"
        )
    if date_to:
        date_ref = add(date_to)
        clauses.append(
            f"(acquisition_start <= {date_ref}::timestamptz OR modified_at <= {date_ref}::timestamptz)"
        )
    if footprint_only:
        clauses.append("footprint IS NOT NULL")
    add_admin_boundary_filter(clauses, add, province, kabupaten, kecamatan)
    if bbox:
        clauses.append(
            "footprint IS NOT NULL AND footprint && ST_MakeEnvelope("
            f"{add(bbox[0])}, {add(bbox[1])}, {add(bbox[2])}, {add(bbox[3])}, 4326)"
        )

    where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    return await conn.fetchval(f"SELECT count(*) FROM datasets {where_sql}", *args)


def build_dataset_order_sql(sortby: list[dict[str, str]] | None) -> str:
    allowed = {
        "datetime": "coalesce(acquisition_start, modified_at)",
        "properties.datetime": "coalesce(acquisition_start, modified_at)",
        "acquisition_start": "acquisition_start",
        "modified": "modified_at",
        "modified_at": "modified_at",
        "title": "title",
        "platform": "platform",
    }
    order_parts = []
    for sort in sortby or []:
        field = sort.get("field") or sort.get("property")
        expression = allowed.get(str(field))
        if not expression:
            continue
        direction = "ASC" if str(sort.get("direction", "desc")).lower() == "asc" else "DESC"
        order_parts.append(f"{expression} {direction} NULLS LAST")
    order_parts.append("coalesce(acquisition_start, modified_at) DESC NULLS LAST")
    return ", ".join(order_parts)


def add_admin_boundary_filter(
    clauses: list[str],
    add,
    province: str | None,
    kabupaten: str | None,
    kecamatan: str | None,
) -> None:
    level = "kecamatan" if kecamatan else "kabupaten" if kabupaten else "province" if province else ""
    name = kecamatan or kabupaten or province
    if not level or not name:
        return
    clauses.append(
        "footprint IS NOT NULL AND EXISTS ("
        "SELECT 1 FROM admin_boundaries b "
        f"WHERE b.level = {add(level)} AND b.name = {add(name)} "
        "AND ST_Intersects(datasets.footprint, b.geom)"
        ")"
    )


async def get_dataset(conn: asyncpg.Connection, dataset_id: str) -> dict[str, Any] | None:
    row = await conn.fetchrow(
        """
        SELECT id::text, collection_id, title, dataset_type, source_path, file_name,
               file_extension, platform, sensor, product, acquisition_start,
               acquisition_end, file_size_bytes, modified_at, bbox, properties, stac_item
        FROM datasets
        WHERE id = $1::uuid
        """,
        dataset_id,
    )
    return dict(row) if row else None


async def list_collections(conn: asyncpg.Connection) -> list[dict[str, Any]]:
    rows = await conn.fetch(
        """
        SELECT collection_id, count(*) AS item_count,
               min(acquisition_start) AS temporal_start,
               max(acquisition_start) AS temporal_end
        FROM datasets
        GROUP BY collection_id
        ORDER BY collection_id
        """
    )
    return [dict(row) for row in rows]


async def list_scan_runs(conn: asyncpg.Connection, limit: int = 20) -> list[dict[str, Any]]:
    rows = await conn.fetch(
        """
        SELECT id::text, root_path, started_at, finished_at, status,
               scanned_files, indexed_files, updated_files, unchanged_files,
               removed_files, skipped_files, message
        FROM scan_runs
        ORDER BY started_at DESC
        LIMIT $1
        """,
        limit,
    )
    return [dict(row) for row in rows]


async def list_platform_status(conn: asyncpg.Connection) -> list[dict[str, Any]]:
    rows = await conn.fetch(
        """
        SELECT coalesce(platform, 'unknown') AS platform,
               count(*) AS total,
               count(*) FILTER (WHERE dataset_type = 'raster') AS raster,
               count(*) FILTER (WHERE dataset_type = 'vector') AS vector,
               max(updated_at) AS latest_indexed_at
        FROM datasets
        GROUP BY coalesce(platform, 'unknown')
        ORDER BY total DESC, platform
        """
    )
    return [dict(row) for row in rows]


async def list_recent_sources(conn: asyncpg.Connection, limit: int = 40) -> list[dict[str, Any]]:
    rows = await conn.fetch(
        """
        SELECT id::text, platform, sensor, collection_id, source_path, file_name,
               regexp_replace(source_path, '/[^/]+$', '') AS folder,
               file_size_bytes, updated_at
        FROM datasets
        ORDER BY updated_at DESC
        LIMIT $1
        """,
        limit,
    )
    return [dict(row) for row in rows]


async def list_locations(
    conn: asyncpg.Connection, province: str | None = None, kabupaten: str | None = None
) -> dict[str, list[dict[str, Any]]]:
    provinces = await conn.fetch(
        """
        SELECT code, name
        FROM admin_boundaries
        WHERE level = 'province'
        ORDER BY name
        """
    )
    kabupaten_args: list[Any] = []
    kabupaten_where = "level = 'kabupaten'"
    if province:
        kabupaten_args.append(province)
        kabupaten_where += " AND province = $1"
        kabupaten_rows = await conn.fetch(
            f"""
            SELECT code, name, province
            FROM admin_boundaries
            WHERE {kabupaten_where}
            ORDER BY name
            """,
            *kabupaten_args,
        )
    else:
        kabupaten_rows = []
    kecamatan_args: list[Any] = []
    kecamatan_where = "level = 'kecamatan'"
    if province:
        kecamatan_args.append(province)
        kecamatan_where += f" AND province = ${len(kecamatan_args)}"
    if kabupaten:
        kecamatan_args.append(kabupaten)
        kecamatan_where += f" AND kabupaten = ${len(kecamatan_args)}"
    if province and kabupaten:
        kecamatan_rows = await conn.fetch(
            f"""
            SELECT code, name, province, kabupaten
            FROM admin_boundaries
            WHERE {kecamatan_where}
            ORDER BY name
            """,
            *kecamatan_args,
        )
    else:
        kecamatan_rows = []
    return {
        "provinces": [dict(row) for row in provinces],
        "kabupaten": [dict(row) for row in kabupaten_rows],
        "kecamatan": [dict(row) for row in kecamatan_rows],
    }


async def get_boundary_geojson(
    conn: asyncpg.Connection, level: str, name: str | None = None, code: str | None = None
) -> dict[str, Any] | None:
    row = await conn.fetchrow(
        """
        SELECT jsonb_build_object(
          'type', 'Feature',
          'properties', jsonb_build_object(
            'level', level,
            'code', code,
            'name', name,
            'province', province,
            'kabupaten', kabupaten,
            'kecamatan', kecamatan
          ) || properties,
          'geometry', ST_AsGeoJSON(geom)::jsonb
        ) AS feature
        FROM admin_boundaries
        WHERE level = $1
          AND ($2::text IS NULL OR name = $2)
          AND ($3::text IS NULL OR code = $3)
        LIMIT 1
        """,
        level,
        name,
        code,
    )
    return row["feature"] if row else None


async def upsert_admin_boundary(
    conn: asyncpg.Connection,
    *,
    boundary_id: str,
    level: str,
    name: str,
    code: str,
    province: str | None,
    kabupaten: str | None,
    kecamatan: str | None,
    geometry: dict[str, Any],
    properties: dict[str, Any],
) -> None:
    await conn.execute(
        """
        INSERT INTO admin_boundaries (
          id, level, province, kabupaten, kecamatan, name, code, geom, properties
        )
        VALUES (
          $1, $2, $3, $4, $5, $6, $7,
          ST_Multi(ST_SetSRID(ST_GeomFromGeoJSON($8), 4326)),
          $9::jsonb
        )
        ON CONFLICT (level, code) DO UPDATE SET
          province = EXCLUDED.province,
          kabupaten = EXCLUDED.kabupaten,
          kecamatan = EXCLUDED.kecamatan,
          name = EXCLUDED.name,
          geom = EXCLUDED.geom,
          properties = EXCLUDED.properties
        """,
        boundary_id,
        level,
        province,
        kabupaten,
        kecamatan,
        name,
        code,
        json.dumps(geometry),
        json.dumps(properties),
    )


async def get_catalog_status(conn: asyncpg.Connection) -> dict[str, Any]:
    dataset_row = await conn.fetchrow(
        """
        SELECT count(*) AS total_datasets,
               count(*) FILTER (WHERE dataset_type = 'raster') AS raster_datasets,
               count(*) FILTER (WHERE dataset_type = 'vector') AS vector_datasets,
               count(DISTINCT collection_id) AS collections,
               max(updated_at) AS latest_indexed_at
        FROM datasets
        """
    )
    latest_run = await conn.fetchrow(
        """
        SELECT id::text, root_path, started_at, finished_at, status,
               scanned_files, indexed_files, updated_files, unchanged_files,
               removed_files, skipped_files, message
        FROM scan_runs
        ORDER BY started_at DESC
        LIMIT 1
        """
    )
    return {
        "datasets": dict(dataset_row) if dataset_row else {},
        "latest_scan_run": dict(latest_run) if latest_run else None,
    }
