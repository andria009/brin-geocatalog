from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import NAMESPACE_URL, uuid4, uuid5

import typer
import uvicorn

from geocatalog.api import create_app
from geocatalog.db import connection
from geocatalog.repository import (
    list_datasets_without_footprint,
    remove_missing_files_in_folder,
    update_dataset_footprint,
    upsert_admin_boundary,
    upsert_dataset,
)
from geocatalog.scanner import extract_footprint, inspect_file, iter_supported_files

app = typer.Typer(help="GeoCatalog command line tools.")


@app.command()
def serve(host: str = "127.0.0.1", port: int = 8010):
    uvicorn.run(create_app(), host=host, port=port)


@app.command()
def scan(
    root: Path = typer.Option(..., "--root", "-r", help="Mounted directory to scan."),
    limit: int | None = None,
    progress_interval: int = typer.Option(
        1000,
        "--progress-interval",
        help="Report and checkpoint progress after this many indexed candidate files.",
    ),
    checkpoint_interval: int = typer.Option(
        100,
        "--checkpoint-interval",
        help="Persist resume checkpoint after this many processed files.",
    ),
    resume: bool = typer.Option(
        True,
        "--resume/--no-resume",
        help="Resume an interrupted scan from the saved checkpoint.",
    ),
    debug: bool = typer.Option(
        False,
        "--debug",
        help="Enable verbose folder enter/leave logs.",
    ),
):
    asyncio.run(run_scan(root, limit, progress_interval, checkpoint_interval, resume, debug))


@app.command()
def scan_loop(
    root: Path = typer.Option(..., "--root", "-r", help="Mounted directory to scan."),
    interval_seconds: int = 600,
    limit: int | None = None,
    progress_interval: int = typer.Option(
        1000,
        "--progress-interval",
        help="Report and checkpoint progress after this many indexed candidate files.",
    ),
    checkpoint_interval: int = typer.Option(
        100,
        "--checkpoint-interval",
        help="Persist resume checkpoint after this many processed files.",
    ),
    resume: bool = typer.Option(
        True,
        "--resume/--no-resume",
        help="Resume an interrupted scan from the saved checkpoint.",
    ),
    debug: bool = typer.Option(
        False,
        "--debug",
        help="Enable verbose folder enter/leave logs.",
    ),
):
    async def loop():
        while True:
            await run_scan(root, limit, progress_interval, checkpoint_interval, resume, debug)
            await asyncio.sleep(interval_seconds)

    asyncio.run(loop())


@app.command()
def import_reference(
    level: str = typer.Option(..., "--level", help="province, kabupaten, or kecamatan."),
    file: Path = typer.Option(..., "--file", exists=True, readable=True, help="GeoJSON file."),
):
    asyncio.run(run_import_reference(level, file))


@app.command()
def backfill_footprints(
    limit: int = typer.Option(1000, "--limit", help="Maximum datasets to inspect this run."),
    platform: str | None = typer.Option(None, "--platform", help="Restrict to one platform."),
    progress_interval: int = typer.Option(100, "--progress-interval"),
):
    asyncio.run(run_backfill_footprints(limit, platform, progress_interval))


@app.command()
def backfill_footprints_loop(
    batch_size: int = typer.Option(1000, "--batch-size", help="Rows inspected per cycle."),
    interval_seconds: int = typer.Option(600, "--interval-seconds", help="Sleep between cycles."),
    platform: str | None = typer.Option(None, "--platform", help="Restrict to one platform."),
    progress_interval: int = typer.Option(100, "--progress-interval"),
):
    async def loop() -> None:
        while True:
            await run_backfill_footprints(batch_size, platform, progress_interval)
            await asyncio.sleep(interval_seconds)

    asyncio.run(loop())


