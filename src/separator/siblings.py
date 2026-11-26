"""Separator – sibling files of one take.

Many recorders and Pro Tools exports write one mono file per track: the boom, every lav and the
wireless mix of the same take sit next to each other as separate WAVs. They share tape, scene, take
and start timecode. This module finds them, so that

  * speech is detected on the lavs too (they carry the cleanest dialogue) and cut from the boom, and
  * a file whose only track is a person's name (a lav named after the actor) is recognized as a lav
    when a boom or atmo file of the same take exists.

Only the file headers are read; the folder index is cached until the folder changes.
"""

import os

from files import is_audio
from metadata import classify_track, read_meta
from wavio import read_wav_info

_cache = {}          # folder -> (mtime, {path: key}, {path: (info, meta)})


def _key(info, meta):
    """Take identity: tape/scene/take plus the start timecode (to the second)"""
    if not (meta.get("scene") or meta.get("take")):
        return None
    return (meta.get("tape") or "", meta.get("scene") or "", meta.get("take") or "",
            int(round((info.time_ref or 0) / max(info.sr, 1))))


def _index(folder):
    try:
        mtime = os.path.getmtime(folder)
    except OSError:
        return {}, {}
    hit = _cache.get(folder)
    if hit and hit[0] == mtime:
        return hit[1], hit[2]
    keys, heads = {}, {}
    for name in os.listdir(folder):
        p = os.path.join(folder, name)
        if name.startswith(".") or not is_audio(p) or not os.path.isfile(p):
            continue
        try:
            info = read_wav_info(p)
            meta = read_meta(info, p)
        except Exception:
            continue
        k = _key(info, meta)
        if k:
            keys[p], heads[p] = k, (info, meta)
    _cache[folder] = (mtime, keys, heads)
    return keys, heads


def find(path):
    """[(path, info, meta)] of the other files of the same take, in the same folder"""
    path = os.path.abspath(path)
    keys, heads = _index(os.path.dirname(path))
    k = keys.get(path)
    if not k:
        return []
    return [(p, *heads[p]) for p, kk in sorted(keys.items()) if kk == k and p != path]


def is_lav_by_context(meta, sibs):
    """True when every track of this file is unnamed by the filter (e.g. an actor's name) while a
    sibling of the same take has a real FX track (boom, atmo …): then this file is a lav."""
    tracks = [t for t in meta.get("tracks", []) if t]
    if not tracks or any(classify_track(t) != "unknown" for t in tracks):
        return False
    return any(classify_track(t) == "keep" for _, _, m in sibs for t in m.get("tracks", []) if t)
