#!/usr/bin/env python3
"""Launch an isolated investigation of one reported perplexity failure.

Inputs are an automation Actions run URL, model identifier, and prefill/decode
regime. The run's automation commit owns the initial harness and dependency
pins. Codex owns reproduction, diagnosis and an optional llama.cpp repair,
using the methodology in that checkout's HRX repository.

The Run works in a workspace thread (lib/python/workspace_thread/README.md):
a worktree of this workspace at .workspace-threads/fix-ppl-<run>-<regime>
with the
ggml-staging-automation sourceset loaded, its clone detached at the run's
automation commit, and Codex hosted on its daemon so the investigation shows
live in the agent command center. The worktree retains the prompt, run
metadata and Markdown handoff. Zero means Codex delivered a report, not that
a repair was independently verified. Setup, execution and missing-report
failures exit nonzero. All output goes to stderr for impd's Run log; there is
no watcher or retry loop.
"""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

from workspace_thread import UNATTENDED_SETTINGS, prepare, run_turn

SOURCESET = "ggml-staging-automation"
FORK_PUSH_URL = "git@github.com:AaronStGeorge/llama.cpp.git"

PROMPT = """\
Reproduce, root-cause, and attempt to fix exactly the {regime} perplexity failure
for model {model} in {run_url}.

Your working directory is a worktree of this workspace. Its
sources/ggml-staging-automation/ checkout is at the run's automation commit
with its pinned submodules initialized. source-run.json records the run
metadata. Preserve these dependency pins for the initial reproduction;
do not switch to current integration HEAD.

Read sources/ggml-staging-automation/hrx-system/loom/docs/src/workflows/agent-driven-kernel-development.md
and use its approach to investigate and validate any repair. Inspect the run's
report, raw logs and artifact provenance; reproduce its model, corpus and
evaluation settings locally, preferably starting with its exact CI binaries.
Explain environment differences or inability to reproduce. Workflow status
alone does not establish numerical correctness.

Read AGENTS.md and the applicable style guides before editing. Before running
HRX, read docs/hrx-gpu-selection.md and pin a supported GPU as instructed.
`.venv/bin/python sources/build.py` builds this checkout. Keep experiments
and evidence in this worktree, without editing the main checkout's sources.

Limit the repair to this selected failure in llama.cpp. If the cause belongs
to HRX or the runner environment, report the evidence and remaining work.
Do not edit HRX. You may create and push a new llama.cpp fix branch only to
{fork_url}; origin's push URL is configured there. Do not force-push, push
other repositories, or create a PR.

Maintain llama.cpp's ggml/src/ggml-hrx/hrx-minimum-commit.txt when the repair
requires newer HRX APIs, Loom syntax, or numerical behavior, including removal
of a workaround after an HRX fix. Raise the floor in the same llama.cpp commit
to the earliest demonstrated required HRX commit that descends from the previous
floor. Do not bump it for unrelated repairs or merely to the latest tested HEAD.
Record the dependency and its HRX commit or PR in the handoff.

After the initial reproduction at the run's original pins, verify the selected
HRX revision with
`python3 sources/ggml-staging-automation/llama.cpp/ggml/src/ggml-hrx/tools/check_hrx_revision.py sources/ggml-staging-automation/hrx-system`
before validating a repair. Staging uses installed packages and bypasses the
HRX_SOURCE_DIR CMake check, so also establish that the tested HRX and Loom
libraries were built from that revision. Missing history or incompatible pins
require a handoff, not a silent dependency change. Historical llama.cpp revisions
without a minimum file have no recorded floor; if a repair adds an HRX dependency,
introduce the file with that required commit in the same repair.

Your final response must be a Markdown handoff report: reproduction results,
root-cause analysis, any fix and validation with evidence locations, remaining
uncertainty, and the optional pushed llama.cpp branch URL. A diagnosis without
a fix is valid. The launcher saves this final response as handoff.md.
"""


def run_to_log(argv, **kwargs):
    return subprocess.run(argv, stdout=sys.stderr.fileno(), **kwargs)


