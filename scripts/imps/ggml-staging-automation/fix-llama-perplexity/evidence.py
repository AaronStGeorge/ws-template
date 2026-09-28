"""Decide completion from downloaded experiments, independently of the agent.

The report owns raw measurements; workflow conclusions and XFAIL policy do
not own numerical truth. This boundary reads schema-v2 reports, cross-checks
successful estimates against backend logs, and computes the existing ratio
criterion. Missing estimates remain missing even when a process exits zero.

A repair is scoped to the user's requested regime. Regression observation
covers the complete smoke tier, frozen from an unfiltered upstream manifest
before the agent runs, plus the selected model when it is outside that tier.
Existing reported failures may remain with explanations; missing rows, new failures or
absent required evidence fail the Run. Live GitHub run, tree and PR data bind the matched experiments
to the frozen integration revision and the published candidate. Provenance
trusts the existing CI producers and manifest hashes; it does not attest every
loaded binary or shared library.
"""

import json
import math
import re
import shlex
import subprocess
from pathlib import Path

REPO = "ROCm/ggml-staging-automation"
RUN_URL = re.compile(r"https://github\.com/ROCm/ggml-staging-automation/actions/runs/([1-9][0-9]*)")
PR_URL = re.compile(r"https://github\.com/AMD-Ecosystem/llama\.cpp/pull/([1-9][0-9]*)")
ESTIMATE = re.compile(r"Final estimate: PPL = (\S+) \+/- (\S+)")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def gh(*args):
    return subprocess.check_output(["gh", *args], text=True)


def api(path):
    return json.loads(gh("api", path))


def run_id(url):
    match = RUN_URL.fullmatch(url)
    require(match is not None, f"not an automation run URL: {url!r}")
    return match.group(1)


def fetch_run(url, destination, artifact, debug_artifact=None):
    number = run_id(url)
    run = api(f"repos/{REPO}/actions/runs/{number}")
    require(run["status"] == "completed", f"run is not completed: {url}")
    destination.mkdir(parents=True)
    (destination / "run.json").write_text(json.dumps(run, indent=2) + "\n")
    gh("run", "download", number, "--repo", REPO, "--name", artifact,
       "--dir", str(destination / "artifacts"))
    if debug_artifact is not None:
        gh("run", "download", number, "--repo", REPO, "--name", debug_artifact,
           "--dir", str(destination / "artifacts" / "debug"))
    (destination / "workflow.log").write_text(
        gh("run", "view", number, "--repo", REPO, "--log")
    )
    return run, destination / "artifacts"


def read_json(directory, name):
    paths = list(directory.rglob(name))
    require(len(paths) == 1, f"expected one {name} in {directory}, found {len(paths)}")
    return json.loads(paths[0].read_text())


def positive(value, label):
    numeric = type(value) in (int, float)
    require(numeric, f"{label} is not numeric")
    finite = math.isfinite(value)
    above_zero = value > 0
    valid = finite and above_zero
    require(valid, f"{label} must be finite and positive")
    return value


def report(directory, model, *, expected_models=None):
    document = read_json(directory, "perplexity.json")
    require(document["schema_version"] == 2, "unsupported perplexity schema (need v2)")
    rows = {row["model"]: row for row in document["models"]}
    require(len(rows) == len(document["models"]), "duplicate model rows in report")
    require(model in rows, f"selected model missing: {model}")
    if expected_models is not None:
        missing = sorted(set(expected_models) - set(rows))
        extra = sorted(set(rows) - set(expected_models))
        require(set(rows) == set(expected_models),
                f"incomplete smoke scope: missing={missing}, unexpected={extra}")
        for name in ("benchmark-hrx.json", "benchmark-vulkan.json"):
            require(not list(directory.rglob(name)), "CI pair also ran Lemonade benchmarks")
    positive(document["settings"]["max_perplexity_ratio"], "ratio criterion")
    require(set(document["regimes"]) == {"prefill-like", "decode-like"}, "report must include both regimes")
    # Metadata is useful only if it describes the command that was run.
    for name, row in rows.items():
        require(set(row["regimes"]) == set(document["regimes"]), f"missing regime measurement: {name}")
        if expected_models is not None:
            require(row["file"] == expected_models[name]["filename"], f"changed model file: {name}")
        for regime_id, pair in row["regimes"].items():
            config = document["regimes"][regime_id]
            for backend in ("hrx", "vulkan"):
                command = pair[backend]["command"]
                offset = 1 + len(document["settings"]["extra_args"])
                model_path, corpus_path = command[offset + 1], command[offset + 3]
                expected = [document["llama_perplexity"], *document["settings"]["extra_args"],
                            "-m", model_path, "-f", corpus_path, "-c", str(config["ctx"]),
                            "--chunks", str(config["chunks"]), "-b", str(config["batch"]),
                            "-ub", str(config["microbatch"]), "--device", document["devices"][backend]]
                require(command == expected, f"measurement command differs from report settings: {name}")
                require(Path(model_path).name == row["file"], f"command uses another model: {name}")
                require(Path(corpus_path).name == document["corpus"]["name"], "command uses another corpus")
    return document, rows[model]


