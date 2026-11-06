"""Build the frontend and a platform-specific backend executable."""

import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
DESKTOP = ROOT / "desktop"


def main() -> None:
    target = next(
        line.split(": ", 1)[1]
        for line in subprocess.check_output(["rustc", "-vV"], text=True).splitlines()
        if line.startswith("host: ")
    )
    env = {**os.environ, "LIBRE_STAGE_DESKTOP": "1", "VITE_DESKTOP": "1", "VITE_API_URL": ""}
    npm = "npm.cmd" if os.name == "nt" else "npm"
    subprocess.run([npm, "run", "build"], cwd=ROOT / "frontend", env=env, check=True)
    work = DESKTOP / "target" / "sidecar-build"
    binaries = DESKTOP / "binaries"
    binaries.mkdir(parents=True, exist_ok=True)
    separator = os.pathsep
    subprocess.run(
        [
            sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile",
            "--name", "spoke-backend", "--paths", str(ROOT),
            "--distpath", str(work / "dist"), "--workpath", str(work / "work"),
            "--specpath", str(work),
            "--add-data", f"{ROOT / 'alembic.ini'}{separator}.",
            "--add-data", f"{ROOT / 'backend' / 'alembic'}{separator}backend/alembic",
            "--add-data", f"{ROOT / 'frontend' / 'build'}{separator}frontend/build",
            "--hidden-import", "uvicorn.logging",
            "--hidden-import", "uvicorn.loops.auto",
            "--hidden-import", "uvicorn.protocols.http.auto",
            "--hidden-import", "uvicorn.protocols.websockets.auto",
            "--hidden-import", "uvicorn.lifespan.on",
            str(DESKTOP / "sidecar_entry.py"),
        ],
        cwd=ROOT,
        check=True,
    )
    suffix = ".exe" if os.name == "nt" else ""
    shutil.copy2(work / "dist" / f"spoke-backend{suffix}", binaries / f"spoke-backend-{target}{suffix}")


if __name__ == "__main__":
    main()
