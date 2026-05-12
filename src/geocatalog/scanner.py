from __future__ import annotations

import hashlib
import logging
import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid5, NAMESPACE_URL

logger = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS = {
    ".tif",
    ".tiff",
    ".jp2",
    ".j2k",
    ".ntf",
    ".nitf",
    ".img",
    ".vrt",
    ".hdf",
    ".h5",
    ".he5",
    ".nc",
    ".nc4",
    ".json",
    ".geojson",
    ".gpkg",
    ".shp",
    ".fgb",
}

DATE_PATTERNS = [
    re.compile(r"(?P<year>20\d{2})(?P<month>\d{2})(?P<day>\d{2})"),
    re.compile(r"(?P<year>20\d{2})[-_](?P<month>\d{2})[-_](?P<day>\d{2})"),
]


@dataclass(frozen=True)
class DatasetCandidate:
    id: str
    collection_id: str
    title: str
    dataset_type: str
    source_path: str
    file_name: str
    file_extension: str
    platform: str | None
    sensor: str | None
    product: str | None
    acquisition_start: datetime | None
    acquisition_end: datetime | None
    file_size_bytes: int
    modified_at: datetime
    checksum: str
    bbox: list[float] | None
    footprint_geojson: dict[str, object] | None
    properties: dict[str, object]
    stac_item: dict[str, object]


def iter_supported_files(
    root: Path,
    limit: int | None = None,
    folder_callback: Callable[[str, Path, list[Path]], None] | None = None,
    resume_after: str | None = None,
) -> Iterator[Path]:
    count = 0

    def walk(directory: Path) -> Iterator[Path]:
        nonlocal count
        files: list[Path] = []
        directories: list[Path] = []
        for child in sorted(directory.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower())):
            if child.is_dir():
                directories.append(child)
            elif child.is_file() and child.suffix.lower() in SUPPORTED_EXTENSIONS:
                files.append(child)
        if folder_callback:
            folder_callback("enter", directory, files)
        for file_path in files:
            if limit is not None and count >= limit:
                break
            if resume_after and str(file_path) <= resume_after:
                continue
            count += 1
            yield file_path
        for child_directory in directories:
            if limit is not None and count >= limit:
                break
            yield from walk(child_directory)
        if folder_callback:
            folder_callback("leave", directory, files)

    yield from walk(root)


def inspect_file(path: Path) -> DatasetCandidate:
    stat = path.stat()
    modified_at = datetime.fromtimestamp(stat.st_mtime, tz=UTC)
    acquisition = parse_date(path.name)
    platform, sensor = infer_platform_sensor(path)
    product = infer_product(path)
    collection_id = "-".join(part for part in [platform, sensor, product] if part) or "uncategorized"
    dataset_type = infer_dataset_type(path)
    dataset_id = str(uuid5(NAMESPACE_URL, str(path)))
    checksum = file_fingerprint(path, stat.st_size, stat.st_mtime_ns)
    footprint = extract_footprint(path) if dataset_type == "raster" else None
    properties = {
        "source_path": str(path),
        "relative_parent": str(path.parent),
        "indexed_by": "geocatalog",
    }
    stac_item = {
        "type": "Feature",
        "stac_version": "1.0.0",
        "id": dataset_id,
        "collection": collection_id,
        "geometry": footprint["geometry"] if footprint else None,
        "bbox": footprint["bbox"] if footprint else None,
        "properties": {
            "datetime": acquisition.isoformat() if acquisition else None,
            "platform": platform,
            "instruments": [sensor] if sensor else [],
            "product": product,
        },
        "assets": {
            "data": {
                "href": str(path),
                "title": path.name,
                "roles": ["data"],
            }
        },
    }
    return DatasetCandidate(
        id=dataset_id,
        collection_id=collection_id,
        title=path.stem,
        dataset_type=dataset_type,
        source_path=str(path),
        file_name=path.name,
        file_extension=path.suffix.lower(),
        platform=platform,
        sensor=sensor,
        product=product,
        acquisition_start=acquisition,
        acquisition_end=acquisition,
        file_size_bytes=stat.st_size,
        modified_at=modified_at,
        checksum=checksum,
        bbox=footprint["bbox"] if footprint else None,
        footprint_geojson=footprint["geometry"] if footprint else None,
        properties=properties,
        stac_item=stac_item,
    )


def parse_date(name: str) -> datetime | None:
    for pattern in DATE_PATTERNS:
        match = pattern.search(name)
        if not match:
            continue
        try:
            parts = {key: int(value) for key, value in match.groupdict().items()}
            return datetime(parts["year"], parts["month"], parts["day"], tzinfo=UTC)
        except ValueError:
            return None
    return None


