"""Separator – the run loop shared by both features.

run() walks the source files, hands each one to the active feature module (FX Library or
DX Delete), keeps the done list and the marker CSV, and reports progress events to the app.

Feature modules are loaded only when used (feature()), so an app built with just one of them
(see edition.py) does not need the other. Every feature module provides:
    KEY, TITLE                                  "library" / "dx", tab title
    process(path, dst, rel_dir, an, dry, log, rel, creator=…, taken=…, view=…)
                                                -> (status, outputs, message) for one take
    preview_parts(runs, meta)                   -> parts for the track view (what would be written)
    after_run(dst, an, creator, log, on_event)  optional, runs once after all takes
"""

import importlib

import csv
import os
import time

import numpy as np

import config as cfg
from wavio import read_wav_info, wavpack_bin
from metadata import read_meta, select_channels
from analysis import Analyzer, analyse
from views import file_view
from files import DONE_NAME, find_files, read_done
import edition

FEATURE_MODULES = {"library": "fx_library", "dx": "dx_delete"}


def feature(mode=None):
    """The feature module for a mode ("library" or "dx"); refuses features this app was built without"""
    mode = mode or cfg.MODE
    if mode not in edition.FEATURES:
        raise RuntimeError(f"This app was built without the {mode} feature")
    return importlib.import_module(FEATURE_MODULES[mode])


def preview(path, an=None, rel=""):
    """Track view for one take: waveforms; with an analyzer also cuts and FX regions (writes nothing)"""
    info = read_wav_info(path)
    meta = read_meta(info, path)
    raw = np.memmap(path, dtype=np.uint8, mode="r", offset=info.data_off,
                    shape=(info.n_frames * info.block,))
    parts = None
    chans = select_channels(meta["tracks"])
    if an is not None and chans:
        runs, _ = analyse(info, raw, an, chans, path)
        parts = feature().preview_parts(runs, meta)
    return file_view(path, info, meta, raw, parts, rel)


def run(src, dst, creator=cfg.CREATOR_ID, limit=0, dry=False, log=print,
        progress=lambda i, n: None, stop=lambda: False, analyzer=None,
        min_s=None, max_s=None, fmt=None, sort_ucs=None, split=None,
        keep_words=None, drop_words=None, unknown=None, on_event=None, only=None,
        group_cat=None, gap_s=None, bundle=None, wild_cont=None, files=None,
        min_level=None, vocal=None, redo=False, mode=None):
    if mode in ("library", "dx"):
        cfg.MODE = mode
    if min_level is not None:
        cfg.MIN_LEVEL_DBFS = float(min_level)
    if vocal in ("keep", "cut"):
        cfg.VOCAL_MODE = vocal
    if bundle is not None:
        cfg.BUNDLE_AFTER_RUN = bool(bundle)
    if wild_cont is not None:
        cfg.WILD_CONTINUOUS = bool(wild_cont)
    if group_cat is not None:
        cfg.GROUP_BY_CAT = bool(group_cat)
    if gap_s is not None:
        cfg.GAP_S = max(0.0, float(gap_s))
    if sort_ucs is not None:
        cfg.SORT_BY_CATEGORY = bool(sort_ucs)
    if split is not None:
        cfg.SPLIT_TRACKS = bool(split)
    if keep_words is not None:
        cfg.KEEP_TRACKS = [w.strip().lower() for w in keep_words if w.strip()]
    if drop_words is not None:
        cfg.IGNORE_TRACKS = [w.strip().lower() for w in drop_words if w.strip()]
    if unknown in ("fallback", "keep", "drop"):
        cfg.UNKNOWN_TRACKS = unknown
    if fmt:
        cfg.OUTPUT_FORMAT = fmt
    if cfg.OUTPUT_FORMAT == "wavpack" and not wavpack_bin():
        log("ERROR: WavPack is not available – please choose WAV.")
        return
    if min_s is not None:
        cfg.MIN_FILE_S = max(cfg.MIN_SEGMENT_S, float(min_s))
    if max_s is not None:
        cfg.MAX_FILE_S = max(0.0, float(max_s))
    if cfg.MAX_FILE_S and cfg.MAX_FILE_S < cfg.MIN_FILE_S:
        cfg.MAX_FILE_S = cfg.MIN_FILE_S
    src, dst = os.path.abspath(src), os.path.abspath(dst)
    ev = on_event or (lambda kind, data: None)
    files = sorted(os.path.abspath(f) for f in files) if files is not None else find_files(src, dst)
    if only:
        wanted = set(only)
        files = [f for f in files if os.path.relpath(f, src) in wanted]
    if limit:
        files = files[:limit]
    log(f"{len(files)} WAV files found. Use tracks: {', '.join(cfg.KEEP_TRACKS) or '–'} · "
        f"drop: {', '.join(cfg.IGNORE_TRACKS) or '–'} · unknown: {cfg.UNKNOWN_TRACKS}"
        + (" · single tracks" if cfg.SPLIT_TRACKS else "") + (" · UCS folders" if cfg.SORT_BY_CATEGORY else ""))
    if not files:
        return
    feat = feature()                      # the active feature module (FX Library or DX Delete)
    an = analyzer or Analyzer(log)
    os.makedirs(dst, exist_ok=True)
    cfg.DIAG_DIR = os.path.join(dst, "_diagnostics") if cfg.DIAGNOSTICS else ""
    done_path = os.path.join(dst, DONE_NAME[cfg.MODE])
    done = set(read_done(dst))
    log_path = os.path.join(dst, "fx_marker_list.csv")
    new_log = not os.path.exists(log_path)
    taken = set()
    with open(log_path, "a", newline="", encoding="utf-8") as lf, \
            open(done_path, "a", encoding="utf-8") as df:
        w = csv.writer(lf, delimiter=";")
        if new_log:
            w.writerow(["Source", "New file", "UCS CatID", "German term", "Confidence", "Pos. original (s)",
                        "Timecode Original", "Pos. neues File (s)", "Dauer (s)"])
        t0 = time.time()
        for i, path in enumerate(files, 1):
            if stop():
                log("Stopped. The next start picks up here.")
                break
            progress(i - 1, len(files))
            rel = os.path.relpath(path, src)
            if rel in done and not dry and not redo:
                log(f"[{i}/{len(files)}] {rel}: already done")
                ev("skip", {"i": i, "n": len(files), "rel": rel})
                continue
            t = time.time()
            ev("start", {"i": i, "n": len(files), "rel": rel})
            try:
                status, outs, msg = feat.process(path, dst, os.path.dirname(rel), an, dry, w, rel,
                                                 creator=creator, taken=taken, view=lambda v: ev("view", v))
                if not dry:
                    df.write(f"{rel}\t{status}\t{len(outs)}\n")
                    df.flush()
            except Exception as ex:
                status, outs, msg = "error", [], f"ERROR: {ex}"
            lf.flush()
            log(f"[{i}/{len(files)}] {rel} {msg} ({time.time() - t:.0f}s)")
            ev("done", {"i": i, "n": len(files), "rel": rel, "status": status, "outputs": outs,
                        "msg": msg, "secs": round(time.time() - t, 1)})
        progress(len(files), len(files))
        ev("finished", {"log": log_path})
    if hasattr(feat, "after_run") and not dry and not stop():
        try:
            feat.after_run(dst, an, creator, log, ev)
        except Exception as ex:
            log(f"ERROR after the run: {ex}")
    log(f"Done in {(time.time() - t0) / 60:.1f} min. List: {log_path}")
