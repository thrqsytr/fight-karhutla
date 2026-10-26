// MapLibre (about 300 KB) loads in parallel with the data instead of blocking the first API request.
const MAPLIBRE_URL = "https://unpkg.com/maplibre-gl@6.16.0/dist/maplibre-gl.mjs";
let maplibregl;

const CFG = window.KARHUTLA_CONFIG || { mapProvider: "maplibre", attributions: [] };
const TOKEN_KEY = "karhutla.verifierToken";
const PAGE = 60;
const RECENT_HOURS = 6;
const BASEMAP_STYLE = "https://tiles.openfreemap.org/styles/positron";

const STATUS = {
  pending_triage: { label: "Pending triage", color: "#7a8b99" },
  likely_fire: { label: "Likely fire", color: "#d92d20" },
  possible_fire: { label: "Possible fire", color: "#f79009" },
  unlikely: { label: "Unlikely", color: "#12b76a" },
  needs_more_info: { label: "Needs more info", color: "#7a5af8" },
};
// Fire radiative power (MW) to heat color. FRP is measured by the satellite, not estimated.
const HEAT_STOPS = [[0, "#ffd66b"], [5, "#ffab40"], [15, "#ff7a1a"], [40, "#ef4423"], [100, "#b3122e"]];
const HEAT_MAX = 100;

/* Icons: simple stroke glyphs drawn for this project. */
const ICONS = {
  flame: '<path d="M12 2.5c.6 3.2 2.6 4.8 4.2 6.6A7 7 0 0 1 12 21.5a7 7 0 0 1-4.6-12.3c.3 1.8 1.2 3 2.4 3.4C9.4 9 10.4 5.4 12 2.5z"/><path d="M12 21.5a3 3 0 0 1-3-3c0-1.8 1.4-2.8 3-4.6 1.6 1.8 3 2.8 3 4.6a3 3 0 0 1-3 3z"/>',
  shield: '<path d="M12 3l7 3v5c0 4.5-3 8.2-7 10-4-1.8-7-5.5-7-10V6l7-3z"/><path d="M9 12l2 2 4-4"/>',
  info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v5"/><path d="M12 8h.01"/>',
  search: '<circle cx="11" cy="11" r="6.5"/><path d="M20 20l-4.2-4.2"/>',
  dots: '<circle cx="7" cy="12" r="2"/><circle cx="12" cy="7" r="2"/><circle cx="17" cy="12" r="2"/><circle cx="12" cy="17" r="2"/>',
  target: '<circle cx="12" cy="12" r="8"/><circle cx="12" cy="12" r="4"/><circle cx="12" cy="12" r="1"/>',
  alert: '<path d="M12 4l9 16H3l9-16z"/><path d="M12 10v4"/><path d="M12 17h.01"/>',
  chevron: '<path d="M6 9l6 6 6-6"/>',
  satellite: '<rect x="9" y="9" width="6" height="6" rx="1" transform="rotate(45 12 12)"/><path d="M3.5 8.5l3-3 3 3-3 3z"/><path d="M14.5 17.5l3-3 3 3-3 3z"/><path d="M3 21c0-2.5 1.5-4 4-4"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  pin: '<path d="M12 21s-6-5.3-6-11a6 6 0 0 1 12 0c0 5.7-6 11-6 11z"/><circle cx="12" cy="10" r="2.2"/>',
  file: '<path d="M7 3h7l4 4v14H7z"/><path d="M14 3v4h4"/><path d="M10 12h5"/><path d="M10 16h5"/>',
  check: '<circle cx="12" cy="12" r="9"/><path d="M8.5 12.5l2.5 2.5 4.5-5"/>',
  layers: '<path d="M12 4l9 5-9 5-9-5 9-5z"/><path d="M3 14l9 5 9-5"/>',
  gavel: '<path d="M14 5l5 5"/><path d="M11 8l5 5"/><path d="M12.5 6.5l-6 6"/><path d="M4 20h9"/><path d="M8.5 15.5L5 19"/>',
  activity: '<path d="M3 12h4l2-6 4 12 2-6h6"/>',
  list: '<path d="M8 6h12"/><path d="M8 12h12"/><path d="M8 18h12"/><path d="M4 6h.01"/><path d="M4 12h.01"/><path d="M4 18h.01"/>',
  x: '<path d="M6 6l12 12"/><path d="M18 6L6 18"/>',
};
const svg = (name, cls = "icon") => {
  const s = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  s.setAttribute("viewBox", "0 0 24 24");
  s.setAttribute("class", cls);
  s.setAttribute("aria-hidden", "true");
  s.innerHTML = ICONS[name] || "";
  return s;
};

