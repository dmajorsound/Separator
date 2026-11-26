"""Separator – AI analysis.

Analyzer loads the models (Silero VAD for speech, an AudioSet classifier for sound classes, CLAP for
sound fingerprints). analyse() turns one take into runs of usable sound with labelled events.
"""

import os

import numpy as np

import config as cfg
from ucs import AMBIENCE_CATS, AUDIOSET_MAP, LABELS, VOCAL_CATS
from wavio import mono, mono_full
from metadata import _tag


class Analyzer:
    def __init__(self, log=print):
        import torch
        self.torch = torch
        torch.set_num_threads(os.cpu_count() or 4)
        log("Loading speech detection (Silero VAD) …")
        from silero_vad import load_silero_vad, get_speech_timestamps
        self.vad_model = load_silero_vad()
        self._gst = get_speech_timestamps

        log(f"Loading sound recognition ({cfg.CLAP_MODEL}) …")
        from transformers import ClapModel, ClapProcessor
        self.model = ClapModel.from_pretrained(cfg.CLAP_MODEL).eval()
        self.proc = ClapProcessor.from_pretrained(cfg.CLAP_MODEL)

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
        self.tagger = None
        try:
            log(f"Loading sound classes ({cfg.TAG_MODEL}) …")
            from transformers import ASTFeatureExtractor, ASTForAudioClassification
            self.tag_fe = ASTFeatureExtractor.from_pretrained(cfg.TAG_MODEL)
            self.tagger = ASTForAudioClassification.from_pretrained(cfg.TAG_MODEL).eval().to(self.device)
            names = {i: n.strip().lower() for i, n in self.tagger.config.id2label.items()}
            self.tag_names = {i: n for i, n in self.tagger.config.id2label.items()}
            self.tag_map, found = [], set()
            for cat, classes in AUDIOSET_MAP.items():
                for cl in classes:
                    ids = [i for i, n in names.items() if n == cl]
                    if ids:
                        self.tag_map.append((cat, ids[0]))
                        found.add(cl)
            missing = [cl for cls in AUDIOSET_MAP.values() for cl in cls if cl not in found]
            if missing:
                log(f"  ({len(missing)} sound classes not in the model: {', '.join(missing[:6])} …)")
        except Exception as ex:
            log(f"  (sound classes unavailable, using CLAP only: {ex})")
            self.tagger = None
        log(f"Ready (running on {'Apple GPU' if self.device == 'mps' else 'CPU'}).")

    def _tag(self, waves48):
        """AudioSet classifier -> per window (catid, prob); speech/vocal classes compete with sounds"""
        from scipy.signal import resample_poly
        torch = self.torch
        x16 = [resample_poly(w, 1, 3).astype(np.float32) for w in waves48]
        feats = self.tag_fe(x16, sampling_rate=16000, return_tensors="pt")
        with torch.no_grad():
            logits = self.tagger(feats["input_values"].to(self.device)).logits
        prob = torch.sigmoid(logits).cpu().numpy()
        out, self.last_top = [], []
        for pr in prob:
            best = {}
            for cat, i in self.tag_map:
                if pr[i] > best.get(cat, 0.0):
                    best[cat] = float(pr[i])
            # AudioSet marks almost every window as "Inside, small room" or "Hum" as well. Those
            # context classes only win when no real event is clearly there.
            score = {c: p * (cfg.AMBIENCE_WEIGHT if c in AMBIENCE_CATS else 1.0) for c, p in best.items()}
            cat = max(score, key=score.get) if score else cfg.UNKNOWN_CAT
            p = best.get(cat, 0.0)
            out.append((cat if p >= cfg.TAG_MIN_PROB else cfg.UNKNOWN_CAT, p))
            top = np.argsort(pr)[::-1][:5]
            self.last_top.append([(self.tag_names[int(i)], float(pr[i])) for i in top])
        return out

    @staticmethod
    def _emb(x):
        if hasattr(x, "pooler_output") and x.pooler_output is not None:
            return x.pooler_output
        if not hasattr(x, "shape"):
            return x[0]
        return x

    def vad(self, mono16):
        """Speech stretches [(start_s, end_s)] in a 16 kHz mono signal (Silero VAD)"""
        x = level_for_vad(mono16)
        if x is None:
            return []
        ts = self._gst(self.torch.from_numpy(x), self.vad_model, sampling_rate=16000,
                       threshold=cfg.VAD_THRESHOLD,
                       min_speech_duration_ms=cfg.VAD_MIN_SPEECH_MS,
                       min_silence_duration_ms=cfg.VAD_MIN_SILENCE_MS,
                       speech_pad_ms=30)
        return [(t["start"] / 16000, t["end"] / 16000) for t in ts]

    def text_embed(self, prompts):
        """Normalized CLAP text embeddings (numpy) for the fine sub-categories"""
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
        self.last_top = []
        top = [None] * len(windows)
        todo = []
        for i, w in enumerate(windows):
            peak = float(np.max(np.abs(w))) if w.size else 0.0
            if peak <= 10 ** (cfg.MIN_LEVEL_DBFS / 20):
                out[i] = ("QUIET", 1.0)         # below the level threshold: cut, never written
            elif peak <= 10 ** (cfg.SILENCE_DBFS / 20):
                out[i] = (cfg.SILENCE_CAT, 1.0)
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
                self.log("  (GPU not compatible, continuing on CPU)")
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
            if self.tagger is not None:
                try:
                    tags = self._tag([w for _, w in batch])
                except Exception as ex:
                    self.log(f"  (sound classes failed, using CLAP: {ex})")
                    self.tagger, tags = None, None
                if tags is not None:
                    for (i, _), t, tp in zip(batch, tags, self.last_top):
                        out[i], top[i] = t, tp
                    continue
            prob = per.softmax(dim=-1).cpu().numpy()
            for (i, _), pr in zip(batch, prob):
                k = int(pr.argmax())
                out[i] = (self.names[k] if pr[k] >= cfg.MIN_PROB else cfg.UNKNOWN_CAT, float(pr[k]))
                top[i] = [("CLAP " + self.names[int(j)], float(pr[j])) for j in np.argsort(pr)[::-1][:5]]
        self.last_top = top
        return out

    def _audio(self, feats):
        with self.torch.no_grad():
            kw = {k: v.to(self.device) for k, v in feats.items() if hasattr(v, "to")}
            a = self._emb(self.model.get_audio_features(**kw))
        return self.torch.nn.functional.normalize(a, dim=-1)


