"""Run with dependencies installed; uses an in-memory OBS double, no live output."""
import base64
import io
import os
import tempfile
import unittest
from unittest.mock import Mock, patch

sandbox = tempfile.TemporaryDirectory()
os.environ.update(ADMIN_PASSWORD='test-password-123456789', INGEST_PASSWORD='test-ingest-password', DATA_DIR=sandbox.name, RELAY_DISABLE_WORKER='1')
from app import server

class APITests(unittest.TestCase):
    def setUp(self):
        self.client = server.app.test_client()
        token = base64.b64encode(b'admin:test-password-123456789').decode()
        self.headers = {'Authorization': 'Basic ' + token, 'X-Relay-Request': '1'}
        server.state.update(connected=True, streaming=False, want_live=False, destination='', preferred='desk', automatic=True)
        self.obs = patch.object(server, 'obs', Mock())
        self.mock = self.obs.start()
        self.mock.call.return_value = {'outputActive': False}
        self.addCleanup(self.obs.stop)

    def test_authentication_required(self):
        for path in ('/', '/api/status', '/api/fallback', '/preview/desk/index.m3u8', '/static/app.js'):
            self.assertEqual(self.client.get(path).status_code, 401)

    def test_request_header_required(self):
        headers = {'Authorization': self.headers['Authorization']}
        self.assertEqual(self.client.post('/api/route', json={'preferred':'desk','automatic':True}, headers=headers).status_code, 403)

    def test_start_sets_destination_and_starts_once(self):
        result = self.client.post('/api/output/start', headers=self.headers, json={'server':'rtmps://example.org/app','key':'secret-key'})
        self.assertEqual(result.status_code, 200)
        self.mock.call.assert_any_call('SetStreamServiceSettings',streamServiceType='rtmp_custom',streamServiceSettings={'server':'rtmps://example.org/app','key':'secret-key','use_auth':False})
        self.assertEqual([c.args[0] for c in self.mock.call.call_args_list].count('StartStream'), 1)
        self.assertNotIn('secret-key', self.client.get('/api/status',headers=self.headers).get_data(as_text=True))

    def test_destination_cannot_change_while_running(self):
        server.state['want_live'] = True
        self.assertEqual(self.client.post('/api/output/start',headers=self.headers,json={'server':'rtmp://example.org/app','key':'key'}).status_code,409)

    def test_invalid_destination(self):
        for url in ('https://example.org', 'rtmp://', 'rtmp://user:pass@example.org/app', 'rtmp://example.org/app?key=bad'):
            self.assertEqual(self.client.post('/api/output/start',headers=self.headers,json={'server':url,'key':'key'}).status_code,400)
        self.assertFalse(any(c.args[0] in ('StartStream', 'SetStreamServiceSettings') for c in self.mock.call.call_args_list))

    def test_stop_clears_persistent_intent(self):
        server.state['want_live'] = True
        self.mock.call.return_value = {'outputActive': True}
        self.assertEqual(self.client.post('/api/output/stop',headers=self.headers,json={}).status_code,200)
        self.assertFalse(server.state['want_live'])
        self.mock.call.assert_any_call('StopStream')

    def test_routing_never_restarts_encoder(self):
        for source in ('desk','irl','fallback'):
            self.assertEqual(self.client.post('/api/route',headers=self.headers,json={'preferred':source,'automatic':True}).status_code,200)
        self.mock.call.assert_not_called()

    def test_bad_graphic_is_rejected(self):
        response = self.client.post('/api/fallback',headers=self.headers,data={'image':(io.BytesIO(b'not an image'),'bad.png')})
        self.assertEqual(response.status_code,400)

    def test_preview_proxy_not_an_open_proxy(self):
        for path in ('/preview/other/index.m3u8','/preview/desk/a..m3u8','/preview/desk/config.yml'):
            self.assertEqual(self.client.get(path,headers=self.headers).status_code,404)

    def test_preview_timeout_is_reported(self):
        with patch.object(server.requests,'get',side_effect=server.requests.Timeout()):
            self.assertEqual(self.client.get('/preview/desk/index.m3u8',headers=self.headers).status_code,503)

if __name__ == '__main__':
    unittest.main()
