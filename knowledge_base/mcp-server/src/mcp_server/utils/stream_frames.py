"""Newline-delimited frame reading that one oversized frame cannot kill.

``asyncio.StreamReader.readline()`` -- and the ``async for line in reader``
sugar over it -- is bounded by the reader's buffer limit (64 KiB by default).
The moment a producer emits one frame larger than that limit, the read raises
``ValueError: Separator is found, but chunk is longer than limit`` and the
consumer dies mid-stream.

Every producer this package reads that way sizes its frames from model output
or evidence payloads: the OpenCode CLI's ``--format json`` event stream carries
whole tool results on a single line, and the run-scoped connector facade
answers with a serialized evidence payload.  Neither has a protocol ceiling
that matches a transport buffer, so the limit is not a guarantee -- it is a run
failure waiting for a large tool result.

Reading raw chunks and splitting them here removes the buffer limit as a
ceiling on frame size, and makes the one remaining bound explicit: a frame
beyond ``max_frame_bytes`` is dropped with its byte count reported, rather than
ending the stream.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable

_READ_CHUNK_BYTES = 65536

# Async so callers can report an overflow through their own logging pipeline.
OversizedCallback = Callable[[int], Awaitable[None]]


class OversizedFrameError(ValueError):
    """A newline-delimited frame exceeded the caller's explicit byte bound.

    Distinct from the transport's ``ValueError`` so callers can answer with
    their own protocol error instead of an unattributable failure.
    """


async def iter_frames(
    reader: asyncio.StreamReader,
    *,
    max_frame_bytes: int,
    on_oversized: OversizedCallback | None = None,
) -> AsyncIterator[bytes]:
    """Yield newline-delimited frames from ``reader`` without a separator limit.

    ``StreamReader.read()`` never raises ``LimitOverrunError``; it only drains
    whatever the transport buffered, so the reader's limit becomes a throughput
    knob instead of a hard frame ceiling.  A frame exceeding ``max_frame_bytes``
    is discarded up to and including its terminating newline and reported to
    ``on_oversized`` with the total bytes dropped; the stream continues with the
    next frame.  A trailing frame with no terminating newline is yielded.
    """
    buffer = bytearray()
    overflow_bytes = 0  # > 0 while discarding the tail of an oversized frame
    while True:
        chunk = await reader.read(_READ_CHUNK_BYTES)
        if not chunk:
            break
        buffer.extend(chunk)
        while (newline := buffer.find(b"\n")) >= 0:
            frame = bytes(buffer[:newline])
            del buffer[: newline + 1]
            # Either the tail of a frame already ruled oversized, or a complete
            # frame that arrived in one chunk and is oversized on its own.
            if overflow_bytes or len(frame) > max_frame_bytes:
                if on_oversized is not None:
                    await on_oversized(overflow_bytes + len(frame))
                overflow_bytes = 0
                continue
            yield frame
        if len(buffer) > max_frame_bytes:
            # No newline in sight and the partial frame is already oversized.
            # Drop what is held and keep discarding until the frame terminates.
            overflow_bytes += len(buffer)
            buffer.clear()
    if overflow_bytes or len(buffer) > max_frame_bytes:
        if on_oversized is not None:
            await on_oversized(overflow_bytes + len(buffer))
    elif buffer:
        yield bytes(buffer)


async def read_frame(
    reader: asyncio.StreamReader,
    *,
    max_frame_bytes: int,
) -> bytes:
    """Read exactly one newline-delimited frame, or raise on an oversized one.

    Used where the frame is a request/response pair rather than a stream: a
    truncated payload is not usable, so the overflow is an error instead of a
    skip.
    """
    async for frame in iter_frames(
        reader,
        max_frame_bytes=max_frame_bytes,
        on_oversized=_raise_oversized,
    ):
        return frame
    return b""


async def _raise_oversized(size: int) -> None:
    raise OversizedFrameError(f"frame exceeds byte limit ({size} bytes)")
