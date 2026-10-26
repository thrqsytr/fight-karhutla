import * as maplibregl from "https://unpkg.com/maplibre-gl@6.16.0/dist/maplibre-gl.mjs";

const CFG = window.KARHUTLA_CONFIG || { mapProvider: "maplibre", attributions: [] };
const TOKEN_KEY = "karhutla.verifierToken";
const STATUS_LABEL = {
  pending_triage: "Pending triage",
  likely_fire: "Likely fire",
  possible_fire: "Possible fire",
  unlikely: "Unlikely",
  needs_more_info: "Needs more info",
};
const STATUS_COLOR = {
  pending_triage: "#8a8178",
  likely_fire: "#c2410c",
  possible_fire: "#b7791f",
  unlikely: "#2f6b4f",
  needs_more_info: "#6b6aa8",
};

const $ = (sel) => document.querySelector(sel);
const el = (tag, attrs = {}, ...children) => {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") n.className = v;
    else if (k === "dataset") Object.assign(n.dataset, v);
    else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
    else n.setAttribute(k, v);
  }
  for (const c of children.flat()) if (c != null) n.append(c);
  return n;
};
const info = (text) => el("span", { class: "info", tabindex: "0", "aria-label": "More information" }, "i", el("span", { class: "tip", role: "tooltip" }, text));

function token() { return localStorage.getItem(TOKEN_KEY) || ""; }
function authHeaders() { return token() ? { Authorization: `Bearer ${token()}` } : {}; }

