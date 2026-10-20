// Virtual Explore: the board, desk and candle in 3D, with the engine's animation streamed onto the board.
import * as THREE from "three";
import { OrbitControls } from "../vendor/three/OrbitControls.js";
import { getJSON, URLS } from "./net.js";

const BOARD_THICK = 0.006;
const FOLLOW_TILT = THREE.MathUtils.degToRad(12);
const OVERVIEW = { polar: THREE.MathUtils.degToRad(36), azimuth: 0, fill: 0.86 };
const BLEND_S = 0.9;
const DESK_HEIGHT = 0.74;

// The opening: walk up to the desk at standing eye height, look down at the board, hold on the title, then
// settle into the show's camera. Seconds from the first board frame; `reduced` keeps the camera still.
const INTRO = {
  walk: 5.2, look: [3.7, 6.3], lean: [4.4, 7.0], title: 6.0, out: 10.6, end: 11.1, blend: 1.6,
  eye: 0.86, start: 2.2, arrive: 0.27, stepHz: 1.7, gaze: THREE.MathUtils.degToRad(21), leanTilt: THREE.MathUtils.degToRad(22),
};
const INTRO_REDUCED = { title: 0.8, out: 5.4, end: 5.9 };

const smooth = (p) => p * p * (3 - 2 * p);
const clamp01 = (p) => Math.min(1, Math.max(0, p));

export class VirtualStage {
  constructor({ canvas, info, playback, onStatus, onFollowChange }) {
    this.canvas = canvas;
    this.info = info;
    this.pb = playback;
    this.onStatus = onStatus;
    this.onFollowChange = onFollowChange;
    this.active = false;
    this.ready = false;
    this.follow = false;
    this.path = null;
    this.inFlight = 0;
    this.seq = 0;
    this.shownSeq = 0;
    this.pendingKey = null;
    this.lastKey = null;
    this.latency = 0.06;
    this.failures = 0;
    this.retryAt = 0;
    this.pendingClose = [];
    this.fallback = null;
    this.touched = false;
    this.intro = null;
    this.onIntro = () => {};

    const b = info.board;
    this.b = b;
    const [tx0, ty0, tx1, ty1] = b.texture_mm;
    const [bx0, by0, bx1, by1] = b.board_mm;
    this.centre = [(bx0 + bx1) / 2, (by0 + by1) / 2];
    this.texRect = b.texture_mm;
    this.endingStart = info.beats?.filter((beat) => beat.start < info.hold).at(-1)?.start ?? info.hold;
  }

  // mm on the drawing (x right, y down) -> world metres (x right, z towards the viewer, y up)
  world(x, y, h = 0) {
    return new THREE.Vector3((x - this.centre[0]) / 1000, h, (y - this.centre[1]) / 1000);
  }

  async init() {
    try {
      this.renderer = new THREE.WebGLRenderer({ canvas: this.canvas, antialias: true, powerPreference: "high-performance" });
    } catch (err) {
      this.fallback = this.canvas.getContext("2d");
      if (!this.fallback) throw new Error("This browser cannot display the board. Try a browser with WebGL or Canvas support.");
      this.onStatus({ tone: "warn", text: "3D is unavailable in this browser (WebGL is off). Showing the flat board instead." });
      this.canvas.setAttribute("aria-label", "Flat view of the animated tapestry board");
      if (this.info.video) this.makeVideo();
      this.ready = false;
      this.resizeObserver = new ResizeObserver(() => this.resize());
      this.resizeObserver.observe(this.canvas);
      this.resize();
      return;
    }
    const r = this.renderer;
    r.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    r.outputColorSpace = THREE.SRGBColorSpace;
    r.toneMapping = THREE.NeutralToneMapping;
    r.toneMappingExposure = 1.0;
    r.shadowMap.enabled = true;
    r.shadowMap.type = THREE.PCFSoftShadowMap;
    // The spotlight and every shadow-casting mesh stay fixed; only the board texture and camera move.
    r.shadowMap.autoUpdate = false;
    r.shadowMap.needsUpdate = true;
    this.maxAniso = r.capabilities.getMaxAnisotropy();

    const scene = (this.scene = new THREE.Scene());
    scene.background = new THREE.Color(0x0b0806);
    scene.fog = new THREE.Fog(0x0b0806, 1.9, 3.6);

    this.camera = new THREE.PerspectiveCamera(38, 1, 0.01, 30);
    this.buildLights();
    await this.buildDesk();
    this.buildBoard();
    this.buildProps();
    this.buildMotes();

    const c = (this.controls = new OrbitControls(this.camera, this.canvas));
    c.enableDamping = true;
    c.dampingFactor = 0.09;
    c.screenSpacePanning = true;
    c.minDistance = 0.07;
    c.maxDistance = 2.6;
    c.maxPolarAngle = THREE.MathUtils.degToRad(84);
    c.addEventListener("start", () => this.userMoved());
    c.addEventListener("change", () => this.clampTarget());
    this.canvas.addEventListener("keydown", (e) => this.onKey(e));

    this.resizeObserver = new ResizeObserver(() => this.resize());
    this.resizeObserver.observe(this.canvas);
    this.resize();
    this.setPose(this.overviewPose());
    c.target.copy(this.overviewPose().target);
    c.update();

    // Follow animation is the default view; it waits for the camera path and gives way once the user moves.
    getJSON(URLS.path).then((p) => {
      if (!(p.rate > 0) || !Array.isArray(p.poses) || !p.poses.length ||
          !p.poses.every((pose) => Array.isArray(pose) && pose.length === 5 && pose.every(Number.isFinite))) {
        throw new Error("Invalid camera path");
      }
      this.path = p;
      if (!this.touched && !this.intro) this.setFollow(true);
    }).catch(() => {
      this.onStatus({ tone: "warn", text: "The show's camera path could not be loaded; Follow animation is unavailable." });
    });
    this.ready = true;
  }

