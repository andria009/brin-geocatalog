from __future__ import annotations

import hashlib
import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid5, NAMESPACE_URL

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


def extract_footprint(path: Path) -> dict[str, object] | None:
    try:
        import rasterio
        from rasterio.warp import transform_bounds
    except ImportError:
        return None
    try:
        with rasterio.open(path) as dataset:
            if not dataset.crs:
                return None
            west, south, east, north = transform_bounds(
                dataset.crs,
                "EPSG:4326",
                *dataset.bounds,
                densify_pts=21,
            )
    except Exception:
        return None
    bbox = [west, south, east, north]
    geometry = {
        "type": "Polygon",
        "coordinates": [
            [
                [west, south],
                [east, south],
                [east, north],
                [west, north],
                [west, south],
            ]
        ],
    }
    return {"bbox": bbox, "geometry": geometry}


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
