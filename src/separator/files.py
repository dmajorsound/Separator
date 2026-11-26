"""Separator – finding source files and remembering finished ones.

Folder scan, drag-and-drop expansion, the per-mode 'done' list that lets a stopped run resume,
and the track-name scan for the track filter.
"""

import os

import config as cfg
from wavio import read_wav_info
from metadata import read_meta


def scan_tracks(src, limit=0, files=None):
    """Read only the headers: which track names occur, and how often"""
    counts = {}
    files = files if files is not None else find_files(src, os.path.join(src, "\0"))
    for p in files[:limit or None]:
        try:
            info = read_wav_info(p)
            names = read_meta(info, p)["tracks"]
        except Exception:
            continue
        if not any(names):
            names = ["(no names)"]
        for n in names:
            n = (n or "(empty)").strip()
            counts[n] = counts.get(n, 0) + 1
    return len(files), sorted(counts.items(), key=lambda kv: -kv[1])


DONE_NAME = {"library": ".separator_done.txt", "dx": ".separator_dx_done.txt"}


def _read_done_file(p):
    out = {}
    if os.path.exists(p):
        for line in open(p, encoding="utf-8").read().splitlines():
            f = line.split("\t")
            if f[0]:
                out[f[0]] = (f[1] if len(f) > 1 else "ok", int(f[2]) if len(f) > 2 and f[2].isdigit() else 0)
    return out


def read_done(dst):
    """Fertig-Liste: {rel: (status, anzahl)} – auch alte Zeilen ohne Status"""
    out = {}
    p = os.path.join(dst, DONE_NAME[cfg.MODE])
    if cfg.MODE == "dx":
        return _read_done_file(p)
    if not os.path.exists(p):
        p = os.path.join(dst, ".fx_fertig.txt")   # from earlier versions
    if os.path.exists(p):
        for line in open(p, encoding="utf-8").read().splitlines():
            f = line.split("\t")
            if f[0]:
                out[f[0]] = (f[1] if len(f) > 1 else "ok", int(f[2]) if len(f) > 2 and f[2].isdigit() else 0)
    return out


def is_audio(name):
    return name.lower().endswith((".wav", ".bwf")) and not name.startswith("._") and not name.endswith(".part")


def expand_paths(paths, dst=None):
    """Dropped files and folders -> sorted list of WAV/BWF files (folders searched recursively)"""
    out, dst = set(), os.path.abspath(dst) if dst else None
    for p in paths:
        p = os.path.abspath(p)
        if os.path.isdir(p):
            for root, dirs, names in os.walk(p):
                if dst and os.path.abspath(root).startswith(dst):
                    dirs[:] = []
                    continue
                dirs[:] = [d for d in dirs if not d.startswith(".")]
                out.update(os.path.join(root, n) for n in names if is_audio(n))
        elif os.path.isfile(p) and is_audio(os.path.basename(p)):
            out.add(p)
    return sorted(out)


def common_root(files):
    """Folder above all given files (the file's own folder for a single file)"""
    if not files:
        return ""
    root = os.path.commonpath([os.path.dirname(f) for f in files])
    return root


def find_files(src, dst):
    files = []
    for root, _, names in os.walk(src):
        if os.path.abspath(root).startswith(os.path.abspath(dst)):
            continue
        for n in sorted(names):
            if n.lower().endswith((".wav", ".bwf")) and not n.startswith("._") and not n.endswith(".part"):
                files.append(os.path.join(root, n))
    return sorted(files)
