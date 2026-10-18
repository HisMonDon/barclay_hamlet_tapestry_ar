// Scan Tapestry: camera frames go to the engine, which finds the ArUco markers and returns the composite.
//
// Sources:
//   browser  this computer's webcam through getUserMedia; frames are uploaded with each request
//   phone    the phone page posts frames to the server; we ask for renders of the latest one
//   stream   the server opens a stream URL (or an OpenCV camera number) with app.py's ResilientCamera
import { postJSON } from "./net.js";

const MAX_IN_FLIGHT = 2;
const IDLE_POLL_MS = 400;

export class ScanStage {
  constructor({ canvas, playback, info, onChange }) {
    this.canvas = canvas;
    this.g = canvas.getContext("2d");
    this.pb = playback;
    this.info = info;
    this.onChange = onChange;
    this.active = false;
    this.kind = null;
    this.state = "idle";
    this.message = "";
    this.locked = false;
    this.tracking = "searching";
    this.seen = [];
    this.follow = true;
    this.overlay = false;
    this.inFlight = 0;
    this.seq = 0;
    this.shown = 0;
    this.latency = 0.08;
    this.lastMeta = null;
    this.frozenKey = null;
    this.pollAt = 0;
    this.token = 0;
    this.stream = null;
    this.resume = null;
    this.serverStatus = null;
    this.video = document.createElement("video");
    this.video.muted = true;
    this.video.playsInline = true;
    this.video.setAttribute("playsinline", "");
    this.cap = typeof OffscreenCanvas !== "undefined" ? new OffscreenCanvas(16, 16) : document.createElement("canvas");
    this.capG = this.cap.getContext("2d");
  }

  // ---- lifecycle ----------------------------------------------------------------------------------------
  activate() {
    this.active = true;
    const loop = () => {
      if (!this.active) return;
      this.raf = requestAnimationFrame(loop);
      this.pump();
    };
    this.raf = requestAnimationFrame(loop);
    if (this.resume) {
      const r = this.resume;
      this.resume = null;
      this.start(r.kind, r.opts);
    } else {
      this.set("idle");
    }
  }

  // Leaving Scan mode releases the camera; it reopens the same source when the user comes back.
  async deactivate() {
    this.active = false;
    cancelAnimationFrame(this.raf);
    if (this.kind) this.resume = { kind: this.kind, opts: this.opts };
    await this.stop();
  }

  gateReason() {
    if (!this.kind) return "Start a camera to begin";
    if (!this.locked) return "Looking for the markers";
    return null;
  }

  set(state, message = "") {
    this.state = state;
    this.message = message;
    this.pb.update();
    this.onChange();
  }

  // ---- sources ------------------------------------------------------------------------------------------
  async start(kind, opts = {}) {
    await this.stop();
    const token = ++this.token;
    this.kind = kind;
    this.opts = opts;
    this.locked = false;
    this.tracking = "searching";
    this.seen = [];
    this.lastMeta = null;
    this.frozenKey = null;
    this.shown = this.seq;
    this.clearCanvas();
    try {
      if (kind === "browser") {
        await this.openWebcam(opts, token);
        if (token !== this.token) return;
        this.serverStatus = await postJSON("/api/source", { kind: "browser" });
        this.set("connecting", "Starting the camera");
      } else {
        this.set(kind === "phone" ? "waiting" : "connecting", kind === "phone" ? "" : "Opening the stream");
        this.serverStatus = await postJSON("/api/source", { kind, source: opts.url || "" });
        if (token !== this.token) return;
        this.applyServerStatus(this.serverStatus);
      }
    } catch (err) {
      if (token !== this.token) return;
      this.kind = null;
      if (err.cameraState) this.set(err.cameraState, err.message);
      else this.set("unavailable", err.message);
      this.releaseWebcam();
    }
  }

  async stop() {
    this.token++;
    const had = this.kind;
    this.kind = null;
    this.locked = false;
    this.releaseWebcam();
    if (had) {
      try { await postJSON("/api/source", { kind: "none" }); } catch { /* server gone; nothing to release */ }
    }
    if (this.active) this.set("idle");
    else this.pb.update();
  }

  async openWebcam(opts, token) {
    const md = navigator.mediaDevices;
    if (!md || !md.getUserMedia) {
      throw cameraError("unavailable", window.isSecureContext
        ? "This browser cannot use cameras. Try Chrome, Edge or Firefox."
        : "Cameras only work on a secure page. Open the UI at http://localhost:" + location.port + " on this computer.");
    }
    const [w, h] = (opts.res || "1280x720").split("x").map(Number);
    const video = { width: { ideal: w }, height: { ideal: h }, frameRate: { ideal: 30 } };
    if (opts.deviceId) video.deviceId = { exact: opts.deviceId };
    this.set("permission");
    let stream;
    try {
      stream = await md.getUserMedia({ video, audio: false });
    } catch (err) {
      throw describeGumError(err);
    }
    if (token !== this.token) {
      stream.getTracks().forEach((t) => t.stop());
      throw cameraError("idle", "");
    }
    this.stream = stream;
    const track = stream.getVideoTracks()[0];
    track.addEventListener("ended", () => {
      if (this.stream === stream) {
        this.kind = null;
        this.releaseWebcam();
        this.set("unavailable", "The camera stopped. It may have been unplugged or taken by another app.");
      }
    });
    this.video.srcObject = stream;
    await this.video.play().catch(() => {});
  }

