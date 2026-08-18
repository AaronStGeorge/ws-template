---
name: layering
description: Build and sharpen a project's layered context docs. Use to define domain terminology, record component requirements, interfaces, and designs, decompose a system into components, or maintain the domain model during a grilling or explore session.
---

**DO WRITE TO CONTEXT.md outside of a specified design session. If launched within a grilling, this is a design session, if the user has told you this is a design session it's a design session. Otherwise CONTEXT.md is read only. Feel free to note to the user if design principles are being violated by current non-design session.**

# Layering

> You can outsource your thinking but you cannot outsource your understanding.

Agents can implement systems faster than humans can understand them. Code is too detailed and verbose to be the medium in which a human and an agent build a shared mental model; reading it becomes the bottleneck. `CONTEXT.md` holds the shared mental model.

This skill is the *active* discipline of building and maintaining a shared understanding of the system: challenge terms, record decisions as they crystallize, and describe each component's problem, interface, and approach at a level a human can hold.

## What CONTEXT.md holds

Four sections, in this order: Language (the ubiquitous design vocabulary), Requirements (*why* the component exists), Spec (*what* it commits to at its boundary), Design (*how* it works inside). Read [CONTEXT-FORMAT.md](./CONTEXT-FORMAT.md): it is the authority on the format.

## How the docs nest

Layered by default. The root `CONTEXT.md` describes the system as a whole; its Design composes the components, naming each and linking its `CONTEXT.md`. Each component doc lives where that component lives and describes how it meets the requirements from above.

**Rooted anywhere.** A `CONTEXT.md` tree is rooted wherever exploration or design begins — the directory bounding the system someone chose to understand or build. The root is a choice of scope, not a property of the repository; a subsystem deep in a larger tree can carry its own root. Every tree starts as one file.

**Requirements flow downward.** A child's Requirements are assigned when its parent's Design carves out the component: the parent states what it needs from the child, and the child receives those requirements. A component does not author its own Requirements. Renegotiate them in the parent context.

**Composition contracts live with the composition.** A contract between children is defined once, in the Design of their nearest common ancestor; each child's obligations flow into its Requirements, and each child's Spec exposes its side with a normative reference back — never a second authoritative copy. Promote only contracts the composition owns and that bind multiple children: an interface one child independently owns stays authoritative in that child's Spec, with required use flowing into consumers' Requirements/Spec by reference and merely-incidental use staying in the consumer's Design.

**Language also flows downward.** A child inherits every ancestor's vocabulary. It may add local terms or sharpen a parent term for its boundary, but it may not silently contradict or redefine ancestral language. Resolve conflicts where the shared term was established.

**Docs split at seams, not at size.** Create a component `CONTEXT.md` only at a true system seam: a component with Requirements received from above, a Spec offered back, and an interior of its own. Size is irrelevant; a small system may need only the root. Create files lazily, when the first term or decision belongs there.

**Membership is by links, not directory nesting.** A doc belongs to the tree whose Design links it; the root is the doc nothing links. A `CONTEXT.md` in an ancestor directory that does not link down is a different system's tree. Adopting a formerly independent tree is an explicit act: the new parent's Design links its root, and that root's Requirements are renegotiated — received now, not elicited. To orient in a session, follow links from the governing root; when it is unclear which doc a topic belongs to, ask.

## During the session

### Police the vocabulary

When the user's term conflicts with the current Language section or an ancestor's, call it out immediately: "Your parent glossary defines 'cancellation' as X, but you seem to mean Y — which is it?" When a term is vague or overloaded, propose a precise canonical one: "You're saying 'account' — do you mean the Customer or the User?"

### Cross-reference code, docs, and sections

`CONTEXT.md` records normative design; it does not claim that the code already implements it. Gaps are expected whenever design intentionally leads implementation, or context is being built up in a new system. If the code is believed to implement the design, a gap may instead indicate a defect, stale documentation, or an abandoned decision. Use the user's working context to determine whether code or docs lead, report the gap without assuming a contradiction, and help them converge.

When the user describes how something works or should work, compare it with the code and classify any gap according to whether code or docs are intended to lead.

Cross-check the sections too: a Spec clause that no Requirement motivates may be scope creep; a Requirement that no Spec behavior satisfies is an unmet need; Design should show how it realizes the Spec. Suspect an abstraction leak only when the Spec exposes an interior choice that consumers neither observe nor need to rely upon. If Design discoveries constrain the interface, revise the commitment explicitly where it is owned.

Across layers, trace every child obligation from the parent's Design through the child's Requirements to behavior in the child's Spec. Treat independent restatements of one composition contract as competing authorities, not harmless duplication. Name the conflicting statements; that identifies the conversation to have.

### Record decisions as they crystallize

When a term, requirement, interface decision, or design choice is resolved, record it immediately in the section and `CONTEXT.md` where it belongs. Sessions can end abruptly; unwritten resolutions disappear.

## What this skill deliberately does not do

This skill does not write implementation code or produce tickets, this is a design discipline focused on building and maintaining a shared understanding of the system through.
