"""Tests for Lethe permission configuration resolution."""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

# Add scripts dir to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "skill" / "scripts"))

from lethe_utils import _validate_permission


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


if __name__ == "__main__":
    unittest.main()