const $ = (sel) => document.querySelector(sel);
const el = (tag, attrs = {}, ...children) => {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null || v === false) continue;
    if (k === "class") n.className = v;
    else if (k === "dataset") Object.assign(n.dataset, v);
    else if (k === "style") n.style.cssText = v;
    else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
    else n.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) if (c != null && c !== false) n.append(c);
  return n;
};
const info = (text, cls = "") => el("span", { class: `info ${cls}`, tabindex: "0", "aria-label": "More information" }, svg("info", "icon icon-sm"), el("span", { class: "tip", role: "tooltip" }, text));

function hydrateIcons(root = document) {
  root.querySelectorAll("[data-icon]").forEach((n) => {
    const cls = n.classList.contains("icon-sm") ? "icon icon-sm" : "icon";
    n.replaceWith(svg(n.dataset.icon, cls));
  });
}

function heatColor(frp) {
  if (frp == null) return "#c5ced6";
  for (let i = HEAT_STOPS.length - 1; i >= 0; i--) if (frp >= HEAT_STOPS[i][0]) return HEAT_STOPS[i][1];
  return HEAT_STOPS[0][1];
}
const heatExpr = (prop) => ["interpolate", ["linear"], ["coalesce", ["get", prop], 0], ...HEAT_STOPS.flat()];

const token = () => localStorage.getItem(TOKEN_KEY) || "";
const authHeaders = () => (token() ? { Authorization: `Bearer ${token()}` } : {});

function toast(msg, icon = "check") {
  const t = el("div", { class: "toast", role: "status" }, svg(icon, "icon icon-sm"), msg);
  document.body.append(t);
  setTimeout(() => t.remove(), 3200);
}

async function api(path, opts = {}) {
  const res = await fetch(path, { ...opts, headers: { "Content-Type": "application/json", ...authHeaders(), ...(opts.headers || {}) } });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `HTTP ${res.status}`);
  }
  return res.json();
}

