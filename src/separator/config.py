"""Separator – settings.

Every adjustable value lives here and only here. The app writes the user's choices into this
module before a run (config.MIN_LEVEL_DBFS = …); all other modules read them at call time
(cfg.MIN_LEVEL_DBFS), so a change takes effect without restarting.

"(app)" marks values the user sets in the window; the others are fixed defaults.
"""

# ---------------------------------------------------------------------------
# Feature
# ---------------------------------------------------------------------------
MODE = "library"            # (app) "library": UCS FX files | "dx": DX Delete – same files, only dialogue cut

# ---------------------------------------------------------------------------
# Cutting
# ---------------------------------------------------------------------------
SPEECH_PAD_S = 0.30         # extra cut before and after every stretch of speech (s)
MIN_SEGMENT_S = 1.5         # pieces between dialogue shorter than this are dropped (s)
FADE_MS = 10                # fade in/out at every cut (ms)
MIN_LEVEL_DBFS = -55.0      # (app) parts whose peak stays below this are cut and never written
VOCAL_MODE = "keep"         # (app) screams, cries, laughs, efforts: "keep" (own VOICES files) or "cut"
CUT_LABELS = {"SPEECH"}     # classifier labels that are cut like dialogue

# ---------------------------------------------------------------------------
# Wild tracks ("Nur-Ton", AT, WT): kept in one piece, only slates and dialogue cut
# ---------------------------------------------------------------------------
WILD_CONTINUOUS = True      # (app)
# Searched in scene, take, note, comments, file name; iXML WILD_TRACK counts too
WILD_PATTERNS = [r"(?i)\bnur[\s_-]?ton\b", r"(?i)\bwild[\s_-]?tracks?\b", r"(?i)\bwild\b",
                 r"\bWT\d*\b", r"\bNT\d*\b", r"\bAT\d*\b", r"(?i)\batmo[\s_-]?take\b"]

# ---------------------------------------------------------------------------
# FX Library output
# ---------------------------------------------------------------------------
GROUP_BY_CAT = True         # (app) one sausage file per category and take
GAP_S = 0.5                 # (app) silence between sounds in a sausage file, 0 = back to back (s)
MIN_FILE_S = 3.0            # (app) shorter results are not written (s)
MAX_FILE_S = 120.0          # (app) longer results are split at sound boundaries, 0 = unlimited (s)
SORT_BY_CATEGORY = False    # (app) destination folders CATEGORY/SUBCATEGORY instead of the source layout
SPLIT_TRACKS = False        # (app) every used track becomes its own mono file
UNCLASSIFIED = ("AMBMisc", "Unclassified")   # CatID + FX name for sounds the classifier is unsure about
FALLBACK_CAT = ("AMBMisc", "AMBIENCE", "MISC")
BUNDLE_AFTER_RUN = True     # (app) build project sausages after every run
BUNDLE_FOLDER = "Project Sausages"
REGIONS_FILE = ".separator_regions.jsonl"    # every written region, the input for project sausages

# UCS file name: CatID_FXName_CreatorID_SourceID_UserNotes.wav
CREATOR_ID = "DMS"          # (app) your ID
SOURCE_ID = "SET"           # replaced by the project name from iXML/bext when there is one

# ---------------------------------------------------------------------------
# Both features
# ---------------------------------------------------------------------------
OUTPUT_FORMAT = "wav"       # (app) "wav" or "wavpack"
WRITE_REGIONS = True        # markers with a length (regions); False = point markers only

# Track filter (app, per source folder). Words match the start of a track name, case-insensitive;
# "use" beats "drop", so "Boom ISO" is used. Files without track names use every track.
KEEP_TRACKS = ["boom", "bm", "atmo", "atmos", "amb", "ambience", "wild", "wt", "plant", "pm", "plt"]
IGNORE_TRACKS = ["lav", "iso", "mix", "mx", "radio", "rf", "tx", "wl", "funk", "sender"]
UNKNOWN_TRACKS = "fallback"  # tracks matching neither list: "fallback" (only if nothing else), "keep", "drop"

# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------
VAD_THRESHOLD = 0.40        # speech detection: lower = more sensitive (cuts more)
VAD_MIN_SPEECH_MS = 200     # shorter speech blips are ignored (a single "ja" is ~200 ms)
VAD_MIN_SILENCE_MS = 250    # pauses shorter than this inside a sentence do not open a gap
USE_SIBLINGS = True         # also detect speech on the other mono files of the same take (lavs)
SNAP_S = 0.15               # cut edges move up to this far to the quietest point nearby (0 = off)
WIN_S = 4.0                 # analysis window of the sound classifier (s)
HOP_S = 2.0                 # analysis step = marker resolution (s)
TAG_MODEL = "MIT/ast-finetuned-audioset-10-10-0.4593"   # sound classes, trained on AudioSet (527 classes)
TAG_MIN_PROB = 0.12         # below this the classifier is unsure -> MISC
AMBIENCE_WEIGHT = 0.6       # room/outside/hum classes count this much against real events
DIAGNOSTICS = False         # (app) write one CSV per take with the top-5 classes of every window
DIAG_DIR = ""               # set by the engine: <destination>/_diagnostics
CLAP_MODEL = "laion/larger_clap_general"                # sound fingerprints (similarity, fine categories)
MIN_PROB = 0.30             # CLAP fallback: below this confidence -> MISC
SILENCE_DBFS = -60.0        # quieter windows are room tone without asking the models
SILENCE_CAT = "AMBRoom"
UNKNOWN_CAT = "MISC"        # marker label for unsure parts (never in a file name)