  // ---- scene --------------------------------------------------------------------------------------------
  buildLights() {
    const s = this.scene;
    s.add(new THREE.HemisphereLight(0x4a3a2a, 0x080503, 0.9));

    const key = (this.key = new THREE.SpotLight(0xffe6c4, 7.5, 0, THREE.MathUtils.degToRad(34), 0.65, 2));
    key.position.copy(this.world(this.centre[0] - 60, this.centre[1] + 160, 1.25));
    key.target.position.copy(this.world(...this.centre, 0));
    key.castShadow = true;
    key.shadow.mapSize.set(2048, 2048);
    key.shadow.bias = -0.0004;
    key.shadow.radius = 4;
    key.shadow.camera.near = 0.3;
    key.shadow.camera.far = 3;
    s.add(key, key.target);

    const rim = new THREE.DirectionalLight(0x8ea6d0, 0.18);
    rim.position.set(-1.2, 0.8, -1.4);
    s.add(rim);
  }

  async buildDesk() {
    const [dx0, dy0, dx1, dy1] = this.b.desk_mm;
    const tex = await new THREE.TextureLoader().loadAsync(URLS.desk).catch(() => null);
    if (tex) {
      tex.colorSpace = THREE.SRGBColorSpace;
      tex.anisotropy = this.maxAniso;
    }
    const mat = new THREE.MeshStandardMaterial({ color: tex ? 0xffffff : 0x3a2618, map: tex, roughness: 0.62, metalness: 0 });
    const desk = this.rectMesh([dx0, dy0, dx1, dy1], null, 0, mat);
    desk.receiveShadow = true;
    this.scene.add(desk);
    this.deskMat = mat;

    // The tabletop as a solid slab on legs, standing on a dark floor, so the walk-up in the opening reads as a
    // table in a room. The slab's shadow pass draws its underside, well clear of the textured top.
    const W = (dx1 - dx0) / 1000, D = (dy1 - dy0) / 1000;
    const depth = 0.04;
    const wood = new THREE.MeshStandardMaterial({ color: 0x24170e, roughness: 0.7 });
    const slab = new THREE.Mesh(new THREE.BoxGeometry(W, depth, D), wood);
    slab.position.copy(this.world((dx0 + dx1) / 2, (dy0 + dy1) / 2, -depth / 2 - 0.0005));
    slab.castShadow = true;
    this.scene.add(slab);

    const legH = DESK_HEIGHT - depth;
    const inset = 0.07;
    for (const sx of [-1, 1]) {
      for (const sz of [-1, 1]) {
        const leg = new THREE.Mesh(new THREE.BoxGeometry(0.055, legH, 0.055), wood);
        leg.position.copy(this.world((dx0 + dx1) / 2, (dy0 + dy1) / 2, -depth - legH / 2))
          .add(new THREE.Vector3(sx * (W / 2 - inset), 0, sz * (D / 2 - inset)));
        leg.receiveShadow = true;
        this.scene.add(leg);
      }
    }
    for (const [w, d, x, z] of [[W - 2 * inset, 0.02, 0, D / 2 - inset], [W - 2 * inset, 0.02, 0, -(D / 2 - inset)],
      [0.02, D - 2 * inset, W / 2 - inset, 0], [0.02, D - 2 * inset, -(W / 2 - inset), 0]]) {
      const apron = new THREE.Mesh(new THREE.BoxGeometry(w, 0.08, d), wood);
      apron.position.copy(this.world((dx0 + dx1) / 2, (dy0 + dy1) / 2, -depth - 0.04)).add(new THREE.Vector3(x, 0, z));
      apron.receiveShadow = true;
      this.scene.add(apron);
    }

    const floor = new THREE.Mesh(new THREE.PlaneGeometry(9, 9), new THREE.MeshStandardMaterial({ color: 0x1a120c, roughness: 0.92 }));
    floor.rotateX(-Math.PI / 2);
    floor.position.copy(this.world(...this.centre, -DESK_HEIGHT));
    floor.receiveShadow = true;
    this.scene.add(floor);
  }

