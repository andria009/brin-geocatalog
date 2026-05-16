import {
  ChevronDown,
  ChevronRight,
  Download,
  ExternalLink,
  Activity,
  Crosshair,
  FolderOpen,
  Layers,
  List,
  LogIn,
  LogOut,
  Map as MapIcon,
  PanelRightClose,
  PanelRightOpen,
  RefreshCw,
  Satellite,
  Search,
  User,
  X
} from "lucide-react";
import maplibregl from "maplibre-gl";
import { useEffect, useMemo, useRef, useState } from "react";
import {
  getBoundary,
  downloadDataset,
  downloadOdcDataset,
  getCurrentUser,
  getDatasets,
  getMyActivity,
  getLocations,
  getPlatforms,
  getServices,
  getSourceFiles,
  getStacApiStatus,
  login,
  type DatasetFilters
} from "./api";
import logo from "./assets/geocatalog-logo.png";
import type {
  AccessActivity,
  Dataset,
  AccessUser,
  LocationOptions,
  PlatformStatus,
  ServiceStatus,
  SourceFile
} from "./types";

type Basemap = "street" | "satellite";
type RightRailMode = "details" | "datasets" | "activity" | null;
type DetailSection = "services" | "platforms" | "sources";
const MAX_MAP_RECORDS = 1000;
const DATASET_PAGE_SIZE = 20;
const SESSION_STORAGE_KEY = "geocatalog-dev-session";

type Session = {
  user: AccessUser;
  headerValue: string;
  expiresAt: string;
};

