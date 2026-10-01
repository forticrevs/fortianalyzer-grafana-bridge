#!/usr/bin/env python3
"""Read-only FortiView bridge for Grafana Infinity. Python 3.10+, standard library."""
import argparse
import hmac
import ipaddress
import json
import logging
import os
import re
import ssl
import threading
import time
from collections import OrderedDict
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request, urlopen


class APIError(Exception):
    pass


def checked_result(response):
    if response.get('error'):
        raise APIError(str(response['error']))
    result = response['result']
    if isinstance(result, list):
        result = result[0]
    if result.get('status', {}).get('code', 0) != 0:
        raise APIError(str(result['status']))
    return result


def query_args(params):
    start, end = int(params['from']), int(params['to'])
    if not 0 < start < end or end - start > 31 * 86400000:
        raise ValueError('Select a positive time range of at most 31 days.')
    device = params.get('device', 'All_Devices')
    if device in ('All', '$__all', ''):
        device = 'All_Devices'
    if not re.fullmatch(r'[A-Za-z0-9_.-]+(?:\[[A-Za-z0-9_.-]+\])?', device):
        raise ValueError('Invalid FortiGate/VDOM identifier.')
    source = params.get('source', '')
    domain = params.get('domain', '')
    filters = []
    if source not in ('', 'All', '$__all'):
        source = str(ipaddress.ip_address(source))
        filters.append('srcip=' + source)
    if domain not in ('', 'All', '$__all'):
        if len(domain) > 253 or not re.fullmatch(r'[A-Za-z0-9_.:-]+', domain):
            raise ValueError('Website must be an exact domain or IP address.')
        filters.append('domain=' + domain)
    dates = {k: datetime.fromtimestamp(v / 1000, timezone.utc).isoformat(timespec='seconds')
             for k, v in [('start', start), ('end', end)]}
    return device, ' and '.join(filters), dates


class FortiView:
    def __init__(self, config):
        self.config = config
        self.session = config.get('session', '')
        self.lock = threading.RLock()
        self.cache = OrderedDict()
        self.context = (ssl.create_default_context(cafile=config.get('ca_file'))
                        if config.get('verify_tls', True) else ssl._create_unverified_context())
        self.adom = config.get('adom', 'root')
        if not re.fullmatch(r'[A-Za-z0-9_.-]+', self.adom):
            raise ValueError('Invalid ADOM name')

    def raw_rpc(self, method, param):
        payload = {'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': [param]}
        if self.session:
            payload['session'] = self.session
        req = Request(self.config['url'].rstrip('/') + '/jsonrpc',
                      data=json.dumps(payload).encode(), headers={'Content-Type': 'application/json'})
        with urlopen(req, context=self.context, timeout=20) as response:
            return json.load(response)

    def login(self):
        password = os.environ.get('FAZ_PASSWORD', self.config.get('password'))
        if not password:
            raise APIError('FAZ_PASSWORD or a password in the private config is required.')
        response = self.raw_rpc('exec', {'url': '/sys/login/user', 'data': {
            'user': self.config.get('username', 'admin'), 'passwd': password}})
        checked_result(response)
        self.session = response['session']

    def rpc(self, method, param):
        if not self.session:
            self.login()
        response = self.raw_rpc(method, param)
        try:
            return checked_result(response)
        except APIError as error:
            # Some FAZ releases report expired sessions as -11 "No permission".
            # Reauthenticate once; a genuine permission failure still propagates.
            result = response.get('result', {})
            if isinstance(result, list):
                result = result[0] if result else {}
            expired = result.get('status', {}).get('code') == -11
            if expired or any(x in str(error).lower() for x in ('session', 'not authenticated')):
                self.session = ''
                self.login()
                return checked_result(self.raw_rpc(method, param))
            raise

    def page(self, view, args, offset, deadline):
        device, filters, dates = args
        base = f'/fortiview/adom/{self.adom}/{view}/run'
        param = {'url': base, 'apiver': 3, 'device': [{'devid': device}],
                 'filter': filters, 'time-range': dates, 'count-total': True,
                 'limit': 1000, 'offset': offset,
                 'sort-by': [{'field': 'time' if view == 'website-line' else 'sessions',
                              'order': 'asc' if view == 'website-line' else 'desc'}]}
        task = self.rpc('add', param)
        fetch = {'url': base + '/' + str(task['tid']), 'apiver': 3}
        try:
            while time.monotonic() < deadline:
                result = self.rpc('get', fetch)
                if result.get('percentage') == 100:
                    return result
                time.sleep(0.4)
            raise TimeoutError('FortiView query timed out; narrow the time range.')
        finally:
            try:
                self.rpc('delete', fetch)
            except Exception:
                logging.warning('Could not cancel completed FortiView task')

    def dataset(self, view, params):
        args = query_args(params)
        key = (view, json.dumps(args, sort_keys=True))
        # Serializes appliance jobs and coalesces duplicate panel requests.
        with self.lock:
            now = time.monotonic()
            if key in self.cache and now - self.cache[key][0] < 60:
                return self.cache[key][1]
            rows, offset = [], 0
            deadline = now + 75
            max_rows = int(self.config.get('max_rows', 10000))
            while True:
                result = self.page(view, args, offset, deadline)
                page = result.get('data', [])
                total = result.get('total-count-all')
                rows.extend(page)
                if len(rows) > max_rows:
                    raise APIError('Result exceeds row limit; select a FortiGate, endpoint or shorter range.')
                if total is not None and len(rows) >= int(total):
                    break
                if len(page) < 1000:
                    if total is not None and len(rows) < int(total):
                        raise APIError('FortiView returned fewer records than its total; refusing partial totals.')
                    break
                if len(rows) >= max_rows or time.monotonic() >= deadline:
                    raise APIError('Query limit reached; narrow the selection.')
                offset = len(rows)
            normalized = [normalize(row, view) for row in rows]
            data = {'rows': normalized, 'meta': {
                'view': view, 'rows': len(rows), 'reported_total': total,
                'database_start': result.get('db_start_time'),
                'data_time_range': result.get('data-time-range'),
                'timezone': result.get('timezone'), 'complete': True,
                'queried_at': datetime.now(timezone.utc).isoformat()}}
            self.cache[key] = (time.monotonic(), data)
            self.cache.move_to_end(key)
            while len(self.cache) > 64:
                self.cache.popitem(last=False)
            return data

    def devices(self):
        with self.lock:
            result = self.rpc('get', {'url': f'/dvmdb/adom/{self.adom}/device'})
            return {'rows': [{'value': d['sn'], 'text': d['name'] + ' (' + d['sn'] + ')'}
                             for d in result['data'] if d.get('sn', '').startswith('FG')]}


