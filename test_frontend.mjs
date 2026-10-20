// Browser-independent regressions: node --test test_frontend.mjs
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

const playbackSource = readFileSync(new URL("./web/js/playback.js", import.meta.url), "utf8");
const { Playback } = await import(`data:text/javascript;base64,${Buffer.from(playbackSource).toString("base64")}`);
const show = { beats: [{ start: 0 }, { start: 3 }], end: 8, hold: 6, audio: true };

test("Pause cancels Play while browser audio permission is pending", async () => {
  let finishResume;
  const pb = new Playback(show);
  pb.ctx = { state: "suspended", resume: () => new Promise((resolve) => { finishResume = resolve; }) };
  const pending = pb.play();
  assert.equal(pb.wantPlay, true);
  pb.pause();
  finishResume();
  await pending;
  assert.equal(pb.wantPlay, false);
  assert.equal(pb.running, false);
});

test("an interrupted audio context does not freeze or jump the show clock", (t) => {
  let now = 1000;
  t.mock.method(performance, "now", () => now);
  const pb = new Playback(show);
  pb.ctx = { state: "suspended", currentTime: 5 };
  Object.assign(pb, { wantPlay: true, running: true, src: "audio", t0: 1, c0: 4 });
  pb.tick();
  assert.equal(pb.src, "perf");
  assert.equal(pb.t0, 2);
  assert.equal(pb.running, true);
  now += 500;
  assert.equal(pb.time, 2.5);
  pb.pause();
});

test("the loading gate preserves a seek and pending Play until a frame arrives", async (t) => {
  let now = 1000;
  t.mock.method(performance, "now", () => now);
  const pb = new Playback({ ...show, audio: false });
  let loading = true;
  pb.setGate(() => loading ? "Loading" : null);
  await pb.play();
  pb.seek(3);
  now += 2000;
  pb.tick();
  assert.equal(pb.time, 3);
  assert.equal(pb.running, false);
  loading = false;
  pb.tick();
  assert.equal(pb.running, true);
  now += 1000;
  assert.equal(pb.time, 4);
  pb.pause();
});

function virtualClass(extra = {}) {
  // Rendering dependencies are stubbed so playback and fallback behavior can run without a GPU or DOM.
  const source = readFileSync(new URL("./web/js/virtual.js", import.meta.url), "utf8")
    .replace(/^import .*;\r?\n/gm, "")
    .replace("export class VirtualStage", "class VirtualStage");
  const sandbox = {
    THREE: { MathUtils: { degToRad: (n) => n * Math.PI / 180 } },
    performance,
    ...extra,
  };
  vm.createContext(sandbox);
  vm.runInContext(`${source}\nthis.VirtualStage = VirtualStage;`, sandbox);
  return sandbox.VirtualStage;
}

test("a fractional hold displays the exact final video frame", () => {
  const VirtualStage = virtualClass();
  const stage = Object.create(VirtualStage.prototype);
  stage.info = { hold: 6.01, video: { fps: 30, frames: 182, duration: 182 / 30 } };
  stage.pb = { heardTime: 6.01, running: true };
  stage.hasFrame = true;
  stage.videoEl = {
    readyState: 2, currentTime: 181 / 30 - 0.01, duration: 182 / 30, paused: false, seeking: false,
    pause() { this.paused = true; },
  };
  stage.syncVideo();
  assert.equal(stage.videoEl.currentTime, 181 / 30);
  assert.equal(stage.videoEl.paused, true);
});