export default function App() {
  const mapRef = useRef<maplibregl.Map | null>(null);
  const mapNodeRef = useRef<HTMLDivElement | null>(null);
  const mapDatasetsRef = useRef<Dataset[]>([]);
  const filtersRef = useRef<DatasetFilters | null>(null);
  const selectAreaModeRef = useRef(false);
  const dragStartRef = useRef<maplibregl.LngLat | null>(null);
  const [datasets, setDatasets] = useState<Dataset[]>([]);
  const [session, setSession] = useState<Session | null>(() => loadSession());
  const [loginUsername, setLoginUsername] = useState("mage@geocatalog.local");
  const [loginPassword, setLoginPassword] = useState("");
  const [loginError, setLoginError] = useState("");
  const [loginBusy, setLoginBusy] = useState(false);
  const [mapDatasets, setMapDatasets] = useState<Dataset[]>([]);
  const [totalDatasets, setTotalDatasets] = useState(0);
  const [datasetPage, setDatasetPage] = useState(0);
  const [catalogSearched, setCatalogSearched] = useState(false);
  const [appliedFilters, setAppliedFilters] = useState<DatasetFilters | null>(null);
  const [platforms, setPlatforms] = useState<PlatformStatus[]>([]);
  const [services, setServices] = useState<ServiceStatus[]>([]);
  const [sources, setSources] = useState<SourceFile[]>([]);
  const [locations, setLocations] = useState<LocationOptions>({
    provinces: [],
    kabupaten: [],
    kecamatan: []
  });
  const [selected, setSelected] = useState<Dataset | null>(null);
  const [selectedBoundary, setSelectedBoundary] = useState<GeoJSON.Feature | null>(null);
  const [basemap, setBasemap] = useState<Basemap>("street");
  const [rightRailMode, setRightRailMode] = useState<RightRailMode>(null);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [detailSections, setDetailSections] = useState<Record<DetailSection, boolean>>({
    services: true,
    platforms: true,
    sources: true
  });
  const [selectAreaMode, setSelectAreaMode] = useState(false);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [assetError, setAssetError] = useState("");
  const [activities, setActivities] = useState<AccessActivity[]>([]);
  const [activityLoading, setActivityLoading] = useState(false);
  const [activityError, setActivityError] = useState("");
  const [mapError, setMapError] = useState("");
  const [province, setProvince] = useState("");
  const [kabupaten, setKabupaten] = useState("");
  const [kecamatan, setKecamatan] = useState("");
  const [filters, setFilters] = useState<DatasetFilters>({
    q: "",
    datasetType: "",
    platform: "",
    sensor: "",
    dateFrom: "",
    dateTo: "",
    cloudMin: "",
    cloudMax: "",
    province: "",
    kabupaten: "",
    kecamatan: ""
  });

  const loadedFootprintCount = useMemo(
    () => mapDatasets.filter((item) => item.bbox && item.bbox.length === 4).length,
    [mapDatasets]
  );
  const platformNames = useMemo(() => platforms.map((item) => item.platform), [platforms]);
  const areaFilter = kecamatan || kabupaten || province;
  const activeFilterLabel = [areaFilter, filters.platform, filters.datasetType]
    .filter(Boolean)
    .join(" / ");
  const hasDatasetFilter = hasActiveDatasetFilter(filters);
  const tooManyRecords = catalogSearched && totalDatasets >= MAX_MAP_RECORDS;
  const totalDatasetPages = Math.max(1, Math.ceil(totalDatasets / DATASET_PAGE_SIZE));
  const isAuthenticated = Boolean(session);
  const cloudMinValue = parseCloudSliderValue(filters.cloudMin, 0);
  const cloudMaxValue = parseCloudSliderValue(filters.cloudMax, 100);

  useEffect(() => {
    if (!session?.expiresAt) {
      return;
    }
    const timeoutMs = Date.parse(session.expiresAt) - Date.now();
    if (timeoutMs <= 0) {
      logout();
      return;
    }
    const timer = window.setTimeout(() => logout(), timeoutMs);
    return () => window.clearTimeout(timer);
  }, [session?.expiresAt]);

  useEffect(() => {
    filtersRef.current = filters;
  }, [filters]);

  useEffect(() => {
    selectAreaModeRef.current = selectAreaMode;
    if (mapRef.current) {
      mapRef.current.getCanvas().style.cursor = selectAreaMode ? "crosshair" : "";
    }
  }, [selectAreaMode]);

  useEffect(() => {
    if (!mapNodeRef.current || mapRef.current) {
      return;
    }
    try {
      mapRef.current = new maplibregl.Map({
        container: mapNodeRef.current,
        center: [118, -2.5],
        zoom: 4.3,
        style: {
          version: 8,
          sources: {
            osm: {
              type: "raster",
              tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"],
              tileSize: 256,
              attribution: "OpenStreetMap"
            },
            esriWorldImagery: {
              type: "raster",
              tiles: [
                "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"
              ],
              tileSize: 256,
              attribution: "Esri World Imagery"
            }
          },
          layers: [
            { id: "osm", type: "raster", source: "osm" },
            {
              id: "esri-world-imagery",
              type: "raster",
              source: "esriWorldImagery",
              layout: { visibility: "none" }
            }
          ]
        }
      });
    } catch (error) {
      setMapError(error instanceof Error ? error.message : "Map failed to initialize.");
      return;
    }
    mapRef.current.addControl(new maplibregl.NavigationControl({ showCompass: false }), "bottom-right");
    mapRef.current.on("load", () => {
      mapRef.current?.addSource("selected-boundary", {
        type: "geojson",
        data: emptyCollection()
      });
      mapRef.current?.addSource("dataset-footprints", {
        type: "geojson",
        data: emptyCollection()
      });
      mapRef.current?.addSource("drawn-bbox", {
        type: "geojson",
        data: emptyCollection()
      });
      mapRef.current?.addSource("selected-dataset", {
        type: "geojson",
        data: emptyCollection()
      });
      mapRef.current?.addLayer({
        id: "drawn-bbox-fill",
        type: "fill",
        source: "drawn-bbox",
        paint: { "fill-color": "#2f80ed", "fill-opacity": 0.12 }
      });
      mapRef.current?.addLayer({
        id: "drawn-bbox-line",
        type: "line",
        source: "drawn-bbox",
        paint: { "line-color": "#2f80ed", "line-width": 2, "line-dasharray": [2, 1.5] }
      });
      mapRef.current?.addLayer({
        id: "selected-boundary-fill",
        type: "fill",
        source: "selected-boundary",
        paint: { "fill-color": "#f08a24", "fill-opacity": 0.18 }
      });
      mapRef.current?.addLayer({
        id: "selected-boundary-line",
        type: "line",
        source: "selected-boundary",
        paint: { "line-color": "#b7192b", "line-width": 2.5 }
      });
      mapRef.current?.addLayer({
        id: "dataset-fill",
        type: "fill",
        source: "dataset-footprints",
        paint: { "fill-color": "#e76f2c", "fill-opacity": 0.14 }
      });
      mapRef.current?.addLayer({
        id: "dataset-bounds",
        type: "line",
        source: "dataset-footprints",
        paint: { "line-color": "#b7192b", "line-width": 1.6, "line-opacity": 0.8 }
      });
      mapRef.current?.addLayer({
        id: "selected-dataset-fill",
        type: "fill",
        source: "selected-dataset",
        paint: { "fill-color": "#2f80ed", "fill-opacity": 0.16 }
      });
      mapRef.current?.addLayer({
        id: "selected-dataset-line",
        type: "line",
        source: "selected-dataset",
        paint: { "line-color": "#1d4ed8", "line-width": 2.5 }
      });
      mapRef.current?.on("click", "dataset-fill", (event) => {
        const id = event.features?.[0]?.properties?.id;
        const match = mapDatasetsRef.current.find((item) => item.id === id);
        if (match) {
          selectDataset(match, false);
        }
      });
      mapRef.current?.on("mouseenter", "dataset-fill", () => {
        mapRef.current!.getCanvas().style.cursor = "pointer";
      });
      mapRef.current?.on("mouseleave", "dataset-fill", () => {
        mapRef.current!.getCanvas().style.cursor = "";
      });
      mapRef.current?.on("mousedown", (event) => {
        if (!selectAreaModeRef.current) {
          return;
        }
        event.preventDefault();
        dragStartRef.current = event.lngLat;
        updateDrawnBbox(event.lngLat, event.lngLat);
      });
      mapRef.current?.on("mousemove", (event) => {
        if (!selectAreaModeRef.current || !dragStartRef.current) {
          return;
        }
        updateDrawnBbox(dragStartRef.current, event.lngLat);
      });
      mapRef.current?.on("mouseup", (event) => {
        if (!selectAreaModeRef.current || !dragStartRef.current) {
          return;
        }
        const bbox = bboxFromLngLats(dragStartRef.current, event.lngLat);
        dragStartRef.current = null;
        setSelectAreaMode(false);
        const nextFilters = { ...(filtersRef.current ?? filters), bbox };
        setFilters(nextFilters);
        void refreshAll(nextFilters);
      });
    });
    return () => {
      mapRef.current?.remove();
      mapRef.current = null;
    };
  }, []);

  useEffect(() => {
    void refreshDashboard();
  }, []);

  useEffect(() => {
    if (rightRailMode !== "details") {
      return undefined;
    }
    const timer = window.setInterval(() => {
      void refreshOperations();
    }, 5000);
    return () => window.clearInterval(timer);
  }, [rightRailMode]);

  useEffect(() => {
    void refreshLocations();
  }, [province, kabupaten]);

  useEffect(() => {
    void refreshBoundary();
  }, [province, kabupaten, kecamatan]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map) {
      return;
    }
    const applyBasemap = () => {
      try {
        if (map.getLayer("osm")) {
          map.setLayoutProperty("osm", "visibility", basemap === "street" ? "visible" : "none");
        }
        if (map.getLayer("esri-world-imagery")) {
          map.setLayoutProperty(
            "esri-world-imagery",
            "visibility",
            basemap === "satellite" ? "visible" : "none"
          );
        }
      } catch (error) {
        setMapError(error instanceof Error ? error.message : "Map layer update failed.");
      }
    };
    if (map.isStyleLoaded()) {
      applyBasemap();
    } else {
      map.once("load", applyBasemap);
    }
  }, [basemap]);

  useEffect(() => {
    mapDatasetsRef.current = mapDatasets;
    const source = mapRef.current?.getSource("dataset-footprints") as
      | maplibregl.GeoJSONSource
      | undefined;
    source?.setData(toFootprintCollection(mapDatasets));
  }, [mapDatasets]);

  useEffect(() => {
    const source = mapRef.current?.getSource("selected-boundary") as
      | maplibregl.GeoJSONSource
      | undefined;
    source?.setData(selectedBoundary ?? emptyCollection());
    if (selectedBoundary) {
      fitFeature(selectedBoundary);
    }
  }, [selectedBoundary]);

  useEffect(() => {
    const source = mapRef.current?.getSource("selected-dataset") as
      | maplibregl.GeoJSONSource
      | undefined;
    source?.setData(selected ? toSingleFootprintCollection(selected) : emptyCollection());
  }, [selected]);

  function updateDrawnBbox(start: maplibregl.LngLat, end: maplibregl.LngLat) {
    const source = mapRef.current?.getSource("drawn-bbox") as maplibregl.GeoJSONSource | undefined;
    source?.setData(bboxFeature(bboxFromLngLats(start, end)));
  }

  function toggleRail(mode: Exclude<RightRailMode, null>) {
    setRightRailMode((current) => (current === mode ? null : mode));
  }

  function toggleDetailSection(section: DetailSection) {
    setDetailSections((current) => ({
      ...current,
      [section]: !current[section]
    }));
  }

  function selectDataset(item: Dataset, shouldFit = true) {
    setSelected(item);
    if (shouldFit && item.bbox?.length === 4) {
      fitFeature(bboxFeature(item.bbox));
    }
  }

  async function refreshDashboard() {
    setLoading(true);
    setLoadError("");
    try {
      const [countResponse, operations] = await Promise.all([
        getDatasets(filters, 1, 0, session?.headerValue),
        fetchOperations()
      ]);
      setTotalDatasets(countResponse.total);
      setDatasets([]);
      setMapDatasets([]);
      setSelected(null);
      applyOperations(operations);
    } catch (error) {
      setLoadError(error instanceof Error ? error.message : "Catalog data failed to load.");
    } finally {
      setLoading(false);
    }
  }

  async function refreshAll(nextFilters = filters) {
    await refreshCatalogPage(nextFilters, 0);
  }

  async function refreshCatalogPage(nextFilters = filters, page = datasetPage) {
    setCatalogSearched(true);
    setAppliedFilters(nextFilters);
    setLoading(true);
    setLoadError("");
    setSelected(null);
    try {
      const active = hasActiveDatasetFilter(nextFilters);
      const offset = page * DATASET_PAGE_SIZE;
      const [datasetResponse, operations] = await Promise.all([
        active
          ? getDatasets(nextFilters, DATASET_PAGE_SIZE, offset, session?.headerValue)
          : getDatasets(nextFilters, 1, 0, session?.headerValue),
        fetchOperations()
      ]);
      setTotalDatasets(datasetResponse.total);
      setDatasetPage(active ? page : 0);
      setDatasets(active ? datasetResponse.items : []);
      applyOperations(operations);
      if (session) {
        await refreshCurrentUser(session.headerValue);
        if (rightRailMode === "activity") {
          await refreshActivity(session.headerValue);
        }
      }

      if (active && datasetResponse.total < MAX_MAP_RECORDS) {
        const mapResponse = await getDatasets(nextFilters, MAX_MAP_RECORDS, 0, session?.headerValue);
        setMapDatasets(mapResponse.items);
      } else {
        setMapDatasets([]);
      }
    } catch (error) {
      setLoadError(error instanceof Error ? error.message : "Catalog data failed to load.");
    } finally {
      setLoading(false);
    }
  }

  async function refreshOperations() {
    try {
      applyOperations(await fetchOperations());
    } catch {
      // Keep the current rail content if the lightweight status refresh fails.
    }
  }

  async function fetchOperations() {
    const [platformRows, serviceRows, stacApiStatus, sourceRows] = await Promise.all([
      getPlatforms(),
      getServices(),
      getStacApiStatus(),
      getSourceFiles()
    ]);
    return {
      platforms: platformRows,
      services: [
        {
          service: "frontend",
          label: "Frontend",
          status: "running",
          detail: "Web interface is loaded.",
          updated_at: new Date().toISOString()
        },
        stacApiStatus,
        ...serviceRows
      ],
      sources: sourceRows
    };
  }

  function applyOperations(operations: Awaited<ReturnType<typeof fetchOperations>>) {
    setPlatforms(operations.platforms);
    setServices(operations.services);
    setSources(operations.sources);
  }

  async function refreshLocations() {
    const response = await getLocations(province, kabupaten);
    setLocations(response);
  }

  async function refreshBoundary() {
    const level = kecamatan ? "kecamatan" : kabupaten ? "kabupaten" : province ? "province" : "";
    const name = kecamatan || kabupaten || province;
    setSelectedBoundary(level ? await getBoundary(level, name) : null);
  }

  function updateProvince(value: string) {
    setProvince(value);
    setKabupaten("");
    setKecamatan("");
    setFilters({ ...filters, province: value, kabupaten: "", kecamatan: "" });
  }

  function updateKabupaten(value: string) {
    setKabupaten(value);
    setKecamatan("");
    setFilters({ ...filters, province, kabupaten: value, kecamatan: "" });
  }

  function updateKecamatan(value: string) {
    setKecamatan(value);
    setFilters({ ...filters, province, kabupaten, kecamatan: value });
  }

  function updateCloudMin(value: string) {
    const next = Math.min(Number(value), cloudMaxValue);
    setFilters({ ...filters, cloudMin: next <= 0 ? "" : String(next) });
  }

  function updateCloudMax(value: string) {
    const next = Math.max(Number(value), cloudMinValue);
    setFilters({ ...filters, cloudMax: next >= 100 ? "" : String(next) });
  }

  async function submitLogin(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setLoginBusy(true);
    setLoginError("");
    try {
      const response = await login(loginUsername, loginPassword);
      const nextSession = {
        user: response.user,
        headerValue: response.development_header.value,
        expiresAt: response.expires_at
      };
      setSession(nextSession);
      window.localStorage.setItem(SESSION_STORAGE_KEY, JSON.stringify(nextSession));
      setLoginPassword("");
      setRightRailMode(null);
      mapRef.current?.resize();
      window.setTimeout(() => mapRef.current?.resize(), 150);
    } catch (error) {
      setLoginError(error instanceof Error ? error.message : "Login failed.");
    } finally {
      setLoginBusy(false);
    }
  }

  function logout() {
    setSession(null);
    window.localStorage.removeItem(SESSION_STORAGE_KEY);
    setDatasets([]);
    setMapDatasets([]);
    setActivities([]);
    setSelected(null);
    setRightRailMode(null);
    setCatalogSearched(false);
    window.setTimeout(() => mapRef.current?.resize(), 150);
  }

  async function refreshCurrentUser(accessUser = session?.headerValue) {
    if (!accessUser) {
      return;
    }
    try {
      const user = await getCurrentUser(accessUser);
      const nextSession = { user, headerValue: accessUser, expiresAt: session?.expiresAt ?? "" };
      setSession(nextSession);
      window.localStorage.setItem(SESSION_STORAGE_KEY, JSON.stringify(nextSession));
    } catch {
      // Keep the current session if a lightweight refresh fails.
    }
  }

  async function openActivityRail() {
    if (rightRailMode === "activity") {
      setRightRailMode(null);
      return;
    }
    setRightRailMode("activity");
    await refreshActivity();
  }

  async function refreshActivity(accessUser = session?.headerValue) {
    if (!accessUser) {
      return;
    }
    setActivityLoading(true);
    setActivityError("");
    try {
      setActivities(await getMyActivity(accessUser));
      await refreshCurrentUser(accessUser);
    } catch (error) {
      setActivityError(error instanceof Error ? error.message : "Activity data failed to load.");
    } finally {
      setActivityLoading(false);
    }
  }

  async function handleDownloadDataset(dataset: Dataset) {
    if (!session) {
      return;
    }
    setAssetError("");
    try {
      await downloadDataset(dataset, session.headerValue);
      await refreshCurrentUser(session.headerValue);
      if (rightRailMode === "activity") {
        await refreshActivity(session.headerValue);
      }
    } catch (error) {
      setAssetError(error instanceof Error ? error.message : "Download failed.");
    }
  }

  async function handleDownloadOdc(dataset: Dataset) {
    if (!session) {
      return;
    }
    setAssetError("");
    try {
      await downloadOdcDataset(dataset, session.headerValue);
      await refreshCurrentUser(session.headerValue);
      if (rightRailMode === "activity") {
        await refreshActivity(session.headerValue);
      }
    } catch (error) {
      setAssetError(error instanceof Error ? error.message : "ODC export failed.");
    }
  }

  function toggleSidebar() {
    setSidebarCollapsed((current) => !current);
    window.setTimeout(() => mapRef.current?.resize(), 150);
  }

  function fitFeature(feature: GeoJSON.Feature) {
    const bounds = new maplibregl.LngLatBounds();
    visitCoordinates(feature.geometry, (coordinate) => bounds.extend(coordinate as [number, number]));
    if (!bounds.isEmpty()) {
      mapRef.current?.fitBounds(bounds, { padding: 44, duration: 500 });
    }
  }

  function downloadVisibleGeoJson() {
    const collection = toFootprintCollection(mapDatasets);
    const blob = new Blob([JSON.stringify(collection, null, 2)], {
      type: "application/geo+json"
    });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = "geocatalog-visible-scenes.geojson";
    link.click();
    URL.revokeObjectURL(url);
  }

  return (
    <main
      className={`${isAuthenticated ? "app-shell" : "landing-shell"} ${
        rightRailMode ? "" : "rail-collapsed"
      } ${sidebarCollapsed ? "sidebar-collapsed" : ""}`}
    >
      {isAuthenticated ? (
      <aside className={`sidebar ${sidebarCollapsed ? "collapsed" : ""}`}>
        <button className="brand brand-toggle" onClick={toggleSidebar} title="Toggle sidebar">
          <img className="brand-logo" src={logo} alt="GeoCatalog" />
          {!sidebarCollapsed ? (
          <div>
            <p>geocatalog</p>
            <span>Multi-Satellite Data Aquisition and Cataloging Platform</span>
          </div>
          ) : null}
        </button>
        {!sidebarCollapsed ? (
        <>
        <div className="sidebar-session">
          <div className="session-identity">
            <User size={15} />
            <span>{session?.user.role}</span>
            {session?.user.role === "mage" ? <strong>{formatNumber(session.user.token_balance)} tokens</strong> : null}
          </div>
          <div className="session-actions">
            <button
              className={`session-icon-button ${rightRailMode === "activity" ? "active" : ""}`}
              onClick={() => void openActivityRail()}
              title={rightRailMode === "activity" ? "Hide activity" : "Show activity"}
              aria-label={rightRailMode === "activity" ? "Hide activity" : "Show activity"}
            >
              <Activity size={14} />
            </button>
            <button onClick={logout}>
              <LogOut size={14} /> Logout
            </button>
          </div>
        </div>

        <section className="panel">
          <div className="panel-title">
            <Search size={16} /> Filters
          </div>
          <div className="controls">
            <label>
              Text
              <input
                value={filters.q}
                onChange={(event) => setFilters({ ...filters, q: event.target.value })}
                placeholder="Filename, product, satellite"
              />
            </label>
            <label>
              Platform
              <input
                list="platform-options"
                value={filters.platform}
                onChange={(event) => setFilters({ ...filters, platform: event.target.value })}
                placeholder="All platforms"
              />
              <datalist id="platform-options">
                {platformNames.map((platform) => (
                  <option key={platform} value={platform === "unknown" ? "" : platform} />
                ))}
              </datalist>
            </label>
            <div className="field-grid">
              <label>
                Type
                <input
                  list="type-options"
                  value={filters.datasetType}
                  onChange={(event) => setFilters({ ...filters, datasetType: event.target.value })}
                  placeholder="Any"
                />
                <datalist id="type-options">
                  <option value="raster" />
                  <option value="vector" />
                </datalist>
              </label>
              <label>
                Sensor
                <input
                  value={filters.sensor}
                  onChange={(event) => setFilters({ ...filters, sensor: event.target.value })}
                  placeholder="viirs, modis, msi"
                />
              </label>
            </div>
            <div className="date-filter">
              <span>Date</span>
              <div className="field-grid">
                <label>
                  From
                  <input
                    type="date"
                    value={filters.dateFrom}
                    onChange={(event) => setFilters({ ...filters, dateFrom: event.target.value })}
                  />
                </label>
                <label>
                  To
                  <input
                    type="date"
                    value={filters.dateTo}
                    onChange={(event) => setFilters({ ...filters, dateTo: event.target.value })}
                  />
                </label>
              </div>
            </div>
            <div className="date-filter">
              <span>Cloud cover (%)</span>
              <div className="range-summary">
                <span>{cloudMinValue}%</span>
                <span>{cloudMaxValue}%</span>
              </div>
              <div className="dual-range" aria-label="Cloud cover range">
                <div
                  className="dual-range-active"
                  style={{ left: `${cloudMinValue}%`, right: `${100 - cloudMaxValue}%` }}
                />
                <label>
                  <span>Minimum cloud cover</span>
                  <input
                    type="range"
                    min="0"
                    max="100"
                    step="1"
                    value={cloudMinValue}
                    onChange={(event) => updateCloudMin(event.target.value)}
                  />
                </label>
                <label>
                  <span>Maximum cloud cover</span>
                  <input
                    type="range"
                    min="0"
                    max="100"
                    step="1"
                    value={cloudMaxValue}
                    onChange={(event) => updateCloudMax(event.target.value)}
                  />
                </label>
              </div>
            </div>
            <label>
              Provinsi
              <input
                list="province-options"
                value={province}
                onChange={(event) => updateProvince(event.target.value)}
                placeholder="All provinces"
              />
              <datalist id="province-options">
                {locations.provinces.map((item) => (
                  <option key={item.code} value={item.name} />
                ))}
              </datalist>
            </label>
            <label className={!province ? "disabled-field" : ""}>
              Kota/Kabupaten
              <input
                list="kabupaten-options"
                value={kabupaten}
                onChange={(event) => updateKabupaten(event.target.value)}
                placeholder={province ? "All kota/kabupaten" : "Select provinsi first"}
                disabled={!province}
              />
              {province ? (
                <datalist id="kabupaten-options">
                  {locations.kabupaten.map((item) => (
                    <option key={item.code} value={item.name} />
                  ))}
                </datalist>
              ) : null}
            </label>
            <label className={!kabupaten ? "disabled-field" : ""}>
              Kecamatan
              <input
                list="kecamatan-options"
                value={kecamatan}
                onChange={(event) => updateKecamatan(event.target.value)}
                placeholder={kabupaten ? "All kecamatan" : "Select kota/kabupaten first"}
                disabled={!kabupaten}
              />
              {kabupaten ? (
                <datalist id="kecamatan-options">
                  {locations.kecamatan.map((item) => (
                    <option key={item.code} value={item.name} />
                  ))}
                </datalist>
              ) : null}
            </label>
            <button className="primary-button" onClick={() => void refreshAll()}>
              <RefreshCw size={16} /> Refresh Catalog
            </button>
          </div>
        </section>
        </>
        ) : null}
      </aside>
      ) : null}

      <section className="map-stage">
        {!isAuthenticated ? (
          <>
            <LandingStatus platforms={platforms} totalDatasets={totalDatasets} />
            <div className="landing-logo">
              <img src={logo} alt="GeoCatalog" />
            </div>
            <form className="login-card" onSubmit={(event) => void submitLogin(event)}>
              <div className="login-title">
                <LogIn size={16} />
                <span>Login</span>
              </div>
              <label>
                Username
                <input
                  value={loginUsername}
                  onChange={(event) => setLoginUsername(event.target.value)}
                  autoComplete="username"
                />
              </label>
              <label>
                Password
                <input
                  type="password"
                  value={loginPassword}
                  onChange={(event) => setLoginPassword(event.target.value)}
                  autoComplete="current-password"
                  placeholder="Sample password"
                />
              </label>
              {loginError ? <div className="login-error">{loginError}</div> : null}
              <button className="primary-button" disabled={loginBusy}>
                <LogIn size={15} /> {loginBusy ? "Signing in" : "Enter Catalog"}
              </button>
            </form>
          </>
        ) : null}
        {isAuthenticated ? (
        <div className="map-toolbar">
          <div className="toolbar-count">
            <strong>{formatNumber(totalDatasets)}</strong>
            <span>records</span>
          </div>
          <div className="basemap-switch" aria-label="Basemap">
            <span className="segmented-icon">
              <MapIcon size={15} />
            </span>
            <button className={basemap === "street" ? "active" : ""} onClick={() => setBasemap("street")}>
              Street
            </button>
            <button
              className={basemap === "satellite" ? "active" : ""}
              onClick={() => setBasemap("satellite")}
            >
              Satellite
            </button>
          </div>
          <button
            className={`toolbar-button ${rightRailMode === "details" ? "active" : ""}`}
            onClick={() => toggleRail("details")}
          >
            {rightRailMode === "details" ? <PanelRightClose size={15} /> : <PanelRightOpen size={15} />}
            Details
          </button>
          <button
            className={`toolbar-button ${rightRailMode === "datasets" ? "active" : ""}`}
            onClick={() => toggleRail("datasets")}
          >
            <List size={15} />
            Datasets
          </button>
          <button
            className={`toolbar-button ${selectAreaMode ? "active" : ""}`}
            onClick={() => setSelectAreaMode((active) => !active)}
          >
            <Crosshair size={15} />
            Select Area
          </button>
          <button className="toolbar-button" onClick={() => void refreshAll()}>
            <RefreshCw size={15} />
            Refresh
          </button>
          <button className="toolbar-button" onClick={downloadVisibleGeoJson} disabled>
            <Download size={15} />
            GeoJSON
          </button>
        </div>
        ) : null}
        {isAuthenticated && !catalogSearched ? (
          <div className="map-notice">
            <strong>Make a filter to show footprints</strong>
            <span>
              {formatNumber(totalDatasets)} records are available. Add a filter and refresh the catalog to
              reduce records before footprints are drawn on the map.
            </span>
          </div>
        ) : null}
        {isAuthenticated && catalogSearched && !hasDatasetFilter ? (
          <div className="map-notice">
            <strong>Add a filter and refresh</strong>
            <span>
              Footprints stay hidden until a filter reduces the catalog. Select an area, platform, text, or
              administrative filter, then refresh the catalog.
            </span>
          </div>
        ) : null}
        {isAuthenticated && tooManyRecords ? (
          <div className="map-notice">
            <strong>{formatNumber(totalDatasets)} matching records</strong>
            <span>
              The map shows footprints only when fewer than {formatNumber(MAX_MAP_RECORDS)} records match.
              Make the filter more specific and refresh the catalog.
            </span>
          </div>
        ) : null}
        {isAuthenticated && catalogSearched && hasDatasetFilter && totalDatasets > 0 && loadedFootprintCount === 0 && !tooManyRecords ? (
          <div className="map-notice">
            <strong>{formatNumber(totalDatasets)} matching catalog records</strong>
            <span>
              {activeFilterLabel ? `Filters: ${activeFilterLabel}. ` : ""}
              Map overlays need scene footprints; the current indexed records do not have footprints yet.
            </span>
          </div>
        ) : null}
        {isAuthenticated && catalogSearched && hasDatasetFilter && totalDatasets === 0 ? (
          <div className="map-notice">
            <strong>No footprint-matched records</strong>
            <span>
              Administrative filters use scene footprints. The selected area is shown, but indexed scenes
              need footprint extraction before they can be counted by area.
            </span>
          </div>
        ) : null}
        {mapError ? (
          <div className="map-notice secondary-notice">
            <strong>Map unavailable</strong>
            <span>{mapError}</span>
          </div>
        ) : null}
        {loading ? (
          <div className="map-notice secondary-notice">
            <strong>Loading catalog</strong>
            <span>Fetching indexed data and reference layers.</span>
          </div>
        ) : null}
        {loadError ? (
          <div className="map-notice secondary-notice">
            <strong>Catalog load failed</strong>
            <span>{loadError}</span>
          </div>
        ) : null}
        {isAuthenticated && selectAreaMode ? (
          <div className="map-hint">
            <strong>Select Area</strong>
            <span>Drag a bounding box on the map.</span>
          </div>
        ) : null}
        {isAuthenticated && selected ? (
          <DatasetInspector
            selected={selected}
            user={session?.user ?? null}
            assetError={assetError}
            onClose={() => setSelected(null)}
            onDownload={() => void handleDownloadDataset(selected)}
            onDownloadOdc={() => void handleDownloadOdc(selected)}
          />
        ) : null}
        <div className="map" ref={mapNodeRef} />
      </section>

      {isAuthenticated && rightRailMode === "details" ? (
      <aside className="right-rail">
        <section className="panel">
          <button className="panel-title collapsible-title" onClick={() => toggleDetailSection("platforms")}>
            <span><Activity size={16} /> Status by Platform</span>
            {detailSections.platforms ? <ChevronDown size={15} /> : <ChevronRight size={15} />}
          </button>
          {detailSections.platforms ? (
            <div className="status-list">
              {platforms.map((item) => (
                <div key={item.platform} className="status-row">
                  <span>{item.platform}</span>
                  <strong>{formatNumber(item.total)}</strong>
                </div>
              ))}
            </div>
          ) : null}
        </section>

        <section className="panel">
          <button className="panel-title collapsible-title" onClick={() => toggleDetailSection("services")}>
            <span><Activity size={16} /> Service Status</span>
            {detailSections.services ? <ChevronDown size={15} /> : <ChevronRight size={15} />}
          </button>
          {detailSections.services ? (
            <div className="service-list">
              {services.map((item) => (
                <div key={item.service} className="service-row">
                  <div>
                    <strong>{item.label}</strong>
                    <span>{item.detail}</span>
                    {item.updated_at ? <small>Updated {formatDateTime(item.updated_at)}</small> : null}
                  </div>
                  <span className={`service-pill service-pill-${statusTone(item.status)}`}>
                    {item.status}
                  </span>
                </div>
              ))}
            </div>
          ) : null}
        </section>

        <section className="panel">
          <button className="panel-title collapsible-title" onClick={() => toggleDetailSection("sources")}>
            <span><FolderOpen size={16} /> Source Files</span>
            {detailSections.sources ? <ChevronDown size={15} /> : <ChevronRight size={15} />}
          </button>
          {detailSections.sources ? (
            <div className="source-list">
              {sources.slice(0, 5).map((item) => (
                <button key={item.id}>
                  <strong>{item.file_name}</strong>
                  <span>{item.folder}</span>
                </button>
              ))}
            </div>
          ) : null}
        </section>

      </aside>
      ) : null}
      {isAuthenticated && rightRailMode === "datasets" ? (
        <aside className="right-rail datasets-rail">
          <section className="panel">
            <div className="panel-title">
              <List size={16} /> Datasets
            </div>
            <p className="rail-summary">
              {catalogSearched && hasDatasetFilter
                ? `Showing ${formatNumber(datasets.length)} of ${formatNumber(
                    totalDatasets
                  )} matching records. Page ${formatNumber(datasetPage + 1)} of ${formatNumber(
                    totalDatasetPages
                  )}.`
                : "Add a filter and refresh the catalog to list datasets."}
            </p>
            {catalogSearched && hasDatasetFilter ? (
              <div className="pagination">
                <button
                  disabled={datasetPage <= 0}
                  onClick={() =>
                    void refreshCatalogPage(appliedFilters ?? filters, Math.max(0, datasetPage - 1))
                  }
                >
                  Previous
                </button>
                <span>
                  {formatNumber(datasetPage + 1)} / {formatNumber(totalDatasetPages)}
                </span>
                <button
                  disabled={datasetPage + 1 >= totalDatasetPages}
                  onClick={() =>
                    void refreshCatalogPage(
                      appliedFilters ?? filters,
                      Math.min(totalDatasetPages - 1, datasetPage + 1)
                    )
                  }
                >
                  Next
                </button>
              </div>
            ) : null}
            <div className="dataset-list">
              {datasets.map((item) => (
                <button
                  key={item.id}
                  className={selected?.id === item.id ? "selected" : ""}
                  onClick={() => selectDataset(item)}
                >
                  <strong>{item.title}</strong>
                  <span>{item.source_path}</span>
                  <small>
                    {[item.platform, item.sensor, item.dataset_type].filter(Boolean).join(" / ")}
                  </small>
                </button>
              ))}
            </div>
          </section>
        </aside>
      ) : null}
      {isAuthenticated && rightRailMode === "activity" ? (
        <aside className="right-rail activity-rail">
          <section className="panel">
            <div className="panel-title">
              <Activity size={16} /> My Activity
            </div>
            <p className="rail-summary">
              {session?.user.role === "mage"
                ? `Token balance: ${formatNumber(session.user.token_balance)}`
                : "Recent actions for your account."}
            </p>
            <button
              className="rail-refresh-button"
              onClick={() => void refreshActivity()}
              disabled={activityLoading}
            >
              <RefreshCw size={14} /> {activityLoading ? "Refreshing" : "Refresh Activity"}
            </button>
            {activityError ? <div className="asset-error">{activityError}</div> : null}
            <div className="activity-list">
              {activities.length ? (
                activities.map((item) => (
                  <div key={item.id} className="activity-row">
                    <div>
                      <strong>{formatActivityLabel(item.activity)}</strong>
                      <span>{formatActivityMetadata(item)}</span>
                      <small>{formatDateTime(item.created_at)}</small>
                    </div>
                    {item.token_delta !== 0 ? (
                      <span className={item.token_delta < 0 ? "token-charge" : "token-credit"}>
                        {item.token_delta > 0 ? "+" : ""}
                        {formatNumber(item.token_delta)}
                      </span>
                    ) : null}
                  </div>
                ))
              ) : (
                <p className="empty-note">
                  {activityLoading ? "Loading activity..." : "No activity has been recorded yet."}
                </p>
              )}
            </div>
          </section>
        </aside>
      ) : null}
    </main>
  );
}

