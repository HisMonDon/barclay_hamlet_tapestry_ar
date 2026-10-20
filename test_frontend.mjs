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

test("a blocked local-app probe still offers direct navigation and a way to stay", () => {
  const source = readFileSync(new URL("./web/js/main.js", import.meta.url), "utf8")
    .replace(/^import .*;\r?\n/gm, "")
    .replace(/boot\(\)\.catch\(\(err\) => \{[\s\S]*?\n\}\);/, "");
  const note = {};
  const tab = { dataset: { mode: "scan" }, querySelector: () => note, focus() {} };
  const elements = {
    "tab-scan": tab,
    notice: { hidden: true, querySelector: () => ({ dataset: {} }) },
    "notice-kicker": {}, "notice-title": {}, "notice-text": {},
    "notice-actions": {
      dataset: {}, children: [],
      replaceChildren(...children) { this.children = children; },
      querySelector() { return this.children[0]; },
    },
  };
  const location = { href: "https://example.github.io/project/" };
  const sandbox = {
    BACKEND: "http://localhost:8000", window: {}, location, postJSON() {},
    localStorage: { getItem: () => null },
    document: {
      getElementById: (id) => elements[id], querySelector: () => null,
      createElement: () => ({ addEventListener(_event, run) { this.click = run; }, focus() {} }),
    },
  };
  vm.createContext(sandbox);
  vm.runInContext(`${source}\nthis.setBackend = setBackend; this.openBackend = openBackend;`, sandbox);
  sandbox.setBackend(false);
  assert.equal(note.textContent, "Local app unavailable");
  sandbox.openBackend(tab);
  assert.equal(elements.notice.hidden, false);
  assert.match(elements["notice-text"].textContent, /browser may block/);
  const [open, stay] = elements["notice-actions"].children;
  stay.click();
  assert.equal(elements.notice.hidden, true);
  open.click();
  assert.equal(location.href, "http://localhost:8000/#scan");
  sandbox.setBackend(true);
  assert.equal(note.textContent, "Local app ready");
});