  releaseWebcam() {
    if (this.stream) this.stream.getTracks().forEach((t) => t.stop());
    this.stream = null;
    this.video.srcObject = null;
  }

  trackLabel() {
    const t = this.stream && this.stream.getVideoTracks()[0];
    return t ? t.label : "";
  }

  applyServerStatus(st) {
    this.serverStatus = st;
    if (!this.kind || this.kind === "browser") return;
    const map = { waiting: "waiting", connecting: "connecting", unavailable: "unavailable", reconnecting: "reconnecting" };
    if (this.lastMeta && st.state === "live") return;
    if (map[st.state]) this.set(map[st.state], st.message || "");
  }

  // ---- frames -------------------------------------------------------------------------------------------
  requestTime() {
    const pb = this.pb;
    if (pb.running) return Math.min(pb.end, pb.heardTime + Math.min(0.25, this.latency));
    return pb.time;
  }

  async pump() {
    if (!this.active || !this.kind || this.inFlight >= MAX_IN_FLIGHT) return;
    const now = performance.now();
    if (now < this.pollAt) return;
    const t = this.requestTime();
    const key = `${this.follow}:${this.overlay}`;
    // From the hold onwards the picture freezes, camera included, as in the projected show.
    if (t >= this.pb.hold && this.frozenKey === key) return;
    if (this.kind === "browser" && (this.video.readyState < 2 || !this.video.videoWidth)) return;
    const seq = ++this.seq;
    const kind = this.kind;
    const token = this.token;
    this.inFlight++;
    const started = performance.now();
    try {
      const body = kind === "browser" ? await this.grab() : null;
      if (token !== this.token || (kind === "browser" && !body)) return;
      const q = `t=${t.toFixed(4)}&follow=${this.follow ? 1 : 0}&overlay=${this.overlay ? 1 : 0}&source=${kind === "browser" ? "browser" : "server"}`;
      const res = await fetch(`/api/scan?${q}`, {
        method: "POST", body, cache: "no-store",
        headers: { "X-Tapestry": "1", "Content-Type": body ? "image/jpeg" : "application/octet-stream" },
      });
      if (token !== this.token) return;
      if (!res.ok) throw new Error((await res.json().catch(() => ({}))).error || `HTTP ${res.status}`);
      if ((res.headers.get("Content-Type") || "").startsWith("application/json")) {
        this.applyServerStatus(await res.json());
        this.pollAt = performance.now() + IDLE_POLL_MS;
        return;
      }
      const meta = JSON.parse(res.headers.get("X-Meta") || "{}");
      const bmp = await createImageBitmap(await res.blob());
      if (token !== this.token || seq < this.shown) { bmp.close(); return; }
      this.shown = seq;
      this.latency = 0.8 * this.latency + 0.2 * ((performance.now() - started) / 1000);
      this.draw(bmp);
      this.accept(meta, key);
    } catch (err) {
      if (token !== this.token) return;
      this.set("error", `The renderer is not answering (${err.message}). Check that server.py is still running.`);
      this.pollAt = performance.now() + 1500;
    } finally {
      this.inFlight--;
    }
  }

  async grab() {
    const v = this.video;
    const long = Math.max(v.videoWidth, v.videoHeight);
    const s = Math.min(1, 1920 / long);
    const w = Math.round(v.videoWidth * s), h = Math.round(v.videoHeight * s);
    if (this.cap.width !== w || this.cap.height !== h) { this.cap.width = w; this.cap.height = h; }
    this.capG.drawImage(v, 0, 0, w, h);
    if (this.cap.convertToBlob) return this.cap.convertToBlob({ type: "image/jpeg", quality: 0.85 });
    return new Promise((ok) => this.cap.toBlob(ok, "image/jpeg", 0.85));
  }

  draw(bmp) {
    if (this.canvas.width !== bmp.width || this.canvas.height !== bmp.height) {
      this.canvas.width = bmp.width;
      this.canvas.height = bmp.height;
    }
    this.g.drawImage(bmp, 0, 0);
    bmp.close();
  }

  clearCanvas() {
    this.g.fillStyle = "#000";
    this.g.fillRect(0, 0, this.canvas.width, this.canvas.height);
  }

  accept(meta, key) {
    const before = `${this.state}|${this.locked}|${this.seen.join()}`;
    this.lastMeta = meta;
    this.locked = !!meta.locked;
    this.tracking = meta.tracking;
    this.seen = meta.seen || [];
    this.frozenKey = meta.frozen && this.pb.time >= this.pb.hold ? key : null;
    const src = meta.source || {};
    if (this.kind !== "browser" && src.state === "reconnecting") this.state = "reconnecting";
    else this.state = this.tracking === "searching" ? "searching" : this.tracking === "lost" ? "lost" : "ready";
    this.message = src.state === "reconnecting" ? src.message : "";
    if (`${this.state}|${this.locked}|${this.seen.join()}` !== before) {
      this.pb.update();
      this.onChange();
    }
  }

