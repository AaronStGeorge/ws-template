---
name: grill-with-docs
description: A relentless interview that sharpens a plan or design while recording settled understanding in doc comments and READMEs.
disable-model-invocation: true
---

Run a `/grilling` session. As decisions settle, record the sharpened
understanding where the code lives, viewed through the
[component lens](../../../docs/component-lens.md):

- **File scale** — the file's narrative doc-comment header, per the
  "narrative header" rule in
  [`docs/style/always.md`](../../../docs/style/always.md).
- **Component/directory scale** — that directory's `README.md`, written in
  the same narrative style: the lens applied at directory scale.

If design is running ahead of implementation, empty files and directories can be
created to hold the README and doc-comment headers, so that the design can be
recorded before the code is written.

Record a resolution immediately when it crystallizes; sessions end abruptly
and unwritten resolutions disappear.
