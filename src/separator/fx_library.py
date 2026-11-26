"""Separator – FX Library (feature module).

Turns a take into UCS-named sound effect files: one sausage file per category and take (or one
continuous file for wild tracks), plus project sausages that group similar sounds from all takes.
Can be built as its own app ("Separator FX").
"""

import csv
import json
import os
import struct
from math import gcd

import numpy as np

import config as cfg
from ucs import DE_NAMES, DE_SUB, SUBLABELS, UCS, VOCAL_CATS, _clean, _readable, description, family, ranking, ucs_name
from wavio import _chunk, decode, encode, frames_bytes, marker_chunks, read_wav_info, tc, to_wavpack, wavpack_bin, write_bwf
import siblings
from metadata import list_info, new_bext, new_ixml, read_meta, select_channels
from analysis import analyse
from views import file_view


def part_mode(meta):
    """(split by category?, gap) for this take – wild tracks stay in one piece"""
    if cfg.WILD_CONTINUOUS and meta.get("wild"):
        return False, 0.0
    return cfg.GROUP_BY_CAT, cfg.GAP_S


def build_parts(runs, group=None, gap=None):
    """Runs -> parts, one file each: {"cat", "segs": [{"start", "end", "events", "cut"}]}.
    cfg.GROUP_BY_CAT: jede Kategorie eigenes File, jedes Geraeusch eigenes Stueck mit Pause dazwischen."""
    group = cfg.GROUP_BY_CAT if group is None else group
    gap = cfg.GAP_S if gap is None else gap
    mx = cfg.MAX_FILE_S or 1e12
    segs = []
    for r in runs:
        first = True
        if group:
            for s, e, l, p in r["events"]:
                segs.append({"start": s, "end": e, "events": [(s, e, l, p)], "cut": first, "cat": l})
                first = False
        else:
            segs.append({"start": r["start"], "end": r["end"], "events": list(r["events"]), "cut": True, "cat": "*"})
    # split pieces longer than MAX_FILE_S at event boundaries, or hard if one event is that long
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
    # collect per category (in order of first appearance) and pack into files
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
        if length >= cfg.MIN_FILE_S or (pt["cat"] in VOCAL_CATS and length >= 0.5):   # keep short screams
            out.append(pt)
    return out


KEY = "library"
TITLE = "FX LIBRARY"


def preview_parts(runs, meta):
    """Feature interface: the parts the track view shows (what process() would write)"""
    return split_parts(runs, meta)


def after_run(dst, an, creator, log, on_event):
    """Feature interface: build the project sausages once all takes are done"""
    if cfg.BUNDLE_AFTER_RUN:
        bundle_project(dst, an, creator, log, on_event)


def split_parts(runs, meta=None):
    return build_parts(runs, *part_mode(meta or {}))