  // A horizontal rectangle (mm) at height h, its UVs cut from the board texture's rectangle when uvRect is set.
  rectMesh(rect, uvRect, h, material) {
    const [x0, y0, x1, y1] = rect;
    const g = new THREE.PlaneGeometry((x1 - x0) / 1000, (y1 - y0) / 1000);
    g.rotateX(-Math.PI / 2);
    const c = this.world((x0 + x1) / 2, (y0 + y1) / 2, h);
    g.translate(c.x, c.y, c.z);
    if (uvRect) {
      const [u0, v0, u1, v1] = uvRect;
      const U = (x) => (x - u0) / (u1 - u0);
      const V = (y) => 1 - (y - v0) / (v1 - v0);
      // PlaneGeometry vertex order: far-left, far-right, near-left, near-right
      g.setAttribute("uv", new THREE.Float32BufferAttribute([U(x0), V(y0), U(x1), V(y0), U(x0), V(y1), U(x1), V(y1)], 2));
    }
    return new THREE.Mesh(g, material);
  }

  buildBoard() {
    const tex = (this.texture = this.info.video ? this.makeVideoTexture() : this.makeTexture(null));
    this.boardMat = new THREE.MeshStandardMaterial({ map: tex, roughness: 0.86, metalness: 0, color: 0xffffff });
    this.stripMat = new THREE.MeshStandardMaterial({ map: tex, roughness: 0.62, metalness: 0, color: 0xffffff });
    this.boardMat.visible = this.stripMat.visible = false;

    const [bx0, by0, bx1, by1] = this.b.board_mm;
    const body = new THREE.Mesh(new THREE.BoxGeometry((bx1 - bx0) / 1000, BOARD_THICK, (by1 - by0) / 1000),
      new THREE.MeshStandardMaterial({ color: 0xd9d6cf, roughness: 0.9 }));
    body.position.copy(this.world((bx0 + bx1) / 2, (by0 + by1) / 2, BOARD_THICK / 2));
    body.castShadow = true;
    body.receiveShadow = true;
    this.scene.add(body);

    const top = this.rectMesh(this.b.board_mm, this.texRect, BOARD_THICK + 0.0002, this.boardMat);
    top.receiveShadow = true;
    this.scene.add(top);

    // the same texture lies on the desk around the board, so the S15 spill crawls off the board onto the wood
    const strip = this.rectMesh(this.texRect, this.texRect, 0.0004, this.stripMat);
    strip.receiveShadow = true;
    this.scene.add(strip);
  }

  // Static site: the engine's board animation as a video, slaved to the playback clock in syncVideo().
  makeVideo() {
    const v = (this.videoEl = document.createElement("video"));
    v.muted = true;
    v.playsInline = true;
    v.setAttribute("playsinline", "");
    v.preload = "auto";
    v.crossOrigin = "anonymous";
    v.addEventListener("error", () => this.onStatus({ tone: "bad", text: "The animation video could not be loaded. Reload the page to try again." }));
    v.src = this.info.video.url;
    return v;
  }

  makeVideoTexture() {
    const v = this.makeVideo();
    const tex = new THREE.VideoTexture(v);
    tex.colorSpace = THREE.SRGBColorSpace;
    tex.anisotropy = this.maxAniso || 1;
    tex.minFilter = THREE.LinearMipmapLinearFilter;
    tex.generateMipmaps = true;
    v.addEventListener("seeked", () => (tex.needsUpdate = true));
    v.addEventListener("loadeddata", () => (tex.needsUpdate = true));
    return tex;
  }

  syncVideo() {
    const v = this.videoEl;
    if (!v) return;
    if (!this.hasFrame && v.readyState >= 2) {
      if (this.fallback) this.drawFallback();
      else this.boardMat.visible = this.stripMat.visible = true;
      this.firstFrame();
    }
    if (v.readyState < 1) return;
    const pb = this.pb;
    const fps = this.info.video.fps;
    const duration = Number.isFinite(v.duration) ? v.duration : this.info.video.duration;
    // The exporter includes an exact hold frame, even when the hold falls between video timestamps.
    const finalFrame = (this.info.video.frames - 1) / fps;
    const last = Math.max(0, Math.min(finalFrame, duration - 0.5 / fps));
    const holding = pb.heardTime >= this.info.hold;
    const want = holding ? last : Math.min(pb.heardTime, last);
    if (pb.running && !holding) {
      const d = want - v.currentTime;
      if (Math.abs(d) > 0.25) {
        if (!v.seeking) v.currentTime = want;
      } else {
        v.playbackRate = 1 + Math.max(-0.1, Math.min(0.1, d * 1.5));
      }
      if (v.paused) v.play().catch(() => {});
    } else {
      if (!v.paused) v.pause();
      v.playbackRate = 1;
      if (Math.abs(v.currentTime - want) > (holding ? 0.0001 : 0.5 / fps) && !v.seeking) v.currentTime = want;
    }
    if (this.fallback && v.readyState >= 2 && !v.seeking && this.flatTime !== v.currentTime) {
      this.flatTime = v.currentTime;
      this.drawFallback();
    }
  }

  makeTexture(image) {
    const tex = new THREE.Texture(image);
    tex.colorSpace = THREE.SRGBColorSpace;
    tex.flipY = false;
    tex.anisotropy = this.maxAniso || 1;
    tex.minFilter = THREE.LinearMipmapLinearFilter;
    tex.generateMipmaps = true;
    return tex;
  }

