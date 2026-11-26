"""Separator – App-Fenster"""

import os
import sys
import json
import time
import queue
import threading
import subprocess
import tkinter as tk
from tkinter import ttk, filedialog

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fx_core  # noqa: E402

# ------------------------------------------------------------
# Farben/Schrift – hier an OLAAF / Binocular anpassen
# ------------------------------------------------------------
THEME = {
    "bg":      "#161616",   # Hintergrund
    "panel":   "#1F1F1F",   # Felder
    "line":    "#2C2C2C",   # Rahmen
    "text":    "#E6E6E6",
    "dim":     "#8A8A8A",
    "accent":  "#2F6FC4",   # Akzent (Buttons, Fortschritt)
    "accent_text": "#FFFFFF",
    "ok":      "#7BD88F",
    "err":     "#FF6B6B",
    "font":    "Helvetica Neue",
    "mono":    "Menlo",
}

APP_DIR = os.path.expanduser("~/Library/Application Support/Separator")
SETTINGS = os.path.join(APP_DIR, "settings.json")


def load_settings():
    try:
        with open(SETTINGS, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_settings(d):
    try:
        os.makedirs(APP_DIR, exist_ok=True)
        with open(SETTINGS, "w", encoding="utf-8") as f:
            json.dump(d, f, indent=2)
    except Exception:
        pass


class App:
    def __init__(self, root):
        self.root = root
        self.q = queue.Queue()
        self.worker = None
        self.stop_flag = False
        self.analyzer = None
        self.t_start = None
        T = THEME
        s = load_settings()

        root.title("Separator")
        root.configure(bg=T["bg"])
        root.minsize(820, 700)
        root.geometry("920x780")

        st = ttk.Style()
        st.theme_use("clam")
        st.configure("TProgressbar", troughcolor=T["panel"], background=T["accent"],
                     bordercolor=T["line"], lightcolor=T["accent"], darkcolor=T["accent"])
        st.configure("TCheckbutton", background=T["bg"], foreground=T["text"],
                     font=(T["font"], 12))
        st.map("TCheckbutton", background=[("active", T["bg"])],
               indicatorcolor=[("selected", T["accent"]), ("!selected", T["panel"])])

        pad = {"padx": 24}
        head = tk.Frame(root, bg=T["bg"])
        head.pack(fill="x", pady=(22, 4), **pad)
        tk.Label(head, text="Separator", bg=T["bg"], fg=T["text"],
                 font=(T["font"], 24, "bold")).pack(side="left")
        tk.Label(head, text="Set-Ton → FX-Library · Dialog raus · UCS", bg=T["bg"],
                 fg=T["dim"], font=(T["font"], 12)).pack(side="left", padx=14, pady=(8, 0))

        form = tk.Frame(root, bg=T["bg"])
        form.pack(fill="x", pady=(14, 0), **pad)
        form.columnconfigure(1, weight=1)
        self.src = tk.StringVar(value=s.get("src", ""))
        self.dst = tk.StringVar(value=s.get("dst", ""))
        self.creator = tk.StringVar(value=s.get("creator", fx_core.CREATOR_ID))
        self.test = tk.BooleanVar(value=s.get("test", True))
        self.fmt = tk.StringVar(value=s.get("fmt", "wav"))
        self.min_s = tk.StringVar(value=str(s.get("min_s", fx_core.MIN_FILE_S)))
        self.max_s = tk.StringVar(value=str(s.get("max_s", fx_core.MAX_FILE_S)))
        self.sort_ucs = tk.BooleanVar(value=False)
        self.split = tk.BooleanVar(value=False)
        self.keep_w = tk.StringVar()
        self.drop_w = tk.StringVar()
        self.unknown = tk.StringVar()
        self.projects = s.get("projects", {})
        self._load_project(self.src.get())
        self._row(form, 0, "Quelle", self.src, self._pick_src)
        self._row(form, 1, "Ziel", self.dst, lambda: self._pick(self.dst, "Zielordner für die FX-Files"))

        tk.Label(form, text="Creator ID", bg=T["bg"], fg=T["dim"], font=(T["font"], 12),
                 anchor="w").grid(row=2, column=0, sticky="w", pady=6)
        opt = tk.Frame(form, bg=T["bg"])
        opt.grid(row=2, column=1, columnspan=2, sticky="w")
        self._entry(opt, self.creator, width=10).pack(side="left")
        ttk.Checkbutton(opt, text="Testlauf: nur die ersten 20 Files",
                        variable=self.test).pack(side="left", padx=18)

        tk.Label(form, text="Format", bg=T["bg"], fg=T["dim"], font=(T["font"], 12),
                 anchor="w").grid(row=3, column=0, sticky="w", pady=6)
        opt2 = tk.Frame(form, bg=T["bg"])
        opt2.grid(row=3, column=1, columnspan=2, sticky="w")
        self.fmt_btns = {}
        for val, txt in (("wav", "WAV"), ("wavpack", "WavPack")):
            b = tk.Label(opt2, text=txt, cursor="hand2", font=(T["font"], 13), padx=16, pady=5)
            b.bind("<Button-1>", lambda _e, v=val: self._set_fmt(v))
            b.pack(side="left", padx=(0, 4))
            self.fmt_btns[val] = b
        self._set_fmt(self.fmt.get())
        tk.Label(opt2, text="File-Länge  min", bg=T["bg"], fg=T["dim"],
                 font=(T["font"], 12)).pack(side="left", padx=(22, 6))
        self._entry(opt2, self.min_s, width=5).pack(side="left")
        tk.Label(opt2, text="s   max", bg=T["bg"], fg=T["dim"],
                 font=(T["font"], 12)).pack(side="left", padx=6)
        self._entry(opt2, self.max_s, width=5).pack(side="left")
        tk.Label(opt2, text="s  (0 = unbegrenzt)", bg=T["bg"], fg=T["dim"],
                 font=(T["font"], 12)).pack(side="left", padx=6)

        tk.Label(form, text="Ordner", bg=T["bg"], fg=T["dim"], font=(T["font"], 12),
                 anchor="w").grid(row=4, column=0, sticky="w", pady=6)
        opt3 = tk.Frame(form, bg=T["bg"])
        opt3.grid(row=4, column=1, columnspan=2, sticky="w")
        ttk.Checkbutton(opt3, text="In UCS-Ordner sortieren (CATEGORY/SUBCATEGORY)",
                        variable=self.sort_ucs).pack(side="left")
        ttk.Checkbutton(opt3, text="Mehrspur-Files in Einzelspuren splitten",
                        variable=self.split).pack(side="left", padx=18)

        sep = tk.Frame(root, bg=T["line"], height=1)
        sep.pack(fill="x", padx=24, pady=(14, 0))
        flt = tk.Frame(root, bg=T["bg"])
        flt.pack(fill="x", padx=24, pady=(10, 0))
        flt.columnconfigure(1, weight=1)
        tk.Label(flt, text="SPURFILTER für dieses Projekt", bg=T["bg"], fg=T["text"],
                 font=(T["font"], 12, "bold")).grid(row=0, column=0, columnspan=2, sticky="w")
        self._button(flt, "Spuren scannen", self.scan).grid(row=0, column=2, sticky="e")
        tk.Label(flt, text="Verwenden", bg=T["bg"], fg=T["ok"], font=(T["font"], 12),
                 anchor="w", width=10).grid(row=1, column=0, sticky="w", pady=(8, 4))
        self._entry(flt, self.keep_w).grid(row=1, column=1, columnspan=2, sticky="ew", ipady=4, pady=(8, 4))
        tk.Label(flt, text="Löschen", bg=T["bg"], fg=T["err"], font=(T["font"], 12),
                 anchor="w", width=10).grid(row=2, column=0, sticky="w", pady=4)
        self._entry(flt, self.drop_w).grid(row=2, column=1, columnspan=2, sticky="ew", ipady=4, pady=4)
        tk.Label(flt, text="Unbekannte", bg=T["bg"], fg=T["dim"], font=(T["font"], 12),
                 anchor="w", width=10).grid(row=3, column=0, sticky="w", pady=4)
        unk = tk.Frame(flt, bg=T["bg"])
        unk.grid(row=3, column=1, columnspan=2, sticky="w")
        for val, txt in (("fallback", "nur wenn sonst keine Spur passt"), ("keep", "immer verwenden"),
                         ("drop", "immer löschen")):
            tk.Radiobutton(unk, text=txt, value=val, variable=self.unknown, bg=T["bg"], fg=T["text"],
                           selectcolor=T["panel"], activebackground=T["bg"], activeforeground=T["text"],
                           highlightthickness=0, font=(T["font"], 12)).pack(side="left", padx=(0, 14))
        tk.Label(flt, text="Wörter mit Komma trennen. Treffer am Wortanfang: „lav“ erwischt Lav1, Lav Hans. "
                           "Verwenden schlägt Löschen („Boom ISO“ bleibt).", bg=T["bg"], fg=T["dim"],
                 font=(T["font"], 11), anchor="w", justify="left").grid(row=4, column=0, columnspan=3, sticky="w", pady=(4, 0))

        bar = tk.Frame(root, bg=T["bg"])
        bar.pack(fill="x", pady=(18, 0), **pad)
        self.btn = self._button(bar, "Start", self.start, primary=True)
        self.btn.pack(side="left")
        self._button(bar, "Zielordner öffnen", self.open_dst).pack(side="left", padx=10)
        self.status = tk.Label(bar, text="Bereit", bg=T["bg"], fg=T["dim"], font=(T["font"], 12))
        self.status.pack(side="right")

        self.prog = ttk.Progressbar(root, mode="determinate", maximum=1)
        self.prog.pack(fill="x", pady=(14, 0), **pad)

        box = tk.Frame(root, bg=T["line"], padx=1, pady=1)
        box.pack(fill="both", expand=True, pady=(14, 22), **pad)
        self.log = tk.Text(box, bg=T["panel"], fg=T["text"], insertbackground=T["text"],
                           relief="flat", bd=0, wrap="word", font=(T["mono"], 11),
                           padx=12, pady=10, highlightthickness=0)
        self.log.pack(fill="both", expand=True)
        self.log.tag_config("err", foreground=T["err"])
        self.log.tag_config("ok", foreground=T["ok"])
        self.log.tag_config("dim", foreground=T["dim"])
        self.log.configure(state="disabled")
        self._write("Quelle und Ziel wählen, dann Start. Beim ersten Start werden die "
                    "KI-Modelle geladen, das dauert etwas.", "dim")

        root.protocol("WM_DELETE_WINDOW", self.close)
        root.after(100, self.poll)

    # ---------- Bausteine ----------
    def _entry(self, parent, var, width=None):
        T = THEME
        e = tk.Entry(parent, textvariable=var, bg=T["panel"], fg=T["text"], relief="flat",
                     insertbackground=T["text"], highlightthickness=1,
                     highlightbackground=T["line"], highlightcolor=T["accent"],
                     font=(T["font"], 13))
        if width:
            e.configure(width=width)
        return e

    def _row(self, parent, r, label, var, cmd):
        T = THEME
        tk.Label(parent, text=label, bg=T["bg"], fg=T["dim"], font=(T["font"], 12),
                 anchor="w", width=10).grid(row=r, column=0, sticky="w", pady=6)
        self._entry(parent, var).grid(row=r, column=1, sticky="ew", ipady=5)
        self._button(parent, "Wählen …", cmd).grid(row=r, column=2, padx=(10, 0))

    def _button(self, parent, text, cmd, primary=False):
        # Label statt Button, weil macOS-Buttons keine eigenen Farben zulassen
        T = THEME
        bg = T["accent"] if primary else T["panel"]
        fg = T["accent_text"] if primary else T["text"]
        b = tk.Label(parent, text=text, bg=bg, fg=fg, cursor="hand2",
                     font=(T["font"], 13, "bold" if primary else "normal"), padx=18, pady=7,
                     highlightthickness=0 if primary else 1, highlightbackground=T["line"])
        b.bind("<Button-1>", lambda _e: cmd())
        b._cmd = cmd
        return b

    def _words(self, var):
        return [w.strip() for w in var.get().replace(";", ",").split(",") if w.strip()]

    def _load_project(self, src):
        p = self.projects.get(os.path.abspath(src) if src else "", {})
        self.keep_w.set(p.get("keep", ", ".join(fx_core.KEEP_TRACKS)))
        self.drop_w.set(p.get("drop", ", ".join(fx_core.IGNORE_TRACKS)))
        self.unknown.set(p.get("unknown", fx_core.UNKNOWN_TRACKS))
        self.sort_ucs.set(p.get("sort_ucs", False))
        self.split.set(p.get("split", False))

    def _project_now(self):
        return {"keep": self.keep_w.get(), "drop": self.drop_w.get(), "unknown": self.unknown.get(),
                "sort_ucs": self.sort_ucs.get(), "split": self.split.get()}

    def _apply_filter(self):
        fx_core.KEEP_TRACKS = [w.lower() for w in self._words(self.keep_w)]
        fx_core.IGNORE_TRACKS = [w.lower() for w in self._words(self.drop_w)]
        fx_core.UNKNOWN_TRACKS = self.unknown.get()

    def scan(self):
        src = self.src.get().strip()
        if not os.path.isdir(src):
            self._write("Bitte zuerst einen Quellordner wählen.", "err")
            return
        self._apply_filter()
        self._write("Scanne Spurnamen (nur Header) …", "dim")

        def work():
            n, counts = fx_core.scan_tracks(src)
            self.q.put(("log", f"{n} Files, {len(counts)} verschiedene Spurnamen:"))
            for name, c in counts:
                cls = fx_core.classify_track(name) if not name.startswith("(") else "keep"
                mark = {"keep": "✓ verwenden", "drop": "✗ löschen", "unknown": "? unbekannt"}[cls]
                self.q.put(("log", f"   {mark:13} {name}  ({c}×)"))
            self.q.put(("log", "Filter anpassen und nochmal scannen, bis alles passt."))
        threading.Thread(target=work, daemon=True).start()

    def _set_fmt(self, v):
        T = THEME
        self.fmt.set(v)
        for k, b in self.fmt_btns.items():
            on = k == v
            b.configure(bg=T["accent"] if on else T["panel"], fg=T["accent_text"] if on else T["text"])

    def _num(self, var, default):
        try:
            return float(var.get().replace(",", "."))
        except ValueError:
            return default

    def _pick_src(self):
        self._pick(self.src, "Ordner mit den Settönen")
        self._load_project(self.src.get())

    def _pick(self, var, title):
        d = filedialog.askdirectory(title=title, initialdir=var.get() or os.path.expanduser("~"))
        if d:
            var.set(d)

    def _write(self, text, tag=None):
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n", tag)
        self.log.see("end")
        self.log.configure(state="disabled")

    # ---------- Ablauf ----------
    def start(self):
        if self.worker and self.worker.is_alive():
            self.stop_flag = True
            self.status.configure(text="Stoppe nach diesem File …")
            return
        src, dst = self.src.get().strip(), self.dst.get().strip()
        if not os.path.isdir(src):
            self._write("Bitte einen gültigen Quellordner wählen.", "err")
            return
        if not dst:
            self._write("Bitte einen Zielordner wählen.", "err")
            return
        if os.path.abspath(dst) == os.path.abspath(src):
            self._write("Ziel darf nicht der Quellordner sein.", "err")
            return
        self.projects[os.path.abspath(src)] = self._project_now()
        save_settings({"projects": self.projects, "src": src, "dst": dst, "creator": self.creator.get().strip(),
                       "test": self.test.get(), "fmt": self.fmt.get(),
                       "min_s": self._num(self.min_s, fx_core.MIN_FILE_S),
                       "max_s": self._num(self.max_s, fx_core.MAX_FILE_S)})
        self.stop_flag = False
        self.t_start = time.time()
        self.btn.configure(text="Stop")
        self.status.configure(text="Läuft …")
        self.worker = threading.Thread(target=self._work, args=(src, dst), daemon=True)
        self.worker.start()

    def _work(self, src, dst):
        try:
            if self.analyzer is None:
                self.analyzer = fx_core.Analyzer(log=lambda m: self.q.put(("log", m)))
            fx_core.run(src, dst, creator=self.creator.get().strip() or fx_core.CREATOR_ID,
                        limit=20 if self.test.get() else 0,
                        log=lambda m: self.q.put(("log", m)),
                        progress=lambda i, n: self.q.put(("prog", (i, n))),
                        stop=lambda: self.stop_flag, analyzer=self.analyzer,
                        min_s=self._num(self.min_s, fx_core.MIN_FILE_S),
                        max_s=self._num(self.max_s, fx_core.MAX_FILE_S),
                        fmt=self.fmt.get(), sort_ucs=self.sort_ucs.get(), split=self.split.get(),
                        keep_words=self._words(self.keep_w), drop_words=self._words(self.drop_w),
                        unknown=self.unknown.get())
        except Exception as ex:
            self.q.put(("log", f"FEHLER: {ex}"))
        self.q.put(("done", None))

    def poll(self):
        try:
            while True:
                kind, val = self.q.get_nowait()
                if kind == "log":
                    tag = "err" if "FEHLER" in val else ("ok" if val.startswith("Fertig") else
                                                          ("dim" if "erledigt" in val else None))
                    self._write(val, tag)
                elif kind == "prog":
                    i, n = val
                    self.prog.configure(maximum=max(n, 1), value=i)
                    eta = ""
                    if 0 < i < n:
                        rest = (time.time() - self.t_start) / i * (n - i)
                        h, m = divmod(int(rest // 60), 60)
                        eta = f" · noch ca. {h} h {m} min" if h else f" · noch ca. {m} min"
                    self.status.configure(text=f"{i} / {n}{eta}")
                elif kind == "done":
                    self.btn.configure(text="Start")
                    if not self.stop_flag:
                        self.status.configure(text="Fertig")
                    else:
                        self.status.configure(text="Gestoppt")
        except queue.Empty:
            pass
        self.root.after(150, self.poll)

    def open_dst(self):
        d = self.dst.get().strip()
        if os.path.isdir(d):
            subprocess.run(["open", d])

    def close(self):
        self.stop_flag = True
        self.root.destroy()


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
