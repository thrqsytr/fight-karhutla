import { $, el, hydrateIcons, info, modal, section, svg, toast } from "/ui.js";
import { applyStatic, DASHBOARD_ID, langSwitch, lang, locale, makeT } from "/i18n.js";

const t = makeT(DASHBOARD_ID, "en");

// MapLibre (about 300 KB) loads in parallel with the data instead of blocking the first API request.
const MAPLIBRE_URL = "https://unpkg.com/maplibre-gl@6.16.0/dist/maplibre-gl.mjs";
let maplibregl;

const CFG = window.KARHUTLA_CONFIG || { mapProvider: "maplibre", attributions: [] };
const TOKEN_KEY = "karhutla.verifierToken";
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

const token = () => localStorage.getItem(TOKEN_KEY) || "";
const authHeaders = () => (token() ? { Authorization: `Bearer ${token()}` } : {});

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
    btn("reset", t("Reset map view"), () => map?.flyTo({ ...HOME_VIEW, speed: 1.6 })));
}

/* Source status: what the app last observed from each source, so a verifier knows what is current. */
const SOURCES = [
  { key: "firms", name: "FIRMS", tip: "NASA FIRMS satellite hotspots (VIIRS and MODIS), fetched every 30 minutes." },
  { key: "gibs", name: "GIBS", tip: "NASA GIBS satellite layers for the map, checked from this browser." },
  { key: "weather", name: "Open-Meteo", tip: "Wind, humidity, and rain evidence for each incident." },
  { key: "osm", name: "OSM", tip: "Nearby settlements, roads, and water from OpenStreetMap (Overpass)." },
];
const SRC_STATE = { ok: "On", error: "Down", sim: "Simulation", unknown: "Not checked" };
const SOURCES_REFRESH_MS = 5 * 60e3;

function renderSources() {
  fill($("#src-status"), SOURCES.map((s, i) => {
    const v = state.sources[s.key] || { state: "unknown" };
    const lines = [t(s.tip)];
    if (v.newest_acquisition) lines.push(t("Newest pass: {when}.", { when: fmtTime(v.newest_acquisition) }));
    if (v.checked_at) lines.push(t("Last checked: {when}.", { when: fmtTime(v.checked_at) }));
    if (v.state === "error" && v.detail) lines.push(v.detail);
    return [
      i ? el("span", { class: "src-sep", "aria-hidden": "true" }) : null,
      el("span", { class: "info src-item", tabindex: "0", dataset: { state: v.state } },
        el("span", { class: "src-dot" }), el("span", { class: "src-name" }, s.name), el("span", { class: "src-state" }, t(SRC_STATE[v.state] || SRC_STATE.unknown)),
        el("span", { class: "tip", role: "tooltip" }, lines.join(" "))),
    ];
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
  map = new maplibregl.Map({ container: "map", style: BASEMAP_STYLE, ...HOME_VIEW, hash: "map", attributionControl: { compact: true } });
  map.on("error", (e) => { if (String(e.sourceId || "").startsWith("nasa-") && state.sources.gibs.state !== "error") setGibs("error", e.error?.message || "GIBS tile failed"); });
  map.addControl(new maplibregl.ScaleControl({ unit: "metric" }), "bottom-right");
  popup = new maplibregl.Popup({ closeButton: false, closeOnClick: false, offset: 10 });
  await new Promise((resolve) => map.on("load", resolve));
  addNasaLayers();
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
  map.addSource("hotspots", { type: "geojson", data: hs });
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
    el("span", { class: "status", dataset: { status: i.status } }, statusLabel(i.status)),
  );
}

function renderList() {
  state.selected = null;
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
    const input = el("input", { type: "password", placeholder: t("Verifier token"), class: "input", "aria-label": t("Verifier token") });
    const go = () => { if (input.value) { localStorage.setItem(TOKEN_KEY, input.value); location.reload(); } };
    input.addEventListener("keydown", (e) => e.key === "Enter" && go());
    return [el("div", { class: "actions" }, input, el("button", { class: "btn btn-primary", onclick: go }, t("Sign in")))];
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
  const out = el("button", { class: "btn", onclick: () => { localStorage.removeItem(TOKEN_KEY); location.reload(); } }, t("Sign out"));
  return [el("div", { class: "actions" }, approve, reject, out)];
}

/* Evidence rows. Citizen media is fetched with the verifier token, so it never sits at a public URL. */
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
      if (single || started.length === 1) pollTriage(started[0]);
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

/* The brief shows the reader's language first and the other one below it. */
function briefParts(triage) {
  const [first, second] = lang() === "id" ? ["id", "en"] : ["en", "id"];
  return [el("p", { class: "brief" }, triage.dispatch_brief[first]), el("p", { class: "brief-en" }, triage.dispatch_brief[second])];
}

async function openIncident(id) {
  state.selected = id;
  crumbs([{ label: t("Incidents"), onclick: () => renderList() }, { label: id }]);
  $("#list-view").hidden = true;
  $("#detail-view").hidden = false;
  renderDetailSkeleton();
  if (map?.getLayer("incident-selected")) map.setFilter("incident-selected", ["==", ["get", "id"], id]);
  let inc;
  try {
    inc = await api(`/api/incidents/${id}`);
  } catch (e) {
    fill($("#detail-view"), el("p", { class: "empty" }, t("Could not load {id}: {e}", { id, e: e.message })));
    return;
  }
  // The list can render before the map exists; the map then opens on the default view.
  map?.flyTo({ center: [inc.lon, inc.lat], zoom: Math.max(map.getZoom(), 10.5), speed: 1.4 });
  const frpPct = inc.max_frp == null ? null : Math.min(100, (inc.max_frp / HEAT_MAX) * 100);
  const recent = isRecent(inc.last_seen);

  const head = el("div", { class: "card detail-head" },
    el("div", { class: "detail-title" }, el("h3", {}, inc.id), el("span", { class: "status", dataset: { status: inc.status } }, statusLabel(inc.status))),
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
        triageButton(inc),
      ] }),
    section({ icon: "check", title: t("Claims"), count: triage?.claims?.length ?? 0, open: !!triage?.claims?.length,
      tip: t("Each claim cites stored evidence. Click an ID to see it. A disputed claim was flagged by the critique model as not supported by its evidence. Claims are written in English by the model."),
      content: claimRows(triage) }),
    section({ icon: "shield", title: t("Evidence gate"), tip: t("Deterministic checks between the model and the verifier: schema, evidence existence, numbers against tool results, area from real masks, location, and content rules. Every change is listed."),
      content: gateRows(gate) }),
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
      content: inc.verdicts.length ? inc.verdicts.map((d) => el("div", { class: "ev" }, el("span", { class: "ev-id" }, t(d.decision)), el("span", {}, `${d.channel} · ${fmtTime(d.created_at)}`)))
                                   : el("p", { class: "empty" }, t("No verdict yet.")) }),
    section({ icon: "check", title: t("Verifier decision"), open: true, tip: t("Approve or reject is recorded as a label. Labels calibrate the evidence gate thresholds."), content: verifierContent(inc) }),
  ];
  fill($("#detail-view"), head, sections);
}

async function main() {
  applyStatic(t);
  hydrateIcons();
  $("#lang-slot").replaceWith(langSwitch(el));
  $("#attrib").textContent = (CFG.attributions || []).join(" · ");
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

  const [hs] = await Promise.all([hsP, mapP]);
  state.hotspots = hs.items;
  setMapData();
  performance.mark("map-data-rendered");
}

main();
