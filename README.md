# GeoCatalog

<p align="center">
  <img src="images/geocatalog_logo.png" alt="BRIN GeoCatalog logo" width="220">
</p>

BRIN GeoCatalog is a standalone satellite and geospatial data catalog.

The catalog discovers files, extracts available metadata, indexes footprints and temporal metadata, and exposes the indexed datasets through a web interface, a read-only REST API, a standards-grade STAC API, and Open Data Cube metadata exports.

## Goals

- Scan mounted folders without moving or rewriting source files.
- Recognize satellite imagery and geospatial data products from filenames, sidecar files, and internal metadata.
- Index search metadata in PostgreSQL/PostGIS.
- Support text, time, administrative boundary, coordinate, radius, bounding-box, and visual map search.
- Publish a full STAC API with 26 conformance classes via stac-fastapi-pgstac + PgSTAC.
- Generate Open Data Cube compatible product and dataset documents.
- Run every service through Docker Compose.

## Current Functional Scope

- Folder scanning and metadata indexing for satellite imagery and geospatial files under mounted source folders.
- Resume checkpoints for interrupted scans, plus folder-level removal detection so moved/deleted files can be reflected in the catalog.
- Spatial footprint extraction for supported raster files, with continuous backfill support.
- Indonesian administrative boundary enrichment for province, kabupaten/kota, and kecamatan filters.
- Read-only catalog API for datasets, scan runs, service status, source files, administrative boundaries, and ODC-style metadata.
- Production STAC API served by stac-fastapi-pgstac + PgSTAC, populated by the incremental STAC sync service.
- Frontend map visualization with OpenStreetMap and ESRI World Imagery basemaps.

## Services

### Default services (always started)

| Service | Description | Port |
|---|---|---|
| `db` | PostgreSQL/PostGIS catalog database | 55432 |
| `api` | Catalog REST API | 8010 |
| `frontend` | MapLibre web interface + nginx reverse proxy | 8090 |
| `pgstac-db` | PostgreSQL/PostGIS database for PgSTAC | 55433 |
| `pgstac-migrate` | One-shot PgSTAC schema migration (runs on startup) | — |
| `stac-api` | stac-fastapi-pgstac 6.2.2 — full STAC API | 8012 |

### Manual utility services (`--profile tools`)

| Service | Description |
|---|---|
| `worker` | One-shot CLI utility for scans, reference imports, and manual STAC syncs |

### Background loop services (`--profile service`)

| Service | Description |
|---|---|
| `worker-service` | Continuous scanner loop |
| `stac-sync-service` | Incremental STAC sync from catalog DB to PgSTAC (every 10 min) |
| `footprint-backfill-service` | Continuous spatial footprint extraction for HDF4/MODIS files |

Operational notes:

- `worker-service`, `footprint-backfill-service`, and `stac-sync-service` are optional long-running services enabled through `--profile service`.
- `worker` is a manual utility container enabled through `--profile tools`.
- `api` mounts `./logs:/app/logs:ro` so `/api/v1/services` can report the latest STAC sync checkpoint written by `stac-sync-service`.
- Log timestamps are emitted in UTC (`+00:00`). Indonesia WIB is UTC+7.

## Technology Stack

- Python 3.14 for processing and API services.
- FastAPI for the catalog API.
- PostgreSQL + PostGIS for spatial indexing.
- PgSTAC + stac-fastapi-pgstac 6.2.2 for the production STAC API (26 conformance classes).
- React + TypeScript + Vite + MapLibre for the frontend.
- nginx as the frontend reverse proxy (routes `/api/` → catalog API, `/stac/` → STAC API).
- Docker Compose for local and production-style deployment.

## Quick Start

Copy the example environment file if you want to override ports or scan paths:

```bash
cp .env.example .env
```

Build default containers:

```bash
docker compose build
```

Build optional background and manual utility containers:

```bash
docker compose --profile service --profile tools build
```

