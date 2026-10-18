import { Playback, formatTime } from "./playback.js";
import { ScanStage, listCameras } from "./scan.js";
import { getJSON, postJSON, STATIC, URLS } from "./net.js";

const $ = (id) => document.getElementById(id);
const PREFS_KEY = "tapestry.ui";

const prefs = loadPrefs();
let info, pb, virtual, scan;
let mode = "virtual";
let virtualStatus = null;
let dragging = false;
let resumeAfterDrag = false;
let lastSecond = -1;
let toastTimer = 0;
let idleTimer = 0;

boot();

async function boot() {
  showLoading("Loading the show");
  try {
    info = await getJSON(URLS.info);
  } catch (err) {
    hideLoading();
    showNotice(STATIC
      ? { kicker: "Loading", tone: "bad", title: "The show could not be loaded", text: `Check the connection and reload the page. (${err.message})`, actions: ["reload"] }
      : { kicker: "Renderer", tone: "bad", title: "The renderer is not running",
        text: `Start it with .venv\\Scripts\\python.exe server.py, then reload this page. (${err.message})`, actions: ["reload"] });
    return;
  }
  document.title = `${info.title.split(":")[0]} — Virtual Explore`;
  const sub = info.title.split(":")[1];
  if (sub) $("show-title").textContent = sub.trim();
  $("gap-mm").textContent = info.board.gap_mm;
  $("min-markers").textContent = info.min_markers;
  if (STATIC) makeStatic();

  pb = new Playback(info);
  pb.volume = prefs.volume ?? 0.9;
  pb.muted = !!prefs.muted;
  if (pb.audioOn) pb.loadSoundtrack(URLS.audio);
  else removeSound();
  pb.addEventListener("state", () => (mode === "scan" && scan ? renderStatus() : renderTransportState()));
  pb.addEventListener("seek", () => (lastSecond = -1));
  pb.addEventListener("beat", renderScene);
  pb.addEventListener("volume", renderVolume);
  pb.addEventListener("audio", () => { if (pb.audioError) toast(pb.audioError, 6000); });

  buildThread();
  wireTransport();
  wireSetup();
  wireKeys();
  wireFullscreen();
  renderVolume();
  renderScene();
  renderTransportState();

  scan = new ScanStage({ canvas: $("scan-canvas"), playback: pb, info, onChange: renderStatus });
  scan.setView({ follow: prefs.scanFollow ?? true, overlay: false });
  $("cine-switch").checked = scan.follow;

  const { VirtualStage } = await import("./virtual.js").catch((err) => {
    toast(`3D view failed to load: ${err.message}`, 8000);
    return {};
  });
  if (VirtualStage) {
    virtual = new VirtualStage({
      canvas: $("virtual-canvas"), info, playback: pb,
      onStatus: (s) => { virtualStatus = s; renderStatus(); },
      onFollowChange: (on) => { renderFollow(); if (!on) toast("Follow animation is off. Press F or the button to turn it back on."); },
    });
    virtual.onFirstFrame = () => { hideLoading(); renderStatus(); };
    await virtual.init();
  }
  pb.setGate(() => (virtual ? virtual.gateReason() : null));
  if (virtual) {
    virtual.activate();
    if (!virtual.hasFrame) showLoading("Laying out the board");
  } else {
    hideLoading();
  }
  if (location.hash === "#scan" && !STATIC) setMode("scan");
  requestAnimationFrame(tick);
  window.addEventListener("pagehide", () => {
    if (scan.kind && !STATIC) fetch("/api/source", { method: "POST", keepalive: true, headers: { "Content-Type": "application/json", "X-Tapestry": "1" }, body: '{"kind":"none"}' });
  });
}

// ---- main loop -------------------------------------------------------------------------------------------
function tick() {
  requestAnimationFrame(tick);
  pb.tick();
  renderTransport();
}