  setView({ follow, overlay }) {
    if (follow !== undefined) this.follow = follow;
    if (overlay !== undefined) this.overlay = overlay;
    this.frozenKey = null;
  }

  // ---- presentation -------------------------------------------------------------------------------------
  // What the viewport and setup panel should say for the current state.
  describe(sourceLabel) {
    const markers = this.seen;
    const n = this.info.min_markers;
    const waiting = this.pb.wantPlay && !this.pb.running;
    switch (this.state) {
      case "idle":
        return { notice: { kicker: "Scan Tapestry", title: "Point a camera at the tapestry",
          text: "Use this computer’s webcam, link a phone, or enter a stream URL. The show waits until it has found the four markers.",
          actions: ["start", "setup"] }, line: { tone: "", text: "No camera running." } };
      case "permission":
        return { pill: { tone: "busy", text: "Waiting for camera permission" },
          notice: { kicker: "Camera permission", title: "Allow camera access", text: "Your browser is asking to use the camera. Choose Allow to continue." },
          line: { tone: "", text: "Waiting for permission…" } };
      case "connecting":
        return { pill: { tone: "busy", text: this.message || "Connecting to the camera" }, line: { tone: "", text: `${this.message || "Connecting"}…` },
          notice: this.kind === "browser" ? null : { kicker: "Connecting", title: "Opening the stream", text: "Waiting for the first frame. Check the URL if this takes more than a few seconds.", actions: ["stop", "setup"] } };
      case "waiting":
        return { pill: { tone: "busy", text: "Waiting for the phone" },
          notice: { kicker: "Phone camera", title: "Waiting for the phone",
            text: this.message || "Scan the QR code in the setup panel with the phone, then tap Start camera on it.", actions: ["setup", "stop"] },
          line: { tone: "warn", text: this.message || "Waiting for the phone to connect." } };
      case "searching":
        return { pill: { tone: "busy", text: waiting ? "Looking for the markers — the show starts when they are found" : `Searching for markers (need ${n} of 4)`, markers },
          line: { tone: "warn", text: `${sourceLabel}: searching for markers. Seen: ${markers.length ? markers.join(", ") : "none"}.` } };
      case "ready":
        return { pill: { tone: "ok", text: this.pb.wantPlay ? "Tracking" : "Markers locked — ready to play", markers },
          line: { tone: "ok", text: `${sourceLabel}: markers locked.` } };
      case "lost":
        return { pill: { tone: "warn", text: "Tracking lost — holding the last position", markers },
          line: { tone: "warn", text: `Tracking lost. Keep at least ${n} markers in view; the last good position is held.` } };
      case "reconnecting":
        return { pill: { tone: "warn", text: "Camera reconnecting — showing the last frame", markers },
          line: { tone: "warn", text: this.message || "The camera stopped sending frames. Retrying." } };
      case "denied":
        return { notice: { kicker: "Camera blocked", tone: "bad", title: "Camera permission was denied",
          text: this.message, actions: ["retry", "phone"] }, line: { tone: "bad", text: "Camera permission was denied." } };
      case "unavailable":
        return { notice: { kicker: "Camera unavailable", tone: "bad", title: "The camera could not be opened", text: this.message, actions: ["retry", "setup"] },
          line: { tone: "bad", text: this.message } };
      case "error":
      default:
        return { notice: { kicker: "Renderer", tone: "bad", title: "Lost contact with the renderer", text: this.message, actions: ["retry"] },
          line: { tone: "bad", text: this.message } };
    }
  }
}

function cameraError(state, message) {
  const e = new Error(message);
  e.cameraState = state;
  return e;
}

function describeGumError(err) {
  const name = err && err.name;
  if (name === "NotAllowedError" || name === "SecurityError" || name === "PermissionDeniedError") {
    return cameraError("denied", "Allow the camera for this page: click the camera icon in the address bar (or the lock icon → Site settings), choose Allow, then press Try again. On Windows, also check Settings → Privacy & security → Camera.");
  }
  if (name === "NotFoundError" || name === "DevicesNotFoundError" || name === "OverconstrainedError") {
    return cameraError("unavailable", "No matching camera was found. Plug one in, or pick another camera in the setup panel.");
  }
  if (name === "NotReadableError" || name === "TrackStartError" || name === "AbortError") {
    return cameraError("unavailable", "The camera is busy or was disconnected. Close other apps that use it (Camera, Teams, Zoom, OBS) and try again.");
  }
  return cameraError("unavailable", `The camera could not start (${name || "error"}: ${err && err.message}).`);
}

export async function listCameras() {
  const md = navigator.mediaDevices;
  if (!md || !md.enumerateDevices) return [];
  try {
    return (await md.enumerateDevices()).filter((d) => d.kind === "videoinput");
  } catch {
    return [];
  }
}
