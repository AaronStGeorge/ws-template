#!/usr/bin/env python3
"""Imp: repoint a bump PR from the validation fork to merged upstream.

Launched by its paired sensor, the condition.py beside this file, which
the fix imp (../fix-llama-bump/imp.py) arms; the
ggml-staging-automation README section `repoint-llama-bump` is the
authoritative boundary. Argv carries the merged upstream PR URL then
the original bump PR URL; exit 0 iff there was nothing to do or the bump PR
is green on the repointed submodule.

First check whether the bump PR is still open — the automation recreates
bump PRs freely, and any state other than OPEN (CLOSED and MERGED alike)
makes this Run a green no-op (say which state on stderr and exit 0; the
next bump PR's fix run absorbs the merged upstream by preference). A bump
PR that MERGED while still pointed at the fork is a human matter — the
no-op log line names the state so the human can spot it.

Otherwise the repoint is mechanical — no agent, and no submodule checkout:
clone the staging repo, check out the PR's head branch, rewrite
`.gitmodules` (canonical AMD-Ecosystem url, `hrx-graph-develop-v2`
branch), and pin llama.cpp at the upstream PR's merge commit via
`git update-index --cacheinfo 160000,<sha>,llama.cpp` — a submodule pin
is an ordinary tree entry, so the index edit alone suffices and the
submodule is never initialized. All of it lands as one commit — the
llama.cpp change complete in itself, hrx-system untouched, per the
never-alone rule — pushed to the PR's head branch. The PR body is then
synced from the pushed head by ``sync_pr_body.py`` (shared by both imps,
one level above this file), so the description's llama.cpp row names the
canonical pin again.

Then CI decides: `gh pr checks --watch` runs until the checks settle, and
its exit code is the verdict. Green is expected, because the identical
change was already validated on this PR via the fork; that expectation is
why there are no retry semantics.

The watch is gated. Checks take a beat to attach after a push, and until
they do the PR still reports the previous head's checks — a watch started
too early returns that stale green in seconds (it happened: PR 57's
repoint run went green before its CI had begun). So the imp polls until
the PR's head is the pushed commit and its rollup holds checks, within a
bounded wait that fails the Run loudly.

Red means launching an agent whose only job is a diagnosis written to
stderr — a guess at what went wrong and what a human should check, under
an explicit change-NOTHING rule (no commits, no pushes, no fixes) — and
exiting nonzero so the failed Run summons the human.

impd gotchas: Imp stdout is discarded and stderr is captured as the
Run's log, so codex's stdout is redirected onto stderr and every wrapper
print goes there too — nothing meaningful may touch stdout. Paths derive
from mkdtemp and argv; the daemon-inherited cwd is relied on for nothing.
"""

import argparse
import json
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

# Both patterns are the provenance gate: argv normally comes from
# condition.py's already-validated Launch Body, but humans launch by hand
# too, so anything but the two real PR-URL shapes is refused before any
# subprocess starts. The bump capture group is the PR number the slug
# derives from. STAGING_PR_URL is duplicated from the fix imp —
# imps are self-contained by design, never importing across imp
# directories.
UPSTREAM_PR_URL = re.compile(
    r"https://github\.com/AMD-Ecosystem/llama\.cpp/pull/(\d+)"
)
STAGING_PR_URL = re.compile(
    r"https://github\.com/ROCm/ggml-staging-automation/pull/(\d+)"
)

# Cloned directly — the imp is self-contained and depends on no local
# checkout of the staging repo.
STAGING_REPO_URL = "git@github.com:ROCm/ggml-staging-automation.git"

# The canonical submodule coordinates the repoint restores: the fix run
# left .gitmodules pointing at the personal fork, and these two values are
# what "back on upstream" means.
CANONICAL_LLAMA_URL = "https://github.com/AMD-Ecosystem/llama.cpp.git"
CANONICAL_LLAMA_BRANCH = "hrx-graph-develop-v2"

# The whole machine-read handoff is one string: the diagnosis agent
# changes nothing, so there is nothing structured to report beyond its
# guess for the human.
DIAGNOSIS_SCHEMA = {
    "type": "object",
    "properties": {
        "diagnosis": {"type": "string"},
    },
    "required": ["diagnosis"],
    "additionalProperties": False,
}

# Diagnosis only — the change-NOTHING rule is the load-bearing line: the
# repointed commit is already pushed, and a failed Run summoning the human
# is the designed outcome, so any agent-made fix here would paper over a
# state the human is meant to see.
DIAGNOSIS_INSTRUCTIONS = """\
The mechanical repoint of the llama.cpp submodule to the merged upstream
PR {upstream_pr_url} went red on the bump PR {bump_pr_url}.

This workspace holds a clone of ggml-staging-automation checked out on
the PR's head branch `{head_branch}`, with the repoint commit already
pushed; submodules are not initialized. Read the failing logs — `gh pr
checks {bump_pr_url}`, `gh run view --log-failed` — and produce a
diagnosis: a guess at what went wrong and what a human should check.

Change NOTHING: no commits, no pushes, no fixes. The diagnosis is the
entire job; a human takes it from here.
"""