Start all default services (catalog DB, API, frontend, PgSTAC database, STAC API):

```bash
docker compose up -d
```

Start background loop services as well:

```bash
docker compose --profile service up -d
```

Default startup does not run a scan. Use the manual `worker` command for one-off jobs, or start `worker-service` when you want continuous rescans.

### Fresh Installation

For a completely fresh installation, stop every service and delete the Compose volumes first. This removes the catalog database, PgSTAC database, and synced STAC records:

```bash
docker compose --profile service --profile tools down -v
rm -rf logs/*
docker compose --profile service --profile tools build
docker compose up -d
docker compose --profile service up -d
```

The `logs/` cleanup removes scan and STAC sync checkpoints so the worker starts from the beginning.

Then reload the Indonesian administrative boundaries and restart indexing:

```bash
docker compose run --rm worker geocatalog import-reference --level province --file /app/data/reference/provinces.geojson
docker compose run --rm worker geocatalog import-reference --level kabupaten --file /app/data/reference/kabupaten.geojson
docker compose run --rm worker geocatalog import-reference --level kecamatan --file /app/data/reference/kecamatan.geojson
docker compose --profile service up -d worker-service footprint-backfill-service stac-sync-service
```

## Database Migrations

GeoCatalog currently uses two migration mechanisms.

### Catalog Database

The catalog database schema lives in `db/init/001_schema.sql` and is mounted into the `db` container at `/docker-entrypoint-initdb.d`.

PostgreSQL runs this file automatically only when the `geocatalog-postgres-data` volume is created for the first time. It creates:

- PostGIS, `pg_trgm`, and `pgcrypto` extensions.
- `datasets`
- `scan_runs`
- `scan_checkpoints`
- `admin_boundaries`
- Search, temporal, collection, and spatial indexes.

The schema file includes idempotent `CREATE ... IF NOT EXISTS` and selected `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` statements. This helps fresh installs and some incremental upgrades, but it is not yet a full migration framework for already-running production databases.

For a fresh catalog database, use the fresh-install flow above. To recreate only the catalog database volume, first confirm the Compose volume name, then remove that volume while the database service is stopped:

```bash
docker compose stop db
docker volume ls | grep geocatalog-postgres-data
docker volume rm geocatalog_geocatalog-postgres-data
docker compose up -d db
```

For an existing database, inspect the current schema before applying manual SQL:

```bash
docker compose exec db psql -U geocatalog -d geocatalog -c "\dt"
docker compose exec db psql -U geocatalog -d geocatalog -c "\d datasets"
docker compose exec db psql -U geocatalog -d geocatalog -c "\d scan_runs"
```

If `db/init/001_schema.sql` gains a new idempotent `ALTER TABLE` statement, apply it manually to an existing database with care:

```bash
docker compose exec db psql -U geocatalog -d geocatalog -f /docker-entrypoint-initdb.d/001_schema.sql
```

Back up production data before running schema updates manually.

### PgSTAC Database

PgSTAC uses its own migration command. The `pgstac-migrate` service runs:

```bash
pypgstac migrate --dsn postgresql://...
```

It runs during default Compose startup after `pgstac-db` is healthy, and `stac-api` waits for it to complete successfully.

Run or rerun PgSTAC migration manually:

```bash
docker compose up pgstac-migrate
```

Verify PgSTAC migration status and tables:

```bash
docker compose exec pgstac-db psql -U pgstac -d pgstac -c "SELECT * FROM pgstac.migrations ORDER BY version DESC LIMIT 5;"
docker compose exec pgstac-db psql -U pgstac -d pgstac -c "SELECT count(*) FROM pgstac.items;"
docker compose exec pgstac-db psql -U pgstac -d pgstac -c "SELECT id FROM pgstac.collections ORDER BY id;"
```

PgSTAC data can be rebuilt from the catalog database by rerunning STAC sync after migration:

```bash
docker compose run --rm worker geocatalog stac sync
```

