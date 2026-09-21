# ADR-033: A stream whose frame size is set by a model or payload is never read with `readline()`

- **Status**: Accepted
- **Date**: 2026-09-14
- **Supersedes**: nothing

## Context

`asyncio.StreamReader.readline()` -- and the `async for line in reader` sugar over
it -- is bounded by the reader's buffer limit, 64 KiB by default. When one frame
exceeds that limit the read does not return a partial line; it raises
`LimitOverrunError`, which `readline()` converts to a bare
`ValueError: Separator is found, but chunk is longer than limit` (or `Separator is
not found, and chunk exceed the limit`, depending on whether the newline was
already buffered). The consumer dies mid-stream with an unattributable message.

Verified failure: project `ettm_TEST_Claude`, generate run 257. The OpenCode
adapter read the `opencode run --format json` event stream this way. One event
carries a whole tool result -- a subagent report, a file read -- so its size is
set by the model, not by the protocol. The run failed 43 seconds in with an empty
`_analysis/` and no attribution beyond "Generation failed but staging is
preserved". The same defect was latent in the stderr reader and in the connector
facade's `_bridge_request`, which read broker responses at the default limit
while the server it talks to was configured at 1 MiB.

## Decision

Newline-delimited streams of this kind are read through
`mcp_server/utils/stream_frames.py`, which reads raw chunks and splits on
newlines itself.

1. **The transport buffer is never the frame ceiling.** Raising `limit=` on the
   subprocess or connection is a throughput knob only. It moves the cliff; it
   does not remove it, because no fixed buffer can bound a size the model picks.
2. **The one real bound is explicit and stated in bytes.** `max_frame_bytes` is
   passed by the call site, which knows what the frame is for.
3. **Streams skip, request/response pairs raise.** A telemetry stream drops the
   oversized frame, reports its byte count, and resynchronises on the next one
   (`iter_frames`). A request/response pair cannot use a truncated payload, so it
   raises the typed `OversizedFrameError` its caller can map to a protocol error
   (`read_frame`).

## Consequences

- A verbose tool result costs a gap in token accounting, not a failed run: the
  documents themselves reach staging through the session's file tools, so the
  event lane is telemetry and is failure-isolated per event.
- Regression tests must pin the transport buffer back to asyncio's 64 KiB
  default. A test that inherits a widened buffer passes for the wrong reason --
  this was caught once already while building the fix.
- Any new subprocess or socket reader of newline-delimited frames uses this
  module. `communicate()` remains correct where the whole output is wanted at
  once; it has no separator limit.
