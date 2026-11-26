"""Separator – DX Delete (feature module).

Keeps every file where it is, with its exact name and all tracks, and only cuts dialogue and
slates. Markers show what is left. Uses the shared engine and nothing from FX Library, so it can
be built as its own app ("Separator DX").
"""

import os

import numpy as np

import config as cfg
from ucs import DE_NAMES
from wavio import _chunk, read_wav_info, tc, to_wavpack, write_bwf
from metadata import list_info, new_bext, new_ixml, read_meta, select_channels
from analysis import analyse
from views import file_view


KEY = "dx"
TITLE = "DX DELETE"
def preview_parts(runs, meta):
    """Feature interface: the parts the track view shows (everything that is not dialogue, in one file)"""
    segs = [{"start": r["start"], "end": r["end"], "events": r["events"], "cut": True} for r in runs]
    return [{"cat": "*", "segs": segs}] if segs else []


def process(path, dst, rel_dir, an, dry, log, rel, view=None, **_):
    """DX Delete: same folder and file name, all tracks kept, only dialogue (and slates) cut, markers added"""
    info = read_wav_info(path)
    meta = read_meta(info, path)
    chans = select_channels(meta["tracks"])          # tracks the dialogue is detected on
    raw = np.memmap(path, dtype=np.uint8, mode="r", offset=info.data_off,
                    shape=(info.n_frames * info.block,))
    if not chans:
        chans = list(range(info.channels))
    runs, dur = analyse(info, raw, an, chans, path)
    segs = [{"start": r["start"], "end": r["end"], "events": r["events"], "cut": True} for r in runs]
    if view:
        view(file_view(path, info, meta, raw, [{"cat": "*", "segs": segs}] if segs else [], rel))
    if not segs:
        return "dialog", [], "dialogue only"
    sr, all_ch = info.sr, list(range(info.channels))
    frame_runs, markers, out_pos = [], [], 0
    for i, sg in enumerate(segs):
        s_f, e_f = int(round(sg["start"] * sr)), min(info.n_frames, int(round(sg["end"] * sr)))
        if e_f <= s_f:
            continue
        frame_runs.append((s_f, e_f))
        for j, (s, e, l, p) in enumerate(sg["events"]):
            es = int(round(s * sr)) - s_f
            src_tc = tc((info.time_ref + int(round(s * sr))) / sr)
            prefix = "CUT/ " if (j == 0 and i > 0) else ""
            label = "QUIET" if l == "QUIET" else l
            markers.append((out_pos + max(0, es), max(0, int(round((e - s) * sr))),
                            f"{prefix}{label} | {DE_NAMES.get(l, l)} | {src_tc}"))
            log.writerow([rel, rel, l, DE_NAMES.get(l, l), f"{p:.2f}", f"{s:.3f}", src_tc,
                          f"{(out_pos + es) / sr:.3f}", f"{e - s:.3f}"])
        out_pos += e_f - s_f
    out_dir = os.path.join(dst, rel_dir)
    out_path = os.path.join(out_dir, os.path.basename(path))     # exactly the original file name
    if not dry:
        new_ref = info.time_ref + frame_runs[0][0]
        extra = _chunk(b"bext", new_bext(info.bext, new_ref, None,
                                         f"A=PCM,T=Separator DX Delete: dialogue removed, {len(frame_runs)} parts"))
        if info.ixml:
            extra += _chunk(b"iXML", new_ixml(info.ixml, new_ref, all_ch, meta["tracks"], None))
        if info.list_info:
            extra += list_info(info.list_info)
        os.makedirs(out_dir, exist_ok=True)
        write_bwf(out_path, info, all_ch, frame_runs, raw, extra, markers, 0)
        if cfg.OUTPUT_FORMAT == "wavpack":
            out_path = to_wavpack(out_path, {})
    elif cfg.OUTPUT_FORMAT == "wavpack":
        out_path = os.path.splitext(out_path)[0] + ".wv"
    o = {"name": os.path.basename(out_path), "dir": rel_dir or ".", "catid": "DX", "dur": round(out_pos / sr, 1),
         "markers": len(markers)}
    return "ok", [o], f"({dur:.0f}s) → {o['name']} ({o['dur']:.0f}s, {len(markers)} markers)"
