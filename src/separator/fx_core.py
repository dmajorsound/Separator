"""
Separator – Kern
Entfernt Sprache aus Set-Ton-WAVs, erkennt Geraeusche, vergibt UCS-Dateinamen
und schreibt neue BWF-Files mit Regionen, Beschreibung und Original-Infos.
"""

import os
import re
import csv
import json
import time
import shutil
import struct
import subprocess
from math import gcd
from xml.sax.saxutils import escape

import numpy as np

# ============================================================
# EINSTELLUNGEN  (hier darfst du frei drehen)
# ============================================================

SPEECH_PAD_S   = 0.30   # so viel wird vor/nach jeder Sprachstelle zusaetzlich weggeschnitten
MIN_SEGMENT_S  = 1.5    # kuerzere Reststuecke zwischen Sprache werden verworfen
MIN_FILE_S     = 3.0    # kuerzere Ergebnis-Files werden nicht geschrieben (in der App einstellbar)
WILD_CONTINUOUS = True  # Nur-Ton/AT/Wildtrack-Takes am Stueck lassen, nur Ansagen/Dialog raus (in der App einstellbar)
# Woran ein Nur-Ton erkannt wird (Szene, Take, Notiz, Kommentare, iXML WILD_TRACK, Dateiname)
WILD_PATTERNS = [r"(?i)\bnur[\s_-]?ton\b", r"(?i)\bwild[\s_-]?tracks?\b", r"(?i)\bwild\b",
                 r"\bWT\d*\b", r"\bNT\d*\b", r"\bAT\d*\b", r"(?i)\batmo[\s_-]?take\b"]
GROUP_BY_CAT   = True   # True = pro Take ein Sausage-File je Kategorie (in der App einstellbar)
GAP_S          = 0.5    # Stille zwischen den Geraeuschen im Sausage-File, 0 = direkt aneinander
UNCLASSIFIED   = ("AMBMisc", "Unclassified")  # CatID + FXName fuer unsicher erkannte Geraeusche
OUTPUT_FORMAT  = "wav"  # "wav" oder "wavpack" (in der App einstellbar)
MAX_FILE_S     = 120.0  # laengere werden in Teile gesplittet, 0 = unbegrenzt (in der App einstellbar)
FADE_MS        = 10     # Fade-In/Out an jedem Schnitt
VAD_THRESHOLD  = 0.40   # Spracherkennung: niedriger = empfindlicher (schneidet mehr)
WIN_S          = 4.0    # Analysefenster fuer die Geraeuscherkennung
HOP_S          = 2.0    # Raster der Erkennung (Marker-Aufloesung)
MIN_PROB       = 0.30   # unter dieser Sicherheit wird ein Stueck als MISC markiert
SILENCE_DBFS   = -60.0  # leiser als das = Raumton, ohne KI-Analyse
WRITE_REGIONS  = True   # True = Regionen mit Laenge, False = nur Marker
SORT_BY_CATEGORY = False  # True = Zielordner CATEGORY/SUBCATEGORY nach UCS (in der App einstellbar)
SPLIT_TRACKS   = False  # True = jede verwendete Spur wird ein eigenes Mono-File (in der App einstellbar)
CLAP_MODEL     = "laion/larger_clap_general"

# UCS-Dateiname: CatID_FXName_CreatorID_SourceID_UserNotes.wav
CREATOR_ID     = "DMS"      # deine Kennung (in der App einstellbar)
SOURCE_ID      = "SET"      # wird durch den Projektnamen aus iXML/bext ersetzt, falls vorhanden

# Spuren: nur diese werden verwendet (Name aus iXML/bext, Gross/Klein egal)
KEEP_TRACKS    = ["boom", "bm", "atmo", "atmos", "amb", "ambience", "wild", "wt",
                  "plant", "pm", "plt"]
IGNORE_TRACKS  = ["lav", "iso", "mix", "mx", "radio", "rf", "tx", "wl"]
UNKNOWN_TRACKS = "fallback"  # Spuren ohne Treffer: "fallback" (nur wenn sonst keine), "keep", "drop"
# Ohne Spurnamen werden alle Spuren verwendet. Ein Verwenden-Treffer schlaegt einen
# Loeschen-Treffer: "Boom ISO" zaehlt als Boom. Alles in der App pro Projekt einstellbar.

# Kategorien: (UCS CatID, Category, SubCategory, Beschreibung fuer die KI auf Englisch)
# Mehrere Zeilen mit gleicher CatID sind erlaubt (= mehrere Beschreibungen).
LABELS = [
    ("SPEECH",    "",               "",          "a person speaking"),
    ("SPEECH",    "",               "",          "people talking in a conversation"),
    ("SPEECH",    "",               "",          "a man talking"),
    ("SPEECH",    "",               "",          "a woman talking"),
    ("SPEECH",    "",               "",          "someone shouting words"),
    ("AMBRoom",   "AMBIENCE",       "ROOM TONE", "quiet indoor room tone"),
    ("AMBUrbn",   "AMBIENCE",       "URBAN",     "outdoor ambience with distant city noise"),
    ("AMBTraf",   "AMBIENCE",       "TRAFFIC",   "city traffic noise"),
    ("VEHBy",     "VEHICLES",       "BY",        "a car driving by"),
    ("VEHCar",    "VEHICLES",       "CAR",       "a car engine idling or starting"),
    ("VEHDoor",   "VEHICLES",       "DOOR",      "a car door opening or slamming"),
    ("DOORMisc",  "DOORS",          "MISC",      "a door opening or closing"),
    ("DOORKnck",  "DOORS",          "KNOCK",     "knocking on a door"),
    ("FEETHmn",   "FOOTSTEPS",      "HUMAN",     "footsteps walking"),
    ("CLOTHMvmt", "CLOTH",          "MOVEMENT",  "clothes rustling and body movement"),
    ("TOOLHand",  "TOOLS",          "HAND",      "hammering and hand tools"),
    ("FGHTImpt",  "FIGHT",          "IMPACT",    "punches and fighting, bodies hitting"),
    ("WATRSplsh", "WATER",          "SPLASH",    "water splashing, someone swimming"),
    ("WATRFlow",  "WATER",          "FLOW",      "water running from a tap"),
    ("HMNBrth",   "HUMAN",          "BREATH",    "heavy breathing"),
    ("HMNSnor",   "HUMAN",          "SNORE",     "someone sleeping and snoring"),
    ("FOODTware", "FOOD & DRINK",   "TABLEWARE", "dishes and cups clattering"),
    ("OBJMisc",   "OBJECTS",        "MISC",      "objects being handled and put down"),
    ("PAPRHndl",  "PAPER",          "HANDLE",    "paper rustling"),
    ("GLASBrk",   "GLASS",          "BREAK",     "glass breaking"),
    ("COMTelph",  "COMMUNICATIONS", "TELEPHONE", "a telephone ringing"),
    ("BIRDMisc",  "BIRDS",          "MISC",      "birds chirping"),
    ("ANMLDog",   "ANIMALS",        "DOG",       "a dog barking"),
    ("WIND",      "WIND",           "GENERAL",   "wind blowing"),
    ("RAIN",      "RAIN",           "GENERAL",   "rain falling"),
    ("MUSCMisc",  "MUSICAL",        "MISC",      "music playing"),
]
SILENCE_CAT  = "AMBRoom"     # sehr leise Stellen
UNKNOWN_CAT  = "MISC"        # Marker fuer unsichere Erkennung (nicht im Dateinamen)
FALLBACK_CAT = ("AMBMisc", "AMBIENCE", "MISC")
CUT_LABELS   = {"SPEECH"}    # alles mit diesen Namen wird zusaetzlich rausgeschnitten

UCS = {c: (cat, sub) for c, cat, sub, _ in LABELS if cat}
UCS[FALLBACK_CAT[0]] = FALLBACK_CAT[1:]

# Deutsche Begriffe: kommen in Marker, Beschreibung und Keywords, nie in den Dateinamen
DE_NAMES = {
    "AMBRoom": "Raumton", "AMBUrbn": "Stadt-Atmo", "AMBTraf": "Verkehr", "AMBMisc": "Atmo",
    "VEHBy": "Auto-Vorbeifahrt", "VEHCar": "Automotor", "VEHDoor": "Autotür",
    "DOORMisc": "Tür", "DOORKnck": "Klopfen", "FEETHmn": "Schritte",
    "CLOTHMvmt": "Kleidung/Bewegung", "TOOLHand": "Hämmern/Werkzeug", "FGHTImpt": "Kampf/Schläge",
    "WATRSplsh": "Wasser/Schwimmen", "WATRFlow": "Wasser fließend", "HMNBrth": "Atmen",
    "HMNSnor": "Schnarchen/Schlafen", "FOODTware": "Geschirr", "OBJMisc": "Gegenstände",
    "PAPRHndl": "Papier", "GLASBrk": "Glasbruch", "COMTelph": "Telefon", "BIRDMisc": "Vögel",
    "ANMLDog": "Hund", "WIND": "Wind", "RAIN": "Regen", "MUSCMisc": "Musik", "MISC": "unklar",
}

# ============================================================
# WAV / BWF lesen
# ============================================================


class WavInfo:
    pass