  buildProps() {
    const s = this.scene;
    // candle on a brass dish, upper left of the board
    const at = this.world(this.b.board_mm[0] - 150, this.b.board_mm[1] + 90, 0);
    const brass = new THREE.MeshStandardMaterial({ color: 0x8a6a2e, metalness: 0.85, roughness: 0.35 });
    const dish = new THREE.Mesh(new THREE.CylinderGeometry(0.048, 0.054, 0.008, 40), brass);
    dish.position.copy(at).add(new THREE.Vector3(0, 0.004, 0));
    const wax = new THREE.Mesh(new THREE.CylinderGeometry(0.014, 0.0155, 0.1, 28),
      new THREE.MeshStandardMaterial({ color: 0xefe3c8, roughness: 0.55, emissive: 0x3a1a05, emissiveIntensity: 0.25 }));
    wax.position.copy(at).add(new THREE.Vector3(0, 0.058, 0));
    const wick = new THREE.Mesh(new THREE.CylinderGeometry(0.0009, 0.0009, 0.008, 6), new THREE.MeshBasicMaterial({ color: 0x1a1008 }));
    wick.position.copy(at).add(new THREE.Vector3(0, 0.112, 0));
    for (const m of [dish, wax]) { m.castShadow = true; m.receiveShadow = true; }
    s.add(dish, wax, wick);

    const glow = document.createElement("canvas");
    glow.width = glow.height = 64;
    const g = glow.getContext("2d");
    const grad = g.createRadialGradient(32, 40, 2, 32, 34, 30);
    grad.addColorStop(0, "rgba(255,248,220,1)");
    grad.addColorStop(0.25, "rgba(255,190,90,.9)");
    grad.addColorStop(1, "rgba(255,120,30,0)");
    g.fillStyle = grad;
    g.fillRect(0, 0, 64, 64);
    const flameTex = new THREE.CanvasTexture(glow);
    flameTex.colorSpace = THREE.SRGBColorSpace;
    this.flame = new THREE.Sprite(new THREE.SpriteMaterial({ map: flameTex, blending: THREE.AdditiveBlending, depthWrite: false, transparent: true }));
    this.flame.position.copy(at).add(new THREE.Vector3(0, 0.126, 0));
    this.flame.scale.set(0.016, 0.03, 1);
    s.add(this.flame);
    this.halo = new THREE.Sprite(new THREE.SpriteMaterial({ map: flameTex, blending: THREE.AdditiveBlending, depthWrite: false, transparent: true, opacity: 0.22 }));
    this.halo.position.copy(this.flame.position);
    this.halo.scale.set(0.12, 0.12, 1);
    s.add(this.halo);
    this.candle = new THREE.PointLight(0xff9d4d, 0.09, 0, 2);
    this.candle.position.copy(this.flame.position);
    s.add(this.candle);

    // an inkwell to the right of the board
    const well = this.world(this.b.board_mm[2] + 120, this.b.board_mm[1] + 160, 0);
    const glass = new THREE.Mesh(new THREE.CylinderGeometry(0.026, 0.03, 0.038, 32),
      new THREE.MeshStandardMaterial({ color: 0x10121a, roughness: 0.12, metalness: 0.2 }));
    glass.position.copy(well).add(new THREE.Vector3(0, 0.019, 0));
    const neck = new THREE.Mesh(new THREE.CylinderGeometry(0.011, 0.013, 0.012, 24), brass);
    neck.position.copy(well).add(new THREE.Vector3(0, 0.044, 0));
    glass.castShadow = neck.castShadow = true;
    s.add(glass, neck);
  }

  buildMotes() {
    const n = 180;
    const rnd = mulberry(5);
    this.moteBase = new Float32Array(n * 6);
    for (let i = 0; i < n; i++) {
      this.moteBase.set([(rnd() - 0.5) * 0.7, 0.04 + rnd() * 0.45, (rnd() - 0.5) * 0.7, rnd() * 6.28, 0.05 + rnd() * 0.18, rnd() * 6.28], i * 6);
    }
    const geo = new THREE.BufferGeometry();
    geo.setAttribute("position", new THREE.Float32BufferAttribute(new Float32Array(n * 3), 3));
    const dot = document.createElement("canvas");
    dot.width = dot.height = 32;
    const g = dot.getContext("2d");
    const grad = g.createRadialGradient(16, 16, 0, 16, 16, 16);
    grad.addColorStop(0, "rgba(255,230,190,1)");
    grad.addColorStop(1, "rgba(255,230,190,0)");
    g.fillStyle = grad;
    g.fillRect(0, 0, 32, 32);
    const map = new THREE.CanvasTexture(dot);
    this.motes = new THREE.Points(geo, new THREE.PointsMaterial({ size: 0.006, map, color: 0xffd8a8, transparent: true, opacity: 0.45,
      depthWrite: false, blending: THREE.AdditiveBlending, sizeAttenuation: true }));
    this.scene.add(this.motes);
  }

