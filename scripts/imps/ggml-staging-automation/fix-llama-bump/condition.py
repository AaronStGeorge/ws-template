#!/usr/bin/env python3
"""Condition script: discover red bump PRs, emit fix-llama-bump launches.

Paired with the imp.py beside it — this script exists only to launch it;
the imps README is the authoritative record of the pair.

The standing sensor of the bump loop: no arguments; each Tick it queries
GitHub via `gh` for open automation bump PRs with failing CI in
ROCm/ggml-staging-automation and emits one Launch Body per discovery, one
JSON per line on stdout. Run Id `fix-bump-pr-<N>` derives from the PR
number alone — emission is idempotent because the Daemon rejects duplicate
Run Ids, so this script never tracks what it already launched. Stderr and
exit code are diagnostics only (they land in impwatch's watch log).

"Red" means the PR's statusCheckRollup holds at least one check whose
conclusion is FAILURE — a rollup still churning without a failure yet is
"not yet", and the next Tick sees it again. Only PRs whose head is the
automation's bump branch count; humans' PRs in the staging repo are none
of this sensor's business. Stdout is sacred: Launch Bodies only, one JSON
per line — everything narrative goes to stderr.
"""

import json
import subprocess
import sys

# The automation opens every bump PR from this branch; it is the sensor's
# whole notion of "a bump PR".
AUTOMATION_HEAD_BRANCH = "users/automation/bump-submodules"


def main():
    # argv is the provenance boundary (armed over impwatch's command line);
    # this sensor takes nothing, so anything present is a mis-arm worth
    # surfacing in the watch log rather than silently ignoring.
    armed_with_arguments = len(sys.argv) > 1
    if armed_with_arguments:
        raise SystemExit("usage: condition.py (takes no arguments)")

    # stdout=PIPE only, never capture_output — a Launch Body must never
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

        # One Launch Body per discovery; the Run Id derives from the PR
        # number alone, so a bump PR gets at most one automatic fix, ever —
        # re-emission on later Ticks is a rejected duplicate, not new work.
        launch_body = {
            "imp": "fix-llama-bump",
            "id": f"fix-bump-pr-{pr['number']}",
            "args": [pr["url"]],
        }
        print(json.dumps(launch_body))


if __name__ == "__main__":
    main()
