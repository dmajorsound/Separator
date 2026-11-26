# Separator

In-house Mac app by dmajorsound. Separator removes dialogue and slates from production sound (set recordings) and keeps the usable sound effects as UCS-named Broadcast WAV files with regions.

## What it does
- Detects speech (Silero VAD) and sound categories (CLAP) locally on the Mac. Nothing leaves the machine.
- Writes one sausage file per category and take (e.g. `DOORMisc_Doors x3_DMS_Project_Sc41-Tk2.wav`) with silence between regions, or keeps wild tracks ("Nur-Ton", NT, AT, WT) continuous and only cuts slates and dialogue.
- After a run, groups similar sounds from all takes into project sausages (e.g. `DOORWood_Wooden Doors x16_DMS_Project.wav`), ordered by sound similarity.
- Keeps all original metadata and adds UCS fields for Soundminer and BaseHead (iXML USER: CATID, CATEGORY, SUBCATEGORY, FXNAME, DESCRIPTION, KEYWORDS …), bext and RIFF INFO. Region labels: `CatID | German term | original timecode`.
- Per-project track filter (use Boom, Atmo, Plant, wild tracks; drop Lav, ISO, Mix), optional UCS folders, single-track split, min/max file length, WAV or WavPack.

## Download
Get `Separator-X.Y-mac.dmg` from the [latest release](https://github.com/dmajorsound/Separator/releases/latest). The disk image holds **Install Separator**, **Uninstall Separator** and **Blocked? Open Privacy & Security** for the one-time macOS block (the installer is free and not signed by Apple). The AI models (about 1 GB) download once on first use.

Builds come from GitHub Actions (`.github/workflows/build.yml`): every push to `main` builds the app and the disk image (with a self-test inside the finished app), every push to `main` publishes or updates the release for the version in `VERSION`.

## Code layout
`src/separator/`, each module only uses the ones above it:

| Module | What it does |
|---|---|
| `config.py` | every setting, in one place |
| `wavio.py` | WAV/BWF/RF64 reading and writing, markers, fades, WavPack |
| `ucs.py` | UCS categories, AudioSet mapping, German terms, file names, descriptions |
| `metadata.py` | recorder metadata (bext, iXML, INFO), track filter |
| `analysis.py` | AI models (speech, sound classes, fingerprints) and per-take analysis |
| `views.py` | data for the track view and the take list |
| `files.py` | source files, drag and drop, done list, track-name scan |
| `fx_library.py` | **feature** FX Library: UCS FX files, sausages, project sausages |
| `dx_delete.py` | **feature** DX Delete: same files and names, only dialogue cut |
| `engine.py` | run loop for both features, loads the feature modules on demand |
| `edition.py` | which features an app contains |
| `fx_core.py` | entry point the windows import (re-exports the modules) |
| `app.py`, `web/index.html` | main window |
| `player.py` | preview playback |
| `gui.py` | simple fallback window |
| `main.py` | start and build self-test |

## Editions: one feature as its own app
The same code builds three apps: **Separator** (both tabs, the release), **Separator FX** (FX Library only) and **Separator DX** (DX Delete only). A single-feature app does not contain the other feature's module, which helps to track down a bug.

- From source: `SEPARATOR_EDITION=dx python src/separator/main.py`
- On GitHub: Actions › Build Separator › Run workflow › edition `fx` or `dx`. The app comes as a zip in the run's artifacts; only `full` is released.

V 1.2 - developed with AI assistance · dmajorsound.com
