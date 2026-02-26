#!/usr/bin/env python3
"""
Lethe: Configuration Resolver

Resolves Lethe config from environment variables and .lethe_config files.

Usage: lethe-config.py [--project-dir PATH]

Outputs resolved config as JSON to stdout:
    {
      "compactor_permission": "acceptEdits",
      "resume_permission": null,
      "compact_size": 400000
    }

Exit code: always 0 — config resolution never fails. Invalid values produce
stderr warnings but the output is always valid JSON with safe defaults.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lethe_utils import resolve_config


def main():
    parser = argparse.ArgumentParser(
        description="Lethe: configuration resolver"
    )
    parser.add_argument(
        "--project-dir",
        default=None,
        help="Project root directory for project-level .lethe_config lookup",
    )
    parser.add_argument(
        "--fallback-compactor-permission",
        default=None,
        help="Caller fallback for compactor_permission (below env/config)",
    )
    parser.add_argument(
        "--fallback-resume-permission",
        default=None,
        help="Caller fallback for resume_permission (below env/config)",
    )

    args = parser.parse_args()
    caller_overrides = {}
    if args.fallback_compactor_permission:
        caller_overrides["compactor_permission"] = args.fallback_compactor_permission
    if args.fallback_resume_permission:
        caller_overrides["resume_permission"] = args.fallback_resume_permission

    config = resolve_config(
        project_dir=args.project_dir,
        caller_overrides=caller_overrides or None,
    )
    print(json.dumps(config, indent=2))
    sys.exit(0)


if __name__ == "__main__":
    main()
