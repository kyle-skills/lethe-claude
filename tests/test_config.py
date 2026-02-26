"""Tests for Lethe permission configuration resolution."""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

# Add scripts dir to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "skill" / "scripts"))

from lethe_utils import _parse_lethe_config, _validate_permission


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


if __name__ == "__main__":
    unittest.main()
