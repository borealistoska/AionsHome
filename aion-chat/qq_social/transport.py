"""Official QQ token, REST and resumable WebSocket transport."""
from __future__ import annotations

import asyncio
import contextlib
import json
import time

import httpx
import websockets

API = 'https://api.bot.qq.com'


class QQAPIError(RuntimeError):
    pass


class BotTransport:
    def __init__(self, credentials, on_event):
        self.app_id = str(credentials['app_id'])
        self.secret = credentials['secret']
        self.on_event = on_event
        self.client = httpx.AsyncClient(timeout=20, trust_env=False)
        self.token = ''
        self.expires = 0
        self.user_id = ''
        self.session_id = ''
        self.sequence = None
        self.status = dict(connected=False, state='准备连接', error='', username='', last_ready=0)

    async def authorize(self):
        if self.token and time.time() < self.expires - 45:
            return
        response = await self.client.post(API+'/app/getAppAccessToken', json={'appId':self.app_id,'clientSecret':self.secret})
        data = response.json()
        if response.status_code != 200 or not data.get('access_token'):
            raise QQAPIError(f"QQ鉴权失败 HTTP {response.status_code} / {data.get('code','unknown')}")
        self.token = data['access_token']
        self.expires = time.time() + int(data.get('expires_in',7200))

    async def request(self, method, path, **kwargs):
        await self.authorize()
        response = await self.client.request(method, API+path, headers={'Authorization':'QQBot '+self.token,'X-Union-Appid':self.app_id}, **kwargs)
        data = response.json()
        if response.status_code >= 400 or (data.get('code') not in (None,0)):
            if response.status_code == 401:
                self.expires = 0
            raise QQAPIError(f"QQ接口失败 HTTP {response.status_code} / {data.get('code','unknown')}")
        return data

    async def send(self, group_id, content, message_id, *, reference_id=''):
        from urllib.parse import quote
        payload = {'msg_type':0,'content':content,'msg_id':message_id,'msg_seq':1}
        if reference_id:
            payload['message_reference'] = {'message_id':reference_id,'ignore_get_message_error':True}
        return await self.request('POST','/v2/groups/'+quote(group_id,safe='')+'/messages',json=payload)

    async def run(self):
        while True:
            try:
                self.status.update(state='连接中',error='')
                me = await self.request('GET','/users/@me')
                self.user_id = str(me.get('id',''))
                self.status['username'] = str(me.get('username',''))
                gateway = await self.request('GET','/gateway')
                await self.listen(gateway['url'])
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.status.update(connected=False,state='等待重连',error=str(exc) if isinstance(exc,QQAPIError) else type(exc).__name__)
            finally:
                self.status['connected'] = False
            await asyncio.sleep(20)

    async def listen(self, url):
        # Only QQ-provided TLS gateways; never downgrade to cleartext.
        from urllib.parse import urlparse
        host = urlparse(url).hostname or ''
        if not url.startswith('wss://') or not (host.endswith('.qq.com') or host.endswith('.tencent.com')):
            raise QQAPIError('QQ网关地址无效')
        async with websockets.connect(url, open_timeout=20, ping_interval=None, max_size=2**20, proxy=None) as ws:
            hello = json.loads(await asyncio.wait_for(ws.recv(),20))
            if hello.get('op') != 10:
                raise QQAPIError('QQ网关未返回Hello')
            interval = max(5,hello['d']['heartbeat_interval']/1000)
            if self.session_id:
                payload = {'op':6,'d':{'token':'QQBot '+self.token,'session_id':self.session_id,'seq':self.sequence}}
            else:
                payload = {'op':2,'d':{'token':'QQBot '+self.token,'intents':1<<25,'shard':[0,1],'properties':{'$os':'windows','$browser':'AionsHome','$device':'QQ social'}}}
            await ws.send(json.dumps(payload))
            acknowledged = True

            async def heartbeat():
                nonlocal acknowledged
                while True:
                    await asyncio.sleep(interval)
                    if not acknowledged:
                        await ws.close()
                        return
                    acknowledged = False
                    await ws.send(json.dumps({'op':1,'d':self.sequence}))

            heart = asyncio.create_task(heartbeat())
            try:
                async for raw in ws:
                    data = json.loads(raw)
                    op = data.get('op')
                    if data.get('s') is not None:
                        self.sequence = data['s']
                    if op == 11:
                        acknowledged = True
                    elif op == 1:
                        await ws.send(json.dumps({'op':1,'d':self.sequence}))
                    elif op == 9:
                        self.session_id = ''
                        self.sequence = None
                        raise QQAPIError('QQ登录会话失效，将重新鉴权')
                    elif op == 7:
                        return
                    elif op == 0:
                        kind, event = data.get('t'), data.get('d') or {}
                        if kind in ('READY','RESUMED'):
                            self.session_id = event.get('session_id') or self.session_id
                            self.user_id = str((event.get('user') or {}).get('id') or self.user_id)
                            self.status.update(connected=True,state='在线',error='',last_ready=time.time())
                        else:
                            await self.on_event(kind,event)
            finally:
                heart.cancel()
                with contextlib.suppress(asyncio.CancelledError,Exception):
                    await heart

    async def close(self):
        await self.client.aclose()
