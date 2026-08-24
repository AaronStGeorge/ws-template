---
name: requirements-cop
description: Question every requirement that has accumulated in the current session — including the user's — and cut the ones that don't earn their cost. Use in plan mode once requirements are drafted and before designing against them, or whenever the user asks to cop, audit, or de-dumb the requirements.
---

# Requirements Cop

Sessions accrete requirements: the user asks for one thing, the agent infers three more, a stale doc contributes a constraint nobody re-checked. Each one fans out into spec, design, code, tests, and upkeep. The cheapest place to kill one is before anything is designed against it.

**Question every requirement, especially the ones from the user.** Every requirement gets a source, and no source is immune — the most dangerous requirement is the one from someone smart enough that nobody questioned it.

## Procedure

1. **Ledger.** The session agent lists every requirement in play — stated by the user, inferred by an agent, pulled from a doc, introduced by the plan itself. One line each: the requirement, its source, and the reason actually given (`none given` counts, and is a finding).

2. **Dispatch the cop.** A read-only sub-agent gets the ledger, the user's original ask verbatim, the list of files the plan touches, and section 3 below. The session agent spent the session accumulating these requirements and is invested in them; a fresh context is the better skeptic.

3. **Grade.** The cop thinks in the frame of the [component lens](../../../docs/component-lens.md): a requirement is a need received from the level above, distinct from the spec that answers it and the design that delivers it — a "requirement" that names a mechanism or has no level above asking for it is already suspect. For evidence of what the system commits to today, read whatever the repo offers: narrative file headers (`docs/style/always.md`), READMEs, docs, and code. For each row: **who needs it, what it drags in** (spec surface, design decisions forced, code, tests, upkeep — a changed boundary ripples to every caller), **and what breaks if we just don't** ("nothing, today" is a common and legitimate answer). One verdict each, with one line of evidence:
   - **KEEP** — traced to a real need, cost proportionate.
   - **CUT** — nobody needs it, its cascade dwarfs what breaks without it, it's already met, or it belongs to another component. Say what breaks and, if it's real-but-not-now, what trigger would revive it.
   - **SHARPEN** — real need, dumb statement: a mechanism posing as a need, unfalsifiable ("fast", "robust"), or generality nobody asked for. Propose the narrow restatement.

   The usual suspects: premature generality, unneeded backwards compatibility, "while we're here", symmetry, an example read as a requirement, "best practice". A ledger that's all KEEPs means the cop didn't do the job.

4. **Decide.** Put every CUT and SHARPEN to the user as a `/grilling`-style round, with the evidence and a recommendation. The user decides; never cut or overrule silently.

5. **Record.** Write survivors and cuts (cuts as non-goals) into whatever the session produces — usually the plan. An unwritten cut gets re-inferred by the next agent.

## Calibration

Bias to CUT. Adding a requirement back later is cheap; carrying it through design, implementation, and maintenance is not. If none of your cuts ever has to come back, you didn't cut enough — a good cop is wrong about roughly one cut in ten.
