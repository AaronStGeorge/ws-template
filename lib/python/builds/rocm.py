from __future__ import annotations

import json
import os
import shutil
import subprocess
import urllib.request
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from buildlib import BuildKnobs, BuildResult, build_dir, load_pin_entry, resolve_source_dir

PROJECT = "rocm"

# Per-socket-operation timeout for the SDK download. Bounds a stalled connection
# without killing a slow-but-progressing transfer (it applies per connect/read,
# not to the whole multi-GB download).
_DOWNLOAD_TIMEOUT_S = 300


class RocmProvider(StrEnum):
    """How a ROCm / TheRock SDK was produced.

    Recorded on every :class:`RocmInstallResult` so a consumer (and the assembly
    line) can tell which strategy created the SDK and reproduce it from the knobs.
    Each value pairs with a :class:`RocmInstallKnobs` subclass that carries the
    inputs that strategy needs.
    """

    # Download one pinned, whole-SDK tarball into a content-addressed cache and
    # symlink it into the consuming repo. Implemented (:class:`PinnedTarballKnobs`).
    PINNED_TARBALL = "pinned_tarball"
    # Build TheRock from a source checkout. Not yet implemented.
    THEROCK_SOURCE = "therock_source"
    # Download individual TheRock artifact closures piecemeal and flatten them
    # into a root (mirrors hrx-system CI's ci_core_linux.py). Not yet implemented.
    PIECEMEAL_ARTIFACTS = "piecemeal_artifacts"


@dataclass(frozen=True)
class RocmInstallKnobs(BuildKnobs):
    """Base knobs shared by every ROCm-install provider.

    ``source_dir`` (required by :class:`BuildKnobs`) is the repo the resulting SDK
    is linked into -- the "into the repo" half of "download ... and symlink it into
    the repo". The concrete strategy is identified by :attr:`provider`, which each
    subclass reports; ``build`` dispatches on the knobs type.
    """

    @property
    def provider(self) -> RocmProvider:  # pragma: no cover - overridden by subclasses
        raise NotImplementedError("RocmInstallKnobs subclasses must declare a provider")


@dataclass(frozen=True)
class PinnedTarballKnobs(RocmInstallKnobs):
    """Knobs for the pinned, whole-SDK tarball provider.

    The pin (``version`` + gfx-templated ``url_template``) is *not* carried here --
    it is read from ``pins.json`` at the repo root via :attr:`pin`, so a pin bump
    is a one-line change in one version-controlled file. ``gfx_target`` is a
    required, explicit *input* (it
    selects the per-architecture nightly tarball and is supplied on the command
    line by the calling script), never read from ``pins.json`` and never
    defaulted; it is templated into the pin's ``url_template`` together with the
    resolved ``version``.
    """

    gfx_target: str  # AMDGPU arch input (required); selects the per-arch tarball
    pin: str = "rocm"  # key to look up in the repo-root pins.json
    cache_dir: str = ""  # download/extract cache root; "" -> ~/.cache/hrx/rocm
    link_name: str = "rocm-root"  # symlink created under <source_dir>/build

    @property
    def provider(self) -> RocmProvider:
        return RocmProvider.PINNED_TARBALL


@dataclass(frozen=True)
class RocmInstallResult(BuildResult):
    """Result of installing a ROCm / TheRock SDK via one of the providers.

    Produced by every provider so the SDK feeds ``hrx_system.build(knobs, rocm)``
    uniformly. ``rocm_path`` is the SDK root a consumer should point at (``None``
    when the install failed); ``provider`` records which strategy produced it and
    ``cache_path`` is where the SDK was materialized on disk.
    """

    knobs: RocmInstallKnobs  # narrow the base's knobs field to this project's type
    provider: RocmProvider
    rocm_path: Path | None
    cache_path: Path | None
    exit_code: int
    log: str

    @property
    def installed(self) -> bool:
        """True when the SDK was materialized and linked without error."""
        return self.exit_code == 0 and self.rocm_path is not None

    def as_prompt_input(self, tail_chars: int = 4000) -> dict[str, object]:
        return {
            "project": self.project,
            "provider": str(self.provider),
            "knobs": self.knobs.as_dict(),
            "installed": self.installed,
            "exit_code": self.exit_code,
            "source_path": str(self.source_path),
            "build_path": str(self.build_path),
            "rocm_path": str(self.rocm_path) if self.rocm_path else None,
            "cache_path": str(self.cache_path) if self.cache_path else None,
            "log_tail": self.log[-tail_chars:],
        }


