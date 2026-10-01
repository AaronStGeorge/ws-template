# The Component Lens

> You can outsource your thinking but you cannot outsource your understanding.

A way of understanding and communicating a system. It is a lens, not a
document format: apply it in conversation, in a file's narrative header, in a
directory's README, in a requirements audit — anywhere a system needs to be
understood or explained.

## The point

A component description should give its intended reader enough understanding
to use, change, or reason about the component. Choose the scope and detail for
that reader's task.

Summarize observable behavior when readers need it without inspecting the
implementation: invocation, outputs, failures, and guarantees can belong in
documentation even when the code also expresses them. Comments beside code
should explain context, constraints, or decisions that the adjacent code does
not make clear.

Judge a passage by the understanding it adds. Remove narration that merely
translates nearby code into prose. Preserve explanations of non-obvious
consequences and why plausible alternatives were rejected.

Judge the description as a whole by whether readers can answer the questions
relevant to their task. An introductory README, an implementation comment, and
a tutorial need different coverage; none must explain the entire component
independently.

Form is free: prose, a diagram, a worked call and its result, a serialization
of the most important data contract, a data-flow sketch, a walkthrough of one
request — whatever teaches this system fastest. No method is required and
none is banned; concrete usually beats abstract.

## The frame

A system is a tree of components. A component is an abstraction meant to ease
cognitive load: a boundary drawn so that a person can hold one piece at a
time, trusting each level's commitments without holding its interior. Each
component is understood through four questions:

- **Language** — the canonical vocabulary at this level: project-specific
  terms whose meaning needs agreement, and ordinary words this system gives a
  specialized meaning.
- **Requirements** — *why* it exists: the problem the level above needs it to
  solve, and the constraints it was built under. Received from the parent's
  design; at the top of whatever scope you're examining, they come from
  outside it — the user or the wider system. A component does not author its
  own requirements; if they feel wrong, the conversation is at the parent.
- **Spec** — *what* it commits to at its boundary: behavior a consumer can
  observe and check without opening the interior.
- **Design** — *how* it delivers the spec inside: the strategy, the decisions
  and trade-offs that shaped it, and the child components it carves out —
  with what it asks of each.

Use these questions to find gaps in understanding. They do not require an
answer in every document; an existing explanation may already serve the
reader.

## Rules that give the frame its teeth

- **Requirements flow downward.** A child's requirements are assigned where
  its parent's design carves out the component. Renegotiate them at the
  parent, not inside the child.
- **Spec is boundary, design is interior.** An interior mechanism — a cache,
  a queue, a protocol — belongs in the spec only when consumers rely on it;
  otherwise state the resulting behavior and keep the mechanism in the
  design. The same line divides invariants: ones consumers may rely on are
  spec, ones only the implementation cares about are design.
- **Composition contracts live with the composition.** Define a contract
  between sibling components in their nearest common ancestor's design.
  Children document their own obligations and may summarize the shared
  contract for local understanding, linking to its definition.
- **Language is inherited downward.** A child may add local terms or sharpen
  a parent's term for its own boundary, but may not silently contradict or
  redefine ancestral vocabulary. Resolve conflicts where the term was
  established.
- **Components split at seams, not at size.** A true component has
  requirements received from above, a spec offered back, and an interior of
  its own. Size is irrelevant; a small system may be a single component.

## Cross-checks the lens enables

The four questions check each other:

- A spec clause no requirement motivates is scope creep.
- A requirement no spec behavior satisfies is an unmet need.
- A "requirement" that names a mechanism, or has no level above asking for
  it, is suspect — it is probably a design choice or nobody's need.
- A spec that exposes an interior choice consumers neither observe nor rely
  on is an abstraction leak.
