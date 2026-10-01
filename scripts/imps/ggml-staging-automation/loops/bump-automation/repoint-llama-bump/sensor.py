#!/usr/bin/env python3
"""Wait for an upstream merge before launching the paired repoint Imp.

The fix Imp arms this as a one-shot Watch with the upstream and bump PR URLs.
../README.md owns that handoff and the Run Id convention. CLOSED without a
merge deliberately keeps the Watch pending for a human to judge; it is not a
successful reconciliation. Launches go to stdout, diagnostics to stderr.
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
    # A human can arm this Watch directly with arbitrary URLs; reject a
    # mis-arm before querying GitHub so every Tick diagnoses the same input.
    exactly_two_arguments = len(sys.argv) == 3
    if not exactly_two_arguments:
        raise SystemExit("usage: sensor.py <upstream-pr-url> <bump-pr-url>")
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
        "sigil": "repoint-llama-bump",
        "id": f"fix-bump-pr-{bump_match.group(1)}-repoint",
        "args": [upstream_pr_url, bump_pr_url],
    }
    print(json.dumps(launch_body))


if __name__ == "__main__":
    main()
