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

    The pin -- just the ``version`` -- is *not* carried here: it is read from
    ``pins.json`` at the repo root via :attr:`pin`, so a pin bump is a one-line
    change in one version-controlled file. The URL form and the gfx->token table
    are constants in code (:func:`_tarball_url`, :data:`_GFX_URL_TARGETS`), not pin
    data. ``gfx_target`` is a required, explicit *input*: a *plain* arch number
    such as ``1201`` (never a ``gfx``-prefixed string), supplied on the command
    line by the calling script and never read from ``pins.json`` nor defaulted. It
    selects the per-architecture nightly tarball by being looked up in
    :data:`_GFX_URL_TARGETS` (see :func:`_url_target`) and assembled with the
    resolved ``version`` into the download URL.
    """

    gfx_target: str  # plain AMDGPU arch number input (required), e.g. "1201"
    pin: str = "rocm"  # key to look up in the repo-root pins.json
    cache_dir: str = ""  # download/extract cache root; "" -> ~/.cache/hrx/rocm
    link_name: str = ".rocm"  # symlink created in <source_dir> (sibling of .envrc)

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

    The pin (just ``version``) is read from ``pins.json`` via ``knobs.pin``; the
    plain gfx number in ``knobs.gfx_target`` (an input) is mapped through the
    in-code :data:`_GFX_URL_TARGETS` table to a tarball token and assembled into
    the download URL. The ``tarball-multi-arch`` nightly channel publishes no
    checksums, so the download is not hash-verified. The tarball is cached at
    ``<cache_dir>/<version>-<gfx>`` and only re-fetched when the pin (recorded in a
    marker file) does not match, so repeated builds are cheap. The SDK root is then
    symlinked to ``<source_dir>/<link_name>`` -- a sibling of ``.envrc``, default
    ``.rocm`` -- and returned as ``rocm_path``. It deliberately does not live under
    ``build/`` so that ``rm -rf build`` cannot delete the toolchain link.
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
        link_path = src / knobs.link_name
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


# How a per-arch nightly tarball URL is built. Only the *version* is a pin (it
# changes on every bump and so lives in pins.json); everything below is a stable
# structural fact about TheRock's ``tarball-multi-arch`` publishing layout, so it
# lives in code rather than masquerading as pin data.
_NIGHTLY_CHANNEL = "https://rocm.nightlies.amd.com/tarball-multi-arch"
_TARBALL_STEM = "therock-dist-linux"

# Plain AMDGPU arch number -> the published ``tarball-multi-arch`` bundle token
# that ships it. This is an explicit lookup table, never a derivation rule,
# because the token is *not* a function of the number: ``1201`` rides in the
# ``gfx120X-all`` family bundle while ``1151`` is published standalone as
# ``gfx1151``. Support a new arch by adding one (verified) row here.
_GFX_URL_TARGETS = {
    "1200": "gfx120X-all",
    "1201": "gfx120X-all",
    "1151": "gfx1151",
}


def _resolve_pin(knobs: PinnedTarballKnobs) -> tuple[str, str]:
    """Resolve (url, version) for ``knobs``.

    The only pin is the ``version``, read from the repo-root ``pins.json``
    (discovered by ``load_pin_entry``). The URL is then built in code from that
    version and the plain arch number in ``knobs.gfx_target`` (an input): the
    number is mapped via the in-code :data:`_GFX_URL_TARGETS` table to a published
    tarball token and assembled by :func:`_tarball_url`.
    """
    entry = load_pin_entry(knobs.pin)
    version = entry.get("version")
    if not version:
        raise RocmInstallError(f"pin {knobs.pin!r} must define 'version'")
    return _tarball_url(_url_target(knobs), version), version


def _tarball_url(url_target: str, version: str) -> str:
    """Assemble the nightly tarball URL from its constant parts and the version."""
    return f"{_NIGHTLY_CHANNEL}/{_TARBALL_STEM}-{url_target}-{version}.tar.gz"


def _url_target(knobs: PinnedTarballKnobs) -> str:
    """Map the plain gfx arch number to its ROCm tarball *target token*.

    The pipeline threads a plain arch number (``1201``); the published
    ``tarball-multi-arch`` bundle that ships it is *not* a simple function of that
    number -- ``1201`` lives in the ``gfx120X-all`` family bundle while ``1151`` is
    published standalone as ``gfx1151`` -- so the mapping is the explicit in-code
    :data:`_GFX_URL_TARGETS` table, never a derivation rule. An arch with no row
    fails here with an actionable error rather than fetching a guessed, wrong URL.
    """
    target = _GFX_URL_TARGETS.get(knobs.gfx_target)
    if not target:
        have = ", ".join(sorted(_GFX_URL_TARGETS)) or "none"
        raise RocmInstallError(
            f"no ROCm tarball target for gfx {knobs.gfx_target!r}; add a "
            f"'{knobs.gfx_target}' entry to _GFX_URL_TARGETS in builds/rocm.py "
            f"(have: {have})"
        )
    return target


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