async def run_scan(
    root: Path,
    limit: int | None = None,
    progress_interval: int = 1000,
    checkpoint_interval: int = 100,
    resume: bool = True,
    debug: bool = False,
):
    run_id = str(uuid4())
    scanned = 0
    indexed = 0
    updated = 0
    unchanged = 0
    removed = 0
    skipped = 0
    log(f"scan started root={root} run_id={run_id}")
    async with connection() as conn:
        await ensure_runtime_schema(conn)
        resume_after = await get_resume_path(conn, str(root)) if resume else None
        if resume_after:
            log(f"scan resume root={root} after={resume_after}")
        await conn.execute(
            "INSERT INTO scan_runs (id, root_path, status) VALUES ($1, $2, 'running')",
            run_id,
            str(root),
        )
        await update_scan_checkpoint(conn, str(root), run_id, "running", resume_after)
        try:
            pending_reconciliations: list[tuple[Path, list[Path]]] = []

            def folder_callback(event: str, directory: Path, files: list[Path]) -> None:
                if debug:
                    log(f"scan folder {event} path={directory}")
                if event == "enter":
                    pending_reconciliations.append((directory, files))

            async def drain_reconciliations() -> None:
                nonlocal removed
                while pending_reconciliations:
                    directory, files = pending_reconciliations.pop(0)
                    removed += await remove_missing_files_in_folder(
                        conn, str(directory), [str(path) for path in files]
                    )

            last_seen_path = resume_after
            for path in iter_supported_files(root, limit, folder_callback, resume_after):
                await drain_reconciliations()
                scanned += 1
                last_seen_path = str(path)
                try:
                    candidate = inspect_file(path)
                    action = await upsert_dataset(conn, candidate)
                    if action == "indexed":
                        indexed += 1
                    elif action == "updated":
                        updated += 1
                    else:
                        unchanged += 1
                except Exception:
                    skipped += 1
                if checkpoint_interval > 0 and scanned % checkpoint_interval == 0:
                    await update_scan_checkpoint(
                        conn, str(root), run_id, "running", last_seen_path
                    )
                if progress_interval > 0 and scanned % progress_interval == 0:
                    await update_scan_run(
                        conn,
                        run_id,
                        "running",
                        scanned,
                        indexed,
                        updated,
                        unchanged,
                        removed,
                        skipped,
                    )
                    log(
                        f"scan progress root={root} scanned={scanned} "
                        f"indexed={indexed} updated={updated} "
                        f"unchanged={unchanged} removed={removed} skipped={skipped}"
                    )
            await drain_reconciliations()
            await update_scan_run(
                conn, run_id, "completed", scanned, indexed, updated, unchanged, removed, skipped
            )
            await update_scan_checkpoint(conn, str(root), run_id, "completed", None)
        except Exception as exc:
            await update_scan_run(
                conn, run_id, "failed", scanned, indexed, updated, unchanged, removed, skipped, str(exc)
            )
            await update_scan_checkpoint(conn, str(root), run_id, "failed", last_seen_path)
            raise
    log(
        f"scan completed root={root} scanned={scanned} indexed={indexed} "
        f"updated={updated} unchanged={unchanged} removed={removed} skipped={skipped}"
    )


async def run_import_reference(level: str, file: Path) -> None:
    if level not in {"province", "kabupaten", "kecamatan"}:
        raise typer.BadParameter("level must be province, kabupaten, or kecamatan")
    data = json.loads(file.read_text())
    features = data.get("features", [])
    imported = 0
    async with connection() as conn:
        await ensure_runtime_schema(conn)
        lookup = await load_reference_lookup(conn)
        for feature in features:
            properties = feature.get("properties") or {}
            geometry = feature.get("geometry")
            if not geometry:
                continue
            name = str(properties.get("wa") or properties.get("name") or "").strip()
            code = str(properties.get("gid") or properties.get("code") or "").strip()
            if not name or not code:
                continue
            province = None
            kabupaten = None
            kecamatan = None
            if level == "province":
                province = name
            elif level == "kabupaten":
                province = lookup["province"].get(str(properties.get("prov_id")))
                kabupaten = name
            else:
                province = lookup["province"].get(str(properties.get("prov_id")))
                kabupaten = lookup["kabupaten"].get(str(properties.get("kab_id")))
                kecamatan = name
            await upsert_admin_boundary(
                conn,
                boundary_id=str(uuid5(NAMESPACE_URL, f"geocatalog:{level}:{code}")),
                level=level,
                name=name,
                code=code,
                province=province,
                kabupaten=kabupaten,
                kecamatan=kecamatan,
                geometry=geometry,
                properties=properties,
            )
            imported += 1
            if imported % 1000 == 0:
                log(f"reference import progress level={level} imported={imported}")
    log(f"reference import completed level={level} file={file} imported={imported}")


