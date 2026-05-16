import type {
  AccessActivity,
  AccessUser,
  DatasetResponse,
  LocationOptions,
  LoginResponse,
  PlatformStatus,
  ScanRun,
  ServiceStatus,
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
  cloudMin: string;
  cloudMax: string;
  province: string;
  kabupaten: string;
  kecamatan: string;
  bbox?: number[];
};

export async function getDatasets(
  filters: DatasetFilters,
  limit = 100,
  offset = 0,
  accessUser = ""
): Promise<DatasetResponse> {
  const params = new URLSearchParams({ limit: String(limit), offset: String(offset) });
  append(params, "q", filters.q);
  append(params, "dataset_type", filters.datasetType);
  append(params, "platform", filters.platform);
  append(params, "sensor", filters.sensor);
  append(params, "date_from", startOfDay(filters.dateFrom));
  append(params, "date_to", endOfDay(filters.dateTo));
  append(params, "cloud_min", filters.cloudMin);
  append(params, "cloud_max", filters.cloudMax);
  append(params, "province", filters.province);
  append(params, "kabupaten", filters.kabupaten);
  append(params, "kecamatan", filters.kecamatan);
  filters.bbox?.forEach((value) => params.append("bbox", String(value)));
  return getJson(`/datasets?${params.toString()}`, {
    items: [],
    total: 0,
    footprint_total: 0,
    limit,
    offset
  }, accessUser);
}

export async function login(username: string, password: string): Promise<LoginResponse> {
  const response = await fetch(`${API_BASE}/access/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password })
  });
  if (!response.ok) {
    throw new Error("Invalid username or password");
  }
  return (await response.json()) as LoginResponse;
}

export async function getCurrentUser(accessUser: string): Promise<AccessUser> {
  return getJson(`/access/me`, {} as AccessUser, accessUser);
}

export async function getMyActivity(accessUser: string, limit = 30): Promise<AccessActivity[]> {
  const response = await getJson<{ items: AccessActivity[] }>(
    `/access/activity?mine=true&limit=${limit}`,
    { items: [] },
    accessUser
  );
  return response.items;
}

export async function downloadDataset(dataset: { id: string; file_name: string }, accessUser: string) {
  const response = await fetch(`${API_BASE}/datasets/${dataset.id}/download-ticket`, {
    headers: { "X-GeoCatalog-User": accessUser }
  });
  if (!response.ok) {
    throw new Error(await response.text());
  }
  const ticket = (await response.json()) as { download_url: string };
  const link = document.createElement("a");
  link.href = ticket.download_url;
  link.download = dataset.file_name;
  document.body.appendChild(link);
  link.click();
  link.remove();
}

export async function downloadOdcDataset(
  dataset: { id: string; title: string },
  accessUser: string
) {
  const response = await fetch(`${API_BASE}/datasets/${dataset.id}/odc`, {
    headers: { "X-GeoCatalog-User": accessUser }
  });
  if (!response.ok) {
    throw new Error(await response.text());
  }
  const blob = new Blob([await response.text()], { type: "application/x-yaml" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `${dataset.title}.odc.yaml`;
  link.click();
  URL.revokeObjectURL(url);
}

export async function getPlatforms(): Promise<PlatformStatus[]> {
  const response = await getJson<{ items: PlatformStatus[] }>("/platforms", { items: [] });
  return response.items;
}

export async function getRuns(): Promise<ScanRun[]> {
  const response = await getJson<{ items: ScanRun[] }>("/scan-runs?limit=10", { items: [] });
  return response.items;
}

export async function getServices(): Promise<ServiceStatus[]> {
  const response = await getJson<{ items: ServiceStatus[] }>("/services", { items: [] });
  return response.items;
}

export async function getStacApiStatus(): Promise<ServiceStatus> {
  try {
    const response = await fetch("/stac/");
    if (!response.ok) {
      throw new Error(`${response.status} ${response.statusText}`);
    }
    return {
      service: "stac-api",
      label: "STAC API",
      status: "running",
      detail: "PgSTAC-backed STAC API is responding.",
      updated_at: new Date().toISOString()
    };
  } catch {
    return {
      service: "stac-api",
      label: "STAC API",
      status: "unknown",
      detail: "STAC API could not be reached from the frontend.",
      updated_at: new Date().toISOString()
    };
  }
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

async function getJson<T>(path: string, fallback: T, accessUser = ""): Promise<T> {
  try {
    const headers = accessUser ? { "X-GeoCatalog-User": accessUser } : undefined;
    const response = await fetch(`${API_BASE}${path}`, { headers });
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

function startOfDay(value: string) {
  return value.trim() ? `${value.trim()}T00:00:00` : "";
}

function endOfDay(value: string) {
  return value.trim() ? `${value.trim()}T23:59:59` : "";
}
