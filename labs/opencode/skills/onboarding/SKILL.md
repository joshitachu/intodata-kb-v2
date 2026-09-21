---
name: onboarding
description: Use when the user wants to bring a NEW codebase into Nexus, or asks how to. Triggers on "onboard this project", "add this repository", "I have a ZIP of a Talend project", "can you scan this codebase", "connect our Git repo", and on resuming or checking an onboarding run that was interrupted. Do NOT use when the user asks to be briefed on a project that already exists -- that is an estate review -- and do NOT use it to regenerate documentation for a project that is already onboarded.
---

# Onboarding a project

Onboarding creates a project and connects a source, then starts a scan that
runs long after the call returns. It is cheap to ask one more question and
expensive to onboard the wrong thing, so collect first and trigger once.

## Procedure

1. **Check it is actually new.** `nexus_query_project_metadata` with
   `query_type=project_list`. If the project already exists, this is not an
   onboarding: the user wants a review, a regeneration, or a status check. Say
   so and switch.

2. **Collect what the source needs, one question at a time.** Do not present a
   form. Ask, listen, and move on.
   - **Project identifier** -- what this project will be called in Nexus.
   - **Source** -- a Git repository URL, or an uploaded ZIP.
   - **Platform** -- the scanner platform of the *codebase*, not of the code
     host. "It is on GitHub" does not answer this; "it is a Talend project"
     does. If the user is unsure, ask what the integrations were built in.
   - **Documentation Pack** (optional) -- offer
     `nexus_list_documentation_packs` only if the user wants to choose one;
     otherwise let the default apply and say that is what you are doing.

3. **Read it back and get a yes.** One short summary of what you are about to
   onboard, then wait for confirmation. This is the last cheap moment.

4. **Trigger once.** `nexus_onboard_project`. The call returning means the work
   started, not that it finished.

5. **Set the expectation.** Say plainly that scanning a codebase takes time,
   that the user does not have to wait in the conversation, and roughly what
   comes out at the end.

6. **Check status later, do not re-trigger.**
   `nexus_get_documentation_generation_status` for a known project, or
   `nexus_list_active_project_workflows` to see what is running. Calling
   `nexus_onboard_project` again to "check" starts a second run.

7. **If a run stalled waiting for corrected input**, use
   `nexus_resume_onboarding` with the corrected value. A fresh
   `nexus_onboard_project` is not the repair path.

## Rules

- Never invent a repository URL, a platform, or an upload receipt. Missing
  means ask.
- If the user pastes credentials, a token, or a password, do not repeat them
  back and do not put them in any call. Say that the connection is configured
  outside the chat.
- Onboarding is where a client's first impression is set. Plain language, no
  internal identifiers, no workflow ids.
