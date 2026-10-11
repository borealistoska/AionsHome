import unittest
from unittest.mock import AsyncMock, Mock
from realtime_stream_transport import consume_realtime_transport
from stream_safety import StreamSafetyResult

class RealtimeStreamTransportTest(unittest.IsolatedAsyncioTestCase):
    async def test_old_mode_setting_still_uses_live_stream(self):
        result = await consume_realtime_transport(
            mode='legacy', source_factory=lambda: None,
            safe_consumer=AsyncMock(return_value=(StreamSafetyResult('正文'), '正文')),
            legacy_consumer=AsyncMock(side_effect=AssertionError('legacy must not run')),
            reset_visible=lambda: None)
        self.assertEqual(result.visible_text, '正文')
        self.assertFalse(result.used_fallback)

    async def test_failure_keeps_checked_prefix_and_cancels_audio_without_retry(self):
        tts = Mock()
        tts.abort = AsyncMock()
        attempts = []
        def source():
            attempts.append(1)
        outcome = await consume_realtime_transport(
            mode='safe_live', source_factory=source,
            safe_consumer=AsyncMock(return_value=(StreamSafetyResult('安全正文', stop_reason='quality'), '安全正文')),
            legacy_consumer=AsyncMock(side_effect=AssertionError('must not retry')),
            reset_visible=lambda: self.fail('must retain checked prefix'), tts_streamer=tts)
        self.assertEqual(attempts, [1])
        self.assertEqual(outcome.visible_text, '安全正文')
        self.assertTrue(outcome.manual_retry_required)
        tts.abort.assert_awaited_once()