# ---------------------------------------------------------------------------
# Speech detection helpers
# ---------------------------------------------------------------------------
def level_for_vad(x):
    """Bring a 16 kHz signal to a working level for the speech detector.

    Normalizing to the peak (the old way) let one door slam push quiet dialogue down by 30 dB, and
    Silero then missed it. Here the loud speech-like frames (95th percentile of 30 ms RMS, digital
    silence ignored) are set to -20 dBFS, with at most +30 dB of gain and soft clipping on top.
    Returns None for silent signals."""
    if x.size == 0:
        return None
    n = len(x) // 480
    if n == 0:
        return None
    rms = np.sqrt(np.mean(x[:n * 480].reshape(n, 480).astype(np.float64) ** 2, axis=1))
    rms = rms[rms > 1e-6]                        # skip digital silence (muted lavs, gaps)
    if rms.size == 0:
        return None
    ref = float(np.percentile(rms, 95))
    gain = min(10 ** (-20 / 20) / max(ref, 1e-9), 10 ** (30 / 20))
    return np.tanh(x * gain).astype(np.float32)


def speech_regions(info, raw, an, path=None):
    """Union of the speech found on every track of this file and of its sibling files.

    Each track is checked on its own: a mono-sum of boom and lavs hides quiet speech under the
    louder track, and the lavs carry the cleanest dialogue even if they are never written out.
    Siblings (separate mono files of the same take, see siblings.py) are shifted by their start
    timecode so they line up with this file."""
    from siblings import find
    cache = an.__dict__.setdefault("_vad_cache", {})

    def of_file(p, inf, rw):
        key = (os.path.abspath(p), inf.n_frames) if p else None
        if key and key in cache:
            return cache[key]
        if rw is None:
            rw = np.memmap(p, dtype=np.uint8, mode="r", offset=inf.data_off, shape=(inf.n_frames * inf.block,))
        found = []
        for ch in range(inf.channels):
            found += an.vad(mono_full(rw, inf, 16000, [ch]))
        found = merge(found)
        if key:
            cache[key] = found
        return found

    speech = list(of_file(path, info, raw))
    if path and cfg.USE_SIBLINGS:
        t0 = (info.time_ref or 0) / info.sr
        dur = info.n_frames / info.sr
        for sp, sinfo, _ in find(path):
            if sinfo.sr <= 0:
                continue
            off = (sinfo.time_ref or 0) / sinfo.sr - t0
            for s, e in of_file(sp, sinfo, None):
                s, e = s + off, e + off
                if e > 0 and s < dur:
                    speech.append((max(0.0, s), min(dur, e)))
    return merge(speech)