def process(path, dst, rel_dir, an, dry, log, rel, creator, taken, view=None):
    info = read_wav_info(path)
    meta = read_meta(info, path)
    chans = select_channels(meta["tracks"])
    why = "no matching track"
    if chans and siblings.is_lav_by_context(meta, siblings.find(path)):
        chans, why = [], "lav (the same take has a boom/atmo file)"   # e.g. a track named "Biggi"
    if not chans:
        if view:
            raw0 = np.memmap(path, dtype=np.uint8, mode="r", offset=info.data_off,
                             shape=(info.n_frames * info.block,))
            view(file_view(path, info, meta, raw0, [], rel))
        return "notrack", [], why
    kept_names = [meta["tracks"][i] or f"Track {i + 1}" for i in chans] if any(meta["tracks"]) else []

    raw = np.memmap(path, dtype=np.uint8, mode="r", offset=info.data_off,
                    shape=(info.n_frames * info.block,))
    runs, dur = analyse(info, raw, an, chans, path)
    grp_mode, gap_s = part_mode(meta)
    parts = build_parts(runs, grp_mode, gap_s)
    if view:
        view(file_view(path, info, meta, raw, parts, rel))
    if not parts:
        return "dialog", [], "dialogue only, too quiet or too short"

    sr = info.sr
    written = []
    groups = [[c] for c in chans] if (cfg.SPLIT_TRACKS and len(chans) > 1) else [chans]
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
          unclassified = grp_mode and ptd["cat"] == cfg.UNKNOWN_CAT
          if unclassified:
              rank = [cfg.UNCLASSIFIED[0]]
          suffix = trk_suffix + (f"-P{seen[ptd['cat']]}" if per_cat[ptd["cat"]] > 1 else "")
          catid, base, fxname = ucs_name(rank, meta, creator, suffix)
          if unclassified:
              base = base.replace(f"{catid}_{fxname}_", f"{catid}_{cfg.UNCLASSIFIED[1]}_", 1)
              fxname = cfg.UNCLASSIFIED[1]
          n_reg = sum(len(sg["events"]) for sg in part)
          if grp_mode and n_reg > 1:
              base = base.replace(f"_{fxname}_", f"_{fxname} x{n_reg}_", 1)
          cat, sub = UCS.get(catid, cfg.FALLBACK_CAT[1:])
          out_dir = os.path.join(dst, _clean(cat, 40), _clean(sub, 40)) if cfg.SORT_BY_CATEGORY \
            else os.path.join(dst, rel_dir)
          name, n = base, 1
          ext = ".wv" if cfg.OUTPUT_FORMAT == "wavpack" else ".wav"
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
                      "SHORTID": creator, "LIBRARY": meta.get("project", cfg.SOURCE_ID),
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
              if cfg.OUTPUT_FORMAT == "wavpack":
                  out_path = to_wavpack(out_path, {"Title": fxname, "Comment": desc, "Genre": f"{cat}-{sub}",
                                                   "Artist": creator, "Keywords": keywords})
          elif cfg.OUTPUT_FORMAT == "wavpack":
              out_path = os.path.splitext(out_path)[0] + ".wv"
          if not dry and grp is groups[0]:
              pe = getattr(info, "piece_embs", [])
              recs = []
              for sg in part:
                  for (s, e, l, p) in sg["events"]:
                      if l in UCS or l == cfg.UNKNOWN_CAT:
                          em = _emb_for(pe, s, e)
                          recs.append({"src": os.path.abspath(path), "rel": rel, "file": meta["file"], "s": round(s, 3),
                                       "e": round(e, 3), "catid": l if l in UCS else cfg.UNCLASSIFIED[0], "p": round(p, 3),
                                       "chans": list(chans), "sr": info.sr, "float": info.is_float,
                                       "project": meta.get("project", ""), "scene": meta.get("scene", ""),
                                       "take": meta.get("take", ""),
                                       "tc": tc((info.time_ref + int(round(s * info.sr))) / info.sr),
                                       "emb": None if em is None else em.tolist()})
              if recs:
                  save_regions(dst, recs)
          written.append({"name": os.path.basename(out_path), "dir": os.path.relpath(out_dir, dst),
                          "catid": catid, "dur": round(out_pos / sr, 1), "markers": len(markers)})

    tracks_txt = f" · tracks: {', '.join(kept_names)}" if kept_names else ""
    msg = f"({dur:.0f}s){tracks_txt}\n    → " + "\n    → ".join(
        f"{o['name']} ({o['dur']:.0f}s, {o['markers']} Marker)" for o in written)
    return "ok", written, msg


def _emb_for(piece_embs, s, e):
    vs = [em for ps, pe, em in piece_embs if em is not None and min(pe, e) - max(ps, s) > 1e-6]
    if not vs:
        return None
    v = np.mean(vs, axis=0)
    return v / (np.linalg.norm(v) or 1.0)