def run_to_log(argv, **kwargs):
    """Run a subprocess whose stdout belongs in the Run log.

    Under impd, Imp stdout is discarded and stderr is the Run's log —
    so anything a child would print (git progress, `gh pr checks` watch
    output, codex's agent transcript) is redirected onto stderr to
    survive. Duplicated from the fix imp: imps are
    self-contained by design.
    """
    return subprocess.run(argv, stdout=sys.stderr.fileno(), **kwargs)


# Checks attach to a fresh head within seconds normally; minutes only when
# GitHub is degraded. Past this the Run fails rather than guessing.
CHECKS_ATTACH_TIMEOUT_SECONDS = 600
CHECKS_ATTACH_POLL_SECONDS = 15


def wait_for_checks_to_attach(bump_pr_url, pushed_sha):
    """Block until the bump PR's head is `pushed_sha` and has checks.

    `gh pr checks` reads the PR's current head and its statusCheckRollup;
    right after a push GitHub can still serve the previous head, or the new
    head with an empty rollup, and a watch started then returns the old
    verdict almost immediately. Both facts must hold before the watch means
    anything. Past the bounded wait, exit nonzero: a Run that cannot tell
    what CI thinks must summon the human, never guess green.
    """
    deadline = time.monotonic() + CHECKS_ATTACH_TIMEOUT_SECONDS
    while True:
        view = json.loads(
            subprocess.run(
                ["gh", "pr", "view", bump_pr_url,
                 "--json", "headRefOid,statusCheckRollup"],
                check=True,
                stdout=subprocess.PIPE,
                text=True,
            ).stdout
        )
        head_is_pushed_commit = view["headRefOid"] == pushed_sha
        rollup_has_checks = len(view["statusCheckRollup"]) > 0
        checks_are_attached = head_is_pushed_commit and rollup_has_checks
        if checks_are_attached:
            print(
                f"checks attached to {pushed_sha[:8]}: "
                f"{len(view['statusCheckRollup'])} in the rollup",
                file=sys.stderr,
            )
            return

        timed_out = time.monotonic() >= deadline
        if timed_out:
            raise SystemExit(
                f"no checks attached to pushed commit {pushed_sha} on "
                f"{bump_pr_url} within {CHECKS_ATTACH_TIMEOUT_SECONDS}s "
                f"(head seen: {view['headRefOid'][:8]}, rollup size "
                f"{len(view['statusCheckRollup'])}); cannot take a CI "
                "verdict"
            )
        time.sleep(CHECKS_ATTACH_POLL_SECONDS)