def envelope(raw, info, chans, step_s=0.01):
    """RMS envelope (10 ms frames) of the selected tracks, for snapping cut points"""
    x = mono_full(raw, info, 16000, chans)
    hop = int(16000 * step_s)
    n = len(x) // hop
    if n == 0:
        return np.zeros(0), step_s
    return np.sqrt(np.mean(x[:n * hop].reshape(n, hop).astype(np.float64) ** 2, axis=1)), step_s


def snap_cuts(cut, env, step, dur):
    """Move each cut edge to the quietest point nearby, so a cut does not chop a footstep or a door
    in half. A cut may grow by up to SNAP_S but never shrinks by more than half of SPEECH_PAD_S, so
    it still covers the speech."""
    if env.size == 0 or cfg.SNAP_S <= 0:
        return cut

    def quietest(a, b):
        i0, i1 = max(0, int(a / step)), min(len(env), int(b / step) + 1)
        return (i0 + int(np.argmin(env[i0:i1]))) * step if i1 > i0 else None

    out = []
    inner = cfg.SPEECH_PAD_S * 0.5
    for s, e in cut:
        ns = quietest(s - cfg.SNAP_S, s + inner) if s > 0 else s
        ne = quietest(e - inner, e + cfg.SNAP_S) if e < dur else e
        out.append((max(0.0, s if ns is None else ns), min(dur, e if ne is None else ne)))
    return merge(out)


def smooth(pieces):
    """Remove single-window flicker (DOOR, FIGHT, DOOR -> DOOR) inside a continuous stretch.
    Speech, vocal and level decisions are never changed."""
    fixed = {"SPEECH", "QUIET", *VOCAL_CATS}
    out = list(pieces)
    for i in range(1, len(out) - 1):
        (s0, e0, l0, p0), (s1, e1, l1, p1), (s2, e2, l2, p2) = out[i - 1], out[i], out[i + 1]
        if (l0 == l2 != l1 and abs(e0 - s1) < 1e-6 and abs(e1 - s2) < 1e-6
                and not ({l0, l1} & fixed) and p1 < max(p0, p2)):
            out[i] = (s1, e1, l0, p1)
    return out


def write_diagnostics(path, rows):
    """One CSV per take: what the models heard in every window (for tuning on real material)"""
    import csv
    folder = cfg.DIAG_DIR
    if not folder or not rows:
        return
    os.makedirs(folder, exist_ok=True)
    name = os.path.splitext(os.path.basename(path or "take"))[0] + ".csv"
    with open(os.path.join(folder, name), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["start_s", "end_s", "where", "label", "prob", "top1", "p1", "top2", "p2", "top3", "p3",
                    "top4", "p4", "top5", "p5"])
        for s, e, where, l, p, top in rows:
            flat = []
            for n, q in (top or [])[:5]:
                flat += [n, f"{q:.3f}"]
            w.writerow([f"{s:.2f}", f"{e:.2f}", where, l, f"{p:.3f}"] + flat)


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