class RocmInstallError(RuntimeError):
    """A ROCm install step failed; carries the exit code to record on the result."""

    def __init__(self, message: str, exit_code: int = 1) -> None:
        super().__init__(message)
        self.exit_code = exit_code


def build(knobs: RocmInstallKnobs) -> RocmInstallResult:
    """Install a ROCm / TheRock SDK, dispatching on the provider knobs type.

    Returns a :class:`RocmInstallResult` that is passed straight to
    ``hrx_system.build(knobs, rocm)``. Only the pinned-tarball provider is
    implemented today; the others are declared on :class:`RocmProvider` as the
    extension points.
    """
    if isinstance(knobs, PinnedTarballKnobs):
        return _build_pinned_tarball(knobs)
    raise NotImplementedError(
        f"ROCm provider {knobs.provider!r} is not implemented yet; "
        f"only {RocmProvider.PINNED_TARBALL!r} is available."
    )


def _build_pinned_tarball(knobs: PinnedTarballKnobs) -> RocmInstallResult:
    """Download a pinned whole-SDK tarball, extract it, and link it in.

    The pin (``version`` + ``url_template``) is read from ``pins.json`` via
    ``knobs.pin``; the gfx target comes from ``knobs.gfx_target`` (an input) and is
    templated into the URL. The ``tarball-multi-arch`` nightly channel publishes no
    checksums, so the download is not hash-verified. The tarball is cached at
    ``<cache_dir>/<version>-<gfx>`` and only re-fetched when the pin (recorded in a
    marker file) does not match, so repeated builds are cheap. The SDK root is then
    symlinked to ``<source_dir>/build/<link_name>`` and returned as ``rocm_path``.
    """
    src = resolve_source_dir(knobs)
    out = build_dir(src)
    out.mkdir(parents=True, exist_ok=True)

    log_parts: list[str] = []
    rocm_path: Path | None = None
    cache_root = _cache_root(knobs)
    install_dir: Path | None = None
    exit_code = 0
    try:
        url, version = _resolve_pin(knobs)
        log_parts.append(
            f"== Pin {knobs.pin}={version} gfx={knobs.gfx_target} from pins.json: {url}"
        )
        install_dir = cache_root / f"{version}-{knobs.gfx_target}"
        marker = install_dir / ".hrx-rocm-pin.json"
        link_path = out / knobs.link_name
        root = _cached_root(marker, url, install_dir)
        if root is not None:
            log_parts.append(f"== Cached ROCm pin {version}/{knobs.gfx_target} at {install_dir}")
        else:
            cache_root.mkdir(parents=True, exist_ok=True)
            archive = cache_root / f"{version}-{knobs.gfx_target}.tar.gz"
            _download(url, archive, log_parts)
            _reset_dir(install_dir)
            _extract(archive, install_dir, log_parts)
            # Validate the extracted tree resolves before persisting the marker, so a
            # bad extract never leaves a "satisfied" cache that the fast path trusts.
            root = _locate_rocm_root(install_dir)
            _write_marker(marker, knobs, url=url, version=version)
        _symlink(link_path, root, log_parts)
        rocm_path = link_path
    except RocmInstallError as exc:
        exit_code = exc.exit_code
        log_parts.append(f"!! {exc}")
    except Exception as exc:  # surface any unexpected failure on the result, not as a crash
        exit_code = 1
        log_parts.append(f"!! {type(exc).__name__}: {exc}")

    return RocmInstallResult(
        project=PROJECT,
        knobs=knobs,
        source_path=src,
        build_path=out,
        provider=RocmProvider.PINNED_TARBALL,
        rocm_path=rocm_path,
        cache_path=install_dir,
        exit_code=exit_code,
        log="\n".join(log_parts),
    )


def _resolve_pin(knobs: PinnedTarballKnobs) -> tuple[str, str]:
    """Resolve (url, version) for ``knobs`` from ``pins.json``.

    ``version`` and ``url_template`` come from the pin; ``gfx_target`` (an input,
    not from the pin) is templated into the URL. The pin is always read from the
    repo-root ``pins.json`` (discovered by ``load_pin_entry``).
    """
    entry = load_pin_entry(knobs.pin)
    version = entry.get("version")
    template = entry.get("url_template")
    if not version or not template:
        raise RocmInstallError(f"pin {knobs.pin!r} must define 'version' and 'url_template'")
    try:
        url = template.format(gfx=knobs.gfx_target, version=version)
    except (KeyError, IndexError, ValueError) as exc:
        raise RocmInstallError(
            f"pin {knobs.pin!r} url_template is malformed: {exc}"
        ) from exc
    return url, version