function DatasetInspector({
  selected,
  user,
  assetError,
  onClose,
  onDownload,
  onDownloadOdc
}: {
  selected: Dataset;
  user: AccessUser | null;
  assetError: string;
  onClose: () => void;
  onDownload: () => void;
  onDownloadOdc: () => void;
}) {
  const canAccessAssets = Boolean(user?.policy.can_access_assets);
  return (
    <section className="dataset-inspector">
      <div className="inspector-title">
        <div className="panel-title">
          <Layers size={16} /> Selected Dataset
        </div>
        <button className="icon-button" onClick={onClose} aria-label="Close selected dataset">
          <X size={16} />
        </button>
      </div>
      <div className="detail-list">
        <strong>{selected.title}</strong>
        <span>{selected.source_path}</span>
        <div className="action-row">
          <button disabled={!canAccessAssets} onClick={onDownload} title={canAccessAssets ? "Costs 10 Mage tokens" : "Asset access is not available for this role"}>
            <Download size={14} /> Download
          </button>
          <button disabled={!canAccessAssets} onClick={onDownloadOdc} title={canAccessAssets ? "Costs 5 Mage tokens" : "Asset access is not available for this role"}>
            <ExternalLink size={14} /> ODC
          </button>
          <a
            href={`/stac/collections/${selected.collection_id}/items/${selected.stac_item_id}`}
            target="_blank"
            rel="noreferrer"
          >
            <ExternalLink size={14} /> STAC
          </a>
        </div>
        {assetError ? <div className="asset-error">{assetError}</div> : null}
        <dl>
          <dt>Collection</dt>
          <dd>{selected.collection_id}</dd>
          <dt>Platform</dt>
          <dd>{selected.platform ?? "-"}</dd>
          <dt>Sensor</dt>
          <dd>{selected.sensor ?? "-"}</dd>
          <dt>File</dt>
          <dd>{selected.file_name}</dd>
          <dt>Size</dt>
          <dd>{formatBytes(selected.file_size_bytes)}</dd>
          <dt>Acquired</dt>
          <dd>{formatDateTime(selected.acquisition_start)}</dd>
          <dt>Modified</dt>
          <dd>{formatDateTime(selected.modified_at)}</dd>
          <dt>Cloud coverage</dt>
          <dd>{formatCloudCover(selected.properties)}</dd>
          <dt>BBox</dt>
          <dd>{selected.bbox ? selected.bbox.map((value) => value.toFixed(4)).join(", ") : "-"}</dd>
        </dl>
      </div>
    </section>
  );
}