NUMBERS = ('sessions', 'session_block', 'session_pass', 'bandwidth', 'traffic_in', 'traffic_out', 'browsetime')


def normalize(row, view):
    result = dict(row)
    for field in NUMBERS:
        if field in result:
            result[field] = int(result[field] or 0)
    if view == 'website-line':
        result['time'] = int(row['time']) * 1000
        result['sessions'] = result['session_pass'] + result['session_block']
        result['bandwidth'] = result['traffic_in'] + result['traffic_out']
    if view == 'source-website':
        result['value'] = row['srcip']
        name = row.get('dev_src_agg', '').split(',')[0]
        result['text'] = row['srcip'] + (' — ' + name if name else '')
    return result


def make_handler(client, token):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass  # Do not write endpoint browsing history or credentials to access logs.

        def respond(self, code, payload):
            body = json.dumps(payload).encode()
            self.send_response(code)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if token and not hmac.compare_digest(self.headers.get('Authorization', ''), 'Bearer ' + token):
                return self.respond(401, {'error': 'Unauthorized'})
            parsed = urlsplit(self.path)
            try:
                if parsed.path == '/health':
                    return self.respond(200, {'status': 'ok', 'service': 'FortiView bridge'})
                if parsed.path == '/api/devices':
                    return self.respond(200, client.devices())
                params = {k: v[-1] for k, v in parse_qs(parsed.query, keep_blank_values=True).items()}
                view = {'/api/websites': 'website-domain', '/api/sources': 'source-website',
                        '/api/timeseries': 'website-line', '/api/summary': 'website-domain'}.get(parsed.path)
                if not view:
                    return self.respond(404, {'error': 'Unknown endpoint'})
                data = client.dataset(view, params)
                if parsed.path == '/api/summary':
                    rows = data['rows']
                    data = {'rows': [{'websites': len(rows), **{
                        k: sum(r.get(k, 0) for r in rows) for k in NUMBERS}}], 'meta': data['meta']}
                self.respond(200, data)
            except (ValueError, KeyError) as error:
                self.respond(400, {'error': str(error)})
            except TimeoutError:
                self.respond(504, {'error': 'FortiView query timed out. Narrow the time range.'})
            except Exception as error:
                logging.error('Request failed: %s', type(error).__name__)
                self.respond(502, {'error': str(error) if isinstance(error, APIError) else 'FortiAnalyzer connection or response failed.'})
    return Handler


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', required=True)
    options = parser.parse_args()
    with open(options.config) as source:
        config = json.load(source)
    bind = config.get('bind', '127.0.0.1')
    token = os.environ.get('BRIDGE_TOKEN', config.get('bridge_token', ''))
    if not ipaddress.ip_address(bind).is_loopback and not token:
        raise SystemExit('Non-loopback binding requires BRIDGE_TOKEN or bridge_token.')
    server = ThreadingHTTPServer((bind, config.get('port', 8788)), make_handler(FortiView(config), token))
    print('FortiView bridge ready', flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
