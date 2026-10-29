import { $, aiBadge, el, fold, hydrateIcons, info, modal, section, siteFooter, svg, toast } from "/ui.js";
import { applyStatic, DASHBOARD_ID, langSwitch, lang, locale, makeT } from "/i18n.js";
import { authHeaders, openSignIn, token, verifierControl, VERIFIER_ID } from "/verifier.js";

const t = makeT({ ...DASHBOARD_ID, ...VERIFIER_ID }, "en");

// MapLibre (about 300 KB) loads in parallel with the data instead of blocking the first API request.
const MAPLIBRE_URL = "https://unpkg.com/maplibre-gl@6.16.0/dist/maplibre-gl.mjs";
let maplibregl;

const CFG = window.KARHUTLA_CONFIG || { mapProvider: "maplibre", attributions: [] };
const PANEL_KEY = "karhutla.panelHidden";
const PAGE = 60;
const RECENT_HOURS = 6;
const BASEMAP_STYLE = "https://tiles.openfreemap.org/styles/positron";
const HOME_VIEW = { center: [117.5, -1.5], zoom: 4.4 };
const BASEMAP_KEY = "karhutla.basemap";
// World view: Esri World Imagery under the positron boundaries and labels. Display only, never evidence.
const WORLD_TILES = "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}";
// Credits as listed on the ArcGIS item 10df2279f9684e4a9f6a7f08febac2a9 (checked 2026-10-29).
const WORLD_ATTRIBUTION = "Imagery: Esri, Vantor, Earthstar Geographics, and the GIS User Community";

const STATUS = {
  pending_triage: { label: "Pending triage", color: "#7a8b99" },
  triaging: { label: "Triage running", color: "#0064e0" },
  likely_fire: { label: "Likely fire", color: "#d92d20" },
  possible_fire: { label: "Possible fire", color: "#f79009" },
  unlikely: { label: "Unlikely", color: "#12b76a" },
  needs_more_info: { label: "Needs more info", color: "#7a5af8" },
};
const LEGEND_STATUSES = ["pending_triage", "likely_fire", "possible_fire", "unlikely", "needs_more_info"];
// Fire radiative power (MW) to heat color. FRP is measured by the satellite, not estimated.
const HEAT_STOPS = [[0, "#ffd66b"], [5, "#ffab40"], [15, "#ff7a1a"], [40, "#ef4423"], [100, "#b3122e"]];
const HEAT_MAX = 100;

// NASA GIBS daily layers, display only. "default" asks GIBS for the latest available day of each layer.
const GIBS = "https://gibs.earthdata.nasa.gov/wmts/epsg3857/best";
const NASA_LAYERS = [
  { key: "truecolor", id: "VIIRS_SNPP_CorrectedReflectance_TrueColor", tms: "GoogleMapsCompatible_Level9", ext: "jpg", maxzoom: 9, opacity: 0.9,
    label: "True color (VIIRS)", tip: "Daily true-color image from VIIRS. Smoke plumes are visible as grey-brown haze." },
  { key: "aod", id: "MODIS_Combined_Value_Added_AOD", tms: "GoogleMapsCompatible_Level6", ext: "png", maxzoom: 6, opacity: 0.65,
    label: "Aerosol optical depth (MODIS)", tip: "How much haze, smoke, or dust blocks sunlight. Higher is thicker. Each square is one cell of the product, about 100 km wide, so it shows regional haze, not where a fire is. Gaps are clouds or no data." },
  { key: "aerosolindex", id: "OMPS_Aerosol_Index", tms: "GoogleMapsCompatible_Level6", ext: "png", maxzoom: 6, opacity: 0.65,
    label: "Smoke and dust index (OMPS)", tip: "UV aerosol index: positive values flag light-absorbing aerosol such as smoke and dust, even above some clouds." },
];

const statusLabel = (s) => t((STATUS[s] || STATUS.pending_triage).label);
const plural = (n) => (lang() === "en" && n !== 1 ? "s" : "");

function heatColor(frp) {
  if (frp == null) return "#c5ced6";
  for (let i = HEAT_STOPS.length - 1; i >= 0; i--) if (frp >= HEAT_STOPS[i][0]) return HEAT_STOPS[i][1];
  return HEAT_STOPS[0][1];
}
const heatExpr = (prop) => ["interpolate", ["linear"], ["coalesce", ["get", prop], 0], ...HEAT_STOPS.flat()];


async function api(path, opts = {}) {
  const res = await fetch(path, { ...opts, headers: { "Content-Type": "application/json", ...authHeaders(), ...(opts.headers || {}) } });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `HTTP ${res.status}`);
  }
  return res.json();
}

