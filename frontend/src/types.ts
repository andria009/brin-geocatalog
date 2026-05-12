export type Dataset = {
  id: string;
  stac_item_id: string;
  collection_id: string;
  title: string;
  dataset_type: string;
  source_path: string;
  file_name: string;
  file_extension: string;
  platform: string | null;
  sensor: string | null;
  product: string | null;
  acquisition_start: string | null;
  acquisition_end: string | null;
  file_size_bytes: number;
  modified_at: string;
  bbox: number[] | null;
  properties: Record<string, unknown>;
  download_url: string;
};

export type DatasetResponse = {
  items: Dataset[];
  total: number;
  footprint_total: number;
  limit: number;
  offset?: number;
};

export type PlatformStatus = {
  platform: string;
  total: number;
  raster: number;
  vector: number;
  latest_indexed_at: string | null;
};

export type ScanRun = {
  id: string;
  root_path: string;
  started_at: string;
  finished_at: string | null;
  status: string;
  scanned_files: number;
  indexed_files: number;
  updated_files: number;
  unchanged_files: number;
  removed_files: number;
  skipped_files: number;
  message: string | null;
};

export type ServiceStatus = {
  service: string;
  label: string;
  status: string;
  detail: string;
  updated_at: string | null;
  progress?: Record<string, number>;
};

export type SourceFile = {
  id: string;
  platform: string | null;
  sensor: string | null;
  collection_id: string;
  source_path: string;
  folder: string;
  file_name: string;
  file_size_bytes: number;
  updated_at: string | null;
};

export type LocationOption = {
  code: string;
  name: string;
  province?: string;
  kabupaten?: string;
};

export type LocationOptions = {
  provinces: LocationOption[];
  kabupaten: LocationOption[];
  kecamatan: LocationOption[];
};
