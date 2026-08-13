#!/usr/bin/env python3
"""
PettyFlow Git Hook Installer
Installs local pre-push hooks to enforce automated reviews before code reaches GitHub.
"""

import os
import sys
import stat

HOOK_SCRIPT_CONTENT = """#!/bin/sh
# PettyFlow Automated Pre-Push Review Hook
echo "[INFO] Executing PettyFlow Pre-Push Code Review & Verification..."
python scripts/pre_push_check.py
if [ $? -ne 0 ]; then
    echo "[ERROR] Git Push Aborted due to pre-push check failures."
    exit 1
fi
exit 0
"""

def install_git_hooks():
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    git_hooks_dir = os.path.join(repo_root, ".git", "hooks")

    if not os.path.exists(git_hooks_dir):
        print(f"[ERROR] .git directory not found at {git_hooks_dir}. Please run 'git init' first.")
        sys.exit(1)

    pre_push_path = os.path.join(git_hooks_dir, "pre-push")
    with open(pre_push_path, "w", encoding="utf-8") as f:
        f.write(HOOK_SCRIPT_CONTENT)

    # Make executable on Unix/Mac/Linux
    st = os.stat(pre_push_path)
    os.chmod(pre_push_path, st.st_mode | stat.S_IEXEC)

    print(f"[SUCCESS] Installed Git pre-push hook at: {pre_push_path}")

if __name__ == "__main__":
    install_git_hooks()