function LandingStatus({
  platforms,
  totalDatasets
}: {
  platforms: PlatformStatus[];
  totalDatasets: number;
}) {
  return (
    <section className="landing-status">
      <div className="panel-title">
        <Activity size={16} /> Status by Platform
      </div>
      <div className="landing-total">
        <strong>{formatNumber(totalDatasets)}</strong>
        <span>indexed records</span>
      </div>
      <div className="status-list">
        {platforms.slice(0, 8).map((item) => (
          <div key={item.platform} className="status-row">
            <span>{item.platform}</span>
            <strong>{formatNumber(item.total)}</strong>
          </div>
        ))}
      </div>
    </section>
  );
}

function loadSession(): Session | null {
  try {
    const raw = window.localStorage.getItem(SESSION_STORAGE_KEY);
    if (!raw) {
      return null;
    }
    const parsed = JSON.parse(raw) as Session;
    if (!parsed?.headerValue || !parsed?.user?.username || !parsed?.expiresAt) {
      return null;
    }
    if (Date.parse(parsed.expiresAt) <= Date.now()) {
      window.localStorage.removeItem(SESSION_STORAGE_KEY);
      return null;
    }
    return parsed;
  } catch {
    return null;
  }
}

function emptyCollection(): GeoJSON.FeatureCollection {
  return { type: "FeatureCollection", features: [] };
}

