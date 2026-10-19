// Small helpers for the local API. POSTs carry X-Tapestry so other web pages cannot drive the server.
//
// The same page also runs as a static site (export_static.py): the board animation is then a pre-rendered video
// and the show data are plain files next to the page.
export const STATIC = !!document.querySelector('meta[name="tapestry-static"]');
export const URLS = STATIC
  ? { info: "data/info.json", path: "data/camera-path.json", audio: "media/soundtrack.wav", desk: "media/desk.jpg" }
  : { info: "/api/info", path: "/api/camera-path", audio: "/api/audio.wav", desk: "/api/desk.jpg" };

// The static site cannot run Scan Tapestry itself. It sends the visitor to the local app (server.py) when that is
// running on their computer; ?backend=http://localhost:8001 points at another port.
export const BACKEND = (() => {
  const given = new URLSearchParams(location.search).get("backend");
  try {
    const u = new URL(given || "http://localhost:8000");
    if (u.protocol === "http:" || u.protocol === "https:") return u.origin;
  } catch { /* fall through to the default */ }
  return "http://localhost:8000";
})();

export async function pingBackend() {
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), 2500);
  try {
    const res = await fetch(`${BACKEND}/api/ping`, { cache: "no-store", signal: ctl.signal });
    return res.ok && (await res.json()).ok === true;
  } catch {
    return false;
  } finally {
    clearTimeout(timer);
  }
}

export async function getJSON(url) {
  const res = await fetch(url, { cache: "no-store" });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.error || `HTTP ${res.status}`);
  return body;
}

export async function postJSON(url, data) {
  const res = await fetch(url, {
    method: "POST",
    cache: "no-store",
    headers: { "Content-Type": "application/json", "X-Tapestry": "1" },
    body: JSON.stringify(data),
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.error || `HTTP ${res.status}`);
  return body;
}