test("the static demo plays upright in 2D when WebGL is unavailable", async () => {
  const drawn = [];
  let flips = 0;
  const context = {
    fillRect() {}, save() {}, translate() {}, restore() {},
    scale() { flips++; },
    drawImage(image) { drawn.push(image); },
  };
  const video = {
    readyState: 0, paused: true, seeking: false, currentTime: 0, duration: 181 / 30,
    addEventListener() {}, setAttribute() {},
  };
  const VirtualStage = virtualClass({
    THREE: {
      MathUtils: { degToRad: (n) => n * Math.PI / 180 },
      WebGLRenderer: class { constructor() { throw new Error("WebGL unavailable"); } },
    },
    document: { createElement: () => video },
    window: { devicePixelRatio: 1 },
    ResizeObserver: class { observe() {} },
  });
  const stage = new VirtualStage({
    canvas: { clientWidth: 100, clientHeight: 100, getContext: () => context, setAttribute() {} },
    info: {
      hold: 6,
      board: { texture_mm: [0, 0, 10, 20], board_mm: [0, 0, 10, 20] },
      video: { url: "media/board.mp4", fps: 30, frames: 181, duration: 181 / 30 },
    },
    playback: { heardTime: 0, running: false, update() {} },
    onStatus() {}, onFollowChange() {},
  });
  await stage.init();
  assert.equal(stage.videoEl.src, "media/board.mp4");
  Object.assign(video, { readyState: 2, videoWidth: 10, videoHeight: 20 });
  stage.pump = () => assert.fail("a static fallback must not request the Python renderer");
  stage.frame();
  assert.equal(stage.hasFrame, true);
  assert.ok(drawn.length > 0);
  assert.ok(drawn.every((image) => image === video));
  assert.equal(flips, 0);
  assert.doesNotThrow(() => {
    stage.setFollow(true);
    stage.resetView();
    stage.zoom(0.8);
    stage.orbit(1, 1);
  });
});

test("Follow's final hold and Reset frame the entire board and desk spill", async () => {
  const threeSource = readFileSync(new URL("./web/vendor/three/three.module.min.js", import.meta.url), "utf8");
  const THREE = await import(`data:text/javascript;base64,${Buffer.from(threeSource).toString("base64")}`);
  const VirtualStage = virtualClass({ THREE });
  const board = {
    drawing_mm: [279.4, 431.8], board_mm: [-80, -80, 359.4, 511.8],
    texture_mm: [-86, -86, 365.4, 581.8],
  };
  const stage = new VirtualStage({
    canvas: {}, playback: {}, onStatus() {}, onFollowChange() {},
    info: { board, output: [1920, 1080], hold: 71, beats: [{ start: 67 }, { start: 71 }] },
  });
  // The actual ending camera uses a wide crop while f stays zero, rather than requesting full-frame blend.
  stage.path = { rate: 1, poses: [[0.50153, 0.50002, 3.21431, 0, 0.00001]] };
  for (const [width, height] of [[1176, 582], [390, 844], [844, 390]]) {
    stage.camera = new THREE.PerspectiveCamera(38, width / height, 0.01, 30);
    for (const view of ["hold", "reset"]) {
      const pose = view === "hold" ? stage.followPose(71) : stage.overviewPose();
      stage.setPose(pose);
      stage.camera.updateMatrixWorld(true);
      for (const x of [board.texture_mm[0], board.texture_mm[2]]) {
        for (const y of [board.texture_mm[1], board.texture_mm[3]]) {
          for (const h of [0, 0.0062]) {
            const corner = stage.world(x, y, h).project(stage.camera);
            const limit = view === "hold" ? 0.92 : (width < height ? 0.86 * 0.86 : 0.86);
            assert.ok(Math.abs(corner.x) <= limit + 1e-6 && Math.abs(corner.y) <= limit + 1e-6,
              `${view} clips texture corner at ${width}x${height}: ${corner.toArray()}`);
            assert.ok(corner.z >= -1 && corner.z <= 1);
          }
        }
      }
    }
    assert.deepEqual(stage.followPose(76).position.toArray(), stage.followPose(71).position.toArray(),
      "the final framing remains still through Hold");
  }
});