function toFootprintCollection(datasets: Dataset[]): GeoJSON.FeatureCollection {
  return {
    type: "FeatureCollection",
    features: datasets
      .filter((item) => item.bbox && item.bbox.length === 4)
      .map((item) => {
        const [west, south, east, north] = item.bbox!;
        return {
          type: "Feature",
          properties: { id: item.id },
          geometry: {
            type: "Polygon",
            coordinates: [[[west, south], [east, south], [east, north], [west, north], [west, south]]]
          }
        };
      })
  };
}

function toSingleFootprintCollection(dataset: Dataset): GeoJSON.FeatureCollection {
  if (!dataset.bbox || dataset.bbox.length !== 4) {
    return emptyCollection();
  }
  return {
    type: "FeatureCollection",
    features: [bboxFeature(dataset.bbox)]
  };
}

function bboxFromLngLats(start: maplibregl.LngLat, end: maplibregl.LngLat): number[] {
  return [
    Math.min(start.lng, end.lng),
    Math.min(start.lat, end.lat),
    Math.max(start.lng, end.lng),
    Math.max(start.lat, end.lat)
  ];
}

function bboxFeature(bbox: number[]): GeoJSON.Feature {
  const [west, south, east, north] = bbox;
  return {
    type: "Feature",
    properties: {},
    geometry: {
      type: "Polygon",
      coordinates: [[[west, south], [east, south], [east, north], [west, north], [west, south]]]
    }
  };
}

