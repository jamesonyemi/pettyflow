# Change Control and Main-Branch Guardrails

PettyFlow uses an approval-first delivery flow. Passing automation is necessary but never sufficient authorization to merge.

## Required flow

1. Implement changes on a non-`main` branch.
2. Inspect the complete diff and check for secrets or generated artifacts.
3. Run `git diff --check`, targeted tests, and the repository pre-push verification.
4. Open a pull request using the repository template.
5. Ask the user to review the exact diff and check results.
6. Wait for explicit approval in the current conversation and a required repository-owner review.
7. Merge the approved pull request through GitHub; never push directly to `main`.
8. Verify that `origin/main` contains the merge and that the local working tree is clean.

## Required GitHub repository settings

Configure these settings for `main` in GitHub branch protection:

- Require a pull request before merging
- Require one approval from `@jamesonyemi`
- Dismiss stale approvals when new commits are pushed
- Require branches to be up to date before merging
- Require the `test-and-verify` status check
- Require conversation resolution
- Restrict direct pushes and force pushes
- Do not allow administrators to bypass these rules

The repository files enforce review intent and CI evidence; GitHub branch protection enforces the server-side merge boundary. Both layers are required.

## Stop conditions

Stop and request direction if the diff contains secrets, required checks are missing or failing, a reviewer requests changes, approval is ambiguous, or the target branch is `main` without an approved pull request.