Run one scan of the mounted data folder:

```bash
docker compose run --rm worker geocatalog scan --root /data/geomimo
```

`/data/geomimo` is the path inside the worker container. By default Docker Compose mounts host `/mnt/geomimo-data` to that container path. To scan a different host folder, set `GEOCATALOG_SCAN_ROOT` in `.env`.

The scanner prints a start line and reports progress every 1000 supported candidate files. To make progress more frequent during testing:

```bash
docker compose run --rm worker geocatalog scan --root /data/geomimo --progress-interval 100
```

Running scans save a resume checkpoint every 100 processed candidate files by default. If the worker container is restarted before the scan completes, the next scan for the same `--root` resumes after the last checkpointed full path:

```bash
docker compose run --rm worker geocatalog scan --root /data/geomimo --checkpoint-interval 50
```

Disable resume for a deliberate full restart from the beginning:

```bash
docker compose run --rm worker geocatalog scan --root /data/geomimo --no-resume
```

Enable verbose folder enter/leave logs when debugging traversal:

```bash
docker compose run --rm worker geocatalog scan --root /data/geomimo --debug
```

Run the scanner continuously:

```bash
docker compose --profile service up worker-service
```

Stop only the continuous scanner:

```bash
docker compose --profile service stop worker-service
```

Run continuous scene-footprint backfill for indexed rasters:

```bash
docker compose --profile service up footprint-backfill-service
```

Run all background loop services together:

```bash
docker compose --profile service up worker-service footprint-backfill-service stac-sync-service
```

Track a running or completed scan from the logs:

```bash
docker compose logs -f worker-service
docker compose logs -f stac-sync-service
docker compose logs -f footprint-backfill-service
```

Track scan status through the API:

```bash
curl http://localhost:8010/api/v1/status
curl http://localhost:8010/api/v1/services
curl http://localhost:8010/api/v1/scan-runs
```

Inspect database content from the host:

```bash
docker compose exec db psql -U geocatalog -d geocatalog -c "SELECT status, started_at, finished_at, scanned_files, indexed_files, updated_files, unchanged_files, removed_files, skipped_files FROM scan_runs ORDER BY started_at DESC LIMIT 5;"
docker compose exec db psql -U geocatalog -d geocatalog -c "SELECT platform, count(*) FROM datasets GROUP BY platform ORDER BY count DESC;"
docker compose exec db psql -U geocatalog -d geocatalog -c "SELECT count(*) AS total, count(*) FILTER (WHERE footprint IS NOT NULL) AS with_footprint FROM datasets;"
```

Inspect STAC sync checkpoint visibility:

```bash
docker compose exec stac-sync-service cat /app/logs/stac_sync_state.json
docker compose exec api cat /app/logs/stac_sync_state.json
```

Load Indonesian administrative reference boundaries:

```bash
docker compose run --rm worker geocatalog import-reference --level province --file /app/data/reference/provinces.geojson
docker compose run --rm worker geocatalog import-reference --level kabupaten --file /app/data/reference/kabupaten.geojson
docker compose run --rm worker geocatalog import-reference --level kecamatan --file /app/data/reference/kecamatan.geojson
```

## Rebuilding After Code Changes

Because services share a Dockerfile but produce separate images, rebuild only the affected service:

```bash
# After changes to scanner.py, stac_sync.py, cli.py, or any src/ file:
docker compose --profile tools build worker

# Each profile service uses its own image — rebuild them separately:
docker compose --profile service build worker-service
docker compose --profile service build footprint-backfill-service
docker compose --profile service build stac-sync-service

# stac-api / pgstac-migrate share services/stac-api/Dockerfile:
docker compose build stac-api pgstac-migrate

# After changes to frontend/src/ or frontend/nginx.conf:
docker compose build frontend
```

Then restart only the rebuilt service without touching the others:

```bash
docker compose up -d --no-deps <service-name>
```

