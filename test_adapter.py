import unittest
from adapter import APIError, FortiView, checked_result, query_args, normalize


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.params = {'from': '1735689600000', 'to': '1735743600000', 'source': '192.0.2.10'}

    def test_utc_and_filters(self):
        device, filters, dates = query_args(self.params)
        self.assertEqual(device, 'All_Devices')
        self.assertEqual(filters, 'srcip=192.0.2.10')
        self.assertEqual(dates['start'], '2025-01-01T00:00:00+00:00')

    def test_filter_injection_and_range_rejected(self):
        for updates in [{'source': '192.0.2.20 or true'}, {'domain': 'x" or true'},
                        {'device': '../../sys'}, {'to': '1739689600000'}]:
            with self.assertRaises(ValueError):
                query_args({**self.params, **updates})

    def test_native_time_and_units(self):
        row = normalize({'time': '1735689600', 'traffic_in': '100', 'traffic_out': '40',
                         'session_pass': '8', 'session_block': '2'}, 'website-line')
        self.assertEqual(row['time'], 1735689600000)
        self.assertEqual(row['bandwidth'], 140)
        self.assertEqual(row['sessions'], 10)

    def test_both_api_error_shapes(self):
        for result in [{'error': {'code': -1}}, {'result': {'status': {'code': -1}}},
                       {'result': [{'status': {'code': -1}}]}]:
            with self.assertRaises(APIError):
                checked_result(result)

    def test_pagination_and_cache(self):
        client = FortiView({'url': 'https://example.invalid'})
        calls = []
        def page(view, args, offset, deadline):
            calls.append(offset)
            return {'data': [{'domain': str(i), 'sessions': '1'} for i in range(offset, min(offset+1000, 1002))], 'total-count-all': 1002}
        client.page = page
        first = client.dataset('website-domain', self.params)
        self.assertEqual(len(first['rows']), 1002)
        self.assertEqual(calls, [0, 1000])
        self.assertEqual(client.dataset('website-domain', self.params), first)
        self.assertEqual(calls, [0, 1000])

    def test_partial_response_is_error(self):
        client = FortiView({'url': 'https://example.invalid'})
        client.page = lambda *_: {'data': [{'domain': 'a'}], 'total-count-all': 50}
        with self.assertRaises(APIError):
            client.dataset('website-domain', self.params)

    def test_task_cleanup_after_failure(self):
        client = FortiView({'url': 'https://example.invalid'})
        calls = []
        def rpc(method, param):
            calls.append(method)
            if method == 'add':
                return {'tid': 1}
            if method == 'get':
                raise APIError('query failed')
            return {}
        client.rpc = rpc
        with self.assertRaises(APIError):
            client.page('website-domain', query_args(self.params), 0, float('inf'))
        self.assertEqual(calls, ['add', 'get', 'delete'])

    def test_expired_session_reauthenticates_once(self):
        client = FortiView({'url': 'https://example.invalid', 'session': 'expired'})
        calls = []
        def login():
            calls.append('login')
            client.session = 'fresh'
        def rpc(method, param):
            calls.append(client.session)
            if client.session == 'expired':
                return {'result': [{'status': {'code': -11, 'message': 'No permission for the resource'}}]}
            return {'result': {'status': {'code': 0}, 'data': []}}
        client.login, client.raw_rpc = login, rpc
        self.assertEqual(client.rpc('get', {'url': '/sys/status'})['data'], [])
        self.assertEqual(calls, ['expired', 'login', 'fresh'])

    def test_real_permission_failure_is_not_retried_forever(self):
        client = FortiView({'url': 'https://example.invalid', 'session': 'active'})
        calls = []
        client.login = lambda: calls.append('login')
        client.raw_rpc = lambda *_: {'result': [{'status': {'code': -11, 'message': 'No permission for the resource'}}]}
        with self.assertRaises(APIError):
            client.rpc('get', {'url': '/sys/status'})
        self.assertEqual(calls, ['login'])


if __name__ == '__main__':
    unittest.main()
