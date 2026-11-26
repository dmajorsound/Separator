# Separator

In-house Mac app by dmajorsound. Separator removes dialogue and slates from production sound (set recordings) and keeps the usable sound effects as UCS-named Broadcast WAV files with regions.

## What it does
- Detects speech (Silero VAD) and sound categories (CLAP) locally on the Mac. Nothing leaves the machine.
- Writes one sausage file per category and take (e.g. `DOORMisc_Doors x3_DMS_Project_Sc41-Tk2.wav`) with silence between regions, or keeps wild tracks ("Nur-Ton", NT, AT, WT) continuous and only cuts slates and dialogue.
- After a run, groups similar sounds from all takes into project sausages (e.g. `DOORWood_Wooden Doors x16_DMS_Project.wav`), ordered by sound similarity.
- Keeps all original metadata and adds UCS fields for Soundminer and BaseHead (iXML USER: CATID, CATEGORY, SUBCATEGORY, FXNAME, DESCRIPTION, KEYWORDS …), bext and RIFF INFO. Region labels: `CatID | German term | original timecode`.
- Per-project track filter (use Boom, Atmo, Plant, wild tracks; drop Lav, ISO, Mix), optional UCS folders, single-track split, min/max file length, WAV or WavPack.

## Download
Builds come from GitHub Actions (`.github/workflows/build.yml`): every push to `main` produces a DMG as a workflow artifact, every tag `vX.Y` publishes a release with `Separator_X.Y.dmg`.

First start: right-click `Separator.app` → Open (the app is not notarized). The AI models (about 1 GB) download once on first use.

## Layout
- `src/separator/fx_core.py` – analysis, cutting, UCS naming, metadata, project sausages
- `src/separator/app.py` – window bridge (pywebview)
- `src/separator/web/index.html` – the interface
- `src/separator/gui.py` – simple fallback window
- `packaging/Separator.spec` – PyInstaller build

V 1.2 - developed with AI assistance · dmajorsound.com
