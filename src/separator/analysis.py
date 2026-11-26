"""Separator – AI analysis.

Analyzer loads the models (Silero VAD for speech, an AudioSet classifier for sound classes, CLAP for
sound fingerprints). analyse() turns one take into runs of usable sound with labelled events.
"""

import os

import numpy as np

import config as cfg
from ucs import AUDIOSET_MAP, LABELS, VOCAL_CATS
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
        out = []
        for pr in prob:
            best = {}
            for cat, i in self.tag_map:
                if pr[i] > best.get(cat, 0.0):
                    best[cat] = float(pr[i])
            # sounds that are clearly there beat speech that is only faintly there (and vice versa)
            cat, p = max(best.items(), key=lambda kv: kv[1]) if best else (cfg.UNKNOWN_CAT, 0.0)
            out.append((cat if p >= cfg.TAG_MIN_PROB else cfg.UNKNOWN_CAT, p))
        return out

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
        ts = self._gst(wav, self.vad_model, sampling_rate=16000, threshold=cfg.VAD_THRESHOLD)
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
                    for (i, _), t in zip(batch, tags):
                        out[i] = t
                    continue
            prob = per.softmax(dim=-1).cpu().numpy()
            for (i, _), pr in zip(batch, prob):
                k = int(pr.argmax())
                out[i] = (self.names[k] if pr[k] >= cfg.MIN_PROB else cfg.UNKNOWN_CAT, float(pr[k]))
        return out

    def _audio(self, feats):
        with self.torch.no_grad():
            kw = {k: v.to(self.device) for k, v in feats.items() if hasattr(v, "to")}
            a = self._emb(self.model.get_audio_features(**kw))
        return self.torch.nn.functional.normalize(a, dim=-1)


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


def analyse(info, raw, an, chans):
    dur = info.n_frames / info.sr
    speech = an.vad(mono_full(raw, info, 16000, chans))
    cut = merge([(max(0, s - cfg.SPEECH_PAD_S), min(dur, e + cfg.SPEECH_PAD_S)) for s, e in speech])
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
        for (s, e), (l, p), em in zip(v_owned, v_labels, v_embs):
            if l in VOCAL_CATS:
                pieces.append((s, e, l, p))
                info.piece_embs.append((s, e, em))
        pieces.sort(key=lambda x: x[0])

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
