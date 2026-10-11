"""Best-effort supervisor; QQ networking/model calls stay outside the home process."""
import asyncio
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

BASE = Path(__file__).resolve().parent
DIRECTORY = BASE/'data/qq_social'
_last_start = 0.0


def reachable():
    try:
        with socket.create_connection(('127.0.0.1',8788),timeout=.15):
            return True
    except OSError:
        return False


def ensure_started():
    global _last_start
    if not (DIRECTORY/'credentials.json').is_file() or not (DIRECTORY/'management.key').is_file():
        return False
    if reachable():
        return True
    if time.monotonic()-_last_start<20:
        return False
    _last_start=time.monotonic()
    env = {**os.environ,'PYTHONUTF8':'1','PYTHONUNBUFFERED':'1'}
    flags = subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0
    # Bounded startup logs; provider/request bodies are never deliberately logged.
    with (DIRECTORY/'worker.stdout.log').open('ab') as out, (DIRECTORY/'worker.stderr.log').open('ab') as err:
        subprocess.Popen([sys.executable,'-m','qq_social.worker'],cwd=BASE,env=env,
                         stdin=subprocess.DEVNULL,stdout=out,stderr=err,creationflags=flags)
    return True


async def supervise():
    while True:
        try:
            await asyncio.to_thread(ensure_started)
        except Exception:
            pass  # QQ must never block or fail the home server
        await asyncio.sleep(20)