// ---- modes ----------------------------------------------------------------------------------------------
async function setMode(next) {
  if (next === mode || !info) return;
  mode = next;
  for (const tab of document.querySelectorAll(".mode-tab")) {
    const on = tab.dataset.mode === mode;
    tab.setAttribute("aria-selected", String(on));
    tab.tabIndex = on ? 0 : -1;
  }
  $("stage").setAttribute("aria-labelledby", `tab-${mode}`);
  for (const el of document.querySelectorAll(".setup-mode")) el.hidden = el.dataset.for !== mode;
  $("virtual-canvas").hidden = mode !== "virtual";
  $("scan-canvas").hidden = mode !== "scan";
  $("viewport").classList.toggle("scan", mode === "scan");
  for (const id of ["reset-view-btn", "zoom-in-btn", "zoom-out-btn"]) $(id).hidden = mode !== "virtual";
  $("zoom-in-btn").parentElement.hidden = mode !== "virtual";
  document.title = `${info.title.split(":")[0]} — ${mode === "scan" ? "Scan Tapestry" : "Virtual Explore"}`;
  history.replaceState(null, "", mode === "scan" ? "#scan" : location.pathname + location.search);
  hideLoading();
  if (mode === "scan") {
    virtual && virtual.deactivate();
    pb.setGate(() => scan.gateReason());
    scan.activate();
  } else {
    await scan.deactivate();
    if (mode !== "virtual") return;
    pb.setGate(() => (virtual ? virtual.gateReason() : null));
    if (virtual) {
      virtual.activate();
      if (!virtual.hasFrame) showLoading("Laying out the board");
    }
  }
  renderFollow();
  renderStatus();
}

// ---- status & notices ------------------------------------------------------------------------------------
function renderStatus() {
  if (!info) return;
  const pill = $("status-pill");
  let d = null;
  if (mode === "scan") {
    d = scan.describe(sourceLabel());
    const line = $("source-status");
    line.textContent = d.line ? d.line.text : "";
    line.dataset.tone = d.line ? d.line.tone : "";
    $("source-stop").hidden = !scan.kind;
    if (d.notice) showNotice(d.notice);
    else hideNotice();
  } else {
    hideNotice();
    if (virtualStatus) d = { pill: virtualStatus };
    else if (pb.blocked) d = { pill: { tone: "busy", text: pb.blocked } };
  }
  if (d && d.pill) {
    pill.hidden = false;
    pill.dataset.tone = d.pill.tone || "";
    $("status-text").textContent = d.pill.text;
    const chips = $("marker-chips");
    if (d.pill.markers) {
      chips.hidden = false;
      chips.innerHTML = [0, 1, 2, 3].map((i) => `<span class="${d.pill.markers.includes(i) ? "on" : ""}">${i}</span>`).join("");
      chips.setAttribute("aria-hidden", "true");
      $("status-text").setAttribute("aria-label", `${d.pill.text}. Markers seen: ${d.pill.markers.join(", ") || "none"}`);
    } else {
      chips.hidden = true;
      $("status-text").removeAttribute("aria-label");
    }
  } else {
    pill.hidden = true;
  }
  renderTransportState();
}

const ACTIONS = {
  start: () => ({ label: startLabel(), primary: true, run: startSelectedSource }),
  retry: () => ({ label: "Try again", primary: true, run: startSelectedSource }),
  setup: () => ({ label: "Open setup", run: () => openSetup(true) }),
  stop: () => ({ label: "Stop", run: () => scan.stop() }),
  phone: () => ({ label: "Use a phone instead", run: () => { selectSource("phone"); openSetup(true); } }),
  reload: () => ({ label: "Reload", primary: true, run: () => location.reload() }),
};

function showNotice(n) {
  $("notice").hidden = false;
  const card = $("notice").querySelector(".notice-card");
  card.dataset.tone = n.tone || "";
  $("notice-kicker").textContent = n.kicker || "";
  $("notice-title").textContent = n.title || "";
  $("notice-text").textContent = n.text || "";
  const box = $("notice-actions");
  const key = (n.actions || []).join(",") + "|" + startLabel();
  if (box.dataset.key !== key) {
    box.dataset.key = key;
    box.replaceChildren(...(n.actions || []).map((a) => {
      const spec = ACTIONS[a]();
      const b = document.createElement("button");
      b.className = `btn ${spec.primary ? "primary" : "ghost"}`;
      b.textContent = spec.label;
      b.addEventListener("click", spec.run);
      return b;
    }));
  }
}

