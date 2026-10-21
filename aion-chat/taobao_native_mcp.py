"""Read-only stdio MCP bridge to Taobao's official product search services.

This is an AionsHome bridge, not Taobao's built-in HTTP MCP server.
Only search_products is exposed. No history, chat, cart or payment tools.
"""
import asyncio
from html import unescape
import json
from pathlib import Path
import re
import subprocess
import uuid

import httpx
from mcp.server.fastmcp import FastMCP

server = FastMCP("AionsHome Taobao native search bridge")
A2A_SEARCH_URL = "https://pc-taoclaw.taobao.com/a2a/itemSearch"


def native_command():
    # Pin the exact local runtime/script used by the successful direct test.
    # Do not silently fall back to launching the Taobao Electron executable.
    node = "C:/Program Files/nodejs/node.exe"
    script = "H:/taobao/bin/cli-rpc.js"
    if not Path(node).is_file() or not Path(script).is_file():
        raise RuntimeError("已验证的 Node 或淘宝脚本路径不存在，请检查本机安装路径")
    return [node, script, "--stdin"]


def _a2a_products(payload: dict) -> list[dict]:
    try:
        parts = payload["result"]["task"]["artifacts"][0]["parts"]
        products = next(part["data"]["data"]["products"] for part in parts
                        if isinstance(part.get("data"), dict))
    except (KeyError, IndexError, StopIteration, TypeError) as exc:
        raise RuntimeError("淘宝 A2A 搜索未返回商品列表") from exc
    if not isinstance(products, list):
        raise RuntimeError("淘宝 A2A 搜索未返回商品列表")
    cleaned = []
    for raw in products:
        if not isinstance(raw, dict):
            continue
        product_url = str(raw.get("auctionURL") or raw.get("productUrl") or "")
        if product_url.startswith("//"):
            product_url = "https:" + product_url
        image = str(raw.get("picPath") or raw.get("image") or "")
        if image.startswith("//"):
            image = "https:" + image
        if image.startswith("http://"):
            image = "https://" + image[7:]
        cleaned.append({
            **raw,
            "title": re.sub(r"<[^>]*>", "", unescape(str(raw.get("title") or ""))),
            "productUrl": product_url,
            "image": image,
        })
    return cleaned


async def _search_a2a(keyword: str) -> dict:
    request_id = uuid.uuid4().hex
    request = {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": "SendMessage",
        "params": {
            "message": {
                "messageId": uuid.uuid4().hex,
                "role": "ROLE_USER",
                "parts": [{"data": {
                    "skillId": "item-search", "query": keyword, "limit": 20,
                }}],
            },
            "configuration": {"returnImmediately": False},
        },
    }
    async with httpx.AsyncClient(trust_env=False, timeout=30) as client:
        response = await client.post(A2A_SEARCH_URL, json=request,
                                     headers={"Accept": "application/json"})
        if response.status_code in (401, 403):
            raise RuntimeError(
                f"淘宝官方搜索授权失败（HTTP {response.status_code}），"
                "请检查 A2A 访问令牌及调用权限"
            )
        response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict) or payload.get("id") != request_id:
        raise RuntimeError("淘宝 A2A 搜索返回了无效响应")
    if payload.get("error"):
        error = payload["error"]
        message = error.get("message") if isinstance(error, dict) else error
        raise RuntimeError(f"淘宝 A2A 搜索失败：{message}")
    return {"products": _a2a_products(payload), "source": "a2a"}


async def _search_cli(keyword: str) -> dict:
    request = {"tool": "search_products", "arguments": {
        "keyword": keyword, "type": "all", "sourceApp": "AionsHome",
    }}
    # Match the direct subprocess.run call, off-thread to keep MCP responsive.
    process = await asyncio.to_thread(
        subprocess.run, native_command(),
        input=json.dumps(request, ensure_ascii=False),
        encoding="utf-8", capture_output=True, timeout=120,
    )
    # The official CLI writes transport failures to stderr, unlike tool results.
    # Preserve that error instead of reporting empty stdout as invalid products.
    if process.returncode:
        for output in (process.stdout, process.stderr):
            try:
                failure = json.loads(output)
            except (UnicodeError, ValueError):
                continue
            if isinstance(failure, dict) and failure.get("error"):
                message = str(failure["error"])[:1000]
                if "应用未运行" in message:
                    message = ("淘宝本地搜索接口不可用：" + message
                               + "；若窗口已打开，请重启淘宝桌面版并确认登录及 AI 代理状态")
                raise RuntimeError(message)
        detail = process.stderr.strip()[:500]
        raise RuntimeError(f"淘宝原生搜索失败（退出码 {process.returncode}）" + (f"：{detail}" if detail else ""))
    try:
        payload = json.loads(process.stdout)
    except (UnicodeError, ValueError) as exc:
        raise RuntimeError("淘宝未返回有效商品数据，请确认已启动、登录并启用 AI 代理") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("淘宝未返回有效商品数据：响应不是 JSON 对象")
    if payload.get("error"):
        raise RuntimeError(str(payload["error"]))
    result = payload.get("result")
    if not isinstance(result, dict) or not isinstance(result.get("products"), list):
        raise RuntimeError("淘宝搜索未返回商品列表，可能需要登录或完成验证")
    return result


@server.tool()
async def search_products(keyword: str, type: str = "all", sourceApp: str = "AionsHome") -> dict:
    """Search real products through Taobao 3 A2A, with the old native CLI as fallback."""
    keyword = keyword.strip()
    if not keyword or len(keyword) > 120:
        raise ValueError("搜索词应为 1 到 120 字")
    try:
        return await _search_a2a(keyword)
    except (httpx.HTTPError, json.JSONDecodeError, RuntimeError) as a2a_error:
        # Taobao 2.x has no A2A service but still supports the verified CLI.
        # This path uses system Node and never launches Electron in Node mode.
        try:
            return await _search_cli(keyword)
        except (RuntimeError, OSError, subprocess.SubprocessError) as cli_error:
            raise RuntimeError(
                f"A2A 搜索失败：{a2a_error}；本地兼容搜索也失败：{cli_error}"
            ) from cli_error


if __name__ == "__main__":
    server.run(transport="stdio")
