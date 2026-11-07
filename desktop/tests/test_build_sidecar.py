import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "build_sidecar.py"
spec = importlib.util.spec_from_file_location("build_sidecar", SCRIPT)
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)


class BuildSidecarTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.desktop = self.root / "desktop"
        self.desktop.mkdir()
        self.target = self.desktop / "target"
        self.patches = (
            patch.object(build, "ROOT", self.root),
            patch.object(build, "DESKTOP", self.desktop),
            patch.object(build, "BUILD", self.target),
        )
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)

    def test_build_uses_one_fetched_commit_and_isolated_dependencies(self):
        commit = "a" * 40
        commands = []

        def output(args, **kwargs):
            if args[0] == "rustc":
                return "host: test-target\n"
            self.assertEqual(args, ["git", "rev-parse", "--verify", "FETCH_HEAD^{commit}"])
            return commit + "\n"

        def run(args, **kwargs):
            commands.append((args, kwargs))
            if args[:2] == ["git", "archive"]:
                self.assertEqual(args[-1], commit)
                archive = Path(args[3].removeprefix("--output="))
                with tarfile.open(archive, "w") as tar:
                    for name in ("alembic.ini", "backend/alembic/env.py", "frontend/package-lock.json"):
                        contents = b"main source"
                        entry = tarfile.TarInfo(name)
                        entry.size = len(contents)
                        tar.addfile(entry, io.BytesIO(contents))
            elif args[0] in ("npm", "npm.cmd") and args[1:3] == ["run", "build"]:
                dest = kwargs["cwd"] / "build"
                dest.mkdir()
                (dest / "index.html").write_text("main frontend")
            elif "-m" in args and "PyInstaller" in args:
                dest = Path(args[args.index("--distpath") + 1])
                dest.mkdir()
                (dest / ("spoke-backend.exe" if os.name == "nt" else "spoke-backend")).write_text("main backend")
            return subprocess.CompletedProcess(args, 0)

        with patch.object(build.subprocess, "check_output", side_effect=output), \
             patch.object(build.subprocess, "run", side_effect=run), \
             patch.dict(os.environ, {"PYTHONPATH": "wrong", "UV_PROJECT_ENVIRONMENT": "wrong"}):
            build.main()

        self.assertEqual(commands[0][0], ["git", "fetch", "--no-tags", "origin", "main"])
        self.assertEqual(json.loads((self.target / "main-build.json").read_text()), {"main_commit": commit})
        self.assertEqual((self.target / "frontend-dist" / "index.html").read_text(), "main frontend")
        for args, kwargs in commands[2:]:
            env = kwargs["env"]
            self.assertNotIn("PYTHONPATH", env)
            self.assertTrue(env["UV_PROJECT_ENVIRONMENT"].startswith(str(self.target)))
            self.assertIn(str(self.target), str(kwargs["cwd"]))
        pyinstaller = commands[-1][0]
        self.assertIn(str(self.target), pyinstaller[pyinstaller.index("--paths") + 1])
        self.assertEqual(len([arg for arg in pyinstaller if arg == "--add-data"]), 3)

    def test_fetch_failure_stops_build(self):
        with patch.object(build.subprocess, "check_output", return_value="host: test-target\n"), \
             patch.object(build.subprocess, "run", side_effect=subprocess.CalledProcessError(1, "git")) as run:
            with self.assertRaises(subprocess.CalledProcessError):
                build.main()
        self.assertEqual(run.call_count, 1)
        self.assertFalse(self.target.exists())


if __name__ == "__main__":
    unittest.main()
