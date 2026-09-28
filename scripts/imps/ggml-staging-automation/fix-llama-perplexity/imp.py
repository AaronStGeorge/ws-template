#!/usr/bin/env python3
"""Investigate one perplexity witness and publish a verified llama.cpp repair.

The three positional inputs are an automation Actions run URL, its report's
model identifier, and `prefill` or `decode`. Each invocation clones GitHub
into a fresh temporary workspace, freezes the current integration commit, and invokes authenticated
codex using the bump imp's structured handoff convention. Source checkouts
and build outputs are isolated from other investigations.

The agent owns diagnosis, local experiments, isolated CI branches and the
personal-fork PR. The wrapper owns completion: it independently downloads the
reported CI evidence and checks a controlled numerical before/after pair.
Both runs cover the complete smoke tier frozen from an upstream manifest,
plus the selected model if necessary, while the repair stays scoped to one
model and regime.
Only a validated scoped repair exits zero. Already-fixed, compiler-blocked,
and incomplete investigations retain their handoff and exit nonzero without
creating duplicate repairs. There is no sensor, retry loop, or follow-on watch.
All meaningful output goes to stderr, which impd retains as the Run log.
"""

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import evidence

STAGING_URL = "git@github.com:ROCm/ggml-staging-automation.git"
INTEGRATION_URL = "https://github.com/AMD-Ecosystem/llama.cpp.git"
FORK_PUSH_URL = "git@github.com:AaronStGeorge/llama.cpp.git"


def object_schema(properties):
    return {"type": "object", "properties": properties,
            "required": list(properties), "additionalProperties": False}


TEXT = {"type": "string"}
NULLABLE_TEXT = {"type": ["string", "null"]}
HANDOFF_SCHEMA = object_schema({
    "outcome": {"type": "string", "enum": ["repaired", "already_fixed", "hrx_blocker", "blocked"]},
    "narrative": TEXT,
    "witness_differences": TEXT,
    "baseline_artifact": NULLABLE_TEXT,
    "candidate_artifact": NULLABLE_TEXT,
    "baseline_debug_artifact": NULLABLE_TEXT,
    "candidate_debug_artifact": NULLABLE_TEXT,
    "candidate_commit": NULLABLE_TEXT,
    "baseline_run": NULLABLE_TEXT,
    "candidate_run": NULLABLE_TEXT,
    "upstream_pr": NULLABLE_TEXT,
    "selected_regime": TEXT,
    "blockers": {"type": "array", "items": object_schema({"model": TEXT, "regime": TEXT, "cause": TEXT})},
    "pushes": {"type": "array", "items": object_schema({"repo": TEXT, "branch": TEXT})},
})