def _cache_root(knobs: PinnedTarballKnobs) -> Path:
    if knobs.cache_dir:
        return Path(knobs.cache_dir).expanduser().resolve()
    return Path(os.environ.get("HRX_ROCM_CACHE_DIR", "~/.cache/hrx/rocm")).expanduser().resolve()


def _download(url: str, dest: Path, log_parts: list[str]) -> None:
    if dest.exists():
        dest.unlink()
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    log_parts.append(f"++ Downloading {url} -> {dest}")
    try:
        with urllib.request.urlopen(url, timeout=_DOWNLOAD_TIMEOUT_S) as response, tmp.open("wb") as out:
            shutil.copyfileobj(response, out)
    except Exception as exc:
        if tmp.exists():
            tmp.unlink()
        raise RocmInstallError(f"download failed for {url}: {exc}") from exc
    tmp.replace(dest)


def _extract(archive: Path, dest: Path, log_parts: list[str]) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    rc = _run(["tar", "-xf", str(archive), "-C", str(dest)], log_parts)
    if rc != 0:
        raise RocmInstallError(f"tar extraction failed for {archive.name}", exit_code=rc)


def _cached_root(marker: Path, url: str, install_dir: Path) -> Path | None:
    """Return the cached SDK root if the pin matches *and* the tree is intact.

    A cache hit requires the marker to record the same ``url`` and the extracted
    SDK root to still resolve; otherwise the cache is treated as stale/corrupt and
    the caller re-downloads, so a partially-evicted cache self-heals instead of
    dead-ending.
    """
    if not _pin_satisfied(marker, url):
        return None
    return _try_locate_rocm_root(install_dir)


def _try_locate_rocm_root(install_dir: Path) -> Path | None:
    try:
        return _locate_rocm_root(install_dir)
    except (RocmInstallError, OSError):
        return None


def _locate_rocm_root(install_dir: Path) -> Path:
    """Find the SDK root inside an extracted tree.

    Archives often wrap everything in a single top-level directory; descend
    through lone wrapper dirs until a directory that looks like a ROCm root
    (has ``bin/`` and ``lib/``) is found.
    """
    current = install_dir
    for _ in range(8):
        if _is_rocm_root(current):
            return current
        children = [c for c in current.iterdir() if not c.name.startswith(".")]
        if len(children) == 1 and children[0].is_dir():
            current = children[0]
            continue
        break
    raise RocmInstallError(
        f"could not locate a ROCm SDK root (bin/ + lib/) under {install_dir}"
    )


def _is_rocm_root(path: Path) -> bool:
    return (path / "bin").is_dir() and (path / "lib").is_dir()


def _symlink(link_path: Path, target: Path, log_parts: list[str]) -> None:
    link_path.parent.mkdir(parents=True, exist_ok=True)
    if link_path.is_symlink() or link_path.is_file():
        link_path.unlink()
    elif link_path.exists():
        shutil.rmtree(link_path)
    link_path.symlink_to(target, target_is_directory=True)
    log_parts.append(f"== Linked {link_path} -> {target}")


def _reset_dir(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.exists():
        shutil.rmtree(path)


def _pin_satisfied(marker: Path, url: str) -> bool:
    if not marker.exists():
        return False
    try:
        recorded = json.loads(marker.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return False
    return recorded.get("url") == url


def _write_marker(
    marker: Path, knobs: PinnedTarballKnobs, *, url: str, version: str
) -> None:
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(
        json.dumps(
            {"provider": str(knobs.provider), "pin": knobs.pin,
             "gfx_target": knobs.gfx_target, "version": version, "url": url},
            indent=2, sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def _run(argv: list[str], log_parts: list[str]) -> int:
    completed = subprocess.run(argv, capture_output=True, text=True)
    log_parts.append(
        f"$ {' '.join(argv)}\n[exit {completed.returncode}]\n"
        f"{completed.stdout}{completed.stderr}"
    )
    return completed.returncode
