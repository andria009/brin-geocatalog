CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS datasets (
  id UUID PRIMARY KEY,
  collection_id TEXT NOT NULL,
  title TEXT NOT NULL,
  description TEXT,
  dataset_type TEXT NOT NULL,
  source_path TEXT NOT NULL UNIQUE,
  file_name TEXT NOT NULL,
  file_extension TEXT NOT NULL,
  platform TEXT,
  sensor TEXT,
  product TEXT,
  acquisition_start TIMESTAMPTZ,
  acquisition_end TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  file_size_bytes BIGINT NOT NULL,
  modified_at TIMESTAMPTZ NOT NULL,
  checksum TEXT,
  bbox DOUBLE PRECISION[],
  footprint geometry(MultiPolygon, 4326),
  properties JSONB NOT NULL DEFAULT '{}'::jsonb,
  stac_item JSONB NOT NULL DEFAULT '{}'::jsonb,
  search_text TSVECTOR GENERATED ALWAYS AS (
    to_tsvector(
      'simple',
      coalesce(title, '') || ' ' ||
      coalesce(description, '') || ' ' ||
      coalesce(collection_id, '') || ' ' ||
      coalesce(dataset_type, '') || ' ' ||
      coalesce(platform, '') || ' ' ||
      coalesce(sensor, '') || ' ' ||
      coalesce(product, '') || ' ' ||
      coalesce(file_name, '')
    )
  ) STORED
);

CREATE INDEX IF NOT EXISTS datasets_search_text_idx ON datasets USING gin(search_text);
CREATE INDEX IF NOT EXISTS datasets_properties_idx ON datasets USING gin(properties);
CREATE INDEX IF NOT EXISTS datasets_footprint_idx ON datasets USING gist(footprint);
CREATE INDEX IF NOT EXISTS datasets_collection_idx ON datasets(collection_id);
CREATE INDEX IF NOT EXISTS datasets_acquisition_start_idx ON datasets(acquisition_start);

CREATE TABLE IF NOT EXISTS scan_runs (
  id UUID PRIMARY KEY,
  root_path TEXT NOT NULL,
  started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  finished_at TIMESTAMPTZ,
  status TEXT NOT NULL,
  scanned_files INTEGER NOT NULL DEFAULT 0,
  indexed_files INTEGER NOT NULL DEFAULT 0,
  updated_files INTEGER NOT NULL DEFAULT 0,
  unchanged_files INTEGER NOT NULL DEFAULT 0,
  removed_files INTEGER NOT NULL DEFAULT 0,
  skipped_files INTEGER NOT NULL DEFAULT 0,
  message TEXT
);

ALTER TABLE scan_runs ADD COLUMN IF NOT EXISTS updated_files INTEGER NOT NULL DEFAULT 0;
ALTER TABLE scan_runs ADD COLUMN IF NOT EXISTS unchanged_files INTEGER NOT NULL DEFAULT 0;
ALTER TABLE scan_runs ADD COLUMN IF NOT EXISTS removed_files INTEGER NOT NULL DEFAULT 0;

CREATE TABLE IF NOT EXISTS scan_checkpoints (
  root_path TEXT PRIMARY KEY,
  run_id UUID,
  status TEXT NOT NULL,
  last_seen_path TEXT,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS admin_boundaries (
  id UUID PRIMARY KEY,
  level TEXT NOT NULL,
  province TEXT,
  kabupaten TEXT,
  kecamatan TEXT,
  name TEXT NOT NULL,
  code TEXT,
  geom geometry(MultiPolygon, 4326) NOT NULL,
  properties JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS admin_boundaries_level_idx ON admin_boundaries(level);
CREATE UNIQUE INDEX IF NOT EXISTS admin_boundaries_level_code_idx ON admin_boundaries(level, code);
CREATE INDEX IF NOT EXISTS admin_boundaries_names_idx ON admin_boundaries(province, kabupaten, kecamatan);
CREATE INDEX IF NOT EXISTS admin_boundaries_geom_idx ON admin_boundaries USING gist(geom);
