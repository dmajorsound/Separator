"""Separator – reading and writing WAV / Broadcast WAV / RF64.

Low-level audio I/O with no knowledge of sounds or naming: chunk parsing, PCM decode/encode,
channel extraction, cue/region markers, writing cut files with fades, waveform peaks, WavPack.
"""

import os
import shutil
import struct
import subprocess
from math import gcd

import numpy as np

import config as cfg


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
            raise ValueError("not a WAV file")
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
        raise ValueError("fmt or data chunk missing")
    code, ch, sr, _, block, bits = struct.unpack("<HHIIHH", info.fmt_bytes[:16])
    if code == 0xFFFE and len(info.fmt_bytes) >= 26:
        code = struct.unpack("<H", info.fmt_bytes[24:26])[0]
    if code not in (1, 3):
        raise ValueError(f"format {code} not supported")
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
        raise ValueError("bit depth not supported")
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
    raise ValueError("bit depth not supported")


def frames_bytes(raw, info, f0, f1, chans):
    """Raw bytes of frames f0..f1, only the channels in chans (bit-exact)"""
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


def _chunk(cid, body):
    return struct.pack("<4sI", cid, len(body)) + body + (b"\0" if len(body) & 1 else b"")


def marker_chunks(markers):
    """markers: Liste von (position_frames, laenge_frames, text)"""
    if not markers:
        return b""
    cue = struct.pack("<I", len(markers))
    adtl = b"adtl"
    for i, (pos, length, text) in enumerate(markers, start=1):
        cue += struct.pack("<I I 4s I I I", i, pos, b"data", 0, 0, pos)
        adtl += _chunk(b"labl", struct.pack("<I", i) + text.encode("utf-8") + b"\0")
        if cfg.WRITE_REGIONS and length > 0:
            adtl += _chunk(b"ltxt", struct.pack("<II4sHHHH", i, length, b"rgn ", 0, 0, 0, 0))
    return _chunk(b"cue ", cue) + _chunk(b"LIST", adtl)


def write_bwf(path, info, chans, runs, raw, head_extra, markers, gap_frames=0):
    nch = len(chans)
    block = info.bps * nch
    fmt = struct.pack("<HHIIHH", 3 if info.is_float else 1, nch, info.sr,
                      info.sr * block, block, info.bits)
    fade = max(1, int(cfg.FADE_MS / 1000 * info.sr))
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


def tc(sec):
    sec = sec % 86400
    h, r = divmod(sec, 3600)
    m, s = divmod(r, 60)
    return f"{int(h):02d}:{int(m):02d}:{s:06.3f}"


def peaks(raw, info, bins=900, max_ch=8):
    """Waveform envelope per track (0..1) for the track view"""
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


def wavpack_bin():
    import sys
    bundled = os.path.join(getattr(sys, "_MEIPASS", ""), "bin", "wavpack")
    for p in (bundled, shutil.which("wavpack"), "/opt/homebrew/bin/wavpack", "/usr/local/bin/wavpack"):
        if p and os.path.exists(p):
            return p
    return None


def to_wavpack(wav_path, tags):
    """WAV -> .wv (lossless, all RIFF chunks kept); the WAV is deleted afterwards"""
    wv = os.path.splitext(wav_path)[0] + ".wv"
    cmd = [wavpack_bin(), "-q", "-y", "-m"]
    for k, v in tags.items():
        cmd += ["-w", f"{k}={v[:1000]}"]
    cmd += [wav_path, "-o", wv]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0 or not os.path.exists(wv):
        raise RuntimeError("WavPack failed: " + (r.stderr or "").strip()[-200:])
    os.remove(wav_path)
    return wv
