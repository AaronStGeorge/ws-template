"""Build Loom Serve and its model catalog against an explicit HRX checkout.

Unlike the HRX public CMake distribution, the serving solution is composed by
Bazel in hrx-solutions. Keep its output base in the checkout so independent
workspace threads never share a Bazel server or mutable build tree.
"""
from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from buildlib import BuildKnobs, BuildResult, build_dir, resolve_source_dir
from .rocm import RocmInstallResult


@dataclass(frozen=True)
class HrxSolutionsKnobs(BuildKnobs):
    hrx_source_dir: str
    bazel: str = "bazel"
    jobs: int = 8
    gpu_selection: str = "0"


@dataclass(frozen=True)
class HrxSolutionsBuildResult(BuildResult):
    knobs: HrxSolutionsKnobs
    exit_code: int
    install_path: Path
    envrc_path: Path
    log_path: Path

    @property
    def built(self) -> bool:
        return self.exit_code == 0


def build(knobs: HrxSolutionsKnobs, rocm: RocmInstallResult) -> HrxSolutionsBuildResult:
    if not rocm.installed:
        raise ValueError("hrx_solutions.build requires an installed ROCm SDK")
    src = resolve_source_dir(knobs)
    hrx = Path(knobs.hrx_source_dir).resolve()
    if not (hrx / "MODULE.bazel").is_file():
        raise ValueError(f"HRX checkout missing MODULE.bazel: {hrx}")
    bazel = shutil.which(knobs.bazel)
    if bazel is None:
        raise ValueError(f"Bazel/Bazelisk executable not found: {knobs.bazel}")
    sdk = Path(rocm.rocm_path).resolve()
    out = build_dir(src)
    out.mkdir(parents=True, exist_ok=True)
    install = out / "install"
    envrc = src / ".envrc"
    log_path = out / "build.log"
    env = dict(os.environ, ROCR_VISIBLE_DEVICES=knobs.gpu_selection,
               BAZELISK_HOME=str(out / "bazelisk"),
               USE_BAZEL_VERSION=(src / ".bazelversion").read_text().strip())
    command = [bazel, f"--output_user_root={out / 'bazel-user'}",
               f"--output_base={out / 'bazel'}"]
    # A sibling module override changes the dependency graph; let Bazel record
    # it locally. Its contents cache cannot live inside the main repository.
    options = ["--config=opt", "--repo_contents_cache=", "--lockfile_mode=update", f"--override_module=hrx={hrx}",
               f"--repo_env=IREE_ROCM_PATH={sdk}",
               f"--repo_env=CC={sdk / 'lib/llvm/bin/clang'}",
               f"--repo_env=CXX={sdk / 'lib/llvm/bin/clang++'}"]
    targets = ["//:loom-serve", "//loom-pipeline:bytecode_catalogs"]
    with log_path.open("w") as log:
        argv = [*command, "build", *options, f"--jobs={knobs.jobs}", *targets]
        print(shlex.join(argv), flush=True)
        log.write(f"$ {shlex.join(argv)}\n")
        log.flush()
        rc = subprocess.run(argv, cwd=src, env=env, stdout=log,
                            stderr=subprocess.STDOUT).returncode
        if rc == 0:
            query = [*command, "cquery", *options, "//:loom-serve", "--output=files"]
            result = subprocess.run(query, cwd=src, env=env, stdout=subprocess.PIPE,
                                    stderr=log, text=True)
            rc = result.returncode
            if rc == 0:
                binary = src / result.stdout.strip()
                (install / "bin").mkdir(parents=True, exist_ok=True)
                _install_file(binary, install / "bin/loom-serve")
                shutil.copytree(src / "bazel-bin/loom-pipeline/bytecode", install / "loom-pipeline",
                                dirs_exist_ok=True, copy_function=_install_file)
                runtime = f"{sdk / 'lib'}:{sdk / 'lib64'}"
                envrc.write_text(
                    f"export PATH={shlex.quote(str(install / 'bin'))}:\"$PATH\"\n"
                    f"export LD_LIBRARY_PATH={shlex.quote(runtime)}${{LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}}\n"
                    f"export ROCR_VISIBLE_DEVICES={shlex.quote(knobs.gpu_selection)}\n"
                )
    return HrxSolutionsBuildResult(
        project="hrx-solutions", knobs=knobs, source_path=src, build_path=out,
        exit_code=rc, install_path=install, envrc_path=envrc, log_path=log_path,
    )


def _install_file(source: str | Path, destination: str | Path) -> str:
    # Bazel outputs are read-only. Replace the installed inode so a subsequent
    # build does not need write permission on the previous output file.
    target = Path(destination)
    with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as file:
        temporary = Path(file.name)
    try:
        shutil.copy2(source, temporary)
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    return str(target)
