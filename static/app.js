"use strict";

/* ------------------------------------------------------------------ */
/* Map setup                                                           */
/* ------------------------------------------------------------------ */
const map = L.map("map").setView([-2.5, 118], 5);

L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
  maxZoom: 19,
  attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
}).addTo(map);

const liveLayer = L.layerGroup().addTo(map);   // GPS sessions (markers + trails)
const lookupLayer = L.layerGroup().addTo(map); // IP / phone lookup markers

/* ------------------------------------------------------------------ */
/* State                                                               */
/* ------------------------------------------------------------------ */
const sessionById = {};   // id -> { card element, marker, circle, trail, data }
let lastLookupMarker = null;
let csrfToken = null;

/* ------------------------------------------------------------------ */
/* Helpers                                                             */
/* ------------------------------------------------------------------ */
function fmtTime(iso) {
  if (!iso) return "never";
  const d = new Date(iso);
  if (isNaN(d)) return iso;
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function fmtAge(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (isNaN(d)) return "—";
  const secs = Math.max(0, Math.floor((Date.now() - d.getTime()) / 1000));
  if (secs < 60) return `${secs}s ago`;
  if (secs < 3600) return `${Math.floor(secs / 60)}m ago`;
  return `${Math.floor(secs / 3600)}h ago`;
}

function esc(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function toast(msg) {
  const el = document.getElementById("toast");
  el.textContent = msg;
  el.classList.remove("hidden");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => el.classList.add("hidden"), 3000);
}

async function api(url, options = {}) {
  const resp = await fetch(url, options);
  if (!resp.ok) {
    let msg = `HTTP ${resp.status}`;
    try {
      const body = await resp.json();
      if (body.error) msg = body.error;
    } catch (_) { /* ignore */ }
    throw new Error(msg);
  }
  return resp.json();
}

async function fetchCsrfToken() {
  try {
    const data = await api("/api/csrf-token");
    csrfToken = data.csrf_token;
  } catch (err) {
    console.error("Failed to fetch CSRF token:", err);
  }
}

/* ------------------------------------------------------------------ */
/* Stats                                                               */
/* ------------------------------------------------------------------ */
async function loadStats() {
  try {
    const stats = await api("/api/stats");
    document.getElementById("stat-total").textContent = stats.total_sessions || 0;
    document.getElementById("stat-active").textContent = stats.active_sessions || 0;
    document.getElementById("stat-points").textContent = stats.total_points || 0;
  } catch (err) {
    console.error("Failed to load stats:", err);
  }
}

/* ------------------------------------------------------------------ */
/* Live GPS sessions                                                   */
/* ------------------------------------------------------------------ */
function renderSessions(data) {
  document.getElementById("map-status").textContent =
    `${data.length} session(s) • live updates via SSE (instant)`;

  const listEl = document.getElementById("session-list");
  const known = new Set();

  for (const s of data) {
    known.add(s.id);
    const entry = sessionById[s.id];
    if (entry) {
      entry.data = s;
      updateSessionCard(entry);
      if (s.latest) updateSessionMap(entry);
    } else {
      const created = createSessionEntry(s);
      listEl.prepend(created.card);
      if (s.latest) updateSessionMap(created);
    }
  }

  // Remove entries that no longer exist (deleted elsewhere)
  for (const id of Object.keys(sessionById)) {
    if (!known.has(Number(id))) {
      const entry = sessionById[id];
      if (entry.marker) liveLayer.removeLayer(entry.marker);
      if (entry.circle) liveLayer.removeLayer(entry.circle);
      if (entry.trail) liveLayer.removeLayer(entry.trail);
      entry.card.remove();
      delete sessionById[id];
    }
  }

  // Refresh stats
  loadStats();
}

async function refreshSessions() {
  try {
    renderSessions(await api("/api/sessions"));
  } catch (err) {
    document.getElementById("map-status").textContent = `Error: ${err.message}`;
  }
}

function connectSSE() {
  const sse = new EventSource("/api/events");
  sse.addEventListener("sessions", (ev) => {
    try {
      renderSessions(JSON.parse(ev.data));
    } catch (err) {
      console.error("Bad SSE payload:", err);
    }
  });
  sse.onerror = () => {
    // EventSource auto-reconnects; show a hint while offline
    document.getElementById("map-status").textContent =
      "Reconnecting to live feed…";
  };
  return sse;
}

function createSessionEntry(s) {
  const card = document.createElement("div");
  card.className = "session-card" + (s.paused ? " paused" : "");
  const entry = { id: s.id, card, data: s, marker: null, circle: null, trail: null };
  sessionById[s.id] = entry;

  card.addEventListener("click", () => {
    const latest = entry.data.latest;
    if (latest) map.flyTo([latest.lat, latest.lon], Math.max(map.getZoom(), 13));
  });
  return entry;
}

function updateSessionCard(entry) {
  const s = entry.data;
  const status = s.paused ? "paused" : (s.latest ? (s.active ? "active" : "stale") : "none");
  const statusLabel = s.paused ? "Paused" : (s.latest ? (s.active ? "Live" : "Stale") : "No fix yet");
  entry.card.className = "session-card" + (s.paused ? " paused" : "");
  entry.card.innerHTML = `
    <div class="session-head">
      <span class="session-name">${esc(s.name)}</span>
      <span class="status-dot ${status}" title="${statusLabel}"></span>
    </div>
    <div class="session-meta">
      ${s.points} point(s) • last seen ${fmtAge(s.latest && s.latest.timestamp)}<br>
      ${s.latest ? `@ ${s.latest.lat.toFixed(5)}, ${s.latest.lon.toFixed(5)}` : "Waiting for first fix…"}
    </div>
    <div class="session-actions">
      <button class="btn small copy-btn" data-token="${esc(s.token)}">Copy link</button>
      <button class="btn small ${s.paused ? 'primary' : 'warning'} pause-btn" data-id="${s.id}" data-paused="${s.paused}">
        ${s.paused ? '▶ Resume' : '⏸ Pause'}
      </button>
      <button class="btn small export-btn" data-id="${s.id}">Export</button>
      <button class="btn small photos-btn" data-token="${esc(s.token)}" data-name="${esc(s.name)}">Photos</button>
      <button class="btn small danger del-btn" data-id="${s.id}">Delete</button>
    </div>`;

  entry.card.querySelector(".copy-btn").addEventListener("click", (ev) => {
    ev.stopPropagation();
    copyShareLink(s.token);
  });

  entry.card.querySelector(".pause-btn").addEventListener("click", async (ev) => {
    ev.stopPropagation();
    const btn = ev.target;
    const action = btn.dataset.paused === "true" ? "resume" : "pause";
    try {
      await api(`/api/sessions/${s.id}/${action}`, {
        method: "POST",
        headers: { "X-CSRF-Token": csrfToken },
      });
      toast(`Session ${action}d`);
      refreshSessions();
    } catch (err) {
      toast(`Failed: ${err.message}`);
    }
  });

  entry.card.querySelector(".export-btn").addEventListener("click", (ev) => {
    ev.stopPropagation();
    showExportModal(s.id, s.name);
  });

  entry.card.querySelector(".photos-btn").addEventListener("click", (ev) => {
    ev.stopPropagation();
    showPhotoGallery(s.token, s.name);
  });

  entry.card.querySelector(".del-btn").addEventListener("click", async (ev) => {
    ev.stopPropagation();
    if (!confirm(`Delete session "${s.name}" and its ${s.points} location point(s)?`)) return;
    try {
      await api(`/api/sessions/${s.id}`, { method: "DELETE" });
      toast("Session deleted");
    } catch (err) {
      toast(`Delete failed: ${err.message}`);
    }
  });
}

function updateSessionMap(entry) {
  const latest = entry.data.latest;
  const color = entry.data.active ? "#4ade80" : (entry.data.paused ? "#fbbf24" : "#f87171");

  if (!entry.marker) {
    entry.marker = L.circleMarker([latest.lat, latest.lon], {
      radius: 8,
      color: "#fff",
      weight: 2,
      fillColor: color,
      fillOpacity: 0.9,
    }).addTo(liveLayer);
    entry.circle = L.circle([latest.lat, latest.lon], {
      radius: latest.accuracy || 50,
      color: color,
      weight: 1,
      fillColor: color,
      fillOpacity: 0.08,
    }).addTo(liveLayer);
    entry.trail = L.polyline([[latest.lat, latest.lon]], {
      color: color,
      weight: 3,
      opacity: 0.7,
    }).addTo(liveLayer);
    entry.marker.bindPopup(() =>
      `<b>${esc(entry.data.name)}</b><br>${latest.lat.toFixed(6)}, ${latest.lon.toFixed(6)}<br>${fmtTime(latest.timestamp)}`
    );
  } else {
    entry.marker.setLatLng([latest.lat, latest.lon]);
    entry.marker.setStyle({ fillColor: color });
    entry.circle.setLatLng([latest.lat, latest.lon]);
    entry.circle.setRadius(latest.accuracy || 50);
    entry.circle.setStyle({ color });
    entry.trail.addLatLng([latest.lat, latest.lon]);
    entry.trail.setStyle({ color });
  }
}

function copyShareLink(token) {
  const url = `${location.origin}/share/${token}`;
  if (navigator.clipboard && window.isSecureContext) {
    navigator.clipboard.writeText(url).then(
      () => toast("Share link copied!"),
      () => fallbackCopy(url)
    );
  } else {
    fallbackCopy(url);
  }
}

function fallbackCopy(text) {
  const ta = document.createElement("textarea");
  ta.value = text;
  document.body.appendChild(ta);
  ta.select();
  try {
    document.execCommand("copy");
    toast("Share link copied!");
  } catch (_) {
    toast(`Share link: ${text}`);
  }
  ta.remove();
}

/* ------------------------------------------------------------------ */
/* Export Modal                                                         */
/* ------------------------------------------------------------------ */
function showExportModal(sessionId, sessionName) {
  // Remove existing modal if any
  const existing = document.querySelector('.modal-overlay');
  if (existing) existing.remove();

  const overlay = document.createElement('div');
  overlay.className = 'modal-overlay';
  overlay.innerHTML = `
    <div class="modal">
      <h3>Export "${esc(sessionName)}"</h3>
      <p style="color: var(--muted); font-size: 0.9rem;">Choose export format:</p>
      <div class="modal-actions">
        <button class="btn primary export-gpx" data-id="${sessionId}">GPX</button>
        <button class="btn primary export-kml" data-id="${sessionId}">KML</button>
        <button class="btn cancel-btn">Cancel</button>
      </div>
    </div>
  `;

  document.body.appendChild(overlay);

  // Handle clicks
  overlay.querySelector('.export-gpx').addEventListener('click', () => {
    downloadExport(sessionId, 'gpx');
    overlay.remove();
  });

  overlay.querySelector('.export-kml').addEventListener('click', () => {
    downloadExport(sessionId, 'kml');
    overlay.remove();
  });

  overlay.querySelector('.cancel-btn').addEventListener('click', () => {
    overlay.remove();
  });

  overlay.addEventListener('click', (e) => {
    if (e.target === overlay) overlay.remove();
  });
}

function downloadExport(sessionId, format) {
  const url = `/api/sessions/${sessionId}/export/${format}`;
  const a = document.createElement('a');
  a.href = url;
  a.download = '';
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  toast(`Exporting as ${format.toUpperCase()}...`);
}

async function showPhotoGallery(token, sessionName) {
  const existing = document.querySelector('.modal-overlay');
  if (existing) existing.remove();
  const overlay = document.createElement('div');
  overlay.className = 'modal-overlay';
  overlay.innerHTML = `<div class="modal"><h3>Photos — ${esc(sessionName)}</h3><div class="photo-gallery">Loading photos…</div><div class="modal-actions"><button class="btn cancel-btn">Close</button></div></div>`;
  document.body.appendChild(overlay);
  overlay.querySelector('.cancel-btn').addEventListener('click', () => overlay.remove());
  overlay.addEventListener('click', (event) => { if (event.target === overlay) overlay.remove(); });
  const gallery = overlay.querySelector('.photo-gallery');
  try {
    const photos = await api(`/api/sessions/${encodeURIComponent(token)}/media`);
    if (!photos.length) {
      gallery.textContent = 'No consented camera photos yet.';
      return;
    }
    gallery.innerHTML = photos.map((photo) => `
      <figure class="photo-item">
        <img src="/api/sessions/${encodeURIComponent(token)}/media/${photo.id}" alt="Camera capture" loading="lazy">
        <figcaption>${esc(fmtTime(photo.created_at))}</figcaption>
      </figure>`).join('');
  } catch (err) {
    gallery.textContent = `Could not load photos: ${err.message}`;
  }
}

/* ------------------------------------------------------------------ */
/* Lookups (IP + phone)                                                */
/* ------------------------------------------------------------------ */
function clearLookupMarker() {
  if (lastLookupMarker) {
    lookupLayer.removeLayer(lastLookupMarker);
    lastLookupMarker = null;
  }
}

function showLookupMarker(lat, lon, title, popupHtml) {
  clearLookupMarker();
  lastLookupMarker = L.marker([lat, lon]).addTo(lookupLayer);
  lastLookupMarker.bindPopup(popupHtml || `<b>${esc(title)}</b>`);
  map.flyTo([lat, lon], Math.max(map.getZoom(), 10), { duration: 0.6 });
}

function kvRow(key, value) {
  const v = value == null || value === "" ? "—" : value;
  return `<div class="k">${esc(key)}</div><div class="v">${esc(v)}</div>`;
}

function renderResult(el, html) {
  el.innerHTML = `<div class="result-card">${html}</div>`;
}

function renderError(el, message) {
  el.innerHTML = `<div class="error-banner">⚠️ ${esc(message)}</div>`;
}

async function handleIpLookup(ev) {
  ev.preventDefault();
  const input = document.getElementById("ip-input");
  const el = document.getElementById("ip-result");
  const addr = input.value.trim();
  if (!addr) return;

  el.innerHTML = `<div class="result-card">Looking up <b>${esc(addr)}</b>…</div>`;
  try {
    const d = await api(`/api/ip/${encodeURIComponent(addr)}`);
    if (d.error) return renderError(el, d.error);
    renderResult(el, `
      <h3>🌐 ${esc(d.ip)}</h3>
      <div class="kv-grid">
        ${kvRow("Country", `${d.country} ${d.flag || ""}`)}
        ${kvRow("City / Region", `${d.city || "—"}, ${d.region || "—"}`)}
        ${kvRow("Coordinates", `${d.latitude}, ${d.longitude}`)}
        ${kvRow("Postal", d.postal)}
        ${kvRow("ISP", d.isp)}
        ${kvRow("Organization", d.org)}
        ${kvRow("ASN", d.asn)}
        ${kvRow("Type", d.type)}
        ${kvRow("Timezone", d.timezone)}
        ${kvRow("Local time", d.current_time)}
        ${kvRow("Capital", d.capital)}
      </div>
      <div class="cta-row">
        <a class="btn" href="${esc(d.maps_url)}" target="_blank" rel="noopener">Open in Google Maps</a>
      </div>`);
    if (d.latitude != null && d.longitude != null) {
      showLookupMarker(d.latitude, d.longitude, d.ip,
        `<b>${esc(d.ip)}</b><br>${esc(d.country)} — ${esc(d.city || "")}<br>${esc(d.isp || "")}`);
    }
  } catch (err) {
    renderError(el, err.message);
  }
}

async function handlePhoneLookup(ev) {
  ev.preventDefault();
  const input = document.getElementById("phone-input");
  const el = document.getElementById("phone-result");
  const num = input.value.trim();
  if (!num) return;

  el.innerHTML = `<div class="result-card">Looking up <b>${esc(num)}</b>…</div>`;
  try {
    const d = await api(`/api/phone/${encodeURIComponent(num)}`);
    if (d.error) return renderError(el, d.error);
    renderResult(el, `
      <h3>📱 ${esc(d.international || d.original)}</h3>
      <div class="kv-grid">
        ${kvRow("Valid number", d.valid ? "✅ Yes" : "❌ No")}
        ${kvRow("Type", d.type)}
        ${kvRow("Operator", d.operator)}
        ${kvRow("Country", d.country)}
        ${kvRow("Region / City", d.location)}
        ${kvRow("Timezones", (d.timezones || []).join(", "))}
        ${kvRow("E.164", d.e164)}
        ${kvRow("National number", d.national_number)}
        ${kvRow("Country code", `+${d.country_code}`)}
      </div>
      <div class="cta-row">
        <a class="btn" href="${esc(d.maps_url)}" target="_blank" rel="noopener">Open in Google Maps</a>
      </div>`);
    if (d.latitude != null && d.longitude != null) {
      showLookupMarker(d.latitude, d.longitude, d.country,
        `<b>${esc(d.country)}</b><br>Operator: ${esc(d.operator || "?")}<br>Region: ${esc(d.location || "?")}`);
    }
  } catch (err) {
    renderError(el, err.message);
  }
}

/* ------------------------------------------------------------------ */
/* Wiring                                                              */
/* ------------------------------------------------------------------ */
document.getElementById("create-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const nameInput = document.getElementById("session-name");
  const ttlSelect = document.getElementById("session-ttl");
  const ttlHours = parseInt(ttlSelect.value, 10);

  try {
    const s = await api("/api/sessions", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name: nameInput.value.trim(),
        ttl_hours: ttlHours > 0 ? ttlHours : null,
      }),
    });
    nameInput.value = "";
    copyShareLink(s.token);
    refreshSessions();
  } catch (err) {
    toast(`Create failed: ${err.message}`);
  }
});

document.getElementById("ip-form").addEventListener("submit", handleIpLookup);
document.getElementById("phone-form").addEventListener("submit", handlePhoneLookup);

document.querySelectorAll(".tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((t) => t.classList.remove("active"));
    document.querySelectorAll(".tab-content").forEach((c) => c.classList.remove("active"));
    tab.classList.add("active");
    document.getElementById(`tab-${tab.dataset.tab}`).classList.add("active");
    if (tab.dataset.tab === "live") map.invalidateSize();
  });
});

// Initialize
fetchCsrfToken();
refreshSessions();           // initial snapshot
connectSSE();                // then instant updates via Server-Sent Events
loadStats();                 // load dashboard stats
setTimeout(() => map.invalidateSize(), 300);
