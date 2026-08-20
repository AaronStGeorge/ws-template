# CONTEXT.md Format

## Structure

The four top-level sections and their order are fixed. Everything within them
is illustrative: omit placeholders and subsections that do not apply, and use
prose, lists, examples, tables, or diagrams as the material demands.

These are learning-oriented design documents. Their job is to transfer a
working mental model — a reader should be able to *predict the system*
afterward, not merely look things up — and concrete teaches faster than
abstract: a real workflow, a data-flow sketch, or a diagram of the system in
motion usually beats prose that describes it.

```md
# {Component Name}

{Optional concise synopsis derived from the sections below.}

## Language

{Define the canonical terms whose project-specific meanings need agreement.
Make each definition easy to find. Add `_Avoid_` only where competing words
would cause real ambiguity.}

## Requirements

{State why the component exists: the problem the level above needs it to
solve, the requirements received from the parent's Design (or elicited from
the user at the root), and its constraints. Use prose or bullets, whichever
reads naturally.}

{When useful, state non-goals that clarify the component's boundary.}

{Place each unresolved `OPEN: {question}` where its answer will belong.}

## Spec

{State the behavior visible to the level above in the form that communicates
it most directly: an interface or wire contract, a CLI, user stories,
consumer-visible invariants, worked examples, or supported testing
interfaces. It must be observable and checkable without opening the interior.}

{Place each unresolved `OPEN: {question}` where its answer will belong.}

## Design

{Explain how the component fulfills its Spec. Organize strategy, tactics,
rationale, and true child components in the order clearest for this component.
Keep instructive decisions and pivots beside the strategy they explain. Link
each child context and state the requirements this Design assigns to it.}

{Place each unresolved `OPEN: {question}` where its answer will belong.}
```

## Rules

### Open questions (any section)

- Any section may carry `OPEN: {question}` lines — the question sits where its answer will eventually live.
- A sparse section with an honest `OPEN:` beats a confidently filled one. The doc is living; it firms up as understanding emerges, and its authority comes from recording only what's actually settled. Never fill a section for appearance.
- An open question closes when its answer crystallizes — don't rush it — and resolves in place: the resolution replaces the question.

### Language

- **Choose deliberately.** When competing words would cause confusion, pick one canonical term and list the others under `_Avoid_`. Omit `_Avoid_` when there is no real ambiguity.
- **Keep definitions tight.** Establish the concept's identity and boundary first; include distinguishing behavior, relationships, or examples when clarity requires them.
- **Include terms whose project-specific meaning needs agreement.** Omit standard concepts used conventionally, but include an ordinary term when this project gives it a specialized meaning.
- **Group terms under subheadings** when natural clusters emerge.
- **Inherit ancestral language.** A child may add local terms or sharpen a term for its own boundary, but must not contradict or silently redefine vocabulary established by a parent. Resolve shared-language conflicts where the term was established.

### Requirements

- State the problem and its constraints without inventing interior implementation choices. A mechanism imposed by an ancestor, compatibility obligation, platform, or environment is a received constraint and may be named here; choices made inside the component belong in Design.
- A child may name an ancestor-owned composition contract and summarize its assigned obligations because that contract is a received constraint; reference rather than copy the contract's mechanisms.
- At the root, requirements are elicited from the user. In a component, they arrive from the parent's Design — received, not authored. If a component's requirements feel wrong, the conversation is at the parent.
- State non-goals when they clarify scope or prevent a likely misunderstanding; omit empty boilerplate.
- Once settled, a change here is noteworthy because it usually means something above changed.

### Spec

- Spec records stable commitments at the boundary; Design records how those commitments are achieved. Consumers depend on Spec — plus any ancestral composition contract it explicitly references — never on Design.
- Form is flexible: a public interface, a wire/JSON contract, a CLI, user stories, invariants — whatever states the behavior visible to the level above most directly. The gate is fixed regardless of form: could a consumer or boundary-level test check this line without opening the interior? (That includes supported testing interfaces; internal test seams belong in Design or code.)
- A child participating in an ancestor-owned composition contract states its side here and normatively references the ancestor's Design; do not create another authoritative copy of the shared contract.
- Keep interior-only choices out of Spec. A cache, queue, planner, protocol, or other mechanism belongs here only when it crosses the boundary or consumers rely on it; otherwise specify the resulting behavior. The same line divides invariants: ones consumers may rely on are Spec, ones only the implementation cares about are Design tactical notes.
- Concrete beats abstract: a worked input → output example is worth a paragraph of description.

### Design

- Make the strategy clear, then include the tactical considerations that matter: ordering constraints, failure handling, and the thing likely to surprise the next person. Organize them in whatever order communicates the Design best.
- A contract this composition introduces is defined once, here, at the participating children's nearest common ancestor; each child's obligations flow into its Requirements and Spec. The full promotion rules live in SKILL.md "How the docs nest."
- Decisions and instructive pivots live here, inline — there are no ADR files. Record them when their lessons help explain the current system, especially when a choice is hard to reverse, surprising without context, or shaped by a real trade-off. Give each lesson as much space as its explanatory value earns; omit dead-end history that does not clarify the system. Design carries the current strategy and the reasoning that shaped it; version control carries chronology and deeper forensics.
- Name and link true child components here. Each gets its own `CONTEXT.md` lazily when the first term or decision belongs there, and its Requirements receive what this Design asks of it. Dependencies that are not architectural children need no child context.
- The section can be as large as it needs to be, and can include code snippets, worked examples, or diagrams wherever they capture a decision or mechanism more precisely than prose. Length is earned by usefulness — a one-paragraph Design is equally fine.