  // Candle, flame and dust are functions of the show time, so they hold still when paused and in the final hold.
  animateAtmosphere(t) {
    const tt = Math.min(t, this.info.hold);
    const f = 1 + 0.08 * Math.sin(tt * 7.3) + 0.05 * Math.sin(tt * 13.1 + 1.2) + 0.03 * Math.sin(tt * 23.7 + 0.4);
    this.candle.intensity = 0.09 * f;
    this.flame.scale.set(0.016 * (0.95 + 0.05 * f), 0.03 * f, 1);
    this.halo.material.opacity = 0.2 * f;
    const pos = this.motes.geometry.attributes.position;
    const B = this.moteBase;
    for (let i = 0; i < pos.count; i++) {
      const o = i * 6;
      const sp = B[o + 4];
      pos.setXYZ(i,
        B[o] + 0.05 * Math.sin(tt * sp + B[o + 3]),
        B[o + 1] + 0.03 * Math.sin(tt * sp * 0.7 + B[o + 5]),
        B[o + 2] + 0.05 * Math.cos(tt * sp * 0.8 + B[o + 5]));
    }
    pos.needsUpdate = true;
  }

  // ---- camera -------------------------------------------------------------------------------------------
  resize() {
    const w = this.canvas.clientWidth, h = this.canvas.clientHeight;
    if (!w || !h) return;
    if (this.fallback) {
      const ratio = Math.min(window.devicePixelRatio || 1, 2);
      this.canvas.width = w * ratio;
      this.canvas.height = h * ratio;
      this.drawFallback();
      return;
    }
    this.renderer.setSize(w, h, false);
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
  }

  // Distance at which a w x h (metres) rectangle facing the camera fits the view.
  fitDistance(w, h) {
    const v = THREE.MathUtils.degToRad(this.camera.fov) / 2;
    const hz = Math.atan(Math.tan(v) * this.camera.aspect);
    return Math.max(w / 2 / Math.tan(hz), h / 2 / Math.tan(v));
  }

  // Fit all corners at both desk and board height. Near corners need more room under perspective,
  // so fitting only the rectangle's width and height can cut off the spill below the board.
  fitRectangle(rect, tilt, fill, roll = 0) {
    const [x0, y0, x1, y1] = rect;
    const target = this.world((x0 + x1) / 2, (y0 + y1) / 2, BOARD_THICK / 2);
    const halfW = (x1 - x0) / 2000, halfH = (y1 - y0) / 2000;
    const tanV = Math.tan(THREE.MathUtils.degToRad(this.camera.fov) / 2) * fill;
    const tanH = tanV * this.camera.aspect;
    const c = Math.cos(tilt), s = Math.sin(tilt), cr = Math.cos(roll), sr = Math.sin(roll);
    let d = 0;
    for (const x of [-halfW, halfW]) {
      for (const z of [-halfH, halfH]) {
        for (const y of [-BOARD_THICK / 2, BOARD_THICK / 2 + 0.0002]) {
          const screenY = y * s - z * c;
          const depth = y * c + z * s;
          d = Math.max(d, depth + Math.abs(x * cr - screenY * sr) / tanH,
            depth + Math.abs(x * sr + screenY * cr) / tanV);
        }
      }
    }
    return { target, d };
  }

  overviewPose() {
    const fill = this.camera.aspect < 1 ? OVERVIEW.fill * 0.86 : OVERVIEW.fill;
    const { target, d } = this.fitRectangle(this.texRect, OVERVIEW.polar, fill);
    const dir = new THREE.Vector3().setFromSphericalCoords(1, OVERVIEW.polar, OVERVIEW.azimuth);
    return { target, position: target.clone().addScaledVector(dir, d), roll: 0 };
  }

  // The show's own camera at t: a crop of the drawing (centre x, y and width w as fractions of the drawing),
  // opening to the whole texture as f goes to 1. The final motion beat also opens the view so the
  // desk spill remains visible through the hold, even when the engine's full-frame blend stays at zero.
  followPose(t) {
    const P = this.path;
    const k = Math.min(P.poses.length - 1, Math.max(0, t * P.rate));
    const i = Math.floor(k), j = Math.min(P.poses.length - 1, i + 1), a = k - i;
    const p = P.poses[i].map((v, n) => v + (P.poses[j][n] - v) * a);
    const [x, y, w, f, roll] = p;
    const ending = this.info.hold > this.endingStart
      ? smooth(THREE.MathUtils.clamp((t - this.endingStart) / (this.info.hold - this.endingStart), 0, 1))
      : Number(t >= this.info.hold);
    const fullBlend = f + (1 - f) * ending;
    const framedRoll = roll * (1 - ending);
    const [dw, dh] = this.b.drawing_mm;
    const [ow, oh] = this.info.output;
    const cw = (w * dw) / 1000, ch = (cw * oh) / ow;
    const crop = { target: this.world(x * dw, y * dh, BOARD_THICK), d: this.fitDistance(cw, ch) };
    const full = this.fitRectangle(this.texRect, FOLLOW_TILT, 0.92, framedRoll);
    const target = crop.target.clone().lerp(full.target, fullBlend);
    const d = Math.exp(Math.log(crop.d) + (Math.log(full.d) - Math.log(crop.d)) * fullBlend);
    const dir = new THREE.Vector3(0, Math.cos(FOLLOW_TILT), Math.sin(FOLLOW_TILT));
    return { target, position: target.clone().addScaledVector(dir, d), roll: framedRoll };
  }

