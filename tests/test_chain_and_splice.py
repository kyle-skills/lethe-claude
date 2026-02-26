"""Regression tests for chain walking and summary sidecar validation."""

from __future__ import annotations

import io
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path

# Add scripts dir to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "skill" / "scripts"))

from lethe_utils import resolve_summary_file_path, walk_chain


ANALYZE_SCRIPT = str(
    Path(__file__).resolve().parent.parent / "skill" / "scripts" / "lethe-analyze.py"
)
SPLICE_SCRIPT = str(
    Path(__file__).resolve().parent.parent / "skill" / "scripts" / "lethe-splice.py"
)


class TestWalkChain(unittest.TestCase):
    def test_sidechain_only_chain_uses_sidechain_leaf(self):
        lines = [
            {
                "type": "user",
                "uuid": "00000000-0000-0000-0000-000000000001",
                "parentUuid": "root-parent",
                "isSidechain": True,
                "message": {"role": "user", "content": "u1"},
            },
            {
                "type": "assistant",
                "uuid": "00000000-0000-0000-0000-000000000002",
                "parentUuid": "00000000-0000-0000-0000-000000000001",
                "isSidechain": True,
                "message": {"role": "assistant", "content": [{"type": "text", "text": "a1"}]},
            },
            {
                "type": "progress",
                "uuid": "00000000-0000-0000-0000-000000000003",
                "parentUuid": "00000000-0000-0000-0000-000000000002",
                "isSidechain": True,
            },
        ]

        stderr = io.StringIO()
        with redirect_stderr(stderr):
            chain = walk_chain(lines)

        self.assertEqual(len(chain), 3)
        self.assertEqual(
            [entry["uuid"] for _, entry in chain],
            [
                "00000000-0000-0000-0000-000000000001",
                "00000000-0000-0000-0000-000000000002",
                "00000000-0000-0000-0000-000000000003",
            ],
        )
        self.assertIn("using sidechain chain head", stderr.getvalue())

    def test_no_false_cycle_warning_for_valid_linear_chain(self):
        lines = [
            {
                "type": "system",
                "subtype": "compact_boundary",
                "uuid": "10000000-0000-0000-0000-000000000001",
                "isSidechain": False,
            },
            {
                "type": "user",
                "uuid": "10000000-0000-0000-0000-000000000002",
                "parentUuid": "10000000-0000-0000-0000-000000000001",
                "isSidechain": False,
                "message": {"role": "user", "content": "hi"},
            },
            {
                "type": "assistant",
                "uuid": "10000000-0000-0000-0000-000000000003",
                "parentUuid": "10000000-0000-0000-0000-000000000002",
                "isSidechain": False,
                "message": {"role": "assistant", "content": [{"type": "text", "text": "hello"}]},
            },
        ]

        stderr = io.StringIO()
        with redirect_stderr(stderr):
            chain = walk_chain(lines)

        self.assertEqual(len(chain), 3)
        self.assertNotIn("safety limit", stderr.getvalue())


