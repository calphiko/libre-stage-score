"""Build the desktop backend and frontend from the current origin/main commit."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[1]
DESKTOP = ROOT / "desktop"
BUILD = DESKTOP / "target"


def main() -> None:
    target = next(
        line.split(": ", 1)[1]
        for line in subprocess.check_output(["rustc", "-vV"], text=True).splitlines()
        if line.startswith("host: ")
    )
    requested_target = os.getenv("TAURI_ENV_TARGET_TRIPLE")
    if requested_target and requested_target != target:
        raise RuntimeError("Cross-compilation is not supported; build on the target platform and architecture")
    subprocess.run(["git", "fetch", "--no-tags", "origin", "main"], cwd=ROOT, check=True)
    commit = subprocess.check_output(
        ["git", "rev-parse", "--verify", "FETCH_HEAD^{commit}"],
        cwd=ROOT,
        text=True,
    ).strip()
    print(f"Building desktop app from origin/main at {commit}", flush=True)

    BUILD.mkdir(parents=True, exist_ok=True)
    npm = "npm.cmd" if os.name == "nt" else "npm"
    uv = "uv.exe" if os.name == "nt" else "uv"
    env = {**os.environ, "LIBRE_STAGE_DESKTOP": "1", "VITE_DESKTOP": "1", "VITE_API_URL": ""}
    env.pop("PYTHONPATH", None)
    with tempfile.TemporaryDirectory(prefix="main-build-", dir=BUILD) as temp:
        work = Path(temp)
        archive = work / "main.tar"
        source = work / "source"
        source.mkdir()
        env["UV_PROJECT_ENVIRONMENT"] = str(source / ".venv")
        env.pop("VIRTUAL_ENV", None)
        env.pop("PYTHONHOME", None)
        subprocess.run(
            ["git", "archive", "--format=tar", f"--output={archive}", commit],
            cwd=ROOT,
            check=True,
        )
        with tarfile.open(archive) as tar:
            tar.extractall(source, filter="data")
        subprocess.run([npm, "ci", "--no-audit", "--no-fund"], cwd=source / "frontend", env=env, check=True)
        subprocess.run([npm, "run", "build"], cwd=source / "frontend", env=env, check=True)
        subprocess.run([uv, "sync", "--frozen", "--extra", "desktop"], cwd=source, env=env, check=True)
        separator = os.pathsep
        subprocess.run(
            [
                uv, "run", "--frozen", "--no-sync", "python", "-m", "PyInstaller",
                "--noconfirm", "--clean", "--onefile", "--name", "spoke-backend",
                "--paths", str(source),
                "--distpath", str(work / "dist"), "--workpath", str(work / "work"),
                "--specpath", str(work),
                "--add-data", f"{source / 'alembic.ini'}{separator}.",
                "--add-data", f"{source / 'backend' / 'alembic'}{separator}backend/alembic",
                "--add-data", f"{source / 'frontend' / 'build'}{separator}frontend/build",
                "--hidden-import", "uvicorn.logging",
                "--hidden-import", "uvicorn.loops.auto",
                "--hidden-import", "uvicorn.protocols.http.auto",
                "--hidden-import", "uvicorn.protocols.websockets.auto",
                "--hidden-import", "uvicorn.lifespan.on",
                str(DESKTOP / "sidecar_entry.py"),
            ],
            cwd=source,
            env=env,
            check=True,
        )
        frontend_dist = BUILD / "frontend-dist"
        if frontend_dist.exists():
            shutil.rmtree(frontend_dist)
        shutil.copytree(source / "frontend" / "build", frontend_dist)
        binaries = DESKTOP / "binaries"
        binaries.mkdir(parents=True, exist_ok=True)
        suffix = ".exe" if os.name == "nt" else ""
        shutil.copy2(work / "dist" / f"spoke-backend{suffix}", binaries / f"spoke-backend-{target}{suffix}")
        (BUILD / "main-build.json").write_text(json.dumps({"main_commit": commit}, indent=2) + "\n")


if __name__ == "__main__":
    main()
