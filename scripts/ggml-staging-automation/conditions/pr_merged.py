#!/usr/bin/env python3
"""Condition script: when an upstream PR merges, emit the repoint launch.

Paired with ../imps/repoint-llama-bump/imp.py — this script exists only to
launch it; the imps README is the authoritative record of the pair.

The reconcile sensor of the bump loop: argv carries the upstream PR URL to
watch, then the original bump PR URL. Emits nothing until `gh` reports the
upstream PR merged, then emits the single repoint-llama-bump Launch Body
(Run Id `fix-bump-pr-<N>-repoint`, N from the bump PR). Armed by
fix-llama-bump runs as a clearing Watch Row, so it normally fires exactly
once. Stderr and exit code are diagnostics only.

Any upstream PR state other than MERGED — including CLOSED — is "not
yet": a closed-unmerged upstream PR keeps the Watch Row pending forever,
which is the wanted behavior, since the human sees it in the pending list
and judges. Stdout is sacred: the one Launch Body only — everything
narrative goes to stderr.
"""

import json
import re
import subprocess
import sys

# Same narrow pattern the fix Imp validates with; the capture group
# is the bump PR number the repoint Run Id derives from.
STAGING_PR_URL = re.compile(
    r"https://github\.com/ROCm/ggml-staging-automation/pull/(\d+)"
)


def main():
    # argv is the provenance boundary: normally armed by the fix imp
    # from already-validated values, but arming is a command line a human
    # can also drive — so both arguments are checked here, before any
    # network call, and a mis-arm crashes visibly on every Tick.
    exactly_two_arguments = len(sys.argv) == 3
    if not exactly_two_arguments:
        raise SystemExit("usage: pr_merged.py <upstream-pr-url> <bump-pr-url>")
    upstream_pr_url = sys.argv[1]
    bump_match = STAGING_PR_URL.fullmatch(sys.argv[2])
    if bump_match is None:
        raise SystemExit(
            f"not a ROCm/ggml-staging-automation PR URL: {sys.argv[2]}"
        )
    bump_pr_url = bump_match.group(0)

    # stdout=PIPE only: the parsed value is stdout's, while gh's stderr
    # flows through to the watch log — a failed call is diagnosable only by
    # the message gh printed.
    state = subprocess.run(
        ["gh", "pr", "view", upstream_pr_url, "--json", "state",
         "--jq", ".state"],
        check=True,
        stdout=subprocess.PIPE,
        text=True,
    ).stdout.strip()

    upstream_is_merged = state == "MERGED"
    if not upstream_is_merged:
        # Emitting nothing is the contract's "not yet"; the note keeps the
        # watch log readable across the days this row may pend.
        print(f"upstream PR {upstream_pr_url} is {state}, not MERGED; "
              "not firing", file=sys.stderr)
        return

    launch_body = {
        "imp": "repoint-llama-bump",
        "id": f"fix-bump-pr-{bump_match.group(1)}-repoint",
        "args": [upstream_pr_url, bump_pr_url],
    }
    print(json.dumps(launch_body))


if __name__ == "__main__":
    main()