  setPose(pose) {
    this.camera.position.copy(pose.position);
    this.camera.up.set(0, 1, 0);
    this.camera.lookAt(pose.target);
    if (pose.roll) this.camera.rotateZ(-pose.roll);
  }

  lookPoint() {
    const dir = new THREE.Vector3();
    this.camera.getWorldDirection(dir);
    if (dir.y > -0.05) return this.controls.target.clone();
    const s = -this.camera.position.y / dir.y;
    return this.camera.position.clone().addScaledVector(dir, s);
  }

  setFollow(on, silent = false, blendS = BLEND_S) {
    if (!this.ready || this.fallback) return;
    if (on && !this.path) {
      this.onStatus({ tone: "warn", text: "The show's camera path is still loading." });
      on = false;
    }
    if (on === this.follow) return;
    this.follow = on;
    this.blend = { t: performance.now(), dur: blendS * 1000,
      from: { position: this.camera.position.clone(), quaternion: this.camera.quaternion.clone() } };
    if (!on) {
      this.blend = null;
      this.controls.target.copy(this.lookPoint());
      this.controls.update();
    }
    if (!silent) this.onFollowChange(on);
  }

  userMoved() {
    this.touched = true;
    this.flight = null;
    if (this.follow) {
      this.follow = false;
      this.blend = null;
      this.controls.target.copy(this.lookPoint());
      this.onFollowChange(false);
    }
  }

  resetView() {
    if (!this.ready || this.fallback) return;
    this.touched = true;
    if (this.follow) this.setFollow(false);
    const to = this.overviewPose();
    this.flight = { t: performance.now(), dur: 900, fromPos: this.camera.position.clone(), fromTarget: this.controls.target.clone(), to };
  }

  zoom(factor) {
    if (!this.ready || this.fallback) return;
    this.userMoved();
    const c = this.controls;
    const off = this.camera.position.clone().sub(c.target);
    const len = THREE.MathUtils.clamp(off.length() * factor, c.minDistance, c.maxDistance);
    this.camera.position.copy(c.target).addScaledVector(off.normalize(), len);
    c.update();
  }

  orbit(dAz, dPolar) {
    if (!this.ready || this.fallback) return;
    this.userMoved();
    const c = this.controls;
    const off = this.camera.position.clone().sub(c.target);
    const s = new THREE.Spherical().setFromVector3(off);
    s.theta += dAz;
    s.phi = THREE.MathUtils.clamp(s.phi + dPolar, 0.05, c.maxPolarAngle);
    this.camera.position.copy(c.target).add(new THREE.Vector3().setFromSpherical(s));
    c.update();
  }

  onKey(e) {
    if (this.intro) return;
    const step = THREE.MathUtils.degToRad(e.shiftKey ? 15 : 5);
    const map = { ArrowLeft: [step, 0], ArrowRight: [-step, 0], ArrowUp: [0, -step], ArrowDown: [0, step] };
    if (map[e.key]) {
      this.orbit(...map[e.key]);
    } else if (e.key === "+" || e.key === "=") {
      this.zoom(0.85);
    } else if (e.key === "-" || e.key === "_") {
      this.zoom(1 / 0.85);
    } else if (e.key === "0") {
      this.resetView();
    } else {
      return;
    }
    e.preventDefault();
    e.stopPropagation();
  }

  clampTarget() {
    const [dx0, dy0, dx1, dy1] = this.b.desk_mm;
    const lo = this.world(dx0 + 150, dy0 + 150), hi = this.world(dx1 - 150, dy1 - 150);
    const t = this.controls.target;
    t.x = THREE.MathUtils.clamp(t.x, lo.x, hi.x);
    t.z = THREE.MathUtils.clamp(t.z, lo.z, hi.z);
    t.y = THREE.MathUtils.clamp(t.y, 0, 0.3);
  }

  // ---- opening ------------------------------------------------------------------------------------------
  // The walk-up starts with the first board frame; until then the camera waits at the far end of the room.
  startIntro() {
    if (!this.ready || this.fallback) return false;
    const reduced = typeof window !== "undefined" && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    if (this.follow) this.setFollow(false, true);
    this.blend = null;
    this.flight = null;
    this.touched = false;
    this.controls.enabled = false;
    this.intro = { t0: null, plan: this.introPlan(), times: reduced ? INTRO_REDUCED : INTRO, reduced, phase: null };
    this.setPose(this.introPose(0));
    return true;
  }

  get introActive() {
    return !!this.intro;
  }

