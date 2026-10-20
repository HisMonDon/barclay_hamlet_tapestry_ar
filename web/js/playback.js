// Playback clock and soundtrack.
//
// The engine renders every frame as a pure function of the timeline position t, so the browser owns the
// clock. While the soundtrack plays, the Web Audio clock *is* the show clock, which keeps sound and picture
// together through pause, seek and restart. Before the soundtrack has loaded, or if audio is unavailable,
// the clock falls back to performance.now().
//
// A gate can hold the clock without changing the user's intent: Scan mode closes it until the markers are
// first locked, and Virtual mode until the first board texture has arrived. Pressing Play while the gate is
// closed arms playback; it starts the moment the gate opens.

export class Playback extends EventTarget {
  constructor(info) {
    super();
    this.beats = info.beats;
    this.starts = info.beats.map((b) => b.start);
    this.end = info.end;
    this.hold = info.hold;
    this.silence = info.silence;
    this.t0 = 0;
    this.c0 = 0;
    this.src = "perf";
    this.wantPlay = false;
    this.running = false;
    this.blocked = null;
    this.gate = () => null;
    this.ctx = null;
    this.gain = null;
    this.buffer = null;
    this.node = null;
    this.volume = 0.9;
    this.muted = false;
    this.audioError = null;
    this.lastBeat = -1;
    this.audioOn = info.audio !== false;
  }

  // ---- audio ------------------------------------------------------------------------------------------
  ensureContext() {
    if (this.ctx || !this.audioOn) return this.ctx;
    const AC = window.AudioContext || window.webkitAudioContext;
    if (!AC) {
      this.audioError = "This browser has no Web Audio support; the show will play silently.";
      return null;
    }
    this.ctx = new AC({ latencyHint: "interactive" });
    this.gain = this.ctx.createGain();
    this.gain.gain.value = this.muted ? 0 : this.volume;
    this.gain.connect(this.ctx.destination);
    this.ctx.addEventListener("statechange", () => this.emit("audio"));
    return this.ctx;
  }

  async loadSoundtrack(url) {
    try {
      const res = await fetch(url);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.arrayBuffer();
      const ctx = this.ensureContext();
      if (!ctx) return;
      this.buffer = await ctx.decodeAudioData(data);
      if (this.running && ctx.state === "running") this.anchor(this.time, true);
      this.emit("audio");
    } catch (err) {
      this.audioError = `The soundtrack could not be loaded (${err.message}). The show will play silently.`;
      this.emit("audio");
    }
  }

  setVolume(v) {
    this.volume = Math.min(1, Math.max(0, v));
    if (this.volume > 0 && this.muted) this.muted = false;
    this.applyGain();
  }

  setMuted(m) {
    this.muted = m;
    this.applyGain();
  }

  applyGain() {
    if (this.gain) this.gain.gain.setTargetAtTime(this.muted ? 0 : this.volume, this.ctx.currentTime, 0.015);
    this.emit("volume");
  }

  startAudio(t) {
    this.stopAudio();
    if (!this.buffer || !this.ctx || t >= this.buffer.duration - 0.01) return false;
    const node = this.ctx.createBufferSource();
    node.buffer = this.buffer;
    node.connect(this.gain);
    node.start(0, Math.max(0, t));
    this.node = node;
    return true;
  }

  stopAudio() {
    if (this.node) {
      try { this.node.stop(); } catch { /* already stopped */ }
      this.node.disconnect();
      this.node = null;
    }
  }

  // ---- clock ------------------------------------------------------------------------------------------
  now(src) {
    return src === "audio" ? this.ctx.currentTime : performance.now() / 1000;
  }

  get time() {
    if (!this.running) return this.t0;
    return Math.min(this.end, this.t0 + Math.max(0, this.now(this.src) - this.c0));
  }

  // The moment the audience is hearing: the audio clock runs a few milliseconds ahead of the speakers.
  get heardTime() {
    const t = this.time;
    if (!this.running || this.src !== "audio") return t;
    const lag = (this.ctx.outputLatency || 0) + (this.ctx.baseLatency || 0);
    return Math.max(this.t0, t - Math.min(0.2, lag));
  }

  get beat() {
    return this.beatAt(this.time);
  }

  beatAt(t) {
    let i = 0;
    for (let k = 0; k < this.starts.length; k++) if (t + 1e-6 >= this.starts[k]) i = k;
    return i;
  }

  // Mirrors Timeline.step: the start of the neighbouring beat, clamped to the show.
  stepTarget(t, dir) {
    const i = this.beatAt(t) + dir;
    if (i < 0) return 0;
    if (i >= this.beats.length) return this.end;
    return this.starts[i];
  }

  anchor(t, withAudio) {
    this.t0 = t;
    const audio = withAudio && this.buffer && this.ctx && this.ctx.state === "running";
    this.src = audio ? "audio" : "perf";
    this.c0 = this.now(this.src);
    if (audio) this.startAudio(t);
    else this.stopAudio();
  }

  startClock() {
    this.running = true;
    this.anchor(this.t0, true);
  }

  stopClock() {
    this.t0 = this.time;
    this.running = false;
    this.stopAudio();
  }

  // ---- transport --------------------------------------------------------------------------------------
  async play() {
    // Record intent before awaiting browser audio permission so Pause can cancel a pending Play.
    if (this.time >= this.end - 1e-3) this.seek(0);
    this.wantPlay = true;
    this.emit("state");
    const ctx = this.ensureContext();
    if (ctx && ctx.state !== "running") {
      try { await ctx.resume(); } catch { /* stays on the performance clock */ }
    }
    if (!this.wantPlay) return;
    this.update();
    this.emit("state");
  }

  pause() {
    this.wantPlay = false;
    this.update();
    this.emit("state");
  }

  toggle() {
    return this.wantPlay ? this.pause() : this.play();
  }

  seek(t) {
    t = Math.min(this.end, Math.max(0, t));
    if (this.running) this.anchor(t, true);
    else this.t0 = t;
    this.emit("seek");
  }

  restart() {
    this.seek(0);
    return this.play();
  }

  step(dir) {
    this.seek(this.stepTarget(this.time, dir));
  }

  setGate(fn) {
    this.gate = fn;
    this.update();
  }

  update() {
    const reason = this.gate();
    const run = this.wantPlay && !reason;
    if (run && !this.running) this.startClock();
    else if (!run && this.running) this.stopClock();
    const blocked = this.wantPlay ? reason : null;
    if (blocked !== this.blocked) {
      this.blocked = blocked;
      this.emit("state");
    }
  }

  // Called every animation frame.
  tick() {
    if (this.running && this.time >= this.end) {
      this.stopClock();
      this.t0 = this.end;
      this.wantPlay = false;
      this.emit("state");
      this.emit("ended");
    }
    // A clock started before the AudioContext was allowed to run switches over once it can.
    if (this.running && this.src === "audio" && this.ctx.state !== "running") this.anchor(this.time, false);
    if (this.running && this.src === "perf" && this.buffer && this.ctx && this.ctx.state === "running") this.anchor(this.time, true);
    this.update();
    const b = this.beat;
    if (b !== this.lastBeat) {
      this.lastBeat = b;
      this.emit("beat");
    }
  }

  emit(type) {
    this.dispatchEvent(new Event(type));
  }
}

export function formatTime(t) {
  const s = Math.max(0, Math.floor(t + 1e-6));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}
