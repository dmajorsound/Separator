"""Separator – App-Fenster (HTML-Oberflaeche in einem eigenen Mac-Fenster via pywebview).
Faellt auf das einfache Tk-Fenster (gui.py) zurueck, falls pywebview fehlt."""

import os
import sys
import json
import re
import time
import threading
import subprocess
from collections import Counter

HERE = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
import fx_core  # noqa: E402
import player  # noqa: E402

VERSION = "1.2"
REPO = "dmajorsound/Separator"
APP_DIR = os.path.expanduser("~/Library/Application Support/Separator")
SETTINGS = os.path.join(APP_DIR, "settings.json")


def load_settings():
    try:
        with open(SETTINGS, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_settings(d):
    try:
        os.makedirs(APP_DIR, exist_ok=True)
        with open(SETTINGS, "w", encoding="utf-8") as f:
            json.dump(d, f, indent=2, ensure_ascii=False)
    except Exception:
        pass


class Api:
    def __init__(self):
        self._window = None
        self._settings = load_settings()
        self._lock = threading.Lock()
        self._events = []          # (seq, kind, data)
        self._seq = 0
        self._view = None
        self._view_seq = 0
        self._running = False
        self._stop_flag = False
        self._analyzer = None
        self._t_start = None
        self._n_total = 0
        self._n_done = 0
        self._secs_done = 0.0
        self._durs = {}
        self._player = player.Player()
        self._player.device_name = self._settings.get("audio_device", "")   # "" = system default

    # ---------- intern ----------
    def _push(self, kind, data):
        with self._lock:
            self._seq += 1
            self._events.append((self._seq, kind, data))
            if len(self._events) > 5000:
                self._events = self._events[-2000:]

    def _set_view(self, v):
        with self._lock:
            self._view = v
            self._view_seq += 1

    def _apply_filter(self, f):
        if not f:
            return
        words = lambda s: [w.strip().lower() for w in str(s).replace(";", ",").split(",") if w.strip()]
        fx_core.KEEP_TRACKS = words(f.get("keep", ""))
        fx_core.IGNORE_TRACKS = words(f.get("drop", ""))
        if f.get("unknown") in ("fallback", "keep", "drop"):
            fx_core.UNKNOWN_TRACKS = f["unknown"]

    # ---------- Einstellungen ----------
    def init(self):
        s = self._settings
        return {
            "version": VERSION,
            "src": s.get("src", ""), "dst": s.get("dst", ""),
            "items": [p for p in s.get("items", []) if os.path.isfile(p)],
            "opts": {
                "creator": s.get("creator", fx_core.CREATOR_ID), "fmt": s.get("fmt", "wav"),
                "min_s": s.get("min_s", fx_core.MIN_FILE_S), "max_s": s.get("max_s", fx_core.MAX_FILE_S),
                "test": s.get("test", True), "theme": s.get("theme", "dark"),
                "audio_device": s.get("audio_device", ""),
                "gap_s": s.get("gap_s", fx_core.GAP_S), "group": s.get("group", fx_core.GROUP_BY_CAT),
                "bundle": s.get("bundle", fx_core.BUNDLE_AFTER_RUN), "wild": s.get("wild", fx_core.WILD_CONTINUOUS),
            },
            "filter": self.project_filter(s.get("src", "")),
            "wavpack": bool(fx_core.wavpack_bin()),
        }

    def project_filter(self, src):
        p = self._settings.get("projects", {}).get(os.path.abspath(src) if src else "", {})
        return {
            "keep": p.get("keep", ", ".join(fx_core.KEEP_TRACKS)),
            "drop": p.get("drop", ", ".join(fx_core.IGNORE_TRACKS)),
            "unknown": p.get("unknown", fx_core.UNKNOWN_TRACKS),
            "sort_ucs": p.get("sort_ucs", False), "split": p.get("split", False),
        }

    def save(self, src, dst, opts, flt, items=None):
        s = self._settings
        s.update({"src": src, "dst": dst, "items": list(items or [])})
        for k in ("creator", "fmt", "min_s", "max_s", "test", "theme", "gap_s", "group", "bundle", "wild"):
            if k in opts:
                s[k] = opts[k]
        if src and flt is not None:
            s.setdefault("projects", {})[os.path.abspath(src)] = flt
        save_settings(s)
        return True

    # ---------- Dateien ----------
    def pick_folder(self, title=""):
        import webview
        kind = getattr(getattr(webview, "FileDialog", None), "FOLDER", None)
        if kind is None:
            kind = webview.FOLDER_DIALOG
        r = self._window.create_file_dialog(kind)
        if not r:
            return ""
        return r[0] if isinstance(r, (list, tuple)) else r

    def list_files(self, src, dst, items=None):
        """items: explicit absolute file paths (from drag and drop); None = the whole source folder"""
        if items:
            files = [p for p in items if os.path.isfile(p)]
            if not files:
                return {"ok": False, "msg": "The dropped files are no longer there."}
            src = fx_core.common_root(files)
        else:
            if not src or not os.path.isdir(src):
                return {"ok": False, "msg": "Source folder not found"}
            src = os.path.abspath(src)
            files = fx_core.find_files(src, dst or os.path.join(src, "\0"))
        done = fx_core.read_done(dst) if dst and os.path.isdir(dst) else {}
        out, sr, bits, total = [], Counter(), Counter(), 0.0
        for p in files:
            try:
                d = fx_core.file_summary(p, src)
            except Exception as ex:
                d = {"rel": os.path.relpath(p, src), "dur": 0, "tracks": [], "error": str(ex)}
            st = done.get(d["rel"])
            d["status"], d["count"] = (st if st else ("error" if d.get("error") else "open", 0))
            total += d.get("dur", 0)
            if d.get("sr"):
                sr[d["sr"]] += 1
                bits[d["bits"]] += 1
            self._durs[d["rel"]] = d.get("dur", 0)
            out.append(d)
        name = os.path.basename(src.rstrip("/")) or src
        if items:
            name = f"{len(files)} dropped file{'s' if len(files) != 1 else ''}"
        return {"ok": True, "files": out, "total_s": total, "src": src,
                "sr": sr.most_common(1)[0][0] if sr else 0, "bits": bits.most_common(1)[0][0] if bits else 0,
                "name": name}

    def expand_drop(self, paths, dst=None):
        """Dropped files/folders -> WAV/BWF files; also counts what was skipped"""
        files = fx_core.expand_paths(paths or [], dst)
        return {"files": files, "skipped": sum(1 for p in paths or [] if os.path.isfile(p)
                                               and not fx_core.is_audio(os.path.basename(p)))}

    def _on_drop(self, e):
        try:
            fl = (e.get("dataTransfer") or {}).get("files") or []
            paths = [f.get("pywebviewFullPath") for f in fl if f.get("pywebviewFullPath")]
        except Exception:
            paths = []
        if self._window is not None:
            self._window.evaluate_js("window.onDropPaths && window.onDropPaths(%s)" % json.dumps(paths))

    def scan_tracks(self, src, flt, items=None):
        self._apply_filter(flt)
        n, counts = fx_core.scan_tracks(src, files=items or None)
        rows = []
        for name, c in counts:
            cls = "keep" if name.startswith("(") else fx_core.classify_track(name)
            rows.append({"name": name, "count": c, "cls": cls})
        return {"files": n, "rows": rows}

    def preview(self, src, rel, flt):
        self._apply_filter(flt)
        try:
            v = fx_core.preview(os.path.join(src, rel), None, rel)
        except Exception as ex:
            return {"error": str(ex)}
        return v

    def analyse_preview(self, src, rel, flt, opts=None):
        """Einzelnes File analysieren (Schnitt + FX), ohne zu schreiben"""
        self._apply_filter(flt)
        if opts:
            fx_core.MIN_FILE_S = max(fx_core.MIN_SEGMENT_S, float(opts.get("min_s", fx_core.MIN_FILE_S)))
            fx_core.MAX_FILE_S = max(0.0, float(opts.get("max_s", fx_core.MAX_FILE_S)))
            fx_core.GAP_S = max(0.0, float(opts.get("gap_s", fx_core.GAP_S)))
            fx_core.GROUP_BY_CAT = bool(opts.get("group", True))
            fx_core.WILD_CONTINUOUS = bool(opts.get("wild", True))
        try:
            if self._analyzer is None:
                self._push("log", "Loading AI models … (first time only, takes a moment)")
                self._analyzer = fx_core.Analyzer(log=lambda m: self._push("log", m))
            return fx_core.preview(os.path.join(src, rel), self._analyzer, rel)
        except Exception as ex:
            return {"error": str(ex)}

    def open_url(self, url):
        if str(url).startswith("https://"):
            subprocess.run(["open", url])
        return True

    def check_update(self):
        """Check GitHub for a newer release; silent when offline (SEPARATOR_UPD=x.y shows the notice for testing)"""
        import urllib.request
        if os.environ.get("SEPARATOR_UPD"):
            return {"latest": os.environ["SEPARATOR_UPD"], "url": f"https://github.com/{REPO}/releases/latest"}
        try:
            req = urllib.request.Request(f"https://api.github.com/repos/{REPO}/releases/latest",
                                         headers={"Accept": "application/vnd.github+json", "User-Agent": "Separator"})
            with urllib.request.urlopen(req, timeout=4) as r:
                d = json.load(r)
            tag = str(d.get("tag_name", "")).lstrip("vV")
            num = lambda v: tuple(int(x) for x in re.findall(r"\d+", v)[:3])
            if tag and num(tag) > num(VERSION):
                return {"latest": tag, "url": d.get("html_url", f"https://github.com/{REPO}/releases/latest")}
        except Exception:
            pass
        return {}

    def open_folder(self, path):
        if path and os.path.isdir(path):
            subprocess.run(["open", path])
        return True

    # ---------- Lauf ----------
    def start(self, src, dst, opts, flt, only=None, items=None):
        if self._running:
            return {"ok": False, "msg": "Already running"}
        if items:
            items = [p for p in items if os.path.isfile(p)]
            src = fx_core.common_root(items) if items else src
        if not os.path.isdir(src or ""):
            return {"ok": False, "msg": "Please choose a source folder or drop audio files."}
        if not dst:
            return {"ok": False, "msg": "Please choose a destination folder."}
        if os.path.abspath(dst) == os.path.abspath(src):
            return {"ok": False, "msg": "The destination can't be the source folder."}
        if opts.get("fmt") == "wavpack" and not fx_core.wavpack_bin():
            return {"ok": False, "msg": "WavPack is missing from this build – please choose WAV."}
        self.save(src, dst, opts, flt, items)
        self._running, self._stop_flag = True, False
        self._t_start, self._n_done, self._secs_done = time.time(), 0, 0.0
        threading.Thread(target=self._work, args=(src, dst, opts, flt, only, items), daemon=True).start()
        return {"ok": True}

    def _on_event(self, kind, data):
        if kind == "view":
            self._set_view(data)
            return
        if kind == "start":
            self._n_total = data["n"]
        if kind in ("done", "skip"):
            self._n_done = data["i"]
            if kind == "done":
                self._secs_done += self._durs.get(data["rel"], 0)
        self._push(kind, data)

    def _work(self, src, dst, opts, flt, only, items=None):
        try:
            if self._analyzer is None:
                self._push("log", "Loading AI models … (first time only, takes a moment)")
                self._analyzer = fx_core.Analyzer(log=lambda m: self._push("log", m))
            words = lambda s: [w.strip() for w in str(s).replace(";", ",").split(",") if w.strip()]
            fx_core.run(src, dst, creator=(opts.get("creator") or fx_core.CREATOR_ID).strip(),
                        limit=20 if opts.get("test") else 0,
                        log=lambda m: self._push("log", m),
                        stop=lambda: self._stop_flag, analyzer=self._analyzer,
                        min_s=float(opts.get("min_s", fx_core.MIN_FILE_S)),
                        max_s=float(opts.get("max_s", fx_core.MAX_FILE_S)),
                        fmt=opts.get("fmt", "wav"), sort_ucs=flt.get("sort_ucs", False),
                        split=flt.get("split", False), keep_words=words(flt.get("keep", "")),
                        drop_words=words(flt.get("drop", "")), unknown=flt.get("unknown"),
                        on_event=self._on_event, only=only,
                        group_cat=opts.get("group", True), gap_s=float(opts.get("gap_s", fx_core.GAP_S)),
                        bundle=opts.get("bundle", True), wild_cont=opts.get("wild", True),
                        files=items or None)
        except Exception as ex:
            self._push("log", f"ERROR: {ex}")
        self._running = False
        self._push("stopped", {"stopped": self._stop_flag})

    def bundle(self, dst, opts):
        """Projekt-Sausages aus allen bisher gemerkten Regionen (neu) bauen"""
        if self._running:
            return {"ok": False, "msg": "Already running"}
        if not dst or not os.path.isdir(dst):
            return {"ok": False, "msg": "Please choose a destination folder."}
        self._running, self._stop_flag = True, False

        def work():
            try:
                if self._analyzer is None:
                    self._push("log", "Loading AI models … (first time only, takes a moment)")
                    self._analyzer = fx_core.Analyzer(log=lambda m: self._push("log", m))
                fx_core.GAP_S = max(0.0, float(opts.get("gap_s", fx_core.GAP_S)))
                fx_core.MAX_FILE_S = max(0.0, float(opts.get("max_s", fx_core.MAX_FILE_S)))
                fx_core.OUTPUT_FORMAT = opts.get("fmt", "wav")
                fx_core.bundle_project(dst, self._analyzer, (opts.get("creator") or fx_core.CREATOR_ID).strip(),
                                       log=lambda m: self._push("log", m), on_event=self._on_event)
            except Exception as ex:
                self._push("log", f"ERROR project sausages: {ex}")
            self._running = False
            self._push("stopped", {"stopped": False, "bundle": True})
        threading.Thread(target=work, daemon=True).start()
        return {"ok": True}

    # ---------- preview playback ----------
    def audio_devices(self):
        try:
            return {"ok": True, "devices": player.list_devices(), "current": self._player.device_name}
        except Exception as ex:
            return {"ok": False, "msg": str(ex), "devices": [], "current": ""}

    def set_audio_device(self, name):
        """"" = follow the system default output"""
        self._player.stop()
        self._player.device_name = name or ""
        self._settings["audio_device"] = self._player.device_name
        save_settings(self._settings)
        return True

    def play(self, src, rel, flt, pos=0.0, segments=None, gap_s=0.0):
        self._apply_filter(flt)
        try:
            path = os.path.join(src, rel)
            info = fx_core.read_wav_info(path)
            chans = fx_core.select_channels(fx_core.read_meta(info, path)["tracks"])
            return {"ok": True, **self._player.play(path, rel, chans, float(pos or 0),
                                                    segments or None, float(gap_s or 0))}
        except Exception as ex:
            self._player.stop()
            return {"ok": False, "msg": str(ex)}

    def stop_play(self):
        return self._player.stop()

    def play_state(self):
        return self._player.state()

    def stop(self):
        self._stop_flag = True
        return True

    def poll(self, since, view_seq):
        with self._lock:
            evs = [{"seq": s, "kind": k, "data": d} for s, k, d in self._events if s > since]
            v = self._view if self._view_seq > view_seq else None
            vs = self._view_seq
        eta = None
        if self._running and self._n_done and self._t_start:
            per = (time.time() - self._t_start) / max(1, self._n_done)
            eta = per * max(0, self._n_total - self._n_done)
        return {"events": evs, "seq": self._seq, "view": v, "view_seq": vs, "running": self._running,
                "done": self._n_done, "total": self._n_total, "eta": eta,
                "device": getattr(self._analyzer, "device", "")}


def main():
    try:
        import webview
    except Exception:
        import gui
        gui.main()
        return
    api = Api()
    s = api._settings
    win = webview.create_window(
        "Separator", os.path.join(HERE, "web", "index.html"), js_api=api,
        width=s.get("win_w", 1280), height=s.get("win_h", 820), min_size=(1040, 680),
        background_color="#2B2D31")
    api._window = win

    def on_loaded():
        try:
            from webview.dom import DOMEventHandler
            win.dom.document.events.drop += DOMEventHandler(api._on_drop, True, True)
        except Exception as ex:          # older pywebview: drop shows a hint instead
            print("drag and drop unavailable:", ex)
    win.events.loaded += on_loaded
    webview.start()


if __name__ == "__main__":
    main()
