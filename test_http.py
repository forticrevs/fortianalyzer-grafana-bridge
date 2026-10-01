"""Local HTTP contract tests using synthetic data only."""
import json
import threading
import unittest
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from adapter import APIError, make_handler, query_args


class FakeFortiView:
    def devices(self):
        return {'rows': [{'text': 'Example firewall', 'value': 'FG_EXAMPLE'}]}

    def dataset(self, view, params):
        query_args(params)
        if params.get('domain') == 'error.example':
            raise APIError('Synthetic query failure')
        return {'rows': [{'domain': 'example.com', 'sessions': 3, 'bandwidth': 100}],
                'meta': {'view': view, 'complete': True}}


class HTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(FakeFortiView(), 'test-only-token'))
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = 'http://127.0.0.1:' + str(cls.server.server_port)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def get(self, path, token='test-only-token'):
        request = Request(self.base + path, headers={'Authorization': 'Bearer ' + token})
        try:
            response = urlopen(request, timeout=2)
        except HTTPError as error:
            response = error
        with response:
            return response.status, json.load(response)

    def test_health_requires_correct_token(self):
        self.assertEqual(self.get('/health', token='')[0], 401)
        self.assertEqual(self.get('/health', token='incorrect')[0], 401)
        status, body = self.get('/health')
        self.assertEqual(status, 200)
        self.assertEqual(body['status'], 'ok')

    def test_inventory_needs_no_time_range(self):
        status, body = self.get('/api/devices')
        self.assertEqual(status, 200)
        self.assertEqual(body['rows'][0]['value'], 'FG_EXAMPLE')

    def test_data_and_summary_contract(self):
        params = '?from=1735689600000&to=1735776000000'
        status, body = self.get('/api/websites' + params)
        self.assertEqual(status, 200)
        self.assertTrue(body['meta']['complete'])
        self.assertEqual(body['rows'][0]['domain'], 'example.com')
        status, body = self.get('/api/summary' + params)
        self.assertEqual(status, 200)
        self.assertEqual(body['rows'][0]['websites'], 1)
        self.assertEqual(body['rows'][0]['sessions'], 3)

    def test_errors_are_not_empty_successes(self):
        self.assertEqual(self.get('/api/websites')[0], 400)
        status, body = self.get('/api/websites?from=1735689600000&to=1735776000000&domain=error.example')
        self.assertEqual(status, 502)
        self.assertIn('error', body)
        self.assertNotIn('rows', body)
        self.assertEqual(self.get('/missing')[0], 404)


if __name__ == '__main__':
    unittest.main()
