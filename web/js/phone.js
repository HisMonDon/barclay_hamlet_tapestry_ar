// Phone camera page: streams JPEG frames to the computer running server.py while Scan Tapestry uses the phone.
const key = new URLSearchParams(location.search).get("k") || "";
const device = /iPhone|iPad/.test(navigator.userAgent) ? "iPhone" : /Android/.test(navigator.userAgent) ? "Android phone" : "Phone";
const $ = (id) => document.getElementById(id);
const video = $("preview");
const cap = document.createElement("canvas");
const capG = cap.getContext("2d");

let stream = null;
let wantCamera = false;
let facing = "environment";
let active = false;
let everActive = false;
let inactiveSince = 0;
let wakeLock = null;
let sending = false;
let lastSend = 0;
let failures = 0;
let sent = 0;

function status(text, tone = "") {
  const el = $("status");
  el.textContent = text;
  el.dataset.tone = tone;
}

function api(path, opts = {}) {
  return fetch(`${path}?k=${encodeURIComponent(key)}`, { cache: "no-store", ...opts, headers: { "X-Device": device, ...(opts.headers || {}) } });
}

async function startCamera(fromUser) {
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    status(window.isSecureContext ? "This browser cannot use the camera. Try Chrome or Safari."
      : "The camera needs the secure link. Open the https:// link from the QR code.", "bad");
    return;
  }
  wantCamera = true;
  status("Waiting for camera permission…", "warn");
  try {
    stopTracks();
    stream = await navigator.mediaDevices.getUserMedia({
      video: { facingMode: { ideal: facing }, width: { ideal: 1280 }, height: { ideal: 720 }, frameRate: { ideal: 30 } },
      audio: false,
    });
  } catch (err) {
    stream = null;
    if (err.name === "NotAllowedError" || err.name === "SecurityError") {
      status(fromUser ? "Camera permission was denied. Allow the camera for this site in the browser settings, then tap Start camera."
        : "Tap Start camera to turn the camera back on.", "bad");
      wantCamera = fromUser ? false : wantCamera;
    } else {
      status(`The camera could not start (${err.name}). Close other camera apps and try again.`, "bad");
    }
    render();
    return;
  }
  video.srcObject = stream;
  await video.play().catch(() => {});
  requestWakeLock();
  render();
  report();
}

function stopTracks() {
  if (stream) stream.getTracks().forEach((t) => t.stop());
  stream = null;
  video.srcObject = null;
}

function stopCamera() {
  wantCamera = false;
  stopTracks();
  releaseWakeLock();
  render();
  report();
}

function render() {
  $("start").hidden = !!stream;
  $("stop").hidden = !stream;
  $("flip").hidden = !stream;
  $("preview-empty").hidden = !!stream;
}

function report() {
  if (!key) return;
  if (stream && active) status(`Sending to the computer${sent ? "" : "…"}`, "ok");
  else if (stream) status("Camera on. On the computer, open Scan Tapestry, choose Phone and press Connect phone.", "warn");
  else if (wantCamera && everActive && !active) status("Paused while the computer is not in Scan Tapestry. The camera comes back on by itself.", "warn");
  else if (active) status("The computer is waiting for this phone. Tap Start camera.", "warn");
  else status("Linked to the computer. Tap Start camera.", "");
}

async function requestWakeLock() {
  try {
    if ("wakeLock" in navigator && !wakeLock) {
      wakeLock = await navigator.wakeLock.request("screen");
      wakeLock.addEventListener("release", () => (wakeLock = null));
    }
  } catch { /* not supported or refused: the tips ask to keep the phone awake */ }
}

function releaseWakeLock() {
  if (wakeLock) wakeLock.release().catch(() => {});
  wakeLock = null;
}

function setActive(on) {
  if (on && !active) {
    everActive = true;
    if (wantCamera && !stream) startCamera(false);
  }
  if (!on && active) inactiveSince = performance.now();
  active = on;
}

// While the computer is not using the phone, ping once a second; release the camera after it has left.
async function ping() {
  if (!sending) {
    try {
      const res = await api("/api/phone/status");
      if (res.status === 403) {
        status("This link has expired. Scan the QR code on the computer again.", "bad");
        return setTimeout(ping, 4000);
      }
      const body = await res.json();
      failures = 0;
      setActive(!!body.active);
      if (!active && stream && everActive && performance.now() - inactiveSince > 3000) stopTracks(), render();
      report();
    } catch {
      failures++;
      status(`Cannot reach the computer at ${location.host}. Check that both are on the same Wi-Fi and server.py is running.`, "bad");
    }
  }
  setTimeout(ping, active && stream ? 2000 : 1000);
}

// A timer rather than animation frames, so frames keep flowing when the browser deprioritises painting.
async function sendLoop() {
  setTimeout(sendLoop, 15);
  if (sending || !stream || !active || video.readyState < 2) return;
  const now = performance.now();
  if (now - lastSend < 40) return;
  lastSend = now;
  sending = true;
  try {
    const w = video.videoWidth, h = video.videoHeight;
    const s = Math.min(1, 1280 / Math.max(w, h));
    cap.width = Math.round(w * s);
    cap.height = Math.round(h * s);
    capG.drawImage(video, 0, 0, cap.width, cap.height);
    const blob = await new Promise((ok) => cap.toBlob(ok, "image/jpeg", 0.78));
    const res = await api("/api/phone/frame", { method: "POST", body: blob, headers: { "Content-Type": "image/jpeg" } });
    if (res.status === 403) {
      status("This link has expired. Scan the QR code on the computer again.", "bad");
      active = false;
      return;
    }
    const body = await res.json();
    sent++;
    if (sent === 1) report();
    setActive(!!body.active);
    if (!active) report();
  } catch {
    status("Lost the connection to the computer. Retrying…", "bad");
    await new Promise((ok) => setTimeout(ok, 800));
  } finally {
    sending = false;
  }
}

$("start").addEventListener("click", () => startCamera(true));
$("stop").addEventListener("click", stopCamera);
$("flip").addEventListener("click", () => {
  facing = facing === "environment" ? "user" : "environment";
  startCamera(true);
});
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible" && stream) requestWakeLock();
});

if (!key) {
  status("Open this page from the QR code shown in Scan Tapestry on the computer.", "bad");
  $("start").disabled = true;
} else {
  render();
  ping();
  sendLoop();
}
