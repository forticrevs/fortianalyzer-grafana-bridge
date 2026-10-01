#!/usr/bin/env python3
"""Generate the portable dashboard; no credentials or saved query results."""
import json
from pathlib import Path

DS = {'type': 'yesoreyeram-infinity-datasource', 'uid': '${DS_FORTIVIEW}'}
RANGE = 'from=${__from}&to=${__to}&device=${fortigate:percentencode}'
FILTERS = RANGE + '&source=${endpoint:percentencode}&domain=${website:percentencode}'


def query(path, columns, timeseries=False, root='rows', params=FILTERS):
    return {'refId': 'A', 'datasource': DS, 'type': 'json', 'source': 'url',
            'parser': 'backend', 'format': 'timeseries' if timeseries else 'table',
            'url': '/api/' + path + ('?' + params if params else ''),
            'url_options': {'method': 'GET'}, 'root_selector': root,
            'columns': [{'selector': k, 'text': title, 'type': typ} for k, title, typ in columns]}


def variable(name, label, path, params):
    q = query(path, [('text', '__text', 'string'), ('value', '__value', 'string')], params=params)
    # Grafana import substitutes the parent datasource, but not this nested query.
    # Let Infinity use the variable's configured datasource instead.
    q.pop('datasource', None)
    return {'name': name, 'label': label, 'type': 'query', 'datasource': DS,
            'query': {'queryType': 'infinity', 'refId': name, 'infinityQuery': q},
            'refresh': 2, 'includeAll': True, 'allValue': 'All', 'multi': False,
            'current': {'text': 'All', 'value': '$__all'}, 'options': [], 'sort': 1}


panels = []


def panel(title, typ, x, y, w, h, target=None, description='', unit='short'):
    p = {'id': len(panels) + 1, 'title': title, 'type': typ,
         'gridPos': {'x': x, 'y': y, 'w': w, 'h': h}, 'description': description,
         'fieldConfig': {'defaults': {'unit': unit, 'color': {'mode': 'palette-classic'},
                                     'mappings': []}, 'overrides': []}, 'options': {}}
    if target:
        p.update({'datasource': DS, 'targets': [target]})
    panels.append(p)
    return p


p = panel('FortiView · Historical web usage', 'text', 0, 0, 24, 3)
p['options'] = {'mode': 'markdown', 'content': '**Historical website usage by endpoint.** Select a FortiGate, endpoint and time range; enter an exact website or click one below. Use `All` to clear a filter.\n\nSessions are not page views or people. Traffic charts show bytes per bucket. Selecting all FortiGates may count traffic observed more than once.'}
for index, (field, title, unit) in enumerate([
        ('websites', 'Website entries', 'short'), ('sessions', 'Web sessions', 'short'),
        ('bandwidth', 'Transferred', 'bytes'), ('session_block', 'Blocked sessions', 'short')]):
    p = panel(title, 'stat', index * 6, 3, 6, 4,
              query('summary', [(field, title, 'number')]), unit=unit)
    p['options'] = {'reduceOptions': {'calcs': ['lastNotNull'], 'fields': '', 'values': False},
                    'colorMode': 'value', 'graphMode': 'none', 'textMode': 'auto'}

for index, (title, cols, unit) in enumerate([
        ('Web sessions over time', [('session_pass', 'Allowed', 'number'), ('session_block', 'Blocked', 'number')], 'short'),
        ('Web traffic per time bucket', [('traffic_in', 'Received', 'number'), ('traffic_out', 'Sent', 'number')], 'bytes')]):
    p = panel(title, 'timeseries', index * 12, 7, 12, 8,
              query('timeseries', [('time', 'Time', 'timestamp_epoch')] + cols, True),
              'Native FortiView website-line time buckets. Bucket sizes are chosen by FortiAnalyzer; edge buckets can be partial. Values are counts or bytes per bucket, not rates.', unit)
    p['fieldConfig']['defaults']['custom'] = {'drawStyle': 'line', 'lineInterpolation': 'linear',
            'lineWidth': 2, 'fillOpacity': 12, 'showPoints': 'auto', 'spanNulls': False,
            'axisLabel': 'Sessions per bucket' if index == 0 else 'Bytes per bucket'}
    p['options'] = {'legend': {'displayMode': 'list', 'placement': 'bottom', 'showLegend': True},
                    'tooltip': {'mode': 'multi', 'sort': 'desc'}}

