import io
import copy
import shutil
import subprocess
import tempfile
import wave
from pathlib import Path

import cv2
import numpy as np

from config import resolve


def wav_bytes(samples, rate):
    data = io.BytesIO()
    with wave.open(data, "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(rate)
        stream.writeframes(np.round(np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes())
    return data.getvalue()


def read_wav(path, rate):
    with wave.open(str(path), "rb") as stream:
        width, channels, source_rate = stream.getsampwidth(), stream.getnchannels(), stream.getframerate()
        raw = stream.readframes(stream.getnframes())
    if width == 1:
        data = (np.frombuffer(raw, np.uint8).astype(np.float32) - 128) / 128
    elif width == 3:
        packed = np.frombuffer(raw, np.uint8).reshape(-1, 3).astype(np.int32)
        data = packed[:, 0] | packed[:, 1] << 8 | packed[:, 2] << 16
        data = ((data ^ 0x800000) - 0x800000).astype(np.float32) / 8388608
    else:
        data = np.frombuffer(raw, {2: "<i2", 4: "<i4"}[width]).astype(np.float32) / 2 ** (8 * width - 1)
    data = data.reshape(-1, channels).mean(axis=1)
    if source_rate != rate and len(data):
        data = np.interp(np.arange(round(len(data) * rate / source_rate)) * source_rate / rate,
                         np.arange(len(data)), data).astype(np.float32)
    return data


def synthesize(name, rate, settings):
    durations = settings.get("durations", {})
    duration = durations.get(name, 4.0 if name in ("crackle", "seep", "creak") else 0.6)
    count = max(1, round(duration * rate))
    rng = np.random.default_rng(settings.get("seed", 19) + sum(ord(x) for x in name))
    t = np.arange(count, dtype=np.float32) / rate
    noise = rng.normal(0, 1, count).astype(np.float32)
    low = cv2.GaussianBlur(noise[:, None], (1, 0), max(1, rate / 1500))[:, 0]
    if name == "crackle":
        data = low * 0.08
        for x in range(max(1, round(duration * settings.get("crackle_density", 9)))):
            start = int(rng.integers(count))
            n = min(count - start, round(rate * rng.uniform(0.008, 0.035)))
            data[start:start + n] += noise[start:start + n] * np.exp(-np.arange(n) / max(1, n / 5)) * rng.uniform(0.15, 0.5)
    elif name == "drip":
        data = np.sin(2 * np.pi * (380 * t + 420 * (1 - np.exp(-t * 17)))) * np.exp(-t * 15)
        data += low * np.exp(-t * 40) * 0.5
    elif name == "seep":
        data = low * (0.25 + 0.12 * np.sin(t * 2.7) ** 2)
        data += np.sin(2 * np.pi * (48 * t + 0.3 * np.sin(t * 1.6))) * 0.035
    elif name == "creak":
        env = (0.5 + 0.5 * np.sin(t * 2.1 + 0.8 * np.sin(t * 4.3))) ** 4
        phase = 2 * np.pi * (95 * t + 2 * np.sin(t * 2.6))
        data = (np.sin(phase) + 0.3 * np.sin(phase * 2.03)) * env * 0.12 + low * 0.2
    elif name == "inhale":
        p = np.clip(t / max(0.001, duration), 0, 1)
        air = cv2.GaussianBlur(noise[:, None], (1, 0), max(1, rate / 9000))[:, 0]
        data = (0.55 * air + 0.45 * low) * np.sin(np.pi * p) ** 2 * np.sqrt(p) * 0.35
    elif name == "shuttle":
        data = low * np.sin(np.pi * np.minimum(1, t / max(0.001, duration))) ** 2 * np.exp(-t * 5)
        data += np.sin(2 * np.pi * 170 * t) * np.exp(-t * 38) * 0.12
    else:
        data = np.zeros(count, np.float32)
    fade = min(count // 2, round(rate * settings.get("edge_s", 0.008)))
    if fade:
        data[:fade] *= np.linspace(0, 1, fade)
        data[-fade:] *= np.linspace(1, 0, fade)
    return data.astype(np.float32)


class Soundtrack:
    def __init__(self, cfg, timeline, scene=None):
        self.cfg = cfg
        self.p = cfg.get("sound", {})
        self.rate = int(self.p.get("rate", 44100))
        self.end = timeline.end
        flagged = [timeline.starts[x] for x in range(len(timeline.beats)) if timeline.beats[x].get("silence")]
        silence_from = self.p.get("silence_from", "B21")
        self.silence = flagged[0] if flagged else timeline.at(silence_from) if silence_from in timeline.ids else timeline.hold_t
        self.samples = np.zeros(round(self.end * self.rate), np.float32)
        self.events = []
        self.assets = {}
        self.warnings = []
        if not self.p.get("on", True):
            return
        sync = dict(scene.cues()) if scene is not None else {}
        for cue, t0, beat in timeline.items("cues"):
            times = [sync.get(cue["sync"], t0)] if "sync" in cue else [t0]
            if cue.get("rows") and scene is not None:
                times = [t for weave in scene.weaves for t, direction in weave.row_events()
                         if timeline.beat_at(weave.t0) == beat]
            for x in times:
                self._cue(cue, x, timeline)
        for x in (scene.sound_events() if hasattr(scene, "sound_events") else []):
            self._cue(x[0], x[1], timeline)
        self.samples *= self.p.get("volume", 0.8)
        np.clip(self.samples, -0.98, 0.98, out=self.samples)
        self.samples[round(self.silence * self.rate):] = 0

    def asset(self, name):
        if name not in self.assets:
            path = resolve(self.cfg, str(Path(self.p.get("dir", "assets/sfx")) / f"{name}.wav"))
            try:
                data = read_wav(path, self.rate) if path.exists() else None
            except (OSError, ValueError, KeyError, wave.Error) as error:
                self.warnings.append(f"{name}: {error}; using synthesized sound")
                data = None
            self.assets[name] = data if data is not None and len(data) else synthesize(name, self.rate, self.p.get("synthesis", {}))
        return self.assets[name]

    def _cue(self, cue, t0, timeline):
        data = self.asset(cue["sound"])
        until = timeline.at(cue["until"]) if "until" in cue else t0 + len(data) / self.rate
        until = min(until, self.silence, self.end)
        start, stop = max(0, round(t0 * self.rate)), max(0, round(until * self.rate))
        if stop <= start:
            return
        count = stop - start
        played = np.resize(data, count) if cue.get("loop") else data[:count]
        if len(played) < count:
            stop = start + len(played)
            count = len(played)
        times = np.arange(count, dtype=np.float32) / self.rate
        gain = np.full(count, cue.get("gain", 1.0), np.float32)
        if "gain_to" in cue:
            ramp_end = timeline.at(cue.get("ramp_until", cue.get("until", self.end)), cue.get("ramp_offset", 0))
            progress = np.clip(times / max(1e-6, ramp_end - t0), 0, 1)
            gain += (cue["gain_to"] - cue.get("gain", 1.0)) * progress
        self.samples[start:stop] += played * gain
        self.events.append((cue["sound"], t0, stop / self.rate))

    def write(self, path, start=0.0, end=None):
        end = self.end if end is None else min(self.end, end)
        Path(path).write_bytes(wav_bytes(self.samples[round(start * self.rate):round(end * self.rate)], self.rate))


def backup_track(original, path):
    track = copy.copy(original)
    track.warnings = list(original.warnings)
    path = Path(path)
    companion = path.with_suffix(".wav")
    data = None
    if companion.exists():
        try:
            data = read_wav(companion, original.rate)
        except (OSError, ValueError, KeyError, wave.Error) as error:
            track.warnings.append(f"Backup WAV: {error}")
    if data is None:
        executable = shutil.which("ffmpeg")
        if executable is None:
            try:
                import imageio_ffmpeg
                executable = imageio_ffmpeg.get_ffmpeg_exe()
            except ImportError:
                pass
        if executable:
            result = subprocess.run([executable, "-v", "error", "-i", str(path), "-vn", "-ac", "1", "-ar", str(original.rate),
                                     "-f", "s16le", "pipe:1"], capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if result.returncode == 0 and result.stdout:
                data = np.frombuffer(result.stdout, "<i2").astype(np.float32) / 32768
    if data is not None:
        track.samples = data.copy() if original.p.get("on", True) else np.zeros(len(data), np.float32)
        track.end = len(data) / track.rate
        track.silence = min(track.end, original.silence)
        track.samples[round(track.silence * track.rate):] = 0
    else:
        track.warnings.append("Backup has no readable audio; using current timeline mix")
    return track


class AudioPlayer:
    def __init__(self, soundtrack, backend=None):
        self.soundtrack = soundtrack
        self.p = soundtrack.p
        self.backend = backend
        self.error = None
        if backend is None and self.p.get("on", True) and self.p.get("backend", "auto") != "none":
            try:
                import winsound
                self.backend = winsound
            except ImportError:
                self.error = "Windows audio unavailable"
        self.folder = tempfile.TemporaryDirectory(prefix="tapestry_audio_")
        self.slot = 0
        self.anchor = None
        self.last_t = None
        self.last_playing = False

    def stop(self):
        if self.backend is not None:
            try:
                self.backend.PlaySound(None, 0)
            except RuntimeError as error:
                self.error = str(error)
        self.anchor = None
        self.last_playing = False

    def sync(self, t, playing, now, force=False):
        audible = playing and t < self.soundtrack.silence and t < self.soundtrack.end
        if not audible:
            if self.last_playing:
                self.stop()
            self.last_t = t
            return
        expected = self.anchor[0] + now - self.anchor[1] if self.anchor is not None else None
        seek = expected is None or abs(t - expected) > self.p.get("sync_tolerance", 0.06)
        if force or not self.last_playing or seek:
            self.stop()
            if self.backend is not None:
                path = Path(self.folder.name) / f"tail{self.slot}.wav"
                self.slot = 1 - self.slot
                self.soundtrack.write(path, t)
                try:
                    self.backend.PlaySound(str(path), self.backend.SND_FILENAME | self.backend.SND_ASYNC | self.backend.SND_NODEFAULT)
                except RuntimeError as error:
                    self.error = str(error)
            self.anchor = (t, now)
        self.last_playing = True
        self.last_t = t

    def close(self):
        self.stop()
        self.folder.cleanup()