test("the opening walks up to the desk, looks down at the board and hands over to Follow", async () => {
  const threeSource = readFileSync(new URL("./web/vendor/three/three.module.min.js", import.meta.url), "utf8");
  const THREE = await import(`data:text/javascript;base64,${Buffer.from(threeSource).toString("base64")}`);
  const VirtualStage = virtualClass({ THREE });
  const board = {
    drawing_mm: [279.4, 431.8], board_mm: [-80, -80, 359.4, 511.8],
    texture_mm: [-86, -86, 365.4, 581.8], desk_mm: [-610.3, -259.1, 889.7, 690.9],
  };
  const stage = new VirtualStage({
    canvas: {}, playback: {}, onStatus() {}, onFollowChange() {},
    info: { board, output: [1920, 1080], hold: 71, beats: [{ start: 67 }, { start: 71 }] },
  });
  stage.path = { rate: 1, poses: [[0.49606, 0.50154, 2.75122, 0, 0]] };
  stage.camera = new THREE.PerspectiveCamera(38, 16 / 9, 0.01, 30);
  stage.controls = { enabled: true, target: new THREE.Vector3(), update() {} };
  stage.ready = true;
  const phases = [];
  stage.onIntro = (phase, opts) => phases.push(opts ? `${phase}:${opts.play}` : phase);

  assert.equal(stage.startIntro(), true);
  assert.equal(stage.controls.enabled, false);
  const centre = stage.world(...stage.centre, 0);
  const far = stage.camera.position.distanceTo(centre);
  stage.runIntro(1000);
  assert.deepEqual(phases, [], "the walk waits for the first board frame");
  stage.hasFrame = true;
  stage.runIntro(1000);
  assert.ok(far > 2.4 && Math.abs(stage.camera.position.y - 0.86) < 0.03, "starts at standing eye height across the room");

  stage.runIntro(1000 + 8000);
  assert.ok(stage.camera.position.distanceTo(centre) < 1.1, "ends close above the desk");
  stage.camera.updateMatrixWorld(true);
  const onScreen = centre.clone().project(stage.camera);
  assert.ok(Math.abs(onScreen.x) < 0.05 && Math.abs(onScreen.y) < 0.05, "looking down at the middle of the board");
  assert.deepEqual(phases, ["walk", "title"]);

  assert.equal(stage.continueIntro(1000 + 3000), false, "Continue only works once the title is up");
  stage.runIntro(1000 + 60000);
  assert.equal(stage.introActive, true, "the title waits for Continue");
  assert.deepEqual(phases, ["walk", "title"]);
  stage.camera.updateMatrixWorld(true);
  const waiting = centre.clone().project(stage.camera);
  assert.ok(Math.abs(waiting.x) < 0.1 && Math.abs(waiting.y) < 0.1, "still looking at the board while waiting");

  assert.equal(stage.continueIntro(1000 + 60000), true);
  assert.equal(stage.continueIntro(1000 + 60000), false);
  stage.runIntro(1000 + 62500);
  assert.deepEqual(phases, ["walk", "title", "out"]);
  assert.equal(stage.introActive, true, "lingers on the tapestry after Continue");
  stage.runIntro(1000 + 64990);
  const opening = stage.followPose(0);
  assert.ok(stage.camera.position.distanceTo(opening.position) < 0.1, "the linger ends just behind the show's opening camera");
  stage.runIntro(1000 + 65100);
  assert.equal(stage.introActive, false);
  assert.equal(stage.follow, true);
  assert.equal(stage.controls.enabled, true);
  assert.deepEqual(phases, ["walk", "title", "out", "done:true"]);
  assert.equal(stage.endIntro(), false, "ending twice is a no-op");
});

function netModule({ hosted = true, search = "", fetch = async () => ({ ok: true, json: async () => ({}) }) } = {}) {
  const source = readFileSync(new URL("./web/js/net.js", import.meta.url), "utf8")
    .replace(/^export /gm, "");
  const sandbox = {
    URL, URLSearchParams, AbortController, setTimeout, clearTimeout, fetch,
    document: { querySelector: () => hosted ? {} : null },
    location: { search, href: "https://example.github.io/project/", origin: "https://example.github.io" },
  };
  vm.createContext(sandbox);
  vm.runInContext(`${source}\nthis.net = { STATIC, BACKEND, URLS, apiURL, apiFetch, getJSON, postJSON };`, sandbox);
  return sandbox.net;
}