def validate_source_regime(document, row, regime):
    """Require a witness for the requested regime without selecting another."""
    require(regime in row["regimes"], f"requested regime absent from source report: {regime}")
    pair = row["regimes"][regime]
    maximum = document["settings"]["max_perplexity_ratio"]
    require(pair["vulkan"]["status"] == "ok",
            f"source {regime} has no valid reference; cannot investigate an HRX repair or switch regimes")
    positive(pair["vulkan"]["ppl"]["value"], f"source {regime} reference PPL")
    if pair["hrx"]["status"] != "ok":
        return
    positive(pair["hrx"]["ppl"]["value"], f"source {regime} HRX PPL")
    exceeds_limit = pair["hrx"]["ppl"]["value"] > maximum * pair["vulkan"]["ppl"]["value"]
    require(exceeds_limit, f"requested source regime has no HRX failure: {regime}")


def select_source(run_url, workspace, model, selected_regime):
    """Download existing report/debug artifacts before giving the agent scope."""
    destination = workspace / "source-artifacts"
    destination.mkdir()
    number = run_id(run_url)
    names = gh("api", f"repos/{REPO}/actions/runs/{number}/artifacts",
               "--paginate", "--jq", ".artifacts[].name").splitlines()
    for name in names:
        if name.startswith("lemonade-bench-"):
            gh("run", "download", number, "--repo", REPO,
               "--name", name, "--dir", str(destination / name))
    matches = []
    for path in sorted(destination.rglob("perplexity.json")):
        document = json.loads(path.read_text())
        has_model = any(row["model"] == model for row in document["models"])
        if has_model:
            matches.append(path)
    require(len(matches) == 1, "selected model missing or ambiguous across source artifacts")
    path = matches[0]
    document, row = report(path.parent, model)
    validate_source_regime(document, row, selected_regime)
    return {"source_artifact": path.relative_to(destination).parts[0],
            "selected_regime": selected_regime}


def measurement(measured, directory, model, regime, backend):
    paths = list(directory.rglob(Path(measured["log"]).name))
    require(len(paths) == 1, f"missing or ambiguous {backend} raw log")
    text = paths[0].read_text(errors="replace")
    marker = f"{model} [{regime}] on "
    sections = [part for part in re.split(r"^===== ", text, flags=re.MULTILINE)
                if part.startswith(marker)]
    require(len(sections) == 1, f"missing or duplicate raw section for {model}/{regime}")
    require("++ " + shlex.join(measured["command"]) in sections[0], "raw command differs from report")
    if measured["status"] != "ok":
        require(measured["status"] == "failed", "unknown measurement status")
        require(measured["ppl"] is None, "failed measurement claims PPL")
        require(bool(measured["error"]), "missing measurement failure explanation")
        require(measured["failure_kind"] in ("execution", "invalid-measurement"),
                "unknown measurement failure kind")
        return None
    require(measured["exit_code"] == 0, "successful measurement has nonzero exit")
    value = positive(measured["ppl"]["value"], "PPL")
    uncertainty = measured["ppl"]["uncertainty"]
    finite_uncertainty = math.isfinite(uncertainty)
    nonnegative_uncertainty = uncertainty >= 0
    valid_uncertainty = finite_uncertainty and nonnegative_uncertainty
    require(valid_uncertainty, "invalid PPL uncertainty")
    # A backend log contains both regimes. Only this model/regime's section
    # can corroborate the selected measurement; a different final PPL cannot.
    estimates = ESTIMATE.findall(sections[0])
    require(len(estimates) == 1, "raw log lacks one final estimate")
    actual = tuple(float(item) for item in estimates[0])
    require(actual == (value, uncertainty), "report PPL differs from raw log")
    return value


