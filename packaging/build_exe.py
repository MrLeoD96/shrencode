"""Build shrencode.exe and the windowless shrencodew.exe into dist/, then zip them.

    python packaging/build_exe.py
"""

import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from shrencode import __version__  # noqa: E402

entry = ROOT / "packaging" / "entry.py"
dist = ROOT / "dist"
for name, window in (("shrencode", "--console"), ("shrencodew", "--noconsole")):
    subprocess.run([sys.executable, "-m", "PyInstaller", "--onefile", window, "--name", name,
                    "--distpath", str(dist), "--workpath", str(ROOT / "build" / name),
                    "--specpath", str(ROOT / "build"), "--paths", str(ROOT / "src"),
                    "--noconfirm", "--log-level", "WARN", str(entry)], check=True)

ext = ".exe" if sys.platform == "win32" else ""
platform = "windows-x64" if sys.platform == "win32" else sys.platform
archive = dist / f"shrencode-{__version__}-{platform}.zip"
with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as z:
    for name in ("shrencode", "shrencodew"):
        z.write(dist / f"{name}{ext}", f"{name}{ext}")
    z.write(ROOT / "LICENSE", "LICENSE")
    z.write(ROOT / "README.md", "README.md")
shutil.rmtree(ROOT / "build", ignore_errors=True)
print(archive)
