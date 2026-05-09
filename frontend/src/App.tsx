import {
  Download,
  ExternalLink,
  Activity,
  Crosshair,
  Database,
  FolderOpen,
  Layers,
  List,
  Map as MapIcon,
  PanelRightClose,
  PanelRightOpen,
  RefreshCw,
  Satellite,
  Search,
  X
} from "lucide-react";
import maplibregl from "maplibre-gl";
import { useEffect, useMemo, useRef, useState } from "react";
import {
  getBoundary,
  getDatasets,
  getLocations,
  getPlatforms,
  getRuns,
  getSourceFiles,
  apiBase,
  type DatasetFilters
} from "./api";
import logo from "./assets/geocatalog-logo.png";
import type { Dataset, LocationOptions, PlatformStatus, ScanRun, SourceFile } from "./types";

type Basemap = "street" | "satellite";
type RightRailMode = "details" | "datasets" | null;
const MAX_MAP_RECORDS = 1000;
const DATASET_PAGE_SIZE = 100;

export default function App() {
  const mapRef = useRef<maplibregl.Map | null>(null);
  const mapNodeRef = useRef<HTMLDivElement | null>(null);
  const mapDatasetsRef = useRef<Dataset[]>([]);
  const filtersRef = useRef<DatasetFilters | null>(null);
  const selectAreaModeRef = useRef(false);
  const dragStartRef = useRef<maplibregl.LngLat | null>(null);
  const [datasets, setDatasets] = useState<Dataset[]>([]);
  const [mapDatasets, setMapDatasets] = useState<Dataset[]>([]);
  const [totalDatasets, setTotalDatasets] = useState(0);
  const [datasetPage, setDatasetPage] = useState(0);
  const [catalogSearched, setCatalogSearched] = useState(false);
  const [appliedFilters, setAppliedFilters] = useState<DatasetFilters | null>(null);
  const [platforms, setPlatforms] = useState<PlatformStatus[]>([]);
  const [runs, setRuns] = useState<ScanRun[]>([]);
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
  const [selectAreaMode, setSelectAreaMode] = useState(false);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
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
      const [countResponse, platformRows, runRows, sourceRows] = await Promise.all([
        getDatasets(filters, 1, 0),
        getPlatforms(),
        getRuns(),
        getSourceFiles()
      ]);
      setTotalDatasets(countResponse.total);
      setDatasets([]);
      setMapDatasets([]);
      setSelected(null);
      setPlatforms(platformRows);
      setRuns(runRows);
      setSources(sourceRows);
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
      const [datasetResponse, platformRows, runRows, sourceRows] = await Promise.all([
        active ? getDatasets(nextFilters, DATASET_PAGE_SIZE, offset) : getDatasets(nextFilters, 1, 0),
        getPlatforms(),
        getRuns(),
        getSourceFiles()
      ]);
      setTotalDatasets(datasetResponse.total);
      setDatasetPage(active ? page : 0);
      setDatasets(active ? datasetResponse.items : []);
      setPlatforms(platformRows);
      setRuns(runRows);
      setSources(sourceRows);

      if (active && datasetResponse.total < MAX_MAP_RECORDS) {
        const mapResponse = await getDatasets(nextFilters, MAX_MAP_RECORDS, 0);
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
    <main className={`app-shell ${rightRailMode ? "" : "rail-collapsed"}`}>
      <aside className="sidebar">
        <div className="brand">
          <img className="brand-logo" src={logo} alt="GeoCatalog" />
          <div>
            <p>geocatalog</p>
            <span>Multi-Satellite Data Aquisition and Cataloging Platform</span>
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
      </aside>

      <section className="map-stage">
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
          <button className="toolbar-button" onClick={downloadVisibleGeoJson}>
            <Download size={15} />
            GeoJSON
          </button>
        </div>
        {!catalogSearched ? (
          <div className="map-notice">
            <strong>Make a filter to show footprints</strong>
            <span>
              {formatNumber(totalDatasets)} records are available. Add a filter and refresh the catalog to
              reduce records before footprints are drawn on the map.
            </span>
          </div>
        ) : null}
        {catalogSearched && !hasDatasetFilter ? (
          <div className="map-notice">
            <strong>Add a filter and refresh</strong>
            <span>
              Footprints stay hidden until a filter reduces the catalog. Select an area, platform, text, or
              administrative filter, then refresh the catalog.
            </span>
          </div>
        ) : null}
        {tooManyRecords ? (
          <div className="map-notice">
            <strong>{formatNumber(totalDatasets)} matching records</strong>
            <span>
              The map shows footprints only when fewer than {formatNumber(MAX_MAP_RECORDS)} records match.
              Make the filter more specific and refresh the catalog.
            </span>
          </div>
        ) : null}
        {catalogSearched && hasDatasetFilter && totalDatasets > 0 && loadedFootprintCount === 0 && !tooManyRecords ? (
          <div className="map-notice">
            <strong>{formatNumber(totalDatasets)} matching catalog records</strong>
            <span>
              {activeFilterLabel ? `Filters: ${activeFilterLabel}. ` : ""}
              Map overlays need scene footprints; the current indexed records do not have footprints yet.
            </span>
          </div>
        ) : null}
        {catalogSearched && hasDatasetFilter && totalDatasets === 0 ? (
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
        {selectAreaMode ? (
          <div className="map-hint">
            <strong>Select Area</strong>
            <span>Drag a bounding box on the map.</span>
          </div>
        ) : null}
        {selected ? <DatasetInspector selected={selected} onClose={() => setSelected(null)} /> : null}
        <div className="map" ref={mapNodeRef} />
      </section>

      {rightRailMode === "details" ? (
      <aside className="right-rail">
        <section className="panel">
          <div className="panel-title">
            <Activity size={16} /> Status by Platform
          </div>
          <div className="status-list">
            {platforms.map((item) => (
              <div key={item.platform} className="status-row">
                <span>{item.platform}</span>
                <strong>{formatNumber(item.total)}</strong>
              </div>
            ))}
          </div>
        </section>

        <section className="panel">
          <div className="panel-title">
            <Database size={16} /> Recent Runs
          </div>
          <div className="run-list">
            {runs.slice(0, 3).map((run) => (
              <div key={run.id} className="run-row">
                <strong>{formatDateTime(run.started_at)}</strong>
                <span>{run.status}</span>
                <small>
                  {formatNumber(run.scanned_files)} scanned, {formatNumber(run.indexed_files)} new,{" "}
                  {formatNumber(run.unchanged_files)} unchanged, {formatNumber(run.removed_files)} removed
                </small>
              </div>
            ))}
          </div>
        </section>

        <section className="panel">
          <div className="panel-title">
            <FolderOpen size={16} /> Source Files
          </div>
          <div className="source-list">
            {sources.slice(0, 3).map((item) => (
              <button key={item.id}>
                <strong>{item.file_name}</strong>
                <span>{item.folder}</span>
              </button>
            ))}
          </div>
        </section>

      </aside>
      ) : null}
      {rightRailMode === "datasets" ? (
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
    </main>
  );
}

function DatasetInspector({ selected, onClose }: { selected: Dataset; onClose: () => void }) {
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
          <a href={`${apiBase}/datasets/${selected.id}/download`}>
            <Download size={14} /> Download
          </a>
          <a href={`${apiBase}/datasets/${selected.id}/odc`} target="_blank" rel="noreferrer">
            <ExternalLink size={14} /> ODC
          </a>
          <a
            href={`/stac/collections/${selected.collection_id}/items/${selected.id}`}
            target="_blank"
            rel="noreferrer"
          >
            <ExternalLink size={14} /> STAC
          </a>
        </div>
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
          <dt>BBox</dt>
          <dd>{selected.bbox ? selected.bbox.map((value) => value.toFixed(4)).join(", ") : "-"}</dd>
        </dl>
      </div>
    </section>
  );
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

function hasActiveDatasetFilter(filters: DatasetFilters) {
  return Boolean(
    filters.q.trim() ||
      filters.datasetType.trim() ||
      filters.platform.trim() ||
      filters.sensor.trim() ||
      filters.dateFrom.trim() ||
      filters.dateTo.trim() ||
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