def verdict(pair, maximum, directory, model, regime):
    values = {backend: measurement(pair[backend], directory, model, regime, backend)
              for backend in ("hrx", "vulkan")}
    reference_missing = values["vulkan"] is None
    hrx_missing = values["hrx"] is None
    if reference_missing:
        return "reference-failure", values
    if hrx_missing:
        return pair["hrx"]["failure_kind"], values
    fails = values["hrx"] > maximum * values["vulkan"]
    return "numerical-failure" if fails else "pass", values


def compare_reports(source, baseline, candidate, model, selected_regime, expected_models,
                    blockers, witness_differences=""):
    """Observe every smoke row before deciding whether the scoped repair passes.

    Numerical regressions are returned with the complete comparison so the
    same renderer can expose failed experiments in a PR. Structural omissions
    still raise immediately: a missing row is not a measured result.
    """
    before_document, before_row = report(baseline, model, expected_models=expected_models)
    after_document, after_row = report(candidate, model, expected_models=expected_models)
    source_document, source_row = report(source, model)
    validate_source_regime(source_document, source_row, selected_regime)
    for key in ("settings", "regimes", "corpus", "devices"):
        require(before_document[key] == after_document[key], f"unmatched {key}")
    for key in ("settings", "corpus"):
        require(source_document[key] == before_document[key], f"changed source {key}")
    for key in ("regimes", "devices"):
        if source_document[key] != before_document[key]:
            require(bool(witness_differences.strip()), f"undocumented source-to-current {key} change")
    require(source_row["file"] == before_row["file"] == after_row["file"], "changed model file")
    regimes = set(before_document["regimes"])
    selected = (model, selected_regime)
    observations = {(name, regime) for name in expected_models for regime in regimes}
    require(set(blockers) <= observations, "unknown blocker model/regime")
    require(selected not in blockers, "selected repair also marked blocked")
    maximum = before_document["settings"]["max_perplexity_ratio"]
    before_rows = {row["model"]: row for row in before_document["models"]}
    after_rows = {row["model"]: row for row in after_document["models"]}
    results, problems = [], []
    for name, regime in sorted(observations):
        before_pair = before_rows[name]["regimes"][regime]
        after_pair = after_rows[name]["regimes"][regime]
        old, before = verdict(before_pair, maximum, baseline, name, regime)
        new, after = verdict(after_pair, maximum, candidate, name, regime)
        issues = []
        key = (name, regime)
        if key == selected:
            repairable = old in ("numerical-failure", "execution", "invalid-measurement")
            if not repairable:
                issues.append("baseline has no repairable HRX failure")
            if new != "pass":
                issues.append("candidate lacks numerical pass for selected repair")
        if new != "pass":
            if old != new:
                issues.append(f"new or changed failure: {old} -> {new}")
            # Compare every backend's reported failure signature. Generic
            # messages do not establish the underlying cause; the agent must
            # explain the before/after raw logs in the blocker narrative.
            for backend in ("hrx", "vulkan"):
                for field in ("status", "failure_kind", "error"):
                    if before_pair[backend][field] != after_pair[backend][field]:
                        issues.append(f"changed {backend} failure evidence: {field}")
            if not blockers.get(key, "").strip():
                issues.append("unexplained remaining failure")
        else:
            require(key not in blockers, f"passing observation marked blocked: {name}/{regime}")
        change = "unchanged pass" if new == "pass" else "still failing"
        if old != new:
            change = "improved" if new == "pass" else "REGRESSION / changed failure"
        if issues:
            problems.extend(f"{name}/{regime}: {issue}" for issue in issues)
        causes = []
        for side, pair in (("before", before_pair), ("after", after_pair)):
            for backend in ("hrx", "vulkan"):
                measured = pair[backend]
                if measured["status"] != "ok":
                    causes.append(f"{side} {backend}: {measured['failure_kind']}: {measured['error']}")
        numerical_failure_present = "numerical-failure" in (old, new)
        if numerical_failure_present:
            causes.append(f"HRX/reference PPL limit: {maximum:g}")
        if key in blockers:
            causes.append(blockers[key])
        results.append({"model": name, "regime": regime, "baseline": before, "candidate": after,
                        "before_verdict": old, "after_verdict": new, "change": change,
                        "causes": causes, "problems": issues})
    return {"selected": [result for result in results if result["model"] == model],
            "smoke": results, "problems": problems}


