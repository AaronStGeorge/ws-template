---
name: layering
description: Answer a question about the current system by viewing it through the component lens — a guided tour along the vector of the question through the existing codebase.
disable-model-invocation: true
---

Take the user's question and answer it through the
[component lens](../../../docs/component-lens.md). This skill produces
nothing — no files, no docs, no changes. The deliverable is understanding:
a working mental model of the slice of the system the question cuts through,
good enough that the user could predict the system's behavior there
afterward.

Where `/grill-with-docs` negotiates a shared understanding and records it,
here the code is the authority and you are the guide. Read it — dispatch
sub-agents for breadth — and reconstruct the component tree *as it actually
is*, not as anyone wishes it were.

**Tour only the vector.** The question traces a path through the tree: the
components it enters, the boundaries it crosses, the level where its answer
lives. Reconstruct that path, not the whole tree. Name each component on the
path and give it just enough of the four questions — language, requirements,
spec, design — for the reader to trust its boundary and move on; go interior
only where the question does.

**Answer at the right level.** Most questions are answered by one
component's spec or one parent's design. Descend until the answer stops
being "because a child does X" and becomes a commitment or a decision; that
level is the answer, and the levels above it are the tour that makes the
answer land.

**Report tensions, don't fix them.** Where the lens's cross-checks fire —
a spec clause nothing motivates, a requirement nothing satisfies, an
abstraction leak, two competing authorities for one contract, a component
tree fighting the file tree — name the tension as part of the tour and move
on. Observations, not tasks.

End with the answer to the question, stated plainly, in the vocabulary the
tour established.