def prepare_checkout(workspace, root, source_sha):
    """Move the loaded clone onto the run's commit and guard its remotes."""
    clone = workspace / "sources" / "ggml-staging-automation"
    run_to_log(["git", "checkout", "--detach", source_sha], cwd=clone, check=True)
    run_to_log(["git", "submodule", "update", "--init", "--recursive"], cwd=clone, check=True)
    llama = clone / "llama.cpp"
    # Keep the existing workspace instructions and personal-fork push default.
    if (llama / "AGENTS.md").exists():
        (llama / "AGENTS.md").unlink()
        run_to_log(["git", "update-index", "--skip-worktree", "AGENTS.md"], cwd=llama, check=True)
    run_to_log(["git", "remote", "set-url", "--push", "origin", FORK_PUSH_URL], cwd=llama, check=True)
    for checkout in (clone, clone / "hrx-system"):
        run_to_log(["git", "remote", "set-url", "--push", "origin", "push-disabled"],
                   cwd=checkout, check=True)
    # The worktree is a checkout of this workspace, so docs/ and AGENTS.md
    # are in place; the venv is shared because Codex's daemon runs the
    # agent's shell without direnv. build.py is the sourceset's asset.
    (workspace / ".venv").symlink_to(root / ".venv")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_url", help="ROCm/ggml-staging-automation Actions run URL")
    parser.add_argument("model", help="exact model identifier in the run's perplexity report")
    parser.add_argument("regime", choices=("prefill", "decode"), help="the one regime to investigate")
    args = parser.parse_args()
    match = re.fullmatch(r"https://github.com/ROCm/ggml-staging-automation/actions/runs/([0-9]+)/?", args.run_url)
    if match is None:
        parser.error("expected a ROCm/ggml-staging-automation Actions run URL")
    number = match.group(1)
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/+-]{0,255}", args.model) is None:
        parser.error("model must be a nonempty report identifier, without whitespace")
    # Check access before allocating an expensive checkout.
    run_to_log(["codex", "login", "status"], check=True)
    run_to_log(["gh", "auth", "status"], check=True)
    source_run = json.loads(subprocess.check_output(
        ["gh", "api", f"repos/ROCm/ggml-staging-automation/actions/runs/{number}"], text=True))
    if source_run["status"] != "completed":
        raise ValueError("source run is not completed")
    source_sha = source_run["head_sha"]
    if re.fullmatch(r"[0-9a-f]{40}", source_sha) is None:
        raise ValueError("source run has an invalid head_sha")
    # Named as the README's launch example names the Run Id. A worktree
    # left by an earlier Run of the same run and regime makes this fail;
    # remove it (`git worktree remove .workspace-threads/<name>`) before relaunching.
    thread, client = prepare(
        f"fix-ppl-{number}-{args.regime}", SOURCESET,
        settings=UNATTENDED_SETTINGS, log=sys.stderr,
    )
    workspace = thread.worktree
    print(f"run workspace: {workspace}", file=sys.stderr)
    (workspace / "source-run.json").write_text(json.dumps(source_run, indent=2) + "\n")
    prepare_checkout(workspace, Path(__file__).resolve().parents[4], source_sha)
    prompt = PROMPT.format(model=args.model, regime=args.regime, run_url=args.run_url, fork_url=FORK_PUSH_URL)
    (workspace / "prompt.txt").write_text(prompt)
    result = run_turn(client, thread, prompt, log=sys.stderr)
    client.close()
    if result.status != "completed":
        raise ValueError(f"codex turn {result.status}: {result.error}")
    handoff = result.last_agent_message or ""
    if not handoff.strip():
        raise ValueError("Codex did not produce a handoff report")
    handoff_path = workspace / "handoff.md"
    handoff_path.write_text(handoff)
    print(handoff, file=sys.stderr)
    print(f"handoff report: {handoff_path}", file=sys.stderr)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, KeyError, TypeError, OSError, subprocess.CalledProcessError) as error:
        print(f"fix-llama-perplexity needs attention: {error}", file=sys.stderr)
        raise SystemExit(1)
