from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from orx.config import resolve_runtime_paths
from orx.doctor import HostDoctorService
from orx.env import ENV_DISABLE, ENV_FILE, load_repo_env
from orx.linear_client import LinearGraphQLClient
from orx.storage import Storage


class RepoEnvTests(unittest.TestCase):
    def test_load_repo_env_populates_linear_key_from_override_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            env_file = Path(temp_dir) / ".env"
            env_file.write_text("ORX_LINEAR_API_KEY='test-linear-key'\n", encoding="utf-8")

            original = dict(os.environ)
            try:
                os.environ.pop("ORX_LINEAR_API_KEY", None)
                os.environ.pop("LINEAR_API_KEY", None)
                os.environ.pop(ENV_DISABLE, None)
                os.environ[ENV_FILE] = str(env_file)

                loaded = load_repo_env()
                client = LinearGraphQLClient.from_env()

                self.assertEqual(loaded["ORX_LINEAR_API_KEY"], "test-linear-key")
                self.assertEqual(client.api_key, "test-linear-key")
            finally:
                os.environ.clear()
                os.environ.update(original)

    def test_doctor_uses_repo_env_for_linear_key_check(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            env_file = Path(temp_dir) / ".env"
            env_file.write_text("LINEAR_API_KEY=test-linear-key\n", encoding="utf-8")

            original = dict(os.environ)
            try:
                os.environ.pop("ORX_LINEAR_API_KEY", None)
                os.environ.pop("LINEAR_API_KEY", None)
                os.environ.pop(ENV_DISABLE, None)
                os.environ[ENV_FILE] = str(env_file)

                storage = Storage(resolve_runtime_paths(temp_dir))
                payload = HostDoctorService(storage).payload()
                linear_check = next(
                    check for check in payload["checks"] if check["name"] == "linear_api_key"
                )

                self.assertTrue(linear_check["ok"])
                self.assertTrue(linear_check["details"]["configured"])
            finally:
                os.environ.clear()
                os.environ.update(original)


if __name__ == "__main__":
    unittest.main()
