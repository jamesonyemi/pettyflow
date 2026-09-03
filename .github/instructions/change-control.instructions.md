---
description: Enforce approval-first change control for PettyFlow repository work
applyTo: '**/*'
---

# Change control

- Never merge or push changes directly to `main` without explicit user approval in the current conversation.
- Before requesting approval, inspect the complete diff, check for secrets, run the smallest relevant tests, run the repository pre-push verification when available, and report the exact results.
- Keep implementation, review, approval, and merge as separate steps. Do not treat a clean branch or a passing CI run as approval.
- Ask the user to review the diff and wait for an unambiguous approval before merging.
- After approval, merge only through an approved pull request and verify the resulting `main` commit and working-tree state.
- If checks fail, stop the merge flow and report the failure; do not bypass hooks, required checks, or branch protection.