def read_wav_info(path):
    size = os.path.getsize(path)
    info = WavInfo()
    info.fmt_bytes = info.bext = info.ixml = None
    info.list_info = {}
    info.data_off = None
    with open(path, "rb") as f:
        riff, _, wave = struct.unpack("<4sI4s", f.read(12))
        if riff not in (b"RIFF", b"RF64", b"BW64") or wave != b"WAVE":
            raise ValueError("kein WAV")
        ds64_data = None
        pos = 12
        while pos + 8 <= size:
            f.seek(pos)
            cid, csz = struct.unpack("<4sI", f.read(8))
            if cid == b"ds64":
                ds64_data = struct.unpack("<QQQ", f.read(csz)[:24])[1]
            elif cid == b"data":
                if csz == 0xFFFFFFFF and ds64_data is not None:
                    csz = ds64_data
                csz = min(csz, size - pos - 8)  # abgebrochene Aufnahmen
                info.data_off, info.data_size = pos + 8, csz
            elif cid == b"fmt ":
                info.fmt_bytes = f.read(csz)
            elif cid == b"bext":
                info.bext = f.read(csz)
            elif cid == b"iXML":
                info.ixml = f.read(csz)
            elif cid == b"LIST" and csz < 1_000_000:
                body = f.read(csz)
                if body[:4] == b"INFO":
                    p = 4
                    while p + 8 <= len(body):
                        k, n = struct.unpack("<4sI", body[p:p + 8])
                        info.list_info[k.decode("latin-1")] = \
                            body[p + 8:p + 8 + n].rstrip(b"\0").decode("utf-8", "replace")
                        p += 8 + n + (n & 1)
            pos += 8 + csz + (csz & 1)

    if info.fmt_bytes is None or info.data_off is None:
        raise ValueError("fmt- oder data-Chunk fehlt")
    code, ch, sr, _, block, bits = struct.unpack("<HHIIHH", info.fmt_bytes[:16])
    if code == 0xFFFE and len(info.fmt_bytes) >= 26:
        code = struct.unpack("<H", info.fmt_bytes[24:26])[0]
    if code not in (1, 3):
        raise ValueError(f"Format {code} wird nicht unterstuetzt")
    info.is_float = code == 3
    info.channels, info.sr, info.block, info.bits = ch, sr, block, bits
    info.bps = block // ch
    info.n_frames = info.data_size // block
    info.time_ref = 0
    if info.bext and len(info.bext) >= 346:
        info.time_ref = struct.unpack("<Q", info.bext[338:346])[0]
    return info


def decode(b, bps, channels, is_float):
    """uint8-Bytes (ganze Frames) -> float64 (frames, channels)"""
    b = np.ascontiguousarray(b)
    if is_float:
        a = b.view("<f4" if bps == 4 else "<f8").astype(np.float64)
    elif bps == 2:
        a = b.view("<i2").astype(np.float64) / 32768.0
    elif bps == 3:
        x = b.reshape(-1, 3).astype(np.int32)
        v = x[:, 0] | (x[:, 1] << 8) | (x[:, 2] << 16)
        v = np.where(v & 0x800000, v - 0x1000000, v)
        a = v.astype(np.float64) / 8388608.0
    elif bps == 4:
        a = b.view("<i4").astype(np.float64) / 2147483648.0
    elif bps == 1:
        a = (b.astype(np.float64) - 128.0) / 128.0
    else:
        raise ValueError("Bittiefe nicht unterstuetzt")
    return a.reshape(-1, channels)


def encode(a, bps, is_float):
    """float64 -> uint8-Bytes im Originalformat"""
    a = a.reshape(-1)
    if is_float:
        return a.astype("<f4" if bps == 4 else "<f8").view(np.uint8)
    if bps == 2:
        return np.clip(np.round(a * 32768), -32768, 32767).astype("<i2").view(np.uint8)
    if bps == 3:
        v = np.clip(np.round(a * 8388608), -8388608, 8388607).astype(np.int32)
        out = np.empty((v.size, 3), np.uint8)
        out[:, 0] = v & 0xFF
        out[:, 1] = (v >> 8) & 0xFF
        out[:, 2] = (v >> 16) & 0xFF
        return out.reshape(-1)
    if bps == 4:
        return np.clip(np.round(a * 2147483648), -2147483648, 2147483647).astype("<i4").view(np.uint8)
    if bps == 1:
        return np.clip(np.round(a * 128 + 128), 0, 255).astype(np.uint8)
    raise ValueError("Bittiefe nicht unterstuetzt")


def frames_bytes(raw, info, f0, f1, chans):
    """Rohbytes der Frames f0..f1, nur die Spuren in chans (bit-genau)"""
    b = raw[f0 * info.block:f1 * info.block]
    if len(chans) == info.channels:
        return np.asarray(b)
    return np.asarray(b).reshape(-1, info.channels, info.bps)[:, chans, :].reshape(-1)


def mono(raw, info, f0, f1, target_sr, chans):
    from scipy.signal import resample_poly
    x = decode(frames_bytes(raw, info, f0, f1, chans), info.bps, len(chans), info.is_float).mean(axis=1)
    if target_sr != info.sr:
        g = gcd(target_sr, info.sr)
        x = resample_poly(x, target_sr // g, info.sr // g)
    return x.astype(np.float32)


def mono_full(raw, info, target_sr, chans, chunk_s=60):
    parts, step = [], int(chunk_s * info.sr)
    for f0 in range(0, info.n_frames, step):
        parts.append(mono(raw, info, f0, min(f0 + step, info.n_frames), target_sr, chans))
    return np.concatenate(parts) if parts else np.zeros(0, np.float32)

# ============================================================
# Metadaten: Original-Infos lesen, Spuren auswaehlen
# ============================================================


def _tag(xml, name):
    m = re.search(rf"<{name}>(.*?)</{name}>", xml, re.S | re.I)
    return re.sub(r"<[^>]+>", " ", m.group(1)).strip() if m else ""


def read_meta(info, path):
    m = {"file": os.path.basename(path), "tracks": [None] * info.channels, "free": []}
    # bext
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
    # iXML (hat Vorrang)
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
    # LIST INFO
    for k in ("ICMT", "INAM", "ISBJ"):
        if info.list_info.get(k):
            m["free"].append(info.list_info[k])
    text = " ".join([m.get("note", ""), m.get("scene", ""), m.get("take", ""), " ".join(m["free"]),
                     os.path.splitext(m["file"])[0]])
    m["wild"] = m.get("wild_track", "").upper() == "TRUE" or any(re.search(p, text) for p in WILD_PATTERNS)
    return m


def _tokens(name):
    return re.findall(r"[a-z]+", (name or "").lower())


def _match(tokens, words):
    return any(t == w or (len(w) > 3 and t.startswith(w)) for t in tokens for w in words)


def classify_track(name):
    """-> "keep", "drop" oder "unknown" """
    tok = _tokens(name)
    if _match(tok, KEEP_TRACKS):
        return "keep"
    if _match(tok, IGNORE_TRACKS):
        return "drop"
    return "unknown"


def select_channels(tracks):
    """-> Liste der Spur-Indizes, die verwendet werden"""
    if not any(tracks):
        return list(range(len(tracks)))
    cls = [classify_track(n) for n in tracks]
    keep = [i for i, c in enumerate(cls) if c == "keep"]
    unknown = [i for i, c in enumerate(cls) if c == "unknown"]
    if UNKNOWN_TRACKS == "keep":
        return sorted(keep + unknown)
    if UNKNOWN_TRACKS == "drop":
        return keep
    return keep if keep else unknown


def scan_tracks(src, limit=0):
    """Liest nur die Header: welche Spurnamen kommen wie oft vor"""
    counts, files = {}, find_files(src, os.path.join(src, "\0"))
    for p in files[:limit or None]:
        try:
            info = read_wav_info(p)
            names = read_meta(info, p)["tracks"]
        except Exception:
            continue
        if not any(names):
            names = ["(ohne Namen)"]
        for n in names:
            n = (n or "(leer)").strip()
            counts[n] = counts.get(n, 0) + 1
    return len(files), sorted(counts.items(), key=lambda kv: -kv[1])

# ============================================================
# UCS-Name und Beschreibung
# ============================================================


def _clean(s, maxlen=40):
    s = _ascii(str(s)).decode("ascii")
    s = re.sub(r"[^A-Za-z0-9 .()&+-]", "", s.replace("_", "-"))
    s = re.sub(r"\s+", " ", s).strip(" .-")
    if len(s) > maxlen:
        cut = s[:maxlen + 1].rsplit(" ", 1)[0] if " " in s[:maxlen + 1] else s[:maxlen]
        s = cut
    return s[:maxlen].strip(" .-")


def _readable(catid):
    cat, sub = UCS.get(catid, ("", ""))
    if cat == "AMBIENCE":
        return sub.title()
    return cat.title() if sub in ("MISC", "GENERAL", "HUMAN", "HAND", "IMPACT", "") else \
        f"{cat.title()} {sub.title()}"


def ranking(runs):
    """CatIDs nach Gewicht: Einzelgeraeusche zaehlen 3x so viel wie Atmos"""
    w = {}
    for r in runs:
        for s, e, l, _ in r["events"]:
            if l in UCS:
                w[l] = w.get(l, 0) + (e - s) * (1 if l.startswith("AMB") else 3)
    return [k for k, _ in sorted(w.items(), key=lambda kv: -kv[1])]


def ucs_name(rank, meta, creator, part=""):
    catid = rank[0] if rank else FALLBACK_CAT[0]
    names = []
    for c in rank:
        n = _readable(c)
        if n and n not in names:
            names.append(n)
        if len(names) == 3:
            break
    fx = ("Wild Track " if meta["wild"] else "") + (" ".join(names) or "Set Ambience")
    source = _clean(meta.get("project") or SOURCE_ID, 24).replace(" ", "")
    if meta.get("scene") or meta.get("take"):
        notes = "-".join(p for p in (f"Sc{meta.get('scene', '')}" if meta.get("scene") else "",
                                     f"Tk{meta.get('take', '')}" if meta.get("take") else "") if p)
    else:
        notes = os.path.splitext(meta["file"])[0]
    notes = _clean(notes, 30).replace(" ", "") + part
    return catid, f"{catid}_{_clean(fx)}_{_clean(creator, 12) or 'NN'}_{source}_{notes}", _clean(fx)


def description(rank, meta, kept_names, info):
    parts = ["Set recording, dialogue removed."]
    if rank:
        parts.append("Contains: " + ", ".join(_readable(c) for c in rank) + ".")
        parts.append("Inhalt: " + ", ".join(DE_NAMES.get(c, c) for c in rank) + ".")
    info_bits = []
    for k, lab in (("project", "Project"), ("scene", "Scene"), ("take", "Take"), ("tape", "Roll")):
        if meta.get(k):
            info_bits.append(f"{lab} {meta[k]}")
    if meta.get("circled", "").upper() == "TRUE":
        info_bits.append("Circled")
    if meta["wild"]:
        info_bits.append("Wild track")
    if meta.get("note"):
        info_bits.append(f"Note: {meta['note']}")
    for f in meta["free"]:
        if f and f not in info_bits:
            info_bits.append(f)
    if kept_names:
        info_bits.append("Tracks: " + ", ".join(kept_names))
    if meta.get("date"):
        info_bits.append(f"Rec {meta['date']} {meta.get('time', '')}".strip())
    info_bits.append(f"Orig TC {tc(info.time_ref / info.sr)}")
    if meta.get("originator"):
        info_bits.append(f"Recorder {meta['originator']}")
    info_bits.append(f"Original file {meta['file']}")
    return " ".join(parts) + " " + " | ".join(info_bits)


def _ascii(s):
    for a, b in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("Ä", "Ae"), ("Ö", "Oe"),
                 ("Ü", "Ue"), ("ß", "ss")):
        s = s.replace(a, b)
    import unicodedata
    s = unicodedata.normalize("NFKD", s)
    return s.encode("ascii", "ignore")