p = panel('Websites for the selected endpoint', 'table', 0, 15, 24, 11, query('websites', [
    ('domain', 'Website', 'string'), ('agg_webcat', 'Category', 'string'),
    ('sessions', 'Sessions', 'number'), ('session_block', 'Blocked', 'number'),
    ('bandwidth', 'Bytes', 'number'), ('traffic_in', 'Received', 'number'),
    ('traffic_out', 'Sent', 'number'), ('fortigate', 'FortiGate serials', 'string')]),
    'Complete website-domain results within the service row limit. Click a website to apply it to the charts. Domain/IP labels are returned unchanged by FortiView.')
p['options'] = {'showHeader': True, 'cellHeight': 'sm', 'sortBy': [{'displayName': 'Sessions', 'desc': True}]}
for field in ('Bytes', 'Received', 'Sent'):
    p['fieldConfig']['overrides'].append({'matcher': {'id': 'byName', 'options': field}, 'properties': [{'id': 'unit', 'value': 'bytes'}]})
p['fieldConfig']['overrides'].append({'matcher': {'id': 'byName', 'options': 'Website'}, 'properties': [
    {'id': 'links', 'value': [{'title': 'Filter this website', 'url': '/d/faz-web-usage?${__url_time_range}&${fortigate:queryparam}&${endpoint:queryparam}&var-website=${__value.raw:percentencode}', 'targetBlank': False}]}]})

p = panel('Endpoints · click an IP to investigate', 'table', 0, 26, 24, 9,
          query('sources', [('srcip', 'Source IP', 'string'), ('dev_src_agg', 'Device identity', 'string'),
                            ('f_user', 'User', 'string'), ('fortigate', 'FortiGate serials', 'string')],
                params=RANGE + '&domain=${website:percentencode}'),
          'Choose an endpoint to see its websites and historical charts above. This navigation table is not narrowed by the endpoint selector.')
p['options'] = {'showHeader': True, 'cellHeight': 'sm', 'sortBy': [{'displayName': 'Sessions', 'desc': True}]}
p['fieldConfig']['overrides'] = [
    {'matcher': {'id': 'byName', 'options': 'Bytes'}, 'properties': [{'id': 'unit', 'value': 'bytes'}]},
    {'matcher': {'id': 'byName', 'options': 'Source IP'}, 'properties': [
        {'id': 'links', 'value': [{'title': 'Show websites for this endpoint', 'url': '/d/faz-web-usage?${__url_time_range}&${fortigate:queryparam}&var-endpoint=${__value.raw}&${website:queryparam}', 'targetBlank': False}]}]}]

dashboard = {
    '__inputs': [{'name': 'DS_FORTIVIEW', 'label': 'FortiAnalyzer FortiView', 'type': 'datasource',
                  'pluginId': 'yesoreyeram-infinity-datasource', 'pluginName': 'Infinity'}],
    'id': None, 'uid': 'faz-web-usage', 'title': 'FortiAnalyzer · Historical Web Usage',
    'description': 'Historical FortiView website domains, source endpoints and native time series, queried through a server-side API bridge.',
    'tags': ['fortianalyzer', 'fortiview', 'web-usage'], 'schemaVersion': 39, 'version': 1,
    'timezone': 'browser', 'editable': True, 'refresh': '',
    'time': {'from': 'now-24h', 'to': 'now'}, 'timepicker': {}, 'panels': panels,
    'templating': {'list': [variable('fortigate', 'FortiGate', 'devices', ''),
                            variable('endpoint', 'Endpoint IP', 'sources', RANGE),
                            {'name': 'website', 'label': 'Website (exact or All)', 'type': 'textbox',
                             'query': 'All', 'current': {'text': 'All', 'value': 'All'}, 'options': []}]},
    'annotations': {'list': []}}
for item in panels:
    if item['id'] in (6, 7):
        item['fieldConfig']['overrides'] = [{'matcher': {'id': 'byName', 'options': 'Blocked' if item['id'] == 6 else 'Sent'}, 'properties': [{'id': 'color', 'value': {'mode': 'fixed', 'fixedColor': 'red' if item['id'] == 6 else 'blue'}}]}]
    if item['id'] == 9:
        item['options']['sortBy'] = []
        item['fieldConfig']['overrides'] = [o for o in item['fieldConfig']['overrides'] if o['matcher']['options'] != 'Bytes']
    if item['gridPos']['y'] == 0:
        item['gridPos']['h'] = 4
    else:
        item['gridPos']['y'] += 1
Path(__file__).with_name('dashboard.json').write_text(json.dumps(dashboard, indent=2) + '\n')
