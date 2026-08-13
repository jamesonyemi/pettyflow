#!/usr/bin/env python3
"""Interactive commit helper that implements the repository's gitcommit.md policy."""

import re
import subprocess
import sys
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
GUIDANCE_FILE = REPOSITORY_ROOT / "gitcommit.md"
SENSITIVE_PATTERN = re.compile(
    r"(?:api[_-]?key|secret|password|token|private[_-]?key|aws_access_key|"
    r"database_url)\s*[:=]\s*['\"]?[^\s'\"]{8,}|"
    r"AKIA[0-9A-Z]{16}|-----BEGIN [A-Z ]*PRIVATE KEY-----",
    re.IGNORECASE,
)


def git(*arguments: str, capture: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *arguments],
        cwd=REPOSITORY_ROOT,
        check=False,
        text=True,
        capture_output=capture,
    )


def print_command_output(*arguments: str) -> None:
    result = git(*arguments, capture=True)
    output = result.stdout.strip()
    if output:
        print(output)
    if result.returncode:
        print(result.stderr.strip(), file=sys.stderr)
        raise SystemExit(result.returncode)


def main() -> int:
    if not GUIDANCE_FILE.is_file():
        print(f"[ERROR] Commit guidance not found: {GUIDANCE_FILE}")
        return 1

    guidance = GUIDANCE_FILE.read_text(encoding="utf-8")
    if "Conventional Commits" not in guidance:
        print("[ERROR] gitcommit.md does not contain the expected commit policy.")
        return 1

    status = git("status", "--short", capture=True).stdout.strip()
    if not status:
        print("[INFO] No changes to commit.")
        return 0

    print("[INFO] Commit review based on gitcommit.md")
    print("\n[CONTEXT] Current branch:")
    print_command_output("branch", "--show-current")
    print("\n[CONTEXT] Working tree:")
    print(status)
    print("\n[CONTEXT] Recent commits:")
    print_command_output("log", "--oneline", "-5")

    diff = git("diff", "HEAD", "--no-ext-diff", capture=True).stdout
    untracked_files = git(
        "ls-files", "--others", "--exclude-standard", capture=True
    ).stdout.splitlines()
    untracked_contents = []
    for relative_path in untracked_files:
        candidate = REPOSITORY_ROOT / relative_path
        if candidate.is_file():
            untracked_contents.append(candidate.read_text(encoding="utf-8", errors="replace"))
    matches = SENSITIVE_PATTERN.findall(diff + "\n".join(untracked_contents))
    environment_files = [
        path for path in untracked_files if Path(path).name == ".env" or Path(path).name.startswith(".env.")
    ]
    if matches or environment_files:
        print("[WARNING] Sensitive-looking terms were found in the diff.")
        print("[INFO] Review them manually; this helper will not stage or commit.")
        return 1

    print("\n[REVIEW] No sensitive-data patterns found in the diff.")
    candidates = [
        "feat(storage): update persistence layer",
        "chore: update project automation",
        "docs: update project guidance",
    ]
    print("\n[PROPOSED COMMIT MESSAGES]")
    for index, candidate in enumerate(candidates, start=1):
        print(f"{index}. {candidate}")

    choice = input("Choose 1-3, or type a custom conventional-commit subject: ").strip()
    if choice in {"1", "2", "3"}:
        subject = candidates[int(choice) - 1]
    else:
        subject = choice

    if not subject:
        print("[INFO] Commit cancelled.")
        return 0
    if len(subject) > 72 or not re.match(r"^[a-z]+(?:\([^)]+\))?!?: .+[^.]$", subject):
        print("[ERROR] Use a conventional-commit subject no longer than 72 characters.")
        return 1

    needs_body = any("migrations/" in path for path in status.splitlines())
    body = input("Commit body (required for migrations; blank otherwise): ").strip()
    if needs_body and not body:
        print("[ERROR] A body is required because this commit contains a migration.")
        return 1

    confirmation = input("Stage all changes and create this commit? [y/N] ").strip().lower()
    if confirmation not in {"y", "yes"}:
        print("[INFO] Commit cancelled.")
        return 0

    if git("add", "--all").returncode != 0:
        return 1
    command = ["commit", "-m", subject]
    if body:
        command.extend(["-m", body])
    return git(*command).returncode


if __name__ == "__main__":
    raise SystemExit(main())