const fmtTime = (iso) =>
  new Date(iso).toLocaleString("en-GB", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", timeZone: "Asia/Makassar" }) + " WITA";
const fmtNum = (n) => (n == null ? "n/a" : Number(n).toLocaleString("en-US"));
const fmtFrp = (v) => (v == null ? "n/a" : `${Number(v).toFixed(1)} MW`);

/* State */
const state = { incidents: [], hotspots: [], stats: null, filter: "all", sort: "heat", q: "", shown: PAGE, selected: null, recentSince: null, layers: { heatmap: true, hotspots: true, incidents: true } };

const isRecent = (iso) => state.recentSince && iso > state.recentSince;

/* Sections: one collapsible component used everywhere */
function section({ icon, title, count, tip, open = false, live = false, content, extraClass = "" }) {
  const body = el("div", { class: "section-body" }, el("div", { class: "section-inner" }, el("div", { class: "section-content" }, content)));
  const head = el("button", { class: "section-head", "aria-expanded": String(open) },
    el("span", { class: "section-icon" }, svg(icon, "icon icon-sm")),
    el("span", { class: "section-title" }, title, count != null ? el("span", { class: "badge-count num" }, String(count)) : null, live ? el("span", { class: "pulse", title: "Recent data" }) : null),
    tip ? info(tip, "tip-right") : null,
    svg("chevron", "icon chev"),
  );
  const s = el("section", { class: `card section ${extraClass}`, dataset: { open: String(open) } }, head, body);
  head.addEventListener("click", (e) => {
    if (e.target.closest(".info")) return;
    const next = s.dataset.open !== "true";
    s.dataset.open = String(next);
    head.setAttribute("aria-expanded", String(next));
  });
  return s;
}

/* KPIs */
function renderKpis() {
  const k = $("#kpis");
  const s = state.stats;
  const tile = (icon, value, label, tip, heat = false) =>
    el("div", { class: "kpi" }, el("span", { class: `kpi-icon ${heat ? "heat" : ""}` }, svg(icon, "icon icon-sm")),
      el("div", {}, s ? el("div", { class: "kpi-value num" }, value) : el("span", { class: "sk" }), el("div", { class: "kpi-label" }, label, info(tip))));
  k.replaceChildren(
    tile("list", s && fmtNum(s.incidents), "Incidents", "Hotspot clusters currently stored. Most are a single hotspot; filter for 3+ to focus."),
    tile("dots", s && fmtNum(s.incidents_multi_hotspot), "With 3+ hotspots", "Clusters with at least three detections, a stronger thermal signal."),
    tile("flame", s && fmtNum(s.hotspots_last_24h), "Hotspots, 24 h", "FIRMS detections in the 24 hours before the newest acquisition.", true),
    tile("target", s && fmtNum(s.high_conf_last_24h), "High confidence", "VIIRS high confidence (h) or MODIS 80 and above in the same window.", true),
    tile("clock", s && s.newest_acquisition ? fmtTime(s.newest_acquisition) : "n/a", "Newest pass", "Acquisition time of the newest satellite detection."),
  );
}

/* Map */
let map;
let popup;
async function initMap() {
  maplibregl = await import(MAPLIBRE_URL);
  if (CFG.mapProvider === "google") {
    const note = $("#map-note");
    note.textContent = "Google Maps adapter is not built; showing MapLibre.";
    note.hidden = false;
  }
  // hash keeps the view in the URL (#map=zoom/lat/lon) so a verifier can share an exact map position.
  map = new maplibregl.Map({ container: "map", style: BASEMAP_STYLE, center: [117.5, -1.5], zoom: 4.4, hash: "map", attributionControl: { compact: true } });
  map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
  map.addControl(new maplibregl.ScaleControl({ unit: "metric" }), "bottom-right");
  popup = new maplibregl.Popup({ closeButton: false, closeOnClick: false, offset: 10 });
  return new Promise((resolve) => map.on("load", resolve));
}

function geo(items, props) {
  return { type: "FeatureCollection", features: items.map((i) => ({ type: "Feature", geometry: { type: "Point", coordinates: [i.lon, i.lat] }, properties: props(i) })) };
}

function setMapData() {
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

  map.on("click", "incidents", (e) => openIncident(e.features[0].properties.id));
  const hover = (layer, html) => {
    map.on("mousemove", layer, (e) => {
      map.getCanvas().style.cursor = "pointer";
      popup.setLngLat(e.lngLat).setDOMContent(html(e.features[0].properties)).addTo(map);
    });
    map.on("mouseleave", layer, () => { map.getCanvas().style.cursor = ""; popup.remove(); });
  };
  hover("incidents", (p) => el("div", {}, el("div", { class: "pop-id" }, p.id), el("div", { class: "pop-meta" }, `${p.n} hotspot${p.n == 1 ? "" : "s"} · max FRP ${fmtFrp(p.frp === "null" ? null : p.frp)}`)));
  hover("hotspots", (p) => el("div", {}, el("div", { class: "pop-meta" }, `FRP ${fmtFrp(p.frp)} · confidence ${p.conf}`), el("div", { class: "pop-meta" }, fmtTime(p.at))));
  animatePulse();
}

// One soft ring per cycle: fade in, ease out while growing, fade to zero, then rest.
// The ring is invisible when the cycle restarts, so there is no jump back to the start size.
const PULSE_MS = 2600;
const PULSE_ACTIVE = 0.72;
const easeOutCubic = (x) => 1 - (1 - x) ** 3;
function pulseFrame(now) {
  const t = (now % PULSE_MS) / PULSE_MS;
  if (t >= PULSE_ACTIVE) return [3, 0];
  const p = t / PULSE_ACTIVE;
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
  const ids = key === "hotspots" ? ["hotspots", "hotspots-pulse"] : [key];
  ids.forEach((id) => map.getLayer(id) && map.setLayoutProperty(id, "visibility", on ? "visible" : "none"));
}

function renderLegend() {
  const sw = (key, label) => {
    const b = el("button", { class: "switch", role: "switch", "aria-checked": String(state.layers[key]), "aria-label": label });
    b.onclick = () => { const on = b.getAttribute("aria-checked") !== "true"; b.setAttribute("aria-checked", String(on)); setLayerVisible(key, on); };
    return el("div", { class: "switch-row" }, el("span", {}, label), b);
  };
  const content = [
    el("div", { class: "legend-label" }, "Fire radiative power", info("Measured by VIIRS or MODIS in megawatts. Higher FRP means a hotter, more intense fire signal. Opacity follows detection confidence.", "tip-left")),
    el("div", { class: "legend-ramp" }),
    el("div", { class: "legend-scale num" }, ...HEAT_STOPS.map(([v]) => el("span", {}, v === HEAT_MAX ? `${v}+` : String(v)))),
    el("div", { class: "legend-label" }, "Incident status"),
    el("div", { class: "legend-status" }, ...Object.values(STATUS).map((s) => el("span", {}, el("span", { class: "ring", style: `color:${s.color}` }), s.label))),
    el("div", { class: "legend-label" }, "Layers"),
    sw("heatmap", "Heat density"), sw("hotspots", "Hotspots"), sw("incidents", "Incidents"),
  ];
  const s = section({ icon: "layers", title: "Map legend", open: true, content });
  s.classList.add("legend");
  s.id = "legend";
  $("#legend").replaceWith(s);
}

/* Breadcrumb */
function crumbs(parts) {
  $("#crumbs").hidden = parts.length < 2;
  $("#crumbs").replaceChildren(...parts.flatMap((p, i) => [
    i ? svg("chevron", "icon icon-sm") : null,
    p.onclick ? el("a", { onclick: p.onclick }, p.label) : el("span", { class: "current" }, p.label),
  ]).filter(Boolean));
  $("#crumbs").querySelectorAll("svg").forEach((s) => (s.style.transform = "rotate(-90deg)"));
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
  const st = STATUS[i.status] || STATUS.pending_triage;
  return el("li", { class: `card incident ${state.selected === i.id ? "active" : ""}`, onclick: () => openIncident(i.id), tabindex: "0", onkeydown: (e) => e.key === "Enter" && openIncident(i.id) },
    el("span", { class: "heat-dot", style: `color:${color};background:${color}22` }, isRecent(i.last_seen) ? el("span", { class: "pulse" }) : el("span", { class: "core" })),
    el("div", {},
      el("div", { class: "id" }, i.id),
      el("div", { class: "meta" },
        el("span", { title: "Max fire radiative power" }, svg("flame"), fmtFrp(i.max_frp)),
        el("span", { title: "Hotspots in cluster" }, svg("dots"), String(i.hotspot_count)),
        el("span", { title: "Last detection" }, svg("clock"), fmtTime(i.last_seen)),
      ),
    ),
    el("span", { class: "status", dataset: { status: i.status } }, st.label),
  );
}

function renderList() {
  state.selected = null;
  if (map?.getLayer("incident-selected")) map.setFilter("incident-selected", ["==", ["get", "id"], ""]);
  crumbs([{ label: "Incidents" }]);
  $("#list-view").hidden = false;
  $("#detail-view").hidden = true;
  const items = filtered();
  $("#incident-count").textContent = items.length === state.incidents.length ? `(${fmtNum(items.length)})` : `(${fmtNum(items.length)} of ${fmtNum(state.incidents.length)})`;
  const list = $("#incident-list");
  if (!items.length) {
    list.replaceChildren(el("li", { class: "empty" }, "No incidents match this filter."));
    return;
  }
  const shown = items.slice(0, state.shown);
  list.replaceChildren(
    ...shown.map(incidentCard),
    items.length > shown.length ? el("li", {}, el("button", { class: "btn more", onclick: () => { state.shown += PAGE; renderList(); } }, `Show ${Math.min(PAGE, items.length - shown.length)} more of ${fmtNum(items.length - shown.length)}`)) : null,
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
  $("#detail-view").replaceChildren(
    el("div", { class: "card detail-head" }, el("span", { class: "sk sk-title" }), el("span", { class: "sk sk-line" }), el("span", { class: "sk sk-block" })),
    ...Array.from({ length: 4 }, () => el("div", { class: "card", style: "padding:14px" }, el("span", { class: "sk sk-title" }))),
  );
}

function verifierContent(inc) {
  if (!token()) {
    const input = el("input", { type: "password", placeholder: "Verifier token", class: "input", "aria-label": "Verifier token" });
    const go = () => { if (input.value) { localStorage.setItem(TOKEN_KEY, input.value); openIncident(inc.id); } };
    input.addEventListener("keydown", (e) => e.key === "Enter" && go());
    return [el("div", { class: "actions" }, input, el("button", { class: "btn btn-primary", onclick: go }, "Sign in"))];
  }
  const approve = el("button", { class: "btn btn-primary" }, svg("check", "icon icon-sm"), "Approve");
  const reject = el("button", { class: "btn btn-ghost-danger" }, svg("x", "icon icon-sm"), "Reject");
  const decide = async (decision) => {
    [approve, reject].forEach((b) => (b.disabled = true));
    try {
      await api(`/api/incidents/${inc.id}/verdict`, { method: "POST", body: JSON.stringify({ decision }) });
      toast(decision === "approve" ? "Approved and recorded." : "Rejected and recorded.");
      openIncident(inc.id);
    } catch (e) {
      toast(`Not recorded: ${e.message}`, "alert");
      [approve, reject].forEach((b) => (b.disabled = false));
    }
  };
  approve.onclick = () => decide("approve");
  reject.onclick = () => decide("reject");
  const out = el("button", { class: "btn", onclick: () => { localStorage.removeItem(TOKEN_KEY); openIncident(inc.id); } }, "Sign out");
  return [el("div", { class: "actions" }, approve, reject, out)];
}

async function openIncident(id) {
  state.selected = id;
  crumbs([{ label: "Incidents", onclick: () => renderList() }, { label: id }]);
  $("#list-view").hidden = true;
  $("#detail-view").hidden = false;
  renderDetailSkeleton();
  if (map?.getLayer("incident-selected")) map.setFilter("incident-selected", ["==", ["get", "id"], id]);
  let inc;
  try {
    inc = await api(`/api/incidents/${id}`);
  } catch (e) {
    $("#detail-view").replaceChildren(el("p", { class: "empty" }, `Could not load ${id}: ${e.message}`));
    return;
  }
  // The list can render before the map exists; the map then opens on the default view.
  map?.flyTo({ center: [inc.lon, inc.lat], zoom: Math.max(map.getZoom(), 10.5), speed: 1.4 });
  const st = STATUS[inc.status] || STATUS.pending_triage;
  const frpPct = inc.max_frp == null ? null : Math.min(100, (inc.max_frp / HEAT_MAX) * 100);
  const recent = isRecent(inc.last_seen);

  const head = el("div", { class: "card detail-head" },
    el("div", { class: "detail-title" }, el("h3", {}, inc.id), el("span", { class: "status", dataset: { status: inc.status } }, st.label)),
    el("div", { class: "tags" },
      inc.simulation ? el("span", { class: "pill pill-sim" }, "SIMULASI") : null,
      recent ? el("span", { class: "pill pill-live" }, el("span", { class: "pulse" }), "Active in last 6 h") : null,
      el("span", { class: "pill" }, svg("satellite", "icon icon-sm"), inc.trigger.replace("_", " ")),
    ),
    el("div", { class: "gauge" },
      el("div", { class: "gauge-row" }, el("span", { style: "display:flex;gap:4px;align-items:center" }, "Max fire radiative power", info("Highest FRP among the cluster's hotspots, as measured by the satellite.")), el("strong", { class: "num" }, fmtFrp(inc.max_frp))),
      el("div", { class: "gauge-track" }, frpPct == null ? null : el("span", { class: "gauge-mark", style: `left:${frpPct}%` })),
    ),
    el("div", { class: "stats-grid" },
      el("div", { class: "stat" }, el("div", { class: "stat-v num" }, String(inc.hotspot_count)), el("div", { class: "stat-l" }, "Hotspots")),
      el("div", { class: "stat" }, el("div", { class: "stat-v num" }, String(inc.high_conf_count)), el("div", { class: "stat-l" }, "High confidence")),
      el("div", { class: "stat" }, el("div", { class: "stat-v num" }, inc.sum_frp == null ? "n/a" : `${Number(inc.sum_frp).toFixed(0)}`), el("div", { class: "stat-l" }, "Total FRP, MW")),
    ),
  );

  const triage = inc.triage;
  const gate = inc.gate;
  const sections = [
    section({ icon: "pin", title: "Overview", open: true, content: el("dl", { class: "kv" },
      el("dt", {}, "Location"), el("dd", { class: "num" }, `${inc.lat.toFixed(3)}, ${inc.lon.toFixed(3)}`),
      el("dt", {}, "First seen"), el("dd", {}, fmtTime(inc.first_seen)),
      el("dt", {}, "Last seen"), el("dd", {}, fmtTime(inc.last_seen)),
    ) }),
    section({ icon: "file", title: "Dispatch brief", open: true, tip: "Written by the triage agent and checked by the evidence gate before it is shown. Indonesian first, English with the same content.",
      content: triage ? [el("p", { class: "brief" }, triage.dispatch_brief.id), el("p", { class: "brief-en" }, triage.dispatch_brief.en)]
                      : el("p", { class: "empty" }, "Not triaged yet. The triage agent runs once the Meta Model API is connected.") }),
    section({ icon: "shield", title: "Evidence gate", tip: "Share of agent claims that cite real, stored evidence. Claims without evidence are removed, never shown as fact.",
      content: gate ? el("p", { class: "brief" }, `Grounding rate ${gate.grounding_rate == null ? "n/a" : Math.round(gate.grounding_rate * 100) + "%"}, ${gate.claims_passed} of ${gate.claims_total} claims passed.`)
                    : el("p", { class: "empty" }, "No gate result yet.") }),
    section({ icon: "activity", title: "Evidence", count: inc.evidence.length, tip: "Every tool result is stored with a numbered ID. Claims link to these IDs.",
      content: inc.evidence.length ? inc.evidence.map((e) => el("div", { class: "ev" }, el("span", { class: "ev-id" }, e.id), el("span", {}, `${e.type} · ${e.source}: ${e.summary}`)))
                                   : el("p", { class: "empty" }, "No evidence collected yet.") }),
    section({ icon: "flame", title: "Hotspots", count: inc.hotspots.length, live: recent, tip: "NASA FIRMS detections. Confidence l, n, h means low, nominal, high.",
      content: [
        el("table", { class: "hs-table" },
          el("thead", {}, el("tr", {}, el("th", {}, "Time"), el("th", {}, "Sat"), el("th", {}, "Conf"), el("th", {}, "FRP"))),
          el("tbody", {}, inc.hotspots.slice(0, 25).map((h) => el("tr", {},
            el("td", {}, fmtTime(h.acquired_at)), el("td", {}, h.satellite), el("td", {}, h.confidence),
            el("td", { class: "num" }, el("span", { class: "frp-dot", style: `background:${heatColor(h.frp)}` }), fmtFrp(h.frp))))),
        ),
        inc.hotspots.length > 25 ? el("p", { class: "empty" }, `Showing 25 of ${inc.hotspots.length}.`) : null,
      ] }),
    section({ icon: "gavel", title: "Verdicts", count: inc.verdicts.length,
      content: inc.verdicts.length ? inc.verdicts.map((d) => el("div", { class: "ev" }, el("span", { class: "ev-id" }, d.decision), el("span", {}, `${d.channel} · ${fmtTime(d.created_at)}`)))
                                   : el("p", { class: "empty" }, "No verdict yet.") }),
    section({ icon: "check", title: "Verifier decision", open: true, tip: "Approve or reject is recorded as a label. Labels calibrate the evidence gate thresholds.", content: verifierContent(inc) }),
  ];
  $("#detail-view").replaceChildren(head, ...sections);
}

async function main() {
  hydrateIcons();
  $("#attrib").textContent = (CFG.attributions || []).join(" · ");
  const pill = $("#mode-pill");
  if (CFG.fixtureMode) { pill.className = "pill pill-sim"; pill.textContent = "SIMULASI"; }
  else { pill.className = "pill pill-live"; pill.replaceChildren(el("span", { class: "pulse" }), "Live FIRMS"); }
  // Start every request at once. The list and KPIs render as soon as their data lands;
  // the map draws its layers whenever both the map and the data are ready.
  const statsP = api("/api/stats").catch(() => null);
  const incP = api("/api/incidents").catch((e) => ({ items: [], error: e }));
  const hsP = api("/api/hotspots").catch(() => ({ items: [] }));
  const mapP = initMap();

  renderKpis();
  renderLegend();
  bindListControls();
  crumbs([{ label: "Incidents" }]);
  $("#incident-list").replaceChildren(...Array.from({ length: 7 }, skeletonCard));

  const stats = await statsP;
  state.stats = stats;
  if (stats?.newest_acquisition) state.recentSince = new Date(new Date(stats.newest_acquisition).getTime() - RECENT_HOURS * 3600e3).toISOString().replace(".000Z", "+00:00");
  renderKpis();
  performance.mark("kpis-rendered");

  const inc = await incP;
  state.incidents = inc.items;
  if (inc.error) $("#incident-list").replaceChildren(el("li", { class: "empty" }, `Could not load incidents: ${inc.error.message}`));
  else if (!state.selected) renderList();
  performance.mark("list-rendered");

  const [hs] = await Promise.all([hsP, mapP]);
  state.hotspots = hs.items;
  setMapData();
  performance.mark("map-data-rendered");
}

main();