function toast(msg) {
  const t = el("div", { class: "toast", role: "status" }, msg);
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

function fmtTime(iso) {
  return new Date(iso).toLocaleString("en-GB", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", timeZone: "Asia/Makassar" }) + " WITA";
}

/* Map */
let map;
function initMap() {
  if (CFG.mapProvider === "google") {
    const note = $("#map-note");
    note.textContent = "Google Maps adapter is not built yet; showing MapLibre with OpenStreetMap tiles.";
    note.hidden = false;
  }
  map = new maplibregl.Map({
    container: "map",
    center: [117.5, -1.5],
    zoom: 4.3,
    style: {
      version: 8,
      sources: {
        osm: {
          type: "raster",
          tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"],
          tileSize: 256,
          maxzoom: 19,
          attribution: "&copy; OpenStreetMap contributors",
        },
      },
      layers: [{ id: "osm", type: "raster", source: "osm" }],
    },
  });
  map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
  return new Promise((resolve) => map.on("load", resolve));
}

function setMapData(hotspots, incidents) {
  const hsGeo = { type: "FeatureCollection", features: hotspots.map((h) => ({ type: "Feature", geometry: { type: "Point", coordinates: [h.lon, h.lat] }, properties: { id: h.id } })) };
  const incGeo = {
    type: "FeatureCollection",
    features: incidents.map((i) => ({ type: "Feature", geometry: { type: "Point", coordinates: [i.lon, i.lat] }, properties: { id: i.id, color: STATUS_COLOR[i.status] || STATUS_COLOR.pending_triage, n: i.hotspot_count } })),
  };
  if (map.getSource("hotspots")) {
    map.getSource("hotspots").setData(hsGeo);
    map.getSource("incidents").setData(incGeo);
    return;
  }
  map.addSource("hotspots", { type: "geojson", data: hsGeo });
  map.addSource("incidents", { type: "geojson", data: incGeo });
  map.addLayer({ id: "hotspots", type: "circle", source: "hotspots", paint: { "circle-radius": 2.5, "circle-color": "#f97316", "circle-opacity": 0.8 } });
  map.addLayer({
    id: "incidents", type: "circle", source: "incidents",
    paint: {
      "circle-radius": ["interpolate", ["linear"], ["get", "n"], 1, 6, 20, 14],
      "circle-color": ["get", "color"], "circle-opacity": 0.35,
      "circle-stroke-color": ["get", "color"], "circle-stroke-width": 2,
    },
  });
  map.on("click", "incidents", (e) => openIncident(e.features[0].properties.id));
  map.on("mouseenter", "incidents", () => (map.getCanvas().style.cursor = "pointer"));
  map.on("mouseleave", "incidents", () => (map.getCanvas().style.cursor = ""));
}

/* Breadcrumb */
function crumbs(parts) {
  const nav = $("#crumbs");
  nav.replaceChildren();
  parts.forEach((p, i) => {
    if (i) nav.append(el("span", { class: "sep" }, "/"));
    nav.append(p.onclick ? el("a", { onclick: p.onclick }, p.label) : el("span", {}, p.label));
  });
}

/* List */
let incidents = [];
function renderSkeletonList(n = 6) {
  const list = $("#incident-list");
  list.replaceChildren(...Array.from({ length: n }, () => $("#tpl-skeleton-item").content.cloneNode(true)));
}

function renderList() {
  crumbs([{ label: "Incidents" }]);
  $("#list-view").hidden = false;
  $("#detail-view").hidden = true;
  $("#incident-count").textContent = `(${incidents.length})`;
  const list = $("#incident-list");
  if (!incidents.length) {
    list.replaceChildren(el("li", { class: "empty" }, "No incidents in the current window."));
    return;
  }
  list.replaceChildren(
    ...incidents.map((i) =>
      el("li", { class: "incident", dataset: { status: i.status }, onclick: () => openIncident(i.id) },
        el("div", { class: "row" }, el("span", { class: "id" }, i.id), el("span", { class: "chip", dataset: { status: i.status } }, STATUS_LABEL[i.status] || i.status)),
        el("div", { class: "meta" }, `${i.hotspot_count} hotspot${i.hotspot_count === 1 ? "" : "s"} · last seen ${fmtTime(i.last_seen)}`),
      ),
    ),
  );
}

/* Detail */
function renderDetailSkeleton() {
  const v = $("#detail-view");
  v.replaceChildren(el("div", { class: "detail" }, el("span", { class: "sk sk-title" }), el("span", { class: "sk sk-line", style: "margin-top:8px" }), el("span", { class: "sk sk-block" }), el("span", { class: "sk sk-block" })));
}

function verifierBlock(inc) {
  const block = el("div", { class: "block" }, el("h4", {}, "Verifier decision", info("Approve or reject is recorded as a label. Labels calibrate the evidence gate thresholds.")));
  if (!token()) {
    const input = el("input", { type: "password", placeholder: "Verifier token", class: "btn", style: "flex:1;font-weight:400" });
    block.append(
      el("div", { class: "actions" }, input,
        el("button", { class: "btn", onclick: () => { if (input.value) { localStorage.setItem(TOKEN_KEY, input.value); openIncident(inc.id); } } }, "Sign in")),
    );
    return block;
  }
  const decide = async (decision, btns) => {
    btns.forEach((b) => (b.disabled = true));
    try {
      await api(`/api/incidents/${inc.id}/verdict`, { method: "POST", body: JSON.stringify({ decision }) });
      toast(decision === "approve" ? "Approved and recorded." : "Rejected and recorded.");
      openIncident(inc.id);
    } catch (e) {
      toast(`Not recorded: ${e.message}`);
      btns.forEach((b) => (b.disabled = false));
    }
  };
  const approve = el("button", { class: "btn btn-primary" }, "Approve");
  const reject = el("button", { class: "btn btn-danger" }, "Reject");
  approve.onclick = () => decide("approve", [approve, reject]);
  reject.onclick = () => decide("reject", [approve, reject]);
  const out = el("button", { class: "btn", onclick: () => { localStorage.removeItem(TOKEN_KEY); openIncident(inc.id); } }, "Sign out");
  block.append(el("div", { class: "actions" }, approve, reject, out));
  return block;
}

async function openIncident(id) {
  crumbs([{ label: "Incidents", onclick: () => renderList() }, { label: id }]);
  $("#list-view").hidden = true;
  $("#detail-view").hidden = false;
  renderDetailSkeleton();
  let inc;
  try {
    inc = await api(`/api/incidents/${id}`);
  } catch (e) {
    $("#detail-view").replaceChildren(el("p", { class: "empty" }, `Could not load ${id}: ${e.message}`));
    return;
  }
  map.flyTo({ center: [inc.lon, inc.lat], zoom: 11 });
  const triage = inc.triage;
  const gate = inc.gate;
  const v = $("#detail-view");
  v.replaceChildren(
    el("div", { class: "detail" },
      el("h3", {}, inc.id),
      el("span", { class: "chip", dataset: { status: inc.status } }, STATUS_LABEL[inc.status] || inc.status),
      inc.simulation ? el("span", { class: "badge badge-sim", style: "margin-left:6px" }, "SIMULASI") : null,
      el("dl", { class: "kv" },
        el("dt", {}, "Location"), el("dd", {}, `${inc.lat.toFixed(3)}, ${inc.lon.toFixed(3)}`),
        el("dt", {}, "Trigger"), el("dd", {}, inc.trigger.replace("_", " ")),
        el("dt", {}, "Hotspots"), el("dd", {}, String(inc.hotspot_count)),
        el("dt", {}, "First seen"), el("dd", {}, fmtTime(inc.first_seen)),
        el("dt", {}, "Last seen"), el("dd", {}, fmtTime(inc.last_seen)),
      ),
      el("div", { class: "block" },
        el("h4", {}, "Dispatch brief", info("Written by the triage agent and checked by the evidence gate before it is shown. Indonesian first, English with the same content.")),
        triage ? [el("p", {}, triage.dispatch_brief.id), el("p", { style: "color:var(--ink-soft)" }, triage.dispatch_brief.en)]
               : el("p", { class: "empty" }, "Not triaged yet. The triage agent runs once the Meta Model API is connected."),
      ),
      el("div", { class: "block" },
        el("h4", {}, "Evidence gate", info("Share of agent claims that cite real, stored evidence. Claims without evidence are removed, never shown as fact.")),
        gate ? el("p", {}, `Grounding rate ${gate.grounding_rate == null ? "n/a" : Math.round(gate.grounding_rate * 100) + "%"} · ${gate.claims_passed} of ${gate.claims_total} claims passed`)
             : el("p", { class: "empty" }, "No gate result yet."),
      ),
      el("div", { class: "block" },
        el("h4", {}, `Evidence (${inc.evidence.length})`, info("Every tool result is stored with a numbered ID. Claims link to these IDs.")),
        inc.evidence.length ? inc.evidence.map((e) => el("div", { class: "ev" }, el("span", { class: "ev-id" }, e.id), `${e.type} · ${e.source}: ${e.summary}`))
                            : el("p", { class: "empty" }, "No evidence collected yet."),
      ),
      el("div", { class: "block" },
        el("h4", {}, `Hotspots (${inc.hotspots.length})`, info("NASA FIRMS VIIRS detections. Confidence l, n, h means low, nominal, high.")),
        inc.hotspots.slice(0, 20).map((h) => el("div", { class: "ev" }, `${fmtTime(h.acquired_at)} · ${h.satellite} · conf ${h.confidence} · FRP ${h.frp ?? "n/a"} MW`)),
        inc.hotspots.length > 20 ? el("p", { class: "empty" }, `Showing 20 of ${inc.hotspots.length}.`) : null,
      ),
      inc.verdicts.length ? el("div", { class: "block" }, el("h4", {}, `Verdicts (${inc.verdicts.length})`), inc.verdicts.map((d) => el("div", { class: "ev" }, `${d.decision} · ${d.channel} · ${fmtTime(d.created_at)}`))) : null,
      verifierBlock(inc),
    ),
  );
}

async function main() {
  $("#attrib").textContent = (CFG.attributions || []).join(" · ");
  $("#sim-badge").hidden = !CFG.fixtureMode;
  renderSkeletonList();
  const [_, inc, hs] = await Promise.all([
    initMap(),
    api("/api/incidents").catch((e) => ({ items: [], error: e })),
    api("/api/hotspots").catch(() => ({ items: [] })),
  ]);
  incidents = inc.items;
  if (inc.error) {
    $("#incident-list").replaceChildren(el("li", { class: "empty" }, `Could not load incidents: ${inc.error.message}`));
  } else {
    renderList();
  }
  setMapData(hs.items, incidents);
}

main();
