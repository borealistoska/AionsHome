"""Home UI proxy; the worker's credential never leaves this server."""
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import FileResponse, JSONResponse

from qq_social_process import BASE, DIRECTORY

router = APIRouter(tags=['qq-social'])


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