test("hosted pages send API requests to the backend while retaining static assets", () => {
  const hosted = netModule({ search: "?backend=http%3A%2F%2Flocalhost%3A8001%2Fignored" });
  assert.equal(hosted.apiURL("/api/source"), "http://localhost:8001/api/source");
  assert.equal(hosted.apiURL("/api/scan?t=1&overlay=0"), "http://localhost:8001/api/scan?t=1&overlay=0");
  for (const asset of ["data/info.json", "media/board.mp4", "media/soundtrack.wav", "data/camera-path.json"]) {
    assert.equal(hosted.apiURL(asset), asset);
  }
  assert.equal(hosted.apiURL("/apiculture"), "/apiculture");
  assert.equal(hosted.apiURL("https://example.com/api/info"), "https://example.com/api/info");
  const local = netModule({ hosted: false });
  assert.equal(local.apiURL("/api/source"), "/api/source");
});

test("backend JSON requests omit credentials and identify loopback access", async () => {
  const calls = [];
  const net = netModule({ fetch: async (url, options) => {
    calls.push({ url, options });
    return { ok: true, json: async () => ({ ok: true }) };
  } });
  assert.equal((await net.getJSON("/api/info")).ok, true);
  assert.equal((await net.postJSON("/api/source", { kind: "browser" })).ok, true);
  assert.equal(calls[0].url, "http://localhost:8000/api/info");
  assert.equal(calls[1].url, "http://localhost:8000/api/source");
  for (const { options } of calls) {
    assert.equal(options.credentials, "omit");
    assert.equal(options.targetAddressSpace, "loopback");
    assert.equal(options.cache, "no-store");
  }
  assert.equal(calls[1].options.method, "POST");
  assert.equal(calls[1].options.headers["X-Tapestry"], "1");
  assert.equal(calls[1].options.headers["Content-Type"], "application/json");
  assert.deepEqual(JSON.parse(calls[1].options.body), { kind: "browser" });
});

test("backend overrides accept loopback URLs and reject off-computer or credentialed destinations", () => {
  for (const backend of ["http://localhost:8001", "http://127.0.0.1:8002", "https://[::1]:8443"]) {
    const net = netModule({ search: `?backend=${encodeURIComponent(backend)}` });
    assert.equal(net.BACKEND, backend);
    assert.equal(net.apiURL("/api/source"), `${backend}/api/source`);
  }
  for (const backend of [
    "https://renderer.example.com", "http://192.168.1.2:8000", "https://localhost.example.com",
    "http://localhost@renderer.example.com", "http://user:password@localhost:8000",
    "ftp://localhost:8000", "javascript:alert(1)", "not a URL",
  ]) {
    const net = netModule({ search: `?backend=${encodeURIComponent(backend)}` });
    assert.equal(net.BACKEND, "http://localhost:8000", backend);
    assert.equal(net.apiURL("/api/source"), "http://localhost:8000/api/source", backend);
  }
});

test("backend API errors reach the caller", async () => {
  let call;
  const net = netModule({ search: "?backend=http%3A%2F%2F127.0.0.1%3A8001", fetch: async (url, options) => {
    call = { url, options };
    return { ok: false, status: 409, json: async () => ({ error: "Camera is busy" }) };
  } });
  await assert.rejects(net.postJSON("/api/source", { kind: "browser" }), /Camera is busy/);
  assert.equal(call.url, "http://127.0.0.1:8001/api/source");
  assert.equal(call.options.credentials, "omit");
  assert.equal(call.options.targetAddressSpace, "loopback");
});

function scanClass(extra = {}) {
  const source = readFileSync(new URL("./web/js/scan.js", import.meta.url), "utf8")
    .replace(/^import .*;\r?\n/gm, "")
    .replace(/^export /gm, "");
  const sandbox = { performance, ...extra };
  vm.createContext(sandbox);
  vm.runInContext(`${source}\nthis.ScanStage = ScanStage;`, sandbox);
  return sandbox.ScanStage;
}

