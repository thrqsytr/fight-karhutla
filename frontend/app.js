import { $, el, hydrateIcons, info, modal, section, siteFooter, svg, toast } from "/ui.js";
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
    label: "Aerosol optical depth (MODIS)", tip: "How much haze, smoke, or dust blocks sunlight. Higher is thicker. Gaps are clouds or no data." },
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
function renderKpis() {
  const s = state.stats;
  const tile = (icon, value, label, tip, heat = false) =>
    el("div", { class: "kpi" }, el("span", { class: `kpi-icon ${heat ? "heat" : ""}` }, svg(icon, "icon icon-sm")),
      el("div", {}, s ? el("div", { class: "kpi-value num" }, value) : el("span", { class: "sk" }), el("div", { class: "kpi-label" }, t(label), info(t(tip)))));
  fill($("#kpis"),
    tile("list", s && fmtNum(s.incidents), "Incidents", "Hotspot clusters currently stored. Most are a single hotspot; filter for 3+ to focus."),
    tile("dots", s && fmtNum(s.incidents_multi_hotspot), "With 3+ hotspots", "Clusters with at least three detections, a stronger thermal signal."),
    tile("flame", s && fmtNum(s.hotspots_last_24h), "Hotspots, 24 h", "FIRMS detections in the 24 hours before the newest acquisition.", true),
    tile("target", s && fmtNum(s.high_conf_last_24h), "High confidence", "VIIRS high confidence (h) or MODIS 80 and above in the same window.", true),
    tile("clock", s && s.newest_acquisition ? fmtTime(s.newest_acquisition) : t("n/a"), "Newest pass", "Acquisition time of the newest satellite detection."),
  );
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
  }));
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

