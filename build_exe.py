"""Build the standalone Windows application with bundled Indonesian audio."""
from pathlib import Path
import os
import struct
import subprocess
import sys

ROOT = Path(__file__).resolve().parent


def main():
    if sys.platform != "win32" or struct.calcsize("P") != 8:
        raise SystemExit("Build requires 64-bit Windows and Python.")
    for filename in ("instan.mp3", "prioritas.mp3"):
        if (ROOT / "audio" / filename).stat().st_size < 1000:
            raise SystemExit("Bundled voice file missing or incomplete: " + filename)
    subprocess.run([
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
        "--onefile", "--windowed", "--noupx", "--name", "PengumumanPesananInstan",
        "--paths", str(ROOT), "--add-data", str(ROOT / "audio") + os.pathsep + "audio",
        "--distpath", str(ROOT / "dist"), "--workpath", str(ROOT / "build"),
        "--specpath", str(ROOT / "build"), str(ROOT / "app.py"),
    ], check=True, cwd=ROOT)


if __name__ == "__main__":
    main()

