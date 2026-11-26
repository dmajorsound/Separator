"""Separator – preview playback (CoreAudio via sounddevice/PortAudio).

Plays a take either as recorded ("original") or as Separator would write it ("result":
only the kept parts, with the gap between them). Uses the tracks Separator would use;
one track plays in the middle, two tracks as left/right, more are summed to the middle.
"""

import threading
import time

import numpy as np

import fx_core

BLOCK = 1024


def _sd():
    try:
        import sounddevice as sd
        return sd
    except Exception as ex:          # no PortAudio: playback disabled, the rest of the app works
        raise RuntimeError(f"Audio output is not available ({ex})")


def list_devices():
    """Output devices: [{"name", "channels", "default"}]"""
    sd = _sd()
    try:
        default_out = sd.default.device[1]
    except Exception:
        default_out = -1
    out = []
    for i, d in enumerate(sd.query_devices()):
        if d.get("max_output_channels", 0) > 0:
            out.append({"name": d["name"], "channels": d["max_output_channels"], "default": i == default_out})
    return out


def _device_index(sd, name):
    if not name:
        return None
    for i, d in enumerate(sd.query_devices()):
        if d["name"] == name and d.get("max_output_channels", 0) > 0:
            return i
    return None                      # device gone: system default


class Player:
    def __init__(self):
        self._lock = threading.Lock()
        self._stream = None
        self._state = {"playing": False, "rel": "", "pos": 0.0, "mode": "original", "error": ""}
        self.device_name = ""

    # ---------- public ----------
    def play(self, path, rel, chans, start_s=0.0, segments=None, gap_s=0.0, opener=None):
        """segments: [(start_s, end_s)] for result mode, None = the whole file"""
        self.stop()
        info = fx_core.read_wav_info(path)
        raw = np.memmap(path, dtype=np.uint8, mode="r", offset=info.data_off,
                        shape=(info.n_frames * info.block,))
        chans = [c for c in (chans or []) if c < info.channels] or list(range(min(2, info.channels)))
        sr = info.sr
        segs = [(0, info.n_frames)] if not segments else [
            (max(0, int(round(s * sr))), min(info.n_frames, int(round(e * sr)))) for s, e in segments]
        segs = [(a, b) for a, b in segs if b > a]
        if not segs:
            raise RuntimeError("Nothing to play")
        # find the segment that holds the start position (source time)
        f0 = int(round(start_s * sr))
        idx = 0
        for k, (a, b) in enumerate(segs):
            if f0 < b:
                idx = k
                break
        else:
            idx, f0 = 0, segs[0][0]
        f0 = max(f0, segs[idx][0])
        self._pb = {"info": info, "raw": raw, "chans": chans, "segs": segs, "seg": idx, "frame": f0,
                    "gap_left": 0, "gap": int(round(gap_s * sr)) if segments else 0, "done": False}
        with self._lock:
            self._state.update(playing=True, rel=rel, pos=f0 / sr, mode="result" if segments else "original",
                               error="")
        opener = opener or self._open_stream
        self._stream = opener(sr, self._callback)
        return self.state()

    def stop(self):
        s, self._stream = self._stream, None
        if s is not None:
            try:
                s.stop()
                s.close()
            except Exception:
                pass
        with self._lock:
            self._state["playing"] = False
        return self.state()

    def state(self):
        with self._lock:
            st = dict(self._state)
        if st["playing"] and getattr(self, "_pb", None) and self._pb["done"]:
            self.stop()
            st["playing"] = False
        return st

    # ---------- audio ----------
    def _open_stream(self, sr, callback):
        sd = _sd()
        dev = _device_index(sd, self.device_name)
        stream = sd.OutputStream(samplerate=sr, channels=2, dtype="float32", blocksize=BLOCK,
                                 device=dev, callback=lambda out, n, t, s: callback(out, n), latency="low")
        stream.start()
        return stream

    def _render(self, n):
        """n output frames (float32, n x 2) from the current position"""
        pb = self._pb
        info, out, filled = pb["info"], np.zeros((n, 2), np.float32), 0
        while filled < n and not pb["done"]:
            if pb["gap_left"]:
                k = min(pb["gap_left"], n - filled)
                pb["gap_left"] -= k
                filled += k
                continue
            a, b = pb["segs"][pb["seg"]]
            k = min(b - pb["frame"], n - filled)
            if k > 0:
                x = fx_core.decode(fx_core.frames_bytes(pb["raw"], info, pb["frame"], pb["frame"] + k, pb["chans"]),
                                   info.bps, len(pb["chans"]), info.is_float).astype(np.float32)
                if x.shape[1] == 2:
                    out[filled:filled + k] = x
                else:
                    m = x.sum(axis=1) / np.sqrt(x.shape[1])
                    out[filled:filled + k, 0] = m
                    out[filled:filled + k, 1] = m
                pb["frame"] += k
                filled += k
            if pb["frame"] >= b:
                if pb["seg"] + 1 < len(pb["segs"]):
                    pb["seg"] += 1
                    pb["frame"] = pb["segs"][pb["seg"]][0]
                    pb["gap_left"] = pb["gap"]
                else:
                    pb["done"] = True
        with self._lock:
            self._state["pos"] = pb["frame"] / info.sr
        return out

    def _callback(self, outdata, frames):
        try:
            outdata[:] = self._render(frames)
        except Exception as ex:          # never let an exception kill the audio thread silently
            outdata[:] = 0
            self._pb["done"] = True
            with self._lock:
                self._state["error"] = str(ex)