def main():
    # The flow: validate argv → closed-PR no-op check → mechanical repoint
    # (clone, .gitmodules rewrite, cacheinfo pin, one commit, push) →
    # watch CI for the verdict → on red, diagnosis agent and a failing
    # exit.
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "upstream_pr_url", help="the merged AMD-Ecosystem/llama.cpp PR URL"
    )
    parser.add_argument(
        "bump_pr_url", help="the original ggml-staging-automation bump PR URL"
    )
    args = parser.parse_args()

    upstream_match = UPSTREAM_PR_URL.fullmatch(args.upstream_pr_url)
    if upstream_match is None:
        raise SystemExit(
            f"not an AMD-Ecosystem/llama.cpp PR URL: {args.upstream_pr_url}"
        )
    upstream_pr_url = upstream_match.group(0)

    bump_match = STAGING_PR_URL.fullmatch(args.bump_pr_url)
    if bump_match is None:
        raise SystemExit(
            f"not a ROCm/ggml-staging-automation PR URL: {args.bump_pr_url}"
        )
    bump_pr_url = bump_match.group(0)

    # slug == the Run Id convention condition.py emits, so the /tmp
    # workspace traces to its Run without this process ever being told its
    # Run Id.
    slug = f"fix-bump-pr-{bump_match.group(1)}-repoint"

    # stdout=PIPE only, never capture_output: the parsed value comes from
    # stdout, while gh's stderr must flow through to the Run log — a failed
    # call in a headless run is diagnosable only by the message gh printed.
    state = subprocess.run(
        ["gh", "pr", "view", bump_pr_url, "--json", "state",
         "--jq", ".state"],
        check=True,
        stdout=subprocess.PIPE,
        text=True,
    ).stdout.strip()

    # The green no-op: the automation recreates bump PRs freely, and a
    # bump PR gone non-OPEN needs no repoint — the next bump PR's fix run
    # absorbs the merged upstream by preference. MERGED lands here too:
    # a bump PR merged while fork-pointed is a human matter, and naming
    # the state in the log is this Run's whole contribution to it.
    bump_pr_is_open = state == "OPEN"
    if not bump_pr_is_open:
        print(
            f"bump PR {bump_pr_url} is {state}, not OPEN — nothing to "
            f"repoint; the next bump PR's fix run absorbs "
            f"{upstream_pr_url} by preference. (A bump PR MERGED while "
            "still fork-pointed needs a human look.)",
            file=sys.stderr,
        )
        return

    head_branch = subprocess.run(
        ["gh", "pr", "view", bump_pr_url, "--json", "headRefName",
         "--jq", ".headRefName"],
        check=True,
        stdout=subprocess.PIPE,
        text=True,
    ).stdout.strip()

    merge_commit_sha = subprocess.run(
        ["gh", "pr", "view", upstream_pr_url, "--json", "mergeCommit",
         "--jq", ".mergeCommit.oid"],
        check=True,
        stdout=subprocess.PIPE,
        text=True,
    ).stdout.strip()

    wsdir = Path(tempfile.mkdtemp(prefix=f"{slug}-"))
    clone = wsdir / "ggml-staging-automation"
    run_to_log(
        ["git", "clone", "--origin", "origin", STAGING_REPO_URL, str(clone)],
        check=True,
    )
    run_to_log(["git", "checkout", head_branch], cwd=clone, check=True)

    print(f"run workspace: {wsdir}", file=sys.stderr)

    # The mechanical repoint, as index-level edits only: .gitmodules back
    # to the canonical coordinates, and the pin moved to the upstream
    # merge commit via cacheinfo — a submodule pin is an ordinary
    # mode-160000 tree entry, so no submodule is ever checked out. One
    # commit carries all of it: a complete llama.cpp change, hrx-system
    # untouched, per the never-alone rule.
    run_to_log(
        ["git", "config", "-f", ".gitmodules",
         "submodule.llama.cpp.url", CANONICAL_LLAMA_URL],
        cwd=clone,
        check=True,
    )
    run_to_log(
        ["git", "config", "-f", ".gitmodules",
         "submodule.llama.cpp.branch", CANONICAL_LLAMA_BRANCH],
        cwd=clone,
        check=True,
    )
    run_to_log(
        ["git", "update-index",
         "--cacheinfo", f"160000,{merge_commit_sha},llama.cpp"],
        cwd=clone,
        check=True,
    )
    run_to_log(["git", "add", ".gitmodules"], cwd=clone, check=True)
    run_to_log(
        ["git", "commit",
         "-m", "Repoint llama.cpp at merged upstream",
         "-m", f"Consumes {upstream_pr_url} in place of the fork-carried "
               "validation branch."],
        cwd=clone,
        check=True,
    )
    run_to_log(["git", "push", "origin", head_branch], cwd=clone, check=True)
    pushed_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=clone,
        check=True,
        stdout=subprocess.PIPE,
        text=True,
    ).stdout.strip()

    # CI is the verdict: `gh pr checks --watch` follows the checks to
    # completion and exits 0 iff every check passed — that fact alone
    # decides the Run. The watch only means something once the checks
    # belong to the commit just pushed; the gate is the header's story.
    wait_for_checks_to_attach(bump_pr_url, pushed_sha)

    # Truthful description: rewrite the body's llama.cpp row from the
    # pushed head — by sha, since the PR's reported head can lag the
    # push. Cosmetic by design — logged on failure, never fatal; the
    # verdict below is CI alone.
    imps_dir = Path(__file__).resolve().parents[1]
    sync = run_to_log(
        [sys.executable, str(imps_dir / "sync_pr_body.py"), bump_pr_url,
         "--head", pushed_sha]
    )
    if sync.returncode != 0:
        print("warning: PR body sync failed; body may be stale", file=sys.stderr)
    checks = run_to_log(
        ["gh", "pr", "checks", bump_pr_url, "--watch", "--interval", "60"]
    )
    bump_pr_is_green = checks.returncode == 0
    if bump_pr_is_green:
        return

    # Red: the diagnosis agent. Its handoff is schema-forced to the one
    # string the human needs, printed into the Run log; the Run then fails
    # on purpose — a failed Run is what summons the human, and there are
    # no retry semantics anywhere in this imp.
    schema_path = wsdir / ".handoff-schema.json"
    schema_path.write_text(
        json.dumps(DIAGNOSIS_SCHEMA, indent=2) + "\n", encoding="utf-8"
    )
    handoff_path = wsdir / ".handoff.json"
    prompt = DIAGNOSIS_INSTRUCTIONS.format(
        upstream_pr_url=upstream_pr_url,
        bump_pr_url=bump_pr_url,
        head_branch=head_branch,
    )
    run_to_log(
        [
            "codex",
            "exec",
            "--dangerously-bypass-approvals-and-sandbox",
            "--skip-git-repo-check",
            "-C",
            str(clone),
            "--output-schema",
            str(schema_path),
            "--output-last-message",
            str(handoff_path),
            prompt,
        ],
        check=True,
    )
    handoff = json.loads(handoff_path.read_text(encoding="utf-8"))
    print(json.dumps(handoff, indent=2), file=sys.stderr)

    raise SystemExit(
        f"bump PR went red after the repoint: `gh pr checks {bump_pr_url} "
        f"--watch` exited {checks.returncode}; diagnosis above"
    )


if __name__ == "__main__":
    main()
