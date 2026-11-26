"""Separator – public entry point for the app windows and the self-test.

Re-exports the engine's functions from the modules below, so app.py, gui.py, player.py and
main.py only import fx_core. Settings are NOT here: read and change them in config.

Module layers (each one only uses the ones above it):
    config      settings
    wavio       WAV/BWF/RF64 reading and writing, markers, WavPack
    ucs         UCS categories, names, descriptions
    metadata    recorder metadata (bext, iXML, INFO), track filter
    analysis    AI models and per-take analysis
    views       data for the track view and the take list
    files       source files, drag and drop, done list
    fx_library  feature: UCS FX files and project sausages
    dx_delete   feature: same files, only dialogue cut
    engine      run loop for both features, loads the feature modules on demand
    edition     which features this app contains (full, FX only, DX only)
"""

from wavio import (WavInfo, read_wav_info, decode, encode, frames_bytes, mono, mono_full, _chunk,  # noqa: F401
                   marker_chunks, write_bwf, peaks, wavpack_bin, to_wavpack, tc)
from ucs import (LABELS, UCS, DE_NAMES, AUDIOSET_MAP, VOCAL_CATS, ucs_name, description)  # noqa: F401
from metadata import (read_meta, classify_track, select_channels, new_bext, new_ixml, list_info)  # noqa: F401
from analysis import Analyzer, analyse  # noqa: F401
from views import file_view, file_summary  # noqa: F401
from files import (read_done, is_audio, expand_paths, common_root, find_files, scan_tracks)  # noqa: F401
from engine import feature, preview, run  # noqa: F401   feature("library") / feature("dx") = feature module
import edition  # noqa: F401,E402
