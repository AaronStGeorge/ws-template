---
name: explore
description: Explore an existing codebase in layers, producing successive CONTEXT.md docs. Use to orient in an unfamiliar repo, build a mental model of existing code, or bootstrap the layered context docs that /layering maintains — starting from wherever the human's interest lies.
---

# Explore

`/layering` keeps `CONTEXT.md` docs ahead of the code; exploring recovers them from code that already exists. The failure mode this skill exists to prevent is the agent inhaling the whole codebase and emitting an even-resolution wiki nobody asked for — expensive to produce, exhausting to read, stale on arrival. A codebase is explored the way a person learns one: in layers, starting where the current question lives, leaving everything else deliberately rough. Each pass produces documents at one resolution; the human's interest decides which part gains resolution next.

## What a pass produces

The same docs `/layering` maintains, in the same format: [CONTEXT-FORMAT.md](../layering/CONTEXT-FORMAT.md). A root `CONTEXT.md` describing the system, and component `CONTEXT.md` files created lazily at real seams as passes descend into them. When these docs already exist, the map is partly drawn: read them first, descend from them, and reconcile — never regenerate from scratch.

One difference in stance from design-time layering: **during exploration, code leads and docs follow.** The docs describe the system as found. What the code *does* can be stated with confidence; what it is *for* — Requirements, intended invariants — is inference. Write inferred intent as the best current hypothesis and hang an `OPEN:` line where the inference is shaky, rather than asserting. The human confirms or corrects intent; the code settles behavior.

## The exploration loop

### 0. Anchor

Establish where the human's interest is: a feature, a bug, a directory, a question ("how does auth work?"), a change they intend to make. If no anchor was given, ask before exploring — the anchor determines the descent path, and exploration without one degenerates into the whole-codebase inhale this skill forbids. An anchor can be broad ("I inherited this repo, orient me"); that makes the first pass the deliverable, not a license to go deep everywhere.

### 1. First pass — the rough map

Survey the top layer only: manifests and build files, directory tree (a level or two), READMEs and existing docs, entry points, CI config. Dispatch Explore subagents for the sweep so raw file contents stay out of the main context; read directly only what shapes the map.

Produce or update the root `CONTEXT.md`:

- **Requirements** — the system's inferred purpose: what problem it appears to solve, for whom, under what constraints. Hypothesis language; `OPEN:` where intent can't be read off the code.
- **Spec** — how the system is actually used from outside: the CLI, the API surface, the UI, the deploy artifact. This is usually the most confidently knowable section in pass 1.
- **Design** — the rough component map: a handful of components carved at *apparent* seams, each with a name, a one-line responsibility, where it lives, and the `OPEN:` questions that would sharpen it. Components at this stage are provisional isolations, not commitments — no child `CONTEXT.md` files yet.

This rough map is the first deliverable. Present it to the human before descending.

### 2. Descend toward the anchor

Each subsequent pass picks the one component on the path to the anchor and explores its interior at the same rough resolution the first pass applied to the whole system: its boundary first (what it exposes, what it consumes), then its interior carved into rough child components. Write its `CONTEXT.md` — this is the moment the component's doc is created — and record in it:

- **Language** — the vocabulary this component actually uses, harvested from its code.
- **Requirements** — what the rest of the system evidently needs from it, stated as received from the parent's Design; inferred, so `OPEN:` where unsure.
- **Spec** — the boundary as observed: the interface consumers actually call, the contract they actually rely on.
- **Design** — how it works inside, at low resolution, with its own rough child components isolated for the next descent.

A descent frequently proves the parent's rough map wrong — a seam drawn in pass 1 turns out to cut through the middle of a real component, or two "components" turn out to be one. Fix the parent's Design in the same pass. Revising upward is the mechanism by which the map converges; a descent that never corrects its parent is suspicious.

### 3. Pause and re-aim

After each pass, stop. Present what changed in the map — a few sentences, not a recap of files read — plus the sharpest new `OPEN:` questions, and ask where interest points next: deeper along the anchor path, sideways into a sibling, back up because the anchor moved, or done. The human steers between passes; the agent never chooses the third pass's target on its own momentum.

### Stopping

Exploration is done when the human's question is answered, not when the codebase is exhausted. Unexplored components remain one-line stubs with `OPEN:` lines in their parent's Design — that is the record working as intended, not unfinished work. The stubs tell the next session exactly where the map's resolution runs out.

## Rules of the road

- **Resolution follows interest.** Uneven docs are the point: deep along the paths a human has cared about, rough everywhere else. Never "round out" a section, a sibling, or a layer for symmetry.
- **Read boundaries before bodies.** A component's exports, its callers, and its tests say what it is faster than its implementation does. Use Explore subagents for anything resembling a sweep; spend direct reads only on the descent path.
- **Seams are hypotheses.** Pass-1 components are guesses at the system's joints. Expect redraws; make them in the parent's Design where the seam is owned, and note an instructive redraw as `Previously: … — abandoned because …` when the wrong guess teaches something about the system.
- **Harvest vocabulary, then choose.** Prefer the names the code already uses. When the code itself disagrees — the same concept named three ways, or one word meaning two things — pick the canonical term in Language, list the competitors under `_Avoid_`, and flag the inconsistency: it is often the first real finding.
- **Docs found in the repo are claims, not ground truth.** READMEs, comments, and pre-existing design docs may be stale. Where they contradict the code, record what the code does and surface the contradiction to the human rather than silently trusting either side.
- **Write inline, `OPEN:` in place.** Every pass commits its findings to the docs before pausing; sessions end abruptly and unwritten maps disappear. Never fill a section for appearance — a sparse section with an honest `OPEN:` beats a confidently filled one.

## Handoff

The files exploration produces are the same files `/layering` maintains — there is no export step. When exploration turns into design ("now I want to change it"), switch to `/layering` to carry the docs forward normatively, `/grilling` to stress-test the intended change, and `/to-tickets` when it's settled enough to build.