function visitCoordinates(
  geometry: GeoJSON.Geometry | null,
  callback: (coordinate: GeoJSON.Position) => void
) {
  if (!geometry) {
    return;
  }
  if (geometry.type === "Point") {
    callback(geometry.coordinates);
    return;
  }
  if (geometry.type === "GeometryCollection") {
    geometry.geometries.forEach((item) => visitCoordinates(item, callback));
    return;
  }
  visitNestedCoordinates(geometry.coordinates, callback);
}

function visitNestedCoordinates(value: unknown, callback: (coordinate: GeoJSON.Position) => void) {
  if (!Array.isArray(value)) {
    return;
  }
  if (typeof value[0] === "number" && typeof value[1] === "number") {
    callback(value as GeoJSON.Position);
    return;
  }
  value.forEach((item) => visitNestedCoordinates(item, callback));
}

function formatNumber(value: number | null | undefined) {
  return Number(value ?? 0).toLocaleString();
}

function formatCloudCover(properties: Record<string, unknown>) {
  const value = readNumericProperty(properties.cloud_cover);
  if (value === null) {
    return "Not available";
  }
  const landValue = readNumericProperty(properties.cloud_cover_land);
  const land = landValue !== null ? `, land ${landValue.toFixed(1)}%` : "";
  const method = formatCloudMethod(properties.cloud_method);
  return `${value.toFixed(1)}%${land}${method}`;
}

