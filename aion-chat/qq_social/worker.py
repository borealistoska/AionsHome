"""Loopback-only management API and independent QQ process."""
from __future__ import annotations

import asyncio
import hmac
import json
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, HTTPException

from .service import Service
from .store import Store
from .transport import BotTransport

DIRECTORY = Path(__file__).resolve().parents[1] / 'data' / 'qq_social'
PORT = 8788


def create_app(directory=DIRECTORY):
    directory = Path(directory)
    store = Store(directory/'social.db')
    transports = {}
    service = Service(store, transports)
    token = (directory/'management.key').read_text(encoding='utf8').strip()

    @asynccontextmanager
    async def lifespan(app):
        credentials = json.loads((directory/'credentials.json').read_text(encoding='utf8'))
        jobs = []
        for actor in ('aion','connor'):
            if actor not in credentials:
                continue
            async def event(kind, data, role=actor):
                await service.ingest(role,kind,data)
            bot = transports[actor] = BotTransport(credentials[actor],event)
            jobs.append(asyncio.create_task(bot.run()))
        jobs.append(asyncio.create_task(service.digest_groups()))
        (directory/'worker.pid').write_text(str(os.getpid()),encoding='ascii')
        try:
            yield
        finally:
            for job in jobs:
                job.cancel()
            await service.close()
            await asyncio.gather(*jobs,return_exceptions=True)
            await asyncio.gather(*(bot.close() for bot in transports.values()))

    app = FastAPI(lifespan=lifespan,docs_url=None,redoc_url=None,openapi_url=None)

    @app.middleware('http')
    async def auth(request, call_next):
        from fastapi.responses import JSONResponse
        if not token or not hmac.compare_digest(request.headers.get('authorization',''),'Bearer '+token):
            return JSONResponse({'detail':'Unauthorized'},status_code=401)
        return await call_next(request)

    @app.get('/status')
    async def status():
        from .context import home_config
        cfg,models,_,names = home_config()
        actors = {}
        for actor in ('aion','connor'):
            bot = transports.get(actor)
            actors[actor] = dict(name=names[actor],model=cfg.get(actor+'_model',''),
                **(bot.status if bot else {'connected':False,'state':'未配置'}),
                last_event=service.last_event.get(actor),last_send=service.last_send.get(actor),
                reply_error=service.last_error.get(actor,''),context=service.diagnostics.get(actor,{}))
        return {'settings':store.settings(),'actors':actors,'groups':store.groups(),'pending':len(service.tasks),'pid':os.getpid()}

    @app.patch('/settings')
    async def settings(request: Request):
        try:
            return service.configure(await request.json())
        except (ValueError,TypeError):
            raise HTTPException(400,'设置值无效')

    @app.patch('/groups/{row_id}')
    async def update_group(row_id:int,request:Request):
        try:
            store.update_group(row_id,await request.json())
            service.cancel_pending()
        except (ValueError,TypeError):
            raise HTTPException(400,'群设置无效')
        return {'ok':True}

    def find_group(row_id):
        for g in store.groups():
            if g['id']==row_id:
                return g
        raise HTTPException(404,'群不存在')

    @app.get('/groups/{row_id}')
    async def group_detail(row_id:int):
        g = find_group(row_id)
        key = (g['actor'],g['group_id'])
        return {'group':g,'messages':store.history(*key,100),'members':store.members(*key),'memories':store.memories(*key)}

    @app.patch('/groups/{row_id}/members/{member_id}')
    async def member(row_id:int,member_id:str,request:Request):
        g = find_group(row_id)
        body = await request.json()
        store.set_member(g['actor'],g['group_id'],member_id,body.get('alias',''),body.get('is_owner') is True)
        return {'ok':True}

    @app.delete('/memories/{memory_id}')
    async def delete_memory(memory_id:int):
        store.delete_memory(memory_id)
        return {'ok':True}

    return app


if __name__ == '__main__':
    import sys
    import uvicorn
    sys.stdout.reconfigure(encoding='utf8',errors='replace')
    sys.stderr.reconfigure(encoding='utf8',errors='replace')
    uvicorn.run(create_app(),host='127.0.0.1',port=PORT,access_log=False,log_level='warning')
