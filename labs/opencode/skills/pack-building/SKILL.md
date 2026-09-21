---
name: pack-building
description: Use when the user wants to create, draft, configure, refine, validate or save a documentation Pack or Analysis Pack, or asks what a Pack should contain. Triggers on "build me a pack", "create an analysis pack", "add a document to the pack", "change the pack to also cover X", "is my pack ready to save". Do NOT use when the user only asks which Packs already exist or whether Packs are available -- that is one listing call, not this procedure.
---

# Building a Pack

A Pack is a reusable specification for what documentation gets generated. It is
saved as an immutable version, so the cost of getting it wrong is a version
nobody can edit. Draft, refine, validate, then save -- and only save when the
user says to.

## Procedure

1. **Check what already exists.** `nexus_list_documentation_packs`. An
   organization Pack that already covers the need is a better answer than a new
   private one. Tell the user what you found and why it does or does not fit
   before building anything.

2. **Settle the brief in the user's own words.** Five things have to be known:
   the Pack's name, its purpose, its audience, the components it focuses on,
   and the documents it should output. If any one of them is missing or
   ambiguous, ask ONE focused question and wait. Do not create a record on a
   guess -- an abandoned draft is visible clutter the user has to clean up.

3. **Create the draft.** `nexus_create_pack_draft` with `name` and nothing
   else. Purpose, audience, focus and outputs are configured afterwards, not in
   this call.

4. **Refine in plain language.** `nexus_refine_pack_draft`, with the user's own
   wording in `instruction` and the draft's current `expected_revision`. The
   Pack author applies the instruction and returns a complete manifest, so
   earlier choices survive. Never write manifest keys, section ids or document
   schemas yourself -- that is the author's job, and hand-written keys are how
   a draft becomes unsavable.

5. **Read back what it became.** `nexus_read_pack_draft`, then describe in
   plain terms what documents this Pack will produce and for whom. The revision
   number changes on every refine; re-read it rather than reusing a stale one,
   or the next call fails on a revision mismatch.

6. **Validate.** `nexus_validate_pack_draft`. Report any failure as what is
   still missing in the user's terms -- "it still needs at least one output
   document" -- not as raw validation output. Fix by refining, then validate
   again.

7. **Save only on an explicit instruction to save.** `nexus_save_pack`. The
   saved version is immutable. Until it returns, the Pack does not exist: do
   not tell the user it has been created.

## One-shot creation

`nexus_create_pack` does steps 3 to 7 in a single action. Use it only when all
five brief elements are already settled in the conversation and the user has
asked for the Pack now. If you would have to guess at any of the five, use the
draft route instead.

## Rules

- Abandoning a draft (`nexus_abandon_pack_draft`) is destructive and needs the
  user to ask for it.
- A Pack describes what to document. It is not the documentation, and listing
  Packs never answers a question about a project's content.
- Building a Pack takes a moment; say so plainly rather than going silent.
