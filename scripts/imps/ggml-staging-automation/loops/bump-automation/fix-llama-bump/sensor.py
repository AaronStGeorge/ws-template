#!/usr/bin/env python3
"""Discover failing automation bump PRs for the paired fix Imp.

../README.md owns the loop's Sensor boundaries and Run Id conventions. This
standing Sensor keeps no discovery state: a PR number produces the same Run
Id each Tick, and the Daemon rejects duplicates while it retains that Id.
Only the automation's bump branch qualifies; unrelated PRs are outside this
loop. Launches go to stdout and diagnostics to the watch log via stderr.
"""

import json
import subprocess
import sys

# The automation opens every bump PR from this branch; it is the Sensor's
# whole notion of "a bump PR".
AUTOMATION_HEAD_BRANCH = "users/automation/bump-submodules"


def main():
    # Surface a mis-armed human Watch in the log instead of ignoring its args.
    armed_with_arguments = len(sys.argv) > 1
    if armed_with_arguments:
        raise SystemExit("usage: sensor.py (takes no arguments)")

    # stdout=PIPE only, never capture_output — a Launch must never
    # carry gh's stdout by accident, but gh's stderr flows through to the
    # watch log, where a failed call is diagnosable by the message gh
    # printed.
    open_prs = json.loads(
        subprocess.run(
            ["gh", "pr", "list",
             "--repo", "ROCm/ggml-staging-automation",
             "--state", "open",
             "--json", "number,url,headRefName"],
            check=True,
            stdout=subprocess.PIPE,
            text=True,
        ).stdout
    )

    for pr in open_prs:
        is_automation_bump = pr["headRefName"] == AUTOMATION_HEAD_BRANCH
        if not is_automation_bump:
            continue

        rollup = json.loads(
            subprocess.run(
                ["gh", "pr", "view", pr["url"],
                 "--json", "statusCheckRollup"],
                check=True,
                stdout=subprocess.PIPE,
                text=True,
            ).stdout
        )["statusCheckRollup"]
        has_failed_check = any(
            check.get("conclusion") == "FAILURE" for check in rollup
        )
        if not has_failed_check:
            print(f"bump PR {pr['url']} is not red; skipping",
                  file=sys.stderr)
            continue

        # One Launch per discovery; the Run Id derives from the PR
        # number alone, so later Ticks are rejected as duplicates during
        # this Daemon's lifetime. A restart forgets the occupied Run Ids.
        launch_body = {
            "sigil": "fix-llama-bump",
            "id": f"fix-bump-pr-{pr['number']}",
            "args": [pr["url"]],
        }
        print(json.dumps(launch_body))


if __name__ == "__main__":
    main()
