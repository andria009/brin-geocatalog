import type {
  DatasetResponse,
  LocationOptions,
  PlatformStatus,
  ScanRun,
  SourceFile
} from "./types";

const API_BASE = import.meta.env.VITE_GEOCATALOG_API_BASE ?? "/api/v1";
export const apiBase = API_BASE;

export type DatasetFilters = {
  q: string;
  datasetType: string;
  platform: string;
  sensor: string;
  dateFrom: string;
  dateTo: string;
  province: string;
  kabupaten: string;
  kecamatan: string;
  bbox?: number[];
};

export async function getDatasets(filters: DatasetFilters, limit = 1000): Promise<DatasetResponse> {
  const params = new URLSearchParams({ limit: String(limit) });
  append(params, "q", filters.q);
  append(params, "dataset_type", filters.datasetType);
  append(params, "platform", filters.platform);
  append(params, "sensor", filters.sensor);
  append(params, "date_from", filters.dateFrom);
  append(params, "date_to", filters.dateTo);
  append(params, "province", filters.province);
  append(params, "kabupaten", filters.kabupaten);
  append(params, "kecamatan", filters.kecamatan);
  filters.bbox?.forEach((value) => params.append("bbox", String(value)));
  return getJson(`/datasets?${params.toString()}`, {
    items: [],
    total: 0,
    footprint_total: 0,
    limit,
    offset: 0
  });
}

export async function getPlatforms(): Promise<PlatformStatus[]> {
  const response = await getJson<{ items: PlatformStatus[] }>("/platforms", { items: [] });
  return response.items;
}

export async function getRuns(): Promise<ScanRun[]> {
  const response = await getJson<{ items: ScanRun[] }>("/scan-runs?limit=10", { items: [] });
  return response.items;
}

export async function getSourceFiles(): Promise<SourceFile[]> {
  const response = await getJson<{ items: SourceFile[] }>("/source-files?limit=30", { items: [] });
  return response.items;
}

export async function getLocations(province = "", kabupaten = ""): Promise<LocationOptions> {
  const params = new URLSearchParams();
  append(params, "province", province);
  append(params, "kabupaten", kabupaten);
  const suffix = params.toString() ? `?${params.toString()}` : "";
  return getJson(`/locations${suffix}`, { provinces: [], kabupaten: [], kecamatan: [] });
}

export async function getBoundary(level: string, name: string): Promise<GeoJSON.Feature | null> {
  if (!name) {
    return null;
  }
  const params = new URLSearchParams({ level, name });
  return getJson(`/boundary?${params.toString()}`, null);
}

async function getJson<T>(path: string, fallback: T): Promise<T> {
  try {
    const response = await fetch(`${API_BASE}${path}`);
    if (!response.ok) {
      throw new Error(`${response.status} ${response.statusText}`);
    }
    return (await response.json()) as T;
  } catch {
    return fallback;
  }
}

function append(params: URLSearchParams, key: string, value: string) {
  const trimmed = value.trim();
  if (trimmed) {
    params.append(key, trimmed);
  }
}