function setMapData() {
  if (!map) return;
  const hs = geo(state.hotspots, (h) => ({ id: h.id, frp: h.frp, conf: h.confidence, at: h.acquired_at, recent: isRecent(h.acquired_at) ? 1 : 0 }));
  const inc = geo(state.incidents, (i) => ({ id: i.id, n: i.hotspot_count, frp: i.max_frp, color: (STATUS[i.status] || STATUS.pending_triage).color, last: i.last_seen }));
  if (map.getSource("hotspots")) {
    map.getSource("hotspots").setData(hs);
    map.getSource("incidents").setData(inc);
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
  return el("li", { class: `card incident ${state.selected === i.id ? "active" : ""} ${state.picking ? "is-picking" : ""} ${on ? "is-picked" : ""}`, onclick: act, tabindex: "0",
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
  );
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

function evidenceRows(inc) {
  const rows = inc.evidence.map((e) => {
    const extra = [];
    if (e.type === "report") extra.push(el("p", { class: "brief", style: "margin-top:4px" }, e.payload.text));
    if (e.type === "voice") extra.push(el("div", { style: "margin-top:6px" }, voiceButton(e.payload.media_id)),
      el("p", { class: "muted", style: "margin:4px 0 0;font-size:12px" }, e.payload.transcript ? t("Transcript: {t}", { t: e.payload.transcript }) : t("Transcript pending")));
    // The report summary repeats the citizen's text, so show the full text once instead.
    const head = e.type === "report" ? `${e.type} · ${e.source}` : `${e.type} · ${e.source}: ${e.summary}`;
    return el("div", { class: "ev", dataset: { ev: e.id } }, el("span", { class: "ev-id" }, e.id), el("div", { style: "flex:1;min-width:0" }, head, extra));
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
    const b = el("button", { class: "btn", type: "button" }, svg("satellite", "icon icon-sm"), t(ev ? "Check for a newer pair" : "Find Sentinel-2 images"));
    b.onclick = () => fetchSatellite(inc.id, b);
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
  const showOnMap = el("button", { class: "btn", type: "button", onclick: () => { showBurnMask(inc, ev); map?.fitBounds([p.image_corners[3], p.image_corners[1]], { padding: 60, duration: 800 }); } },
    svg("layers", "icon icon-sm"), t("Show scar on map"));
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

/* Drone survey proposals: a plan from the agent or a verifier, flown only after a human approves. */
const PROPOSAL_LABEL = { proposed: "Proposed", approved: "Approved", rejected: "Rejected", cancelled: "Cancelled" };
const ACTIVE_PLAN = ["proposed", "approved"];
const EXAMPLE_SURVEY = "INC-20261025-0407";  // the incident with a simulated survey (frames, Glimmer, SAM)

/* Simulated flight: a drone marker flies the approved route on the map with its camera footprint, faster than
   real time. Only the route is simulated; a plan has no imagery until a pilot flies it. */
const FLIGHT_PLAYBACK_S = 24;
let flight = null;
const FLY_LAYERS = ["drone-fly-foot-fill", "drone-fly-foot-line", "drone-fly-shots", "drone-fly-pos"];

function stopFlight() {
  if (!flight) return;
  cancelAnimationFrame(flight.raf);
  const done = flight.onStop;
  flight = null;
  done?.(false);
  if (!map) return;
  FLY_LAYERS.forEach((id) => map.getLayer(id) && map.removeLayer(id));
  ["drone-fly", "drone-fly-shots"].forEach((id) => map.getSource(id) && map.removeSource(id));
}

function flyPlan(p, statusEl, onStop) {
  stopFlight();
  showFlightPlan(p);
  state.mapReady?.then(() => {
    if (!map) return;
    const plan = p.plan;
    const wps = plan.waypoints;
    const [fw, fh] = plan.footprint_m;  // across track, along track
    const m = (a, b) => [(b.lon - a.lon) * 111320 * Math.cos((a.lat * Math.PI) / 180), (b.lat - a.lat) * 111320];
    const legs = wps.slice(1).map((w, i) => Math.hypot(...m(wps[i], w)));
    const total = legs.reduce((x, y) => x + y, 0);
    const realS = plan.estimate.flight_minutes * 60;
    const empty = { type: "FeatureCollection", features: [] };
    map.addSource("drone-fly", { type: "geojson", data: empty });
    map.addSource("drone-fly-shots", { type: "geojson", data: empty });
    map.addLayer({ id: "drone-fly-shots", type: "circle", source: "drone-fly-shots", paint: { "circle-radius": 3, "circle-color": "#7a5af8", "circle-stroke-color": "#fff", "circle-stroke-width": 1 } });
    map.addLayer({ id: "drone-fly-foot-fill", type: "fill", source: "drone-fly", filter: ["==", ["get", "kind"], "foot"], paint: { "fill-color": "#7a5af8", "fill-opacity": 0.18 } });
    map.addLayer({ id: "drone-fly-foot-line", type: "line", source: "drone-fly", filter: ["==", ["get", "kind"], "foot"], paint: { "line-color": "#5b3fd6", "line-width": 1.5 } });
    map.addLayer({ id: "drone-fly-pos", type: "circle", source: "drone-fly", filter: ["==", ["get", "kind"], "pos"], paint: { "circle-radius": 7, "circle-color": "#5b3fd6", "circle-stroke-color": "#fff", "circle-stroke-width": 2 } });
    const start = performance.now();
    const mmss = (s) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
    const step = (now) => {
      if (!flight) return;
      const f = Math.min(1, (now - start) / (FLIGHT_PLAYBACK_S * 1000));
      let d = f * total, i = 0;
      while (i < legs.length - 1 && d > legs[i]) { d -= legs[i]; i += 1; }
      const a = wps[i], b = wps[Math.min(i + 1, wps.length - 1)];
      const k = legs[i] ? Math.min(1, d / legs[i]) : 1;
      const lon = a.lon + (b.lon - a.lon) * k, lat = a.lat + (b.lat - a.lat) * k;
      const [vx, vy] = m(a, b);
      const len = Math.hypot(vx, vy) || 1;
      const ux = vx / len, uy = vy / len;  // along track, metres
      const toDeg = (x, y) => [lon + x / (111320 * Math.cos((lat * Math.PI) / 180)), lat + y / 111320];
      const corner = (sa, sb) => toDeg(ux * sa * fh / 2 + uy * sb * fw / 2, uy * sa * fh / 2 - ux * sb * fw / 2);
      const foot = [corner(1, 1), corner(1, -1), corner(-1, -1), corner(-1, 1), corner(1, 1)];
      const shots = f >= 1 ? wps.length : i + (k >= 1 ? 2 : 1);
      map.getSource("drone-fly")?.setData({ type: "FeatureCollection", features: [
        { type: "Feature", properties: { kind: "foot" }, geometry: { type: "Polygon", coordinates: [foot] } },
        { type: "Feature", properties: { kind: "pos" }, geometry: { type: "Point", coordinates: [lon, lat] } }] });
      map.getSource("drone-fly-shots")?.setData({ type: "FeatureCollection", features: wps.slice(0, shots).map((w) =>
        ({ type: "Feature", properties: {}, geometry: { type: "Point", coordinates: [w.lon, w.lat] } })) });
      if (statusEl) statusEl.textContent = t("Simulated flight: frame {k} of {n}, {done} of {total} flight time", { k: shots, n: wps.length, done: mmss(f * realS), total: mmss(realS) });
      if (f < 1) flight.raf = requestAnimationFrame(step);
      else { const done = flight.onStop; flight.onStop = null; done?.(true); }
    };
    flight = { raf: requestAnimationFrame(step), onStop };
  });
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

async function proposeDrone(inc, btn) {
  if (btn) btn.disabled = true;
  try {
    const r = await api(`/api/incidents/${inc.id}/drone-proposals`, { method: "POST", body: JSON.stringify({ reason: t("Planned by a verifier from the incident view.") }) });
    toast(t(r.created ? "Drone survey proposed. Approve it to send it to a pilot." : "An active plan already exists; showing it."), "check");
    openIncident(inc.id);
  } catch (e) {
    toast(t("Not proposed: {e}", { e: e.message }), "alert");
    if (btn) btn.disabled = false;
  }
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
  const flightStatus = el("p", { class: "muted flight-status", hidden: true });
  const play = el("button", { class: "btn btn-primary", type: "button" });
  const playing = (on) => {
    fill(play, svg(on ? "stop" : "play", "icon icon-sm"), t(on ? "Stop" : "Play simulated flight"));
    play.dataset.on = String(on);
  };
  playing(false);
  play.onclick = () => {
    if (play.dataset.on === "true") { stopFlight(); return; }
    flightStatus.hidden = false;
    playing(true);
    flyPlan(p, flightStatus, () => playing(false));
  };
  return el("div", { class: `proposal ${p.status}` },
    el("div", { class: "sat-head" },
      el("span", { class: `proposal-pill ${p.status}` }, t(PROPOSAL_LABEL[p.status])),
      el("span", { class: "muted", style: "font-size:12px" }, t(p.source === "agent" ? "proposed by the AI agent" : "proposed by a verifier"), " · ", fmtTime(p.created_at))),
    p.reason ? el("p", { class: "proposal-reason" }, `"${p.reason}"`) : null,
    el("dl", { class: "kv" },
      el("dt", {}, t("Area")), el("dd", {}, `${fmtNum(plan.area_km2)} km² · ${t("{lines} lines x {n} frames, {alt} m above ground, GSD {gsd} m", { lines: plan.lines, n: plan.frames_per_line, alt: plan.altitude_agl_m, gsd: fmtNum(plan.gsd_m) })}`),
      el("dt", {}, t("Flight")), el("dd", {}, t("about {min} min, {km} km path, {b} battery(ies)", { min: fmtNum(est.flight_minutes), km: fmtNum(est.path_km), b: est.batteries }))),
    p.status === "approved" ? el("p", { class: "muted", style: "margin:0;font-size:12px" }, t("Approved {when}. Waiting for a pilot: no drone is connected to this prototype. The simulated survey of INC-20261025-0407 shows what a flight returns.", { when: fmtTime(p.decided_at) })) : null,
    p.status === "approved" ? [flightStatus, el("p", { class: "muted", style: "margin:0;font-size:11px" }, t("Flight replayed about {x} times faster. Only the route is simulated: this plan has no imagery until a pilot flies it.", { x: Math.round((est.flight_minutes * 60) / FLIGHT_PLAYBACK_S) }))] : null,
    p.note ? el("p", { class: "muted", style: "margin:0;font-size:12px" }, `${t("Note")}: ${p.note}`) : null,
    el("div", { class: "actions" },
      p.status === "approved" ? play : null,
      el("button", { class: "btn", type: "button", onclick: () => showFlightPlan(p) }, svg("layers", "icon icon-sm"), t("Show plan on map")),
      p.status === "approved" && inc.id !== EXAMPLE_SURVEY ? el("button", { class: "btn", type: "button", onclick: () => openIncident(EXAMPLE_SURVEY) }, svg("camera", "icon icon-sm"), t("See an example survey result")) : null,
      p.status === "approved" ? el("button", { class: "btn btn-ghost-danger", type: "button", onclick: () => decideProposal(inc, p, "cancel") }, svg("x", "icon icon-sm"), t("Cancel plan")) : null,
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
  const open = r.items.some((p) => ACTIVE_PLAN.includes(p.status));
  const btn = el("button", { class: "btn", type: "button" }, svg("target", "icon icon-sm"), t("Propose a drone survey"));
  btn.onclick = () => proposeDrone(inc, btn);
  return section({ icon: "target", title: t("Drone survey plan"), count: r.count, open: open,
    tip: t("A mapping flight planned over the hotspots: lawnmower lines at 120 m above ground. The AI agent may propose one when independent evidence is missing; only a verifier can approve it. Planning is free."),
    content: [r.items.length ? r.items.map((p) => proposalCard(inc, p)) : el("p", { class: "empty" }, t("No drone survey planned.")),
      open ? el("p", { class: "muted", style: "margin:0;font-size:12px" }, t("One active plan per incident. Cancel the approved plan to make a new one."))
        : el("div", { class: "actions" }, btn)] });
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
    el("div", { class: "actions" }, el("button", { class: "btn", type: "button", onclick: () => showFootprints(m) }, svg("layers", "icon icon-sm"), t("Show footprints on map"))),
  ];
}

async function droneSection(inc) {
  const list = await api(`/api/drone/missions?incident_id=${encodeURIComponent(inc.id)}`).catch(() => null);
  if (!list?.count) return null;
  const m = await api(`/api/drone/missions/${encodeURIComponent(list.items[0].mission_id)}`).catch(() => null);
  if (!m) return null;
  return section({ icon: "target", title: t("Drone survey (simulation)"), open: false,
    tip: t("A simulated mapping flight: Muse Glimmer at the ground station picks which frames to send, SAM 3.1 segments them, and code turns masks into hectares from the telemetry."),
    content: droneContent(m) });
}

async function fetchSatellite(id, btn) {
  btn.disabled = true;
  const label = btn.lastChild.textContent;
  btn.lastChild.textContent = t("Searching Sentinel-2...");
  try {
    const r = await api(`/api/incidents/${id}/satellite`, { method: "POST" });
    if (r.evidence_id) { toast(t(r.reused ? "Pair checked in the last 24 hours; showing it." : "Sentinel-2 pair stored as {id}.", { id: r.evidence_id }), "check"); openIncident(id); return; }
    toast(t("No pair: {why}", { why: r.reason || "unknown" }), "alert");
  } catch (e) {
    toast(t("Sentinel-2 failed: {e}", { e: e.message }), "alert");
  }
  btn.disabled = false;
  btn.lastChild.textContent = label;
}

async function collectNow(id, btn) {
  btn.disabled = true;
  try {
    const r = await api(`/api/incidents/${id}/enrich`, { method: "POST" });
    const failed = Object.entries(r.sources).filter(([, v]) => String(v).startsWith("failed")).map(([k]) => k);
    toast(failed.length ? t("Collected, but {list} failed and will be retried.", { list: failed.join(", ") }) : t("Evidence collected."), failed.length ? "alert" : "check");
    openIncident(id);
  } catch (e) {
    toast(t("Not collected: {e}", { e: e.message }), "alert");
    btn.disabled = false;
  }
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
      el("p", { class: "claim-text" }, c.text),
      el("div", { class: "claim-meta" }, c.evidence_ids.map(evidenceChip),
        c.disputed ? el("span", { class: "dispute" }, svg("alert", "icon icon-sm"), t("Disputed: "), c.disputed) : null)));
}

function gateRows(gate) {
  if (!gate) return [el("p", { class: "empty" }, t("No gate result yet."))];
  const pct = gate.grounding_rate == null ? t("n/a") : `${Math.round(gate.grounding_rate * 100)}%`;
  const rows = [el("div", { class: "stats-grid" },
    el("div", { class: "stat" }, el("div", { class: "stat-v num" }, pct), el("div", { class: "stat-l" }, t("Grounding rate"))),
    el("div", { class: "stat" }, el("div", { class: "stat-v num" }, `${gate.claims_passed}/${gate.claims_total}`), el("div", { class: "stat-l" }, t("Claims passed"))),
    el("div", { class: "stat" }, el("div", { class: "stat-v num" }, String(gate.critique?.disputed?.length ?? 0)), el("div", { class: "stat-l" }, t("Disputed"))))];
  const list = (label, items, fmt) => items?.length ? rows.push(el("div", { class: "gate-list" }, el("div", { class: "legend-label" }, t(label)), items.map((x) => el("div", { class: "ev" }, fmt(x))))) : null;
  list("Removed claims", gate.claims_dropped, (d) => `${d.text} (${d.reason})`);
  list("Corrections", gate.corrections, (c) => t("{where}: {claimed} replaced with {corrected}", { where: c.where, claimed: c.claimed, corrected: c.corrected ?? t("nothing") }) + (c.note ? ` (${c.note})` : ""));
  list("Content rule removals", gate.violations, (v) => `${v.where}, ${v.rule}: ${v.removed}`);
  list("Status changes", gate.downgrades, (d) => d);
  list("Not reviewed", gate.critique?.errors, (e) => e);
  rows.push(el("p", { class: "muted", style: "margin:0;font-size:12px" },
    t("Triaged {when} in {s} s, metered cost ${cost}. Critique: {who}.", {
      when: gate.triaged_at ? fmtTime(gate.triaged_at) : "", s: gate.seconds ?? "?", cost: (gate.cost_usd ?? 0).toFixed(4),
      who: (gate.critique?.providers || []).join(", ") || t("none") })));
  return rows;
}

/* Deliberate triage: pick incidents in the list or draw an area on the map, then confirm with the execution code. */
function batchLimit() { return state.control?.batch_limit ?? 5; }
function estCost(n) { return (n * (state.control?.est_cost_per_triage_usd ?? 0.055)).toFixed(2); }

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
        t("Estimated cost ${cost} (about ${each} each, metered after the run).", { cost: estCost(n), each: (state.control?.est_cost_per_triage_usd ?? 0.055).toFixed(3) }) + " " +
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
    hotspot: types.has("hotspot"), weather: types.has("weather"), osm: types.has("osm"), satellite: types.has("satellite"),
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
    el("span", {}, t("What you see is raw detection: hotspots and the evidence collected so far. No AI or human has judged it. The status will change after AI triage, and only a verifier can mark it verified."))));
  else headline = el("div", { class: "phead warn" }, svg("activity", "icon"), el("div", {}, el("strong", {}, t("AI recommendation: {s}", { s: statusLabel(inc.status) })),
    el("span", {}, tri.hidden ? t("Built on citizen evidence; details are shown after a verifier approves.")
      : t("Confidence {c}%, judged on {when}. Waiting for a human verifier to approve or reject.", { c: Math.round((tri.confidence ?? 0) * 100), when: triagedAt ? fmtTime(triagedAt) : "" }))));

  // What is missing, each with what fills it.
  const missing = [];
  const act = (label, icon, onclick, primary) => el("button", { class: `btn ${primary ? "btn-primary" : ""}`, type: "button", onclick }, svg(icon, "icon icon-sm"), t(label));
  if (!verdict) {
    if (!has.citizen && !has.satellite) missing.push({
      title: "Independent evidence", why: "A satellite hotspot is a heat signal, not a confirmed fire. To reach \"likely fire\" the AI needs a second, independent source: a photo, voice note, or report from the ground, or a burn scar in Sentinel-2 images.",
      actions: [
        act("Copy report link for the field", "camera", async () => {
          try { await navigator.clipboard.writeText(reportLink(inc)); toast(t("Link copied. Anyone near the site can send photos with it.")); }
          catch { toast(reportLink(inc)); }
        }),
        token() ? act("Find Sentinel-2 images", "satellite", (e) => fetchSatellite(inc.id, e.currentTarget)) : null,
        token() ? act("Propose a drone survey", "target", (e) => proposeDrone(inc, e.currentTarget)) : null,
      ] });
    if (!has.weather) missing.push({ title: "Weather", why: "Wind and rain decide how fast a fire spreads and which way smoke goes.",
      actions: [token() ? act("Collect missing evidence", "activity", (e) => collectNow(inc.id, e.currentTarget)) : null] });
    if (!has.osm) missing.push({ title: "Nearby settlements and water", why: "Distance to villages, roads, and water points from OpenStreetMap. The public OSM servers are often down; collection retries automatically.", actions: [] });
    if (tri?.status === "needs_more_info" || tri?.uncertainties?.length) missing.push({ title: "What the AI asked for", why: null,
      list: tri.uncertainties || [], actions: [] });
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
      missing.map((m) => el("div", { class: "pmiss" }, el("strong", {}, t(m.title)), m.why ? el("p", {}, t(m.why)) : null,
        m.list?.length ? el("ul", {}, m.list.map((u) => el("li", {}, u))) : null,
        m.actions.filter(Boolean).length ? el("div", { class: "actions" }, m.actions) : null))) : null,
    nextStep,
  );
}

/* Share: a public link whose preview (title, summary, static map image) WhatsApp and Facebook show. */
async function openShare(inc) {
  let s;
  try {
    s = await api(`/api/incidents/${encodeURIComponent(inc.id)}/share?lang=${lang()}`);
  } catch (e) {
    return toast(t("Not shareable yet: {e}", { e: e.message }), "alert");
  }
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
  const [first, second] = lang() === "id" ? ["id", "en"] : ["en", "id"];
  return [el("p", { class: "brief" }, triage.dispatch_brief[first]), el("p", { class: "brief-en" }, triage.dispatch_brief[second])];
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
  const view = $("#detail-view");
  const saved = !view.hidden && view.dataset.incident === id ? saveDetailView() : null;
  state.selected = id;
  crumbs([{ label: t("Incidents"), onclick: () => renderList() }, { label: id }]);
  $("#list-view").hidden = true;
  view.hidden = false;
  view.dataset.incident = id;
  if (!saved) renderDetailSkeleton();
  if (map?.getLayer("incident-selected")) map.setFilter("incident-selected", ["==", ["get", "id"], id]);
  let inc;
  try {
    inc = await api(`/api/incidents/${id}`);
  } catch (e) {
    fill($("#detail-view"), el("p", { class: "empty" }, t("Could not load {id}: {e}", { id, e: e.message })));
    return;
  }
  // The list can render before the map exists; the map then opens on the default view. A reload stays put.
  if (!saved) map?.flyTo({ center: [inc.lon, inc.lat], zoom: Math.max(map.getZoom(), 10.5), speed: 1.4 });
  const frpPct = inc.max_frp == null ? null : Math.min(100, (inc.max_frp / HEAT_MAX) * 100);
  const recent = isRecent(inc.last_seen);

  const head = el("div", { class: "card detail-head" },
    el("div", { class: "detail-title" }, el("h3", {}, inc.id),
      el("div", { class: "detail-title-right" }, el("span", { class: "status", dataset: { status: inc.status } }, statusLabel(inc.status)),
        el("button", { class: "btn btn-icon", type: "button", title: t("Share"), "aria-label": t("Share"), onclick: () => openShare(inc) }, svg("share", "icon icon-sm")))),
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
    section({ icon: "pin", title: t("Overview"), open: true, content: el("dl", { class: "kv" },
      el("dt", {}, t("Location")), el("dd", { class: "num" }, `${inc.lat.toFixed(3)}, ${inc.lon.toFixed(3)}`),
      el("dt", {}, t("First seen")), el("dd", {}, fmtTime(inc.first_seen)),
      el("dt", {}, t("Last seen")), el("dd", {}, fmtTime(inc.last_seen)),
    ) }),
    section({ icon: "file", title: t("Dispatch brief"), open: true, live: inc.status === "triaging",
      tip: t("Written by Muse Spark 1.3 from the claims below, then checked by the evidence gate and a second model. Nothing is sent to anyone automatically."),
      content: [
        triage?.hidden ? el("p", { class: "empty" }, t("Built on citizen evidence: shown after a verifier approves."))
          : triage ? [briefParts(triage),
                      triage.dispatch_brief.disputed ? el("span", { class: "dispute" }, svg("alert", "icon icon-sm"), t("Brief disputed: "), triage.dispatch_brief.disputed.join(" ")) : null,
                      triage.uncertainties?.length ? el("div", { class: "gate-list" }, el("div", { class: "legend-label" }, t("Uncertainties")), triage.uncertainties.map((u) => el("div", { class: "ev" }, u))) : null]
          : el("p", { class: "empty" }, t(inc.status === "triaging" ? "Triage is running..." : "Not triaged yet.")),
        // Before the first run the button lives in the progress card, with the reason to press it.
        triage || inc.status === "triaging" ? triageButton(inc) : null,
      ] }),
    section({ icon: "check", title: t("Claims"), count: triage?.claims?.length ?? 0, open: !!triage?.claims?.length,
      tip: t("Each claim cites stored evidence. Click an ID to see it. A disputed claim was flagged by the critique model as not supported by its evidence. Claims are written in English by the model."),
      content: claimRows(triage) }),
    section({ icon: "shield", title: t("Evidence gate"), tip: t("Deterministic checks between the model and the verifier: schema, evidence existence, numbers against tool results, area from real masks, location, and content rules. Every change is listed."),
      content: gateRows(gate) }),
    section({ icon: "satellite", title: t("Sentinel-2 burn scar"), open: inc.evidence.some((e) => e.type === "satellite"),
      tip: t("Before and after images from Sentinel-2. dNBR is the drop in the normalized burn ratio; burned hectares count 20 m pixels at dNBR 0.27 or more, computed by code. Under heavy haze no hectares are claimed."),
      content: satelliteContent(inc) }),
    section({ icon: "activity", title: t("Evidence"), count: inc.evidence.length, open: inc.evidence.length > 0, tip: t("Every tool result is stored with a numbered ID. Claims link to these IDs. Citizen photos, voice, and text are shown to verifiers only."),
      content: [evidenceRows(inc), token() ? el("div", { class: "actions" }, (() => { const b = el("button", { class: "btn", type: "button" }, svg("activity", "icon icon-sm"), t("Collect missing evidence")); b.onclick = () => collectNow(inc.id, b); return b; })()) : null] }),
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
  restoreDetailView(saved);
  // Drone plans and the simulated replay load after the main view so they never delay it.
  Promise.all([proposalSection(inc), droneSection(inc)]).then(([plan, sim]) => {
    if (state.selected !== inc.id) return;
    const sat = [...document.querySelectorAll("#detail-view .section")].find((x) => x.innerText.startsWith(t("Sentinel-2 burn scar")));
    const after = (node, s) => (node ? node.after(s) : $("#detail-view").append(s));
    if (plan) after(sat, plan);
    if (sim) after(plan || sat, sim);
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
