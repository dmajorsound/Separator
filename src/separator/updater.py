"""Separator – updates straight from GitHub.

The footer asks GitHub once per start whether a newer release exists (check). If the release has an
installer for this system (Separator-x.y-mac.dmg) it is downloaded in the background into a folder
the user can choose (Downloads by default), written as "<file>.part" and only renamed when it arrived
whole. Then the installer opens from the disk image.

No UI code here: app.py hands the state to the web page, main.py --update-test runs the same code in
the GitHub build.
"""

import os
import re
import subprocess
import threading
import urllib.request

REPO = "dmajorsound/Separator"
ASSET_PREFIX = f"https://github.com/{REPO}/releases/download/"
ASSET_SUFFIX = "-mac.dmg"        # Mac only; a Windows build would use "-win64.exe"
STALL_S = 30                     # give up when nothing arrives for this long
MOUNT = "/tmp/separator-update"
APP_NAME = "Separator"


def _num(v):
    return tuple(int(x) for x in re.findall(r"\d+", str(v))[:3])


def pick_asset(assets):
    """The installer for this system among a release's assets, or "" (only GitHub release addresses)"""
    for a in assets or []:
        url = str(a.get("browser_download_url", ""))
        name = url.rsplit("/", 1)[-1].lower()
        if url.startswith(ASSET_PREFIX) and name.endswith(ASSET_SUFFIX) and "uninstall" not in name:
            return url
    return ""


def check(version, timeout=4):
    """{"latest", "url", "asset"} when GitHub has a newer release, else {} (also when offline)"""
    import json
    try:
        req = urllib.request.Request(f"https://api.github.com/repos/{REPO}/releases/latest",
                                     headers={"Accept": "application/vnd.github+json", "User-Agent": APP_NAME})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            d = json.load(r)
    except Exception:
        return {}
    tag = str(d.get("tag_name", "")).lstrip("vV")
    if not tag or _num(tag) <= _num(version):
        return {}
    return {"latest": tag, "url": d.get("html_url") or f"https://github.com/{REPO}/releases/latest",
            "asset": pick_asset(d.get("assets"))}


def default_folder():
    return os.path.join(os.path.expanduser("~"), "Downloads")


class Download:
    """One background download. state: 0 not started, 1 running, 2 there (path), 3 failed (error)"""

    def __init__(self):
        self.state, self.progress, self.path, self.error = 0, 0.0, "", ""
        self.install = False
        self._lock = threading.Lock()

    def snapshot(self):
        with self._lock:
            return {"state": self.state, "progress": self.progress, "path": self.path, "error": self.error}

    def start(self, url, folder, install=False, on_done=None):
        """Starts in a thread; a second call while one is running does nothing"""
        with self._lock:
            if self.state == 1:
                return False
            self.state, self.progress, self.path, self.error, self.install = 1, 0.0, "", "", install
        threading.Thread(target=self._run, args=(url, folder, on_done), daemon=True).start()
        return True

    def run_blocking(self, url, folder):
        with self._lock:
            self.state = 1
        self._run(url, folder, None)
        return self.snapshot()

    def _set(self, **kw):
        with self._lock:
            for k, v in kw.items():
                setattr(self, k, v)

    def _run(self, url, folder, on_done):
        part = ""
        try:
            if not url.startswith(ASSET_PREFIX):
                raise ValueError("not a GitHub release address")
            os.makedirs(folder, exist_ok=True)
            name = url.rsplit("/", 1)[-1]
            final = os.path.join(folder, name)
            part = final + ".part"
            req = urllib.request.Request(url, headers={"User-Agent": APP_NAME, "Accept": "application/octet-stream"})
            # urllib follows GitHub's redirect to objects.githubusercontent.com; the timeout applies to
            # every single read, so a stalled connection fails after STALL_S
            with urllib.request.urlopen(req, timeout=STALL_S) as r:
                total = int(r.headers.get("Content-Length") or 0)
                got = 0
                self._set(progress=0.0 if total else -1.0)
                with open(part, "wb") as f:
                    while True:
                        chunk = r.read(1 << 16)
                        if not chunk:
                            break
                        f.write(chunk)
                        got += len(chunk)
                        if total:
                            self._set(progress=min(1.0, got / total))
            if total and got != total:
                raise IOError(f"incomplete: {got} of {total} bytes")
            if got == 0:
                raise IOError("nothing arrived")
            os.replace(part, final)
            self._set(state=2, progress=1.0, path=final)
        except Exception as e:
            if part and os.path.exists(part):
                try:
                    os.remove(part)
                except OSError:
                    pass
            msg = str(getattr(e, "reason", "") or e) or e.__class__.__name__
            self._set(state=3, error=msg)
        if self.state == 2 and self.install:
            try:
                open_installer(self.path)     # a failure here leaves the download in place
            except Exception:
                pass
        if on_done:
            on_done(self.snapshot())


def open_installer(path):
    """Mounts the disk image quietly and opens "Install Separator.pkg"; if that fails, opens the image.
    Every program gets an argument list, no shell."""
    if not path or not os.path.isfile(path):
        return False
    if path.lower().endswith(".dmg"):
        try:
            subprocess.run(["hdiutil", "detach", "-quiet", "-force", MOUNT], capture_output=True, timeout=20)
            subprocess.run(["hdiutil", "attach", "-nobrowse", "-noautoopen", "-quiet", "-mountpoint", MOUNT, path],
                           check=True, capture_output=True, timeout=120)
            pkg = os.path.join(MOUNT, f"Install {APP_NAME}.pkg")
            if os.path.exists(pkg):
                subprocess.run(["open", pkg], check=True, timeout=20)
                return True
        except Exception:
            pass
    subprocess.run(["open", path])
    return True


def reveal(path):
    """Shows the file selected in the Finder"""
    if path and os.path.exists(path):
        subprocess.run(["open", "-R", path])
        return True
    return False


def self_test(url, folder):
    """Used by the GitHub build: downloads a released installer with exactly this code. 0 = fine"""
    snap = Download().run_blocking(url, folder)
    if snap["state"] != 2:
        print(f"download failed: {snap['error']}")
        return 1
    size = os.path.getsize(snap["path"])
    print(f"downloaded {snap['path']} ({size} bytes)")
    return 0 if size > 500_000 else 1