test("Scan uploads frames through the backend client and uses cross-origin tracking metadata", async () => {
  const jpeg = { type: "image/jpeg" };
  const bitmap = { width: 1280, height: 720, close() {} };
  const meta = { locked: true, tracking: "tracking", seen: [0, 1, 2, 3] };
  let request, shown, accepted;
  const ScanStage = scanClass({
    fetch: () => assert.fail("Scan must use the backend client for hosted pages"),
    apiFetch: async (url, options) => {
      request = { url, options };
      return {
        ok: true,
        headers: { get: (name) => name === "X-Meta" ? JSON.stringify(meta) : "image/jpeg" },
        blob: async () => jpeg,
      };
    },
    createImageBitmap: async (blob) => { assert.equal(blob, jpeg); return bitmap; },
  });
  const stage = Object.assign(Object.create(ScanStage.prototype), {
    active: true, kind: "browser", inFlight: 0, pollAt: 0, follow: true, overlay: false,
    frozenKey: null, token: 1, seq: 0, shown: 0, latency: 0.08,
    video: { readyState: 2, videoWidth: 1280 },
    pb: { time: 2, hold: 10, running: false },
    grab: async () => jpeg,
    draw: (bmp) => { shown = bmp; },
    accept: (data, key) => { accepted = { data, key }; },
    set: (_state, message) => assert.fail(message),
  });
  await stage.pump();
  assert.equal(request.url, "/api/scan?t=2.0000&follow=1&overlay=0&source=browser");
  assert.equal(request.options.method, "POST");
  assert.equal(request.options.body, jpeg);
  assert.equal(request.options.headers["Content-Type"], "image/jpeg");
  assert.equal(request.options.headers["X-Tapestry"], "1");
  assert.equal(shown, bitmap);
  assert.deepEqual(JSON.parse(JSON.stringify(accepted.data)), meta);
  assert.equal(accepted.key, "true:false");
  assert.equal(stage.inFlight, 0);
});

const hostedInfo = {
  title: "Hamlet: The tapestry", end: 8, hold: 6, audio: true, min_markers: 4,
  beats: [
    { id: "S1", name: "Opening", start: 0, duration: 6, hold: false, silence: false },
    { id: "S2", name: "Hold", start: 6, duration: 2, hold: true, silence: false },
  ],
  board: {
    drawing_mm: [279.4, 431.8], marker_mm: 40, gap_mm: 8, paper: "11x17",
    markers_mm: { 0: [[-48, -48], [-8, -48], [-8, -8], [-48, -8]] },
  },
  phone: { enabled: false },
};

function mainApp(getJSON = async () => structuredClone(hostedInfo)) {
  const source = readFileSync(new URL("./web/js/main.js", import.meta.url), "utf8")
    .replace(/^import .*;\r?\n/gm, "")
    .replace(/boot\(\)\.catch\(\(err\) => \{[\s\S]*?\n\}\);/, "");
  const elements = new Map();
  const element = (id) => {
    if (!elements.has(id)) elements.set(id, {
      hidden: false, dataset: {}, parentElement: {},
      classList: { toggle() {}, add() {}, remove() {} },
      setAttribute(name, value) { this[name] = value; },
      focus() {}, querySelector: () => ({ dataset: {} }),
    });
    return elements.get(id);
  };
  const state = { scanActivations: 0, virtualActivations: 0 };
  const scan = {
    active: false, follow: false,
    activate() { this.active = true; state.scanActivations++; },
    async deactivate() { this.active = false; },
    gateReason: () => "Start a camera to begin",
    describe: () => ({ line: { text: "No camera running." } }),
  };
  const virtual = {
    active: true, hasFrame: true,
    activate() { this.active = true; state.virtualActivations++; },
    deactivate() { this.active = false; },
    gateReason: () => null,
  };
  const location = {
    href: "https://example.github.io/project/?backend=http%3A%2F%2Flocalhost%3A8000",
    pathname: "/project/", search: "?backend=http%3A%2F%2Flocalhost%3A8000", hash: "",
  };
  const sandbox = {
    STATIC: true, BACKEND: "http://localhost:8000", window: {}, location, getJSON, postJSON() {},
    AbortController, setTimeout, clearTimeout, URL, URLSearchParams,
    history: { replaceState(_state, _title, url) { state.historyURL = url; } },
    localStorage: { getItem: () => null },
    document: {
      getElementById: element, querySelector: () => null, querySelectorAll: () => [],
      documentElement: { classList: { toggle() {}, add() {}, remove() {} } },
    },
    __info: structuredClone(hostedInfo), __scan: scan, __virtual: virtual,
    __playback: { setGate(gate) { state.gate = gate; }, update() {} },
  };
  vm.createContext(sandbox);
  vm.runInContext(`${source}
    info = this.__info; scan = this.__scan; virtual = this.__virtual; pb = this.__playback;
    renderStatus = () => {}; renderFollow = () => {}; configurePhone = () => {};
    this.main = { setMode, connectBackend, sameShow, actions: ACTIONS,
      get mode() { return mode; }, get ready() { return backendReady; },
      get error() { return backendError; }, get description() { return backendDescription(); } };
  `, sandbox);
  return { app: sandbox.main, state, scan, virtual, location, elements };
}