const fmtTime = (iso) =>
  new Date(iso).toLocaleString(locale(), { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", timeZone: "Asia/Makassar" }) + " WITA";
const fmtNum = (n) => (n == null ? t("n/a") : Number(n).toLocaleString(locale()));
const fmtDate = (iso) => new Date(iso).toLocaleDateString(locale(), { day: "numeric", month: "short", year: "numeric" });
const fmtFrp = (v) => (v == null ? t("n/a") : `${Number(v).toLocaleString(locale(), { minimumFractionDigits: 1, maximumFractionDigits: 1 })} MW`);

/** replaceChildren that skips null and false, which the DOM would otherwise print as text. */
const fill = (node, ...kids) => node.replaceChildren(...kids.flat(Infinity).filter((k) => k != null && k !== false));

/* State */
const state = {
  incidents: [], hotspots: [], stats: null, filter: "all", sort: "heat", q: "", shown: PAGE, selected: null, recentSince: null,
  layers: { heatmap: true, hotspots: true, incidents: true, truecolor: false, aod: false, aerosolindex: false },
  picking: false, control: null, sources: { gibs: { state: "unknown" } },
};
const picked = new Set();

const isRecent = (iso) => state.recentSince && iso > state.recentSince;

/* KPIs */
/* The header shares its width with the language switch, verifier controls, and the report group, whose widths
   change with language and mode. Show as many whole KPI tiles as fit, dropping from the right; never a cut tile. */
let kpiObserver;
function fitKpis() {
  const box = $("#kpis");
  if (!box) return;
  const tiles = [...box.children];
  tiles.forEach((k) => { k.hidden = false; });
  for (let i = tiles.length - 1; i > 0 && box.scrollWidth > box.clientWidth + 1; i--) tiles[i].hidden = true;
}

function renderKpis() {
  const s = state.stats;
  const tile = (icon, value, label, tip, heat = false) =>
    el("div", { class: "kpi" }, el("span", { class: `kpi-icon ${heat ? "heat" : ""}` }, svg(icon, "icon icon-sm")),
      el("div", {}, s ? el("div", { class: "kpi-value num" }, value) : el("span", { class: "sk" }), el("div", { class: "kpi-label" }, t(label), info(t(tip)))));
  if (!kpiObserver) { kpiObserver = new ResizeObserver(fitKpis); kpiObserver.observe($("#kpis")); }
  fill($("#kpis"),
    tile("list", s && fmtNum(s.incidents), "Incidents", "Hotspot clusters currently stored. Most are a single hotspot; filter for 3+ to focus."),
    tile("dots", s && fmtNum(s.incidents_multi_hotspot), "With 3+ hotspots", "Clusters with at least three detections, a stronger thermal signal."),
    tile("flame", s && fmtNum(s.hotspots_last_24h), "Hotspots, 24 h", "FIRMS detections in the 24 hours before the newest acquisition.", true),
    tile("target", s && fmtNum(s.high_conf_last_24h), "High confidence", "VIIRS high confidence (h) or MODIS 80 and above in the same window.", true),
    tile("clock", s && s.newest_acquisition ? fmtTime(s.newest_acquisition) : t("n/a"), "Newest pass", "Acquisition time of the newest satellite detection."),
  );
  fitKpis();  // reading scrollWidth lays the row out now; no frame callback needed
}

/* Map */
let map;
let popup;

/* View controls: zoom and reset in one horizontal group above the source status and the legend. */
function renderViewControls() {
  const btn = (icon, label, onclick) => el("button", { type: "button", class: "map-btn", title: label, "aria-label": label, onclick }, svg(icon, "icon icon-sm"));
  fill($("#view-controls"),
    btn("plus", t("Zoom in"), () => map?.zoomIn()),
    btn("minus", t("Zoom out"), () => map?.zoomOut()),
    btn("reset", t("Reset map view"), resetView));
  renderBasemapSwitch();
}

/* Basemap switch under the zoom group: the light map, or world satellite imagery with the same labels. */
const basemap = () => (localStorage.getItem(BASEMAP_KEY) === "map" ? "map" : "world");  // World by default (owner choice, decision 53)
let baseLayers = [];  // positron layer ids, captured before the app adds its own layers
const savedPaint = new Map();  // layer id + property -> positron value, restored when leaving World

function renderBasemapSwitch() {
  const opt = (key, label, tip) => el("button", { type: "button", class: "map-btn basemap-opt", title: tip, "aria-label": tip,
    "aria-pressed": String(basemap() === key), onclick: () => setBasemap(key) }, label);
  fill($("#basemap-switch"),
    opt("map", t("Map"), t("Light map with places, roads, and boundaries.")),
    opt("world", t("World"), t("World satellite imagery from Esri with the same boundaries and labels. Display only: not used as evidence, and the image date varies by place.")));
}

function setBasemap(key) {
  localStorage.setItem(BASEMAP_KEY, key);
  renderBasemapSwitch();
  applyBasemap();
}

function applyBasemap() {
  if (!map?.getLayer("world-imagery")) return;
  const world = basemap() === "world";
  const paint = (id, prop, value) => {
    const k = `${id}|${prop}`;
    if (world) {
      if (!savedPaint.has(k)) savedPaint.set(k, map.getPaintProperty(id, prop));
      map.setPaintProperty(id, prop, value);
    } else if (savedPaint.has(k)) {
      map.setPaintProperty(id, prop, savedPaint.get(k));
    }
  };
  map.setLayoutProperty("world-imagery", "visibility", world ? "visible" : "none");
  for (const id of baseLayers) {
    const type = map.getLayer(id).type;
    const keep = type === "symbol" || id.startsWith("boundary") || id.startsWith("highway_motorway");
    map.setLayoutProperty(id, "visibility", !world || keep ? "visible" : "none");
    if (type === "symbol") {
      paint(id, "text-color", "#ffffff");
      paint(id, "text-halo-color", "rgba(0,0,0,0.75)");
      paint(id, "text-halo-width", 1.4);
    } else if (id.startsWith("boundary")) {
      paint(id, "line-color", "rgba(255,255,255,0.7)");
    } else if (id.startsWith("highway_motorway")) {
      paint(id, "line-opacity", 0.45);
    }
  }
}

function addWorldImagery() {
  baseLayers = map.getStyle().layers.map((l) => l.id);
  map.addSource("world-imagery", { type: "raster", tileSize: 256, maxzoom: 19, tiles: [WORLD_TILES], attribution: WORLD_ATTRIBUTION });
  // Right above the background, so every positron layer that stays visible draws over the imagery.
  map.addLayer({ id: "world-imagery", type: "raster", source: "world-imagery", layout: { visibility: "none" } }, baseLayers[1]);
  applyBasemap();
}

/** Reset goes all the way back: home view, and an open incident returns to the list. */
function resetView() {
  map?.flyTo({ ...HOME_VIEW, speed: 1.6 });
  if (state.selected || !$("#detail-view").hidden) renderList();
}

/* Source status: what the app last observed from each source, so a verifier knows what is current. */
const SOURCES = [
  { key: "firms", name: "FIRMS", tip: "NASA FIRMS satellite hotspots (VIIRS and MODIS), fetched every 30 minutes." },
  { key: "gibs", name: "GIBS", tip: "NASA GIBS satellite layers for the map, checked from this browser." },
  { key: "weather", name: "Open-Meteo", tip: "Wind, humidity, and rain evidence for each incident." },
  { key: "osm", name: "OSM", tip: "Nearby settlements, roads, and water from OpenStreetMap (Overpass)." },
  { key: "sentinel2", name: "Sentinel-2", tip: "Sentinel-2 before and after images for burn scars, fetched on request." },
];
const SRC_STATE = { ok: "On", error: "Down", sim: "Simulation", unknown: "Not checked", fallback: "Offline copy" };
const SOURCES_REFRESH_MS = 5 * 60e3;

let srcBox;

/** The status bar is a map control, so it shares the bottom row with the scale and the attribution. */
const sourceStatusControl = {
  onAdd() {
    srcBox = el("div", { class: "maplibregl-ctrl src-status card overlay", role: "status", "aria-label": t("Data sources") });
    renderSources();
    return srcBox;
  },
  onRemove() { srcBox.remove(); srcBox = null; },
};

function renderSources() {
  if (!srcBox) return;
  fill(srcBox, SOURCES.map((s) => {
    const v = state.sources[s.key] || { state: "unknown" };
    const lines = [t(s.tip)];
    if (v.newest_acquisition) lines.push(t("Newest pass: {when}.", { when: fmtTime(v.newest_acquisition) }));
    if (v.checked_at) lines.push(t("Last checked: {when}.", { when: fmtTime(v.checked_at) }));
    if (v.state === "error" && v.detail) lines.push(v.detail);
    return el("span", { class: "info src-item", tabindex: "0", dataset: { state: v.state } },
      el("span", { class: "src-name" }, s.name),
      el("span", { class: "src-state" }, el("span", { class: "src-dot" }), el("span", { class: "src-word" }, t(SRC_STATE[v.state] || SRC_STATE.unknown))),
      el("span", { class: "tip", role: "tooltip" }, lines.join(" ")));
  }), triageStatus());
}

/* AI triage in the same status bar: ready in verifier mode, otherwise only a verifier can run it. */
function triageStatus() {
  const ready = !!token();
  const mode = state.control?.mode;
  const lines = [t("AI triage runs only on purpose: a verifier selects incidents and confirms with an execution code, within a daily cap.")];
  if (ready && mode) lines.push(t("Scheduled triage: {m}.", { m: t(mode === "off" ? "off" : mode) }));
  return el("span", { class: "info src-item", tabindex: "0", dataset: { state: ready ? "ok" : "unknown" } },
    el("span", { class: "src-name" }, t("AI triage")),
    el("span", { class: "src-state" }, el("span", { class: "src-dot" }), el("span", { class: "src-word" }, t(ready ? "Ready" : "Verifier only"))),
    el("span", { class: "tip", role: "tooltip" }, lines.join(" ")));
}

async function loadSources() {
  const s = await api("/api/sources").catch(() => null);
  if (s) Object.assign(state.sources, s);
  renderSources();
}

function setGibs(st, detail = null) {
  state.sources.gibs = { state: st, checked_at: new Date().toISOString(), detail };
  renderSources();
}

// One small tile tells whether GIBS answers, before any NASA layer is switched on.
function probeGibs() {
  const L = NASA_LAYERS[1];
  const img = new Image();
  img.onload = () => setGibs("ok");
  img.onerror = () => setGibs("error", "GIBS tile request failed");
  img.src = `${GIBS}/${L.id}/default/default/${L.tms}/0/0/0.${L.ext}`;
}

async function initMap() {
  maplibregl = await import(MAPLIBRE_URL);
  if (CFG.mapProvider === "google") {
    const note = $("#map-note");
    note.textContent = t("Google Maps adapter is not built; showing MapLibre.");
    note.hidden = false;
  }
  // hash keeps the view in the URL (#map=zoom/lat/lon) so a verifier can share an exact map position.
  map = new maplibregl.Map({ container: "map", style: BASEMAP_STYLE, ...HOME_VIEW, hash: "map", attributionControl: false });
  map.on("error", (e) => { if (String(e.sourceId || "").startsWith("nasa-") && state.sources.gibs.state !== "error") setGibs("error", e.error?.message || "GIBS tile failed"); });
  // Bottom controls are inserted first-in-row, so adding attribution, scale, then status reads status, scale, attribution.
  map.addControl(new maplibregl.AttributionControl({ compact: true }), "bottom-right");
  map.addControl(new maplibregl.ScaleControl({ unit: "metric" }), "bottom-right");
  map.addControl(sourceStatusControl, "bottom-right");
  collapseAttribution(map);
  popup = new maplibregl.Popup({ closeButton: false, closeOnClick: false, offset: 10 });
  await new Promise((resolve) => map.on("load", resolve));
  addWorldImagery();
  addNasaLayers();
}

/** MapLibre opens the compact attribution once its sources load; close it that first time. The (i) still opens it. */
function collapseAttribution(m) {
  const box = m.getContainer().querySelector(".maplibregl-ctrl-attrib");
  if (!box) return;
  const close = () => {
    if (!box.classList.contains("maplibregl-compact-show")) return;
    box.classList.remove("maplibregl-compact-show");
    box.removeAttribute("open");
    watch.disconnect();
  };
  const watch = new MutationObserver(close);
  watch.observe(box, { attributes: true, attributeFilter: ["class"] });
  close();
}

function addNasaLayers() {
  // Under the basemap labels, so place names stay readable over satellite imagery.
  const firstSymbol = map.getStyle().layers.find((l) => l.type === "symbol")?.id;
  for (const L of NASA_LAYERS) {
    map.addSource(`nasa-${L.key}`, {
      type: "raster", tileSize: 256, maxzoom: L.maxzoom,
      tiles: [`${GIBS}/${L.id}/default/default/${L.tms}/{z}/{y}/{x}.${L.ext}`],
      attribution: "Imagery: NASA GIBS / Worldview",
    });
    map.addLayer({ id: `nasa-${L.key}`, type: "raster", source: `nasa-${L.key}`, paint: { "raster-opacity": L.opacity },
      layout: { visibility: state.layers[L.key] ? "visible" : "none" } }, firstSymbol);
  }
}

function geo(items, props) {
  return { type: "FeatureCollection", features: items.map((i) => ({ type: "Feature", geometry: { type: "Point", coordinates: [i.lon, i.lat] }, properties: props(i) })) };
}

/* Background work per incident (collecting evidence, searching Sentinel-2, planning a drone survey). While it runs,
   the button shows a spinner, the incident pulses on the map, its list card gets a badge, and the incident view a
   banner. Triage running on the server (status triaging) shows the same way. */
const busy = new Map();  // incident id -> Map(key -> label)
const busyLabels = (id) => [...(busy.get(id)?.values() || []), ...(state.incidents.find((i) => i.id === id)?.status === "triaging" ? [t("Triage running")] : [])];

async function track(id, key, label, work) {
  if (busy.get(id)?.has(key)) return toast(t("Already running: {what}", { what: label }), "alert");
  if (!busy.has(id)) busy.set(id, new Map());
  busy.get(id).set(key, label);
  refreshBusy();
  try {
    return await work();
  } finally {
    busy.get(id)?.delete(key);
    if (!busy.get(id)?.size) busy.delete(id);
    refreshBusy();
  }
}

/* Buttons tagged data-busy="<key>" turn into a spinner while that action runs for the open incident, wherever
   they sit (the same action is offered in the progress card and in its own section). */
const busyOriginal = new WeakMap();
function paintBusyButtons() {
  const view = $("#detail-view");
  const keys = busy.get(view.dataset.incident);
  view.querySelectorAll("[data-busy]").forEach((b) => {
    const label = keys?.get(b.dataset.busy);
    if (label && !busyOriginal.has(b)) {
      busyOriginal.set(b, [...b.childNodes]);
      b.disabled = true;
      b.setAttribute("aria-busy", "true");
      fill(b, el("span", { class: "spinner", "aria-hidden": "true" }), b.classList.contains("btn-icon") ? null : `${label}...`);
    } else if (!label && busyOriginal.has(b)) {
      b.replaceChildren(...busyOriginal.get(b));
      busyOriginal.delete(b);
      b.disabled = false;
      b.removeAttribute("aria-busy");
    }
  });
}

let busyTimer = null;
function refreshBusy() {
  const ids = [...new Set([...busy.keys(), ...state.incidents.filter((i) => i.status === "triaging").map((i) => i.id)])];
  // List badges
  document.querySelectorAll("#incident-list .incident[data-id]").forEach((li) => {
    const on = ids.includes(li.dataset.id);
    li.querySelector(".busy-badge")?.remove();
    if (on) li.querySelector(".meta")?.append(el("span", { class: "busy-badge", title: busyLabels(li.dataset.id).join(", ") }, el("span", { class: "spinner", "aria-hidden": "true" }), t("In progress")));
  });
  paintBusyButtons();
  // Banner in the open incident
  const view = $("#detail-view");
  view.querySelector(".busy-banner")?.remove();
  if (!view.hidden && ids.includes(view.dataset.incident)) {
    view.prepend(el("div", { class: "busy-banner", role: "status" }, el("span", { class: "spinner", "aria-hidden": "true" }),
      el("span", {}, el("strong", {}, t("Working in the background: ")), busyLabels(view.dataset.incident).join(", "))));
  }
  // Pulsing ring on the map
  if (!map?.getSource("busy")) return;
  const pts = ids.map((id) => state.incidents.find((i) => i.id === id)).filter(Boolean);
  map.getSource("busy").setData(geo(pts, (i) => ({ id: i.id })));
  if (pts.length && !busyTimer) {
    const t0 = performance.now();
    busyTimer = setInterval(() => {
      const k = ((performance.now() - t0) % 1400) / 1400;
      // Grows from just outside the incident circle (as wide as the selection ring) at every zoom.
      map.setPaintProperty("incident-busy", "circle-radius", ["interpolate", ["linear"], ["zoom"], 4, 10 + 18 * k, 11, 46 + 34 * k]);
      map.setPaintProperty("incident-busy", "circle-stroke-opacity", 0.9 * (1 - k));
    }, 50);
  } else if (!pts.length && busyTimer) {
    clearInterval(busyTimer);
    busyTimer = null;
  }
}

function setMapData() {
  if (!map) return;
  const hs = geo(state.hotspots, (h) => ({ id: h.id, frp: h.frp, conf: h.confidence, at: h.acquired_at, recent: isRecent(h.acquired_at) ? 1 : 0 }));
  const inc = geo(state.incidents, (i) => ({ id: i.id, n: i.hotspot_count, frp: i.max_frp, color: (STATUS[i.status] || STATUS.pending_triage).color, last: i.last_seen }));
  if (map.getSource("hotspots")) {
    map.getSource("hotspots").setData(hs);
    map.getSource("incidents").setData(inc);
    refreshBusy();
    return;
  }
  map.addSource("hotspots", { type: "geojson", data: hs, attribution: "Hotspots: NASA FIRMS" });
  map.addSource("incidents", { type: "geojson", data: inc });

  // Regional view: density weighted by FRP, fading out as individual points take over.
  map.addLayer({
    id: "heatmap", type: "heatmap", source: "hotspots", maxzoom: 10,
    paint: {
      "heatmap-weight": ["interpolate", ["linear"], ["coalesce", ["get", "frp"], 0], 0, 0.15, 40, 1],
      "heatmap-intensity": ["interpolate", ["linear"], ["zoom"], 3, 0.6, 9, 1.6],
      "heatmap-radius": ["interpolate", ["linear"], ["zoom"], 3, 8, 9, 22],
      "heatmap-color": ["interpolate", ["linear"], ["heatmap-density"],
        0, "rgba(255,214,107,0)", 0.2, "rgba(255,214,107,0.35)", 0.45, "rgba(255,171,64,0.5)", 0.7, "rgba(255,122,26,0.6)", 0.9, "rgba(239,68,35,0.68)", 1, "rgba(179,18,46,0.75)"],
      "heatmap-opacity": ["interpolate", ["linear"], ["zoom"], 5, 0.85, 9, 0],
    },
  });
  map.addLayer({
    id: "incidents", type: "circle", source: "incidents",
    paint: {
      "circle-radius": ["interpolate", ["linear"], ["zoom"], 4, ["interpolate", ["linear"], ["get", "n"], 1, 3, 20, 9], 11, ["interpolate", ["linear"], ["get", "n"], 1, 14, 20, 40]],
      "circle-color": ["get", "color"],
      // At regional zoom the heatmap carries the signal; stacked gray fills over orange turn muddy.
      "circle-opacity": ["interpolate", ["linear"], ["zoom"], 6.5, 0, 9, 0.06],
      "circle-stroke-color": ["get", "color"],
      "circle-stroke-width": 1,
      "circle-stroke-opacity": ["interpolate", ["linear"], ["zoom"], 6, 0, 8, 0.85],
    },
  });
  map.addLayer({
    id: "hotspots-pulse", type: "circle", source: "hotspots", minzoom: 5, filter: ["==", ["get", "recent"], 1],
    paint: { "circle-radius": 4, "circle-color": heatExpr("frp"), "circle-opacity": 0, "circle-stroke-width": 0 },
  });
  map.addLayer({
    id: "hotspots", type: "circle", source: "hotspots", minzoom: 5,
    paint: {
      "circle-radius": ["interpolate", ["linear"], ["zoom"], 5, ["interpolate", ["linear"], ["coalesce", ["get", "frp"], 0], 0, 1.5, HEAT_MAX, 4], 12, ["interpolate", ["linear"], ["coalesce", ["get", "frp"], 0], 0, 4, HEAT_MAX, 10]],
      "circle-color": heatExpr("frp"),
      "circle-opacity": ["interpolate", ["linear"], ["zoom"], 5, 0, 6.5, ["match", ["get", "conf"], "l", 0.4, "n", 0.6, "h", 0.8, 0.6]],
      "circle-stroke-color": "#ffffff",
      "circle-stroke-width": 0.5,
      "circle-stroke-opacity": 0.7,
    },
  });
  map.addLayer({
    id: "incident-selected", type: "circle", source: "incidents", filter: ["==", ["get", "id"], state.selected || ""],
    paint: { "circle-radius": ["interpolate", ["linear"], ["zoom"], 4, 10, 11, 46], "circle-color": "rgba(0,0,0,0)", "circle-stroke-color": "#0064e0", "circle-stroke-width": 1.5 },
  });
  map.addLayer({
    id: "incident-picked", type: "circle", source: "incidents", filter: ["in", ["get", "id"], ["literal", [...picked]]],
    paint: { "circle-radius": ["interpolate", ["linear"], ["zoom"], 4, 8, 11, 42], "circle-color": "rgba(0,100,224,0.12)", "circle-stroke-color": "#0064e0", "circle-stroke-width": 2 },
  });

  map.addSource("busy", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
  map.addLayer({ id: "incident-busy", type: "circle", source: "busy",
    paint: { "circle-radius": 12, "circle-color": "rgba(0,0,0,0)", "circle-stroke-color": "#0064e0", "circle-stroke-width": 3, "circle-stroke-opacity": 0.8 } });
  refreshBusy();

  map.on("click", "incidents", (e) => (state.picking ? togglePick(e.features[0].properties.id) : openIncident(e.features[0].properties.id)));
  const hover = (layer, html) => {
    map.on("mousemove", layer, (e) => {
      map.getCanvas().style.cursor = "pointer";
      popup.setLngLat(e.lngLat).setDOMContent(html(e.features[0].properties)).addTo(map);
    });
    map.on("mouseleave", layer, () => { map.getCanvas().style.cursor = ""; popup.remove(); });
  };
  hover("incidents", (p) => el("div", {}, el("div", { class: "pop-id" }, p.id),
    el("div", { class: "pop-meta" }, t("{n} hotspots · max FRP {frp}", { n: p.n, frp: fmtFrp(p.frp === "null" ? null : p.frp) }))));
  hover("hotspots", (p) => el("div", {}, el("div", { class: "pop-meta" }, t("FRP {frp} · confidence {c}", { frp: fmtFrp(p.frp), c: p.conf })), el("div", { class: "pop-meta" }, fmtTime(p.at))));
  animatePulse();
}

// One soft ring per cycle: fade in, ease out while growing, fade to zero, then rest.
// The ring is invisible when the cycle restarts, so there is no jump back to the start size.
const PULSE_MS = 2600;
const PULSE_ACTIVE = 0.72;
const easeOutCubic = (x) => 1 - (1 - x) ** 3;
function pulseFrame(now) {
  const p0 = (now % PULSE_MS) / PULSE_MS;
  if (p0 >= PULSE_ACTIVE) return [3, 0];
  const p = p0 / PULSE_ACTIVE;
  const e = easeOutCubic(p);
  const fadeIn = Math.min(1, p / 0.12);
  return [3 + e * 7, 0.5 * fadeIn * (1 - e) ** 1.4];
}

function animatePulse() {
  if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
  let last = 0;
  const tick = (now) => {
    if (now - last > 40 && map.getLayer("hotspots-pulse")) {
      last = now;
      const [r, o] = pulseFrame(now);
      map.setPaintProperty("hotspots-pulse", "circle-radius", r);
      map.setPaintProperty("hotspots-pulse", "circle-opacity", o);
    }
    requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
}

function setLayerVisible(key, on) {
  state.layers[key] = on;
  const ids = key === "hotspots" ? ["hotspots", "hotspots-pulse"] : NASA_LAYERS.some((L) => L.key === key) ? [`nasa-${key}`] : [key];
  ids.forEach((id) => map?.getLayer(id) && map.setLayoutProperty(id, "visibility", on ? "visible" : "none"));
}

function renderLegend() {
  const sw = (key, label, tip, after) => {
    const b = el("button", { class: "switch", role: "switch", "aria-checked": String(state.layers[key]), "aria-label": label });
    b.onclick = () => { const on = b.getAttribute("aria-checked") !== "true"; b.setAttribute("aria-checked", String(on)); setLayerVisible(key, on); after?.(on); };
    return el("div", { class: "switch-row" }, el("span", { style: "display:flex;gap:4px;align-items:center" }, label, tip ? info(tip, "tip-left") : null), b);
  };
  // NASA color legends are 300 to 400 KB each, so they load only when their layer is switched on.
  const nasaRows = NASA_LAYERS.map((L) => {
    const legendBox = el("div", { class: "nasa-legend", hidden: !state.layers[L.key] || L.key === "truecolor" });
    const showLegend = (on) => {
      legendBox.hidden = !on || L.key === "truecolor";
      if (on && L.key !== "truecolor" && !legendBox.firstChild) legendBox.append(el("img", { src: `https://gibs.earthdata.nasa.gov/legends/${L.id}_H.svg`, alt: t(L.label), loading: "lazy" }));
    };
    return [sw(L.key, t(L.label), t(L.tip), showLegend), legendBox];
  });
  const content = [
    el("div", { class: "legend-label" }, t("Fire radiative power"), info(t("Measured by VIIRS or MODIS in megawatts. Higher FRP means a hotter, more intense fire signal. Opacity follows detection confidence."), "tip-left")),
    el("div", { class: "legend-ramp" }),
    el("div", { class: "legend-scale num" }, ...HEAT_STOPS.map(([v]) => el("span", {}, v === HEAT_MAX ? `${v}+` : String(v)))),
    el("div", { class: "legend-label" }, t("Incident status")),
    el("div", { class: "legend-status" }, ...LEGEND_STATUSES.map((k) => el("span", {}, el("span", { class: "ring", style: `color:${STATUS[k].color}` }), statusLabel(k)))),
    el("div", { class: "legend-label" }, t("Layers")),
    sw("heatmap", t("Heat density")), sw("hotspots", t("Hotspots")), sw("incidents", t("Incidents")),
    el("div", { class: "legend-label" }, t("NASA satellite layers"), info(t("Latest available day from NASA GIBS. Display only: these images are not used as evidence and clouds can hide smoke."), "tip-left")),
    ...nasaRows,
  ];
  // Collapsed, the legend shrinks to its icon so it stops covering the map.
  const s = section({ icon: "layers", title: t("Map legend"), open: window.innerWidth > 820, content });
  s.classList.add("legend", "overlay");
  s.id = "legend";
  $("#legend").replaceWith(s);
}

/* Side panel: hide it to give the map the full width. The toggle icon points where the panel goes. */
function applyPanel(hidden) {
  document.body.classList.toggle("panel-hidden", hidden);
  localStorage.setItem(PANEL_KEY, hidden ? "1" : "");
  const b = $("#panel-toggle");
  fill(b, svg("chevron", `icon icon-sm ${hidden ? "chev-right" : "chev-left"}`));
  b.setAttribute("aria-label", t(hidden ? "Show panel" : "Hide panel"));
  b.title = t(hidden ? "Show panel" : "Hide panel");
  setTimeout(() => map?.resize(), 220);
}

/* Breadcrumb */
function crumbs(parts) {
  $("#crumbs").hidden = parts.length < 2;
  fill($("#crumbs"), parts.map((p, i) => [
    i ? svg("chevron", "icon icon-sm crumb-sep") : null,
    p.onclick ? el("a", { onclick: p.onclick }, p.label) : el("span", { class: "current" }, p.label),
  ]));
}

/* List */
function filtered() {
  let items = state.incidents;
  if (state.q) items = items.filter((i) => i.id.toLowerCase().includes(state.q));
  if (state.filter === "multi") items = items.filter((i) => i.hotspot_count >= 3);
  if (state.filter === "high") items = items.filter((i) => i.high_conf_count > 0);
  if (state.filter === "recent") items = items.filter((i) => isRecent(i.last_seen));
  const key = { heat: (i) => i.max_frp ?? -1, size: (i) => i.hotspot_count, recent: (i) => i.last_seen };
  const k = key[state.sort];
  return [...items].sort((a, b) => (k(b) > k(a) ? 1 : k(b) < k(a) ? -1 : a.id.localeCompare(b.id)));
}

function skeletonCard() {
  return el("li", { class: "card incident skeleton" }, el("span", { class: "sk sk-circle" }), el("div", {}, el("span", { class: "sk sk-title" }), el("span", { class: "sk sk-line" })), el("span", { class: "sk sk-chip" }));
}

function incidentCard(i) {
  const color = heatColor(i.max_frp);
  const act = () => (state.picking ? togglePick(i.id) : openIncident(i.id));
  const on = picked.has(i.id);
  return el("li", { class: `card incident ${state.selected === i.id ? "active" : ""} ${state.picking ? "is-picking" : ""} ${on ? "is-picked" : ""}`, dataset: { id: i.id }, onclick: act, tabindex: "0",
    onkeydown: (e) => (e.key === "Enter" || (state.picking && e.key === " ")) && (e.preventDefault(), act()), "aria-pressed": state.picking ? String(on) : null },
    state.picking ? el("span", { class: "pick-box", "aria-hidden": "true" }, on ? svg("check", "icon icon-sm") : null)
      : el("span", { class: "heat-dot", style: `color:${color};background:${color}22` }, isRecent(i.last_seen) ? el("span", { class: "pulse" }) : el("span", { class: "core" })),
    el("div", {},
      el("div", { class: "id" }, i.id),
      el("div", { class: "meta" },
        el("span", { title: t("Max fire radiative power") }, svg("flame"), fmtFrp(i.max_frp)),
        el("span", { title: t("Hotspots in cluster") }, svg("dots"), fmtNum(i.hotspot_count)),
        el("span", { title: t("Last detection") }, svg("clock"), fmtTime(i.last_seen)),
      ),
    ),
    i.latest_verdict ? el("span", { class: `verdict-pill ${i.latest_verdict}` }, svg(i.latest_verdict === "approve" ? "check" : "x", "icon icon-sm"), t(i.latest_verdict === "approve" ? "Verified" : "Rejected"))
      : el("span", { class: "status", dataset: { status: i.status } }, statusLabel(i.status)),
  );
}

function renderList() {
  stopFlight();
  state.selected = null;
  showBurnMask(null);
  showFootprints(null);
  showFlightPlan(null);
  if (map?.getLayer("incident-selected")) map.setFilter("incident-selected", ["==", ["get", "id"], ""]);
  crumbs([{ label: t("Incidents") }]);
  $("#list-view").hidden = false;
  $("#detail-view").hidden = true;
  const items = filtered();
  $("#incident-count").textContent = items.length === state.incidents.length
    ? t("({n})", { n: fmtNum(items.length) }) : t("({n} of {total})", { n: fmtNum(items.length), total: fmtNum(state.incidents.length) });
  const list = $("#incident-list");
  if (!items.length) {
    fill(list, selectionBar(), el("li", { class: "empty" }, t("No incidents match this filter.")));
    return;
  }
  if (state.picking && picked.size) {
    // Picked incidents stay on top while selecting, even if the area put them beyond the current page.
    const chosen = state.incidents.filter((i) => picked.has(i.id));
    items.splice(0, items.length, ...chosen, ...items.filter((i) => !picked.has(i.id)));
  }
  const shown = items.slice(0, state.shown);
  syncPickedLayer();
  if (state.control) renderControl();
  fill(list,
    selectionBar(),
    shown.map(incidentCard),
    items.length > shown.length ? el("li", {}, el("button", { class: "btn more", onclick: () => { state.shown += PAGE; renderList(); } },
      t("Show {n} more of {rest}", { n: Math.min(PAGE, items.length - shown.length), rest: fmtNum(items.length - shown.length) }))) : null,
  );  refreshBusy();
}

function bindListControls() {
  $("#filters").addEventListener("click", (e) => {
    const b = e.target.closest("[data-filter]");
    if (!b) return;
    state.filter = b.dataset.filter;
    state.shown = PAGE;
    $("#filters").querySelectorAll("[data-filter]").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
    renderList();
  });
  $("#sort").addEventListener("click", (e) => {
    const b = e.target.closest("[data-sort]");
    if (!b) return;
    state.sort = b.dataset.sort;
    $("#sort").querySelectorAll("[data-sort]").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
    renderList();
  });
  $("#search").addEventListener("input", (e) => { state.q = e.target.value.trim().toLowerCase(); state.shown = PAGE; renderList(); });
}

/* Detail */
function renderDetailSkeleton() {
  fill($("#detail-view"),
    el("div", { class: "card detail-head" }, el("span", { class: "sk sk-title" }), el("span", { class: "sk sk-line" }), el("span", { class: "sk sk-block" })),
    Array.from({ length: 4 }, () => el("div", { class: "card", style: "padding:14px" }, el("span", { class: "sk sk-title" }))),
  );
}

function verifierContent(inc) {
  if (!token()) {
    return [el("p", { class: "empty" }, t("Approving or rejecting needs verifier mode.")),
      el("div", { class: "actions" }, el("button", { class: "btn btn-primary", type: "button", onclick: () => openSignIn(t) }, svg("shield", "icon icon-sm"), t("Verifier sign in")))];
  }
  const standing = activeVerdict(inc);
  if (standing) {
    const undo = el("button", { class: "btn", type: "button", onclick: () => revokeDialog(inc, standing) }, svg("reset", "icon icon-sm"), t("Cancel this decision"));
    return [el("div", { class: `phead ${standing.decision === "approve" ? "ok" : "bad"}` }, svg(standing.decision === "approve" ? "check" : "x", "icon"),
      el("div", {}, el("strong", {}, t(standing.decision === "approve" ? "Approved" : "Rejected")), el("span", {}, t("Decided on {when}.", { when: fmtTime(standing.created_at) })),
        standing.note ? el("span", {}, `"${standing.note}"`) : null)),
      el("div", { class: "actions" }, undo)];
  }
  const approve = el("button", { class: "btn btn-primary" }, svg("check", "icon icon-sm"), t("Approve"));
  const reject = el("button", { class: "btn btn-ghost-danger" }, svg("x", "icon icon-sm"), t("Reject"));
  if (inc.status === "triaging") {
    // Deciding while the AI is still writing would judge a brief nobody has read yet.
    [approve, reject].forEach((b) => { b.setAttribute("aria-disabled", "true"); b.onclick = () => toast(t("Wait for the triage to finish, then decide."), "alert"); });
    return [el("p", { class: "muted", style: "margin:0;font-size:12px" }, t("AI triage is running. The decision opens when it finishes.")), el("div", { class: "actions" }, approve, reject)];
  }
  const decide = async (decision) => {
    [approve, reject].forEach((b) => (b.disabled = true));
    try {
      await api(`/api/incidents/${inc.id}/verdict`, { method: "POST", body: JSON.stringify({ decision }) });
      toast(t(decision === "approve" ? "Approved and recorded." : "Rejected and recorded."));
      openIncident(inc.id);
    } catch (e) {
      toast(t("Not recorded: {e}", { e: e.message }), "alert");
      [approve, reject].forEach((b) => (b.disabled = false));
    }
  };
  approve.onclick = () => decide("approve");
  reject.onclick = () => decide("reject");
  return [el("div", { class: "actions" }, approve, reject)];
}

/** The decision that stands: the latest verdict, unless it was revoked. */
function activeVerdict(inc) {
  const last = inc.verdicts.at(-1);
  return last && last.decision !== "revoke" ? last : null;
}

function revokeDialog(inc, standing) {
  const note = el("input", { class: "input", maxlength: 500, placeholder: t("Reason (optional)"), "aria-label": t("Reason (optional)") });
  const err = el("p", { class: "form-error", hidden: true });
  const ok = el("button", { class: "btn btn-primary", type: "button" }, svg("reset", "icon icon-sm"), t("Cancel this decision"));
  const dlg = modal({
    title: t(standing.decision === "approve" ? "Cancel the approval?" : "Cancel the rejection?"),
    body: [el("p", { style: "margin:0" }, t(standing.decision === "approve"
      ? "The incident goes back to waiting for a verifier. If it rests on citizen reports only, it leaves the public map until it is approved again. The original approval stays in the history."
      : "The incident goes back to waiting for a verifier. The original rejection stays in the history.")), note, err],
    actions: [el("button", { class: "btn", type: "button", onclick: () => dlg.close() }, t("Keep it")), ok],
  });
  ok.onclick = async () => {
    ok.disabled = true;
    try {
      await api(`/api/incidents/${inc.id}/verdict`, { method: "POST", body: JSON.stringify({ decision: "revoke", note: note.value.trim() || null }) });
      dlg.close();
      toast(t("Decision cancelled. The incident waits for a verifier again."));
      openIncident(inc.id);
      api("/api/incidents").then((r) => { state.incidents = r.items; setMapData(); }).catch(() => {});
    } catch (e) {
      err.hidden = false;
      fill(err, svg("alert", "icon icon-sm"), el("span", {}, e.message));
      ok.disabled = false;
    }
  };
}

/* Evidence rows. Citizen media is fetched with the verifier token, so it never sits at a public URL. */
const VERDICT_LABEL = { approve: "approved", reject: "rejected", revoke: "cancelled" };
const TRIGGER_LABEL = { hotspot_cluster: "hotspot cluster", citizen_report: "citizen report", merged: "hotspots + citizen report" };

async function mediaUrl(mediaId) {
  const res = await fetch(`/api/media/${encodeURIComponent(mediaId)}`, { headers: authHeaders() });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return URL.createObjectURL(await res.blob());
}

function photoTile(mediaId, alt) {
  const img = el("img", { alt, loading: "lazy" });
  mediaUrl(mediaId).then((u) => (img.src = u)).catch(() => img.replaceWith(el("span", { class: "media-locked" }, svg("alert", "icon icon-sm"), t("Photo unavailable"))));
  return img;
}

function voiceButton(mediaId) {
  let audio;
  const label = (playing) => [svg(playing ? "stop" : "play", "icon icon-sm"), t(playing ? "Stop" : "Play voice note")];
  const b = el("button", { class: "btn", type: "button" }, label(false));
  b.onclick = async () => {
    try {
      if (!audio) audio = new Audio(await mediaUrl(mediaId));
      if (audio.paused) { await audio.play(); fill(b, label(true)); audio.onended = () => fill(b, label(false)); }
      else { audio.pause(); audio.currentTime = 0; fill(b, label(false)); }
    } catch (e) {
      toast(t("Voice note unavailable: {e}", { e: e.message }), "alert");
    }
  };
  return b;
}

/** The burned area of the newest Sentinel-2 pair, for the section title (the gist without opening it). */
function burnedGist(inc) {
  const ev = [...inc.evidence].reverse().find((e) => e.type === "satellite");
  if (!ev) return null;
  return ev.payload.burned_area_ha == null ? t("not claimed") : `${fmtNum(ev.payload.burned_area_ha)} ha`;
}

/* Text written by the model comes in English and, since 2026-10-29, with an Indonesian twin (field_id).
   In Indonesian mode the twin is shown; an older triage has none, so its English is shown with an EN mark. */
const enMark = () => el("span", { class: "lang-mark", title: t("Written by the model in English (triage from before Indonesian output)") }, "EN");
function modelText(en, id) {
  if (lang() === "id" && id) return el("span", {}, id);
  return lang() === "id" && en ? el("span", { lang: "en" }, enMark(), " ", en) : el("span", {}, en || "");
}
/** A model list (drivers, actions, uncertainties) in the reader's language: the twin list when it has the same length. */
function modelList(en = [], id = []) {
  const twin = lang() === "id" && id?.length === en.length;
  return en.map((x, i) => (twin ? el("span", {}, id[i]) : modelText(x, null)));
}

/* Evidence lines are rebuilt from the measured payload in the reader's language. The stored English summary is
   the fallback for anything this code does not know. */
const EV_TYPE = { hotspot: "Hotspots", weather: "Weather", osm: "Nearby places", satellite: "Sentinel-2", report: "Citizen report", image: "Citizen photo", voice: "Voice note" };
const CONF_WORD = { h: "high", n: "nominal", l: "low" };
const OSM_KIND = { settlement: "settlement", road: "road", river: "river", lake: "lake or pond", fire_station: "fire station" };
const km = (m) => `${fmtNum(Math.round(m / 100) / 10)} km`;
const day = (iso) => (iso ? String(iso).slice(0, 10) : "?");

function evidenceText(e) {
  const p = e.payload || {};
  try {
    if (e.type === "hotspot" && p.count != null) {
      const conf = Object.entries(p.confidence_counts || {}).map(([k, n]) => `${n} ${t(CONF_WORD[k] || k)}`).join(", ");
      return t("{n} FIRMS hotspot(s), max FRP {frp} MW, confidence: {conf}", { n: p.count, frp: fmtNum(p.max_frp_mw), conf: conf || t("n/a") });
    }
    if (e.type === "weather" && p.wind_speed_ms != null) {
      return t("Wind {v} m/s from {d} deg, humidity {h}%, rain 24 h {r} mm", { v: fmtNum(p.wind_speed_ms), d: p.wind_direction_deg ?? "?", h: p.relative_humidity_pct ?? "?", r: fmtNum(p.rain_24h_mm) });
    }
    if (e.type === "osm" && p.nearest && typeof p.nearest === "object") {
      const r = p.search_radius_m || 10000;
      const parts = Object.entries(OSM_KIND).map(([k, label]) => {
        const x = p.nearest[k];
        const within = k === "fire_station" ? (p.fire_station_search_radius_m || 30000) : r;
        return x ? `${t(label)} ${km(x.distance_m)}${x.name ? ` (${x.name})` : ""}` : t("no {what} within {d}", { what: t(label), d: km(within) });
      });
      return `${t("Nearest")}: ${parts.join(", ")}`;
    }
    if (e.type === "satellite" && p.pre && p.post) {
      const when = t("before {a} and after {b}", { a: day(p.pre.datetime), b: day(p.post.datetime) });
      if (p.status === "hazy") return t("Sentinel-2 dNBR, {when}: heavy haze (aerosol optical thickness {aot}); smoke lowers NIR, so dNBR may overstate the burn and no hectares are claimed", { when, aot: fmtNum(p.post.aot_median) });
      if (p.status === "ok") return t("Sentinel-2 dNBR, {when}: {ha} ha at dNBR of {thr} or more within {clear} ha of clear ground; {low} ha more at low severity", {
        when, ha: fmtNum(p.burned_area_ha), thr: fmtNum(p.dnbr_threshold), clear: fmtNum(p.clear_area_ha), low: fmtNum(p.low_severity_area_ha) });
    }
    if (e.type === "satellite" && p.status === "no_post_image") return t("Sentinel-2: no clear scene of this area since the first detection yet (a pass comes about every 5 days)");
    if (e.type === "satellite" && p.status === "no_pre_image") return t("Sentinel-2: no clear scene from before the first detection to compare with");
    if (e.type === "image" && p.width) return t("photo {w} x {h} px, metadata removed", { w: p.width, h: p.height });
    if (e.type === "voice") return t("voice note");
  } catch { /* fall back to the stored summary */ }
  return lang() === "id" ? [enMark(), " ", e.summary] : e.summary;
}

function evidenceSource(e) {
  const p = e.payload || {};
  if (["report", "image", "voice"].includes(e.type)) return p.channel === "whatsapp" ? "WhatsApp" : t("web form");
  if (e.type === "osm") return p.offline_source ? `OpenStreetMap, ${t("offline copy {src}", { src: p.offline_source })}` : "OpenStreetMap";
  return e.source;
}

/* The gate's reasons are fixed sentences written by code, so they can be shown in either language. */
const GATE_REASON = {
  "no evidence": "no evidence", "content rule": "content rule", "no matching tool value": "no matching tool value", "no real mask cited": "no real mask cited",
  "claims without valid evidence were removed": "claims without valid evidence were removed",
  "location outside Indonesia or more than 5 km from cited hotspot or report": "location outside Indonesia or more than 5 km from cited hotspot or report",
};
const RULE_LABEL = { medical: "health advice", cause: "naming a cause", party: "naming a person or party" };
const gateReason = (r) => {
  const s = String(r || "");
  if (GATE_REASON[s]) return t(GATE_REASON[s]);
  if (s.startsWith("unknown evidence")) return t("unknown evidence {ids}", { ids: s.replace("unknown evidence", "").trim() });
  const m = s.match(/^likely_fire needs independent evidence.*?([\d.]+) ha\)$/);
  if (m) return t("likely fire needs independent evidence (photo, voice, or a satellite burn of at least {ha} ha)", { ha: m[1] });
  return s;
};
function statusChange(d) {
  const m = String(d).match(/^(\w+) -> (\w+): (.*)$/);
  return m ? `${statusLabel(m[1])} → ${statusLabel(m[2])}: ${gateReason(m[3])}` : d;
}

function evidenceRows(inc) {
  const rows = inc.evidence.map((e) => {
    const extra = [];
    if (e.type === "report") extra.push(fold(el("p", { class: "brief", style: "margin-top:4px" }, e.payload.text), { lines: 3 }));
    if (e.type === "voice") extra.push(el("div", { style: "margin-top:6px" }, voiceButton(e.payload.media_id)),
      fold(el("p", { class: "muted", style: "margin:4px 0 0;font-size:12px" }, e.payload.transcript ? [aiBadge("Muse Voice Transcribe"), " ", t("Transcript: {t}", { t: e.payload.transcript })] : t("Transcript pending"))));
    // The report summary repeats the citizen's text, so show the full text once instead.
    const head = e.type === "report" ? `${t(EV_TYPE[e.type])} · ${evidenceSource(e)}`
      : el("span", {}, `${t(EV_TYPE[e.type] || e.type)} · ${evidenceSource(e)}: `, evidenceText(e));
    return el("div", { class: "ev", dataset: { ev: e.id } }, el("span", { class: "ev-id" }, e.id), el("div", { style: "flex:1;min-width:0" }, fold(head), extra));
  });
  const photos = inc.evidence.filter((e) => e.type === "image" && e.payload.media_id);
  if (photos.length) rows.push(el("div", { class: "media-grid" }, photos.map((e) => photoTile(e.payload.media_id, `${e.id} ${t("citizen photo")}`))));
  // A source that failed is shown as unknown with its reason. Silence would read as "nothing found".
  const st = inc.enrichment || {};
  for (const [src, v] of Object.entries(st)) {
    if (String(v).startsWith("failed")) rows.push(el("div", { class: "ev" }, el("span", { class: "ev-id", style: "color:var(--warn)" }, t("UNKNOWN")),
      el("span", {}, t("{src}: not collected yet ({why}). Retried automatically.", { src, why: String(v).replace(/^failed:\s*/, "").slice(0, 120) }))));
  }
  if (!rows.length) rows.push(el("p", { class: "empty" }, t(inc.enriched_at ? "No evidence collected." : "Not collected yet. Small clusters are collected on request.")));
  return rows;
}

/* Sentinel-2: before and after images, the burn scar, and the numbers the code measured. */
const satUrl = (inc, name) => `/api/satellite/${encodeURIComponent(inc.id)}/${encodeURIComponent(name)}`;

function showBurnMask(inc, ev) {
  // The detail view can open before the map has loaded; draw once it has.
  state.mapReady?.then(() => drawBurnMask(inc, ev));
}

function drawBurnMask(inc, ev) {
  if (!map) return;
  if (map.getLayer("burn-mask")) map.removeLayer("burn-mask");
  if (map.getSource("burn-mask")) map.removeSource("burn-mask");
  const p = ev?.payload;
  if (!inc || !p?.images?.mask || !p.image_corners) return;
  map.addSource("burn-mask", { type: "image", url: satUrl(inc, p.images.mask), coordinates: p.image_corners });
  // Under the hotspots so the detections stay readable on top of the scar.
  map.addLayer({ id: "burn-mask", type: "raster", source: "burn-mask", paint: { "raster-opacity": 0.8, "raster-fade-duration": 200 } }, map.getLayer("hotspots") ? "hotspots" : undefined);
}

function satelliteContent(inc) {
  const ev = [...inc.evidence].reverse().find((e) => e.type === "satellite");
  const fetchBtn = token() ? (() => {
    const b = el("button", { class: "btn", type: "button", dataset: { busy: "satellite" } }, svg("satellite", "icon icon-sm"), t(ev ? "Check for a newer pair" : "Find Sentinel-2 images"));
    b.onclick = () => fetchSatellite(inc.id);
    return el("div", { class: "actions" }, b);
  })() : null;
  if (!ev) return [el("p", { class: "empty" }, t("No Sentinel-2 pair yet. Clouds often cover the area; a pass comes about every 5 days.")), fetchBtn];
  const p = ev.payload;
  const views = [["overlay", t("Burn scar")], ["post", t("After")], ["pre", t("Before")]].filter(([k]) => p.images?.[k]);
  const img = el("img", { class: "sat-img", alt: t("Sentinel-2 image"), loading: "lazy" });
  const seg = el("div", { class: "segmented", role: "group", "aria-label": t("Sentinel-2 view") });
  const pick = (k) => {
    img.src = satUrl(inc, p.images[k]);
    seg.querySelectorAll("button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.k === k)));
  };
  views.forEach(([k, label]) => seg.append(el("button", { type: "button", dataset: { k }, "aria-pressed": "false", onclick: () => pick(k) }, label)));
  if (views.length) pick(views[0][0]);
  const scene = (s) => `${fmtDate(s.datetime)} · ${t("{pct}% clear", { pct: Math.round(s.aoi_clear_frac * 100) })} · AOT ${s.aot_median}`;
  const ha = (v) => (v == null ? t("not claimed") : `${fmtNum(v)} ha`);
  const showOnMap = mapToggle("scar", "Show scar on map", () => { showBurnMask(inc, ev); map?.fitBounds([p.image_corners[3], p.image_corners[1]], { padding: 60, duration: 800 }); }, () => showBurnMask(null));
  return [
    el("div", { class: "sat-head" }, el("span", { class: "ev-id" }, ev.id), seg),
    views.length ? img : null,
    p.status === "hazy" ? el("span", { class: "dispute" }, svg("alert", "icon icon-sm"), t("Heavy haze: no hectares are claimed from this pair.")) : null,
    el("dl", { class: "kv" },
      el("dt", {}, t("Burned area")), el("dd", {}, el("strong", { class: "num" }, ha(p.burned_area_ha)), " ", el("span", { class: "muted" }, `dNBR ≥ ${fmtNum(p.dnbr_threshold)}`)),
      el("dt", {}, t("Low severity")), el("dd", { class: "num" }, ha(p.low_severity_area_ha)),
      el("dt", {}, t("Clear ground")), el("dd", { class: "num" }, `${fmtNum(p.clear_area_ha)} ha`),
      el("dt", {}, t("Before")), el("dd", {}, scene(p.pre)),
      el("dt", {}, t("After")), el("dd", {}, scene(p.post)),
    ),
    el("p", { class: "muted", style: "margin:0;font-size:11px" }, p.attribution || "Contains modified Copernicus Sentinel data"),
    el("div", { class: "actions" }, p.images?.mask ? showOnMap : null, fetchBtn?.firstChild || null),
  ];
}

/* A map overlay button shows its layer, then offers to hide it. Buttons of one group share the layer, so showing one
   resets the others. */
function mapToggle(group, label, show, hide) {
  const b = el("button", { class: "btn", type: "button", dataset: { mapToggle: group }, "aria-pressed": "false" });
  const paint = (on) => { fill(b, svg("layers", "icon icon-sm"), t(on ? "Hide from map" : label)); b.setAttribute("aria-pressed", String(on)); };
  paint(false);
  b.onclick = () => {
    const on = b.getAttribute("aria-pressed") !== "true";
    document.querySelectorAll(`[data-map-toggle="${group}"]`).forEach((x) => x !== b && x.getAttribute("aria-pressed") === "true" && x.dispatchEvent(new CustomEvent("reset")));
    (on ? show : hide)();
    paint(on);
  };
  b.addEventListener("reset", () => paint(false));
  return b;
}

/* Drone survey proposals: a plan from the agent or a verifier, flown only after a human approves. */
const PROPOSAL_LABEL = { proposed: "Proposed", approved: "Approved", rejected: "Rejected", cancelled: "Cancelled" };
const EXAMPLE_SURVEY = "INC-20261025-0407";  // the incident with a simulated survey (frames, Glimmer, SAM)

/* Simulated flight (SIMULASI). The server simulates telemetry for an approved plan once (backend/drone_flight.py)
   and keeps a sim clock; the browser places the drone at the sim time on the map. A route preview uses the same
   drawing with a local clock. No drone is connected in this build. */
const PREVIEW_S = 24;
const REPLAY_3D_S = 75;  // a whole flight in about a minute in the 3D view
const DRONE_SCALE_3D = 30;  // as drawn in flight3d.js
const FLY_LAYERS = ["drone-fly-foot-fill", "drone-fly-foot-line", "drone-fly-shots", "drone-fly-pos"];
let flight = null;  // { timer, onStop }

function stopFlight() {
  if (!flight) return;
  clearInterval(flight.timer);
  const done = flight.onStop;
  flight = null;
  done?.();
  if (!map) return;
  FLY_LAYERS.forEach((id) => map.getLayer(id) && map.removeLayer(id));
  ["drone-fly", "drone-fly-shots"].forEach((id) => map.getSource(id) && map.removeSource(id));
}

const metres = (a, b) => [(b.lon - a.lon) * 111320 * Math.cos((a.lat * Math.PI) / 180), (b.lat - a.lat) * 111320];

/* Where the drone is at sim second s: between two frames it flies the leg; before leaving a frame after a
   battery swap it waits on the ground. */
function telemetryAt(tel, s) {
  const S = tel.samples;
  const shots = S.filter((x) => x.t_s <= s).length;
  const i = S.findIndex((x) => x.t_s > s);
  if (i === -1) { const z = S[S.length - 1]; return { ...z, shots, k: 1, a: S[S.length - 2] || z, b: z, done: true, swapping: false }; }
  if (i === 0) return { ...S[0], shots, k: 0, a: S[0], b: S[1] || S[0], done: false, swapping: false };
  const a = S[i - 1], b = S[i];
  const fly = Math.hypot(...metres(a, b)) / b.ground_speed_ms;
  const depart = b.t_s - fly - 2;  // 2 s per frame for the camera, as in the planner
  const k = s <= depart ? 0 : Math.min(1, (s - depart) / fly);
  const swapping = s < depart && b.pack !== a.pack;
  return { lat: a.lat + (b.lat - a.lat) * k, lon: a.lon + (b.lon - a.lon) * k, a, b, k, shots, done: false, swapping,
    alt_m: swapping ? 0 : b.alt_m, ground_speed_ms: s < depart ? 0 : b.ground_speed_ms, pack: swapping ? b.pack : a.pack,
    battery_pct: swapping ? a.battery_pct : a.battery_pct + (b.battery_pct - a.battery_pct) * (b.pack === a.pack ? k : 0) };
}

function drawDrone(plan, st) {
  if (!map) return;
  if (!map.getSource("drone-fly")) {
    const empty = { type: "FeatureCollection", features: [] };
    map.addSource("drone-fly", { type: "geojson", data: empty });
    map.addSource("drone-fly-shots", { type: "geojson", data: empty });
    map.addLayer({ id: "drone-fly-shots", type: "circle", source: "drone-fly-shots", paint: { "circle-radius": 3, "circle-color": "#7a5af8", "circle-stroke-color": "#fff", "circle-stroke-width": 1 } });
    map.addLayer({ id: "drone-fly-foot-fill", type: "fill", source: "drone-fly", filter: ["==", ["get", "kind"], "foot"], paint: { "fill-color": "#7a5af8", "fill-opacity": 0.18 } });
    map.addLayer({ id: "drone-fly-foot-line", type: "line", source: "drone-fly", filter: ["==", ["get", "kind"], "foot"], paint: { "line-color": "#5b3fd6", "line-width": 1.5 } });
    map.addLayer({ id: "drone-fly-pos", type: "circle", source: "drone-fly", filter: ["==", ["get", "kind"], "pos"], paint: { "circle-radius": 7, "circle-color": ["case", ["get", "ground"], "#f79009", "#5b3fd6"], "circle-stroke-color": "#fff", "circle-stroke-width": 2 } });
  }
  const [fw, fh] = plan.footprint_m;  // across track, along track
  const [vx, vy] = metres(st.a, st.b);
  const len = Math.hypot(vx, vy) || 1;
  const ux = vx / len, uy = vy / len;
  const toDeg = (x, y) => [st.lon + x / (111320 * Math.cos((st.lat * Math.PI) / 180)), st.lat + y / 111320];
  const corner = (sa, sb) => toDeg(ux * sa * fh / 2 + uy * sb * fw / 2, uy * sa * fh / 2 - ux * sb * fw / 2);
  const features = [{ type: "Feature", properties: { kind: "pos", ground: !!st.swapping }, geometry: { type: "Point", coordinates: [st.lon, st.lat] } }];
  if (!st.swapping) features.push({ type: "Feature", properties: { kind: "foot" }, geometry: { type: "Polygon", coordinates: [[corner(1, 1), corner(1, -1), corner(-1, -1), corner(-1, 1), corner(1, 1)]] } });
  map.getSource("drone-fly")?.setData({ type: "FeatureCollection", features });
  map.getSource("drone-fly-shots")?.setData({ type: "FeatureCollection", features: plan.waypoints.slice(0, st.shots).map((w) =>
    ({ type: "Feature", properties: {}, geometry: { type: "Point", coordinates: [w.lon, w.lat] } })) });
}

/* Runs a flight on the map. clock() returns sim seconds; tick(state) updates the console. */
function runFlight(p, tel, clock, tick, onStop, endStops = false) {
  stopFlight();
  showFlightPlan(p);
  // The run exists at once, so Stop works even before the map has loaded; drawing starts when it has.
  const run = { timer: null, onStop, mapOk: false };
  flight = run;
  state.mapReady?.then(() => { run.mapOk = true; });
  const step = () => {
    if (flight !== run) return;
    const s = Math.min(tel.duration_s, clock());
    const st = telemetryAt(tel, s);
    if (run.mapOk) drawDrone(p.plan, st);
    tick?.(st, s);
    if (s >= tel.duration_s) {
      clearInterval(run.timer);
      run.timer = null;
      if (endStops) setTimeout(() => flight === run && stopFlight(), 1500);  // a preview or replay leaves the map clean
    }
  };
  run.timer = setInterval(step, 120);
  step();
}

const mmss = (s) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
const windText = (w) => (w?.from_deg == null ? t("calm (no weather evidence)") : t("{v} m/s from {d} deg", { v: fmtNum(w.speed_ms), d: Math.round(w.from_deg) }));

function flightConsole() {
  const bar = el("span", { class: "flight-bar-fill" });
  const cells = {};
  const cell = (k, label) => { cells[k] = el("dd", { class: "num" }, "-"); return [el("dt", {}, t(label)), cells[k]]; };
  const node = el("div", { class: "flight-console" },
    el("div", { class: "flight-head" }, el("span", { class: "pill pill-sim" }, "SIMULASI"), el("strong", { class: "flight-state" }), el("span", { class: "muted flight-clock num" })),
    el("div", { class: "flight-bar" }, bar),
    el("dl", { class: "kv flight-kv" }, cell("frame", "Frame"), cell("battery", "Battery"), cell("speed", "Ground speed"), cell("alt", "Altitude"), cell("wind", "Wind")));
  const update = (tel, st, s, label) => {
    bar.style.width = `${Math.round((s / tel.duration_s) * 100)}%`;
    node.querySelector(".flight-state").textContent = label;
    node.querySelector(".flight-clock").textContent = `${mmss(s)} / ${mmss(tel.duration_s)}`;
    cells.frame.textContent = `${st.shots} / ${tel.samples.length}`;
    cells.battery.textContent = `${Math.round(st.battery_pct)}% · ${t("pack {n} of {m}", { n: st.pack, m: tel.packs })}`;
    cells.speed.textContent = `${fmtNum(st.ground_speed_ms)} m/s`;
    cells.alt.textContent = `${Math.round(st.alt_m)} m`;
    cells.wind.textContent = windText(tel.wind);
  };
  return { node, update };
}

function startFlightDialog(inc, p) {
  const plan = p.plan;
  const weather = [...inc.evidence].reverse().find((e) => e.type === "weather")?.payload;
  let speed = 30;
  const seg = el("div", { class: "segmented", role: "group", "aria-label": t("Playback speed") }, [10, 30, 60].map((v) =>
    el("button", { type: "button", "aria-pressed": String(v === speed), onclick: (e) => { speed = v; seg.querySelectorAll("button").forEach((b) => b.setAttribute("aria-pressed", String(b === e.currentTarget))); } }, `x${v}`)));
  const err = el("p", { class: "form-error", hidden: true });
  const ok = el("button", { class: "btn btn-primary", type: "button" }, svg("play", "icon icon-sm"), t("Take off"));
  const dlg = modal({
    title: t("Pre-flight check (simulated pilot)"),
    body: [
      el("ul", { class: "preflight" },
        el("li", {}, svg("check", "icon icon-sm"), t("Plan approved by a verifier")),
        el("li", {}, svg("check", "icon icon-sm"), t("{lines} lines x {n} frames, {alt} m above ground, GSD {gsd} m", { lines: plan.lines, n: plan.frames_per_line, alt: plan.altitude_agl_m, gsd: fmtNum(plan.gsd_m) })),
        el("li", {}, svg("check", "icon icon-sm"), t("Wind from the incident's weather evidence: {w}", { w: windText(weather ? { speed_ms: weather.wind_speed_ms, from_deg: weather.wind_direction_deg } : null) })),
        el("li", {}, svg("check", "icon icon-sm"), t("about {min} min, {km} km path, {b} battery(ies)", { min: fmtNum(plan.estimate.flight_minutes), km: fmtNum(plan.estimate.path_km), b: plan.estimate.batteries }))),
      el("div", { class: "admin-setting-head" }, el("span", { class: "admin-label" }, t("Playback speed")), seg),
      el("p", { class: "muted", style: "margin:0;font-size:12px" }, t("No real drone is connected. The flight is simulated: telemetry is computed from the plan and the wind, and everyone who opens this incident sees the same flight.")), err],
    actions: [el("button", { class: "btn", type: "button", onclick: () => dlg.close() }, t("Cancel")), ok],
  });
  ok.onclick = async () => {
    ok.disabled = true;
    try {
      await api(`/api/drone-proposals/${p.id}/flight`, { method: "POST", body: JSON.stringify({ speed }) });
      dlg.close();
      toast(t("Take-off. The simulated flight is on the map."), "check");
      openIncident(inc.id);
    } catch (e) {
      err.hidden = false;
      fill(err, svg("alert", "icon icon-sm"), el("span", {}, e.message));
      ok.disabled = false;
    }
  };
}

function finishFlight(inc, f, outcome) {
  const note = el("input", { class: "input", maxlength: 300, placeholder: t("Note (optional)"), "aria-label": t("Note (optional)") });
  const err = el("p", { class: "form-error", hidden: true });
  const ok = el("button", { class: `btn ${outcome === "completed" ? "btn-primary" : "btn-ghost-danger"}`, type: "button" }, t(outcome === "completed" ? "Land and finish" : "Abort flight"));
  const dlg = modal({
    title: t(outcome === "completed" ? "Land and finish the flight?" : "Abort this flight?"),
    body: [el("p", { style: "margin:0" }, t(outcome === "completed" ? "The drone lands and the flight is recorded as completed." : "The drone returns and the flight is recorded as aborted. The plan can be flown again.")), note, err],
    actions: [el("button", { class: "btn", type: "button", onclick: () => dlg.close() }, t("Keep flying")), ok],
  });
  ok.onclick = async () => {
    ok.disabled = true;
    try {
      await api(`/api/drone-flights/${f.id}/finish`, { method: "POST", body: JSON.stringify({ outcome, note: note.value.trim() || null }) });
      dlg.close();
      stopFlight();
      toast(t(outcome === "completed" ? "Flight completed and recorded." : "Flight aborted and recorded."), "check");
      openIncident(inc.id);
    } catch (e) {
      err.hidden = false;
      fill(err, svg("alert", "icon icon-sm"), el("span", {}, e.message));
      ok.disabled = false;
    }
  };
}

/* Data console over the 3D view, written like a CLI log. Plain lines are computed: plan, simulated telemetry,
   and the incident's weather evidence. Lines starting with "~" illustrate a real drone's data path (onboard
   filter, downlink to the ground station); no drone is connected, so they carry no measured data. */
const CONSOLE_LINES = 80;
const CONSOLE_GPS_EVERY_MS = 1500;
const pad2 = (n) => String(n).padStart(2, "0");

function dataConsole(p, tel) {
  const plan = p.plan;
  const total = tel.samples.length;
  const log = el("div", { class: "f3d-log", role: "log", "aria-live": "off" });
  const node = el("div", { class: "f3d-console" },
    el("div", { class: "f3d-console-head" }, el("span", { class: "f3d-console-dot" }), el("strong", {}, t("DATA CONSOLE")), el("span", { class: "f3d-console-sim" }, "SIMULASI"),
      info(t("Plain lines are computed from the flight plan, the simulated telemetry, and the incident's weather evidence. Lines starting with ~ only illustrate how a real drone would filter and send its frames; no drone is connected."), "tip-right")),
    log);
  const st0 = { shots: -1, pack: null, swapping: false, done: false, gpsAt: 0, started: false };
  const write = (s, tag, text, ill = false) => {
    log.append(el("div", { class: `f3d-line ${ill ? "ill" : ""}` }, el("span", { class: "f3d-t" }, mmss(s)), el("span", { class: `f3d-tag tag-${tag.toLowerCase()}` }, (ill ? "~" : "") + tag), el("span", {}, text)));
    while (log.childElementCount > CONSOLE_LINES) log.firstChild.remove();
    log.scrollTop = log.scrollHeight;
  };
  const bearing = (a, b) => {
    const dx = (b.lon - a.lon) * Math.cos((a.lat * Math.PI) / 180), dy = b.lat - a.lat;
    return Math.round(((Math.atan2(dx, dy) * 180) / Math.PI + 360) % 360);
  };
  const update = (st, s) => {
    if (!st0.started) {
      st0.started = true;
      write(s, "SYS", t("autopilot armed · plan #{id} · {lines} lines x {n} frames · {alt} m AGL", { id: p.id, lines: plan.lines, n: plan.frames_per_line, alt: plan.altitude_agl_m }));
      write(s, "WX", `${windText(tel.wind)}${tel.wind?.evidence_id ? ` (${String(tel.wind.evidence_id).split("/").pop()})` : ""}`);
      write(s, "BATT", t("{n} pack(s) planned · swap at 20%", { n: tel.packs }));
      st0.shots = st.shots;
    }
    if (st.pack !== st0.pack && st0.pack != null) write(s, "BATT", t("pack {n} of {m} in · back to the route", { n: st.pack, m: tel.packs }));
    st0.pack = st.pack;
    if (st.swapping && !st0.swapping) write(s, "BATT", t("battery low · returning to swap packs, about 5 min on the ground"));
    st0.swapping = st.swapping;
    const fresh = st.shots - st0.shots;
    if (fresh > 4) {
      write(s, "CAM", t("frames {a}-{b} of {n} captured", { a: pad2(st0.shots + 1), b: pad2(st.shots), n: total }));
    } else {
      for (let k = st0.shots + 1; k <= st.shots; k++) {
        write(s, "CAM", t("frame {k}/{n} · GSD {gsd} m · footprint {w} x {h} m", { k: pad2(k), n: total, gsd: fmtNum(plan.gsd_m), w: Math.round(plan.footprint_m[0]), h: Math.round(plan.footprint_m[1]) }));
        write(s, "EDGE", t("frame {k} → onboard filter → queued", { k: pad2(k) }), true);
        write(s, "LINK", t("frame {k} → ground station", { k: pad2(k) }), true);
      }
    }
    st0.shots = st.shots;
    const now = performance.now();
    const fix = `${st.lat.toFixed(5)},${st.lon.toFixed(5)}`;
    if (!st.swapping && !st.done && now - st0.gpsAt > CONSOLE_GPS_EVERY_MS && fix !== st0.fix) {
      st0.gpsAt = now;
      st0.fix = fix;
      write(s, "GPS", `${st.lat.toFixed(5)}, ${st.lon.toFixed(5)} · ALT ${Math.round(st.alt_m)} m · GS ${fmtNum(st.ground_speed_ms)} m/s · HDG ${pad2(bearing(st.a, st.b))}° · BAT ${Math.round(st.battery_pct)}%`);
    }
    if (st.done && !st0.done) write(s, "SYS", t("route complete · {n} frames · landing", { n: total }));
    st0.done = st.done;
  };
  return { node, update };
}

/* 3D view of a flight: the same telemetry and clock as the console, drawn in three.js (frontend/flight3d.js). */
async function open3DView(inc, p, tel, clock, label) {
  let view = null;
  const hud = {};
  const cell = (k, l) => { hud[k] = el("strong", { class: "num" }, "-"); return el("div", { class: "f3d-cell" }, el("span", {}, t(l)), hud[k]); };
  const stage = el("div", { class: "f3d-stage" }, el("div", { class: "f3d-loading" }, t("Loading 3D view...")));
  const maskBtn = el("button", { class: "btn btn-sm", type: "button", hidden: true, "aria-pressed": "true" }, t("Burn scar"));
  const dc = dataConsole(p, tel);
  const consoleBtn = el("button", { class: "btn btn-sm", type: "button", "aria-pressed": "true" }, svg("list", "icon icon-sm"), t("Console"));
  consoleBtn.onclick = () => { const on = consoleBtn.getAttribute("aria-pressed") !== "true"; consoleBtn.setAttribute("aria-pressed", String(on)); dc.node.hidden = !on; };
  const note = el("p", { class: "f3d-note" });
  const close = () => { view?.destroy(); overlay.remove(); document.removeEventListener("keydown", onKey); clearInterval(timer); };
  const onKey = (e) => { if (e.key === "Escape") close(); };
  const overlay = el("div", { class: "f3d", role: "dialog", "aria-modal": "true", "aria-label": t("3D flight view") },
    el("div", { class: "f3d-head" },
      el("span", { class: "pill pill-sim" }, "SIMULASI"), el("strong", {}, t("3D flight view")), el("span", { class: "muted" }, inc.id),
      el("span", { class: "f3d-state" }), el("div", { class: "f3d-tools" }, consoleBtn, maskBtn,
        el("button", { class: "btn btn-sm", type: "button", onclick: () => view?.recentre() }, svg("target", "icon icon-sm"), t("Follow the drone")),
        el("button", { class: "btn btn-icon", type: "button", "aria-label": t("Close"), onclick: () => close() }, svg("x", "icon icon-sm")))),
    stage,
    el("div", { class: "f3d-hud" }, cell("clock", "Flight time"), cell("frame", "Frame"), cell("battery", "Battery"), cell("speed", "Ground speed"), cell("alt", "Altitude"), cell("wind", "Wind")),
    note);
  document.body.append(overlay);
  document.addEventListener("keydown", onKey);
  let current = null;
  // The HUD and the console read the clock themselves, so they keep time even when the 3D loop is throttled.
  const timer = setInterval(() => {
    const s = Math.max(0, Math.min(tel.duration_s, clock()));
    const st = telemetryAt(tel, s);
    current = { st, s };
    overlay.querySelector(".f3d-state").textContent = label(st, s);
    hud.clock.textContent = `${mmss(s)} / ${mmss(tel.duration_s)}`;
    hud.frame.textContent = `${st.shots} / ${tel.samples.length}`;
    hud.battery.textContent = `${Math.round(st.battery_pct)}% · ${t("pack {n} of {m}", { n: st.pack, m: tel.packs })}`;
    hud.speed.textContent = `${fmtNum(st.ground_speed_ms)} m/s`;
    hud.alt.textContent = `${Math.round(st.alt_m)} m`;
    hud.wind.textContent = windText(tel.wind);
    dc.update(st, s);
  }, 200);
  try {
    const [{ createFlight3D }, ground] = await Promise.all([import("/flight3d.js"), api(`/api/drone-proposals/${p.id}/ground`)]);
    const loadImage = async (url) => {
      const res = await fetch(url, { headers: url.startsWith("/api/drone-proposals/") ? authHeaders() : {} });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return createImageBitmap(await res.blob(), { imageOrientation: "flipY" });
    };
    stage.replaceChildren(dc.node);
    view = createFlight3D(stage, {
      plan: p.plan, ground, hotspots: inc.hotspots, loadImage, t,
      getState: () => {
        const s = Math.max(0, Math.min(tel.duration_s, clock()));
        current = { st: telemetryAt(tel, s), s };
        return current.st;
      },
    });
    if (view.hasMask) {
      maskBtn.hidden = false;
      maskBtn.onclick = () => { const on = maskBtn.getAttribute("aria-pressed") !== "true"; maskBtn.setAttribute("aria-pressed", String(on)); view.setMask(on); };
    }
    note.textContent = t(ground.kind === "sentinel2"
      ? "Ground: Sentinel-2 after the fire ({d}), burn scar in red, laid flat (no elevation model). Hotspots in orange. The drone is drawn {x} times larger than life. Inset: what its camera sees."
      : "Ground: OpenStreetMap, laid flat (no elevation model). Hotspots in orange. The drone is drawn {x} times larger than life. Inset: what its camera sees. OpenStreetMap shows little in forest: a Sentinel-2 pair (Find Sentinel-2 images in the incident view) gives this view real imagery.", { d: ground.dates?.[1] || "", x: DRONE_SCALE_3D })
      + ` ${ground.attribution || ""}`;
  } catch (e) {
    stage.replaceChildren(el("p", { class: "empty" }, t("3D view unavailable: {e}", { e: e.message })));
  }
}

/* A play button that turns into Stop while its run is on the map. */
function runToggle(label, start, primary = false) {
  const b = el("button", { class: `btn ${primary ? "btn-primary" : ""}`, type: "button" });
  const paint = (on) => { fill(b, svg(on ? "stop" : "play", "icon icon-sm"), t(on ? "Stop" : label)); b.dataset.on = String(on); };
  paint(false);
  b.onclick = () => {
    if (b.dataset.on === "true") { stopFlight(); return; }
    start(() => paint(false));
    paint(true);
  };
  return b;
}

/* What an approved plan shows: before a flight, during it, and after. */
function flightBlock(inc, p) {
  const f = p.flight;
  const plan = p.plan;
  const btn = (cls, icon, label, onclick) => el("button", { class: `btn ${cls}`, type: "button", onclick }, svg(icon, "icon icon-sm"), t(label));
  const example = inc.id === EXAMPLE_SURVEY
    ? btn("", "camera", "See the survey result below", () => [...document.querySelectorAll("#detail-view .section")].find((s) => /Survei drone|Drone survey/.test(s.textContent))?.scrollIntoView({ behavior: "smooth" }))
    : btn("", "camera", "See an example survey result", () => openIncident(EXAMPLE_SURVEY));
  if (f?.state === "flying") {
    const con = flightConsole();
    const started = Date.parse(f.started_at);
    const land = btn("btn-primary", "check", "Land and finish", () => (con.node.dataset.done === "true" ? finishFlight(inc, f, "completed") : toast(t("The route is not finished yet. Abort instead, or wait for the last frame."), "alert")));
    land.setAttribute("aria-disabled", "true");
    const abort = btn("btn-ghost-danger", "x", "Abort flight", () => finishFlight(inc, f, "aborted"));
    runFlight(p, f.telemetry, () => ((Date.now() - started) / 1000) * f.speed, (st, s) => {
      const done = s >= f.telemetry.duration_s;
      con.update(f.telemetry, st, s, t(done ? "Route done: ready to land" : st.swapping ? "Battery swap on the ground" : "Flying (simulated pilot)"));
      con.node.dataset.done = String(done);
      land.setAttribute("aria-disabled", String(!done));
      abort.hidden = done;
    });
    const live3d = btn("", "layers", "View in 3D", () => open3DView(inc, p, f.telemetry, () => ((Date.now() - started) / 1000) * f.speed,
      (st, s) => t(s >= f.telemetry.duration_s ? "Route done: ready to land" : st.swapping ? "Battery swap on the ground" : "Flying (simulated pilot)")));
    return [con.node, el("p", { class: "muted", style: "margin:0;font-size:11px" }, t("Played x{x} faster than real time.", { x: f.speed })),
      el("div", { class: "actions" }, land, live3d, abort)];
  }
  if (f?.state === "completed") {
    const tel = f.telemetry;
    const con = flightConsole();
    con.node.hidden = true;
    const replay = runToggle("Replay flight", (done) => {
      con.node.hidden = false;
      const t0 = performance.now();
      runFlight(p, tel, () => ((performance.now() - t0) / 1000) * (tel.duration_s / PREVIEW_S), (st, s) => con.update(tel, st, s, t("Replay")), () => { con.node.hidden = true; done(); }, true);
    }, true);
    return [
      el("dl", { class: "kv" },
        el("dt", {}, t("Landed")), el("dd", {}, fmtTime(f.finished_at)),
        el("dt", {}, t("Flight time")), el("dd", { class: "num" }, `${mmss(tel.duration_s)} · ${t("{n} battery pack(s)", { n: tel.packs })}`),
        el("dt", {}, t("Frames")), el("dd", { class: "num" }, `${f.frames_captured} / ${tel.samples.length}`),
        el("dt", {}, t("Wind")), el("dd", {}, windText(tel.wind)),
        el("dt", {}, t("Imagery")), el("dd", {}, t(inc.id === EXAMPLE_SURVEY ? "Simulated survey of this incident: frames, Glimmer filter, SAM area below." : "None: only the route was simulated. A real flight would upload its frames here."))),
      con.node, el("div", { class: "actions" }, replay,
        btn("", "layers", "Replay in 3D", () => { const t0 = performance.now(); open3DView(inc, p, tel, () => ((performance.now() - t0) / 1000) * (tel.duration_s / REPLAY_3D_S), () => t("Replay")); }),
        example)];
  }
  const preview = runToggle("Preview route", (done) => {
    const t0 = performance.now();
    const tel = { samples: plan.waypoints.map((w, i) => ({ ...w, t_s: i, alt_m: plan.altitude_agl_m, ground_speed_ms: 8, battery_pct: 100, pack: 1 })), duration_s: plan.waypoints.length - 1, packs: 1 };
    runFlight(p, tel, () => ((performance.now() - t0) / 1000) * (tel.duration_s / PREVIEW_S), null, done, true);
  });
  const preview3d = btn("", "layers", "Preview in 3D", () => {
    const t0 = performance.now();
    const tel = { samples: plan.waypoints.map((w, i) => ({ ...w, t_s: i * 15, alt_m: plan.altitude_agl_m, ground_speed_ms: 8, battery_pct: 100, pack: 1 })),
      duration_s: (plan.waypoints.length - 1) * 15, packs: 1, wind: null };
    open3DView(inc, p, tel, () => ((performance.now() - t0) / 1000) * (tel.duration_s / REPLAY_3D_S), () => t("Route preview"));
  });
  return [
    f?.state === "aborted" ? el("p", { class: "muted", style: "margin:0;font-size:12px" }, t("Last flight aborted at frame {k} of {n}, {when}.", { k: f.frames_captured, n: f.telemetry.samples.length, when: fmtTime(f.finished_at) })) : null,
    p.otherFlying ? el("p", { class: "muted", style: "margin:0;font-size:12px" }, t("Another plan of this incident is in the air. One drone flies at a time.")) : null,
    el("div", { class: "actions" }, p.otherFlying ? null : btn("btn-primary", "play", f?.state === "aborted" ? "Fly again" : "Start flight (simulated pilot)", () => startFlightDialog(inc, p)), preview, preview3d, example,
      btn("btn-ghost-danger", "x", "Cancel plan", () => decideProposal(inc, p, "cancel")))];
}

function showFlightPlan(p) {
  state.mapReady?.then(() => {
    if (!map) return;
    ["drone-plan-area", "drone-plan-path"].forEach((id) => map.getLayer(id) && map.removeLayer(id));
    if (map.getSource("drone-plan")) map.removeSource("drone-plan");
    if (!p) return;
    const [w, s, e, n] = p.plan.bbox;
    const area = { type: "Feature", properties: { kind: "area" }, geometry: { type: "Polygon", coordinates: [[[w, s], [e, s], [e, n], [w, n], [w, s]]] } };
    const path = { type: "Feature", properties: { kind: "path" }, geometry: { type: "LineString", coordinates: p.plan.waypoints.map((x) => [x.lon, x.lat]) } };
    map.addSource("drone-plan", { type: "geojson", data: { type: "FeatureCollection", features: [area, path] } });
    map.addLayer({ id: "drone-plan-area", type: "fill", source: "drone-plan", filter: ["==", ["get", "kind"], "area"], paint: { "fill-color": "#7a5af8", "fill-opacity": 0.08 } });
    map.addLayer({ id: "drone-plan-path", type: "line", source: "drone-plan", filter: ["==", ["get", "kind"], "path"], paint: { "line-color": "#7a5af8", "line-width": 1.5, "line-dasharray": [2, 1] } });
    map.fitBounds([[w, s], [e, n]], { padding: 80, duration: 800 });
  });
}

async function proposeDrone(inc) {
  let reload = false;
  await track(inc.id, "drone", t("Planning a drone survey"), async () => {
    try {
      const r = await api(`/api/incidents/${inc.id}/drone-proposals`, { method: "POST", body: JSON.stringify({ reason: t("Planned by a verifier from the incident view.") }) });
      toast(t(r.created ? "Drone survey proposed. Approve it to send it to a pilot." : "An active plan already exists; showing it."), "check");
      reload = true;
    } catch (e) {
      toast(t("Not proposed: {e}", { e: e.message }), "alert");
    }
  });
  if (reload && state.selected === inc.id) openIncident(inc.id);
}

function decideProposal(inc, p, decision) {
  const note = el("input", { class: "input", maxlength: 300, placeholder: t("Note (optional)"), "aria-label": t("Note (optional)") });
  const err = el("p", { class: "form-error", hidden: true });
  const text = {
    approve: ["Approve mission", "Approve this drone survey?", "Approval clears the plan for a pilot. This prototype has no drone connected, so nothing flies by itself; the approval is recorded with the plan.", "Mission approved and recorded."],
    reject: ["Reject mission", "Reject this drone survey?", "The plan is closed. A new one can be proposed later.", "Mission rejected and recorded."],
    cancel: ["Cancel plan", "Cancel this drone survey?", "The approved plan is withdrawn and kept on record. A new plan can then be proposed.", "Mission cancelled and recorded."],
  }[decision];
  const ok = el("button", { class: `btn ${decision === "approve" ? "btn-primary" : "btn-ghost-danger"}`, type: "button" }, t(text[0]));
  const dlg = modal({
    title: t(text[1]),
    body: [el("p", { style: "margin:0" }, t(text[2])), note, err],
    actions: [el("button", { class: "btn", type: "button", onclick: () => dlg.close() }, t(decision === "cancel" ? "Keep plan" : "Cancel")), ok],
  });
  ok.onclick = async () => {
    ok.disabled = true;
    try {
      await api(`/api/drone-proposals/${p.id}/decision`, { method: "POST", body: JSON.stringify({ decision, note: note.value.trim() || null }) });
      dlg.close();
      if (decision === "cancel") stopFlight();
      toast(t(text[3]), "check");
      openIncident(inc.id);
    } catch (e) {
      err.hidden = false;
      fill(err, svg("alert", "icon icon-sm"), el("span", {}, e.message));
      ok.disabled = false;
    }
  };
}

function proposalCard(inc, p) {
  const plan = p.plan;
  const est = plan.estimate;
  const flown = p.status === "approved" && p.flight?.state === "completed";
  return el("div", { class: `proposal ${flown ? "flown" : p.status}` },
    el("div", { class: "sat-head" },
      el("span", { class: `proposal-pill ${flown ? "flown" : p.flight?.state === "flying" ? "flying" : p.status}` },
        t(flown ? "Flown" : p.flight?.state === "flying" ? "In flight" : PROPOSAL_LABEL[p.status])),
      el("span", { class: "muted", style: "font-size:12px" }, t(p.source === "agent" ? "proposed by the AI agent" : "proposed by a verifier"), " · ", fmtTime(p.created_at))),
    p.reason ? el("p", { class: "proposal-reason" }, `"${p.reason}"`) : null,
    el("dl", { class: "kv" },
      el("dt", {}, t("Area")), el("dd", {}, `${fmtNum(plan.area_km2)} km² · ${t("{lines} lines x {n} frames, {alt} m above ground, GSD {gsd} m", { lines: plan.lines, n: plan.frames_per_line, alt: plan.altitude_agl_m, gsd: fmtNum(plan.gsd_m) })}`),
      el("dt", {}, t("Flight")), el("dd", {}, t("about {min} min, {km} km path, {b} battery(ies)", { min: fmtNum(est.flight_minutes), km: fmtNum(est.path_km), b: est.batteries }))),
    p.note ? el("p", { class: "muted", style: "margin:0;font-size:12px" }, `${t("Note")}: ${p.note}`) : null,
    p.status === "approved" ? flightBlock(inc, p) : null,
    el("div", { class: "actions" },
      mapToggle("plan", "Show plan on map", () => showFlightPlan(p), () => showFlightPlan(null)),
      p.status === "proposed" ? [
        el("button", { class: "btn btn-primary", type: "button", onclick: () => decideProposal(inc, p, "approve") }, svg("check", "icon icon-sm"), t("Approve mission")),
        el("button", { class: "btn btn-ghost-danger", type: "button", onclick: () => decideProposal(inc, p, "reject") }, svg("x", "icon icon-sm"), t("Reject"))] : null),
  );
}

async function proposalSection(inc) {
  if (!token()) return null;
  const r = await api(`/api/incidents/${encodeURIComponent(inc.id)}/drone-proposals`).catch(() => null);
  if (!r) return null;
  // One active plan (proposed or approved) per incident: the propose button appears only when none is active.
  const open = r.items.some((p) => p.status === "proposed" || (p.status === "approved" && p.flight?.state !== "completed"));
  const btn = el("button", { class: "btn", type: "button", dataset: { busy: "drone" } }, svg("target", "icon icon-sm"), t("Propose a drone survey"));
  btn.onclick = () => proposeDrone(inc);
  const flyingId = r.items.find((p) => p.flight?.state === "flying")?.id ?? null;
  r.items.forEach((p) => { p.otherFlying = flyingId != null && flyingId !== p.id; });
  // Open only when a plan waits for a verifier's decision; otherwise the title says what is there.
  const node = section({ icon: "target", title: t("Drone survey plan"), count: r.count, open: r.items.some((p) => p.status === "proposed"),
    tip: t("A mapping flight planned over the hotspots: lawnmower lines at 120 m above ground. The AI agent may propose one when independent evidence is missing; only a verifier can approve it. Planning is free."),
    content: [r.items.length ? r.items.map((p) => proposalCard(inc, p)) : el("p", { class: "empty" }, t("No drone survey planned.")),
      open ? el("p", { class: "muted", style: "margin:0;font-size:12px" }, t("One active plan per incident. Cancel the approved plan to make a new one."))
        : el("div", { class: "actions" }, btn)] });
  node.dataset.activePlan = String(open);
  return node;
}

/* Simulated drone survey (SIMULASI): shown beside the incident, never part of its evidence. */
const droneUrl = (m, kind, file) => `/api/drone/missions/${encodeURIComponent(m.mission_id)}/${kind}/${file}`;

function showFootprints(m) {
  state.mapReady?.then(() => {
    if (!map) return;
    ["drone-foot-fill", "drone-foot-line"].forEach((id) => map.getLayer(id) && map.removeLayer(id));
    if (map.getSource("drone-foot")) map.removeSource("drone-foot");
    if (!m) return;
    const features = m.frames.map((f) => ({ type: "Feature", properties: { up: !!f.glimmer?.uplink, seq: f.seq },
      geometry: { type: "Polygon", coordinates: [[...f.footprint, f.footprint[0]]] } }));
    map.addSource("drone-foot", { type: "geojson", data: { type: "FeatureCollection", features } });
    map.addLayer({ id: "drone-foot-fill", type: "fill", source: "drone-foot", paint: { "fill-color": ["case", ["get", "up"], "#0064e0", "#7a8b99"], "fill-opacity": ["case", ["get", "up"], 0.12, 0.05] } });
    map.addLayer({ id: "drone-foot-line", type: "line", source: "drone-foot", paint: { "line-color": ["case", ["get", "up"], "#0064e0", "#7a8b99"], "line-width": 1, "line-opacity": 0.7 } });
    const lons = m.frames.flatMap((f) => f.footprint.map((p) => p[0]));
    const lats = m.frames.flatMap((f) => f.footprint.map((p) => p[1]));
    map.fitBounds([[Math.min(...lons), Math.min(...lats)], [Math.max(...lons), Math.max(...lats)]], { padding: 60, duration: 800 });
  });
}

function droneContent(m) {
  const g = m.glimmer || {};
  const s = m.survey || {};
  const p = m.plan;
  let current = m.frames.find((f) => f.glimmer?.uplink) || m.frames[0];
  let view = "frames";
  const big = el("img", { class: "sat-img drone-img", alt: t("Drone frame") });
  const caption = el("p", { class: "muted drone-caption" });
  const seg = el("div", { class: "segmented", role: "group", "aria-label": t("Drone view") });
  const show = () => {
    const kind = view === "frames" || !current.glimmer?.uplink && view === "sam" ? "frames" : view;
    big.src = droneUrl(m, kind, kind === "frames" ? current.file : current.file.replace(".jpg", ".png"));
    seg.querySelectorAll("button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.k === kind)));
    const d = current.glimmer;
    fill(caption, `${current.file} · `, d ? (d.uplink ? t("Glimmer: uplinked") : t("Glimmer: dropped")) : t("Glimmer: no decision"),
      d?.reason ? ` · "${d.reason}"` : null, current.iou != null ? ` · IoU ${fmtNum(current.iou)}` : null);
    strip.querySelectorAll("button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.seq === String(current.seq))));
  };
  [["frames", t("Frame")], ["sam", t("SAM mask")], ["truth", t("Truth")]].forEach(([k, label]) =>
    seg.append(el("button", { type: "button", dataset: { k }, "aria-pressed": "false", onclick: () => { view = k; show(); } }, label)));
  const strip = el("div", { class: "drone-strip" }, m.frames.map((f) =>
    el("button", { type: "button", class: `drone-thumb ${f.glimmer?.uplink ? "" : "dropped"}`, dataset: { seq: String(f.seq) }, title: f.file,
      onclick: () => { current = f; show(); } }, el("img", { src: droneUrl(m, "frames", f.file), alt: f.file, loading: "lazy" }))));
  show();
  return [
    el("div", { class: "sat-head" }, el("span", { class: "pill pill-sim" }, "SIMULASI"), seg),
    big, caption, strip,
    el("dl", { class: "kv" },
      el("dt", {}, t("Mission")), el("dd", {}, t("{lines} lines x {n} frames, {alt} m above ground, GSD {gsd} m", { lines: p.lines, n: p.frames_per_line, alt: p.altitude_agl_m, gsd: fmtNum(p.gsd_m) })),
      el("dt", {}, t("Glimmer filter")), el("dd", {}, t("{up} of {all} frames uplinked, {drop} dropped", { up: g.uplinked ?? "?", all: g.frames ?? m.frames.length, drop: g.dropped ?? "?" })),
      el("dt", {}, t("SAM area")), el("dd", {}, s.sam_burned_ha == null ? t("not measured") : [el("strong", { class: "num" }, `${fmtNum(s.sam_burned_ha)} ha`), " ", el("span", { class: "muted" }, `"${s.concept}"`)]),
      el("dt", {}, t("Measured frames")), el("dd", {}, s.frames_measured == null ? "" : t("{m} of {up} uplinked frames; SAM found no mask on frames burned edge to edge", { m: s.frames_measured, up: s.frames_uplinked })),
      el("dt", {}, t("Truth (render)")), el("dd", {}, s.truth_burned_ha_measured == null ? "" : `${fmtNum(s.truth_burned_ha_measured)} ha · ${t("error {e}%", { e: fmtNum(s.area_error_pct) })} · IoU ${fmtNum(s.median_iou)}`),
      el("dt", {}, t("Missed by filter")), el("dd", {}, s.missed_by_filter_ha == null ? "" : t("{ha} ha of burned ground in dropped frames", { ha: fmtNum(s.missed_by_filter_ha) })),
      el("dt", {}, t("Cost")), el("dd", { class: "num" }, s.cost_usd == null ? "" : `$${s.cost_usd}`),
    ),
    el("p", { class: "muted", style: "margin:0;font-size:11px" }, t("Frames are rendered in Blender over the real Sentinel-2 burn mask of this incident. The error is against the render, not real-world accuracy. These numbers never enter the triage.")),
    el("div", { class: "actions" }, mapToggle("footprints", "Show footprints on map", () => showFootprints(m), () => showFootprints(null))),
  ];
}

async function droneSection(inc) {
  const list = await api(`/api/drone/missions?incident_id=${encodeURIComponent(inc.id)}`).catch(() => null);
  if (!list?.count) return null;
  const m = await api(`/api/drone/missions/${encodeURIComponent(list.items[0].mission_id)}`).catch(() => null);
  if (!m) return null;
  return section({ icon: "target", title: [t("Drone survey (simulation)"), aiBadge("Muse Glimmer + SAM 3.1")], open: false,
    tip: t("A simulated mapping flight: Muse Glimmer at the ground station picks which frames to send, SAM 3.1 segments them, and code turns masks into hectares from the telemetry."),
    content: droneContent(m) });
}

async function fetchSatellite(id) {
  let reload = false;
  await track(id, "satellite", t("Searching Sentinel-2"), async () => {
    try {
      const r = await api(`/api/incidents/${id}/satellite`, { method: "POST" });
      if (r.evidence_id) { toast(t(r.reused ? "Pair checked in the last 24 hours; showing it." : "Sentinel-2 pair stored as {id}.", { id: r.evidence_id }), "check"); reload = true; return; }
      toast(t("No pair: {why}", { why: r.reason || "unknown" }), "alert");
    } catch (e) {
      toast(t("Sentinel-2 failed: {e}", { e: e.message }), "alert");
    }
  });
  if (reload && state.selected === id) openIncident(id);
}

async function collectNow(id) {
  let reload = false;
  await track(id, "collect", t("Collecting evidence"), async () => {
    try {
      const r = await api(`/api/incidents/${id}/enrich`, { method: "POST" });
      const failed = Object.entries(r.sources).filter(([, v]) => String(v).startsWith("failed")).map(([k]) => k);
      toast(failed.length ? t("Collected, but {list} failed and will be retried.", { list: failed.join(", ") }) : t("Evidence collected."), failed.length ? "alert" : "check");
      reload = true;
    } catch (e) {
      toast(t("Not collected: {e}", { e: e.message }), "alert");
    }
  });
  // Only reload the view if the reader is still on this incident; otherwise the ring and badge just disappear.
  if (reload && state.selected === id) openIncident(id);
}

/* Sources the collector can still add: absent, or failed last time. */
function missingSources(inc) {
  const have = new Set(inc.evidence.map((e) => e.type));
  const st = inc.enrichment || {};
  return ["hotspot", "weather", "osm"].filter((k) => (k === "hotspot" ? inc.hotspot_count > 0 : true) && (!have.has(k) || String(st[k] || "").startsWith("failed")));
}

/* Triage view: claims link to their evidence, disputes and gate actions are shown, never hidden. */
function evidenceChip(id) {
  return el("button", { class: "ev-chip", type: "button", title: t("Show {id}", { id }), onclick: (e) => { e.stopPropagation(); focusEvidence(id); } }, id);
}

function focusEvidence(id) {
  const row = document.querySelector(`[data-ev="${id}"]`);
  if (!row) return toast(t("{id} is not visible in this view", { id }), "alert");
  const sec = row.closest(".section");
  if (sec && sec.dataset.open !== "true") sec.querySelector(".section-head").click();
  row.scrollIntoView({ behavior: "smooth", block: "center" });
  row.classList.remove("flash");
  void row.offsetWidth;
  row.classList.add("flash");
}

function claimRows(triage) {
  if (!triage?.claims?.length) return [el("p", { class: "empty" }, t("No claims passed the evidence gate."))];
  return triage.claims.map((c) =>
    el("div", { class: `claim ${c.disputed ? "is-disputed" : ""}` },
      el("p", { class: "claim-text" }, modelText(c.text, c.text_id)),
      el("div", { class: "claim-meta" }, c.evidence_ids.map(evidenceChip),
        c.disputed ? el("span", { class: "dispute" }, svg("alert", "icon icon-sm"), t("Disputed: "), modelText(c.disputed, null)) : null)));
}

function gateRows(gate) {
  if (!gate) return [el("p", { class: "empty" }, t("No gate result yet."))];
  const pct = gate.grounding_rate == null ? t("n/a") : `${Math.round(gate.grounding_rate * 100)}%`;
  const rows = [el("div", { class: "stats-grid" },
    el("div", { class: "stat" }, el("div", { class: "stat-v num" }, pct), el("div", { class: "stat-l" }, t("Grounding rate"))),
    el("div", { class: "stat" }, el("div", { class: "stat-v num" }, `${gate.claims_passed}/${gate.claims_total}`), el("div", { class: "stat-l" }, t("Claims passed"))),
    el("div", { class: "stat" }, el("div", { class: "stat-v num" }, String(gate.critique?.disputed?.length ?? 0)), el("div", { class: "stat-l" }, t("Disputed"))))];
  const list = (label, items, fmt) => items?.length ? rows.push(el("div", { class: "gate-list" }, el("div", { class: "legend-label" }, t(label)), items.map((x) => el("div", { class: "ev" }, fmt(x))))) : null;
  list("Removed claims", gate.claims_dropped, (d) => el("span", {}, modelText(d.text, null), ` (${gateReason(d.reason)})`));
  list("Corrections", gate.corrections, (c) => t("{where}: {claimed} replaced with {corrected}", { where: c.where, claimed: c.claimed, corrected: c.corrected ?? t("nothing") }) + (c.note ? ` (${gateReason(c.note)})` : ""));
  list("Content rule removals", gate.violations, (v) => el("span", {}, `${v.where}, ${t(RULE_LABEL[v.rule] || v.rule)}: `, modelText(v.removed, null)));
  list("Status changes", gate.downgrades, statusChange);
  list("Not reviewed", gate.critique?.errors, (e) => el("span", { lang: "en" }, fold(e, { lines: 1 })));
  rows.push(el("p", { class: "muted", style: "margin:0;font-size:12px" },
    t("Triaged {when} in {s} s, metered cost ${cost}. Critique: {who}.", {
      when: gate.triaged_at ? fmtTime(gate.triaged_at) : "", s: gate.seconds ?? "?", cost: (gate.cost_usd ?? 0).toFixed(4),
      who: (gate.critique?.providers || []).join(", ") || t("none") })));
  return rows;
}

/* Deliberate triage: pick incidents in the list or draw an area on the map, then confirm with the execution code. */
function batchLimit() { return state.control?.batch_limit ?? 5; }
function estCost(n) { return (n * (state.control?.est_cost_per_triage_usd ?? 0.15)).toFixed(2); }

function togglePick(id) {
  if (picked.has(id)) picked.delete(id);
  else if (picked.size >= batchLimit()) return toast(t("At most {n} incidents per execution.", { n: batchLimit() }), "alert");
  else picked.add(id);
  renderList();
}

function setPicking(on) {
  state.picking = on;
  if (!on) picked.clear();
  renderList();
}

function syncPickedLayer() {
  if (map?.getLayer("incident-picked")) map.setFilter("incident-picked", ["in", ["get", "id"], ["literal", [...picked]]]);
}

function selectionBar() {
  if (!state.picking) return null;
  const n = picked.size;
  const run = el("button", { class: "btn btn-primary", type: "button" }, svg("activity", "icon icon-sm"), t("Triage"));
  run.disabled = !n;
  run.onclick = () => openTriageDialog([...picked]);
  return el("li", { class: "sel-bar card" },
    el("div", {}, el("strong", { class: "num" }, t("{n} of {max} selected", { n, max: batchLimit() })),
      el("div", { class: "muted", style: "font-size:12px" }, n ? t("Estimated ${cost}, metered after the run", { cost: estCost(n) }) : t("Tick incidents, or draw an area on the map"))),
    el("div", { class: "actions" },
      el("button", { class: "btn", type: "button", onclick: () => startAreaSelect() }, svg("crosshair", "icon icon-sm"), t("Area")),
      el("button", { class: "btn", type: "button", onclick: () => setPicking(false) }, t("Cancel")),
      run));
}

/* Area selection: drag a box on the map; incidents inside are picked, highest heat first, up to the limit. */
let areaBox = null;
function startAreaSelect() {
  if (!map) return;
  if (!state.picking) setPicking(true);
  const canvas = map.getCanvasContainer();
  canvas.classList.add("is-area-select");
  map.dragPan.disable();
  map.boxZoom.disable();
  toast(t("Drag a box over the area to triage. Esc cancels."), "crosshair");
  let start = null;
  // Measure against the map container: the canvas container is a zero-height wrapper.
  const frame = map.getContainer();
  const pos = (e) => { const r = frame.getBoundingClientRect(); return { x: e.clientX - r.left, y: e.clientY - r.top }; };
  const finish = (endPt) => {
    canvas.classList.remove("is-area-select");
    canvas.removeEventListener("pointerdown", down);
    window.removeEventListener("pointermove", move);
    window.removeEventListener("pointerup", up);
    document.removeEventListener("keydown", esc);
    areaBox?.remove();
    areaBox = null;
    map.dragPan.enable();
    map.boxZoom.enable();
    if (!start || !endPt) return;
    const a = map.unproject([Math.min(start.x, endPt.x), Math.max(start.y, endPt.y)]);
    const b = map.unproject([Math.max(start.x, endPt.x), Math.min(start.y, endPt.y)]);
    const inside = state.incidents.filter((i) => i.lon >= a.lng && i.lon <= b.lng && i.lat >= a.lat && i.lat <= b.lat)
      .sort((x, y) => (y.max_frp ?? -1) - (x.max_frp ?? -1));
    if (!inside.length) return toast(t("No incidents in that area."), "alert");
    picked.clear();
    inside.slice(0, batchLimit()).forEach((i) => picked.add(i.id));
    toast(inside.length > batchLimit() ? t("{n} incidents in the area; picked the {max} with the highest FRP.", { n: inside.length, max: batchLimit() })
      : t("{n} incident(s) picked.", { n: inside.length }).replace("(s)", plural(inside.length)));
    renderList();
  };
  const down = (e) => {
    start = pos(e);
    areaBox = el("div", { class: "area-box" });
    frame.append(areaBox);
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  };
  const move = (e) => {
    const p = pos(e);
    Object.assign(areaBox.style, { left: `${Math.min(start.x, p.x)}px`, top: `${Math.min(start.y, p.y)}px`, width: `${Math.abs(p.x - start.x)}px`, height: `${Math.abs(p.y - start.y)}px` });
  };
  const up = (e) => finish(pos(e));
  const esc = (e) => { if (e.key === "Escape") finish(null); };
  canvas.addEventListener("pointerdown", down, { once: true });
  document.addEventListener("keydown", esc);
}

function codeField() {
  return el("input", { class: "input code-input", type: "password", placeholder: t("Execution code"), autocomplete: "off", "aria-label": t("Execution code") });
}

function errorBox() {
  return el("div", { class: "form-error", role: "alert", hidden: true });
}

function showError(box, msg) {
  box.hidden = false;
  fill(box, svg("alert", "icon icon-sm"), el("span", {}, msg));
}

function openTriageDialog(ids) {
  const n = ids.length;
  const items = ids.map((id) => state.incidents.find((i) => i.id === id) || { id });
  const code = codeField();
  const err = errorBox();
  const left = state.control?.remaining_today;
  const run = el("button", { class: "btn btn-primary", type: "button" }, svg("activity", "icon icon-sm"), t("Run {n} triage(s)", { n }).replace("(s)", plural(n)));
  const dlg = modal({
    title: t("Triage {n} incident(s)", { n }).replace("(s)", plural(n)),
    body: [
      el("div", { class: "gate-list" }, items.map((i) => el("div", { class: "ev" }, el("span", { class: "ev-id" }, i.id),
        el("span", {}, i.hotspot_count != null ? t("{n} hotspots, max FRP {frp}", { n: i.hotspot_count, frp: fmtFrp(i.max_frp) }) : "")))),
      el("p", { class: "muted", style: "margin:0;font-size:13px" },
        t("Estimated cost ${cost} (about ${each} each, metered after the run).", { cost: estCost(n), each: (state.control?.est_cost_per_triage_usd ?? 0.15).toFixed(3) }) + " " +
        (left != null ? t("{left} of {cap} runs left today.", { left, cap: state.control.daily_cap }) : "")),
      code, err,
    ],
    actions: [el("button", { class: "btn", type: "button", onclick: () => dlg.close() }, t("Cancel")), run],
  });
  const go = async () => {
    run.disabled = true;
    err.hidden = true;
    try {
      const single = n === 1 && !state.picking;
      const r = single
        ? await api(`/api/incidents/${ids[0]}/triage`, { method: "POST", body: JSON.stringify({ code: code.value }) })
        : await api("/api/triage/batch", { method: "POST", body: JSON.stringify({ incident_ids: ids, code: code.value }) });
      dlg.close();
      const started = single ? ids : r.accepted;
      toast(t("Triage started for {n} incident(s). About a minute each.", { n: started.length }).replace("(s)", plural(started.length)));
      started.forEach((id) => { const i = state.incidents.find((x) => x.id === id); if (i) i.status = "triaging"; });
      if (state.picking) setPicking(false);
      // The open incident re-renders at once, so its button shows the run instead of inviting a second one.
      if (state.selected && started.includes(state.selected)) { await openIncident(state.selected); pollTriage(state.selected); }
      else if (single || started.length === 1) pollTriage(started[0]);
      watchBatch(started);
      loadControl();
    } catch (e) {
      showError(err, e.message);
      run.disabled = false;
    }
  };
  run.onclick = go;
  code.addEventListener("keydown", (e) => e.key === "Enter" && go());
}

/* Batch progress: refresh the list until every started incident has left "triaging". */
let batchTimer;
function watchBatch(ids) {
  clearTimeout(batchTimer);
  const tick = async () => {
    try {
      const fresh = await api("/api/incidents");
      state.incidents = fresh.items;
      if (!state.selected) renderList();
      setMapData();
      if (ids.some((id) => state.incidents.find((i) => i.id === id)?.status === "triaging")) batchTimer = setTimeout(tick, 8000);
      else loadControl();
    } catch { batchTimer = setTimeout(tick, 8000); }
  };
  batchTimer = setTimeout(tick, 6000);
}

/* Schedule and usage card, verifiers only. */
const MODE_LABEL = { off: "Off", daily: "Daily", hourly: "Hourly" };
const modeLabel = (m) => t(MODE_LABEL[m] || m);

async function loadControl() {
  const box = $("#triage-control");
  if (!token()) return fill(box);
  try {
    state.control = await api("/api/triage/control");
    renderSources();
  } catch {
    return fill(box);
  }
  renderControl();
}

function renderControl() {
  const c = state.control;
  const box = $("#triage-control");
  if (!c) return fill(box);
  const seg = el("div", { class: "segmented", role: "group", "aria-label": t("Scheduled triage") },
    c.modes.map((m) => el("button", { type: "button", "aria-pressed": String(m === c.mode), onclick: () => m !== c.mode && changeMode(m) }, modeLabel(m))));
  const next = c.mode === "off" ? t("Scheduled triage is off.")
    : t("Next scheduled pass: {when}.", { when: c.next_auto_run && c.next_auto_run.startsWith("20") ? fmtTime(c.next_auto_run) : t("soon") });
  const content = [
    el("div", { class: "mode-row" }, el("span", { class: "legend-label" }, t("Schedule"),
      info(t("Scheduled triage covers citizen-reported incidents only, at most {n} per pass. Daily is the default; hourly is the high-intensity mode. Changing it needs the execution code.", { n: c.auto_budget_per_run }))), seg),
    el("div", { class: "stats-grid" },
      el("div", { class: "stat" }, el("div", { class: "stat-v num" }, `${c.today.runs}/${c.daily_cap}`), el("div", { class: "stat-l" }, t("Runs today"))),
      el("div", { class: "stat" }, el("div", { class: "stat-v num" }, `$${c.today.cost_usd.toFixed(2)}`), el("div", { class: "stat-l" }, t("Metered today"))),
      el("div", { class: "stat" }, el("div", { class: "stat-v num" }, String(c.batch_limit)), el("div", { class: "stat-l" }, t("Per selection")))),
    el("p", { class: "muted", style: "margin:0;font-size:12px" }, next),
    el("div", { class: "actions" },
      el("button", { class: "btn btn-primary", type: "button", onclick: () => setPicking(!state.picking) }, svg("check", "icon icon-sm"), t(state.picking ? "Stop selecting" : "Select incidents")),
      el("button", { class: "btn", type: "button", onclick: () => startAreaSelect() }, svg("crosshair", "icon icon-sm"), t("Select area on map"))),
    !c.code_configured ? el("p", { class: "form-error" }, svg("alert", "icon icon-sm"), el("span", {}, t("No execution code is configured on the server, so triage cannot run."))) : null,
  ];
  fill(box, section({ icon: "activity", title: t("Triage control"), open: state.picking || false, content }));
}

function changeMode(mode) {
  const code = codeField();
  const err = errorBox();
  const ok = el("button", { class: "btn btn-primary", type: "button" }, t("Set {mode}", { mode: modeLabel(mode) }));
  const explain = {
    off: "No scheduled triage. Verifiers still run triage on selected incidents.",
    daily: "One scheduled pass per day over citizen-reported incidents.",
    hourly: "High intensity: one scheduled pass per hour over citizen-reported incidents. The daily cap still applies.",
  };
  const dlg = modal({
    title: t("Scheduled triage: {mode}", { mode: modeLabel(mode) }),
    body: [el("p", { style: "margin:0" }, t(explain[mode])), code, err],
    actions: [el("button", { class: "btn", type: "button", onclick: () => dlg.close() }, t("Cancel")), ok],
  });
  const go = async () => {
    ok.disabled = true;
    try {
      state.control = await api("/api/triage/control/mode", { method: "PUT", body: JSON.stringify({ mode, code: code.value }) });
      dlg.close();
      toast(t("Scheduled triage set to {mode}.", { mode: modeLabel(mode) }));
      renderControl();
    } catch (e) {
      showError(err, e.message);
      ok.disabled = false;
    }
  };
  ok.onclick = go;
  code.addEventListener("keydown", (e) => e.key === "Enter" && go());
}

function triageButton(inc) {
  if (!token()) return null;
  const running = inc.status === "triaging";
  const b = el("button", { class: "btn btn-primary", type: "button" }, svg("activity", "icon icon-sm"), t(running ? "Triage running..." : inc.triage ? "Run triage again" : "Run triage"));
  b.disabled = running;
  b.onclick = () => openTriageDialog([inc.id]);
  return el("div", { class: "actions" }, b);
}

let pollTimer;
function pollTriage(id) {
  clearTimeout(pollTimer);
  const tick = async () => {
    if (state.selected !== id) return;
    try {
      const inc = await api(`/api/incidents/${id}`);
      if (inc.status !== "triaging") { openIncident(id); return; }
    } catch { /* keep polling; a transient error is not a result */ }
    pollTimer = setTimeout(tick, 5000);
  };
  pollTimer = setTimeout(tick, 4000);
}

/* Verification progress: the four stages, where this incident stands, and what would move it forward.
   Only a human verifier's approval makes an incident verified; the AI recommends, it never verifies. */
const CITIZEN_TRIGGERS = new Set(["citizen_report", "merged"]);

function reportLink(inc) {
  return `${location.origin}/lapor/?lat=${inc.lat.toFixed(5)}&lon=${inc.lon.toFixed(5)}&ref=${encodeURIComponent(inc.id)}`;
}

function progressCard(inc) {
  const types = new Set(inc.evidence.map((e) => e.type));
  const tri = inc.triage;
  const verdict = activeVerdict(inc);
  const triagedAt = inc.gate?.triaged_at;
  const has = {
    // The FIRMS detections themselves are evidence, even before enrichment writes their summary (EV) row.
    hotspot: types.has("hotspot") || inc.hotspots.length > 0, weather: types.has("weather"), osm: types.has("osm"), satellite: types.has("satellite"),
    citizen: inc.reporter_count > 0 || CITIZEN_TRIGGERS.has(inc.trigger) || ["report", "voice", "image"].some((x) => types.has(x)),
  };
  const newerEvidence = triagedAt && inc.evidence.some((e) => e.retrieved_at > triagedAt && e.type !== "hotspot");
  const step = (done, label, detail, current) => el("li", { class: `pstep ${done ? "done" : ""} ${current ? "current" : ""}` },
    el("span", { class: "pstep-dot" }, done ? svg("check", "icon icon-sm") : null), el("div", {}, el("div", { class: "pstep-label" }, label), detail ? el("div", { class: "pstep-detail" }, detail) : null));
  const chip = (ok, label) => el("span", { class: `ev-have ${ok ? "ok" : ""}` }, svg(ok ? "check" : "x", "icon icon-sm"), t(label));
  const evDone = has.hotspot || has.citizen;
  const triDone = !!tri;
  const stage = verdict ? 4 : triDone ? 3 : evDone ? 2 : 1;

  let headline;
  if (verdict?.decision === "approve") headline = el("div", { class: "phead ok" }, svg("check", "icon"), el("div", {}, el("strong", {}, t("Verified")), el("span", {}, t("A human verifier approved this incident on {when}.", { when: fmtTime(verdict.created_at) }))));
  else if (verdict?.decision === "reject") headline = el("div", { class: "phead bad" }, svg("x", "icon"), el("div", {}, el("strong", {}, t("Rejected")), el("span", {}, t("A human verifier rejected this incident on {when}.", { when: fmtTime(verdict.created_at) }))));
  else if (!tri) headline = el("div", { class: "phead" }, svg("info", "icon"), el("div", {}, el("strong", {}, t("Not triaged yet")),
    fold(el("span", {}, t("What you see is raw detection: hotspots and the evidence collected so far. No AI or human has judged it. The status will change after AI triage, and only a verifier can mark it verified.")))));
  else headline = el("div", { class: "phead warn" }, svg("activity", "icon"), el("div", {}, el("strong", {}, t("AI recommendation: {s}", { s: statusLabel(inc.status) }), " ", aiBadge("Muse Spark 1.3")),
    el("span", {}, tri.hidden ? t("Built on citizen evidence; details are shown after a verifier approves.")
      : t("Confidence {c}%, judged on {when}. Waiting for a human verifier to approve or reject.", { c: Math.round((tri.confidence ?? 0) * 100), when: triagedAt ? fmtTime(triagedAt) : "" }))));

  // What is missing, each with what fills it.
  const missing = [];
  const act = (label, icon, onclick, primary, busyKey) => el("button", { class: `btn ${primary ? "btn-primary" : ""}`, type: "button", onclick, dataset: busyKey ? { busy: busyKey } : {} }, svg(icon, "icon icon-sm"), t(label));
  if (!verdict) {
    if (!has.citizen && !has.satellite) missing.push({
      title: "Independent evidence", why: "A satellite hotspot is a heat signal, not a confirmed fire. To reach \"likely fire\" the AI needs a second, independent source: a photo, voice note, or report from the ground, or a burn scar in Sentinel-2 images.",
      actions: [
        act("Copy report link for the field", "camera", async () => {
          try { await navigator.clipboard.writeText(reportLink(inc)); toast(t("Link copied. Anyone near the site can send photos with it.")); }
          catch { toast(reportLink(inc)); }
        }),
        token() ? act("Find Sentinel-2 images", "satellite", () => fetchSatellite(inc.id), false, "satellite") : null,
        token() ? act("Propose a drone survey", "target", () => proposeDrone(inc), false, "drone") : null,
      ] });
    if (!has.weather) missing.push({ title: "Weather", why: "Wind and rain decide how fast a fire spreads and which way smoke goes.",
      actions: [token() ? act("Collect missing evidence", "activity", () => collectNow(inc.id), false, "collect") : null] });
    if (!has.osm) missing.push({ title: "Nearby settlements and water", why: "Distance to villages, roads, and water points from OpenStreetMap. The public OSM servers are often down; collection retries automatically.", actions: [] });
    if (tri?.status === "needs_more_info" || tri?.uncertainties?.length) missing.push({ title: "What the AI asked for", ai: true, why: null,
      list: modelList(tri.uncertainties || [], tri.uncertainties_id), actions: [] });
  }
  const nextStep = verdict ? null
    : !tri ? (token() ? el("div", { class: "pnext" }, el("span", {}, t("Next: run AI triage on the evidence above.")), triageButton(inc)) : el("p", { class: "pnext muted" }, t("Next: a verifier runs AI triage.")))
    : newerEvidence ? (token() ? el("div", { class: "pnext" }, el("span", {}, t("New evidence arrived after the last triage. Run it again to use it.")), triageButton(inc)) : null)
    : el("p", { class: "pnext muted" }, t(token() ? "Next: review the brief and claims, then approve or reject below." : "Next: a verifier reviews the AI brief and approves or rejects it."));

  return el("div", { class: "card progress-card" },
    headline,
    el("ol", { class: "psteps" },
      step(true, t("Detected"), t(TRIGGER_LABEL[inc.trigger] || inc.trigger), stage === 1),
      step(evDone, t("Evidence"), el("div", { class: "ev-haves" }, chip(has.hotspot, "Hotspots"), chip(has.weather, "Weather"), chip(has.osm, "OSM"), chip(has.satellite, "Sentinel-2"), chip(has.citizen, "Citizen")), stage === 2),
      step(triDone, t("AI triage"), tri ? statusLabel(inc.status) : t("not yet"), stage === 3),
      step(!!verdict, t("Human verifier"), verdict ? t(verdict.decision === "approve" ? "approved" : "rejected") : t("waiting"), stage === 4)),
    missing.length ? el("div", { class: "pmissing" }, el("div", { class: "legend-label" }, t("Still missing")),
      missing.map((m) => el("div", { class: "pmiss" }, el("strong", {}, t(m.title), m.ai ? [" ", aiBadge("Muse Spark 1.3")] : null), m.why ? fold(el("p", {}, t(m.why))) : null,
        m.list?.length ? el("ul", {}, m.list.map((u) => el("li", {}, u))) : null,
        m.actions.filter(Boolean).length ? el("div", { class: "actions" }, m.actions) : null))) : null,
    nextStep,
  );
}

/* Suppression outlook. Code reads the stored evidence and states what it means for a ground crew: where the
   fire is likely to move, which side to approach from, access, water, the nearest fire station, how dry it is,
   and how big. Thresholds are written in the (i). It is guidance for the people who decide, not an order.
   The AI part (risk, drivers, spread, actions) appears once a triage has run. */
const COMPASS = ["north", "northeast", "east", "southeast", "south", "southwest", "west", "northwest"];
const compass = (deg) => t(COMPASS[Math.round((((deg % 360) + 360) % 360) / 45) % 8]);
const latest = (inc, type) => [...inc.evidence].reverse().find((e) => e.type === type);
const WATER_NEAR_M = 1000, WATER_RELAY_M = 3000, ROAD_FAR_M = 10000, SETTLEMENT_NEAR_M = 5000;

function suppressionFacts(inc) {
  const rows = [];
  const add = (icon, title, value, note, tone = "") => rows.push({ icon, title, value, note, tone });
  const w = latest(inc, "weather")?.payload;
  const osm = latest(inc, "osm")?.payload?.nearest;
  const sat = latest(inc, "satellite")?.payload;
  let spread = null;
  if (w?.wind_direction_deg != null && w.wind_speed_ms != null) {
    if (w.wind_speed_ms < 1) {
      add("activity", t("Spread"), t("weak wind ({v} m/s)", { v: fmtNum(w.wind_speed_ms) }), t("Spread is slow and its direction uncertain; watch the smoke on site."));
    } else {
      spread = (w.wind_direction_deg + 180) % 360;
      add("arrow", t("Likely spread"), t("toward the {dir} ({deg} deg)", { dir: compass(spread), deg: Math.round(spread) }),
        t("Wind {v} m/s from the {from} ({deg} deg). Approach from the {from} side or the flanks, upwind of the fire; do not stand in front of its spread.", { v: fmtNum(w.wind_speed_ms), from: compass(w.wind_direction_deg), deg: w.wind_direction_deg }),
        w.wind_speed_ms >= 5 ? "warn" : "");
    }
    const dry = w.relative_humidity_pct != null && w.relative_humidity_pct < 50 && (w.rain_24h_mm ?? 0) < 1;
    const wet = (w.relative_humidity_pct ?? 0) >= 80 || (w.rain_24h_mm ?? 0) >= 5;
    add("clock", t("Fuel and weather"), t(dry ? "dry" : wet ? "humid" : "moderate"),
      t("Humidity {h}%, rain in the last 24 h {r} mm.", { h: w.relative_humidity_pct ?? "?", r: fmtNum(w.rain_24h_mm) }) + " " +
      t(dry ? "Dry fuel: fire spreads fast; peat can keep burning underground." : wet ? "Moist conditions slow the spread, but peat can smoulder underground." : "Watch for drying through the day."),
      dry ? "warn" : "");
  } else {
    add("clock", t("Wind and weather"), t("unknown"), t("No weather evidence yet: collect it before planning the approach."), "warn");
  }
  if (osm) {
    const road = osm.road;
    add("pin", t("Ground access"), road ? `${road.name || t("unnamed road")} · ${km(road.distance_m)}` : t("no road within {d}", { d: km(ROAD_FAR_M) }),
      road ? t("Nearest mapped road from OpenStreetMap; the last stretch may be a track or on foot.") : t("Plan access by river, on foot, or by air; OpenStreetMap shows no road nearby."),
      road && road.distance_m <= ROAD_FAR_M ? "" : "warn");
    const water = [osm.river, osm.lake].filter(Boolean).sort((a, b) => a.distance_m - b.distance_m)[0];
    add("target", t("Water source"), water ? `${water.name || t(water.kind === "river" ? "river" : "lake or pond")} · ${km(water.distance_m)}` : t("none within {d}", { d: km(ROAD_FAR_M) }),
      !water ? t("Bring water tanks, or request aerial water support.")
        : water.distance_m <= WATER_NEAR_M ? t("Close enough for a portable pump and hose lines.")
        : water.distance_m <= WATER_RELAY_M ? t("Reachable with a relay of pumps or water tanks.") : t("Far: water tanks or aerial support are more practical."),
      !water || water.distance_m > WATER_RELAY_M ? "warn" : "");
    const fs = osm.fire_station;
    add("shield", t("Nearest fire station"), fs ? `${fs.name || t("fire station")} · ${km(fs.distance_m)}` : t("none within {d}", { d: "30 km" }),
      fs ? t("From OpenStreetMap; confirm the post is staffed.") : t("Coordinate with the nearest officers and village; the response may take long."), fs ? "" : "warn");
    const set = osm.settlement;
    if (set) add("pin", t("Nearest settlement"), `${set.name || t("settlement")} · ${km(set.distance_m)}`,
      set.distance_m <= SETTLEMENT_NEAR_M ? t("Within {d}: prepare to inform residents and keep their access open.", { d: km(SETTLEMENT_NEAR_M) }) : t("Outside {d}.", { d: km(SETTLEMENT_NEAR_M) }),
      set.distance_m <= SETTLEMENT_NEAR_M ? "warn" : "");
  } else {
    add("pin", t("Access, water, and fire station"), t("unknown"), t("No OpenStreetMap evidence yet: collect it to see roads, rivers, and fire stations."), "warn");
  }
  const scale = [t("{n} hotspot(s), max FRP {frp} MW", { n: inc.hotspot_count, frp: fmtNum(inc.max_frp) })];
  if (sat?.status === "ok" && sat.burned_area_ha != null) scale.push(t("{ha} ha burned (Sentinel-2)", { ha: fmtNum(sat.burned_area_ha) }));
  else if (sat?.status === "hazy") scale.push(t("burned area not claimed (haze)"));
  add("flame", t("Size and intensity"), scale.join(" · "),
    isRecent(inc.last_seen) ? t("Detected again in the last 6 h: the fire is likely still active.") : t("No detection in the last 6 h; check on site whether it still burns."),
    isRecent(inc.last_seen) || (inc.max_frp ?? 0) >= 40 ? "warn" : "");
  return { rows, spread };
}

function suppressionContent(inc) {
  const { rows, spread } = suppressionFacts(inc);
  const tri = inc.triage && !inc.triage.hidden ? inc.triage : null;
  const facts = el("div", { class: "sup-list" }, rows.map((r) => el("div", { class: `sup-row ${r.tone}` },
    el("span", { class: "sup-icon" }, svg(r.icon, "icon icon-sm")),
    el("div", { class: "sup-body" }, el("div", { class: "sup-head" }, el("span", { class: "sup-title" }, r.title), el("strong", { class: "sup-value" }, r.value)),
      r.note ? fold(el("p", { class: "sup-note" }, r.note)) : null))));
  const ai = tri ? el("div", { class: "sup-ai" },
    el("div", { class: "legend-label" }, t("From the AI triage"), aiBadge("Muse Spark 1.3"), info(t("Written by Muse Spark 1.3 from the stored evidence and checked by the evidence gate. A human verifier decides."))),
    el("div", { class: "sup-risk" }, el("span", {}, t("Risk")), el("div", { class: "sup-bar" }, el("span", { style: `width:${Math.max(0, Math.min(100, tri.risk?.score ?? 0))}%` })),
      el("strong", { class: "num" }, `${Math.round(tri.risk?.score ?? 0)}/100`)),
    tri.risk?.drivers?.length ? el("div", { class: "chips" }, modelList(tri.risk.drivers, tri.risk.drivers_id).map((d) => el("span", { class: "pill" }, d))) : null,
    tri.risk?.spread_direction_deg != null ? el("p", { class: "sup-note" }, t("AI spread direction: toward the {dir} ({deg} deg)", { dir: compass(tri.risk.spread_direction_deg), deg: Math.round(tri.risk.spread_direction_deg) })) : null,
    tri.recommended_actions?.length ? el("ol", { class: "sup-actions" }, modelList(tri.recommended_actions, tri.recommended_actions_id).map((a) => el("li", {}, a))) : null)
    : el("p", { class: "empty" }, t("The AI's risk score and recommended actions appear here after AI triage."));
  const drone = el("div", { class: "sup-drone" });
  api(`/api/drone/missions?incident_id=${encodeURIComponent(inc.id)}`).then((m) => {
    const mine = (m.items || []).filter((x) => x.incident_id === inc.id);
    if (!mine.length) return;
    fill(drone, mine.map((x) => el("div", { class: "sup-row" }, el("span", { class: "sup-icon" }, svg("target", "icon icon-sm")),
      el("div", { class: "sup-body" }, el("div", { class: "sup-head" }, el("span", { class: "sup-title" }, t("Drone survey"), " ", el("span", { class: "pill pill-sim" }, "SIMULASI"), " ", aiBadge("Muse Glimmer + SAM 3.1"))),
        el("p", { class: "sup-note" }, t("{u} of {n} frames sent by the onboard filter; SAM 3.1 measured {ha} ha burned on the frames it could mask. Use it to check the burn extent before sending a crew.", {
          u: x.uplinked, n: x.frames, ha: x.sam_burned_ha == null ? t("n/a") : fmtNum(x.sam_burned_ha) }))))));
  }).catch(() => {});
  return [
    facts, drone,
    spread != null ? el("div", { class: "actions" }, mapToggle("spread", "Show spread on map", () => showSpread(inc, spread), () => showSpread(null))) : null,
    ai,
  ];
}

/* Likely spread on the map: an arrow from the incident centre, 3 km long, downwind. */
function showSpread(inc, deg) {
  state.mapReady?.then(() => {
    if (!map) return;
    ["spread-line", "spread-head"].forEach((id) => map.getLayer(id) && map.removeLayer(id));
    if (map.getSource("spread")) map.removeSource("spread");
    if (!inc) return;
    const R = 6371000, d = 3000, b = (deg * Math.PI) / 180, lat1 = (inc.lat * Math.PI) / 180, lon1 = (inc.lon * Math.PI) / 180;
    const dest = (dist, brg) => {
      const lat2 = Math.asin(Math.sin(lat1) * Math.cos(dist / R) + Math.cos(lat1) * Math.sin(dist / R) * Math.cos(brg));
      const lon2 = lon1 + Math.atan2(Math.sin(brg) * Math.sin(dist / R) * Math.cos(lat1), Math.cos(dist / R) - Math.sin(lat1) * Math.sin(lat2));
      return [(lon2 * 180) / Math.PI, (lat2 * 180) / Math.PI];
    };
    const tip = dest(d, b);
    const back = (side) => {
      const [x, y] = dest(d - 450, b + side * 0.12);
      return [x, y];
    };
    map.addSource("spread", { type: "geojson", data: { type: "FeatureCollection", features: [
      { type: "Feature", properties: { k: "line" }, geometry: { type: "LineString", coordinates: [[inc.lon, inc.lat], tip] } },
      { type: "Feature", properties: { k: "head" }, geometry: { type: "Polygon", coordinates: [[tip, back(1), back(-1), tip]] } }] } });
    map.addLayer({ id: "spread-line", type: "line", source: "spread", filter: ["==", ["get", "k"], "line"], paint: { "line-color": "#f04438", "line-width": 4, "line-dasharray": [2, 1] } });
    map.addLayer({ id: "spread-head", type: "fill", source: "spread", filter: ["==", ["get", "k"], "head"], paint: { "fill-color": "#f04438" } });
    map.fitBounds([[Math.min(inc.lon, tip[0]), Math.min(inc.lat, tip[1])], [Math.max(inc.lon, tip[0]), Math.max(inc.lat, tip[1])]], { padding: 120, duration: 800, maxZoom: 13 });
  });
}

/* Share: a public link whose preview (title, summary, static map image) WhatsApp and Facebook show. */
async function openShare(inc) {
  if (document.querySelector(".share-card")) return;
  let s;
  try {
    s = await track(inc.id, "share", t("Preparing the share"), () => api(`/api/incidents/${encodeURIComponent(inc.id)}/share?lang=${lang()}`));
  } catch (e) {
    return toast(t("Not shareable yet: {e}", { e: e.message }), "alert");
  }
  if (!s) return;
  const text = `${s.title}\n${s.description}\n${s.url}`;
  const img = el("img", { class: "share-img", src: s.image, alt: s.title });
  const copy = async (value, done) => {
    try { await navigator.clipboard.writeText(value); toast(t(done), "check"); } catch { toast(value); }
  };
  const actions = [
    navigator.share ? el("button", { class: "btn btn-primary", type: "button", onclick: () => navigator.share({ title: s.title, text: `${s.title}\n${s.description}`, url: s.url }).catch(() => {}) },
      svg("share", "icon icon-sm"), t("Share...")) : null,
    el("a", { class: "btn btn-wa", href: `https://wa.me/?text=${encodeURIComponent(text)}`, target: "_blank", rel: "noopener" }, t("WhatsApp")),
    el("a", { class: "btn", href: `https://www.facebook.com/sharer/sharer.php?u=${encodeURIComponent(s.url)}`, target: "_blank", rel: "noopener" }, t("Facebook")),
    el("button", { class: "btn", type: "button", onclick: () => copy(s.url, "Link copied.") }, t("Copy link")),
    el("button", { class: "btn", type: "button", onclick: () => copy(text, "Summary and link copied.") }, t("Copy text")),
    el("a", { class: "btn", href: s.image, download: `${inc.id}.png` }, t("Download image")),
  ];
  const dlg = modal({
    title: t("Share {id}", { id: inc.id }),
    body: [img, el("p", { class: "share-title" }, s.title), el("p", { class: "muted", style: "margin:0;font-size:13px" }, s.description),
      el("p", { class: "muted", style: "margin:0;font-size:12px" }, t("Only public information is shared: rounded location, satellite hotspots, status, and the nearest settlement. Citizen photos and text are never included."))],
    actions: [el("div", { class: "share-actions" }, actions)],
  });
  dlg.card.classList.add("share-card");
}

/* The brief shows the reader's language first and the other one below it. */
function briefParts(triage) {
  // The model writes both; the reader sees the language they chose.
  return [el("p", { class: "brief" }, triage.dispatch_brief[lang()] || triage.dispatch_brief.en)];
}

/* An action inside the detail view (propose a drone flight, check Sentinel-2, decide) reloads the same incident.
   That reload keeps the reader where they were: scroll position and which sections are open. */
const sectionKey = (s) => s.querySelector(".section-title")?.childNodes[0]?.textContent || "";
function saveDetailView() {
  const view = $("#detail-view");
  return { top: view.closest(".panel")?.scrollTop ?? 0,
    open: new Map([...view.querySelectorAll(".section")].map((s) => [sectionKey(s), s.dataset.open])) };
}
function restoreDetailView(saved) {
  if (!saved) return;
  const view = $("#detail-view");
  view.querySelectorAll(".section").forEach((s) => {
    const open = saved.open.get(sectionKey(s));
    if (open == null) return;
    s.dataset.open = open;
    s.querySelector(".section-head")?.setAttribute("aria-expanded", open);
  });
  const panel = view.closest(".panel");
  if (panel) panel.scrollTop = saved.top;
}

async function openIncident(id) {
  stopFlight();  // a running flight belongs to the view being replaced; the new view starts it again if it is live
  const view = $("#detail-view");
  const saved = !view.hidden && view.dataset.incident === id ? saveDetailView() : null;
  state.selected = id;
  crumbs([{ label: t("Incidents"), onclick: () => renderList() }, { label: id }]);
  $("#list-view").hidden = true;
  view.hidden = false;
  view.dataset.incident = id;
  if (!saved) {
    renderDetailSkeleton();
    // Overlays belong to the incident they were shown for.
    showBurnMask(null);
    showFootprints(null);
    showFlightPlan(null);
    showSpread(null);
  }
  if (map?.getLayer("incident-selected")) map.setFilter("incident-selected", ["==", ["get", "id"], id]);
  let inc;
  try {
    inc = await api(`/api/incidents/${id}`);
  } catch (e) {
    fill($("#detail-view"), el("p", { class: "empty" }, t("Could not load {id}: {e}", { id, e: e.message })));
    return;
  }
  // The list entry follows what was just read, so the list, map ring, and banner agree with the view.
  const listed = state.incidents.find((i) => i.id === inc.id);
  if (listed && listed.status !== inc.status) { listed.status = inc.status; setMapData(); }
  // The list can render before the map exists; the map then opens on the default view. A reload stays put.
  if (!saved) map?.flyTo({ center: [inc.lon, inc.lat], zoom: Math.max(map.getZoom(), 10.5), speed: 1.4 });
  const frpPct = inc.max_frp == null ? null : Math.min(100, (inc.max_frp / HEAT_MAX) * 100);
  const recent = isRecent(inc.last_seen);

  const head = el("div", { class: "card detail-head" },
    el("div", { class: "detail-title" }, el("h3", {}, inc.id),
      el("div", { class: "detail-title-right" }, el("span", { class: "status", dataset: { status: inc.status } }, statusLabel(inc.status)),
        el("button", { class: "btn btn-icon", type: "button", title: t("Share"), "aria-label": t("Share"), dataset: { busy: "share" }, onclick: () => openShare(inc) }, svg("share", "icon icon-sm")))),
    el("div", { class: "tags" },
      inc.simulation ? el("span", { class: "pill pill-sim" }, "SIMULASI") : null,
      recent ? el("span", { class: "pill pill-live" }, el("span", { class: "pulse" }), t("Active in last 6 h")) : null,
      el("span", { class: "pill" }, svg(inc.trigger === "hotspot_cluster" ? "satellite" : "camera", "icon icon-sm"), t(TRIGGER_LABEL[inc.trigger] || inc.trigger)),
    ),
    el("div", { class: "gauge" },
      el("div", { class: "gauge-row" }, el("span", { style: "display:flex;gap:4px;align-items:center" }, t("Max fire radiative power"), info(t("Highest FRP among the cluster's hotspots, as measured by the satellite."))), el("strong", { class: "num" }, fmtFrp(inc.max_frp))),
      el("div", { class: "gauge-track" }, frpPct == null ? null : el("span", { class: "gauge-mark", style: `left:${frpPct}%` })),
    ),
    el("div", { class: "stats-grid" },
      el("div", { class: "stat" }, el("div", { class: "stat-v num" }, fmtNum(inc.hotspot_count)), el("div", { class: "stat-l" }, t("Hotspots"))),
      el("div", { class: "stat" }, el("div", { class: "stat-v num" }, fmtNum(inc.high_conf_count)), el("div", { class: "stat-l" }, t("High confidence"))),
      el("div", { class: "stat" }, el("div", { class: "stat-v num" }, inc.sum_frp == null ? t("n/a") : fmtNum(Math.round(inc.sum_frp))), el("div", { class: "stat-l" }, t("Total FRP, MW"))),
    ),
  );

  const triage = inc.triage;
  const gate = inc.gate;
  const progress = progressCard(inc);
  const sections = [
    section({ icon: "flame", title: t("Suppression outlook"), open: true, extraClass: "sup-section",
      tip: t("What the stored evidence means for a ground crew: likely spread and the side to approach from, access, water, the nearest fire station, how dry it is, and the size. Computed by code with the thresholds named here: within 1 km of water a portable pump reaches, within 3 km a relay of pumps or tanks; no road within 10 km means another way in; dry is humidity under 50% with under 1 mm of rain in 24 h, humid is 80% or more or 5 mm of rain. Guidance for the people who decide, not an order."),
      content: suppressionContent(inc) }),
    section({ icon: "pin", title: t("Overview"), open: true, content: el("dl", { class: "kv" },
      el("dt", {}, t("Location")), el("dd", { class: "num" }, `${inc.lat.toFixed(3)}, ${inc.lon.toFixed(3)}`),
      el("dt", {}, t("First seen")), el("dd", {}, fmtTime(inc.first_seen)),
      el("dt", {}, t("Last seen")), el("dd", {}, fmtTime(inc.last_seen)),
    ) }),
    section({ icon: "file", title: [t("Dispatch brief"), aiBadge("Muse Spark 1.3")], open: true, live: inc.status === "triaging",
      tip: t("Written by Muse Spark 1.3 from the claims below, then checked by the evidence gate and a second model. Nothing is sent to anyone automatically."),
      content: [
        triage?.hidden ? el("p", { class: "empty" }, t("Built on citizen evidence: shown after a verifier approves."))
          : triage ? [briefParts(triage),
                      triage.dispatch_brief.disputed ? el("span", { class: "dispute" }, svg("alert", "icon icon-sm"), t("Brief disputed: "), triage.dispatch_brief.disputed.join(" ")) : null,
                      triage.uncertainties?.length ? el("div", { class: "gate-list" }, el("div", { class: "legend-label" }, t("Uncertainties")), modelList(triage.uncertainties, triage.uncertainties_id).map((u) => el("div", { class: "ev" }, u))) : null]
          : el("p", { class: "empty" }, t(inc.status === "triaging" ? "Triage is running..." : "Not triaged yet.")),
        // Before the first run the button lives in the progress card, with the reason to press it.
        triage || inc.status === "triaging" ? triageButton(inc) : null,
      ] }),
    section({ icon: "check", title: [t("Claims"), aiBadge("Muse Spark 1.3")], count: triage?.claims?.length ?? 0, open: !!triage?.claims?.length,
      tip: t("Each claim cites stored evidence. Click an ID to see it. A disputed claim was flagged by the critique model as not supported by its evidence. Claims are written in English by the model."),
      content: claimRows(triage) }),
    section({ icon: "shield", title: t("Evidence gate"), tip: t("Deterministic checks between the model and the verifier: schema, evidence existence, numbers against tool results, area from real masks, location, and content rules. Every change is listed."),
      content: gateRows(gate) }),
    section({ icon: "satellite", title: t("Sentinel-2 burn scar"), count: burnedGist(inc),
      tip: t("Before and after images from Sentinel-2. dNBR is the drop in the normalized burn ratio; burned hectares count 20 m pixels at dNBR 0.27 or more, computed by code. Under heavy haze no hectares are claimed."),
      content: satelliteContent(inc) }),
    section({ icon: "activity", title: t("Evidence"), count: inc.evidence.length, tip: t("Every tool result is stored with a numbered ID. Claims link to these IDs. Citizen photos, voice, and text are shown to verifiers only."),
      content: [evidenceRows(inc), token() && missingSources(inc).length
        ? el("div", { class: "actions" }, el("button", { class: "btn", type: "button", dataset: { busy: "collect" }, onclick: () => collectNow(inc.id) }, svg("activity", "icon icon-sm"), t("Collect missing evidence")))
        : null] }),
    section({ icon: "flame", title: t("Hotspots"), count: inc.hotspots.length, live: recent, tip: t("NASA FIRMS detections. Confidence l, n, h means low, nominal, high."),
      content: [
        el("table", { class: "hs-table" },
          el("thead", {}, el("tr", {}, el("th", {}, t("Time")), el("th", {}, t("Sat")), el("th", {}, t("Conf")), el("th", {}, "FRP"))),
          el("tbody", {}, inc.hotspots.slice(0, 25).map((h) => el("tr", {},
            el("td", {}, fmtTime(h.acquired_at)), el("td", {}, h.satellite), el("td", {}, h.confidence),
            el("td", { class: "num" }, el("span", { class: "frp-dot", style: `background:${heatColor(h.frp)}` }), fmtFrp(h.frp))))),
        ),
        inc.hotspots.length > 25 ? el("p", { class: "empty" }, t("Showing 25 of {n}.", { n: inc.hotspots.length })) : null,
      ] }),
    section({ icon: "gavel", title: t("Verdicts"), count: inc.verdicts.length,
      content: inc.verdicts.length ? inc.verdicts.map((d) => el("div", { class: "ev" }, el("span", { class: "ev-id" }, t(VERDICT_LABEL[d.decision] || d.decision)),
        el("span", {}, `${d.channel} · ${fmtTime(d.created_at)}`, d.note ? ` · "${d.note}"` : "")))
                                   : el("p", { class: "empty" }, t("No verdict yet.")) }),
    section({ icon: "check", title: t("Verifier decision"), open: true, tip: t("Approve or reject is recorded as a label. Labels calibrate the evidence gate thresholds."), content: verifierContent(inc) }),
  ];
  fill($("#detail-view"), head, progress, sections);
  refreshBusy();
  restoreDetailView(saved);
  // Drone plans and the simulated replay load after the main view so they never delay it.
  Promise.all([proposalSection(inc), droneSection(inc)]).then(([plan, sim]) => {
    if (state.selected !== inc.id) return;
    const sat = [...document.querySelectorAll("#detail-view .section")].find((x) => x.innerText.startsWith(t("Sentinel-2 burn scar")));
    const after = (node, s) => (node ? node.after(s) : $("#detail-view").append(s));
    if (plan) after(sat, plan);
    if (sim) after(plan || sat, sim);
    if (plan?.dataset.activePlan === "true") {
      document.querySelectorAll('#detail-view .progress-card [data-busy="drone"]').forEach((b) => b.replaceWith(
        el("button", { class: "btn", type: "button", onclick: () => plan.scrollIntoView({ behavior: "smooth", block: "start" }) }, svg("target", "icon icon-sm"), t("See the drone survey plan"))));
    }
    restoreDetailView(saved);  // again: these sections sit above much of the view and shift it
  });
}

async function main() {
  applyStatic(t);
  hydrateIcons();
  $("#lang-slot").replaceWith(langSwitch(el));
  $("#verifier-slot").replaceWith(verifierControl(t));
  siteFooter($("#site-footer"), lang());
  renderViewControls();
  renderSources();
  loadSources();
  probeGibs();
  setInterval(loadSources, SOURCES_REFRESH_MS);
  $("#panel-toggle").addEventListener("click", () => applyPanel(!document.body.classList.contains("panel-hidden")));
  applyPanel(localStorage.getItem(PANEL_KEY) === "1");
  // Start every request at once. The list and KPIs render as soon as their data lands;
  // the map draws its layers whenever both the map and the data are ready.
  const statsP = api("/api/stats").catch(() => null);
  const incP = api("/api/incidents").catch((e) => ({ items: [], error: e }));
  const hsP = api("/api/hotspots").catch(() => ({ items: [] }));
  const mapP = initMap();
  state.mapReady = mapP;

  renderKpis();
  renderLegend();
  loadControl();
  bindListControls();
  crumbs([{ label: t("Incidents") }]);
  fill($("#incident-list"), Array.from({ length: 7 }, skeletonCard));

  const stats = await statsP;
  state.stats = stats;
  if (stats?.newest_acquisition) state.recentSince = new Date(new Date(stats.newest_acquisition).getTime() - RECENT_HOURS * 3600e3).toISOString().replace(".000Z", "+00:00");
  renderKpis();
  performance.mark("kpis-rendered");

  const inc = await incP;
  state.incidents = inc.items;
  if (inc.error) fill($("#incident-list"), el("li", { class: "empty" }, t("Could not load incidents: {e}", { e: inc.error.message })));
  else if (!state.selected) renderList();
  performance.mark("list-rendered");

  // A shared link (/i/<id>) lands here with ?incident=<id>: open it once the list is in.
  const wanted = new URLSearchParams(location.search).get("incident");
  if (wanted && !inc.error) {
    history.replaceState(null, "", location.pathname + location.hash);
    openIncident(wanted);
  }

  const [hs] = await Promise.all([hsP, mapP]);
  state.hotspots = hs.items;
  setMapData();
  performance.mark("map-data-rendered");
}

main();