function hideNotice() {
  $("notice").hidden = true;
}

function showLoading(text) {
  $("loading-text").textContent = text;
  $("loading").hidden = false;
}

function hideLoading() {
  $("loading").hidden = true;
}

function toast(text, ms = 3500) {
  const el = $("toast");
  el.textContent = text;
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (el.hidden = true), ms);
}

// ---- transport ------------------------------------------------------------------------------------------
function buildThread() {
  const track = $("thread-track");
  track.replaceChildren();
  for (const b of info.beats) {
    const s = document.createElement("div");
    s.className = "stitch";
    s.style.flex = `${b.duration} 1 0`;
    s.title = `${b.id} ${b.name}`;
    const g = document.createElement("div");
    g.className = "stitch-gold";
    s.append(g);
    track.append(s);
  }
  const silence = info.beats.find((b) => b.silence);
  const hold = info.beats.find((b) => b.hold);
  for (const [beat, label] of [[pb.audioOn && silence, "silence"], [hold, "hold"]]) {
    if (!beat) continue;
    const z = document.createElement("div");
    z.className = "zone";
    z.style.left = `${(beat.start / info.end) * 100}%`;
    z.textContent = label;
    $("thread").append(z);
  }
  const sc = $("scrubber");
  sc.max = String(info.end);
  $("time-total").textContent = formatTime(info.end);
}

function wireTransport() {
  $("play-btn").addEventListener("click", () => pb.toggle());
  $("restart-btn").addEventListener("click", () => pb.restart());
  $("prev-btn").addEventListener("click", () => pb.step(-1));
  $("next-btn").addEventListener("click", () => pb.step(+1));
  $("mute-btn").addEventListener("click", () => { pb.setMuted(!pb.muted); savePrefs({ muted: pb.muted }); });
  $("volume").addEventListener("input", (e) => { pb.setVolume(Number(e.target.value)); savePrefs({ volume: pb.volume, muted: pb.muted }); });

  const sc = $("scrubber");
  const begin = () => {
    if (dragging) return;
    dragging = true;
    $("thread").classList.add("dragging");
    resumeAfterDrag = pb.wantPlay;
    if (resumeAfterDrag) pb.pause();
  };
  const end = () => {
    if (!dragging) return;
    dragging = false;
    $("thread").classList.remove("dragging");
    if (resumeAfterDrag) pb.play();
  };
  sc.addEventListener("pointerdown", begin);
  sc.addEventListener("pointerup", end);
  sc.addEventListener("pointercancel", end);
  sc.addEventListener("change", end);
  sc.addEventListener("input", () => pb.seek(Number(sc.value)));
  sc.addEventListener("keydown", (e) => {
    const jump = { ArrowLeft: -1, ArrowRight: 1, ArrowDown: -1, ArrowUp: 1 }[e.key];
    if (jump) {
      pb.seek(pb.time + jump * (e.shiftKey ? 5 : 1));
      e.preventDefault();
      e.stopPropagation();
    }
  });
}

function renderTransport() {
  const t = pb.time;
  const sc = $("scrubber");
  if (!dragging) sc.value = String(t);
  const stitches = $("thread-track").children;
  for (let k = 0; k < info.beats.length; k++) {
    const b = info.beats[k];
    const p = Math.min(1, Math.max(0, (t - b.start) / b.duration));
    const w = `${(p * 100).toFixed(2)}%`;
    const g = stitches[k].firstChild;
    if (g.style.width !== w) g.style.width = w;
  }
  $("needle").style.left = `${(t / info.end) * 100}%`;
  const sec = Math.floor(t + 1e-6);
  if (sec !== lastSecond) {
    lastSecond = sec;
    $("time-elapsed").textContent = formatTime(t);
    const b = info.beats[pb.beatAt(t)];
    sc.setAttribute("aria-valuetext", `${formatTime(t)} of ${formatTime(info.end)}, ${b.id} ${b.name}`);
  }
}

