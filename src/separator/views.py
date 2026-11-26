"""Separator – data for the track view (waveform peaks, cut and kept parts, FX regions) and the
header summary shown in the take list.
"""

import os

from ucs import DE_NAMES
from wavio import peaks, read_wav_info, tc
from metadata import classify_track, read_meta, select_channels
from analysis import merge


def file_view(path, info, meta, raw, parts=None, rel=""):
    """Everything the track view needs for one take"""
    dur = info.n_frames / info.sr
    pk = peaks(raw, info)
    used = set(select_channels(meta["tracks"]))
    lanes = []
    for i in range(len(pk)):
        name = meta["tracks"][i] or f"Track {i + 1}"
        cls = classify_track(meta["tracks"][i]) if any(meta["tracks"]) else "keep"
        lanes.append({"name": name, "cls": cls, "used": i in used, "peaks": pk[i]})
    v = {"rel": rel, "file": meta["file"], "dur": round(dur, 3), "tc": tc(info.time_ref / info.sr),
         "scene": meta.get("scene", ""), "take": meta.get("take", ""), "note": meta.get("note", ""),
         "wild": meta["wild"], "sr": info.sr, "bits": info.bits, "lanes": lanes,
         "analysed": parts is not None, "fx": [], "keep": []}
    if parts is not None:
        for pt in parts:
            for sg in pt["segs"]:
                v["keep"].append([round(sg["start"], 3), round(sg["end"], 3)])
                for s, e, l, p in sg["events"]:
                    v["fx"].append([round(s, 3), round(e, 3), l, DE_NAMES.get(l, l)])
        v["keep"] = [list(x) for x in merge([tuple(k) for k in v["keep"]])]
    return v


def file_summary(path, src):
    """Quick header data for the take list (reads no audio)"""
    info = read_wav_info(path)
    meta = read_meta(info, path)
    return {"rel": os.path.relpath(path, src), "dur": round(info.n_frames / info.sr, 2), "sr": info.sr,
            "bits": info.bits, "ch": info.channels, "scene": meta.get("scene", ""),
            "take": meta.get("take", ""), "wild": meta["wild"],
            "tracks": [t or "" for t in meta["tracks"]]}
