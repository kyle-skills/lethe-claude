"""Tests for Lethe permission configuration resolution."""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

# Add scripts dir to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "skill" / "scripts"))

from lethe_utils import _parse_lethe_config, _validate_permission, resolve_config


class TestValidatePermission(unittest.TestCase):
    def test_accepts_acceptEdits(self):
        self.assertEqual(_validate_permission("compactor_permission", "acceptEdits"), "acceptEdits")

    def test_accepts_bypassPermissions(self):
        self.assertEqual(_validate_permission("resume_permission", "bypassPermissions"), "bypassPermissions")

    def test_rejects_invalid_returns_none(self):
        self.assertIsNone(_validate_permission("compactor_permission", "invalid"))

    def test_rejects_empty_string(self):
        self.assertIsNone(_validate_permission("compactor_permission", ""))

    def test_rejects_case_mismatch(self):
        self.assertIsNone(_validate_permission("compactor_permission", "bypasspermissions"))

    def test_warns_on_invalid(self):
        with patch("sys.stderr") as mock_stderr:
            _validate_permission("compactor_permission", "bogus")
            mock_stderr.write.assert_called()


class TestParseLetheConfig(unittest.TestCase):
    def _write_config(self, content: str) -> Path:
        """Write content to a temp file and return its Path."""
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".lethe_config", delete=False)
        f.write(content)
        f.close()
        return Path(f.name)

    def test_parses_key_value_pairs(self):
        path = self._write_config("compactor_permission=bypassPermissions\nresume_permission=acceptEdits\n")
        result = _parse_lethe_config(path)
        self.assertEqual(result, {"compactor_permission": "bypassPermissions", "resume_permission": "acceptEdits"})
        path.unlink()

    def test_ignores_comments(self):
        path = self._write_config("# this is a comment\ncompactor_permission=acceptEdits\n")
        result = _parse_lethe_config(path)
        self.assertEqual(result, {"compactor_permission": "acceptEdits"})
        path.unlink()

    def test_ignores_empty_lines(self):
        path = self._write_config("\n\ncompactor_permission=acceptEdits\n\n")
        result = _parse_lethe_config(path)
        self.assertEqual(result, {"compactor_permission": "acceptEdits"})
        path.unlink()

    def test_ignores_lines_without_equals(self):
        path = self._write_config("no equals here\ncompactor_permission=acceptEdits\n")
        result = _parse_lethe_config(path)
        self.assertEqual(result, {"compactor_permission": "acceptEdits"})
        path.unlink()

    def test_unknown_keys_included(self):
        path = self._write_config("future_key=future_value\n")
        result = _parse_lethe_config(path)
        self.assertEqual(result, {"future_key": "future_value"})
        path.unlink()

    def test_returns_empty_dict_for_missing_file(self):
        result = _parse_lethe_config(Path("/nonexistent/.lethe_config"))
        self.assertEqual(result, {})

    def test_no_whitespace_around_equals(self):
        path = self._write_config("compactor_permission = bypassPermissions\n")
        result = _parse_lethe_config(path)
        # "compactor_permission " (with space) as key, not "compactor_permission"
        self.assertNotIn("compactor_permission", result)
        path.unlink()


class TestResolveConfig(unittest.TestCase):
    def setUp(self):
        for var in ("LETHE_COMPACTOR_PERMISSION", "LETHE_RESUME_PERMISSION"):
            os.environ.pop(var, None)

    def tearDown(self):
        for var in ("LETHE_COMPACTOR_PERMISSION", "LETHE_RESUME_PERMISSION"):
            os.environ.pop(var, None)

    def test_defaults_no_config(self):
        result = resolve_config()
        self.assertEqual(result["compactor_permission"], "acceptEdits")
        self.assertIsNone(result["resume_permission"])

    def test_env_var_compactor(self):
        os.environ["LETHE_COMPACTOR_PERMISSION"] = "bypassPermissions"
        result = resolve_config()
        self.assertEqual(result["compactor_permission"], "bypassPermissions")

    def test_env_var_resume(self):
        os.environ["LETHE_RESUME_PERMISSION"] = "acceptEdits"
        result = resolve_config()
        self.assertEqual(result["resume_permission"], "acceptEdits")

    def test_invalid_env_var_falls_back_to_default(self):
        os.environ["LETHE_COMPACTOR_PERMISSION"] = "bogus"
        result = resolve_config()
        self.assertEqual(result["compactor_permission"], "acceptEdits")

    def test_project_config_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config = Path(tmpdir) / ".lethe_config"
            config.write_text("compactor_permission=bypassPermissions\nresume_permission=acceptEdits\n")
            result = resolve_config(project_dir=tmpdir)
            self.assertEqual(result["compactor_permission"], "bypassPermissions")
            self.assertEqual(result["resume_permission"], "acceptEdits")

    def test_env_overrides_project_config(self):
        os.environ["LETHE_COMPACTOR_PERMISSION"] = "acceptEdits"
        with tempfile.TemporaryDirectory() as tmpdir:
            config = Path(tmpdir) / ".lethe_config"
            config.write_text("compactor_permission=bypassPermissions\n")
            result = resolve_config(project_dir=tmpdir)
            self.assertEqual(result["compactor_permission"], "acceptEdits")  # env wins

    def test_home_config_as_fallback(self):
        with tempfile.TemporaryDirectory() as fake_home:
            config = Path(fake_home) / ".lethe_config"
            config.write_text("resume_permission=bypassPermissions\n")
            with patch("lethe_utils.Path.home", return_value=Path(fake_home)):
                result = resolve_config()
                self.assertEqual(result["resume_permission"], "bypassPermissions")

    def test_project_overrides_home(self):
        with tempfile.TemporaryDirectory() as proj_dir, \
             tempfile.TemporaryDirectory() as fake_home:
            (Path(proj_dir) / ".lethe_config").write_text("resume_permission=acceptEdits\n")
            (Path(fake_home) / ".lethe_config").write_text("resume_permission=bypassPermissions\n")
            with patch("lethe_utils.Path.home", return_value=Path(fake_home)):
                result = resolve_config(project_dir=proj_dir)
                self.assertEqual(result["resume_permission"], "acceptEdits")  # project wins

    def test_mixed_sources(self):
        """Env sets compactor, home config sets resume."""
        os.environ["LETHE_COMPACTOR_PERMISSION"] = "bypassPermissions"
        with tempfile.TemporaryDirectory() as fake_home:
            (Path(fake_home) / ".lethe_config").write_text("resume_permission=acceptEdits\n")
            with patch("lethe_utils.Path.home", return_value=Path(fake_home)):
                result = resolve_config()
                self.assertEqual(result["compactor_permission"], "bypassPermissions")
                self.assertEqual(result["resume_permission"], "acceptEdits")

    def test_early_termination_skips_lower_priority(self):
        """When both keys resolved from env, no file reading needed."""
        os.environ["LETHE_COMPACTOR_PERMISSION"] = "bypassPermissions"
        os.environ["LETHE_RESUME_PERMISSION"] = "acceptEdits"
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / ".lethe_config").write_text(
                "compactor_permission=acceptEdits\nresume_permission=bypassPermissions\n"
            )
            result = resolve_config(project_dir=tmpdir)
            self.assertEqual(result["compactor_permission"], "bypassPermissions")
            self.assertEqual(result["resume_permission"], "acceptEdits")


if __name__ == "__main__":
    unittest.main()