The `--profile` option belongs immediately after `docker compose`, for example `docker compose --profile service build`, not `docker compose build --profile service`.

If a Compose volume definition changes, recreate the affected container instead of only rebuilding:

```bash
docker compose up -d --force-recreate api
```

## Access Points

| Interface | URL |
|---|---|
| Web frontend | `http://localhost:8090` |
| Catalog REST API | `http://localhost:8010/api/v1` |
| Catalog API interactive docs | `http://localhost:8010/docs` |
| **STAC API root** | **`http://localhost:8090/stac/`** |
| **STAC API interactive docs** | **`http://localhost:8090/stac/api.html`** |
| STAC API (direct, bypassing nginx) | `http://localhost:8012` |
| Catalog PostGIS (host tools) | `localhost:55432` |
| PgSTAC PostGIS (host tools) | `localhost:55433` |

The STAC API is served by stac-fastapi-pgstac and exposed through the nginx frontend proxy at `/stac/`. Use `http://localhost:8090/stac/` as the primary STAC entry point.

## Catalog REST API Overview

- `GET /api/v1/health`
- `GET /api/v1/status`
- `GET /api/v1/services`
- `GET /api/v1/platforms`
- `GET /api/v1/scan-runs`
- `GET /api/v1/source-files`
- `GET /api/v1/datasets`
- `GET /api/v1/datasets/{dataset_id}`
- `GET /api/v1/datasets/{dataset_id}/odc`
- `GET /api/v1/datasets/{dataset_id}/download`
- `GET /api/v1/search`
- `GET /api/v1/locations`
- `GET /api/v1/boundary`

`/api/v1/services` is a lightweight operational endpoint used by the frontend detail rail. It reports frontend/API reachability, catalog database status, latest worker progress, STAC sync checkpoint status, and footprint-backfill availability.

## Frontend

The web frontend is available at `http://localhost:8090`.

Current features:

- Indonesia-centered MapLibre map.
- OpenStreetMap street basemap and ESRI World Imagery satellite basemap.
- Sidebar filters ordered as text, platform, type/sensor, date, province, kabupaten/kota, and kecamatan.
- Text filter supports normal full-text search and wildcard search. Use `*` for any characters and `?` for one character, for example `LC08*QA*` or `a1.210??.*`.
- Area selection from the map by drawing a bounding box.
- Dataset rail paginated at 20 records per page.
- Detail rail with collapsible sections ordered as Status by Platform, Recent Runs, Service Status, and Source Files.
- Source Files section shows the latest 5 processed files.
- Selected Dataset inspector shows source path, collection, platform, sensor, file size, acquisition/modified times, bbox, and STAC link.

Temporarily disabled frontend actions:

- Map GeoJSON download button.
- Selected Dataset Download button.
- Selected Dataset ODC button.

The REST endpoints for dataset download and ODC metadata still exist, but the frontend buttons are disabled until asset access and ODC workflow details are finalized.

## STAC API

GeoCatalog serves a full STAC API via **stac-fastapi-pgstac 6.2.2** backed by a dedicated PgSTAC database.

**Interactive API documentation:** `http://localhost:8090/stac/api.html`

### Conformance

26 conformance classes including:

- STAC Core, Item Search, Collections
- OGC API — Features (core, fields, sort, query)
- Fields, Sort, Filter/CQL2 (basic, JSON, text)
- Collection Search (filter, sort, fields, free-text, query)

Check conformance:

```bash
curl "http://localhost:8090/stac/conformance" | jq '.conformsTo[]'
```

### STAC Sync

The `stac-sync-service` runs every 10 minutes and syncs datasets from the catalog database into PgSTAC. Files are grouped into multi-asset STAC Items by scene:

| Platform | Grouping | STAC Item ID example |
|---|---|---|
| Landsat-8 / Landsat-9 | Scene ID prefix | `LC09_L1TP_128058_20230417_20230417_02_T1` |
| Sentinel-2 A/B/C | Tile + sensing datetime | `T47MBU_20260401T030541` |
| MODIS / VIIRS | Product + date + tile | `MOD09GA.A2024105.h28v08` |
| All others | One file = one STAC Item | geocatalog dataset UUID |