def analyse(info, raw, an, chans, path=None):
    """One take -> (runs of usable sound with labelled events, duration).

    path enables the sibling lookup (lavs in separate files) and the diagnostics CSV."""
    dur = info.n_frames / info.sr
    speech = speech_regions(info, raw, an, path)
    cut = merge([(max(0, s - cfg.SPEECH_PAD_S), min(dur, e + cfg.SPEECH_PAD_S)) for s, e in speech])
    cut = snap_cuts(cut, *envelope(raw, info, chans), dur)
    keep = [k for k in invert(cut, dur) if k[1] - k[0] >= cfg.MIN_SEGMENT_S]

    owned, wins = [], []
    for s, e in keep:
        t = s
        while t < e - 1e-6:
            oe = t + cfg.HOP_S
            if e - oe < cfg.HOP_S * 0.5:
                oe = e
            ws, we = t, min(t + cfg.WIN_S, e)
            if we - ws < cfg.WIN_S:
                ws = max(s, we - cfg.WIN_S)
            owned.append((t, oe))
            wins.append((ws, we))
            t = oe
    audio = [mono(raw, info, int(ws * info.sr), int(we * info.sr), 48000, chans) for ws, we in wins]
    labels = an.classify(audio) if audio else []
    pieces = [(s, e, l, p) for (s, e), (l, p) in zip(owned, labels)]
    tops = list(getattr(an, "last_top", None) or [None] * len(owned))
    diag = [(s, e, "kept", l, p, t) for (s, e, l, p), t in zip(pieces, tops)]
    embs = getattr(an, "last_emb", None) or [None] * len(owned)
    info.piece_embs = [(s, e, em) for (s, e), em in zip(owned, embs)]

    # The speech detector also fires on screams, cries and laughs. Check what it marked:
    # vocal sounds are kept as their own category (VOICES), real dialogue stays cut.
    if cfg.VOCAL_MODE == "keep" and speech:
        v_owned, v_wins = [], []
        for s, e in speech:
            t = s
            while t < e - 1e-6:
                oe = min(e, t + cfg.HOP_S)
                if e - oe < cfg.HOP_S * 0.5:
                    oe = e
                ws, we = t, min(dur, max(oe, t + cfg.WIN_S))
                v_owned.append((t, oe))
                v_wins.append((ws, we))
                t = oe
        v_audio = [mono(raw, info, int(ws * info.sr), int(we * info.sr), 48000, chans) for ws, we in v_wins]
        v_labels = an.classify(v_audio) if v_audio else []
        v_embs = getattr(an, "last_emb", None) or [None] * len(v_owned)
        v_tops = list(getattr(an, "last_top", None) or [None] * len(v_owned))
        diag += [(s, e, "speech", l, p, t) for (s, e), (l, p), t in zip(v_owned, v_labels, v_tops)]
        for (s, e), (l, p), em in zip(v_owned, v_labels, v_embs):
            if l in VOCAL_CATS:
                pieces.append((s, e, l, p))
                info.piece_embs.append((s, e, em))
        pieces.sort(key=lambda x: x[0])
    pieces = smooth(pieces)
    if cfg.DIAGNOSTICS:
        write_diagnostics(path, sorted(diag, key=lambda d: d[0]))

    runs = []
    cut = set(cfg.CUT_LABELS) | (set() if cfg.MODE == "dx" else {"QUIET"}) | (VOCAL_CATS if cfg.VOCAL_MODE == "cut" else set())
    for s, e, l, p in pieces:
        if l in cut:
            continue
        if runs and abs(runs[-1]["end"] - s) < 1e-6:
            runs[-1]["end"] = e
            runs[-1]["pieces"].append((s, e, l, p))
        else:
            runs.append({"start": s, "end": e, "pieces": [(s, e, l, p)]})
    runs = [r for r in runs if r["end"] - r["start"] >= cfg.MIN_SEGMENT_S
            or all(l in VOCAL_CATS for _, _, l, _ in r["pieces"])]   # a short scream is still a scream

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
