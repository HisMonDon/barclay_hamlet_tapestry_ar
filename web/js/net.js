// Helpers for the Python API. POSTs carry X-Tapestry; the server also checks the page's origin.
//
// The same page also runs as a static site (export_static.py): the board animation is then a pre-rendered video
// and the show data are plain files next to the page.
export const STATIC = !!document.querySelector('meta[name="tapestry-static"]');
export const URLS = STATIC
  ? { info: "data/info.json", path: "data/camera-path.json", audio: "media/soundtrack.wav", desk: "media/desk.jpg" }
  : { info: "/api/info", path: "/api/camera-path", audio: "/api/audio.wav", desk: "/api/desk.jpg" };

// The hosted page uses a manually started backend for Scan without leaving the site.
// ?backend=http://localhost:8001 points at another port; Virtual Explore stays entirely static.
const LOOPBACK_HOSTS = new Set(["localhost", "127.0.0.1", "[::1]"]);
export const BACKEND = (() => {
  const given = new URLSearchParams(location.search).get("backend");
  try {
    const u = new URL(given || "http://localhost:8000");
    // A link must never redirect camera uploads to an off-computer server while keeping this page's origin.
    if (["http:", "https:"].includes(u.protocol) && LOOPBACK_HOSTS.has(u.hostname)
        && !u.username && !u.password) return u.origin;
  } catch { /* fall through to the default */ }
  return "http://localhost:8000";
})();

export function apiURL(path) {
  return STATIC && path.startsWith("/api/") ? `${BACKEND}${path}` : path;
}

export function apiFetch(path, options = {}) {
  const hostedAPI = STATIC && path.startsWith("/api/");
  return fetch(apiURL(path), {
    ...(hostedAPI ? { credentials: "omit", targetAddressSpace: "loopback" } : {}),
    ...options,
  });
}

export async function pingBackend() {
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), 2500);
  try {
    const res = await apiFetch("/api/ping", { cache: "no-store", signal: ctl.signal });
    return res.ok && (await res.json()).ok === true;
  } catch {
    return false;
  } finally {
    clearTimeout(timer);
  }
}

export async function getJSON(url, options = {}) {
  const res = await apiFetch(url, { cache: "no-store", ...options });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.error || `HTTP ${res.status}`);
  return body;
}

export async function postJSON(url, data) {
  const res = await apiFetch(url, {
    method: "POST",
    cache: "no-store",
    headers: { "Content-Type": "application/json", "X-Tapestry": "1" },
    body: JSON.stringify(data),
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.error || `HTTP ${res.status}`);
  return body;
}
