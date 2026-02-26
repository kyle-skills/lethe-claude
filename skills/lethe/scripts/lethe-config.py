#!/usr/bin/env python3
"""
Lethe: Permission Configuration Resolver

Resolves permission config from environment variables and .lethe_config files.

Usage: lethe-config.py [--project-dir PATH]

Outputs resolved config as JSON to stdout:
    {"compactor_permission": "acceptEdits", "resume_permission": null}

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
        description="Lethe: Permission configuration resolver"
    )
    parser.add_argument(
        "--project-dir",
        default=None,
        help="Project root directory for project-level .lethe_config lookup",
    )

    args = parser.parse_args()
    config = resolve_config(project_dir=args.project_dir)
    print(json.dumps(config, indent=2))
    sys.exit(0)


if __name__ == "__main__":
    main()
