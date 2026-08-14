import json
import os
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock, patch

from bff import admin


class TestPipelineLocking(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.status_file = os.path.join(self.temporary.name, "pipeline_status.json")
        self.lock_file = os.path.join(self.temporary.name, "pipeline_run.lock")
        self.constants = patch.multiple(
            admin,
            STATUS_FILE=self.status_file,
            LOCK_FILE=self.lock_file,
            LOG_FILE=os.path.join(self.temporary.name, "pipeline.log"),
        )
        self.constants.start()

    def tearDown(self):
        admin.release_pipeline_claim()
        self.constants.stop()
        self.temporary.cleanup()

    def test_ten_parallel_claims_start_exactly_one_pipeline(self):
        with ThreadPoolExecutor(max_workers=10) as executor:
            results = list(executor.map(lambda _: admin.claim_pipeline_run("full_run"), range(10)))
        self.assertEqual(sum(results), 1)
        self.assertTrue(os.path.exists(self.lock_file))
        self.assertFalse(admin.claim_pipeline_run("full_run"))
        admin.release_pipeline_claim()
        self.assertTrue(admin.claim_pipeline_run("full_run"))

    def test_status_writes_are_atomic_and_corruption_is_safe(self):
        admin.write_status("running", "full_run")
        with open(self.status_file, "r", encoding="utf-8") as handle:
            self.assertEqual(json.load(handle)["status"], "running")
        with open(self.status_file, "w", encoding="utf-8") as handle:
            handle.write("{not json")
        self.assertEqual(admin.read_status()["status"], "idle")

    def test_stale_lock_is_recovered_but_live_owner_is_not_reclaimed(self):
        with open(self.lock_file, "w", encoding="utf-8") as handle:
            json.dump({"pid": 99999999, "mode": "full_run"}, handle)
        stale_time = time.time() - admin.LOCK_STALE_AFTER_SECONDS - 1
        os.utime(self.lock_file, (stale_time, stale_time))
        self.assertTrue(admin.claim_pipeline_run("full_run"))
        admin.release_pipeline_claim()

        with open(self.lock_file, "w", encoding="utf-8") as handle:
            json.dump({"pid": os.getpid(), "mode": "full_run"}, handle)
        os.utime(self.lock_file, (stale_time, stale_time))
        self.assertFalse(admin.claim_pipeline_run("full_run"))

    @patch("bff.admin.subprocess.Popen")
    def test_exception_releases_claim(self, mocked_popen):
        process = MagicMock()
        process.returncode = 1
        mocked_popen.return_value = process
        admin.run_pipeline_task("quick_consolidate")
        self.assertFalse(os.path.exists(self.lock_file))
        self.assertEqual(admin.read_status()["status"], "failed")

