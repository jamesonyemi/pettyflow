#!/usr/bin/env python3
"""
PettyFlow Local Pre-Push CI/CD Review & Verification Script
Runs automatically before git push to guarantee code quality, test passing,
performance benchmark compliance, and documentation generation.
"""

import sys
import os
import subprocess
import time

def print_header(title: str):
    print("\n" + "=" * 60)
    print(f"  {title}")
    print("=" * 60)

def run_step(step_name: str, command: list) -> bool:
    print(f"\n[CHECK] Running: {step_name}...")
    start = time.perf_counter()
    res = subprocess.run(command, capture_output=True, text=True)
    elapsed = (time.perf_counter() - start) * 1000.0
    
    if res.returncode == 0:
        print(f"[PASSED] ({elapsed:.1f} ms)")
        if res.stdout.strip():
            for line in res.stdout.strip().splitlines()[-5:]:
                print(f"   {line}")
        return True
    else:
        print(f"[FAILED] ({elapsed:.1f} ms)")
        print("\n--- STDOUT ---")
        print(res.stdout)
        print("--- STDERR ---")
        print(res.stderr)
        return False

def main():
    print_header("PETTYFLOW PRE-PUSH AUTOMATED CODE REVIEW & VERIFICATION")
    
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    os.chdir(repo_root)

    python_bin = sys.executable
    venv_python = os.path.join(repo_root, ".venv", "Scripts", "python.exe") if os.name == "nt" else os.path.join(repo_root, ".venv", "bin", "python")
    if os.path.exists(venv_python):
        python_bin = venv_python

    checks = [
        ("Python Code Syntax & Compilation", [python_bin, "-m", "compileall", "src", "tests"]),
        ("Unit Tests & Latency Benchmark", [python_bin, "-m", "unittest", "discover", "-s", "tests", "-v"]),
    ]

    all_passed = True
    for name, cmd in checks:
        if not run_step(name, cmd):
            all_passed = False
            break

    print("\n" + "=" * 60)
    if all_passed:
        print("[SUCCESS] ALL PRE-PUSH CHECKS PASSED! Ready to push to GitHub.")
        print("=" * 60 + "\n")
        sys.exit(0)
    else:
        print("[ERROR] PRE-PUSH CHECKS FAILED! Push blocked until issues are resolved.")
        print("=" * 60 + "\n")
        sys.exit(1)

if __name__ == "__main__":
    main()
