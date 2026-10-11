"""Use one checked live stream for every chat model; never retry unsafe output."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from stream_safety import StreamSafetyResult


@dataclass(frozen=True)
class RealtimeTransportOutcome:
    result: StreamSafetyResult
    visible_text: str
    used_fallback: bool = False
    manual_retry_required: bool = False


async def consume_realtime_transport(
    *,
    mode: str,
    source_factory: Callable[[], Any],
    safe_consumer: Callable[[Any], Awaitable[tuple[StreamSafetyResult, str]]],
    legacy_consumer: Callable[[Any], Awaitable[tuple[StreamSafetyResult, str]]],
    reset_visible: Callable[[], Any | Awaitable[Any]],
    tts_streamer=None,
) -> RealtimeTransportOutcome:
    """Old mode/callback arguments are accepted for callers, but never selected."""
    result, visible = await safe_consumer(source_factory())
    if result.stop_reason and tts_streamer is not None:
        await tts_streamer.abort()
    return RealtimeTransportOutcome(
        result=result,
        visible_text=visible,
        manual_retry_required=result.stop_reason is not None,
    )
