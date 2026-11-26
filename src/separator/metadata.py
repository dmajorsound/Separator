"""Separator – production sound metadata.

Reads what the recorder wrote (bext, iXML, RIFF INFO: project, scene, take, notes, track names),
decides which tracks to use (track filter) and writes updated bext / iXML / INFO chunks.
"""

import os
import re
import struct
from xml.sax.saxutils import escape

import config as cfg
from ucs import _ascii
from wavio import _chunk


def _tag(xml, name):
    m = re.search(rf"<{name}>(.*?)</{name}>", xml, re.S | re.I)
    return re.sub(r"<[^>]+>", " ", m.group(1)).strip() if m else ""


def read_meta(info, path):
    m = {"file": os.path.basename(path), "tracks": [None] * info.channels, "free": []}
    # bext description lines (sSCENE=…, sTRK1=…) and free text
    if info.bext and len(info.bext) >= 602:
        b = info.bext
        desc = b[:256].split(b"\0")[0].decode("latin-1", "replace")
        m["originator"] = b[256:288].split(b"\0")[0].decode("latin-1", "replace").strip()
        m["date"] = b[320:330].decode("latin-1", "replace").strip("\0 ")
        m["time"] = b[330:338].decode("latin-1", "replace").strip("\0 ")
        for line in re.split(r"[\r\n]+", desc):
            line = line.strip()
            if not line:
                continue
            kv = re.match(r"^[a-z]?([A-Z][A-Z0-9_]*)=(.*)$", line)
            if kv:
                k, v = kv.group(1), kv.group(2).strip()
                t = re.match(r"TRK(\d+)$", k)
                if t and 0 < int(t.group(1)) <= info.channels:
                    m["tracks"][int(t.group(1)) - 1] = v
                elif k in ("SCENE", "TAKE", "TAPE", "NOTE", "CIRCLED", "PROJECT") and v:
                    m[k.lower()] = v
            else:
                m["free"].append(line)
    # iXML (wins over bext)
    if info.ixml:
        x = info.ixml.decode("utf-8", "replace")
        for k in ("PROJECT", "SCENE", "TAKE", "TAPE", "NOTE", "CIRCLED", "WILD_TRACK"):
            v = _tag(x, k)
            if v:
                m[k.lower()] = v
        user = _tag(x, "USER")
        if user:
            m["free"].append(user)
        for t in re.findall(r"<TRACK>(.*?)</TRACK>", x, re.S | re.I):
            idx = _tag(t, "INTERLEAVE_INDEX") or _tag(t, "CHANNEL_INDEX")
            name = _tag(t, "NAME")
            if idx.isdigit() and 0 < int(idx) <= info.channels and name:
                m["tracks"][int(idx) - 1] = name
    # RIFF LIST INFO comments
    for k in ("ICMT", "INAM", "ISBJ"):
        if info.list_info.get(k):
            m["free"].append(info.list_info[k])
    text = " ".join([m.get("note", ""), m.get("scene", ""), m.get("take", ""), " ".join(m["free"]),
                     os.path.splitext(m["file"])[0]])
    m["wild"] = m.get("wild_track", "").upper() == "TRUE" or any(re.search(p, text) for p in cfg.WILD_PATTERNS)
    return m


def _tokens(name):
    return re.findall(r"[a-z]+", (name or "").lower())


def _match(tokens, words):
    return any(t == w or (len(w) > 3 and t.startswith(w)) for t in tokens for w in words)


def classify_track(name):
    """Track filter verdict for one track name: "keep", "drop" or "unknown" """
    tok = _tokens(name)
    if _match(tok, cfg.KEEP_TRACKS):
        return "keep"
    if _match(tok, cfg.IGNORE_TRACKS):
        return "drop"
    return "unknown"


def select_channels(tracks):
    """Indices of the tracks to use, following the track filter and UNKNOWN_TRACKS"""
    if not any(tracks):
        return list(range(len(tracks)))
    cls = [classify_track(n) for n in tracks]
    keep = [i for i, c in enumerate(cls) if c == "keep"]
    unknown = [i for i, c in enumerate(cls) if c == "unknown"]
    if cfg.UNKNOWN_TRACKS == "keep":
        return sorted(keep + unknown)
    if cfg.UNKNOWN_TRACKS == "drop":
        return keep
    return keep if keep else unknown


def new_bext(old, new_time_ref, desc, note):
    if old and len(old) >= 602:
        head = bytearray(old[:602])
        history = old[602:].rstrip(b"\0")
    else:
        head = bytearray(602)
        struct.pack_into("<H", head, 346, 1)
        history = b""
    if desc is not None:
        head[0:256] = _ascii(desc)[:255].ljust(256, b"\0")
    struct.pack_into("<Q", head, 338, new_time_ref)
    history += _ascii(note) + b"\r\n"
    return bytes(head) + history


def new_ixml(old, new_time_ref, kept, tracks, user):
    if old:
        txt = old.decode("utf-8", "replace")
    else:
        txt = '<?xml version="1.0" encoding="UTF-8"?>\n<BWFXML><IXML_VERSION>2.10</IXML_VERSION></BWFXML>'
    lo, hi = new_time_ref & 0xFFFFFFFF, new_time_ref >> 32
    txt = re.sub(r"(<TIMESTAMP_SAMPLES_SINCE_MIDNIGHT_LO>)\d+", rf"\g<1>{lo}", txt)
    txt = re.sub(r"(<TIMESTAMP_SAMPLES_SINCE_MIDNIGHT_HI>)\d+", rf"\g<1>{hi}", txt)
    # track list = the tracks that are written
    tl = "<TRACK_LIST><TRACK_COUNT>%d</TRACK_COUNT>" % len(kept)
    for n, i in enumerate(kept, 1):
        tl += ("<TRACK><CHANNEL_INDEX>%d</CHANNEL_INDEX><INTERLEAVE_INDEX>%d</INTERLEAVE_INDEX>"
               "<NAME>%s</NAME></TRACK>") % (n, n, escape(tracks[i] or f"Track {i + 1}"))
    tl += "</TRACK_LIST>"
    if re.search(r"<TRACK_LIST>.*?</TRACK_LIST>", txt, re.S):
        txt = re.sub(r"<TRACK_LIST>.*?</TRACK_LIST>", lambda _: tl, txt, count=1, flags=re.S)
    else:
        txt = txt.replace("</BWFXML>", tl + "</BWFXML>")
    if user is None:                     # DX Delete: keep the recorder's own USER block
        return txt.encode("utf-8")
    # UCS fields in the USER block (read by Soundminer and BaseHead)
    ub = "<USER>" + "".join(f"<{k}>{escape(v)}</{k}>" for k, v in user.items()) + "</USER>"
    txt = re.sub(r"<USER>.*?</USER>|<USER\s*/>", "", txt, flags=re.S)
    txt = txt.replace("</BWFXML>", ub + "</BWFXML>")
    return txt.encode("utf-8")


def list_info(items):
    body = b"INFO"
    for k, v in items.items():
        body += _chunk(k.encode("ascii"), v.encode("utf-8") + b"\0")
    return _chunk(b"LIST", body)