function renderScene() {
  const i = pb.beatAt(pb.time);
  const b = info.beats[i];
  $("scene-id").textContent = b.id;
  $("scene-name").textContent = b.name;
  $("scene-desc").textContent = b.desc;
  $("scene").title = b.desc;
  const stitches = $("thread-track").children;
  for (let k = 0; k < stitches.length; k++) stitches[k].classList.toggle("current", k === i);
  $("prev-btn").disabled = pb.time <= 0 && i === 0;
  $("next-btn").disabled = i >= info.beats.length - 1 && pb.time >= info.end;
  lastSecond = -1;
}

function renderTransportState() {
  if (!pb) return;
  const btn = $("play-btn");
  const playing = pb.wantPlay;
  btn.classList.toggle("playing", playing);
  btn.classList.toggle("waiting", playing && !!pb.blocked);
  btn.setAttribute("aria-label", playing ? (pb.blocked ? `Pause (waiting: ${pb.blocked})` : "Pause") : "Play");
  if (mode === "virtual" && !virtualStatus) {
    const pill = $("status-pill");
    if (pb.blocked) {
      pill.hidden = false;
      pill.dataset.tone = "busy";
      $("status-text").textContent = pb.blocked;
      $("marker-chips").hidden = true;
    } else {
      pill.hidden = true;
    }
  }
  if (!playing) document.documentElement.classList.remove("idle");
}

function renderVolume() {
  $("mute-btn").setAttribute("aria-pressed", String(pb.muted));
  $("mute-btn").setAttribute("aria-label", pb.muted ? "Unmute" : "Mute");
  $("volume").value = String(pb.muted ? 0 : pb.volume);
}

// ---- follow / view tools ---------------------------------------------------------------------------------
function currentFollow() {
  return mode === "scan" ? scan.follow : !!(virtual && virtual.follow);
}

function setFollow(on) {
  if (mode === "scan") {
    scan.setView({ follow: on });
    savePrefs({ scanFollow: on });
  } else if (virtual) {
    virtual.setFollow(on, true);
  }
  renderFollow();
}

function renderFollow() {
  const on = currentFollow();
  $("follow-btn").setAttribute("aria-pressed", String(on));
  $("follow-switch").checked = !!(virtual && virtual.follow);
  $("cine-switch").checked = scan.follow;
}

// ---- setup panel -----------------------------------------------------------------------------------------
function wireSetup() {
  for (const tab of document.querySelectorAll(".mode-tab")) {
    tab.addEventListener("click", () => setMode(tab.dataset.mode));
    tab.addEventListener("keydown", (e) => {
      if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
        const next = mode === "virtual" ? "scan" : "virtual";
        setMode(next);
        $(`tab-${next}`).focus();
        e.preventDefault();
        e.stopPropagation();
      }
    });
  }
  $("setup-toggle").addEventListener("click", () => openSetup($("setup-toggle").getAttribute("aria-expanded") !== "true"));
  const narrow = window.matchMedia("(max-width: 860px)").matches;
  openSetup(prefs.setupOpen ?? !narrow, false);

  $("follow-btn").addEventListener("click", () => setFollow(!currentFollow()));
  $("follow-switch").addEventListener("change", (e) => { if (virtual) virtual.setFollow(e.target.checked, true); renderFollow(); });
  $("cine-switch").addEventListener("change", (e) => { scan.setView({ follow: e.target.checked }); savePrefs({ scanFollow: e.target.checked }); renderFollow(); });
  $("overlay-switch").addEventListener("change", (e) => scan.setView({ overlay: e.target.checked }));
  for (const id of ["reset-view-btn", "reset-view-btn-2"]) $(id).addEventListener("click", () => virtual && virtual.resetView());
  $("zoom-in-btn").addEventListener("click", () => virtual && virtual.zoom(0.8));
  $("zoom-out-btn").addEventListener("click", () => virtual && virtual.zoom(1.25));

  if (STATIC) return;

  // camera sources
  for (const r of document.querySelectorAll('input[name="source"]')) r.addEventListener("change", () => selectSource(r.value));
  selectSource(prefs.source || "browser");
  $("source-start").addEventListener("click", startSelectedSource);
  $("source-stop").addEventListener("click", () => scan.stop());
  $("res-select").value = prefs.res || "1280x720";
  $("stream-url").value = prefs.streamUrl || "";
  $("stream-url").addEventListener("keydown", (e) => { if (e.key === "Enter") startSelectedSource(); });
  for (const id of ["device-select", "res-select"]) {
    $(id).addEventListener("change", () => {
      savePrefs({ deviceId: $("device-select").value, res: $("res-select").value });
      if (scan.kind === "browser") startSelectedSource();
    });
  }
  refreshCameras();
  navigator.mediaDevices && navigator.mediaDevices.addEventListener && navigator.mediaDevices.addEventListener("devicechange", refreshCameras);

  // phone link
  const ph = info.phone;
  if (ph.enabled) {
    const sel = $("phone-addr");
    sel.replaceChildren(...ph.urls.map((u, i) => new Option(new URL(u).host, String(i))));
    $("phone-addr-field").hidden = ph.urls.length < 2;
    const show = () => {
      const i = Number(sel.value || 0);
      $("phone-qr").src = `/api/phone-qr.png?i=${i}`;
      $("phone-url").textContent = ph.urls[i];
    };
    sel.addEventListener("change", show);
    show();
    $("copy-phone-url").addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText($("phone-url").textContent);
        toast("Link copied. Open it on the phone.");
      } catch {
        toast("Copy failed. Select the link text and copy it by hand.");
      }
    });
  } else {
    $("phone-link").hidden = true;
    $("phone-error").hidden = false;
    $("phone-error").textContent = ph.error || "The phone link is unavailable. Use a phone webcam app or a stream URL instead.";
  }
}