def save_regions(dst, records):
    import base64
    with open(os.path.join(dst, cfg.REGIONS_FILE), "a", encoding="utf-8") as f:
        for r in records:
            r = dict(r)
            if r.get("emb") is not None:
                r["emb"] = base64.b64encode(np.asarray(r["emb"], np.float16).tobytes()).decode()
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def load_regions(dst):
    import base64
    p = os.path.join(dst, cfg.REGIONS_FILE)
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
    """Order in which every sound is as similar as possible to the one before (greedy nearest neighbor)"""
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
    fl = min(int(cfg.FADE_MS / 1000 * target_sr), len(x) // 2)
    if fl:
        ramp = np.sin(0.5 * np.pi * (np.arange(fl) + 0.5) / fl)[:, None]
        x[:fl] *= ramp
        x[-fl:] *= ramp[::-1]
    return x


def _write_stream(path, sr, nch, bps, is_float, pieces, gap, head_extra, tail):
    """Write a WAV from float pieces with silence between them; chunk sizes are filled in afterwards"""
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
            raise RuntimeError("project sausage too large (>4 GB) – please set a maximum length")
        f.seek(4)
        f.write(struct.pack("<I", end - 8))
        f.seek(data_pos + 4)
        f.write(struct.pack("<I", size))
    os.replace(tmp, path)


def bundle_project(dst, an, creator=None, log=print, on_event=None):
    """Group all recorded regions by family and fine sub-category and write them as project sausages"""
    ev = on_event or (lambda k, d: None)
    creator = creator or cfg.CREATOR_ID
    regs = load_regions(dst)
    if not regs:
        log("Project sausages: no regions recorded yet.")
        return []
    out_root = os.path.join(dst, cfg.BUNDLE_FOLDER)
    # replace the project sausages of the previous build
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
            cat, sub = UCS.get(r["catid"], cfg.FALLBACK_CAT[1:])
            lab = (r["catid"], cat, sub, _readable(r["catid"]) or r["catid"], "")
        key = (r.get("project") or cfg.SOURCE_ID, lab[0], lab[3])
        groups.setdefault(key, {"lab": lab, "regs": []})["regs"].append(r)
    srs = [r.get("sr", 48000) for r in regs]
    target_sr = max(set(srs), key=srs.count)
    written, rows = [], []
    for gi, ((proj, cid, fxn), g) in enumerate(sorted(groups.items()), 1):
        rs = g["regs"]
        counts = [len(r["chans"]) for r in rs]
        nch = max(set(counts), key=counts.count)   # most common track count; the others are matched to it
        E = np.stack([r["emb"] if r.get("emb") is not None else np.zeros(1, np.float32) for r in rs]) \
            if all(r.get("emb") is not None for r in rs) else None
        order = _similarity_order(E) if E is not None else sorted(range(len(rs)), key=lambda i: (rs[i]["file"], rs[i]["s"]))
        rs = [rs[i] for i in order]
        # split into parts by the maximum length
        mx = cfg.MAX_FILE_S or 1e12
        parts, cur, ln = [], [], 0.0
        for r in rs:
            d = r["e"] - r["s"]
            if cur and ln + d + cfg.GAP_S > mx:
                parts.append(cur)
                cur, ln = [], 0.0
            cur.append(r)
            ln += d + (cfg.GAP_S if len(cur) > 1 else 0)
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
            out_dir = os.path.join(out_root, _clean(cat, 40), _clean(sub, 40)) if cfg.SORT_BY_CATEGORY else out_root
            os.makedirs(out_dir, exist_ok=True)
            path = os.path.join(out_dir, base + ".wav")
            gap_f = int(round(cfg.GAP_S * target_sr))
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
                    + (f" | {cfg.GAP_S:g} s silence between" if cfg.GAP_S else "")
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
            if cfg.OUTPUT_FORMAT == "wavpack" and wavpack_bin():
                path = to_wavpack(path, {"Title": fx_name, "Comment": desc, "Genre": f"{cat}-{sub}", "Artist": creator})
            written.append(path)
            log(f"Project sausage: {os.path.relpath(path, dst)} ({len(part)} regions, {pos / target_sr:.0f}s)")
            ev("bundle_file", {"name": os.path.basename(path), "dir": os.path.relpath(os.path.dirname(path), dst),
                               "catid": cid, "regions": len(part), "dur": round(pos / target_sr, 1)})
    os.makedirs(out_root, exist_ok=True)
    json.dump(written, open(man, "w", encoding="utf-8"), ensure_ascii=False)
    with open(os.path.join(out_root, "project_sausages.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["Project sausage", "Region", "Source", "Original timecode", "Duration (s)", "CatID take", "CatID sausage"])
        w.writerows(rows)
    log(f"Project sausages done: {len(written)} files from {len(regs)} regions.")
    ev("bundle_done", {"files": len(written), "regions": len(regs)})
    return written
