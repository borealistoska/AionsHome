import json
import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import FastAPI
import ai_providers
import system_logs


class SystemLogTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        db = patch.object(system_logs, "LOG_DB_PATH", Path(folder.name) / "logs.db")
        db.start()
        self.addCleanup(db.stop)
        broadcast = patch.object(system_logs, "_broadcast", new=AsyncMock())
        broadcast.start()
        self.addCleanup(broadcast.stop)

    async def test_background_provider_call_records_prompt_and_usage(self):
        async def respond(request):
            return httpx.Response(200, text='data: ' + json.dumps({
                "choices": [{"delta": {"content": "hello"}}],
                "usage": {"prompt_tokens": 12, "completion_tokens": 2, "total_tokens": 14},
            }) + '\n\ndata: [DONE]\n\n')

        client = httpx.AsyncClient(transport=httpx.MockTransport(respond), trust_env=False)
        meta = {}
        with patch("ai_providers.httpx.AsyncClient", return_value=client):
            chunks = [c async for c in ai_providers.call_siliconflow(
                [{"role": "user", "content": "wake up"}], "test-model", meta)]
        self.assertEqual(chunks, ["hello"])
        self.assertIn("system_log_id", meta)
        from system_logs import list_logs
        entry = next(e for e in list_logs() if e["log_id"] == meta["system_log_id"])
        self.assertEqual(entry["status"], "success")
        self.assertEqual(entry["usage"]["total_tokens"], 14)
        self.assertEqual(entry["prompt_messages"][0]["content"], "wake up")

    async def test_persistence_retention_and_chat_details_share_one_entry(self):
        for i in range(35):
            system_logs.save_log({"log_id": str(i), "started_at": i, "status": "success"})
        entries = system_logs.list_logs()
        self.assertEqual(len(entries), 30)
        self.assertEqual([entries[0]["log_id"], entries[-1]["log_id"]], ["34", "5"])
        system_logs.enrich_debug({"usage": {"system_log_id": "34"}, "recall_topic": "wake"})
        app = FastAPI()
        app.include_router(system_logs.router)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            entries = (await client.get("/api/system-logs")).json()
            self.assertEqual(len(entries), 30)
            self.assertEqual(entries[0]["recall_topic"], "wake")
            self.assertEqual((await client.delete("/api/system-logs")).status_code, 200)
            self.assertEqual((await client.get("/api/system-logs")).json(), [])

    async def test_error_chunks_exceptions_and_closed_streams_are_recorded(self):
        @system_logs.logged_ai_call("test")
        async def stream(messages, model, meta=None):
            yield "[测试错误 503] unavailable"
            raise RuntimeError("disconnected")

        with self.assertRaises(RuntimeError):
            async for _ in stream([], "test"):
                pass
        self.assertEqual(system_logs.list_logs()[0]["status"], "error")
        self.assertIn("disconnected", system_logs.list_logs()[0]["error_text"])

        @system_logs.logged_ai_call("test")
        async def cancelled(messages, model):
            yield "partial"
            await asyncio.sleep(60)

        generator = cancelled([], "test")
        self.assertEqual(await anext(generator), "partial")
        self.assertEqual(system_logs.list_logs()[0]["status"], "running")
        await generator.aclose()
        self.assertEqual(system_logs.list_logs()[0]["status"], "cancelled")

    async def test_sentinel_calls_are_logged_without_serializing_credentials(self):
        from memory import _call_sentinel_text

        async def respond(request):
            return httpx.Response(200, json={"choices": [{"message": {"content": "stay"}}]})

        client = httpx.AsyncClient(transport=httpx.MockTransport(respond), trust_env=False)
        with patch("memory.httpx.AsyncClient", return_value=client):
            result = await _call_sentinel_text({
                "use_openai": True, "base_url": "https://example.invalid",
                "api_key": "secret-test-key", "model": "sentinel-test",
            }, "Should I wake up?")
        self.assertEqual(result, "stay")
        entries = system_logs.list_logs()
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["model"], "sentinel-test")
        self.assertNotIn("secret-test-key", json.dumps(entries))


if __name__ == "__main__":
    unittest.main()