function openSetup(open, save = true) {
  $("setup-toggle").setAttribute("aria-expanded", String(open));
  $("workspace").classList.toggle("setup-closed", !open);
  if (save) savePrefs({ setupOpen: open });
  if (open && save === true && window.matchMedia("(max-width: 860px)").matches) $("setup").scrollIntoView({ behavior: "smooth", block: "start" });
  virtual && virtual.resize();
}

function selectSource(kind) {
  for (const r of document.querySelectorAll('input[name="source"]')) r.checked = r.value === kind;
  for (const p of document.querySelectorAll(".source-pane")) p.hidden = p.dataset.source !== kind;
  $("source-start").textContent = startLabel();
  savePrefs({ source: kind });
  if (scan && mode === "scan") renderStatus();
}

function selectedSource() {
  const r = document.querySelector('input[name="source"]:checked');
  return r ? r.value : "browser";
}

function startLabel() {
  return { browser: "Start camera", phone: "Connect phone", stream: "Connect stream" }[selectedSource()];
}

function sourceLabel() {
  if (scan.kind === "browser") return scan.trackLabel() || "Webcam";
  if (scan.kind === "phone") return "Phone";
  if (scan.kind === "stream") return "Stream";
  return "Camera";
}

function startSelectedSource() {
  const kind = selectedSource();
  if (kind === "browser") {
    scan.start("browser", { deviceId: $("device-select").value, res: $("res-select").value }).then(refreshCameras);
  } else if (kind === "phone") {
    scan.start("phone", {});
  } else {
    const url = $("stream-url").value.trim();
    if (!url) {
      toast("Enter a stream URL or camera number first.");
      openSetup(true);
      $("stream-url").focus();
      return;
    }
    savePrefs({ streamUrl: url });
    scan.start("stream", { url });
  }
}

async function refreshCameras() {
  const sel = $("device-select");
  const cams = await listCameras();
  const keep = sel.value || prefs.deviceId || "";
  const opts = [new Option("Default camera", "")];
  cams.forEach((c, i) => { if (c.deviceId) opts.push(new Option(c.label || `Camera ${i + 1}`, c.deviceId)); });
  sel.replaceChildren(...opts);
  if ([...sel.options].some((o) => o.value === keep)) sel.value = keep;
}

// A show exported without its soundtrack has no sound controls.
function removeSound() {
  $("mute-btn").hidden = true;
  $("volume").hidden = true;
  $("key-mute").hidden = true;
}