PROMPT = """\
Investigate the perplexity report entry for {model} from {run_url}.

Before investigating, read hrx-system/loom/docs/src/workflows/agent-driven-kernel-development.md
in this checkout. Follow its approach throughout: establish the production
failure, isolate a meaningful operation, form a falsifiable hypothesis, gather
numerical and compiler evidence as applicable, and validate the integrated result.

Locate the selected model's entry in the run's perplexity report. Read
underlying measurements, metadata, and relevant logs. Determine whether the
problem is numerical degradation, missing/invalid measurement, or execution
failure. Do not infer numerical correctness from overall workflow status.

Record source revisions, model/corpus identities, GPU/backend, and evaluation
settings. Reproduce the reported problem using those inputs. Determine whether
it still exists at the current hrx-graph-develop-v2 integration commit.

Investigate only the selected model. Develop a minimal, causally justified fix
where still needed. Validate locally, create matched baseline and candidate
perplexity-only CI runs, and open a llama.cpp PR containing the explanation
and before/after evidence.

This run is frozen to the user's requested regime, {selected_regime}, in the
selected source report row. Pursue exactly one causal repair for that regime.
The wrapper validated it before invoking you; do not change it after inspecting
upstream. If it is already fixed, report already_fixed and stop. Never roll
from fixed prefill into a decode repair, or from fixed decode into prefill.
Both CI runs must measure the complete smoke tier in both regimes as
regression observations; do not bundle a second repair or investigate another
model. If a shared change causes a regression elsewhere, revise that same
causal repair or report blocked rather than adding an unrelated fix.

The checkout above is ggml-staging-automation/ inside this isolated workspace;
start there when reading the Loom document. The explicit task inputs are the
run URL, model and requested regime above. The integration commit was resolved
by the wrapper to {integration}; keep that exact baseline for this investigation.
The source run's metadata is source-run.json. The automation starts at its
head commit, submodules initialized; no shared sources/ checkout is available.
The frozen selection is selection.json; source artifacts are already under
source-artifacts/, with the report in artifact {source_artifact}.
The wrapper froze the unfiltered default-branch model manifest at
{smoke_manifest_commit}. Its smoke models are {smoke_models}.
selection.json contains their exact model identities in expected_models.
That set, plus the selected model when outside smoke, is required in BOTH runs.

Read AGENTS.md, docs/style/always.md, and docs/style/github-actions.md before
edits. Before any HRX build/test/benchmark read docs/hrx-gpu-selection.md,
identify a supported GPU, and pin ROCR_VISIBLE_DEVICES as it instructs.
Use the provided .venv/bin/python build.py for local builds; its source/build
paths belong to this workspace. Keep captures, inputs, compiler artifacts,
logs, and local reproductions here. Do not modify shared source checkouts.

Diagnosis and repair:

- Download the source report artifact and corresponding raw debug logs with
  authenticated gh. Select exactly the supplied model identifier; ambiguity
  or an absent model is blocked, never permission to investigate a neighbor.
  Preserve original artifacts and discover the report schema/settings rather
  than assuming a historical quantization bug, PPL value, or patch.
- Inspect upstream history and open PRs before deriving a repair. If the
  issue is already fixed at {integration}, identify the fix and its evidence,
  report already_fixed and stop, even if another regime still fails. Do not
  open a duplicate PR or switch regimes. Reproduce at the
  source revisions first, then at {integration}. Inspect current automation
  and its HRX pin for the current integration experiment; retain both sets
  of revisions when they differ.
- Narrow to the earliest useful numerical divergence. Define a semantic cut
  and extract a small reproducer with real production inputs when practical.
  Use an independent numerical reference, not two backends sharing a suspect
  transformation. Before each candidate production change, record the
  hypothesis, supporting observation and cheapest falsifier in investigation.md.
- Inspect compiler reports, IR and native instructions when the hypothesis
  concerns lowering or emitted behavior. Numerical correctness is the gate;
  performance tuning is not a prerequisite for this repair.
- Add meaningful regression coverage that fails the unpatched baseline and
  passes the candidate. Retain exact commands, exit codes and raw outputs.
  Exercise affected prefill and decode routes where applicable, realistic
  nonzero inputs and relevant tails, then verify the full selected model.
  Name limitations honestly; prefill improvement does not prove decode.
- hrx-system source changes are outside scope. Do not edit, commit or push
  that checkout. If it needs a repair, retain a minimized reproducer, exact
  public command, expected/observed behavior, target/compiler provenance,
  relevant reports/IR/native evidence and a precise compiler handoff. Report
  hrx_blocker without disguising the defect as a llama.cpp workaround.

Controlled CI pair, only after local validation:

- Inspect the current workflow and benchmark scripts before configuring it.
  Create fresh isolated automation branches named {slug}-baseline and
  {slug}-candidate. Never push the original run's branch, main, or a bump PR.
  Use current supported perplexity-only dispatch inputs with model_tier=smoke.
  Run the COMPLETE smoke tier in both regimes on BOTH baseline and candidate.
  Do not filter smoke models, silently shorten the suite, or use the full tier.
  Preserve the pinned smoke identities in selection.json; a mutable or reduced
  branch manifest cannot redefine the expected model set.
- If the selected model is outside smoke, run smoke plus that model on BOTH
  branches, preserving its source identity. Apply any necessary selection or
  tier adjustment identically to both branches and explain it in the PR.
  No other models are added. Regression observation does not expand repair scope.
- Freeze every other experimental variable: automation code, HRX revision,
  model bytes/revision, corpus, GPU/backend, evaluation arguments, regimes,
  tolerance and expectations. Preserve source model/corpus identity and
  numerical criteria. Record any source-to-current HRX, GPU or regime-setting
  changes in witness_differences and verbatim in the PR, with justification.
  Document a shared change from XFAIL to PASS if needed to expose the check;
  both branches must apply it identically and preserve numerical verdicts.
  The original reproduction and current matched CI pair are separate
  experiments. Do not weaken tolerances, omit a failing regime or change
  expectations to manufacture success. A workflow may still fail because
  an expectation sees XPASS or an unrelated decode blocker; raw PPL decides.
- Both automation trees must be identical except the llama.cpp gitlink.
  Point BOTH at the personal fork in .gitmodules, with baseline pin
  {integration} and candidate pin containing only your fix on that baseline.
  Verify actual build checkouts, not merely declared gitlinks. Keep the chosen
  HRX revision identical in the pair. The candidate must descend from {integration}.
- Retain the existing perplexity report/metadata artifact and both raw backend
  debug logs on each run. Use matching target artifact names on both sides.
  metadata.json contains declared source pins; cross-check actual checkout
  revisions in build logs. The wrapper trusts those existing CI producers,
  manifest hashes and run metadata; it does not attest all loaded binaries.
- Run and wait for both workflows to complete. Verify checked-out revisions
  in their build logs and metadata. Download and inspect actual
  artifacts. A green XFAIL can contain numerical failure; exit zero without
  a valid finite positive final PPL is a missing measurement.

Publication and completion:

- Push llama.cpp only to {fork_url}, on {slug}-fix; origin's push URL already
  enforces that default. Open the upstream PR with gh pr create --repo
  AMD-Ecosystem/llama.cpp --base hrx-graph-develop-v2 --head
  AaronStGeorge:{slug}-fix. These isolated CI branch pushes, personal-fork
  push and upstream PR are authorized runtime work. Never force-push.
- Explain the failure, causal evidence, minimal repair, regression commands,
  before/after results, exact source revisions/settings and shared harness
  adjustments. Include the two completed CI links and this table, filled
  only from artifacts (use unavailable with causes for missing measurements):

  | Regime | Baseline HRX / reference PPL | Candidate HRX / reference PPL | Numerical verdict | Before / after CI links |
  | --- | --- | --- | --- | --- |

  Use evidence.py's compare_reports with selection.json's expected_models
  and blockers keyed by (model, regime). It returns selected, smoke and
  problems. Render the selected table with format_table(results['selected'],
  baseline_url, candidate_url), AND the complete smoke comparison with
  format_smoke_table(results['smoke'], baseline_url, candidate_url).
  Both exact tables belong in the PR. The smoke table includes every required
  model/regime, raw before/after PPL, numerical verdict changes and failure
  causes. Review every returned problem; none may remain for completion.
  Do not edit this helper. Link the pinned unfiltered manifest:
  https://github.com/ROCm/ggml-staging-automation/blob/{smoke_manifest_commit}/benchmarks/hrx/model_manifest.json

- The selected regime requires a failing baseline and a real, numerically
  passing candidate pair. A scoped repair may coexist with a documented
  pre-existing blocker in another model/regime. Record every remaining
  failure by model and regime in blockers, with its cause verbatim in the PR.
  Inspect both raw logs: matching failure_kind or generic error text does not
  prove the same root cause. Explain the actual before/after causes and any
  uncertainty; a "still failing" row is not proof of unchanged behavior.
  Never describe that as an entire-model or entire-smoke pass. A previously
  passing row that fails numerically, a new missing/reference measurement,
  an omitted model/regime or changed failure evidence blocks completion,
  even if the selected repair passes or the workflow is green under XFAIL.
- Leave investigation.md with the witness, causal chain, regression evidence,
  rejected hypotheses and any compiler handoff. End with the schema-forced
  handoff. selected_regime must be exactly {selected_regime}. Name the
  baseline/candidate report and debug artifact names, run URLs, full candidate
  commit, PR URL, and unresolved blockers. For non-repair outcomes use null for absent
  links and explain why. The wrapper verifies actual artifacts independently;
  agent outcome text and workflow status alone cannot make this Run succeed.
"""


