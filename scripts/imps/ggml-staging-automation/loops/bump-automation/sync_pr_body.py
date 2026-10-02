#!/usr/bin/env python3
"""Refresh llama.cpp's pin and links in the bump PR description.

Both Imp wrappers invoke this executable, and the repair agent calls it after
pushes. README.md documents invocation and outcomes. Keeping
it executable also lets the agent use the same operation as the wrappers.

The row is derived from the contents API and .gitmodules at one commit. A
caller that just pushed must supply --head: PR 57 once overwrote a canonical
row with stale fork coordinates because the PR API still reported its old
head. Only llama.cpp's repository, branch, and "To" pin are refreshed;
hrx-system and both "From" pins retain their values and link destinations.

The staging workflow owns the initial template. Reference links and branch
names outside the table keep rows below GitHub's observed 72-column squash
wrapping. The synchronizer updates only llama.cpp's To label, branch line,
and repository/To link definitions. Everything else is left verbatim.
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


def rewrite_body(body, state):
    r"""Replace the four llama.cpp fields; None if any is missing or repeated.

    The producer is ROCm/ggml-staging-automation's
    .github/workflows/bump_submodules.yml, in the Create pull request step's
    `body`. Its relevant Markdown looks like this (HRX entries omitted):

        | Submodule | From | To |
        | --- | --- | --- |
        | [llama.cpp][l] | [`2ba2ddc76a8a`][l0] | [`3cbd76abae6e`][l1] |

        Tracked branches:
        - llama.cpp: `hrx-graph-develop-v2`

        [l]:
          <repository-url>
        [l0]:
          <from-repository-url>/commit/<full-from-sha>
        [l1]:
          <repository-url>/commit/<full-to-sha>

    `l` names the repository, `l0` the From commit, and `l1` the To commit.
    The four patterns below match, in order:
    1. A backtick-wrapped label followed by [l1], replaced with pin[:12].
    2. The line starting '- llama.cpp: ', replaced with the tracked branch.
    3. The [l]: definition and its indented URL, replaced with repo_web_url.
    4. The [l1]: definition and its indented URL, replaced with the full
       commit URL built from repo_web_url and pin.

    MULTILINE makes ^ mean the start of each line. \r?\n accepts LF or CRLF;
    [ \t]+ requires indentation before a definition's URL. Negated character
    classes stop at line endings (and, for the hash label, at a backtick).
    These are independent matches, not a parser for the surrounding table.
    From links, HRX entries, and all other text remain untouched.

    Each pattern must match exactly once. Otherwise main leaves the original
    PR body intact instead of publishing a partial or ambiguous update. The
    replacement lambda inserts state values literally, without interpreting
    backslashes as regex replacement escapes.

    If the producer's format changes, update this example and the affected
    patterns together. Inspect the raw PR body with `gh pr view <url> --json
    body`, then run this script with `<url> --dry-run` to review the result.
    """
    replacements = (
        (r"\[`[^`\r\n]+`\]\[l1\]", f"[`{state['pin'][:12]}`][l1]"),
        (r"^- llama\.cpp: [^\r\n]*", f"- llama.cpp: `{state['branch']}`"),
        (r"^\[l\]:\r?\n[ \t]+[^\r\n]+", f"[l]:\n  {state['repo_web_url']}"),
        (
            r"^\[l1\]:\r?\n[ \t]+[^\r\n]+",
            f"[l1]:\n  {state['repo_web_url']}/commit/{state['pin']}",
        ),
    )
    for pattern, replacement in replacements:
        body, count = re.subn(
            pattern, lambda match: replacement, body, flags=re.MULTILINE
        )
        if count != 1:
            return None
    return body


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

    body = rewrite_body(state["body"], state)
    fields_were_found = body is not None
    if not fields_were_found:
        print(
            "warning: missing or repeated llama.cpp fields; leaving PR body untouched",
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