  // Ends the opening early or on time and hands over to the show's camera; `play` tells the page to start the show.
  endIntro({ play = true } = {}) {
    if (!this.intro) return false;
    this.intro = null;
    this.controls.enabled = true;
    if (this.path) {
      this.setFollow(true, true, INTRO.blend);
    } else {
      // Without the camera path yet, keep the current view direction and drift to the overview.
      const dir = new THREE.Vector3();
      this.camera.getWorldDirection(dir);
      this.controls.target.copy(this.camera.position).addScaledVector(dir, 0.8);
      this.flight = { t: performance.now(), dur: 1400, fromPos: this.camera.position.clone(), fromTarget: this.controls.target.clone(), to: this.overviewPose() };
    }
    this.onIntro("done", { play });
    return true;
  }

  introPlan() {
    const [, , , dy1] = this.b.desk_mm;
    const edge = this.world(this.centre[0], dy1).z;
    const fit = this.fitRectangle(this.b.board_mm, INTRO.leanTilt, 1);
    const d = Math.min(fit.d, 1.05);
    return {
      from: new THREE.Vector3(0.09, INTRO.eye + 0.012, edge + INTRO.start),
      arrive: new THREE.Vector3(0, INTRO.eye, edge + INTRO.arrive),
      lean: { target: fit.target, position: fit.target.clone().add(new THREE.Vector3(0, Math.cos(INTRO.leanTilt), Math.sin(INTRO.leanTilt)).multiplyScalar(d)) },
    };
  }

  // Camera at e seconds into the opening: an easing walk with head-bob that slows to a stop at the desk edge,
  // the gaze dropping from across the desk to the board, a lean over it, then a slow breath while the title holds.
  introPose(e) {
    const P = this.intro.plan;
    if (this.intro.reduced) return { position: P.lean.position.clone(), target: P.lean.target.clone(), roll: 0 };
    const s = clamp01(e / INTRO.walk);
    const position = P.from.clone().lerp(P.arrive, 1 - (1 - s) ** 2);
    const pace = 1 - s;
    const step = e * Math.PI * 2 * INTRO.stepHz;
    position.y += 0.009 * Math.sin(step) * pace;
    position.x += 0.013 * Math.sin(step / 2) * pace;
    const lean = smooth(clamp01((e - INTRO.lean[0]) / (INTRO.lean[1] - INTRO.lean[0])));
    position.lerp(P.lean.position, lean);
    position.lerp(P.lean.target, 0.06 * smooth(clamp01((e - INTRO.lean[1]) / 5)));
    // While walking, the eyes rest on the table at a steady downward angle, aimed past the far side of the board.
    const far = P.lean.target.z - 0.6;
    const ahead = new THREE.Vector3(P.lean.target.x - 0.02, position.y - Math.tan(INTRO.gaze) * (position.z - far), far);
    const look = smooth(clamp01((e - INTRO.look[0]) / (INTRO.look[1] - INTRO.look[0])));
    const target = ahead.lerp(P.lean.target, look);
    return { position, target, roll: 0.007 * Math.sin(step / 2) * pace };
  }

  runIntro(now) {
    const I = this.intro;
    if (I.t0 === null && this.hasFrame) I.t0 = now;
    const e = I.t0 === null ? 0 : (now - I.t0) / 1000;
    if (I.t0 !== null) {
      const phase = e >= I.times.out ? "out" : e >= I.times.title ? "title" : "walk";
      if (phase !== I.phase) {
        I.phase = phase;
        this.onIntro(phase);
      }
      if (e >= I.times.end) {
        this.endIntro();
        return;
      }
    }
    this.setPose(this.introPose(e));
  }

  updateCamera(t) {
    const now = performance.now();
    if (this.intro) {
      this.runIntro(now);
      if (this.intro) return;
    }
    if (this.follow && this.path) {
      const want = this.followPose(t);
      this.setPose(want);
      if (this.blend) {
        const p = Math.min(1, (now - this.blend.t) / this.blend.dur);
        if (p < 1) {
          const q = smooth(p);
          const pos = this.blend.from.position.clone().lerp(want.position, q);
          const quat = this.blend.from.quaternion.clone().slerp(this.camera.quaternion, q);
          this.camera.position.copy(pos);
          this.camera.quaternion.copy(quat);
        } else {
          this.blend = null;
        }
      }
      this.controls.target.copy(want.target);
      return;
    }
    if (this.flight) {
      const f = this.flight;
      const p = Math.min(1, (now - f.t) / f.dur);
      const q = smooth(p);
      this.controls.target.copy(f.fromTarget).lerp(f.to.target, q);
      this.camera.position.copy(f.fromPos).lerp(f.to.position, q);
      if (p >= 1) this.flight = null;
    }
    this.controls.update();
  }

  // ---- frames -------------------------------------------------------------------------------------------
  // The frame to ask for next: slightly ahead while playing (to cover the render round trip), the exact
  // paused position otherwise, and one frozen frame for the whole hold. Paused frames are re-rendered sharper.
  wanted() {
    const pb = this.pb;
    if (pb.running) {
      let t = Math.min(pb.end, pb.heardTime + Math.min(0.25, this.latency));
      const frozen = t >= pb.hold;
      if (frozen) t = pb.hold;
      return { t, detail: "standard", key: frozen ? `standard:hold` : null };
    }
    const t = pb.time >= pb.hold ? pb.hold : pb.time;
    const base = `${t.toFixed(3)}`;
    const detail = this.lastKey === `standard:${base}` || this.lastKey === `high:${base}` ? "high" : "standard";
    return { t, detail, key: `${detail}:${base}` };
  }