def tree(sha):
    data = api(f"repos/{REPO}/git/trees/{sha}")
    require(not data.get("truncated", False), "truncated automation tree")
    return {entry["path"]: (entry["mode"], entry["type"], entry["sha"])
            for entry in data["tree"]}


def model_manifest(sha):
    manifest = json.loads(gh("api", f"repos/{REPO}/contents/benchmarks/hrx/model_manifest.json?ref={sha}",
                             "-H", "Accept: application/vnd.github.raw+json"))
    rows = {row["name"]: row for row in manifest["models"]}
    require(len(rows) == len(manifest["models"]), "duplicate models in pinned manifest")
    for name, row in rows.items():
        for key in ("repository", "revision", "filename", "sha256"):
            require(bool(row[key]), f"manifest lacks model {key}: {name}")
        require(re.fullmatch(r"[0-9a-f]{64}", row["sha256"]) is not None,
                f"manifest lacks valid model content hash: {name}")
    return rows


def model_spec(sha, model):
    rows = model_manifest(sha)
    require(model in rows, "model missing in pinned manifest")
    return rows[model]


def smoke_scope(source_sha, model):
    """Own the expected set before agent-controlled CI branches can filter it."""
    default_branch = api(f"repos/{REPO}")["default_branch"]
    commit = api(f"repos/{REPO}/commits/{default_branch}")["sha"]
    manifest = model_manifest(commit)
    expected = {name: spec for name, spec in manifest.items() if spec["tier"] == "smoke"}
    require(bool(expected), "canonical manifest has no smoke models")
    smoke_models = sorted(expected)
    original_spec = model_spec(source_sha, model)
    if model in expected:
        for key in ("repository", "revision", "filename", "sha256"):
            require(original_spec[key] == expected[model][key],
                    f"source model conflicts with pinned smoke identity: {key}")
    else:
        expected[model] = original_spec
    return {"smoke_manifest_commit": commit, "smoke_models": smoke_models,
            "expected_models": expected}


def verify_provenance(directory, expected_llama, expected_hrx):
    metadata = read_json(directory, "metadata.json")
    require(metadata["llama_cpp"] == expected_llama, "llama.cpp revision mismatch")
    require(metadata["hrx_system"] == expected_hrx, "HRX revision mismatch")
    return metadata


def format_table(results, baseline_url, candidate_url):
    lines = ["| Regime | Baseline HRX / reference PPL | Candidate HRX / reference PPL | Numerical verdict | Before / after CI links |",
             "| --- | --- | --- | --- | --- |"]
    for result in results:
        cells = [result["regime"]]
        for side in ("baseline", "candidate"):
            values = result[side]
            cells.append(" / ".join("unavailable" if values[key] is None else f"{values[key]:.4f}"
                                    for key in ("hrx", "vulkan")))
        cells.append(f"{result['before_verdict']} -> {result['after_verdict']}")
        cells.append(f"[before]({baseline_url}) / [after]({candidate_url})")
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def format_smoke_table(results, baseline_url, candidate_url):
    """The full comparison stays reviewable even when the repair regresses CI."""
    lines = ["| Model | Regime | Baseline HRX / reference PPL | Candidate HRX / reference PPL | Numerical verdict | Change | Failure causes |",
             "| --- | --- | --- | --- | --- | --- | --- |"]
    for result in results:
        cells = [result["model"], result["regime"]]
        for side in ("baseline", "candidate"):
            values = result[side]
            cells.append(" / ".join("unavailable" if values[key] is None else f"{values[key]:.4f}"
                                    for key in ("hrx", "vulkan")))
        cells.extend([f"{result['before_verdict']} -> {result['after_verdict']}", result["change"],
                      "; ".join(result["causes"] + result["problems"]) or "none"])
        escaped = [cell.replace("|", "\\|").replace("\n", "<br>") for cell in cells]
        lines.append("| " + " | ".join(escaped) + " |")
    lines.extend(["", f"Smoke comparison: [before]({baseline_url}) / [after]({candidate_url})."])
    return "\n".join(lines)


