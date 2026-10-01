#!/usr/bin/env python3
"""Refresh the llama.cpp row after the bump loop changes its pin or remote.

Both Imp wrappers invoke this executable, and the repair agent calls it after
pushes. README.md documents invocation and outcomes. Keeping
it executable also lets the agent use the same operation as the wrappers.

The row is derived from the contents API and .gitmodules at one commit. A
caller that just pushed must supply --head: PR 57 once overwrote a canonical
row with stale fork coordinates because the PR API still reported its old
head. Only the llama.cpp row is owned here; hrx-system and the bot's "From"
cell are preserved. An unrecognized table is left intact so template drift
cannot silently damage the rest of the PR description.
"""

import argparse
import base64
import json
import re
import subprocess
import sys

BUMP_PR_URL = re.compile(
    r"https://github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+)/pull/(?P<number>\d+)"
)

# The bot's llama.cpp row: link cell, branch cell, From cell, To cell. Only
# the From cell is kept verbatim; the other three are regenerated.
LLAMA_ROW = re.compile(
    r"^\| \[`llama\.cpp`\]\([^)]*\) \| (?P<branch>[^|]*) \| "
    r"(?P<from_cell>[^|]*) \| (?P<to_cell>[^|]*) \|[ \t]*$",
    re.MULTILINE,
)


def gh_json(argv):
    """Run a gh command whose stdout is JSON, stderr flowing to the log."""
    return json.loads(
        subprocess.run(
            argv, check=True, stdout=subprocess.PIPE, text=True
        ).stdout
    )


def tracked_branch(gitmodules_text):
    """The `branch =` of the llama.cpp section, or None when unset."""
    in_llama_section = False
    for line in gitmodules_text.splitlines():
        stripped = line.strip()
        is_section_header = stripped.startswith("[submodule ")
        if is_section_header:
            in_llama_section = stripped == '[submodule "llama.cpp"]'
            continue
        is_branch_line = in_llama_section and stripped.startswith("branch")
        if is_branch_line:
            return stripped.split("=", 1)[1].strip()
    return None


def read_head_state(owner, repo, pr_url, head_sha):
    """Everything the row needs, from `head_sha` (or the PR's head)."""
    view = gh_json(["gh", "pr", "view", pr_url, "--json", "headRefOid,body"])
    if head_sha is None:
        head_sha = view["headRefOid"]

    submodule = gh_json(
        ["gh", "api", f"repos/{owner}/{repo}/contents/llama.cpp?ref={head_sha}"]
    )
    gitmodules = gh_json(
        ["gh", "api", f"repos/{owner}/{repo}/contents/.gitmodules?ref={head_sha}"]
    )
    gitmodules_text = base64.b64decode(gitmodules["content"]).decode("utf-8")

    repo_web_url = re.sub(r"\.git$", "", submodule["submodule_git_url"])
    return {
        "body": view["body"],
        "pin": submodule["sha"],
        "repo_web_url": repo_web_url,
        "repo_slug": repo_web_url.removeprefix("https://github.com/"),
        "branch": tracked_branch(gitmodules_text) or "(no tracked branch)",
    }


def rewrite_row(body, state):
    """Overwrite the llama.cpp row's regenerated cells; None if no row."""
    match = LLAMA_ROW.search(body)
    if match is None:
        return None
    pin_link = f"[`{state['pin'][:12]}`]({state['repo_web_url']}/commit/{state['pin']})"
    row = (
        f"| [`llama.cpp`]({state['repo_web_url']}) | `{state['branch']}` | "
        f"{match.group('from_cell')} | {pin_link} |"
    )
    return body[: match.start()] + row + body[match.end():]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("bump_pr_url", help="the bump PR whose body to sync")
    parser.add_argument(
        "--head",
        default=None,
        help="the head sha to sync from; pass the sha you just pushed",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the new body to stderr instead of editing the PR",
    )
    args = parser.parse_args()

    # A human or repair agent can supply a PR URL outside the expected shape.
    match = BUMP_PR_URL.fullmatch(args.bump_pr_url)
    if match is None:
        raise SystemExit(f"not a GitHub PR URL: {args.bump_pr_url!r}")
    owner, repo = match.group("owner"), match.group("repo")

    state = read_head_state(owner, repo, args.bump_pr_url, args.head)
    print(
        f"head pins llama.cpp `{state['branch']}` on {state['repo_slug']} "
        f"at {state['pin'][:12]}",
        file=sys.stderr,
    )

    body = rewrite_row(state["body"], state)
    row_was_found = body is not None
    if not row_was_found:
        print(
            "warning: no llama.cpp row found in the PR body; leaving it "
            "untouched",
            file=sys.stderr,
        )
        return

    body_is_unchanged = body == state["body"]
    if body_is_unchanged:
        print("PR body already in sync; nothing to do", file=sys.stderr)
        return
    if args.dry_run:
        print(body, file=sys.stderr)
        return
    subprocess.run(
        ["gh", "pr", "edit", args.bump_pr_url, "--body-file", "-"],
        check=True,
        input=body,
        text=True,
        stdout=sys.stderr.fileno(),
    )
    print("PR body synced", file=sys.stderr)


if __name__ == "__main__":
    main()