  // Two requests overlap while playing, so the browser decodes one frame while the engine renders the next.
  async pump() {
    if (!this.active || performance.now() < this.retryAt) return;
    if (this.inFlight >= (this.pb.running ? 2 : 1)) return;
    const w = this.wanted();
    if (w.key && (w.key === this.lastKey || w.key === this.pendingKey)) return;
    const seq = ++this.seq;
    const key = w.key || `${w.detail}:${w.t.toFixed(3)}`;
    this.inFlight++;
    if (w.key) this.pendingKey = w.key;
    if (!this.abort || this.abort.signal.aborted) this.abort = new AbortController();
    const started = performance.now();
    try {
      const res = await fetch(`/api/virtual?t=${w.t.toFixed(4)}&detail=${w.detail}`, { signal: this.abort.signal, cache: "no-store" });
      if (!res.ok) throw new Error((await res.json().catch(() => ({}))).error || `HTTP ${res.status}`);
      const meta = JSON.parse(res.headers.get("X-Meta") || "{}");
      const bmp = await createImageBitmap(await res.blob(), { imageOrientation: "flipY" });
      if (!this.active || seq < this.shownSeq) { bmp.close(); return; }
      this.shownSeq = seq;
      this.latency = 0.8 * this.latency + 0.2 * ((performance.now() - started) / 1000);
      this.showTexture(bmp, meta);
      this.lastKey = key;
      if (this.failures) this.onStatus(null);
      this.failures = 0;
    } catch (err) {
      if (err.name === "AbortError") return;
      this.failures += 1;
      this.retryAt = performance.now() + Math.min(4000, 400 * this.failures);
      this.onStatus({ tone: "bad", text: `The renderer is not answering (${err.message}). Check that server.py is still running.` });
    } finally {
      this.inFlight--;
      if (this.pendingKey === w.key) this.pendingKey = null;
    }
  }

  showTexture(bmp, meta) {
    if (this.fallback) {
      if (this.flat && this.flat !== bmp) this.flat.close();
      this.flat = bmp;
      this.drawFallback();
      this.firstFrame();
      return;
    }
    const old = this.texture;
    if (!old.image || old.image.width !== bmp.width || old.image.height !== bmp.height) {
      this.texture = this.makeTexture(bmp);
      this.boardMat.map = this.stripMat.map = this.texture;
      this.boardMat.needsUpdate = this.stripMat.needsUpdate = true;
      if (old.image) this.pendingClose.push(old.image);
      old.dispose();
    } else {
      this.pendingClose.push(old.image);
      old.image = bmp;
    }
    this.texture.needsUpdate = true;
    this.boardMat.visible = this.stripMat.visible = true;
    this.frameMeta = meta;
    this.firstFrame();
  }

  firstFrame() {
    if (this.hasFrame) return;
    this.hasFrame = true;
    this.pb.update();
    this.onFirstFrame && this.onFirstFrame();
  }

  drawFallback() {
    const g = this.fallback;
    if (!g) return;
    const W = this.canvas.width, H = this.canvas.height;
    g.fillStyle = "#0b0806";
    g.fillRect(0, 0, W, H);
    const image = this.videoEl || this.flat;
    if (!image || (this.videoEl && this.videoEl.readyState < 2)) return;
    const iw = this.videoEl ? image.videoWidth : image.width;
    const ih = this.videoEl ? image.videoHeight : image.height;
    const s = Math.min(W / iw, H / ih) * 0.94;
    const w = iw * s, h = ih * s;
    g.save();
    g.translate((W - w) / 2, this.videoEl ? (H - h) / 2 : (H + h) / 2);
    if (!this.videoEl) g.scale(1, -1); // API bitmaps arrive flipped for WebGL; videos are already upright.
    g.drawImage(image, 0, 0, w, h);
    g.restore();
  }

  // ---- lifecycle ----------------------------------------------------------------------------------------
  activate() {
    this.active = true;
    this.lastKey = null;
    this.retryAt = 0;
    const loop = () => {
      if (!this.active) return;
      this.raf = requestAnimationFrame(loop);
      this.frame();
    };
    this.raf = requestAnimationFrame(loop);
  }

  deactivate() {
    this.endIntro({ play: false });
    this.active = false;
    cancelAnimationFrame(this.raf);
    if (this.abort) this.abort.abort();
    if (this.videoEl) this.videoEl.pause();
  }

  frame() {
    if (this.videoEl) this.syncVideo();
    else this.pump();
    if (this.fallback || !this.ready) return;
    const t = this.pb.heardTime;
    this.animateAtmosphere(t);
    this.updateCamera(t);
    this.renderer.render(this.scene, this.camera);
    while (this.pendingClose.length) {
      const b = this.pendingClose.pop();
      if (b && b.close) b.close();
    }
  }

  gateReason() {
    return this.hasFrame ? null : "Laying out the board";
  }
}

function mulberry(a) {
  return () => {
    a |= 0; a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}
