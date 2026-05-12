"""
Sync geocatalog indexed datasets to PgSTAC.

Design:
- GeoCatalog DB is the source of truth for file discovery and metadata.
- This module reads from the geocatalog datasets table, groups files into
  scene-level STAC Items, builds STAC Collections and Items, and loads
  them into PgSTAC via pypgstac.
- Grouping strategy per platform:
    Landsat-8/9  : multi-band files → one item per scene ID
    Sentinel-2   : multi-band files → one item per tile+datetime granule
    MODIS/VIIRS  : typically one HDF per granule, grouped by product+date+tile
    All others   : one file → one item (generic fallback)
- The sync is idempotent: upsert mode is always used.
- Deletion propagation: orphaned PgSTAC items (present in PgSTAC but removed
  from geocatalog) are deleted after each successful upsert pass.
"""
from __future__ import annotations

import io
import json
import logging
import re
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import asyncpg

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Platform sets
# ---------------------------------------------------------------------------

LANDSAT_PLATFORMS: frozenset[str] = frozenset({"landsat-8", "landsat-9", "landsat"})
SENTINEL2_PLATFORMS: frozenset[str] = frozenset({"sentinel-2a", "sentinel-2b", "sentinel-2"})
MODIS_PLATFORMS: frozenset[str] = frozenset(
    {"terra", "aqua", "terra-modis", "aqua-modis", "modis"}
)
VIIRS_PLATFORMS: frozenset[str] = frozenset(
    {"suomi-npp", "noaa-20", "suomi-npp-viirs", "noaa-20-viirs", "viirs"}
)

# Platforms where multiple files map to one STAC Item
GROUPED_PLATFORMS: frozenset[str] = (
    LANDSAT_PLATFORMS | SENTINEL2_PLATFORMS | MODIS_PLATFORMS | VIIRS_PLATFORMS
)

# ---------------------------------------------------------------------------
# Scene / granule ID regexes
# ---------------------------------------------------------------------------

# Landsat Collection 2: LC08_L1TP_124064_20250707_20250707_02_RT
_LANDSAT_SCENE_RE = re.compile(
    r"^(L[COEST]\d{2}_\w+_\d{6}_\d{8}_\d{8}_\d{2}_(?:RT|T1|T2))",
    re.IGNORECASE,
)

# Sentinel-2 short COG/ARD format: T47NTJ_20220518T030551_B02_10m.jp2
_S2_SHORT_RE = re.compile(
    r"^(T\d{2}[A-Z]{3}_\d{8}T\d{6})_(\w+?)(?:_\d+m)?$",
    re.IGNORECASE,
)
# Sentinel-2 long SAFE format: S2A_MSIL2A_20220518T030551_N0400_R032_T47NTJ_..._B02
_S2_LONG_RE = re.compile(
    r"^S2[AB]_MSI\w+?_(\d{8}T\d{6})_N\d+_R\d+_(T\d{2}[A-Z]{3})_",
    re.IGNORECASE,
)

