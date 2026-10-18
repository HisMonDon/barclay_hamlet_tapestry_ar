// Small helpers for the local API. POSTs carry X-Tapestry so other web pages cannot drive the server.
//
// The same page also runs as a static site (export_static.py): the board animation is then a pre-rendered video
// and the show data are plain files next to the page.
export const STATIC = !!document.querySelector('meta[name="tapestry-static"]');
export const URLS = STATIC
  ? { info: "data/info.json", path: "data/camera-path.json", audio: "media/soundtrack.wav", desk: "media/desk.jpg" }
  : { info: "/api/info", path: "/api/camera-path", audio: "/api/audio.wav", desk: "/api/desk.jpg" };

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
