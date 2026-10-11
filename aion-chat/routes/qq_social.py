"""Home UI proxy; the worker's credential never leaves this server."""
from urllib.parse import urlsplit
import asyncio
import json
import os
import secrets
import tempfile

import httpx
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import FileResponse, JSONResponse

from qq_social_process import BASE, DIRECTORY

router = APIRouter(tags=['qq-social'])
_credential_lock = asyncio.Lock()


def read_credentials():
    path = DIRECTORY/'credentials.json'
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding='utf8'))


async def connect_saved_credentials():
    from qq_social_process import ensure_started
    try:
        await asyncio.to_thread(ensure_started)
        async with httpx.AsyncClient(timeout=5, trust_env=False) as client:
            response = await client.post('http://127.0.0.1:8788/reload-credentials', headers={
                'Authorization':'Bearer '+(DIRECTORY/'management.key').read_text(encoding='utf8').strip()})
        if response.status_code == 404:
            return 'restart_required'  # A worker from before page-based setup is still running.
        return 'connecting' if response.is_success else 'retry_required'
    except httpx.HTTPError:
        return 'connecting'  # The supervisor may still be starting the worker.
    except OSError:
        return 'retry_required'


@router.get('/api/qq-social/credentials')
async def credential_status():
    from qq_social.context import home_config
    names = home_config()[3]
    try:
        saved = read_credentials()
    except (OSError, ValueError):
        raise HTTPException(500, '本地 QQ 配置读取失败，请检查数据文件')
    return JSONResponse({'actors':{
        actor:{'name':names[actor], 'app_id':saved.get(actor,{}).get('app_id',''),
               'configured':bool(saved.get(actor,{}).get('secret'))}
        for actor in ('aion','connor')
    }}, headers={'Cache-Control':'no-store'})


@router.patch('/api/qq-social/credentials')
async def save_credentials(request: Request):
    origin = request.headers.get('origin')
    if origin and urlsplit(origin).netloc != request.headers.get('host'):
        raise HTTPException(403, 'Invalid origin')
    try:
        body = await request.json()
    except ValueError:
        raise HTTPException(400, '配置格式无效')
    if not isinstance(body,dict) or body.get('actor') not in ('aion','connor'):
        raise HTTPException(400, '请选择要配置的 AI')
    actor = body['actor']
    app_id, secret = body.get('app_id',''), body.get('secret','')
    if not isinstance(app_id,str) or not isinstance(secret,str):
        raise HTTPException(400, 'AppID 和 AppSecret 必须是文本')
    app_id, secret = app_id.strip(), secret.strip()
    if not app_id or len(app_id)>64 or len(secret)>2048:
        raise HTTPException(400, '请填写有效的 AppID 和 AppSecret')
    async with _credential_lock:
        try:
            saved = read_credentials()
        except (OSError, ValueError):
            raise HTTPException(500, '原 QQ 配置读取失败，未覆盖已有文件')
        old = saved.get(actor,{})
        if not secret and app_id == old.get('app_id'):
            secret = old.get('secret','')
        if not secret:
            raise HTTPException(400, '首次配置或更换 AppID 时，请同时填写 AppSecret')
        if any(role!=actor and bot.get('app_id')==app_id for role,bot in saved.items()):
            raise HTTPException(400, '两位 AI 请使用各自的 QQ 机器人，不能共用同一个 AppID')
        from qq_social.transport import BotTransport
        bot = BotTransport({'app_id':app_id, 'secret':secret}, None)
        try:
            await bot.authorize()
        except Exception:
            raise HTTPException(400, 'QQ 凭据验证未通过，请检查 AppID、AppSecret 和网络；原配置未改动')
        finally:
            await bot.close()
        from qq_social.store import Store
        saved[actor] = {'app_id':app_id,'secret':secret}
        DIRECTORY.mkdir(parents=True,exist_ok=True)
        if not (DIRECTORY/'social.db').exists():
            # First connection does not turn on replies; the user opens the master switch.
            Store(DIRECTORY/'social.db').configure({'bind_first_group':True})
        key = DIRECTORY/'management.key'
        if not key.exists():
            key.write_text(secrets.token_urlsafe(32),encoding='utf8')
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode='w',encoding='utf8',dir=DIRECTORY,
                                             prefix='credentials-',suffix='.tmp',delete=False) as file:
                temporary = file.name
                json.dump(saved,file)
            os.replace(temporary,DIRECTORY/'credentials.json')
        finally:
            if temporary and os.path.exists(temporary):
                os.unlink(temporary)
        connection = await connect_saved_credentials()
    return JSONResponse({'ok':True,'connection':connection},headers={'Cache-Control':'no-store'})


@router.get('/qq-social')
async def page():
    return FileResponse(BASE/'static/qq-social.html')


@router.get('/api/external-memories')
async def outside_memories():
    from external_memories import recent_memories
    return {actor:await recent_memories(actor, limit=10, days=3650) for actor in ('aion','connor')}


@router.api_route('/api/qq-social/{path:path}',methods=['GET','PATCH','DELETE'])
async def proxy(path:str,request:Request):
    import re
    if not re.fullmatch(r'(?:status|settings|groups/\d+(?:/members/[\w-]+)?|memories/\d+)',path):
        raise HTTPException(404,'Not found')
    if request.method!='GET':
        origin=request.headers.get('origin')
        if origin and urlsplit(origin).netloc != request.headers.get('host'):
            raise HTTPException(403,'Invalid origin')
    key=DIRECTORY/'management.key'
    if not key.exists():
        return JSONResponse({'detail':'QQ尚未配置'},status_code=503)
    try:
        async with httpx.AsyncClient(timeout=15,trust_env=False) as client:
            response=await client.request(request.method,'http://127.0.0.1:8788/'+path,
                headers={'Authorization':'Bearer '+key.read_text(encoding='utf8').strip(),'Content-Type':'application/json'},
                content=await request.body())
        return JSONResponse(response.json(),status_code=response.status_code)
    except (httpx.HTTPError,ValueError,OSError):
        return JSONResponse({'detail':'QQ独立服务暂不可用，小家聊天不受影响'},status_code=503)