class TestSummaryPathValidation(unittest.TestCase):
    def test_accepts_file_under_session_directory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            session_id = "session-abc"
            session_dir = base / session_id
            session_dir.mkdir(parents=True)

            good = session_dir / "summary.txt"
            good.write_text("ok", encoding="utf-8")

            resolved = resolve_summary_file_path(
                str(good), session_id, base_dir=base
            )
            self.assertEqual(resolved, good.resolve())

    def test_rejects_parent_traversal(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            session_id = "session-abc"
            session_dir = base / session_id
            session_dir.mkdir(parents=True)

            outside = base / "outside.txt"
            outside.write_text("secret", encoding="utf-8")
            traversal = session_dir / ".." / "outside.txt"

            with self.assertRaises(ValueError) as ctx:
                resolve_summary_file_path(
                    str(traversal), session_id, base_dir=base
                )
            self.assertIn("must be under", str(ctx.exception))

    def test_rejects_symlink_escape(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            session_id = "session-abc"
            session_dir = base / session_id
            session_dir.mkdir(parents=True)

            outside = base / "outside.txt"
            outside.write_text("secret", encoding="utf-8")
            link = session_dir / "link.txt"
            try:
                link.symlink_to(outside)
            except OSError:
                self.skipTest("symlink not supported in this environment")

            with self.assertRaises(ValueError) as ctx:
                resolve_summary_file_path(str(link), session_id, base_dir=base)
            self.assertIn("must be under", str(ctx.exception))


class TestAnalyzeCLI(unittest.TestCase):
    def test_analyze_handles_sidechain_only_session(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "sidechain.jsonl"
            path.write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "type": "user",
                                "uuid": "20000000-0000-0000-0000-000000000001",
                                "parentUuid": "missing-root",
                                "isSidechain": True,
                                "message": {"role": "user", "content": "u1"},
                            }
                        ),
                        json.dumps(
                            {
                                "type": "assistant",
                                "uuid": "20000000-0000-0000-0000-000000000002",
                                "parentUuid": "20000000-0000-0000-0000-000000000001",
                                "isSidechain": True,
                                "message": {
                                    "role": "assistant",
                                    "content": [{"type": "text", "text": "a1"}],
                                },
                            }
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            result = subprocess.run(
                [sys.executable, ANALYZE_SCRIPT, "sidechain", "--jsonl-path", str(path)],
                capture_output=True,
                text=True,
                timeout=10,
            )
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            manifest = json.loads(result.stdout)
            self.assertEqual(manifest["chain_length"], 2)


class TestSpliceCLI(unittest.TestCase):
    def test_keep_all_with_existing_summary_marker_stays_valid(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            session_id = "keep-existing-summary"
            jsonl_path = Path(tmpdir) / "session.jsonl"
            jsonl_path.write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "type": "system",
                                "subtype": "compact_boundary",
                                "uuid": "30000000-0000-0000-0000-000000000001",
                                "sessionId": session_id,
                                "isSidechain": False,
                            }
                        ),
                        json.dumps(
                            {
                                "type": "user",
                                "uuid": "30000000-0000-0000-0000-000000000002",
                                "parentUuid": "30000000-0000-0000-0000-000000000001",
                                "sessionId": session_id,
                                "isSidechain": False,
                                "message": {
                                    "role": "user",
                                    "content": "[lethe summary] Existing summary from prior run",
                                },
                            }
                        ),
                        json.dumps(
                            {
                                "type": "assistant",
                                "uuid": "30000000-0000-0000-0000-000000000003",
                                "parentUuid": "30000000-0000-0000-0000-000000000002",
                                "sessionId": session_id,
                                "isSidechain": False,
                                "message": {
                                    "role": "assistant",
                                    "content": [{"type": "text", "text": "Acknowledged."}],
                                },
                            }
                        ),
                        json.dumps(
                            {
                                "type": "user",
                                "uuid": "30000000-0000-0000-0000-000000000004",
                                "parentUuid": "30000000-0000-0000-0000-000000000003",
                                "sessionId": session_id,
                                "isSidechain": False,
                                "message": {"role": "user", "content": "Continue"},
                            }
                        ),
                        json.dumps(
                            {
                                "type": "assistant",
                                "uuid": "30000000-0000-0000-0000-000000000005",
                                "parentUuid": "30000000-0000-0000-0000-000000000004",
                                "sessionId": session_id,
                                "isSidechain": False,
                                "message": {
                                    "role": "assistant",
                                    "content": [{"type": "text", "text": "Done"}],
                                },
                            }
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            analyze = subprocess.run(
                [sys.executable, ANALYZE_SCRIPT, session_id, "--jsonl-path", str(jsonl_path)],
                capture_output=True,
                text=True,
                timeout=10,
            )
            self.assertEqual(analyze.returncode, 0, msg=analyze.stderr)
            manifest = json.loads(analyze.stdout)
            plan = {
                "actions": [
                    {"segment_id": seg["id"], "action": "keep"}
                    for seg in manifest["segments"]
                ]
            }
            plan_path = Path(tmpdir) / "keep-plan.json"
            plan_path.write_text(json.dumps(plan), encoding="utf-8")

            splice = subprocess.run(
                [
                    sys.executable,
                    SPLICE_SCRIPT,
                    session_id,
                    "--jsonl-path",
                    str(jsonl_path),
                    "--cut-plan",
                    str(plan_path),
                    "--no-backup",
                ],
                capture_output=True,
                text=True,
                timeout=10,
            )
            self.assertEqual(splice.returncode, 0, msg=splice.stderr)
            result = json.loads(splice.stdout)
            self.assertTrue(result["ok"])
            self.assertTrue(result["chain_verification"]["ok"])


if __name__ == "__main__":
    unittest.main()
