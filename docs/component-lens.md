# The Component Lens

> You can outsource your thinking but you cannot outsource your understanding.

A way of understanding and communicating a system. It is a lens, not a
document format: apply it in conversation, in a file's narrative header, in a
directory's README, in a requirements audit — anywhere a system needs to be
understood or explained.

## The point

A description of a component exists to transfer a working mental model — a
reader should be able to *predict the system* afterward, not merely look
things up — and it must never restate what the code already says. Form is
free: prose, a diagram, a worked call and its result, a serialization of the
most important data contract, a data-flow sketch, a walkthrough of one
request — whatever teaches this system fastest. No method is required and
none is banned; concrete usually beats abstract.

Two tests for any sentence of description:

- Is it just a re-statement of the code? Leave it to the code. Is it an
  answer to something that can't be in the code — why design X over Y — or
  something the code states only diffusely? Then it earns its place.
- Could a reader who saw only the description predict how the component
  behaves at its boundary, and why it exists?

## The frame

A system is a tree of components. A component is an abstraction meant to ease
cognitive load: a boundary drawn so that a person can hold one piece at a
time, trusting each level's commitments without holding its interior. Each
component is understood through four questions, in order:

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

The questions are a checklist for the writer, not an outline for the reader:
answer them in whatever order and form communicates best.

## Rules that give the frame its teeth

- **Requirements flow downward.** A child's requirements are assigned where
  its parent's design carves out the component. Renegotiate them at the
  parent, not inside the child.
- **Spec is boundary, design is interior.** An interior mechanism — a cache,
  a queue, a protocol — belongs in the spec only when consumers rely on it;
  otherwise state the resulting behavior and keep the mechanism in the
  design. The same line divides invariants: ones consumers may rely on are
  spec, ones only the implementation cares about are design.
- **Composition contracts live with the composition.** A contract between
  sibling components is owned once, by the nearest common ancestor's design;
  each child carries its obligations, never a second authoritative copy.
- **Language is inherited downward.** A child may add local terms or sharpen
  a parent's term for its own boundary, but may not silently contradict or
  redefine ancestral vocabulary. Resolve conflicts where the term was
  established.
- **Components split at seams, not at size.** A true component has
  requirements received from above, a spec offered back, and an interior of
  its own. Size is irrelevant; a small system may be a single component.
  Components are not obliged to follow repository or directory boundaries,
  but they likely should — when the component tree and the file tree
  disagree, suspect one of them.

## Cross-checks the lens enables

The four questions check each other:

- A spec clause no requirement motivates is scope creep.
- A requirement no spec behavior satisfies is an unmet need.
- A "requirement" that names a mechanism, or has no level above asking for
  it, is suspect — it is probably a design choice or nobody's need.
- A spec that exposes an interior choice consumers neither observe nor rely
  on is an abstraction leak.
- Two independent restatements of one composition contract are competing
  authorities, not harmless duplication — name them; that identifies the
  conversation to have.