# ============================================================
# BWF schreiben
# ============================================================


def _chunk(cid, body):
    return struct.pack("<4sI", cid, len(body)) + body + (b"\0" if len(body) & 1 else b"")


def new_bext(old, new_time_ref, desc, note):
    if old and len(old) >= 602:
        head = bytearray(old[:602])
        history = old[602:].rstrip(b"\0")
    else:
        head = bytearray(602)
        struct.pack_into("<H", head, 346, 1)
        history = b""
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
    # Spurliste an die behaltenen Spuren anpassen
    tl = "<TRACK_LIST><TRACK_COUNT>%d</TRACK_COUNT>" % len(kept)
    for n, i in enumerate(kept, 1):
        tl += ("<TRACK><CHANNEL_INDEX>%d</CHANNEL_INDEX><INTERLEAVE_INDEX>%d</INTERLEAVE_INDEX>"
               "<NAME>%s</NAME></TRACK>") % (n, n, escape(tracks[i] or f"Track {i + 1}"))
    tl += "</TRACK_LIST>"
    if re.search(r"<TRACK_LIST>.*?</TRACK_LIST>", txt, re.S):
        txt = re.sub(r"<TRACK_LIST>.*?</TRACK_LIST>", lambda _: tl, txt, count=1, flags=re.S)
    else:
        txt = txt.replace("</BWFXML>", tl + "</BWFXML>")
    # UCS-Felder im USER-Block
    ub = "<USER>" + "".join(f"<{k}>{escape(v)}</{k}>" for k, v in user.items()) + "</USER>"
    txt = re.sub(r"<USER>.*?</USER>|<USER\s*/>", "", txt, flags=re.S)
    txt = txt.replace("</BWFXML>", ub + "</BWFXML>")
    return txt.encode("utf-8")


def list_info(items):
    body = b"INFO"
    for k, v in items.items():
        body += _chunk(k.encode("ascii"), v.encode("utf-8") + b"\0")
    return _chunk(b"LIST", body)


def marker_chunks(markers):
    """markers: Liste von (position_frames, laenge_frames, text)"""
    if not markers:
        return b""
    cue = struct.pack("<I", len(markers))
    adtl = b"adtl"
    for i, (pos, length, text) in enumerate(markers, start=1):
        cue += struct.pack("<I I 4s I I I", i, pos, b"data", 0, 0, pos)
        adtl += _chunk(b"labl", struct.pack("<I", i) + text.encode("utf-8") + b"\0")
        if WRITE_REGIONS and length > 0:
            adtl += _chunk(b"ltxt", struct.pack("<II4sHHHH", i, length, b"rgn ", 0, 0, 0, 0))
    return _chunk(b"cue ", cue) + _chunk(b"LIST", adtl)


