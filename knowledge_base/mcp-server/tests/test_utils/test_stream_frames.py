from __future__ import annotations

import asyncio

import pytest

from mcp_server.utils.stream_frames import (
    OversizedFrameError,
    iter_frames,
    read_frame,
)


def _reader(payload: bytes, *, limit: int = 64 * 1024) -> asyncio.StreamReader:
    # Must be constructed inside a running loop: StreamReader binds one.
    reader = asyncio.StreamReader(limit=limit)
    reader.feed_data(payload)
    reader.feed_eof()
    return reader


def _collect(payload: bytes, *, limit: int = 64 * 1024, **kwargs) -> list[bytes]:
    async def run() -> list[bytes]:
        reader = _reader(payload, limit=limit)
        return [frame async for frame in iter_frames(reader, **kwargs)]

    return asyncio.run(run())


def test_readline_is_the_defect_being_fixed():
    # Pins the failure this module exists to remove: the stock line iteration
    # raises `Separator is found, but chunk is longer than limit` on one long
    # frame, which is exactly how an OpenCode run died mid-stream.
    async def run() -> None:
        await _reader(b"x" * 200_000 + b"\n", limit=64 * 1024).readline()

    with pytest.raises(ValueError, match="longer than limit"):
        asyncio.run(run())


def test_frame_far_above_the_stream_limit_is_yielded_whole():
    payload = b"y" * 500_000
    frames = _collect(
        b'{"a":1}\n' + payload + b"\n",
        limit=64 * 1024,
        max_frame_bytes=8 * 1024 * 1024,
    )
    assert frames == [b'{"a":1}', payload]


def test_frames_are_split_across_chunk_boundaries():
    assert _collect(b"one\ntwo\nthree\n", max_frame_bytes=1024) == [
        b"one", b"two", b"three",
    ]


def test_trailing_frame_without_newline_is_yielded():
    assert _collect(b"one\ntwo", max_frame_bytes=1024) == [b"one", b"two"]


def test_oversized_frame_is_dropped_and_the_stream_continues():
    seen: list[int] = []

    async def record(size: int) -> None:
        seen.append(size)

    frames = _collect(
        b"before\n" + b"z" * 5000 + b"\nafter\n",
        max_frame_bytes=1024,
        on_oversized=record,
    )
    assert frames == [b"before", b"after"]
    assert seen == [5000]


def test_oversized_trailing_frame_without_newline_is_reported_not_yielded():
    seen: list[int] = []

    async def record(size: int) -> None:
        seen.append(size)

    frames = _collect(
        b"before\n" + b"z" * 5000, max_frame_bytes=1024, on_oversized=record,
    )
    assert frames == [b"before"]
    assert seen == [5000]


def _read_one(payload: bytes, *, max_frame_bytes: int) -> bytes:
    async def run() -> bytes:
        return await read_frame(_reader(payload), max_frame_bytes=max_frame_bytes)

    return asyncio.run(run())


def test_read_frame_returns_the_first_frame_only():
    assert _read_one(
        b'{"ok":true}\n{"next":1}\n', max_frame_bytes=1024,
    ) == b'{"ok":true}'


def test_read_frame_raises_a_typed_error_when_oversized():
    with pytest.raises(OversizedFrameError):
        _read_one(b"z" * 5000 + b"\n", max_frame_bytes=1024)


def test_read_frame_on_empty_stream_returns_empty():
    assert _read_one(b"", max_frame_bytes=1024) == b""
