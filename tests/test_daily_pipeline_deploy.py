"""Daily runner transfer checks without SSH, database, or external API calls."""

import os
from pathlib import Path
import signal
import subprocess
import tempfile
import time
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "deploy/run-daily-pipeline.sh"


class DailyPipelineDeployTest(unittest.TestCase):
    def run_case(self, *, download_failure=False, pipeline_status=0, upload_failure=False, interrupt=None):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            remote = root / "remote"
            for name in ("sources", "jev", "models"):
                (remote / name).mkdir(parents=True)
            (remote / "sources/coffee.parquet").write_text("old")
            (remote / "jev/responses.json").write_text("old")
            (remote / "models/production_dlinear_60.pt").write_text("model")
            (remote / "models/selected_news").mkdir()
            (remote / "models/selected_news/manifest.json").write_text("selected")
            commands = root / "commands"
            bin_dir = root / "bin"
            bin_dir.mkdir()
            mocks = {
                "ssh": "#!/bin/sh\nexec sleep 30\n",
                "python": """#!/usr/bin/env python3
import os, pathlib, sys, time
args = sys.argv[1:]
assert args[:3] == ['-m', 'coffee_service.pipeline', 'refresh']
pathlib.Path(os.environ['FAKE_COMMANDS']).open('a').write('pipeline\\n')
source = pathlib.Path(args[args.index('--source-dir') + 1])
cache = pathlib.Path(args[args.index('--jev-cache') + 1])
source.joinpath('updated.parquet').write_text('new')
cache.write_text('new')
if os.environ['FAKE_WAIT_FOR_SIGNAL'] == '1':
    pathlib.Path(os.environ['FAKE_READY']).write_text('ready')
    time.sleep(30)
sys.exit(int(os.environ['FAKE_PIPELINE_STATUS']))
""",
                "rsync": """#!/usr/bin/env python3
import os, pathlib, shutil, sys
args = sys.argv[1:]
assert '--delete' not in args
config = pathlib.Path(os.environ['RSYNC_RSH'].split(' -F ', 1)[1])
lines = config.read_text().splitlines()
for directive in ('IdentityFile', 'UserKnownHostsFile'):
    secret_path = pathlib.Path(next(line.split(maxsplit=1)[1] for line in lines if line.strip().startswith(directive)))
    assert secret_path.stat().st_mode & 0o777 == 0o600
assert '  StrictHostKeyChecking yes' in lines
assert '  ConnectTimeout 15' in lines
assert '  ServerAliveInterval 15' in lines
assert '  ServerAliveCountMax 3' in lines
upload = '--delay-updates' in args
source, target = args[-2:]
with pathlib.Path(os.environ['FAKE_COMMANDS']).open('a') as log:
    log.write(('upload' if upload else 'download') + ' ' + source + ' ' + target + '\\n')
if upload and os.environ['FAKE_UPLOAD_FAILURE'] == '1':
    sys.exit(12)
if not upload and os.environ['FAKE_DOWNLOAD_FAILURE'] == '1':
    sys.exit(12)
def path(value):
    if value.startswith('coffee-vm:/srv/coffee/pipeline/'):
        return pathlib.Path(os.environ['FAKE_REMOTE']) / value.split('/srv/coffee/pipeline/', 1)[1]
    return pathlib.Path(value)
source, target = path(source), path(target)
if source.is_dir():
    target.mkdir(parents=True, exist_ok=True)
    for item in source.iterdir():
        shutil.copy2(item, target / item.name)
else:
    shutil.copy2(source, target)
""",
            }
            for name, body in mocks.items():
                path = bin_dir / name
                path.write_text(body)
                path.chmod(0o755)
            env = os.environ.copy()
            env.update({
                "PATH": str(bin_dir) + os.pathsep + env["PATH"],
                "RUNNER_TEMP": str(root),
                "COFFEE_HOST": "example.test",
                "COFFEE_SSH_USER": "coffee-actions",
                "COFFEE_STATE_DIR": "/srv/coffee/pipeline",
                "COFFEE_SSH_KEY": "fixture-key",
                "COFFEE_KNOWN_HOSTS": "fixture-host-key",
                "PGHOST": "127.0.0.1", "PGPORT": "15432",
                "PGDATABASE": "coffee_price", "PGUSER": "coffee_pipeline",
                "PGPASSWORD": "fixture-password", "FRED_API_KEY": "fixture-fred",
                "AI_GATEWAY_API_KEY": "fixture-gateway",
                "FAKE_REMOTE": str(remote), "FAKE_COMMANDS": str(commands),
                "FAKE_DOWNLOAD_FAILURE": str(int(download_failure)),
                "FAKE_PIPELINE_STATUS": str(pipeline_status),
                "FAKE_UPLOAD_FAILURE": str(int(upload_failure)),
                "FAKE_WAIT_FOR_SIGNAL": str(int(interrupt is not None)),
                "FAKE_READY": str(root / "ready"),
            })
            if interrupt is None:
                result = subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True, text=True, timeout=10)
                status, output = result.returncode, result.stdout + result.stderr
            else:
                process = subprocess.Popen(["bash", str(SCRIPT)], env=env, stdout=subprocess.PIPE,
                                           stderr=subprocess.PIPE, text=True)
                for _ in range(200):
                    if (root / "ready").exists() or process.poll() is not None:
                        break
                    time.sleep(0.05)
                self.assertTrue((root / "ready").exists(), "mock pipeline did not start")
                process.send_signal(interrupt)
                stdout, stderr = process.communicate(timeout=10)
                status, output = process.returncode, stdout + stderr
            calls = commands.read_text().splitlines()
            self.assertFalse(list(root.glob("coffee-pipeline.*")), "temporary SSH key and state must be removed")
            self.assertNotIn("fixture-password", output)
            self.last_output = output
            self.assertEqual((remote / "models/production_dlinear_60.pt").read_text(), "model")
            return status, calls, (remote / "sources/updated.parquet").exists(), (remote / "jev/responses.json").read_text()

    def test_initial_download_failure_does_not_upload(self):
        status, calls, source_updated, jev = self.run_case(download_failure=True)
        self.assertNotEqual(status, 0)
        self.assertEqual(len(calls), 1)
        self.assertFalse(source_updated)
        self.assertEqual(jev, "old")

    def test_pipeline_failure_keeps_status_and_uploads_both_state_directories(self):
        status, calls, source_updated, jev = self.run_case(pipeline_status=7)
        self.assertEqual(status, 7)
        self.assertEqual([call.split()[0] for call in calls], ["download"] * 3 + ["pipeline", "upload", "upload"])
        self.assertTrue(source_updated)
        self.assertEqual(jev, "new")

    def test_partial_refresh_warns_without_failing_and_still_uploads(self):
        status, calls, source_updated, jev = self.run_case(pipeline_status=3)
        self.assertEqual(status, 0)
        self.assertIn("::warning title=Daily pipeline partial::", self.last_output)
        self.assertEqual([call.split()[0] for call in calls], ["download"] * 3 + ["pipeline", "upload", "upload"])
        self.assertTrue(source_updated)
        self.assertEqual(jev, "new")

    def test_upload_failure_fails_workflow(self):
        status, calls, source_updated, jev = self.run_case(upload_failure=True)
        self.assertNotEqual(status, 0)
        self.assertEqual([call.split()[0] for call in calls][-2:], ["upload", "upload"])
        self.assertFalse(source_updated)
        self.assertEqual(jev, "old")

    def test_signals_sync_checkpoint_before_removing_ssh_key(self):
        for interrupt, expected_status in ((signal.SIGTERM, 143), (signal.SIGINT, 130)):
            with self.subTest(interrupt=interrupt):
                status, calls, source_updated, jev = self.run_case(interrupt=interrupt)
                self.assertEqual(status, expected_status)
                self.assertEqual([call.split()[0] for call in calls][-2:], ["upload", "upload"])
                self.assertTrue(source_updated)
                self.assertEqual(jev, "new")


if __name__ == "__main__":
    unittest.main()
