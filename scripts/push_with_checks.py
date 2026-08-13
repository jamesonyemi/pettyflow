#!/usr/bin/env python3
"""Run local verification, then push the current branch after confirmation."""

import argparse
import subprocess
import sys
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parent.parent


def run(command: list[str]) -> int:
    return subprocess.run(command, cwd=REPOSITORY_ROOT, check=False).returncode


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run PettyFlow checks, then push the current Git branch."
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="push without the interactive confirmation prompt",
    )
    parser.add_argument(
        "push_args",
        nargs="*",
        help="arguments forwarded to git push, such as origin main",
    )
    args = parser.parse_args()

    print("[INFO] Running local pre-push verification...")
    if run([sys.executable, "scripts/pre_push_check.py"]) != 0:
        print("[ERROR] Push cancelled because local verification failed.")
        return 1

    if not args.yes:
        response = input("[CONFIRM] Push the current branch to its configured remote? [y/N] ")
        if response.strip().lower() not in {"y", "yes"}:
            print("[INFO] Push cancelled.")
            return 0

    print("[INFO] Pushing branch...")
    # Do not use --no-verify: an installed Git pre-push hook remains enforced.
    return run(["git", "push", *args.push_args])


if __name__ == "__main__":
    raise SystemExit(main())