def infer_platform_sensor(path: Path) -> tuple[str | None, str | None]:
    text = normalize_path_text(path)
    if has_any(text, ["sentinel-1a", "/s1a", "s1a-"]):
        return "sentinel-1a", "c-sar"
    if has_any(text, ["sentinel-2a", "/s2a", "s2a-"]):
        return "sentinel-2a", "msi"
    if has_any(text, ["sentinel-2b", "/s2b", "s2b-"]):
        return "sentinel-2b", "msi"
    if has_any(text, ["sentinel-2c", "/s2c", "s2c-"]):
        return "sentinel-2c", "msi"
    if has_any(text, ["sentinel-2", "sentinel2", "/s2"]):
        return "sentinel-2", "msi"
    if has_any(text, ["landsat-8", "landsat8", "lc08", "lo08", "lt08"]):
        return "landsat-8", "oli-tirs"
    if has_any(text, ["landsat-9", "landsat9", "lc09", "lo09", "lt09"]):
        return "landsat-9", "oli-tirs"
    if "landsat" in text:
        return "landsat", "oli-tirs"
    if has_any(text, ["noaa-20", "noaa20", "j01", "jpSS-1".lower()]):
        return "noaa-20", "viirs"
    if "snpp" in text or "suomi" in text:
        return "suomi-npp", "viirs"
    if "aqua" in text:
        return "aqua", "modis"
    if has_any(text, ["terra", "/tera", "mod03", "mod09", "mod14", "modis-terra"]):
        return "terra", "modis"
    if has_any(text, ["gaofen-1b", "gaofen1b", "gf1b", "gf-1b"]):
        return "gaofen-1b", infer_gaofen_sensor(text)
    if has_any(text, ["gaofen-1c", "gaofen1c", "gf1c", "gf-1c"]):
        return "gaofen-1c", infer_gaofen_sensor(text)
    if has_any(text, ["gaofen-1d", "gaofen1d", "gf1d", "gf-1d"]):
        return "gaofen-1d", infer_gaofen_sensor(text)
    if has_any(text, ["gaofen-1", "gaofen1", "gf1", "gf-1"]):
        return "gaofen-1", infer_gaofen_sensor(text)
    if has_any(text, ["geoeye-1", "geoeye1"]):
        return "geoeye-1", "geis"
    if has_any(text, ["pleiades-1a", "pleiades1a", "phr1a"]):
        return "pleiades-1a", "hiri"
    if has_any(text, ["pleiades-1b", "pleiades1b", "phr1b"]):
        return "pleiades-1b", "hiri"
    if has_any(text, ["pleiades-neo3", "pleiades-neo-3"]):
        return "pleiades-neo-3", "neo-imager"
    if has_any(text, ["pleiades-neo4", "pleiades-neo-4"]):
        return "pleiades-neo-4", "neo-imager"
    if has_any(text, ["spot-6", "spot6"]):
        return "spot-6", "naomi"
    if has_any(text, ["spot-7", "spot7"]):
        return "spot-7", "naomi"
    if has_any(text, ["worldview-2", "worldview2", "wv02", "wv-2"]):
        return "worldview-2", "wv110"
    if has_any(text, ["worldview-3", "worldview3", "wv03", "wv-3"]):
        return "worldview-3", "wv110"
    if has_any(text, ["ziyuan-302", "ziyuan-3-02", "zy-302", "zy3-02", "zy302"]):
        return "ziyuan-3-02", "mux"
    return None, None


def infer_product(path: Path) -> str | None:
    text = normalize_path_text(path)
    for product in [
        "ndvi",
        "hotspot",
        "burn",
        "cloud",
        "rain",
        "rgb",
        "pan",
        "panchromatic",
        "multispectral",
        "surface-reflectance",
        "orthorectified",
    ]:
        if product in text:
            return product
    return None