def run_to_log(argv, **kwargs):
    return subprocess.run(argv, stdout=sys.stderr.fileno(), **kwargs)


def prepare(workspace, root, source_sha):
    clone = workspace / "ggml-staging-automation"
    run_to_log(["git", "clone", STAGING_URL, str(clone)], check=True)
    run_to_log(["git", "checkout", "--detach", source_sha], cwd=clone, check=True)
    run_to_log(["git", "submodule", "update", "--init", "--recursive"], cwd=clone, check=True)
    llama = clone / "llama.cpp"
    # Match the existing bump imp's fork guards and workspace instruction setup.
    if (llama / "AGENTS.md").exists():
        (llama / "AGENTS.md").unlink()
        run_to_log(["git", "update-index", "--skip-worktree", "AGENTS.md"], cwd=llama, check=True)
    run_to_log(["git", "remote", "set-url", "--push", "origin", FORK_PUSH_URL], cwd=llama, check=True)
    run_to_log(["git", "remote", "set-url", "--push", "origin", "hrx-system-is-never-pushed"],
               cwd=clone / "hrx-system", check=True)
    run_to_log(["git", "fetch", INTEGRATION_URL, "hrx-graph-develop-v2"], cwd=llama, check=True)
    integration = subprocess.check_output(["git", "rev-parse", "FETCH_HEAD"], cwd=llama, text=True).strip()
    for name in (".agents", ".claude"):
        (workspace / name).mkdir()
        (workspace / name / "skills").symlink_to(root / ".agents" / "skills", target_is_directory=True)
    for name in ("docs", ".venv"):
        (workspace / name).symlink_to(root / name, target_is_directory=True)
    for name in ("AGENTS.md", "CLAUDE.md"):
        (workspace / name).symlink_to(root / "AGENTS.md")
    here = Path(__file__).resolve().parent
    shutil.copy2(here.parent / "build.py", workspace / "build.py")
    shutil.copy2(here / "evidence.py", workspace / "evidence.py")
    return integration


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_url", help="ROCm/ggml-staging-automation Actions run URL")
    parser.add_argument("model", help="exact model identifier in the run's perplexity report")
    parser.add_argument("regime", choices=("prefill", "decode"), help="the one regime to investigate")
    args = parser.parse_args()
    selected_regime = {"prefill": "prefill-like", "decode": "decode-like"}[args.regime]
    number = evidence.run_id(args.run_url)
    require_model = re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/+-]{0,255}", args.model)
    if require_model is None:
        parser.error("model must be a nonempty report identifier, without whitespace")
    # Fail on missing authentication before allocating an expensive checkout.
    run_to_log(["codex", "login", "status"], check=True)
    run_to_log(["gh", "auth", "status"], check=True)
    source_run = evidence.api(f"repos/{evidence.REPO}/actions/runs/{number}")
    evidence.require(source_run["status"] == "completed", "source run is not completed")
    model_id = hashlib.sha256(args.model.encode()).hexdigest()[:10]
    prefix = f"fix-ppl-{number}-{model_id}-{args.regime}"
    workspace = Path(tempfile.mkdtemp(prefix=prefix + "-"))
    slug = workspace.name
    print(f"run workspace: {workspace}", file=sys.stderr)
    (workspace / "source-run.json").write_text(json.dumps(source_run, indent=2) + "\n")
    selection = evidence.select_source(args.run_url, workspace, args.model, selected_regime)
    selection.update(evidence.smoke_scope(source_run["head_sha"], args.model))
    (workspace / "selection.json").write_text(json.dumps(selection, indent=2) + "\n")
    print(f"selected regime: {selection['selected_regime']}", file=sys.stderr)
    integration = prepare(workspace, Path(__file__).resolve().parents[4], source_run["head_sha"])
    prompt = PROMPT.format(model=args.model, run_url=args.run_url, integration=integration,
                           slug=slug, fork_url=FORK_PUSH_URL, **selection)
    (workspace / "prompt.txt").write_text(prompt)
    schema_path = workspace / ".handoff-schema.json"
    schema_path.write_text(json.dumps(HANDOFF_SCHEMA, indent=2) + "\n")
    handoff_path = workspace / ".handoff.json"
    run_to_log(["codex", "exec", "--dangerously-bypass-approvals-and-sandbox",
                "--skip-git-repo-check", "-C", str(workspace),
                "--output-schema", str(schema_path), "--output-last-message", str(handoff_path),
                prompt], check=True)
    handoff = json.loads(handoff_path.read_text())
    print(json.dumps(handoff, indent=2), file=sys.stderr)
    if handoff["outcome"] != "repaired":
        evidence.require(handoff["selected_regime"] == selection["selected_regime"],
                         "handoff changed the frozen regime")
        raise ValueError(f"{handoff['outcome']}: no validated repair; see retained handoff")
    llama = workspace / "ggml-staging-automation" / "llama.cpp"
    run_to_log(["git", "merge-base", "--is-ancestor", integration, handoff["candidate_commit"]],
               cwd=llama, check=True)
    results = evidence.verify(handoff, workspace, args.run_url, args.model, integration, source_run, selection)
    print(json.dumps(results, indent=2), file=sys.stderr)
    print(f"validated scoped repair: {handoff['upstream_pr']}", file=sys.stderr)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, KeyError, IndexError, TypeError, OSError, subprocess.CalledProcessError) as error:
        print(f"fix-llama-perplexity needs attention: {error}", file=sys.stderr)
        raise SystemExit(1)
