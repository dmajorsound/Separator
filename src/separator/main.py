"""Separator – app entry point (also used by the GitHub build)."""
import os
import sys

os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def selftest():
    """Run by the GitHub build inside the finished app: checks that every package is bundled and the engine runs."""
    import struct
    import tempfile
    import numpy as np
    import fx_core
    import torch  # noqa: F401
    import transformers  # noqa: F401
    import webview  # noqa: F401
    import sounddevice as sd
    print("SELFTEST PortAudio:", sd.get_portaudio_version()[1])
    from silero_vad import load_silero_vad, get_speech_timestamps

    vad = load_silero_vad()
    tmp = tempfile.mkdtemp()
    src, dst = os.path.join(tmp, "src"), os.path.join(tmp, "dst")
    os.makedirs(src)
    sr, n = 48000, 48000 * 12
    rng = np.random.default_rng(1)
    x = np.stack([rng.normal(0, 0.05, n), rng.normal(0, 0.05, n)], 1)
    fmt = struct.pack("<HHIIHH", 1, 2, sr, sr * 6, 6, 24)
    ixml = (b"<BWFXML><PROJECT>Test</PROJECT><SCENE>1</SCENE><TAKE>1</TAKE><TRACK_LIST>"
            b"<TRACK><INTERLEAVE_INDEX>1</INTERLEAVE_INDEX><NAME>Boom</NAME></TRACK>"
            b"<TRACK><INTERLEAVE_INDEX>2</INTERLEAVE_INDEX><NAME>Lav1</NAME></TRACK></TRACK_LIST></BWFXML>")
    body = fx_core._chunk(b"fmt ", fmt) + fx_core._chunk(b"iXML", ixml) + \
        fx_core._chunk(b"data", fx_core.encode(x, 3, False).tobytes())
    open(os.path.join(src, "T001.WAV"), "wb").write(b"RIFF" + struct.pack("<I", 4 + len(body)) + b"WAVE" + body)

    class Stub:
        device = "cpu"

        def vad(self, m):
            ts = get_speech_timestamps(torch.from_numpy(m), vad, sampling_rate=16000)
            return [(t["start"] / 16000, t["end"] / 16000) for t in ts]

        def classify(self, w):
            self.last_emb = [None] * len(w)
            return [("AMBRoom", 0.9) for _ in w]

    import edition
    for mode in edition.FEATURES:                   # every feature this app was built with
        fx_core.run(src, os.path.join(dst, mode), analyzer=Stub(), log=print, bundle=False, mode=mode)
    print("SELFTEST edition:", edition.NAME, edition.FEATURES)
    out = [f for _, _, fs in os.walk(dst) for f in fs if f.endswith(".wav")]
    print("SELFTEST files:", out, "wavpack:", fx_core.wavpack_bin())
    assert out, "keine Ausgabe"
    print("SELFTEST OK")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    else:
        import app
        app.main()
