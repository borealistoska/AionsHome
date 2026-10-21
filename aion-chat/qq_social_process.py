"""Best-effort supervisor; QQ networking/model calls stay outside the home process."""
import asyncio
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time

BASE = Path(__file__).resolve().parent
DIRECTORY = BASE/'data/qq_social'
# Keep the supervisor, worker and home proxy on the same loopback endpoint.
PORT = 18788
WORKER_URL = f'http://127.0.0.1:{PORT}'
_last_start = 0.0
_start_attempts = 0
_start_lock = threading.Lock()
_wake = None


def reset_retries():
    """Only an explicit UI connection request releases the saved pause."""
    global _last_start, _start_attempts
    with _start_lock:
        for name in ('connection.paused', 'aion.paused', 'connor.paused'):
            (DIRECTORY/name).unlink(missing_ok=True)
        _last_start = 0.0
        _start_attempts = 0
    if _wake is not None:
        _wake.set()


def reachable():
    try:
        with socket.create_connection(('127.0.0.1',PORT),timeout=.15):
            return True
    except OSError:
        return False


def ensure_started():
    with _start_lock:
        return _ensure_started()


def _ensure_started():
    global _last_start, _start_attempts
    if (DIRECTORY/'connection.paused').exists():
        return False
    if not (DIRECTORY/'credentials.json').is_file() or not (DIRECTORY/'management.key').is_file():
        return False
    if reachable():
        _start_attempts = 0
        return True
    if _start_attempts >= 3:
        (DIRECTORY/'connection.paused').touch()
        return False
    if time.monotonic()-_last_start<20:
        return False
    _last_start=time.monotonic()
    _start_attempts += 1
    env = {**os.environ,'PYTHONUTF8':'1','PYTHONUNBUFFERED':'1'}
    flags = subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0
    # Bounded startup logs; provider/request bodies are never deliberately logged.
    with (DIRECTORY/'worker.stdout.log').open('ab') as out, (DIRECTORY/'worker.stderr.log').open('ab') as err:
        subprocess.Popen([sys.executable,'-m','qq_social.worker'],cwd=BASE,env=env,
                         stdin=subprocess.DEVNULL,stdout=out,stderr=err,creationflags=flags)
    return True


async def supervise():
    global _wake
    _wake = asyncio.Event()
    while True:
        _wake.clear()
        try:
            await asyncio.to_thread(ensure_started)
        except Exception:
            pass  # QQ must never block or fail the home server
        if (DIRECTORY/'connection.paused').exists() or not (DIRECTORY/'credentials.json').exists():
            await _wake.wait()  # No timers, socket probes, or process launches while stopped.
        else:
            try:
                await asyncio.wait_for(_wake.wait(), timeout=20)
            except asyncio.TimeoutError:
                pass
