#!/usr/bin/env python3
"""Launch an isolated investigation of one reported perplexity failure.

Inputs are an automation Actions run URL, model identifier, and prefill/decode
regime. The run's automation commit owns the initial harness and dependency
pins. Codex owns reproduction, diagnosis and an optional llama.cpp repair,
using the methodology in that checkout's HRX repository.

The retained workspace contains the prompt, run metadata and Markdown handoff.
Zero means Codex delivered a report, not that a repair was independently
verified. Setup, execution and missing-report failures exit nonzero. All
output goes to stderr for impd's Run log; there is no watcher or retry loop.
"""

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

STAGING_URL = "git@github.com:ROCm/ggml-staging-automation.git"
FORK_PUSH_URL = "git@github.com:AaronStGeorge/llama.cpp.git"

PROMPT = """\
Reproduce, root-cause, and attempt to fix exactly the {regime} perplexity failure
for model {model} in {run_url}.

The isolated ggml-staging-automation/ checkout is at the run's automation
commit with its pinned submodules initialized. source-run.json records the
run metadata. Preserve these dependency pins for the initial reproduction;
do not switch to current integration HEAD.

Read ggml-staging-automation/hrx-system/loom/docs/src/workflows/agent-driven-kernel-development.md
and use its approach to investigate and validate any repair. Inspect the run's
report, raw logs and artifact provenance; reproduce its model, corpus and
evaluation settings locally, preferably starting with its exact CI binaries.
Explain environment differences or inability to reproduce. Workflow status
alone does not establish numerical correctness.

Read AGENTS.md and the applicable style guides before editing. Before running
HRX, read docs/hrx-gpu-selection.md and pin a supported GPU as instructed.
The provided .venv/bin/python build.py builds this isolated checkout. Keep
experiments and evidence in this workspace, without editing shared sources.

Limit the repair to this selected failure in llama.cpp. If the cause belongs
to HRX or the runner environment, report the evidence and remaining work.
Do not edit HRX. You may create and push a new llama.cpp fix branch only to
{fork_url}; origin's push URL is configured there. Do not force-push, push
other repositories, or create a PR.

Your final response must be a Markdown handoff report: reproduction results,
root-cause analysis, any fix and validation with evidence locations, remaining
uncertainty, and the optional pushed llama.cpp branch URL. A diagnosis without
a fix is valid. The launcher saves this final response as handoff.md.
"""


def run_to_log(argv, **kwargs):
    return subprocess.run(argv, stdout=sys.stderr.fileno(), **kwargs)


def prepare(workspace, root, source_sha):
    clone = workspace / "ggml-staging-automation"
    run_to_log(["git", "clone", STAGING_URL, str(clone)], check=True)
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
    for name in ("docs", ".venv", "AGENTS.md"):
        (workspace / name).symlink_to(root / name)
    here = Path(__file__).resolve().parent
    shutil.copy2(here.parent / "build.py", workspace / "build.py")


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
    workspace = Path(tempfile.mkdtemp(prefix=f"fix-ppl-{number}-{args.regime}-"))
    print(f"run workspace: {workspace}", file=sys.stderr)
    (workspace / "source-run.json").write_text(json.dumps(source_run, indent=2) + "\n")
    prepare(workspace, Path(__file__).resolve().parents[4], source_sha)
    prompt = PROMPT.format(model=args.model, regime=args.regime, run_url=args.run_url, fork_url=FORK_PUSH_URL)
    (workspace / "prompt.txt").write_text(prompt)
    handoff_path = workspace / "handoff.md"
    run_to_log(["codex", "exec", "--dangerously-bypass-approvals-and-sandbox",
                "--skip-git-repo-check", "-C", str(workspace),
                "--output-last-message", str(handoff_path), prompt], check=True)
    handoff = handoff_path.read_text()
    if not handoff.strip():
        raise ValueError("Codex did not produce a handoff report")
    print(handoff, file=sys.stderr)
    print(f"handoff report: {handoff_path}", file=sys.stderr)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, KeyError, TypeError, OSError, subprocess.CalledProcessError) as error:
        print(f"fix-llama-perplexity needs attention: {error}", file=sys.stderr)
        raise SystemExit(1)