function readNumericProperty(value: unknown) {
  if (typeof value === "number" && Number.isFinite(value)) {
    return value;
  }
  if (typeof value === "string" && value.trim()) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}

function formatCloudMethod(value: unknown) {
  if (value === "landsat_mtl") {
    return " (Landsat metadata)";
  }
  if (value === "sentinel2_mtd") {
    return " (Sentinel-2 metadata)";
  }
  if (value === "estimated_rgb") {
    return " (estimated from RGB)";
  }
  return typeof value === "string" && value ? ` (${value})` : "";
}

function parseCloudSliderValue(value: string, fallback: number) {
  if (!value.trim()) {
    return fallback;
  }
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) {
    return fallback;
  }
  return Math.min(100, Math.max(0, parsed));
}

function formatActivityLabel(activity: AccessActivity["activity"]) {
  if (activity === "search") {
    return "Search/filter";
  }
  if (activity === "download") {
    return "Download asset";
  }
  if (activity === "odc_asset") {
    return "ODC access";
  }
  if (activity === "stac_asset") {
    return "STAC access";
  }
  if (activity === "admin_adjustment") {
    return "Token adjustment";
  }
  return activity;
}

function formatActivityMetadata(item: AccessActivity) {
  const fileName = item.metadata.file_name;
  if (typeof fileName === "string" && fileName) {
    return fileName;
  }
  if (item.activity === "search") {
    const total = typeof item.metadata.total === "number" ? item.metadata.total : null;
    const platform = typeof item.metadata.platform === "string" ? item.metadata.platform : "";
    const q = typeof item.metadata.q === "string" ? item.metadata.q : "";
    const parts = [
      total !== null ? `${formatNumber(total)} matching records` : "Catalog search",
      platform ? `platform ${platform}` : "",
      q ? `text ${q}` : ""
    ].filter(Boolean);
    return parts.join(" / ");
  }
  return item.dataset_id ?? "Account activity";
}