def infer_dataset_type(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in {".tif", ".tiff", ".vrt", ".hdf", ".h5", ".he5", ".nc", ".nc4"}:
        return "raster"
    if ext in {".json", ".geojson", ".gpkg", ".shp", ".fgb"}:
        return "vector"
    return "file"


_HDF4_EXTENSIONS: frozenset[str] = frozenset({".hdf", ".hdf4", ".h4", ".he4"})

# Attribute names used in MODIS ECS CoreMetadata bounding-box groups.
_MODIS_BBOX_ATTRS: dict[str, str] = {
    "west": "WESTBOUNDINGCOORDINATE",
    "east": "EASTBOUNDINGCOORDINATE",
    "north": "NORTHBOUNDINGCOORDINATE",
    "south": "SOUTHBOUNDINGCOORDINATE",
}


def _bbox_geometry(west: float, south: float, east: float, north: float) -> dict[str, object]:
    return {
        "bbox": [west, south, east, north],
        "geometry": {
            "type": "Polygon",
            "coordinates": [
                [[west, south], [east, south], [east, north], [west, north], [west, south]]
            ],
        },
    }


def _extract_footprint_hdf4(path: Path) -> dict[str, object] | None:
    """Extract bbox from an HDF4 file (MODIS Level-1/2/3) via gdalinfo.

    Strategy (tried in order, first success wins):
    1. ``gdalinfo -json`` → ``wgs84Extent``  — georeferenced Level-3 grids/tiles.
    2. ``gdalinfo -json`` → metadata dict for WESTBOUNDINGCOORDINATE etc.
       OR text-mode ``gdalinfo`` searching the ODL CoreMetadata blob  — standard
       NASA MODIS Level-1/2 products.
    3. Lat/lon SDS subdatasets — SeaDAS-processed HDF4 files whose metadata
       carries the geolocation as embedded Latitude/Longitude arrays rather than
       a CoreMetadata bounding box.
    """
    import json
    import re
    import subprocess

    # ── shared gdalinfo -json call (used by steps 1 & 2) ────────────────────
    info: dict = {}
    try:
        r = subprocess.run(
            ["gdalinfo", "-json", str(path)],
            capture_output=True, text=True, timeout=60,
        )
        if r.returncode == 0:
            info = json.loads(r.stdout)
    except FileNotFoundError:
        logger.warning("footprint skip — gdalinfo not found in PATH: %s", path)
        return None
    except subprocess.TimeoutExpired:
        logger.warning("footprint skip — gdalinfo timeout: %s", path)
        return None
    except Exception as exc:
        logger.debug("gdalinfo -json failed (%s): %s", type(exc).__name__, path)

    # ── 1. wgs84Extent (georeferenced products) ──────────────────────────────
    extent = info.get("wgs84Extent")
    if extent:
        ring = extent.get("coordinates", [[]])[0]
        if ring:
            lons = [c[0] for c in ring]
            lats = [c[1] for c in ring]
            west, east = min(lons), max(lons)
            south, north = min(lats), max(lats)
            if -180 <= west <= east <= 180 and -90 <= south <= north <= 90:
                logger.debug("footprint via gdalinfo wgs84Extent: %s", path)
                return _bbox_geometry(west, south, east, north)

    # ── 2a. MODIS CoreMetadata in gdalinfo JSON metadata dict ────────────────
    root_meta: dict = info.get("metadata", {}).get("", {})
    # The CoreMetadata.0 ODL blob is stored as a single string value
    odl_blob = ""
    for key, val in root_meta.items():
        if "coremetadata" in key.lower():
            odl_blob += str(val)
    # Also accept direct bounding-box keys (some products expose them flat)
    coords: dict[str, float] = {}
    for key, attr in _MODIS_BBOX_ATTRS.items():
        if attr in root_meta:
            try:
                coords[key] = float(root_meta[attr])
            except (ValueError, TypeError):
                pass
    # Search inside the ODL blob if flat keys weren't found
    if len(coords) < 4 and odl_blob:
        for key, attr in _MODIS_BBOX_ATTRS.items():
            if key not in coords:
                m = re.search(rf"{re.escape(attr)}\s*=\s*(-?[\d.]+)", odl_blob)
                if m:
                    coords[key] = float(m.group(1))
    if len(coords) == 4:
        west, east = coords["west"], coords["east"]
        south, north = coords["south"], coords["north"]
        if -180 <= west <= east <= 180 and -90 <= south <= north <= 90:
            logger.debug("footprint via CoreMetadata JSON bbox: %s", path)
            return _bbox_geometry(west, south, east, north)

    # ── 2b. CoreMetadata in gdalinfo plain-text output ───────────────────────
    # Fallback for files where the JSON metadata dict is incomplete but the
    # text output still contains the full ODL blob.
    try:
        r2 = subprocess.run(
            ["gdalinfo", str(path)],
            capture_output=True, text=True, timeout=60,
        )
        if r2.returncode == 0:
            text = r2.stdout
            coords2: dict[str, float] = {}
            for key, attr in _MODIS_BBOX_ATTRS.items():
                m = re.search(rf"{re.escape(attr)}\s*=\s*(-?[\d.]+)", text)
                if m:
                    coords2[key] = float(m.group(1))
            if len(coords2) == 4:
                west, east = coords2["west"], coords2["east"]
                south, north = coords2["south"], coords2["north"]
                if -180 <= west <= east <= 180 and -90 <= south <= north <= 90:
                    logger.debug("footprint via CoreMetadata text bbox: %s", path)
                    return _bbox_geometry(west, south, east, north)
    except subprocess.TimeoutExpired:
        logger.warning("footprint skip — gdalinfo text timeout: %s", path)
    except Exception as exc:
        logger.debug("gdalinfo text failed (%s): %s", type(exc).__name__, path)

    # ── 3. Lat/Lon SDS subdatasets (SeaDAS-processed products) ───────────────
    # SeaDAS HDF4 Level-2 files embed per-pixel geolocation as SDS arrays
    # (typically named "Latitude" / "Longitude") rather than CoreMetadata.
    # In gdalinfo -json output, subdatasets live under
    # info["metadata"]["SUBDATASETS"] as a flat dict of
    # SUBDATASET_N_NAME / SUBDATASET_N_DESC pairs — NOT at info["subdatasets"].
    sds_meta: dict = info.get("metadata", {}).get("SUBDATASETS", {})
    sds_pairs: list[tuple[str, str]] = []
    i = 1
    while f"SUBDATASET_{i}_NAME" in sds_meta:
        name = sds_meta[f"SUBDATASET_{i}_NAME"]
        desc = sds_meta.get(f"SUBDATASET_{i}_DESC", "").lower()
        sds_pairs.append((name, desc))
        i += 1

    lat_sds: str | None = None
    lon_sds: str | None = None
    for name, desc in sds_pairs:
        # desc format: "[NxM] sds_name (type)" — use search, not match
        if lat_sds is None and re.search(r"\blat(itude)?\b", desc):
            lat_sds = name
        if lon_sds is None and re.search(r"\blon(gitude)?\b", desc):
            lon_sds = name

    # ── 3b. Companion seadas.hdf fallback ────────────────────────────────────
    # Files like mod14/modlst/ndvi/crefl carry no lat/lon SDS themselves but
    # always have a companion *.seadas.hdf (same orbit key, same directory)
    # that does.  SeaDAS files follow the naming pattern:
    #   a1.<doy>.<hhmm>.<product>[.<resolution>].hdf
    # The orbit key is the first 3 dot-separated stem components, e.g.
    # "a1.26126.0842" from "a1.26126.0842.ndvi.500m".
    if (lat_sds is None or lon_sds is None) and path.suffix.lower() in _HDF4_EXTENSIONS:
        stem_parts = path.stem.split(".")
        orbit_key = ".".join(stem_parts[:3]) if len(stem_parts) >= 3 else path.stem
        companion = path.parent / f"{orbit_key}.seadas.hdf"
        if companion.exists() and companion != path:
            try:
                r_comp = subprocess.run(
                    ["gdalinfo", "-json", str(companion)],
                    capture_output=True, text=True, timeout=60,
                )
                if r_comp.returncode == 0:
                    comp_info = json.loads(r_comp.stdout)
                    comp_meta = comp_info.get("metadata", {}).get("SUBDATASETS", {})
                    comp_pairs: list[tuple[str, str]] = []
                    j = 1
                    while f"SUBDATASET_{j}_NAME" in comp_meta:
                        comp_pairs.append((
                            comp_meta[f"SUBDATASET_{j}_NAME"],
                            comp_meta.get(f"SUBDATASET_{j}_DESC", "").lower(),
                        ))
                        j += 1
                    for name, desc in comp_pairs:
                        if lat_sds is None and re.search(r"\blat(itude)?\b", desc):
                            lat_sds = name
                        if lon_sds is None and re.search(r"\blon(gitude)?\b", desc):
                            lon_sds = name
                    if lat_sds and lon_sds:
                        logger.debug("footprint: using companion seadas SDS: %s", companion)
            except Exception as exc:
                logger.debug(
                    "companion seadas lookup failed (%s): %s", type(exc).__name__, companion
                )

    if lat_sds and lon_sds:
        try:
            import os
            import tempfile

            def _normalize_sds_name(sds_name: str) -> str:
                """Strip quotes gdalinfo inserts around paths containing dots.
                HDF4_SDS:UNKNOWN:"/path/file.hdf":N → HDF4_SDS:UNKNOWN:/path/file.hdf:N
                """
                return re.sub(r':"([^"]+)"(:\d+)$', r':\1\2', sds_name)

            def _sds_valid_range(
                sds_name: str, nodata: float, lo_clip: float, hi_clip: float
            ) -> tuple[float, float] | None:
                """Compute min/max of an HDF4 SDS, excluding fill-value pixels.

                Strategy: gdal_translate writes a VRT to /tmp that marks
                ``nodata`` as the no-data value; gdalinfo -mm then computes
                min/max skipping those pixels.  Both calls share the same
                on-disk temp file so /vsimem cross-process isolation is avoided.
                """
                clean = _normalize_sds_name(sds_name)
                vrt_fd, vrt_path = tempfile.mkstemp(suffix=".vrt", dir="/tmp")
                os.close(vrt_fd)
                try:
                    r1 = subprocess.run(
                        ["gdal_translate", "-of", "VRT",
                         "-a_nodata", str(int(nodata)), clean, vrt_path],
                        capture_output=True, text=True, timeout=60,
                    )
                    if r1.returncode != 0:
                        return None
                    r2 = subprocess.run(
                        ["gdalinfo", "-json", "-mm", vrt_path],
                        capture_output=True, text=True, timeout=120,
                    )
                    if r2.returncode != 0:
                        return None
                    sds_info = json.loads(r2.stdout)
                    for band in sds_info.get("bands", []):
                        lo = band.get("computedMin")
                        hi = band.get("computedMax")
                        if lo is not None and hi is not None:
                            lo, hi = float(lo), float(hi)
                            if lo_clip <= lo <= hi_clip and lo_clip <= hi <= hi_clip:
                                return lo, hi
                    return None
                finally:
                    try:
                        os.unlink(vrt_path)
                    except OSError:
                        pass

            lat_range = _sds_valid_range(lat_sds, -999.0, -90.0, 90.0)
            lon_range = _sds_valid_range(lon_sds, -999.0, -180.0, 180.0)
            if lat_range and lon_range:
                south, north = lat_range
                west, east = lon_range
                if -180 <= west <= east <= 180 and -90 <= south <= north <= 90:
                    logger.debug("footprint via lat/lon SDS stats: %s", path)
                    return _bbox_geometry(west, south, east, north)
                logger.warning(
                    "footprint skip — SDS range invalid lat=%s lon=%s: %s",
                    lat_range, lon_range, path,
                )
            else:
                logger.warning(
                    "footprint skip — SDS has no valid lat/lon pixels: %s", path
                )
        except Exception as exc:
            logger.warning(
                "footprint skip — SDS read error (%s): %s", type(exc).__name__, path
            )
    else:
        logger.debug(
            "footprint skip — no lat/lon SDS found (subdatasets=%d): %s",
            len(sds_pairs), path,
        )

    return None


def extract_footprint(path: Path) -> dict[str, object] | None:
    """Extract a WGS-84 bounding box + polygon geometry from a geospatial file.

    Dispatches to a format-specific extractor:
    - HDF4 (.hdf / .hdf4 / .h4): MODIS Level-2 swath files via pyhdf
    - All other raster formats: rasterio + GDAL
    """
    if path.suffix.lower() in _HDF4_EXTENSIONS:
        return _extract_footprint_hdf4(path)
    return _extract_footprint_rasterio(path)


def _extract_footprint_rasterio(path: Path) -> dict[str, object] | None:
    try:
        import rasterio
        from rasterio.warp import transform_bounds
    except ImportError:
        return None
    try:
        with rasterio.open(path) as dataset:
            if not dataset.crs:
                logger.warning("footprint skip — no CRS: %s", path)
                return None
            west, south, east, north = transform_bounds(
                dataset.crs,
                "EPSG:4326",
                *dataset.bounds,
                densify_pts=21,
            )
    except Exception as exc:
        logger.warning("footprint skip — rasterio error (%s): %s", type(exc).__name__, path)
        return None
    return _bbox_geometry(west, south, east, north)


def file_fingerprint(path: Path, size: int, mtime_ns: int) -> str:
    digest = hashlib.sha256()
    digest.update(str(path).encode())
    digest.update(str(size).encode())
    digest.update(str(mtime_ns).encode())
    return digest.hexdigest()


def normalize_path_text(path: Path) -> str:
    return str(path).lower().replace("_", "-")


def has_any(text: str, needles: list[str]) -> bool:
    return any(needle in text for needle in needles)


def infer_gaofen_sensor(text: str) -> str:
    if "pms" in text:
        return "pms"
    if "wfv" in text:
        return "wfv"
    if "wfc" in text:
        return "wfc"
    return "optical"