def write_bwf(path, info, chans, runs, raw, head_extra, markers, gap_frames=0):
    nch = len(chans)
    block = info.bps * nch
    fmt = struct.pack("<HHIIHH", 3 if info.is_float else 1, nch, info.sr,
                      info.sr * block, block, info.bits)
    fade = max(1, int(FADE_MS / 1000 * info.sr))
    data_size = (sum(e - s for s, e in runs) + gap_frames * max(0, len(runs) - 1)) * block
    silence = bytes([128 if (info.bps == 1 and not info.is_float) else 0]) * (gap_frames * block)
    head = _chunk(b"fmt ", fmt) + head_extra
    tail = marker_chunks(markers)
    pad = data_size & 1
    body_size = 4 + len(head) + 8 + data_size + pad + len(tail)

    rf64 = body_size + 36 > 0xFFFFFFFF
    tmp = path + ".part"
    with open(tmp, "wb") as f:
        if rf64:
            body_size += 36
            f.write(struct.pack("<4sI4s", b"RF64", 0xFFFFFFFF, b"WAVE"))
            f.write(_chunk(b"ds64", struct.pack("<QQQI", body_size, data_size, data_size // block, 0)))
            f.write(head)
            f.write(struct.pack("<4sI", b"data", 0xFFFFFFFF))
        else:
            f.write(struct.pack("<4sI4s", b"RIFF", body_size, b"WAVE"))
            f.write(head)
            f.write(struct.pack("<4sI", b"data", data_size))

        for ri, (s, e) in enumerate(runs):
            if ri and gap_frames:
                f.write(silence)
            fl = min(fade, (e - s) // 2)
            step = int(info.sr * 30)
            for a in range(s, e, step):
                b = min(a + step, e)
                chunk = frames_bytes(raw, info, a, b, chans)
                if fl and (a < s + fl or b > e - fl):
                    x = decode(chunk, info.bps, nch, info.is_float)
                    idx = np.arange(a, b)
                    g = np.ones(b - a)
                    m = idx < s + fl
                    g[m] = np.sin(0.5 * np.pi * (idx[m] - s + 0.5) / fl)
                    m = idx >= e - fl
                    g[m] = np.minimum(g[m], np.sin(0.5 * np.pi * (e - idx[m] - 0.5) / fl))
                    chunk = encode(x * g[:, None], info.bps, info.is_float)
                f.write(np.ascontiguousarray(chunk).tobytes())
        if pad:
            f.write(b"\0")
        f.write(tail)
    os.replace(tmp, path)

# ============================================================
# Analyse (KI)
# ============================================================


class Analyzer:
    def __init__(self, log=print):
        import torch
        self.torch = torch
        torch.set_num_threads(os.cpu_count() or 4)
        log("Lade Spracherkennung (Silero VAD) …")
        from silero_vad import load_silero_vad, get_speech_timestamps
        self.vad_model = load_silero_vad()
        self._gst = get_speech_timestamps

        log(f"Lade Geräuscherkennung ({CLAP_MODEL}) …")
        from transformers import ClapModel, ClapProcessor
        self.model = ClapModel.from_pretrained(CLAP_MODEL).eval()
        self.proc = ClapProcessor.from_pretrained(CLAP_MODEL)

        self.names = sorted({c for c, *_ in LABELS})
        self.prompt_idx = [self.names.index(c) for c, *_ in LABELS]
        tok = self.proc.tokenizer([p for *_, p in LABELS], padding=True, return_tensors="pt")
        with torch.no_grad():
            t = self._emb(self.model.get_text_features(**tok))
        self.text = torch.nn.functional.normalize(t, dim=-1)
        scale = getattr(self.model, "logit_scale_a", None)
        self.scale = float(scale.exp()) if scale is not None else 100.0
        self.device = "mps" if torch.backends.mps.is_available() else "cpu"
        self.model.to(self.device)
        self.text = self.text.to(self.device)
        self.log = log
        log(f"Bereit (Rechner: {'Apple-Grafikchip' if self.device == 'mps' else 'CPU'}).")

    @staticmethod
    def _emb(x):
        if hasattr(x, "pooler_output") and x.pooler_output is not None:
            return x.pooler_output
        if not hasattr(x, "shape"):
            return x[0]
        return x

    def vad(self, mono16):
        if mono16.size == 0:
            return []
        peak = float(np.max(np.abs(mono16))) or 1.0
        wav = self.torch.from_numpy(mono16 / peak * 0.9)
        ts = self._gst(wav, self.vad_model, sampling_rate=16000, threshold=VAD_THRESHOLD)
        return [(t["start"] / 16000, t["end"] / 16000) for t in ts]

    def text_embed(self, prompts):
        """Normalisierte Text-Embeddings (numpy) fuer feinere Beschreibungen"""
        torch = self.torch
        tok = self.proc.tokenizer(list(prompts), padding=True, return_tensors="pt")
        with torch.no_grad():
            t = self._emb(self.model.to("cpu").get_text_features(**tok))
        self.model.to(self.device)
        return torch.nn.functional.normalize(t, dim=-1).numpy().astype(np.float32)

    def classify(self, windows):
        torch = self.torch
        out = [None] * len(windows)
        self.last_emb = [None] * len(windows)
        todo = []
        for i, w in enumerate(windows):
            peak = float(np.max(np.abs(w))) if w.size else 0.0
            if peak <= 10 ** (SILENCE_DBFS / 20):
                out[i] = (SILENCE_CAT, 1.0)
            else:
                todo.append((i, w / peak * 0.7))
        for b in range(0, len(todo), 16):
            batch = todo[b:b + 16]
            feats = self.proc.feature_extractor([w for _, w in batch], sampling_rate=48000,
                                                return_tensors="pt")
            try:
                a = self._audio(feats)
            except Exception:
                if self.device == "cpu":
                    raise
                self.log("  (Grafikchip nicht kompatibel, rechne auf CPU weiter)")
                self.device = "cpu"
                self.model.to("cpu")
                self.text = self.text.cpu()
                a = self._audio(feats)
            for (i, _), row in zip(batch, a.detach().cpu().numpy()):
                self.last_emb[i] = row.astype(np.float32)
            logits = self.scale * a @ self.text.T
            per = torch.full((len(batch), len(self.names)), -1e9, device=logits.device)
            for p, n in enumerate(self.prompt_idx):
                per[:, n] = torch.maximum(per[:, n], logits[:, p])
            prob = per.softmax(dim=-1).cpu().numpy()
            for (i, _), pr in zip(batch, prob):
                k = int(pr.argmax())
                out[i] = (self.names[k] if pr[k] >= MIN_PROB else UNKNOWN_CAT, float(pr[k]))
        return out

    def _audio(self, feats):
        with self.torch.no_grad():
            kw = {k: v.to(self.device) for k, v in feats.items() if hasattr(v, "to")}
            a = self._emb(self.model.get_audio_features(**kw))
        return self.torch.nn.functional.normalize(a, dim=-1)

# ============================================================
# Ablauf pro File
# ============================================================


def merge(iv):
    out = []
    for s, e in sorted(iv):
        if out and s <= out[-1][1]:
            out[-1][1] = max(out[-1][1], e)
        else:
            out.append([s, e])
    return [tuple(x) for x in out]


def invert(iv, dur):
    out, t = [], 0.0
    for s, e in iv:
        if s > t:
            out.append((t, s))
        t = max(t, e)
    if t < dur:
        out.append((t, dur))
    return out


def tc(sec):
    sec = sec % 86400
    h, r = divmod(sec, 3600)
    m, s = divmod(r, 60)
    return f"{int(h):02d}:{int(m):02d}:{s:06.3f}"


def analyse(info, raw, an, chans):
    dur = info.n_frames / info.sr
    speech = an.vad(mono_full(raw, info, 16000, chans))
    cut = merge([(max(0, s - SPEECH_PAD_S), min(dur, e + SPEECH_PAD_S)) for s, e in speech])
    keep = [k for k in invert(cut, dur) if k[1] - k[0] >= MIN_SEGMENT_S]

    owned, wins = [], []
    for s, e in keep:
        t = s
        while t < e - 1e-6:
            oe = t + HOP_S
            if e - oe < HOP_S * 0.5:
                oe = e
            ws, we = t, min(t + WIN_S, e)
            if we - ws < WIN_S:
                ws = max(s, we - WIN_S)
            owned.append((t, oe))
            wins.append((ws, we))
            t = oe
    audio = [mono(raw, info, int(ws * info.sr), int(we * info.sr), 48000, chans) for ws, we in wins]
    labels = an.classify(audio) if audio else []
    pieces = [(s, e, l, p) for (s, e), (l, p) in zip(owned, labels)]
    embs = getattr(an, "last_emb", None) or [None] * len(owned)
    info.piece_embs = [(s, e, em) for (s, e), em in zip(owned, embs)]

    runs = []
    for s, e, l, p in pieces:
        if l in CUT_LABELS:
            continue
        if runs and abs(runs[-1]["end"] - s) < 1e-6:
            runs[-1]["end"] = e
            runs[-1]["pieces"].append((s, e, l, p))
        else:
            runs.append({"start": s, "end": e, "pieces": [(s, e, l, p)]})
    runs = [r for r in runs if r["end"] - r["start"] >= MIN_SEGMENT_S]

    for r in runs:
        ev = []
        for s, e, l, p in r["pieces"]:
            if ev and ev[-1][2] == l:
                ev[-1][1] = e
                ev[-1][3].append(p)
            else:
                ev.append([s, e, l, [p]])
        r["events"] = [(s, e, l, sum(ps) / len(ps)) for s, e, l, ps in ev]
    return runs, dur


def peaks(raw, info, bins=900, max_ch=8):
    """Wellenform-Huellkurve pro Spur (0..1) fuer die Anzeige"""
    n = max(1, info.n_frames)
    ch = min(info.channels, max_ch)
    out = np.zeros((ch, bins), np.float32)
    step = int(info.sr * 30)
    for f0 in range(0, n, step):
        f1 = min(n, f0 + step)
        x = np.abs(decode(raw[f0 * info.block:f1 * info.block], info.bps, info.channels, info.is_float)[:, :ch])
        b = (np.arange(f0, f1, dtype=np.int64) * bins) // n
        starts = np.flatnonzero(np.r_[True, b[1:] != b[:-1]])
        red = np.maximum.reduceat(x, starts, axis=0)
        idx = b[starts]
        out[:, idx] = np.maximum(out[:, idx], red.T)
    m = float(out.max()) or 1.0
    return [[round(float(v), 3) for v in row] for row in (out / m) ** 0.85]


def file_view(path, info, meta, raw, parts=None, rel=""):
    """Alles, was die Spur-Ansicht fuer ein File braucht"""
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


def preview(path, an=None, rel=""):
    """Vorschau: Spuren + Wellenform; mit Analyzer auch Schnitt und FX (ohne zu schreiben)"""
    info = read_wav_info(path)
    meta = read_meta(info, path)
    raw = np.memmap(path, dtype=np.uint8, mode="r", offset=info.data_off,
                    shape=(info.n_frames * info.block,))
    parts = None
    chans = select_channels(meta["tracks"])
    if an is not None and chans:
        runs, _ = analyse(info, raw, an, chans)
        parts = split_parts(runs, meta)
    return file_view(path, info, meta, raw, parts, rel)


def file_summary(path, src):
    """Schnelle Kopfdaten fuer die Liste (ohne Audio zu lesen)"""
    info = read_wav_info(path)
    meta = read_meta(info, path)
    return {"rel": os.path.relpath(path, src), "dur": round(info.n_frames / info.sr, 2), "sr": info.sr,
            "bits": info.bits, "ch": info.channels, "scene": meta.get("scene", ""),
            "take": meta.get("take", ""), "wild": meta["wild"],
            "tracks": [t or "" for t in meta["tracks"]]}


def wavpack_bin():
    import sys
    bundled = os.path.join(getattr(sys, "_MEIPASS", ""), "bin", "wavpack")
    for p in (bundled, shutil.which("wavpack"), "/opt/homebrew/bin/wavpack", "/usr/local/bin/wavpack"):
        if p and os.path.exists(p):
            return p
    return None


def to_wavpack(wav_path, tags):
    """WAV -> .wv (verlustfrei, alle RIFF-Chunks bleiben erhalten), WAV wird danach geloescht"""
    wv = os.path.splitext(wav_path)[0] + ".wv"
    cmd = [wavpack_bin(), "-q", "-y", "-m"]
    for k, v in tags.items():
        cmd += ["-w", f"{k}={v[:1000]}"]
    cmd += [wav_path, "-o", wv]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0 or not os.path.exists(wv):
        raise RuntimeError("WavPack fehlgeschlagen: " + (r.stderr or "").strip()[-200:])
    os.remove(wav_path)
    return wv


def part_mode(meta):
    """(nach Kategorie trennen?, Pause) – Nur-Ton bleibt am Stueck"""
    if WILD_CONTINUOUS and meta.get("wild"):
        return False, 0.0
    return GROUP_BY_CAT, GAP_S


def build_parts(runs, group=None, gap=None):
    """Runs -> Teile (je ein File): {"catid", "segs": [{"start","end","events","cut"}]}.
    GROUP_BY_CAT: jede Kategorie eigenes File, jedes Geraeusch eigenes Stueck mit Pause dazwischen."""
    group = GROUP_BY_CAT if group is None else group
    gap = GAP_S if gap is None else gap
    mx = MAX_FILE_S or 1e12
    segs = []
    for r in runs:
        first = True
        if group:
            for s, e, l, p in r["events"]:
                segs.append({"start": s, "end": e, "events": [(s, e, l, p)], "cut": first, "cat": l})
                first = False
        else:
            segs.append({"start": r["start"], "end": r["end"], "events": list(r["events"]), "cut": True, "cat": "*"})
    # zu lange Stuecke an Event-Grenzen bzw. hart teilen
    chopped = []
    for sg in segs:
        cur = None
        for s, e, l, p in sg["events"]:
            while e - s > 1e-6:
                ce = min(e, s + mx)
                if cur is None or (ce - cur["start"]) > mx:
                    if cur:
                        chopped.append(cur)
                    cur = {"start": s, "end": s, "events": [], "cut": sg["cut"] if cur is None else False,
                           "cat": sg["cat"]}
                cur["events"].append((s, ce, l, p))
                cur["end"] = ce
                s = ce
        if cur:
            chopped.append(cur)
    # nach Kategorie sammeln (Reihenfolge des ersten Auftretens) und zu Files packen
    order, groups = [], {}
    for sg in chopped:
        if sg["cat"] not in groups:
            order.append(sg["cat"])
            groups[sg["cat"]] = []
        groups[sg["cat"]].append(sg)
    parts = []
    for cat in order:
        cur, cur_len = [], 0.0
        for sg in groups[cat]:
            n = sg["end"] - sg["start"]
            add = n + (gap if cur else 0)
            if cur and cur_len + add > mx:
                parts.append({"cat": cat, "segs": cur})
                cur, cur_len, add = [], 0.0, n
            cur.append(sg)
            cur_len += add
        if cur:
            parts.append({"cat": cat, "segs": cur})
    out = []
    for pt in parts:
        length = sum(sg["end"] - sg["start"] for sg in pt["segs"]) + gap * (len(pt["segs"]) - 1)
        if length >= MIN_FILE_S:
            out.append(pt)
    return out


def split_parts(runs, meta=None):
    return build_parts(runs, *part_mode(meta or {}))


def process(path, dst, rel_dir, an, dry, log, rel, creator, taken, view=None):
    info = read_wav_info(path)
    meta = read_meta(info, path)
    chans = select_channels(meta["tracks"])
    if not chans:
        if view:
            raw0 = np.memmap(path, dtype=np.uint8, mode="r", offset=info.data_off,
                             shape=(info.n_frames * info.block,))
            view(file_view(path, info, meta, raw0, [], rel))
        return "notrack", [], "keine passende Spur"
    kept_names = [meta["tracks"][i] or f"Track {i + 1}" for i in chans] if any(meta["tracks"]) else []

    raw = np.memmap(path, dtype=np.uint8, mode="r", offset=info.data_off,
                    shape=(info.n_frames * info.block,))
    runs, dur = analyse(info, raw, an, chans)
    grp_mode, gap_s = part_mode(meta)
    parts = build_parts(runs, grp_mode, gap_s)
    if view:
        view(file_view(path, info, meta, raw, parts, rel))
    if not parts:
        return "dialog", [], "nur Dialog oder zu kurz"

    sr = info.sr
    written = []
    groups = [[c] for c in chans] if (SPLIT_TRACKS and len(chans) > 1) else [chans]
    for grp in groups:
      grp_names = [meta["tracks"][i] or f"Track {i + 1}" for i in grp] if any(meta["tracks"]) else []
      trk_suffix = ("-" + _clean(grp_names[0] if grp_names else f"Tr{grp[0] + 1}", 16).replace(" ", "")) \
          if len(groups) > 1 else ""
      per_cat = {}
      for pt in parts:
          per_cat[pt["cat"]] = per_cat.get(pt["cat"], 0) + 1
      seen = {}
      for k, ptd in enumerate(parts, 1):
          part = ptd["segs"]
          seen[ptd["cat"]] = seen.get(ptd["cat"], 0) + 1
          rank = ranking(part)
          unclassified = grp_mode and ptd["cat"] == UNKNOWN_CAT
          if unclassified:
              rank = [UNCLASSIFIED[0]]
          suffix = trk_suffix + (f"-P{seen[ptd['cat']]}" if per_cat[ptd["cat"]] > 1 else "")
          catid, base, fxname = ucs_name(rank, meta, creator, suffix)
          if unclassified:
              base = base.replace(f"{catid}_{fxname}_", f"{catid}_{UNCLASSIFIED[1]}_", 1)
              fxname = UNCLASSIFIED[1]
          n_reg = sum(len(sg["events"]) for sg in part)
          if grp_mode and n_reg > 1:
              base = base.replace(f"_{fxname}_", f"_{fxname} x{n_reg}_", 1)
          cat, sub = UCS.get(catid, FALLBACK_CAT[1:])
          out_dir = os.path.join(dst, _clean(cat, 40), _clean(sub, 40)) if SORT_BY_CATEGORY \
            else os.path.join(dst, rel_dir)
          name, n = base, 1
          ext = ".wv" if OUTPUT_FORMAT == "wavpack" else ".wav"
          while (os.path.join(out_dir, name + ext).lower() in taken
                 or os.path.exists(os.path.join(out_dir, name + ext))):
              n += 1
              name = f"{base}-{n}"
          out_path = os.path.join(out_dir, name + ".wav")
          taken.add((os.path.join(out_dir, name) + ext).lower())
          desc = description(rank, meta, kept_names, info)
          if per_cat[ptd["cat"]] > 1:
              desc += f" | Part {seen[ptd['cat']]} of {per_cat[ptd['cat']]}"
          if grp_mode and n_reg > 1:
              desc += f" | Sausage file: {n_reg} regions" + (f", {gap_s:g} s silence between" if gap_s else "")
          if meta.get("wild") and not grp_mode:
              desc += " | Wild track (Nur-Ton): kept continuous, only slates and dialogue removed"

          frame_runs, markers, out_pos = [], [], 0
          gap_f = int(round(gap_s * sr))
          for i, sg in enumerate(part):
              s_f = min(info.n_frames, int(round(sg["start"] * sr)))
              e_f = min(info.n_frames, int(round(sg["end"] * sr)))
              if e_f <= s_f:
                  continue
              if frame_runs and gap_f:
                  out_pos += gap_f
              frame_runs.append((s_f, e_f))
              for j, (s, e, l, p) in enumerate(sg["events"]):
                  es = int(round(s * sr)) - s_f
                  el = int(round(e * sr)) - int(round(s * sr))
                  src = tc((info.time_ref + int(round(s * sr))) / sr)
                  prefix = "CUT/ " if (j == 0 and i > 0 and sg["cut"] and not gap_f) else ""
                  markers.append((out_pos + max(0, es), max(0, el),
                                  f"{prefix}{l} | {DE_NAMES.get(l, l)} | {src}"))
                  log.writerow([rel, os.path.relpath(out_path, dst), l, DE_NAMES.get(l, l), f"{p:.2f}",
                                f"{s:.3f}", src, f"{(out_pos + es) / sr:.3f}", f"{e - s:.3f}"])
              out_pos += e_f - s_f

          if not dry:
              new_ref = info.time_ref + frame_runs[0][0]
              keywords = ", ".join([_readable(c) for c in rank] + [DE_NAMES.get(c, c) for c in rank])
              user = {"CATID": catid, "CATEGORY": cat, "SUBCATEGORY": sub,
                      "CATEGORYFULL": f"{cat}-{sub}", "FXNAME": fxname, "TRACKTITLE": fxname,
                      "DESCRIPTION": desc, "KEYWORDS": keywords, "DESIGNER": creator,
                      "SHORTID": creator, "LIBRARY": meta.get("project", SOURCE_ID),
                      "SHOW": meta.get("project", ""), "SOURCE": meta["file"],
                      "NOTES": meta.get("note", ""), "MICROPHONE": ", ".join(grp_names),
                      "SHOOTDATE": meta.get("date", ""), "RECTYPE": "Production Sound"}
              user = {k: v for k, v in user.items() if v}
              extra = _chunk(b"bext", new_bext(info.bext, new_ref, desc,
                                               f"A=PCM,T=Separator: dialogue removed, {len(frame_runs)} parts"))
              extra += _chunk(b"iXML", new_ixml(info.ixml, new_ref, grp, meta["tracks"], user))
              extra += list_info({"INAM": fxname, "ICMT": desc, "IGNR": f"{cat}-{sub}",
                                  "IKEY": keywords, "IART": creator, "ISFT": "Separator"})
              os.makedirs(out_dir, exist_ok=True)
              write_bwf(out_path, info, grp, frame_runs, raw, extra, markers, gap_f)
              if OUTPUT_FORMAT == "wavpack":
                  out_path = to_wavpack(out_path, {"Title": fxname, "Comment": desc, "Genre": f"{cat}-{sub}",
                                                   "Artist": creator, "Keywords": keywords})
          elif OUTPUT_FORMAT == "wavpack":
              out_path = os.path.splitext(out_path)[0] + ".wv"
          if not dry and grp is groups[0]:
              pe = getattr(info, "piece_embs", [])
              recs = []
              for sg in part:
                  for (s, e, l, p) in sg["events"]:
                      if l in UCS or l == UNKNOWN_CAT:
                          em = _emb_for(pe, s, e)
                          recs.append({"src": os.path.abspath(path), "rel": rel, "file": meta["file"], "s": round(s, 3),
                                       "e": round(e, 3), "catid": l if l in UCS else UNCLASSIFIED[0], "p": round(p, 3),
                                       "chans": list(chans), "sr": info.sr, "float": info.is_float,
                                       "project": meta.get("project", ""), "scene": meta.get("scene", ""),
                                       "take": meta.get("take", ""),
                                       "tc": tc((info.time_ref + int(round(s * info.sr))) / info.sr),
                                       "emb": None if em is None else em.tolist()})
              if recs:
                  save_regions(dst, recs)
          written.append({"name": os.path.basename(out_path), "dir": os.path.relpath(out_dir, dst),
                          "catid": catid, "dur": round(out_pos / sr, 1), "markers": len(markers)})

    tracks_txt = f" · Spuren: {', '.join(kept_names)}" if kept_names else ""
    msg = f"({dur:.0f}s){tracks_txt}\n    → " + "\n    → ".join(
        f"{o['name']} ({o['dur']:.0f}s, {o['markers']} Marker)" for o in written)
    return "ok", written, msg


# ============================================================
# Projekt-Sausages: aehnliche Geraeusche aller Takes buendeln
# ============================================================

BUNDLE_AFTER_RUN = True        # nach jedem Lauf automatisch buendeln (in der App einstellbar)
BUNDLE_FOLDER    = "Projekt-Sausages"
REGIONS_FILE     = ".separator_regionen.jsonl"

# Feinere Beschreibungen je Familie: (CatID, Category, SubCategory, FXName, Beschreibung fuer die KI)
SUBLABELS = {
    "DOOR": [
        ("DOORWood", "DOORS", "WOOD", "Wooden Doors", "a wooden door opening or closing"),
        ("DOORMetl", "DOORS", "METAL", "Metal Doors", "a heavy metal door slamming"),
        ("DOORGlas", "DOORS", "GLASS", "Glass Doors", "a glass door opening or closing"),
        ("DOORCreak", "DOORS", "CREAK", "Creaking Doors", "a creaking squeaky door"),
        ("DOORSlid", "DOORS", "SLIDING", "Sliding Doors", "a sliding door"),
        ("DOORHdwr", "DOORS", "HARDWARE", "Door Handles and Locks", "a door handle and a key turning in a lock"),
        ("DOORKnck", "DOORS", "KNOCK", "Door Knocks", "knocking on a door"),
    ],
    "FEET": [
        ("FEETHmn", "FOOTSTEPS", "HUMAN", "Footsteps Wood", "footsteps on a wooden floor"),
        ("FEETHmn", "FOOTSTEPS", "HUMAN", "Footsteps Concrete", "footsteps on concrete"),
        ("FEETHmn", "FOOTSTEPS", "HUMAN", "Footsteps Gravel", "footsteps on gravel"),
        ("FEETHmn", "FOOTSTEPS", "HUMAN", "Footsteps Carpet", "soft footsteps on carpet"),
        ("FEETHmn", "FOOTSTEPS", "HUMAN", "Footsteps Heels", "high heels walking"),
        ("FEETHmn", "FOOTSTEPS", "HUMAN", "Footsteps Stairs", "footsteps on stairs"),
        ("FEETHmn", "FOOTSTEPS", "HUMAN", "Footsteps Running", "someone running"),
    ],
    "AMB": [
        ("AMBRoom", "AMBIENCE", "ROOM TONE", "Room Tone", "quiet indoor room tone"),
        ("AMBOffc", "AMBIENCE", "OFFICE", "Office", "office ambience"),
        ("AMBRest", "AMBIENCE", "RESTAURANT", "Restaurant", "restaurant ambience with dishes"),
        ("AMBUrbn", "AMBIENCE", "URBAN", "City", "city ambience"),
        ("AMBTraf", "AMBIENCE", "TRAFFIC", "Traffic", "street traffic"),
        ("AMBSubn", "AMBIENCE", "SUBURBAN", "Suburban", "quiet suburban street"),
        ("AMBPark", "AMBIENCE", "PARK", "Park", "park with birds and distant voices"),
        ("AMBForst", "AMBIENCE", "FOREST", "Forest", "forest ambience"),
        ("AMBRurl", "AMBIENCE", "RURAL", "Countryside", "countryside ambience"),
        ("AMBInd", "AMBIENCE", "INDUSTRIAL", "Industrial", "industrial machinery hum"),
    ],
    "VEH": [
        ("VEHCar", "VEHICLES", "CAR", "Car Engine", "a car engine idling or starting"),
        ("VEHBy", "VEHICLES", "BY", "Car Pass By", "a car driving by"),
        ("VEHDoor", "VEHICLES", "DOOR", "Car Doors", "a car door opening or slamming"),
        ("VEHHorn", "VEHICLES", "HORN", "Car Horns", "a car horn"),
        ("VEHTruck", "VEHICLES", "TRUCK", "Trucks", "a truck"),
        ("VEHMoto", "VEHICLES", "MOTORCYCLE", "Motorcycles", "a motorcycle"),
        ("VEHBus", "VEHICLES", "BUS", "Buses", "a bus"),
    ],
    "CLOTH": [
        ("CLOTHMvmt", "CLOTH", "MOVEMENT", "Cloth Movement", "clothes rustling and body movement"),
        ("CLOTHHndl", "CLOTH", "HANDLE", "Cloth Handling", "handling fabric"),
        ("CLOTHRip", "CLOTH", "RIP", "Cloth Rips", "fabric ripping"),
        ("CLOTHFlp", "CLOTH", "FLAP", "Cloth Flaps", "fabric flapping"),
    ],
    "FGHT": [
        ("FGHTImpt", "FIGHT", "IMPACT", "Punches", "punches and hits"),
        ("FGHTBf", "FIGHT", "BODYFALL", "Bodyfalls", "a body falling to the ground"),
        ("FGHTGrab", "FIGHT", "GRAB", "Grabs", "grabbing and wrestling"),
        ("FGHTClth", "FIGHT", "CLOTH", "Fight Cloth", "clothes during a fight"),
    ],
    "WATR": [
        ("WATRSplsh", "WATER", "SPLASH", "Water Splashes", "water splashing, someone swimming"),
        ("WATRFlow", "WATER", "FLOW", "Water Flow", "water running"),
        ("WATRPour", "WATER", "POUR", "Water Pour", "water being poured"),
        ("WATRDrip", "WATER", "DRIP", "Drips", "water dripping"),
        ("WATRPlmb", "WATER", "PLUMBING", "Plumbing", "a toilet flush and pipes"),
    ],
    "HMN": [
        ("HMNBrth", "HUMAN", "BREATH", "Breaths", "heavy breathing"),
        ("HMNSnor", "HUMAN", "SNORE", "Snores", "someone sleeping and snoring"),
        ("HMNCough", "HUMAN", "COUGH", "Coughs", "someone coughing"),
        ("HMNSniff", "HUMAN", "SNIFF", "Sniffs", "someone sniffing"),
    ],
    "FOOD": [
        ("FOODTware", "FOOD & DRINK", "TABLEWARE", "Tableware", "plates and cutlery on a table"),
        ("FOODKware", "FOOD & DRINK", "KITCHENWARE", "Kitchenware", "pots and pans in a kitchen"),
        ("FOODCook", "FOOD & DRINK", "COOKING", "Cooking", "food sizzling in a pan"),
        ("FOODEat", "FOOD & DRINK", "EATING", "Eating", "someone eating"),
        ("FOODDrnk", "FOOD & DRINK", "DRINKING", "Drinking", "someone drinking"),
        ("FOODPour", "FOOD & DRINK", "POUR", "Pouring Drinks", "pouring a drink into a glass"),
    ],
    "OBJ": [
        ("OBJKey", "OBJECTS", "KEYS", "Keys", "a bunch of keys jingling"),
        ("OBJBag", "OBJECTS", "BAG", "Bags", "a bag being handled"),
        ("OBJFurn", "OBJECTS", "FURNITURE", "Furniture", "a chair or table being moved"),
        ("OBJHsehld", "OBJECTS", "HOUSEHOLD", "Household Objects", "household objects being handled"),
        ("OBJMisc", "OBJECTS", "MISC", "Objects", "objects being handled and put down"),
    ],
}
DE_SUB = {"Wooden Doors": "Holztüren", "Metal Doors": "Metalltüren", "Glass Doors": "Glastüren",
          "Creaking Doors": "knarzende Türen", "Sliding Doors": "Schiebetüren",
          "Door Handles and Locks": "Türklinken und Schlösser", "Door Knocks": "Klopfen",
          "Footsteps Wood": "Schritte Holz", "Footsteps Concrete": "Schritte Beton",
          "Footsteps Gravel": "Schritte Kies", "Footsteps Carpet": "Schritte Teppich",
          "Footsteps Heels": "Schritte Absätze", "Footsteps Stairs": "Schritte Stiegen",
          "Footsteps Running": "Laufen", "Room Tone": "Raumton", "Office": "Büro-Atmo",
          "Restaurant": "Restaurant-Atmo", "City": "Stadt-Atmo", "Traffic": "Verkehr",
          "Suburban": "Vorstadt-Atmo", "Park": "Park-Atmo", "Forest": "Wald-Atmo",
          "Countryside": "Land-Atmo", "Industrial": "Industrie-Atmo"}


def family(catid):
    for k in SUBLABELS:
        if catid.startswith(k):
            return k
    return catid


def _emb_for(piece_embs, s, e):
    vs = [em for ps, pe, em in piece_embs if em is not None and min(pe, e) - max(ps, s) > 1e-6]
    if not vs:
        return None
    v = np.mean(vs, axis=0)
    return v / (np.linalg.norm(v) or 1.0)


def save_regions(dst, records):
    import base64
    with open(os.path.join(dst, REGIONS_FILE), "a", encoding="utf-8") as f:
        for r in records:
            r = dict(r)
            if r.get("emb") is not None:
                r["emb"] = base64.b64encode(np.asarray(r["emb"], np.float16).tobytes()).decode()
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def load_regions(dst):
    import base64
    p = os.path.join(dst, REGIONS_FILE)
    out = {}
    if not os.path.exists(p):
        return []
    for line in open(p, encoding="utf-8"):
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if r.get("emb"):
            r["emb"] = np.frombuffer(base64.b64decode(r["emb"]), np.float16).astype(np.float32)
        out[(r["src"], round(r["s"], 3), round(r["e"], 3))] = r   # Einzelspuren nur einmal
    return list(out.values())


def _similarity_order(E):
    """Reihenfolge, in der jedes Geraeusch dem vorigen moeglichst aehnlich ist"""
    n = len(E)
    if n <= 2:
        return list(range(n))
    S = E @ E.T
    start = int(np.argmax(S.mean(axis=1)))
    order, left = [start], set(range(n)) - {start}
    while left:
        last = order[-1]
        nxt = max(left, key=lambda j: S[last, j])
        order.append(nxt)
        left.remove(nxt)
    return order


def _read_region(r, target_sr, nch):
    from scipy.signal import resample_poly
    info = read_wav_info(r["src"])
    raw = np.memmap(r["src"], dtype=np.uint8, mode="r", offset=info.data_off,
                    shape=(info.n_frames * info.block,))
    f0 = max(0, int(round(r["s"] * info.sr)))
    f1 = min(info.n_frames, int(round(r["e"] * info.sr)))
    ch = [c for c in r["chans"] if c < info.channels][:nch] or [0]
    x = decode(frames_bytes(raw, info, f0, f1, ch), info.bps, len(ch), info.is_float)
    if x.shape[1] < nch:
        x = np.repeat(x[:, :1], nch, axis=1)
    if info.sr != target_sr:
        g = gcd(target_sr, info.sr)
        x = resample_poly(x, target_sr // g, info.sr // g, axis=0)
    fl = min(int(FADE_MS / 1000 * target_sr), len(x) // 2)
    if fl:
        ramp = np.sin(0.5 * np.pi * (np.arange(fl) + 0.5) / fl)[:, None]
        x[:fl] *= ramp
        x[-fl:] *= ramp[::-1]
    return x


def _write_stream(path, sr, nch, bps, is_float, pieces, gap, head_extra, tail):
    """Schreibt ein WAV aus float-Stuecken mit Stille dazwischen; Groessen werden danach eingetragen"""
    block = bps * nch
    fmt = struct.pack("<HHIIHH", 3 if is_float else 1, nch, sr, sr * block, block, bps * 8)
    head = _chunk(b"fmt ", fmt) + head_extra
    tmp = path + ".part"
    with open(tmp, "wb") as f:
        f.write(struct.pack("<4sI4s", b"RIFF", 0, b"WAVE"))
        f.write(head)
        data_pos = f.tell()
        f.write(struct.pack("<4sI", b"data", 0))
        size = 0
        for k, x in enumerate(pieces):
            if k and gap:
                f.write(bytes(gap * block))
                size += gap * block
            b = encode(np.clip(x, -1, 1), bps, is_float).tobytes()
            f.write(b)
            size += len(b)
        if size & 1:
            f.write(b"\0")
        f.write(tail)
        end = f.tell()
        if end - 8 > 0xFFFFFFFF:
            raise RuntimeError("Projekt-Sausage zu gross (>4 GB) – bitte Max-Länge setzen")
        f.seek(4)
        f.write(struct.pack("<I", end - 8))
        f.seek(data_pos + 4)
        f.write(struct.pack("<I", size))
    os.replace(tmp, path)


def bundle_project(dst, an, creator=None, log=print, on_event=None):
    """Alle gemerkten Regionen nach Familie + feiner Beschreibung buendeln und als Projekt-Sausages schreiben"""
    ev = on_event or (lambda k, d: None)
    creator = creator or CREATOR_ID
    regs = load_regions(dst)
    if not regs:
        log("Projekt-Sausages: noch keine Regionen gemerkt.")
        return []
    out_root = os.path.join(dst, BUNDLE_FOLDER)
    # alte Projekt-Sausages ersetzen
    man = os.path.join(out_root, ".manifest.json")
    if os.path.exists(man):
        for p in json.load(open(man, encoding="utf-8")):
            for q in (p, os.path.splitext(p)[0] + ".wv"):
                if os.path.exists(q):
                    os.remove(q)
    text_cache = {}
    groups = {}
    for r in regs:
        fam = family(r["catid"])
        opts = SUBLABELS.get(fam)
        lab = None
        if opts and r.get("emb") is not None and hasattr(an, "text_embed"):
            if fam not in text_cache:
                text_cache[fam] = an.text_embed([o[4] for o in opts])
            lab = opts[int(np.argmax(text_cache[fam] @ r["emb"]))]
        if lab is None:
            cat, sub = UCS.get(r["catid"], FALLBACK_CAT[1:])
            lab = (r["catid"], cat, sub, _readable(r["catid"]) or r["catid"], "")
        key = (r.get("project") or SOURCE_ID, lab[0], lab[3])
        groups.setdefault(key, {"lab": lab, "regs": []})["regs"].append(r)
    srs = [r.get("sr", 48000) for r in regs]
    target_sr = max(set(srs), key=srs.count)
    written, rows = [], []
    for gi, ((proj, cid, fxn), g) in enumerate(sorted(groups.items()), 1):
        rs = g["regs"]
        counts = [len(r["chans"]) for r in rs]
        nch = max(set(counts), key=counts.count)   # haeufigste Spurzahl, Rest wird angeglichen
        E = np.stack([r["emb"] if r.get("emb") is not None else np.zeros(1, np.float32) for r in rs]) \
            if all(r.get("emb") is not None for r in rs) else None
        order = _similarity_order(E) if E is not None else sorted(range(len(rs)), key=lambda i: (rs[i]["file"], rs[i]["s"]))
        rs = [rs[i] for i in order]
        # in Teile nach Max-Laenge
        mx = MAX_FILE_S or 1e12
        parts, cur, ln = [], [], 0.0
        for r in rs:
            d = r["e"] - r["s"]
            if cur and ln + d + GAP_S > mx:
                parts.append(cur)
                cur, ln = [], 0.0
            cur.append(r)
            ln += d + (GAP_S if len(cur) > 1 else 0)
        if cur:
            parts.append(cur)
        lab = g["lab"]
        cat, sub = lab[1], lab[2]
        bits = 32 if any(r.get("float") for r in rs) else 24
        for k, part in enumerate(parts, 1):
            suffix = f"-P{k}" if len(parts) > 1 else ""
            fx_name = _clean(f"{fxn} x{len(part)}")
            base = f"{cid}_{fx_name}_{_clean(creator, 12) or 'NN'}_{_clean(proj, 24).replace(' ', '')}" + \
                   (f"_{suffix.lstrip('-')}" if suffix else "")
            out_dir = os.path.join(out_root, _clean(cat, 40), _clean(sub, 40)) if SORT_BY_CATEGORY else out_root
            os.makedirs(out_dir, exist_ok=True)
            path = os.path.join(out_dir, base + ".wav")
            gap_f = int(round(GAP_S * target_sr))
            pieces, markers, pos = [], [], 0
            takes = []
            for i, r in enumerate(part):
                x = _read_region(r, target_sr, nch)
                if i and gap_f:
                    pos += gap_f
                src_lbl = os.path.splitext(r["file"])[0] + (f" Sc{r['scene']}" if r.get("scene") else "") + \
                    (f" Tk{r['take']}" if r.get("take") else "")
                markers.append((pos, len(x), f"{cid} | {DE_SUB.get(fxn, DE_NAMES.get(cid, fxn))} | {src_lbl} | {r.get('tc', '')}"))
                rows.append([os.path.relpath(path, dst), i + 1, r.get("rel", r["file"]), r.get("tc", ""),
                             f"{r['e'] - r['s']:.3f}", r["catid"], cid])
                takes.append(r["file"])
                pos += len(x)
                pieces.append(x)
            de = DE_SUB.get(fxn, DE_NAMES.get(cid, ""))
            desc = (f"Project sausage: {fxn}, {len(part)} regions from {len(set(takes))} takes, "
                    f"dialogue removed, sorted by sound similarity. Inhalt: {de}. Project {proj}"
                    + (f" | {GAP_S:g} s silence between" if GAP_S else "")
                    + " | Sources: " + ", ".join(sorted(set(takes)))[:600])
            kw = ", ".join(x for x in (fxn, cat.title(), sub.title(), de) if x)
            user = {"CATID": cid, "CATEGORY": cat, "SUBCATEGORY": sub, "CATEGORYFULL": f"{cat}-{sub}",
                    "FXNAME": fx_name, "TRACKTITLE": fx_name, "DESCRIPTION": desc, "KEYWORDS": kw,
                    "DESIGNER": creator, "SHORTID": creator, "LIBRARY": proj, "SHOW": proj,
                    "RECTYPE": "Production Sound"}
            extra = _chunk(b"bext", new_bext(None, 0, desc, "A=PCM,T=Separator: project sausage"))
            extra += _chunk(b"iXML", new_ixml(None, 0, list(range(nch)), ["Mono" if nch == 1 else f"Ch {c + 1}" for c in range(nch)], user))
            extra += list_info({"INAM": fx_name, "ICMT": desc, "IGNR": f"{cat}-{sub}", "IKEY": kw,
                                "IART": creator, "ISFT": "Separator"})
            _write_stream(path, target_sr, nch, bits // 8, bits == 32, pieces, gap_f, extra, marker_chunks(markers))
            if OUTPUT_FORMAT == "wavpack" and wavpack_bin():
                path = to_wavpack(path, {"Title": fx_name, "Comment": desc, "Genre": f"{cat}-{sub}", "Artist": creator})
            written.append(path)
            log(f"Projekt-Sausage: {os.path.relpath(path, dst)} ({len(part)} Regionen, {pos / target_sr:.0f}s)")
            ev("bundle_file", {"name": os.path.basename(path), "dir": os.path.relpath(os.path.dirname(path), dst),
                               "catid": cid, "regions": len(part), "dur": round(pos / target_sr, 1)})
    os.makedirs(out_root, exist_ok=True)
    json.dump(written, open(man, "w", encoding="utf-8"), ensure_ascii=False)
    with open(os.path.join(out_root, "projekt_sausages.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["Projekt-Sausage", "Region", "Quelle", "Timecode Original", "Dauer (s)", "CatID Take", "CatID Sausage"])
        w.writerows(rows)
    log(f"Projekt-Sausages fertig: {len(written)} Files aus {len(regs)} Regionen.")
    ev("bundle_done", {"files": len(written), "regions": len(regs)})
    return written


# ============================================================
# Gesamtlauf (von App und Kommandozeile benutzt)
# ============================================================


def read_done(dst):
    """Fertig-Liste: {rel: (status, anzahl)} – auch alte Zeilen ohne Status"""
    out = {}
    p = os.path.join(dst, ".fx_fertig.txt")
    if os.path.exists(p):
        for line in open(p, encoding="utf-8").read().splitlines():
            f = line.split("\t")
            if f[0]:
                out[f[0]] = (f[1] if len(f) > 1 else "ok", int(f[2]) if len(f) > 2 and f[2].isdigit() else 0)
    return out


def find_files(src, dst):
    files = []
    for root, _, names in os.walk(src):
        if os.path.abspath(root).startswith(os.path.abspath(dst)):
            continue
        for n in sorted(names):
            if n.lower().endswith((".wav", ".bwf")) and not n.startswith("._") and not n.endswith(".part"):
                files.append(os.path.join(root, n))
    return sorted(files)


def run(src, dst, creator=CREATOR_ID, limit=0, dry=False, log=print,
        progress=lambda i, n: None, stop=lambda: False, analyzer=None,
        min_s=None, max_s=None, fmt=None, sort_ucs=None, split=None,
        keep_words=None, drop_words=None, unknown=None, on_event=None, only=None,
        group_cat=None, gap_s=None, bundle=None, wild_cont=None):
    global MIN_FILE_S, MAX_FILE_S, OUTPUT_FORMAT, SORT_BY_CATEGORY, SPLIT_TRACKS
    global KEEP_TRACKS, IGNORE_TRACKS, UNKNOWN_TRACKS, GROUP_BY_CAT, GAP_S, BUNDLE_AFTER_RUN
    global WILD_CONTINUOUS
    if bundle is not None:
        BUNDLE_AFTER_RUN = bool(bundle)
    if wild_cont is not None:
        WILD_CONTINUOUS = bool(wild_cont)
    if group_cat is not None:
        GROUP_BY_CAT = bool(group_cat)
    if gap_s is not None:
        GAP_S = max(0.0, float(gap_s))
    if sort_ucs is not None:
        SORT_BY_CATEGORY = bool(sort_ucs)
    if split is not None:
        SPLIT_TRACKS = bool(split)
    if keep_words is not None:
        KEEP_TRACKS = [w.strip().lower() for w in keep_words if w.strip()]
    if drop_words is not None:
        IGNORE_TRACKS = [w.strip().lower() for w in drop_words if w.strip()]
    if unknown in ("fallback", "keep", "drop"):
        UNKNOWN_TRACKS = unknown
    if fmt:
        OUTPUT_FORMAT = fmt
    if OUTPUT_FORMAT == "wavpack" and not wavpack_bin():
        log("FEHLER: WavPack ist nicht installiert. Im Terminal: brew install wavpack – oder WAV wählen.")
        return
    if min_s is not None:
        MIN_FILE_S = max(MIN_SEGMENT_S, float(min_s))
    if max_s is not None:
        MAX_FILE_S = max(0.0, float(max_s))
    if MAX_FILE_S and MAX_FILE_S < MIN_FILE_S:
        MAX_FILE_S = MIN_FILE_S
    src, dst = os.path.abspath(src), os.path.abspath(dst)
    ev = on_event or (lambda kind, data: None)
    files = find_files(src, dst)
    if only:
        wanted = set(only)
        files = [f for f in files if os.path.relpath(f, src) in wanted]
    if limit:
        files = files[:limit]
    log(f"{len(files)} WAV-Files gefunden. Spuren verwenden: {', '.join(KEEP_TRACKS) or '–'} · "
        f"löschen: {', '.join(IGNORE_TRACKS) or '–'} · unbekannte: {UNKNOWN_TRACKS}"
        + (" · Einzelspuren" if SPLIT_TRACKS else "") + (" · UCS-Ordner" if SORT_BY_CATEGORY else ""))
    if not files:
        return
    an = analyzer or Analyzer(log)
    os.makedirs(dst, exist_ok=True)
    done_path = os.path.join(dst, ".fx_fertig.txt")
    done = set(read_done(dst))
    log_path = os.path.join(dst, "fx_marker_liste.csv")
    new_log = not os.path.exists(log_path)
    taken = set()
    with open(log_path, "a", newline="", encoding="utf-8") as lf, \
            open(done_path, "a", encoding="utf-8") as df:
        w = csv.writer(lf, delimiter=";")
        if new_log:
            w.writerow(["Quelle", "Neues File", "UCS CatID", "Kategorie DE", "Sicherheit", "Pos. Original (s)",
                        "Timecode Original", "Pos. neues File (s)", "Dauer (s)"])
        t0 = time.time()
        for i, path in enumerate(files, 1):
            if stop():
                log("Gestoppt. Beim nächsten Start geht es hier weiter.")
                break
            progress(i - 1, len(files))
            rel = os.path.relpath(path, src)
            if rel in done and not dry:
                log(f"[{i}/{len(files)}] {rel}: schon erledigt")
                ev("skip", {"i": i, "n": len(files), "rel": rel})
                continue
            t = time.time()
            ev("start", {"i": i, "n": len(files), "rel": rel})
            try:
                status, outs, msg = process(path, dst, os.path.dirname(rel), an, dry, w, rel, creator, taken,
                                            view=lambda v: ev("view", v))
                if not dry:
                    df.write(f"{rel}\t{status}\t{len(outs)}\n")
                    df.flush()
            except Exception as ex:
                status, outs, msg = "error", [], f"FEHLER: {ex}"
            lf.flush()
            log(f"[{i}/{len(files)}] {rel} {msg} ({time.time() - t:.0f}s)")
            ev("done", {"i": i, "n": len(files), "rel": rel, "status": status, "outputs": outs,
                        "msg": msg, "secs": round(time.time() - t, 1)})
        progress(len(files), len(files))
        ev("finished", {"log": log_path})
    if BUNDLE_AFTER_RUN and not dry and not stop():
        try:
            bundle_project(dst, an, creator, log, ev)
        except Exception as ex:
            log(f"FEHLER Projekt-Sausages: {ex}")
        log(f"Fertig in {(time.time() - t0) / 60:.1f} min. Liste: {log_path}")