def verify(handoff, workspace, source_url, model, integration, source_run, selection):
    require(handoff["outcome"] == "repaired", "no validated repair; see handoff narrative")
    require(handoff["selected_regime"] == selection["selected_regime"],
            "handoff changed the frozen regime")
    baseline_url, candidate_url = handoff["baseline_run"], handoff["candidate_run"]
    require(baseline_url != candidate_url, "baseline and candidate are the same run")
    retained = workspace / "verified-evidence"
    _, source = fetch_run(source_url, retained / "source", selection["source_artifact"])
    require(handoff["baseline_artifact"] == handoff["candidate_artifact"], "unmatched report artifact targets")
    require(handoff["baseline_debug_artifact"] == handoff["candidate_debug_artifact"], "unmatched debug artifact targets")
    baseline_run, baseline = fetch_run(baseline_url, retained / "baseline",
                                       handoff["baseline_artifact"], handoff["baseline_debug_artifact"])
    candidate_run, candidate = fetch_run(candidate_url, retained / "candidate",
                                         handoff["candidate_artifact"], handoff["candidate_debug_artifact"])
    before_tree, after_tree = tree(baseline_run["head_sha"]), tree(candidate_run["head_sha"])
    candidate_sha = handoff["candidate_commit"]
    require(before_tree.pop("llama.cpp")[2] == integration, "baseline is not frozen integration")
    require(after_tree.pop("llama.cpp")[2] == candidate_sha, "candidate pin differs from handoff")
    require(before_tree == after_tree, "automation pair differs beyond llama.cpp gitlink")
    source_tree = tree(source_run["head_sha"])
    hrx = before_tree["hrx-system"][2]
    differences = handoff["witness_differences"]
    if source_tree["hrx-system"][2] != hrx:
        require(bool(differences.strip()), "undocumented source-to-current HRX change")
    original_spec = model_spec(source_run["head_sha"], model)
    pair_manifest = model_manifest(baseline_run["head_sha"])
    expected_models = selection["expected_models"]
    for name, spec in expected_models.items():
        require(name in pair_manifest, f"CI manifest omitted smoke/selected model: {name}")
        for key in ("repository", "revision", "filename", "sha256"):
            require(spec[key] == pair_manifest[name][key], f"changed smoke model identity: {name}/{key}")
    pair_spec = pair_manifest[model]
    for key in ("repository", "revision", "filename", "sha256"):
        require(original_spec.get(key) == pair_spec.get(key), f"changed model identity/expectation: {key}")
    if original_spec.get("hrx") != pair_spec.get("hrx"):
        require(bool(differences.strip()), "undocumented source-to-current expectation change")
    before_metadata = verify_provenance(baseline, integration, hrx)
    after_metadata = verify_provenance(candidate, candidate_sha, hrx)
    before_metadata.pop("llama_cpp")
    after_metadata.pop("llama_cpp")
    require(before_metadata == after_metadata, "unmatched benchmark metadata")
    blockers = {(item["model"], item["regime"]): item["cause"] for item in handoff["blockers"]}
    require(len(blockers) == len(handoff["blockers"]), "duplicate blocker model/regime")
    results = compare_reports(source, baseline, candidate, model, selection["selected_regime"],
                              expected_models, blockers, differences)
    selected_table = format_table(results["selected"], baseline_url, candidate_url)
    smoke_table = format_smoke_table(results["smoke"], baseline_url, candidate_url)
    (retained / "verdict.json").write_text(json.dumps(results, indent=2) + "\n")
    (retained / "comparison.md").write_text(selected_table + "\n\n" + smoke_table + "\n")
    require(not results["problems"], "smoke comparison failed: " + "; ".join(results["problems"]))
    pr_url = handoff["upstream_pr"]
    match = PR_URL.fullmatch(pr_url)
    require(match is not None, "PR is not against AMD-Ecosystem/llama.cpp")
    pr = api(f"repos/AMD-Ecosystem/llama.cpp/pulls/{match.group(1)}")
    require(pr["base"]["ref"] == "hrx-graph-develop-v2", "wrong PR integration branch")
    require(pr["head"]["repo"]["full_name"] == "AaronStGeorge/llama.cpp", "PR does not use personal fork")
    require(pr["head"]["sha"] == candidate_sha, "PR head differs from validated candidate")
    for link in (baseline_url, candidate_url):
        require(link in pr["body"], f"PR omits completed CI run: {link}")
    require(selected_table in pr["body"],
            "PR evidence table differs from actual artifacts")
    require(smoke_table in pr["body"], "PR full smoke comparison differs from actual artifacts")
    scope_link = f"https://github.com/{REPO}/blob/{selection['smoke_manifest_commit']}/benchmarks/hrx/model_manifest.json"
    require(scope_link in pr["body"], "PR omits pinned unfiltered smoke manifest")
    if differences:
        require(differences in pr["body"], "PR omits source-to-current experimental differences")
    return results
