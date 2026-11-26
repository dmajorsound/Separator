# PyInstaller build for Separator.app (run from the repo root: pyinstaller packaging/Separator.spec)
import os
import shutil
from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata

ROOT = os.path.abspath(".")
SRC = os.path.join(ROOT, "src", "separator")
VERSION = open(os.path.join(ROOT, "VERSION")).read().strip()

datas = [(os.path.join(SRC, "web"), "web")]
datas += collect_data_files("silero_vad")
datas += collect_data_files("webview")
datas += collect_data_files("_sounddevice_data")   # bundled PortAudio for preview playback
for pkg in ("transformers", "tokenizers", "huggingface_hub", "safetensors", "torch", "tqdm",
            "regex", "requests", "packaging", "filelock", "numpy", "pyyaml"):
    try:
        datas += copy_metadata(pkg)
    except Exception:
        pass

binaries = []
wv = shutil.which("wavpack")
if wv:
    binaries.append((wv, "bin"))   # WavPack-Programm mitliefern; Abhaengigkeiten sammelt PyInstaller

hiddenimports = (collect_submodules("transformers.models.clap") + collect_submodules("silero_vad")
                 + ["webview.platforms.cocoa", "scipy.signal", "sounddevice", "_sounddevice_data"])

a = Analysis(
    [os.path.join(SRC, "main.py")],
    pathex=[SRC],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["tkinter.test", "matplotlib", "IPython", "pandas", "tensorflow", "jax"],
    module_collection_mode={"torch": "pyz+py", "transformers": "pyz+py", "silero_vad": "pyz+py"},
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="Separator", console=False,
          argv_emulation=False, target_arch="arm64", codesign_identity=None)
coll = COLLECT(exe, a.binaries, a.datas, name="Separator")
app = BUNDLE(
    coll,
    name="Separator.app",
    icon=os.path.join(ROOT, "assets", "Separator.icns") if os.path.exists(os.path.join(ROOT, "assets", "Separator.icns")) else None  # built in CI from Separator_icon_1024.png,
    bundle_identifier="com.dmajorsound.separator",
    version=VERSION,
    info_plist={
        "CFBundleName": "Separator",
        "CFBundleDisplayName": "Separator",
        "CFBundleShortVersionString": VERSION,
        "CFBundleVersion": VERSION,
        "LSMinimumSystemVersion": "12.0",
        "NSHighResolutionCapable": True,
        "NSHumanReadableCopyright": "dmajorsound.com",
    },
)