async def run_backfill_footprints(
    limit: int = 1000,
    platform: str | None = None,
    progress_interval: int = 100,
) -> None:
    inspected = 0
    updated = 0
    missing = 0
    unsupported = 0
    log(f"footprint backfill started limit={limit} platform={platform or '*'}")
    async with connection() as conn:
        rows = await list_datasets_without_footprint(conn, limit=limit, platform=platform)
        for row in rows:
            inspected += 1
            path = Path(row["source_path"])
            if not path.exists():
                missing += 1
                continue
            footprint = extract_footprint(path)
            if not footprint:
                unsupported += 1
                continue
            await update_dataset_footprint(
                conn,
                dataset_id=row["id"],
                bbox=footprint["bbox"],
                footprint_geojson=footprint["geometry"],
            )
            updated += 1
            if progress_interval > 0 and inspected % progress_interval == 0:
                log(
                    f"footprint backfill progress inspected={inspected} "
                    f"updated={updated} missing={missing} unsupported={unsupported}"
                )
    log(
        f"footprint backfill completed inspected={inspected} updated={updated} "
        f"missing={missing} unsupported={unsupported}"
    )


def log(message: str) -> None:
    timestamp = datetime.now(UTC).isoformat(timespec="seconds")
    typer.echo(f"{timestamp} {message}", err=True)


async def load_reference_lookup(conn) -> dict[str, dict[str, str]]:
    province_rows = await conn.fetch(
        "SELECT code, name FROM admin_boundaries WHERE level = 'province'"
    )
    kabupaten_rows = await conn.fetch(
        "SELECT code, name FROM admin_boundaries WHERE level = 'kabupaten'"
    )
    return {
        "province": {row["code"]: row["name"] for row in province_rows},
        "kabupaten": {row["code"]: row["name"] for row in kabupaten_rows},
    }


async def update_scan_run(
    conn,
    run_id: str,
    status: str,
    scanned: int,
    indexed: int,
    updated: int,
    unchanged: int,
    removed: int,
    skipped: int,
    message: str | None = None,
) -> None:
    await conn.execute(
        """
        UPDATE scan_runs
        SET finished_at = now(), status = $2,
            scanned_files = $3, indexed_files = $4, updated_files = $5,
            unchanged_files = $6, removed_files = $7, skipped_files = $8, message = $9
        WHERE id = $1
        """,
        run_id,
        status,
        scanned,
        indexed,
        updated,
        unchanged,
        removed,
        skipped,
        message,
    )


async def get_resume_path(conn, root_path: str) -> str | None:
    row = await conn.fetchrow(
        """
        SELECT last_seen_path
        FROM scan_checkpoints
        WHERE root_path = $1
          AND status IN ('running', 'failed')
          AND last_seen_path IS NOT NULL
        """,
        root_path,
    )
    return row["last_seen_path"] if row else None


async def update_scan_checkpoint(
    conn,
    root_path: str,
    run_id: str,
    status: str,
    last_seen_path: str | None,
) -> None:
    await conn.execute(
        """
        INSERT INTO scan_checkpoints (root_path, run_id, status, last_seen_path, updated_at)
        VALUES ($1, $2::uuid, $3, $4, now())
        ON CONFLICT (root_path) DO UPDATE SET
          run_id = EXCLUDED.run_id,
          status = EXCLUDED.status,
          last_seen_path = EXCLUDED.last_seen_path,
          updated_at = now()
        """,
        root_path,
        run_id,
        status,
        last_seen_path,
    )


async def ensure_runtime_schema(conn) -> None:
    await conn.execute(
        "ALTER TABLE scan_runs ADD COLUMN IF NOT EXISTS updated_files INTEGER NOT NULL DEFAULT 0"
    )
    await conn.execute(
        "ALTER TABLE scan_runs ADD COLUMN IF NOT EXISTS unchanged_files INTEGER NOT NULL DEFAULT 0"
    )
    await conn.execute(
        "ALTER TABLE scan_runs ADD COLUMN IF NOT EXISTS removed_files INTEGER NOT NULL DEFAULT 0"
    )
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS scan_checkpoints (
          root_path TEXT PRIMARY KEY,
          run_id UUID,
          status TEXT NOT NULL,
          last_seen_path TEXT,
          updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    await conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS admin_boundaries_level_code_idx
        ON admin_boundaries(level, code)
        """
    )