test("hosted Scan connects to a compatible backend without leaving GitHub Pages", async () => {
  const requests = [];
  const { app, scan, virtual, state, location } = mainApp(async (url) => {
    requests.push(url);
    return structuredClone(hostedInfo);
  });
  const originalURL = location.href;
  await app.setMode("scan");
  assert.equal(app.mode, "scan");
  assert.equal(app.ready, true);
  assert.equal(scan.active, true);
  assert.equal(virtual.active, false);
  assert.equal(state.scanActivations, 1);
  assert.deepEqual(requests, ["/api/info"]);
  assert.equal(location.href, originalURL);
  assert.equal(state.historyURL, "#scan");
});

test("an unavailable hosted backend offers retry and recovers when manually started", async () => {
  let running = false;
  const { app, scan, location } = mainApp(async () => {
    if (!running) throw new Error("Failed to fetch");
    return structuredClone(hostedInfo);
  });
  const originalURL = location.href;
  await app.setMode("scan");
  assert.equal(app.mode, "scan");
  assert.equal(scan.active, false);
  assert.equal(app.ready, false);
  assert.match(app.description.notice.text, /server\.py/);
  assert.ok(app.description.notice.actions.includes("backend"));
  assert.ok(app.description.notice.actions.includes("virtual"));
  running = true;
  await app.actions.backend().run();
  assert.equal(app.ready, true);
  assert.equal(scan.active, true);
  assert.equal(location.href, originalURL);
});

test("the backend connection notice can return to Virtual Explore", async () => {
  const { app, virtual, scan, state } = mainApp(async () => { throw new Error("Failed to fetch"); });
  await app.setMode("scan");
  await app.actions.virtual().run();
  assert.equal(app.mode, "virtual");
  assert.equal(virtual.active, true);
  assert.equal(scan.active, false);
  assert.match(state.historyURL, /^\/project\/\?backend=/);
});

test("a connection response arriving after returning to Virtual does not activate Scan", async () => {
  let finish;
  const { app, scan, virtual, state } = mainApp(() => new Promise((resolve) => { finish = resolve; }));
  const connecting = app.setMode("scan");
  const pending = app.connectBackend();
  await app.setMode("virtual");
  finish(structuredClone(hostedInfo));
  await Promise.all([connecting, pending]);
  assert.equal(app.mode, "virtual");
  assert.equal(scan.active, false);
  assert.equal(virtual.active, true);
  assert.equal(state.scanActivations, 0);
});

test("a mismatched local show is rejected before Scan can start", async () => {
  const different = structuredClone(hostedInfo);
  different.beats[0].duration += 1;
  const { app, scan } = mainApp(async () => different);
  await app.setMode("scan");
  assert.equal(app.ready, false);
  assert.equal(scan.active, false);
  assert.ok(app.error);
  assert.match(app.description.notice.text, /match|same|different|version/i);
});

test("show compatibility checks timing and board geometry while allowing transport metadata", () => {
  const { app } = mainApp();
  const compatible = structuredClone(hostedInfo);
  compatible.phone = { enabled: true, urls: ["https://192.168.1.2:8443/phone"] };
  compatible.quality = "render";
  compatible.output = [1920, 1080];
  assert.equal(app.sameShow(hostedInfo, compatible), true);
  for (const change of [
    (copy) => { copy.hold += 1; },
    (copy) => { copy.end += 1; },
    (copy) => { copy.beats[0].start += 0.5; },
    (copy) => { copy.board.gap_mm += 1; },
    (copy) => { copy.board.markers_mm[0][0][0] += 1; },
  ]) {
    const incompatible = structuredClone(hostedInfo);
    change(incompatible);
    assert.equal(app.sameShow(hostedInfo, incompatible), false);
  }
});
