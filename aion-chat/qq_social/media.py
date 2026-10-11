"""QQ image attachments, fetched only while preparing an allowed reply."""
from contextlib import asynccontextmanager
import io
import json
from pathlib import Path
from urllib.parse import urlsplit
import uuid

import httpx
from PIL import Image, ImageOps

DIRECTORY = Path(__file__).resolve().parents[1] / 'data/qq_social/media'
MAX_IMAGES = 4
MAX_BYTES = 8 * 1024 * 1024


def attachment_metadata(raw):
    """Diagnostic shape only: never expose signed URLs or file contents."""
    if not isinstance(raw,list):
        return []
    result=[]
    for item in raw[:4]:
        if not isinstance(item,dict):
            continue
        url=str(item.get('url') or '')
        try:
            host=urlsplit(url if '://' in url else '//'+url.lstrip('/')).hostname
        except ValueError:
            host='invalid'
        result.append({'content_type':str(item.get('content_type') or '')[:80],
                       'host':str(host or '')[:180],
                       'fields':sorted(str(k)[:40] for k in item)[:15]})
    return result


def image_attachments(raw):
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return []
    if not isinstance(raw, list):
        return []
    result = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        mime = str(item.get('content_type') or '').lower()
        if not mime.startswith('image/'):
            continue
        url = str(item.get('url') or '')
        if url.startswith('//'):
            url = 'https:' + url
        elif '://' not in url:
            url = 'https://' + url
        try:
            parsed = urlsplit(url)
            host = parsed.hostname or ''
            trusted = (host in ('multimedia.nt.qq.com','multimedia.nt.qq.com.cn') or host.endswith('.qpic.cn') or host.endswith('.gtimg.cn'))
            if trusted and parsed.scheme == 'http' and parsed.port in (None,80) and not parsed.username and not parsed.password:
                url = parsed._replace(scheme='https',netloc=host).geturl()
                parsed = urlsplit(url)
            if parsed.scheme != 'https' or not trusted or parsed.username or parsed.password or parsed.port not in (None, 443):
                continue
        except ValueError:
            continue
        result.append({'url':url, 'content_type':mime})
        if len(result) >= MAX_IMAGES:
            break
    return result


def recent_images(event, history):
    """Prefer this message, otherwise the same speaker's latest image message."""
    current = image_attachments(event.get('attachments', []))
    if current:
        return current
    for row in reversed(history):
        if row['direction'] != 'in' or row['member_id'] != event['member_id']:
            continue
        if not 0 <= event['created_at'] - row['created_at'] <= 600:
            continue
        refs = image_attachments(row.get('attachments', []))
        if refs:
            return refs
    return []


@asynccontextmanager
async def prepare_images(refs, *, directory=DIRECTORY, client=None):
    owned = client is None
    client = client or httpx.AsyncClient(timeout=15, trust_env=False, follow_redirects=False)
    paths, attachments, notes = [], [], []
    try:
        for index, ref in enumerate(image_attachments(refs), 1):
            try:
                async with client.stream('GET', ref['url'], follow_redirects=False) as response:
                    response.raise_for_status()
                    if int(response.headers.get('content-length', 0)) > MAX_BYTES:
                        raise ValueError('Image too large')
                    data = bytearray()
                    async for part in response.aiter_bytes():
                        data.extend(part)
                        if len(data) > MAX_BYTES:
                            raise ValueError('Image too large')
                with Image.open(io.BytesIO(data)) as source:
                    if source.width * source.height > 20_000_000:
                        raise ValueError('Image dimensions too large')
                    picture = ImageOps.exif_transpose(source)
                    picture.thumbnail((1600, 1600))
                    picture = picture.convert('RGB')
                    directory.mkdir(parents=True, exist_ok=True)
                    path = directory / (uuid.uuid4().hex + '.jpg')
                    paths.append(path)
                    picture.save(path, format='JPEG', quality=88)
                attachments.append('/qq-media/' + path.name)
            except (httpx.HTTPError, OSError, ValueError, Image.DecompressionBombError):
                notes.append(f'第{index}张图片读取失败或超过大小限制，不能猜测图片内容。')
        yield attachments, notes
    finally:
        for path in paths:
            path.unlink(missing_ok=True)
        if owned:
            await client.aclose()