Datasets without a valid spatial footprint (bbox IS NULL) are excluded from STAC — PgSTAC requires non-null geometry.

The sync service writes its last successful checkpoint to `logs/stac_sync_state.json`. Manual one-shot syncs do not update that loop checkpoint.

Run a one-shot sync manually:

```bash
docker compose run --rm worker geocatalog stac sync
```

### STAC API Examples

List all collections:

```bash
curl "http://localhost:8090/stac/collections" | jq '[.collections[].id]'
```

Browse items in a collection (token-based pagination):

```bash
curl "http://localhost:8090/stac/collections/landsat-9-oli-tirs/items?limit=5" | jq
```

Spatial and temporal search:

```bash
curl "http://localhost:8090/stac/search?collections=landsat-9-oli-tirs&bbox=103,-7,106,-4&datetime=2023-01-01T00:00:00Z/2023-12-31T23:59:59Z&limit=5" | jq
```

POST search with field selection:

```bash
curl -X POST "http://localhost:8090/stac/search" \
  -H "Content-Type: application/json" \
  -d '{
    "collections": ["landsat-9-oli-tirs"],
    "datetime": "2023-01-01T00:00:00Z/2023-12-31T23:59:59Z",
    "limit": 5,
    "fields": {"include": ["id", "properties.datetime", "bbox", "assets"]}
  }' | jq
```

CQL2 JSON filter:

```bash
curl -X POST "http://localhost:8090/stac/search" \
  -H "Content-Type: application/json" \
  -d '{
    "filter-lang": "cql2-json",
    "filter": {
      "op": "and",
      "args": [
        {"op": "in", "args": [{"property": "collection"}, ["landsat-9-oli-tirs"]]},
        {"op": ">=", "args": [{"property": "datetime"}, "2023-04-01T00:00:00Z"]}
      ]
    },
    "limit": 5
  }' | jq
```

Pagination uses token-based cursors (`next` / `prev` links in the response). `numberMatched` is `null` by default — PgSTAC disables row counting for performance.

## Search Modes

The catalog API supports:

- Free text search with optional `*` and `?` wildcards
- Dataset type, source, platform, and sensor filters
- Date/time range search
- Province, kabupaten/kota, and kecamatan filters
- Point and radius search
- Bounding-box search
- Map-drawn polygon search

Administrative boundary search requires province, kabupaten/kota, and kecamatan reference tables to be loaded into PostGIS.

## Current Satellite Recognition

The scanner recognizes these satellite/platform families from folder names and filenames:

- Aqua / Terra: MODIS
- SNPP / NOAA-20: VIIRS
- Landsat-8 / Landsat-9: OLI-TIRS
- Sentinel-1A: C-SAR
- Sentinel-2A / Sentinel-2B / Sentinel-2C: MSI
- Gaofen-1 / Gaofen-1B / Gaofen-1C / Gaofen-1D: PMS, WFV, WFC, or generic optical
- GeoEye-1: GEIS
- Pleiades-1A / Pleiades-1B: HiRI
- Pleiades-Neo3 / Pleiades-Neo4: Neo Imager
- SPOT-6 / SPOT-7: NAOMI
- WorldView-2 / WorldView-3: WV110
- ZiYuan-302: MUX

Supported file formats: GeoTIFF, JP2/J2K, NITF, IMG, VRT, HDF/HDF4, HDF5, NetCDF, GeoJSON, GeoPackage, Shapefile, and FlatGeobuf.

## License

![License: BSD 3-Clause](https://img.shields.io/badge/license-BSD%203--Clause-blue.svg)

This project is licensed under the [BSD 3-Clause License](LICENSE).

Copyright (c) 2026, Andria Arisal (BRIN).
