"""Offline checks: never execute the real shopping client in these tests."""
import json
import subprocess
import unittest
from unittest.mock import AsyncMock, patch

import httpx

import taobao_native_mcp as bridge


class NativeSearchTests(unittest.IsolatedAsyncioTestCase):
    async def test_authorization_failure_survives_unavailable_cli_fallback(self):
        def unauthorized(request):
            return httpx.Response(401, json={
                "jsonrpc": "2.0", "id": json.loads(request.content)["id"],
                "error": {"code": -32000,
                          "message": "Unauthorized: missing, invalid or expired access token",
                          "data": None},
            })

        client = httpx.AsyncClient(transport=httpx.MockTransport(unauthorized))
        response = subprocess.CompletedProcess([], 1, '', json.dumps({
            "error": "应用未运行，请先执行 taobao-native start",
        }, ensure_ascii=False))
        with patch.object(bridge.httpx, "AsyncClient", return_value=client), \
             patch.object(bridge.subprocess, "run", return_value=response):
            with self.assertRaises(RuntimeError) as caught:
                await bridge.search_products("桌面手机支架")
        message = str(caught.exception)
        self.assertIn("401", message)
        self.assertIn("授权", message)
        self.assertIn("应用未运行", message)

    async def test_search_uses_a2a_without_touching_native_cli(self):
        products = [{"itemId": "12345", "productUrl": "https://item.taobao.com/item.htm?id=12345"}]
        with patch.object(bridge, "_search_a2a", new=AsyncMock(
                return_value={"products": products, "source": "a2a"})), \
             patch.object(bridge.subprocess, "run", side_effect=AssertionError("CLI fallback must not run")):
            result = await bridge.search_products("桌面手机支架")
        self.assertEqual(result, {"products": products, "source": "a2a"})

    def test_a2a_response_is_mapped_to_existing_product_shape(self):
        payload = {"result": {"task": {"artifacts": [{"parts": [{"data": {"data": {
            "products": [{
                "itemId": "12345", "title": "桌面<span class=H>支架</span>",
                "auctionURL": "//item.taobao.com/item.htm?id=12345",
                "picPath": "http://g.search.alicdn.com/example.jpg",
            }]
        }}}]}]}}}
        self.assertEqual(bridge._a2a_products(payload), [{
            "itemId": "12345", "title": "桌面支架",
            "auctionURL": "//item.taobao.com/item.htm?id=12345",
            "picPath": "http://g.search.alicdn.com/example.jpg",
            "productUrl": "https://item.taobao.com/item.htm?id=12345",
            "image": "https://g.search.alicdn.com/example.jpg",
        }])

    async def test_search_uses_known_working_node_command_and_stdin(self):
        response = subprocess.CompletedProcess([], 0, '{"result":{"products":[]}}', '')
        with patch.object(bridge, "_search_a2a", new=AsyncMock(side_effect=RuntimeError("offline"))), \
             patch.object(bridge.subprocess, "run", return_value=response) as run, \
             patch.object(bridge.asyncio, "create_subprocess_exec", new=AsyncMock(
                 side_effect=AssertionError("Legacy Electron launcher must not run"))):
            result = await bridge.search_products("桌面手机支架")
        self.assertEqual(result, {"products": []})
        run.assert_called_once_with(
            ["C:/Program Files/nodejs/node.exe", "H:/taobao/bin/cli-rpc.js", "--stdin"],
            input=json.dumps({"tool": "search_products", "arguments": {
                "keyword": "桌面手机支架", "type": "all", "sourceApp": "AionsHome",
            }}, ensure_ascii=False),
            encoding="utf-8", capture_output=True, timeout=120,
        )

    async def test_login_failure_is_reported_without_retry(self):
        response = subprocess.CompletedProcess([], 0, json.dumps({
            "error": "未登录，已打开登录页面，请先登录淘宝账号",
        }), '')
        with patch.object(bridge, "_search_a2a", new=AsyncMock(side_effect=RuntimeError("offline"))), \
             patch.object(bridge.subprocess, "run", return_value=response) as run, \
             patch.object(bridge.asyncio, "create_subprocess_exec", new=AsyncMock(
                 side_effect=AssertionError("Legacy Electron launcher must not run"))):
            with self.assertRaisesRegex(RuntimeError, "未登录"):
                await bridge.search_products("桌面手机支架")
        self.assertEqual(run.call_count, 1)

    async def test_native_stderr_error_is_preserved(self):
        response = subprocess.CompletedProcess([], 1, '', json.dumps({
            "error": "应用未运行，请先执行 taobao-native start",
        }, ensure_ascii=False))
        with patch.object(bridge, "_search_a2a", new=AsyncMock(side_effect=RuntimeError("offline"))), \
             patch.object(bridge.subprocess, "run", return_value=response) as run:
            with self.assertRaisesRegex(RuntimeError, "应用未运行.*若窗口已打开，请重启"):
                await bridge.search_products("桌面手机支架")
        self.assertEqual(run.call_count, 1)

    async def test_plain_stderr_failure_is_reported(self):
        response = subprocess.CompletedProcess([], 1, '', 'Error: connect EACCES')
        with patch.object(bridge, "_search_a2a", new=AsyncMock(side_effect=RuntimeError("offline"))), \
             patch.object(bridge.subprocess, "run", return_value=response):
            with self.assertRaisesRegex(RuntimeError, "connect EACCES"):
                await bridge.search_products("桌面手机支架")
