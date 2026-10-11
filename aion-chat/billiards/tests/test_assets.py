import re
import unittest

from billiards.routes import STATIC, BilliardsAssets


class AssetTests(unittest.IsolatedAsyncioTestCase):
    async def test_scripts_and_styles_revalidate_even_on_not_modified(self):
        assets = BilliardsAssets(directory=str(STATIC))
        scope = {'type': 'http', 'method': 'GET', 'headers': []}
        response = await assets.get_response('room.js', scope)
        self.assertEqual(response.headers.get('cache-control'), 'no-cache')
        scope['headers'] = [(b'if-none-match', response.headers['etag'].encode())]
        cached = await assets.get_response('room.js', scope)
        self.assertEqual(cached.status_code, 304)
        self.assertEqual(cached.headers.get('cache-control'), 'no-cache')

    async def test_lobby_requests_versioned_scripts_and_styles(self):
        html = (STATIC / 'index.html').read_text(encoding='utf-8')
        urls = re.findall(r'(?:src|href)="(/billiards-assets/[^"?]+\.(?:js|css)[^"]*)"', html)
        self.assertGreaterEqual(len(urls), 13)
        self.assertTrue(all('?v=' in url for url in urls))