// The static site is Virtual Explore alone: no mode tabs, no camera setup.
function makeStatic() {
  document.documentElement.classList.add("static");
  document.querySelector(".modes").remove();
  document.querySelector('.setup-mode[data-for="scan"]').hidden = true;
  $("about-view").textContent = "The animation was rendered by the tapestry engine and is laid onto a virtual board, with the four tracking markers where they sit on the real one. The camera-tracked version runs from the project’s own app.";
}

// ---- keyboard ------------------------------------------------------------------------------------------
function wireKeys() {
  document.addEventListener("keydown", (e) => {
    if (e.defaultPrevented || e.ctrlKey || e.metaKey || e.altKey || !pb) return;
    const el = e.target;
    const tag = el.tagName;
    if (tag === "SELECT" || tag === "TEXTAREA" || (tag === "INPUT" && el.type !== "range" && el.type !== "checkbox" && el.type !== "radio")) return;
    const onControl = tag === "BUTTON" || (tag === "INPUT" && el.type !== "range");
    switch (e.key) {
      case " ":
      case "k":
        if (onControl) return;
        pb.toggle();
        break;
      case "ArrowLeft":
        if (tag === "INPUT") return;
        pb.step(-1);
        break;
      case "ArrowRight":
        if (tag === "INPUT") return;
        pb.step(1);
        break;
      case "Home":
        pb.restart();
        break;
      case "m":
      case "M":
        if (!pb.audioOn) return;
        pb.setMuted(!pb.muted);
        savePrefs({ muted: pb.muted });
        break;
      case "F":
        toggleFullscreen();
        break;
      case "f":
        setFollow(!currentFollow());
        break;
      case "0":
        if (mode === "virtual" && virtual) virtual.resetView();
        break;
      default:
        return;
    }
    e.preventDefault();
    wake();
  });
}

// ---- fullscreen -----------------------------------------------------------------------------------------
function wireFullscreen() {
  $("fullscreen-btn").addEventListener("click", toggleFullscreen);
  document.addEventListener("fullscreenchange", () => {
    const on = document.fullscreenElement === $("stage");
    document.documentElement.classList.toggle("is-fullscreen", on);
    $("fullscreen-btn").setAttribute("aria-label", on ? "Exit fullscreen" : "Enter fullscreen");
    wake();
  });
  for (const ev of ["pointermove", "pointerdown", "keydown"]) document.addEventListener(ev, wake, { passive: true });
}

function toggleFullscreen() {
  const stage = $("stage");
  const root = document.documentElement;
  if (stage.requestFullscreen) {
    if (document.fullscreenElement) document.exitFullscreen();
    else stage.requestFullscreen().catch((err) => toast(`Fullscreen was refused: ${err.message}`));
    return;
  }
  // iPhone Safari cannot make a page element fullscreen; fill the window instead.
  const on = !root.classList.contains("pseudo-fs");
  root.classList.toggle("pseudo-fs", on);
  root.classList.toggle("is-fullscreen", on);
  $("fullscreen-btn").setAttribute("aria-label", on ? "Exit fullscreen" : "Enter fullscreen");
}

function wake() {
  const root = document.documentElement;
  root.classList.remove("idle");
  clearTimeout(idleTimer);
  if (root.classList.contains("is-fullscreen")) {
    idleTimer = setTimeout(() => { if (pb && pb.running) root.classList.add("idle"); }, 2600);
  }
}

// ---- preferences -----------------------------------------------------------------------------------------
function loadPrefs() {
  try {
    return JSON.parse(localStorage.getItem(PREFS_KEY) || "{}");
  } catch {
    return {};
  }
}

function savePrefs(patch) {
  Object.assign(prefs, patch);
  try {
    localStorage.setItem(PREFS_KEY, JSON.stringify(prefs));
  } catch { /* private window: preferences last for this visit only */ }
}

// exposed for debugging from the console
window.tapestry = { get playback() { return pb; }, get virtual() { return virtual; }, get scan() { return scan; }, setMode, postJSON };