# MODIS gridded: MOD09GA.A2024105.h28v08.061.2024107023547.hdf
# VIIRS gridded: VNP09GA.A2024106.h27v08.002.2024107214722.h5
# MODIS swath legacy: a1.21001.1751.geo.hdf / a1.21014.0457.mod14.hdf
# Capture the granule key while ignoring product suffixes, versions, and
# processing timestamps.
_MODIS_VIIRS_GRANULE_RE = re.compile(
    r"^(((?:MOD|MYD|MCD|VNP|VJ1|VJ2)\w+\.A\d{7}\.h\d{2}v\d{2})|([at]\d\.\d{5}\.\d{4}))",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Band metadata
# ---------------------------------------------------------------------------

_LANDSAT_BAND_META: dict[str, dict[str, Any]] = {
    "B1":        {"common_name": "coastal",  "gsd": 30,  "roles": ["data", "reflectance"]},
    "B2":        {"common_name": "blue",     "gsd": 30,  "roles": ["data", "reflectance"]},
    "B3":        {"common_name": "green",    "gsd": 30,  "roles": ["data", "reflectance"]},
    "B4":        {"common_name": "red",      "gsd": 30,  "roles": ["data", "reflectance"]},
    "B5":        {"common_name": "nir08",    "gsd": 30,  "roles": ["data", "reflectance"]},
    "B6":        {"common_name": "swir16",   "gsd": 30,  "roles": ["data", "reflectance"]},
    "B7":        {"common_name": "swir22",   "gsd": 30,  "roles": ["data", "reflectance"]},
    "B8":        {"common_name": "pan",      "gsd": 15,  "roles": ["data"]},
    "B9":        {"common_name": "cirrus",   "gsd": 30,  "roles": ["data", "reflectance"]},
    "B10":       {"common_name": "lwir11",   "gsd": 100, "roles": ["data"]},
    "B11":       {"common_name": "lwir12",   "gsd": 100, "roles": ["data"]},
    "QA_PIXEL":  {"roles": ["cloud", "qa"]},
    "QA_RADSAT": {"roles": ["saturation", "qa"]},
    "SAA":       {"roles": ["metadata"]},
    "SZA":       {"roles": ["metadata"]},
    "VAA":       {"roles": ["metadata"]},
    "VZA":       {"roles": ["metadata"]},
}

# Sentinel-2 band key (uppercase stem suffix) → asset metadata
# gsd: 10m bands = B02/B03/B04/B08/TCI, 20m = B05/B06/B07/B8A/B11/B12/SCL, 60m = B01/B09/B10
_SENTINEL2_BAND_META: dict[str, dict[str, Any]] = {
    "B01": {"common_name": "coastal",   "gsd": 60, "roles": ["data", "reflectance"]},
    "B02": {"common_name": "blue",      "gsd": 10, "roles": ["data", "reflectance"]},
    "B03": {"common_name": "green",     "gsd": 10, "roles": ["data", "reflectance"]},
    "B04": {"common_name": "red",       "gsd": 10, "roles": ["data", "reflectance"]},
    "B05": {"common_name": "rededge",   "gsd": 20, "roles": ["data", "reflectance"]},
    "B06": {"common_name": "rededge",   "gsd": 20, "roles": ["data", "reflectance"]},
    "B07": {"common_name": "rededge",   "gsd": 20, "roles": ["data", "reflectance"]},
    "B08": {"common_name": "nir",       "gsd": 10, "roles": ["data", "reflectance"]},
    "B8A": {"common_name": "nir08",     "gsd": 20, "roles": ["data", "reflectance"]},
    "B09": {"common_name": "nir09",     "gsd": 60, "roles": ["data", "reflectance"]},
    "B10": {"common_name": "cirrus",    "gsd": 60, "roles": ["data", "reflectance"]},
    "B11": {"common_name": "swir16",    "gsd": 20, "roles": ["data", "reflectance"]},
    "B12": {"common_name": "swir22",    "gsd": 20, "roles": ["data", "reflectance"]},
    "SCL": {"gsd": 20, "roles": ["data", "cloud"]},
    "TCI": {"gsd": 10, "roles": ["visual"]},
    "WVP": {"gsd": 10, "roles": ["data"]},
    "AOT": {"gsd": 10, "roles": ["data"]},
}

_EXT_MEDIA_TYPE: dict[str, str] = {
    ".tif":  "image/tiff; application=geotiff",
    ".tiff": "image/tiff; application=geotiff",
    ".jp2":  "image/jp2",
    ".j2k":  "image/jp2",
    ".nc":   "application/x-netcdf",
    ".nc4":  "application/x-netcdf",
    ".hdf":  "application/x-hdf",
    ".h5":   "application/x-hdf5",
    ".he5":  "application/x-hdf5",
    ".json": "application/json",
    ".xml":  "application/xml",
    ".txt":  "text/plain",
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _stac_dt(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _media_type(file_name: str) -> str:
    ext = Path(file_name).suffix.lower()
    return _EXT_MEDIA_TYPE.get(ext, "application/octet-stream")


def _asset_href(api_base_url: str, dataset_id: str) -> str:
    return f"{api_base_url.rstrip('/')}/api/v1/datasets/{dataset_id}/download"


def _parse_stac_item_field(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except Exception:
            return {}
    return {}


def _bbox_to_polygon(bbox: list[float]) -> dict[str, Any]:
    w, s, e, n = bbox
    return {"type": "Polygon", "coordinates": [[[w, s], [e, s], [e, n], [w, n], [w, s]]]}


def _best_bbox_geometry(rows: list[dict[str, Any]]) -> tuple[list[float] | None, dict | None]:
    """Return (bbox, geometry) from the first row that has spatial data.

    Falls back to a bbox-derived polygon when no stored footprint exists so
    PgSTAC's non-null geometry constraint is always satisfied.
    """
    for ds in rows:
        bbox = ds.get("bbox")
        if not bbox or len(bbox) != 4:
            continue
        bbox_f = list(map(float, bbox))
        geom = _parse_stac_item_field(ds.get("stac_item")).get("geometry")
        if geom is None:
            geom = _bbox_to_polygon(bbox_f)
        return bbox_f, geom
    return None, None


def _props_with_dt(
    dt: str | None,
    dt_end: str | None,
    platform: str | None,
    instruments: list[str],
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build STAC properties dict, handling null datetime per spec."""
    props: dict[str, Any] = {
        "datetime": dt,
        "platform": platform,
        "instruments": instruments,
    }
    if dt is None:
        props["start_datetime"] = dt_end or "1970-01-01T00:00:00Z"
        props["end_datetime"] = dt_end or "1970-01-01T00:00:00Z"
    if extra:
        props.update(extra)
    return {k: v for k, v in props.items() if v is not None or k == "datetime"}


# ---------------------------------------------------------------------------
# Scene / granule key extractors
# ---------------------------------------------------------------------------

def _landsat_scene_parts(file_name: str) -> tuple[str | None, str]:
    """Return (scene_id, band_key). band_key is uppercased."""
    stem = Path(file_name).stem.upper()
    m = _LANDSAT_SCENE_RE.match(stem)
    if not m:
        return None, "DATA"
    scene_id = m.group(1)
    band_key = stem[len(scene_id):].lstrip("_") or "DATA"
    return scene_id, band_key


def _sentinel2_scene_parts(file_name: str) -> tuple[str | None, str]:
    """Return (granule_id, band_key) for a Sentinel-2 filename.

    granule_id format: {TILE}_{SENSING_DATETIME} e.g. T47NTJ_20220518T030551
    band_key: B02, B8A, SCL, TCI, etc. (uppercased)
    """
    stem = Path(file_name).stem.upper()
    m = _S2_SHORT_RE.match(stem)
    if m:
        return m.group(1), m.group(2)
    m = _S2_LONG_RE.match(stem)
    if m:
        sensing_dt, tile = m.group(1), m.group(2)
        granule_id = f"{tile}_{sensing_dt}"
        # Extract band key from the end of the stem
        parts = stem.rsplit("_", 1)
        band_key = parts[-1] if len(parts) > 1 else "DATA"
        return granule_id, band_key
    return None, "DATA"


def _modis_viirs_granule_key(file_name: str) -> str | None:
    """Return product+date+tile key for MODIS/VIIRS files.

    e.g. MOD09GA.A2024105.h28v08 from MOD09GA.A2024105.h28v08.061.2024107023547.hdf
    or a1.21001.1751 from a1.21001.1751.geo.hdf / a1.21001.1751.mod14.hdf
    """
    stem = Path(file_name).stem
    m = _MODIS_VIIRS_GRANULE_RE.match(stem)
    return m.group(1) if m else None


def stac_item_id_for_dataset(platform: str | None, file_name: str, fallback_id: str) -> str:
    """Return the PgSTAC item ID used for a geocatalog dataset row."""
    if platform in LANDSAT_PLATFORMS:
        scene_id, _ = _landsat_scene_parts(file_name)
        return scene_id or fallback_id
    if platform in SENTINEL2_PLATFORMS:
        scene_id, _ = _sentinel2_scene_parts(file_name)
        return scene_id or fallback_id
    if platform in MODIS_PLATFORMS | VIIRS_PLATFORMS:
        return _modis_viirs_granule_key(file_name) or fallback_id
    return fallback_id


# ---------------------------------------------------------------------------
# Scene grouping
# ---------------------------------------------------------------------------

def _group_by_scene(
    rows: list[dict[str, Any]],
    key_fn: Any,  # callable(file_name) -> (scene_id | None, band_key)
) -> list[tuple[str, list[dict[str, Any]]]]:
    """Generic grouper: apply key_fn to each row's file_name, group by scene_id."""
    scenes: dict[str, list[dict[str, Any]]] = defaultdict(list)
    ungrouped: list[dict[str, Any]] = []
    for ds in rows:
        scene_id, _ = key_fn(ds["file_name"])
        if scene_id:
            scenes[scene_id].append(ds)
        else:
            ungrouped.append(ds)
    result = list(scenes.items())
    for ds in ungrouped:
        result.append((ds["id"], [ds]))
    return result


def _group_modis_viirs(rows: list[dict[str, Any]]) -> list[tuple[str, list[dict[str, Any]]]]:
    scenes: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for ds in rows:
        key = _modis_viirs_granule_key(ds["file_name"])
        scenes[key or ds["id"]].append(ds)
    return list(scenes.items())


def group_into_items(
    rows: list[dict[str, Any]], platform: str | None,
) -> list[tuple[str, list[dict[str, Any]]]]:
    """Group dataset rows into (item_id, [rows]) tuples based on platform."""
    if platform in LANDSAT_PLATFORMS:
        return _group_by_scene(rows, _landsat_scene_parts)
    if platform in SENTINEL2_PLATFORMS:
        return _group_by_scene(rows, _sentinel2_scene_parts)
    if platform in MODIS_PLATFORMS | VIIRS_PLATFORMS:
        return _group_modis_viirs(rows)
    return [(ds["id"], [ds]) for ds in rows]


# ---------------------------------------------------------------------------
# STAC Item builders
# ---------------------------------------------------------------------------

def _build_banded_item(
    scene_id: str,
    scene_rows: list[dict[str, Any]],
    collection_id: str,
    api_base_url: str,
    band_meta: dict[str, dict[str, Any]],
    band_key_fn: Any,  # callable(file_name) -> (scene_id, band_key)
    default_gsd: int | None = None,
) -> dict[str, Any]:
    """Generic multi-band item builder shared by Landsat and Sentinel-2."""
    scene_rows = sorted(scene_rows, key=lambda d: d["file_name"])
    ref = scene_rows[0]
    bbox, geometry = _best_bbox_geometry(scene_rows)

    assets: dict[str, Any] = {}
    for ds in scene_rows:
        _, band_key = band_key_fn(ds["file_name"])
        asset_key = band_key.lower()
        meta = band_meta.get(band_key.upper(), {})
        asset: dict[str, Any] = {
            "href": _asset_href(api_base_url, ds["id"]),
            "title": ds["file_name"],
            "type": _media_type(ds["file_name"]),
            "roles": meta.get("roles", ["data"]),
        }
        cn = meta.get("common_name")
        if cn:
            asset["eo:bands"] = [{"name": band_key.upper(), "common_name": cn}]
        gsd = meta.get("gsd", default_gsd)
        if gsd is not None:
            asset["gsd"] = gsd
        assets[asset_key] = asset

    dt = _stac_dt(ref.get("acquisition_start"))
    dt_end = _stac_dt(ref.get("acquisition_end"))
    instruments = [ref["sensor"].upper()] if ref.get("sensor") else []
    extra: dict[str, Any] = {}
    if default_gsd is not None:
        extra["gsd"] = default_gsd
    created = _stac_dt(ref.get("created_at"))
    updated = _stac_dt(ref.get("updated_at"))
    if created:
        extra["created"] = created
    if updated:
        extra["updated"] = updated

    props = _props_with_dt(dt, dt_end, ref.get("platform"), instruments, extra)

    return {
        "type": "Feature",
        "stac_version": "1.0.0",
        "id": scene_id,
        "collection": collection_id,
        "geometry": geometry,
        "bbox": bbox,
        "properties": props,
        "assets": assets,
        "links": [],
    }


def _build_modis_viirs_item(
    granule_key: str,
    scene_rows: list[dict[str, Any]],
    collection_id: str,
    api_base_url: str,
) -> dict[str, Any]:
    """Build a STAC Item for a MODIS or VIIRS granule.

    MODIS/VIIRS files are self-contained HDF/HDF5 granules (one file = one
    product). Multiple files sharing the same product+date+tile key (e.g. a
    browse image alongside the science granule) are collected as separate
    assets under the same item.
    """
    scene_rows = sorted(scene_rows, key=lambda d: d["file_name"])
    ref = scene_rows[0]
    bbox, geometry = _best_bbox_geometry(scene_rows)

    assets: dict[str, Any] = {}
    for ds in scene_rows:
        stem = Path(ds["file_name"]).stem
        # Use version+processing timestamp suffix as asset key when available,
        # otherwise fall back to the dataset UUID for uniqueness.
        parts = stem.split(".")
        asset_key = ".".join(parts[3:]) if len(parts) > 3 else ds["id"]
        assets[asset_key] = {
            "href": _asset_href(api_base_url, ds["id"]),
            "title": ds["file_name"],
            "type": _media_type(ds["file_name"]),
            "roles": ["data"],
        }

    dt = _stac_dt(ref.get("acquisition_start"))
    dt_end = _stac_dt(ref.get("acquisition_end"))
    instruments = [ref["sensor"].upper()] if ref.get("sensor") else []
    extra: dict[str, Any] = {}
    created = _stac_dt(ref.get("created_at"))
    updated = _stac_dt(ref.get("updated_at"))
    if created:
        extra["created"] = created
    if updated:
        extra["updated"] = updated

    props = _props_with_dt(dt, dt_end, ref.get("platform"), instruments, extra)

    return {
        "type": "Feature",
        "stac_version": "1.0.0",
        "id": granule_key,
        "collection": collection_id,
        "geometry": geometry,
        "bbox": bbox,
        "properties": props,
        "assets": assets,
        "links": [],
    }


def _build_generic_item(ds: dict[str, Any], api_base_url: str) -> dict[str, Any]:
    stored = _parse_stac_item_field(ds.get("stac_item"))
    raw_bbox = ds.get("bbox")
    bbox = list(map(float, raw_bbox)) if raw_bbox and len(raw_bbox) == 4 else None
    geometry = stored.get("geometry")
    if geometry is None and bbox is not None:
        geometry = _bbox_to_polygon(bbox)

    dt = _stac_dt(ds.get("acquisition_start"))
    dt_end = _stac_dt(ds.get("acquisition_end"))
    instruments = [ds["sensor"].upper()] if ds.get("sensor") else []
    extra: dict[str, Any] = {}
    created = _stac_dt(ds.get("created_at"))
    updated = _stac_dt(ds.get("updated_at"))
    if created:
        extra["created"] = created
    if updated:
        extra["updated"] = updated

    props = _props_with_dt(dt, dt_end, ds.get("platform"), instruments, extra)

    return {
        "type": "Feature",
        "stac_version": "1.0.0",
        "id": ds["id"],
        "collection": ds["collection_id"],
        "geometry": geometry,
        "bbox": bbox,
        "properties": props,
        "assets": {
            "data": {
                "href": _asset_href(api_base_url, ds["id"]),
                "title": ds["file_name"],
                "type": _media_type(ds["file_name"]),
                "roles": ["data"],
            }
        },
        "links": [],
    }


def build_stac_items(
    item_groups: list[tuple[str, list[dict[str, Any]]]],
    collection_id: str,
    platform: str | None,
    api_base_url: str,
) -> list[dict[str, Any]]:
    items = []
    for item_id, group in item_groups:
        if platform in LANDSAT_PLATFORMS:
            items.append(_build_banded_item(
                item_id, group, collection_id, api_base_url,
                _LANDSAT_BAND_META, _landsat_scene_parts, default_gsd=30,
            ))
        elif platform in SENTINEL2_PLATFORMS:
            items.append(_build_banded_item(
                item_id, group, collection_id, api_base_url,
                _SENTINEL2_BAND_META, _sentinel2_scene_parts, default_gsd=None,
            ))
        elif platform in MODIS_PLATFORMS | VIIRS_PLATFORMS:
            items.append(_build_modis_viirs_item(item_id, group, collection_id, api_base_url))
        else:
            items.append(_build_generic_item(group[0], api_base_url))
    return items


# ---------------------------------------------------------------------------
# STAC Collection builder
# ---------------------------------------------------------------------------

def build_stac_collection(collection_id: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    datetimes = [r["acquisition_start"] for r in rows if r.get("acquisition_start")]
    min_dt = _stac_dt(min(datetimes)) if datetimes else None
    max_dt = _stac_dt(max(datetimes)) if datetimes else None

    bboxes = [r["bbox"] for r in rows if r.get("bbox") and len(r["bbox"]) == 4]
    if bboxes:
        spatial_bbox = [
            float(min(b[0] for b in bboxes)),
            float(min(b[1] for b in bboxes)),
            float(max(b[2] for b in bboxes)),
            float(max(b[3] for b in bboxes)),
        ]
    else:
        spatial_bbox = [-180.0, -90.0, 180.0, 90.0]

    platforms = sorted({r["platform"] for r in rows if r.get("platform")})
    title = collection_id.replace("-", " ").title()

    return {
        "type": "Collection",
        "id": collection_id,
        "stac_version": "1.0.0",
        "description": f"GeoCatalog indexed datasets — {collection_id}.",
        "links": [],
        "title": title,
        "extent": {
            "spatial": {"bbox": [spatial_bbox]},
            "temporal": {"interval": [[min_dt, max_dt]]},
        },
        "license": "proprietary",
        "summaries": {"platform": platforms},
    }


# ---------------------------------------------------------------------------
# Database fetch helpers
# ---------------------------------------------------------------------------

async def _list_collection_ids(conn: asyncpg.Connection) -> list[str]:
    rows = await conn.fetch(
        "SELECT DISTINCT collection_id FROM datasets ORDER BY collection_id"
    )
    return [r["collection_id"] for r in rows]


async def _list_changed_collection_ids(
    conn: asyncpg.Connection, since: datetime,
) -> list[str]:
    rows = await conn.fetch(
        """
        SELECT DISTINCT collection_id FROM datasets
        WHERE updated_at > $1 OR created_at > $1
        ORDER BY collection_id
        """,
        since,
    )
    return [r["collection_id"] for r in rows]


_SCENE_KEY_SQL = """
    CASE
        WHEN platform IN ('landsat-8','landsat-9','landsat')
        THEN regexp_replace(
               upper(file_name),
               '^(L[COEST]\\d{2}_\\w+_\\d{6}_\\d{8}_\\d{8}_\\d{2}_(?:RT|T1|T2)).*$',
               '\\1'
             )
        WHEN platform IN ('sentinel-2a','sentinel-2b','sentinel-2')
        THEN regexp_replace(
               upper(file_name),
               '^(T\\d{2}[A-Z]{3}_\\d{8}T\\d{6})_.*$',
               '\\1'
             )
        WHEN platform IN ('terra','aqua','terra-modis','aqua-modis','modis',
                          'suomi-npp','noaa-20','suomi-npp-viirs','noaa-20-viirs','viirs')
        THEN regexp_replace(
               file_name,
               '^(((MOD|MYD|MCD|VNP|VJ1|VJ2)\\w+\\.A\\d{7}\\.h\\d{2}v\\d{2})|([at][0-9]\\.\\d{5}\\.\\d{4})).*$',
               '\\1'
             )
        ELSE id::text
    END
"""


_SELECT_DATASETS = """
    SELECT id::text, collection_id, file_name, file_extension,
           platform, sensor, product,
           acquisition_start, acquisition_end,
           bbox, stac_item, created_at, updated_at
    FROM datasets
"""


async def _fetch_collection_rows(
    conn: asyncpg.Connection,
    collection_id: str,
    since: datetime | None = None,
) -> list[dict[str, Any]]:
    if since is None:
        rows = await conn.fetch(
            f"{_SELECT_DATASETS} WHERE collection_id = $1"
            " ORDER BY acquisition_start NULLS LAST, file_name",
            collection_id,
        )
    else:
        # Fetch all rows belonging to any scene/granule that has at least one
        # changed row, so the assembled STAC Item is always complete.
        rows = await conn.fetch(
            f"""
            WITH changed_scenes AS (
                SELECT DISTINCT {_SCENE_KEY_SQL} AS scene_key
                FROM datasets
                WHERE collection_id = $1
                  AND (updated_at > $2 OR created_at > $2)
            )
            SELECT d.id::text, d.collection_id, d.file_name, d.file_extension,
                   d.platform, d.sensor, d.product,
                   d.acquisition_start, d.acquisition_end,
                   d.bbox, d.stac_item, d.created_at, d.updated_at
            FROM datasets d
            JOIN changed_scenes cs ON (
                CASE
                    WHEN d.platform IN ('landsat-8','landsat-9','landsat')
                    THEN regexp_replace(
                           upper(d.file_name),
                           '^(L[COEST]\\d{{2}}_\\w+_\\d{{6}}_\\d{{8}}_\\d{{8}}_\\d{{2}}_(?:RT|T1|T2)).*$',
                           '\\1'
                         )
                    WHEN d.platform IN ('sentinel-2a','sentinel-2b','sentinel-2')
                    THEN regexp_replace(
                           upper(d.file_name),
                           '^(T\\d{{2}}[A-Z]{{3}}_\\d{{8}}T\\d{{6}})_.*$',
                           '\\1'
                         )
                    WHEN d.platform IN ('terra','aqua','terra-modis','aqua-modis','modis',
                                        'suomi-npp','noaa-20','suomi-npp-viirs','noaa-20-viirs','viirs')
                    THEN regexp_replace(
                           d.file_name,
                           '^(((MOD|MYD|MCD|VNP|VJ1|VJ2)\\w+\\.A\\d{{7}}\\.h\\d{{2}}v\\d{{2}})|([at][0-9]\\.\\d{{5}}\\.\\d{{4}})).*$',
                           '\\1'
                         )
                    ELSE d.id::text
                END = cs.scene_key
            )
            WHERE d.collection_id = $1
            ORDER BY d.acquisition_start NULLS LAST, d.file_name
            """,
            collection_id,
            since,
        )
    return [dict(r) for r in rows]


async def _fetch_expected_item_ids(
    conn: asyncpg.Connection,
    collection_id: str,
    platform: str | None,
) -> set[str]:
    """Return the complete set of STAC item IDs that geocatalog expects in PgSTAC."""
    if platform in GROUPED_PLATFORMS:
        rows = await conn.fetch(
            "SELECT id::text, file_name, platform FROM datasets WHERE collection_id = $1",
            collection_id,
        )
        ids: set[str] = set()
        for r in rows:
            fn = r["file_name"]
            plat = r["platform"]
            ids.add(stac_item_id_for_dataset(plat, fn, r["id"]))
        return ids
    else:
        rows = await conn.fetch(
            "SELECT id::text FROM datasets WHERE collection_id = $1",
            collection_id,
        )
        return {r["id"] for r in rows}


# ---------------------------------------------------------------------------
# PgSTAC write + orphan deletion
# ---------------------------------------------------------------------------

def _write_to_pgstac(
    pgstac_dsn: str,
    collection: dict[str, Any],
    items: list[dict[str, Any]],
    expected_ids: set[str] | None = None,
) -> int:
    """Upsert collection + items into PgSTAC and delete orphaned items.

    Returns the number of items deleted as orphans.
    """
    import psycopg
    from pypgstac.db import PgstacDB
    from pypgstac.load import Loader, Methods

    with PgstacDB(dsn=pgstac_dsn) as db:
        loader = Loader(db=db)
        loader.load_collections(
            io.StringIO(json.dumps(collection)),
            insert_mode=Methods.upsert,
        )
        if items:
            ndjson = "\n".join(json.dumps(item) for item in items)
            loader.load_items(
                io.StringIO(ndjson),
                insert_mode=Methods.upsert,
            )

    n_deleted = 0
    if expected_ids is not None:
        with psycopg.connect(pgstac_dsn) as conn:
            conn.execute("SET search_path TO pgstac, public")
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id FROM items WHERE collection = %s",
                    [collection["id"]],
                )
                pgstac_ids = {row[0] for row in cur.fetchall()}
            orphan_ids = list(pgstac_ids - expected_ids)
            if orphan_ids:
                with conn.cursor() as cur:
                    cur.execute(
                        "DELETE FROM items WHERE collection = %s AND id = ANY(%s)",
                        [collection["id"], orphan_ids],
                    )
                n_deleted = len(orphan_ids)
                logger.info(
                    "collection %s — deleted %d orphaned items from PgSTAC",
                    collection["id"], n_deleted,
                )
    return n_deleted


# ---------------------------------------------------------------------------
# Public sync entry point
# ---------------------------------------------------------------------------

async def run_sync(
    conn: asyncpg.Connection,
    pgstac_dsn: str,
    collection_id: str | None,
    api_base_url: str,
    dry_run: bool = False,
    since: datetime | None = None,
) -> dict[str, int]:
    """
    Sync one or all geocatalog collections to PgSTAC.

    When `since` is given, only collections with rows changed after that
    timestamp are processed, and only the affected scene groups are
    re-built and upserted. Collection metadata always reflects the full
    collection extent.

    Orphaned PgSTAC items (deleted from geocatalog) are removed after each
    successful upsert pass.

    Returns: {"collections": N, "scenes": N, "assets": N,
              "deleted": N, "failed": N}
    """
    if collection_id:
        target_ids = [collection_id]
    elif since is not None:
        target_ids = await _list_changed_collection_ids(conn, since)
        if not target_ids:
            logger.info("stac sync — no changes since %s", since.isoformat())
            return {"collections": 0, "scenes": 0, "assets": 0, "deleted": 0, "failed": 0}
    else:
        target_ids = await _list_collection_ids(conn)

    total_scenes = 0
    total_assets = 0
    total_deleted = 0
    total_failed = 0

    for cid in target_ids:
        rows = await _fetch_collection_rows(conn, cid, since=since)
        if not rows:
            logger.info("collection %s — no datasets, skipping", cid)
            continue

        platform = rows[0].get("platform")
        item_groups = group_into_items(rows, platform)
        items = build_stac_items(item_groups, cid, platform, api_base_url)

        # PgSTAC requires non-null geometry; skip items with no spatial data
        spatial_items = [it for it in items if it.get("geometry") is not None]
        n_skipped = len(items) - len(spatial_items)
        items = spatial_items

        # Collection metadata always reflects the full collection extent
        all_rows = rows if since is None else await _fetch_collection_rows(conn, cid)
        collection = build_stac_collection(cid, all_rows)

        # Expected IDs for orphan detection (full collection scope)
        expected_ids = await _fetch_expected_item_ids(conn, cid, platform)

        n_scenes = len(items)
        n_assets = len(rows)
        tag = " [dry-run]" if dry_run else ""
        skip_note = f", {n_skipped} skipped (no geometry)" if n_skipped else ""
        logger.info(
            "collection %s — %d assets → %d scenes%s%s",
            cid, n_assets, n_scenes, skip_note, tag,
        )

        if not dry_run:
            try:
                n_deleted = _write_to_pgstac(pgstac_dsn, collection, items, expected_ids)
                total_deleted += n_deleted
            except Exception:
                logger.exception("collection %s — pgstac write failed", cid)
                total_failed += 1
                continue

        total_scenes += n_scenes
        total_assets += n_assets

    return {
        "collections": len(target_ids),
        "scenes": total_scenes,
        "assets": total_assets,
        "deleted": total_deleted,
        "failed": total_failed,
    }
