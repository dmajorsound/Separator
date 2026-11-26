"""Separator – which features this app contains.

The same code builds three apps:
    full  "Separator"     FX Library and DX Delete as tabs (default)
    fx    "Separator FX"  FX Library only
    dx    "Separator DX"  DX Delete only
The build writes the edition into edition.txt next to this file (packaging/Separator.spec);
SEPARATOR_EDITION overrides it when running from source, e.g. to debug one feature alone.
"""

import os
import sys

EDITIONS = {
    "full": ("Separator", ["library", "dx"]),
    "fx": ("Separator FX", ["library"]),
    "dx": ("Separator DX", ["dx"]),
}


def _current():
    key = os.environ.get("SEPARATOR_EDITION", "").strip().lower()
    if not key:
        here = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
        try:
            with open(os.path.join(here, "edition.txt"), encoding="utf-8") as f:
                key = f.read().strip().lower()
        except OSError:
            key = "full"
    return key if key in EDITIONS else "full"


KEY = _current()
NAME, FEATURES = EDITIONS[KEY]