function hasActiveDatasetFilter(filters: DatasetFilters) {
  return Boolean(
    filters.q.trim() ||
      filters.datasetType.trim() ||
      filters.platform.trim() ||
      filters.sensor.trim() ||
      filters.dateFrom.trim() ||
      filters.dateTo.trim() ||
      filters.cloudMin.trim() ||
      filters.cloudMax.trim() ||
      filters.province.trim() ||
      filters.kabupaten.trim() ||
      filters.kecamatan.trim() ||
      filters.bbox?.length
  );
}

function formatDateTime(value: string | null | undefined) {
  if (!value) {
    return "-";
  }
  return new Date(value).toLocaleString();
}

function statusTone(status: string) {
  const normalized = status.toLowerCase();
  if (["running", "synced", "completed", "available"].includes(normalized)) {
    return "ok";
  }
  if (["pending", "unknown"].includes(normalized)) {
    return "muted";
  }
  if (["failed", "error"].includes(normalized)) {
    return "bad";
  }
  return "muted";
}

function formatBytes(value: number | null | undefined) {
  const bytes = Number(value ?? 0);
  if (bytes < 1024) {
    return `${bytes} B`;
  }
  if (bytes < 1024 * 1024) {
    return `${(bytes / 1024).toFixed(1)} KB`;
  }
  if (bytes < 1024 * 1024 * 1024) {
    return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  }
  return `${(bytes / 1024 / 1024 / 1024).toFixed(1)} GB`;
}
