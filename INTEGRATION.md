# Connecting FortiAnalyzer to Grafana through an API bridge

This guide covers the bridge between FortiAnalyzer (FAZ) and Grafana: what it does, how to run it, and how to query it. It assumes familiarity with Grafana data sources and dashboards.

The accompanying Python bridge is a custom integration for **historical FortiView web usage**, not a general-purpose proxy for every FAZ API. It uses Python 3.10 or later with no additional Python packages. Confirm the API behavior and account permissions on your target FAZ release before deployment.

## How the bridge works

```text
Grafana with Infinity → HTTP GET → Python bridge → HTTPS JSON-RPC → FortiAnalyzer
```

FortiView queries are asynchronous. A request creates a task that must be polled before its results can be used. The bridge handles that process so Grafana can make a single request for JSON data.

For each data query, the bridge:

1. Authenticates to FAZ using a server-side username and password.
2. Submits a FortiView query for the configured ADOM, dates, and filters.
3. Polls until the task reaches 100 percent completion.
4. Retrieves the result pages and converts numeric strings and timestamps into usable types.
5. Deletes its own query tasks and returns the results to Grafana.

FAZ credentials and session IDs remain on the bridge. Queries run on demand; the bridge does not maintain a historical database or require Prometheus. A short in-memory cache reduces repeated queries.

## 1. Prepare the FAZ connection

Confirm that FortiView already shows the required website activity for the devices and dates you intend to query. The bridge can only return data available to FAZ.

Create or select an API account with access to the required ADOM and permission to run FortiView queries and read the device inventory. Enable API access according to the target FAZ release, and restrict access to the bridge host where supported. Use the minimum permissions that pass both inventory and historical-query tests.

The bridge host needs HTTPS access to FAZ's `/jsonrpc` endpoint and must trust the FAZ certificate. Grafana needs access to the bridge; it does not need direct access to FAZ.

## 2. Configure and run the bridge

Files in the repository root:

- [adapter.py](../adapter.py) — the bridge application.
- [config.example.json](../config.example.json) — a generic configuration template.
- [fortiview-bridge.service](../fortiview-bridge.service) — a Linux systemd service.
- [test_adapter.py](../test_adapter.py) — local implementation tests.

Example configuration:

```json
{
  "url": "https://faz.example.com",
  "username": "grafana-api",
  "adom": "root",
  "verify_tls": true,
  "bind": "127.0.0.1",
  "port": 8788,
  "max_rows": 10000
}
```

Replace the FAZ hostname, username, and ADOM. If FAZ uses a private certificate authority, add `"ca_file": "/etc/fortiview-bridge/faz-ca.pem"` and make that CA file readable by the service. Keep certificate verification enabled.

The bridge reads the password from `FAZ_PASSWORD`. It can also read a `password` field in a private configuration file, but credentials should not be included in shared examples, dashboards, or source control. This implementation uses password-based FAZ sessions; API-token authentication would require an implementation change.

For a foreground test, with `FAZ_PASSWORD` supplied through your environment:

```bash
python3 adapter.py --config config.json
```

### Run persistently on Linux

From the repository root:

```bash
sudo install -d /opt/fortiview-bridge /etc/fortiview-bridge
sudo install -m 0644 adapter.py /opt/fortiview-bridge/adapter.py
sudo install -m 0644 config.example.json /etc/fortiview-bridge/config.json
sudoedit /etc/fortiview-bridge/config.json
sudo install -m 0600 /dev/null /etc/fortiview-bridge/secrets.env
sudoedit /etc/fortiview-bridge/secrets.env
```

Put the password in `secrets.env`, using systemd environment-file syntax:

```ini
FAZ_PASSWORD="replace-with-the-FAZ-account-password"
```

Then install and start the service:

```bash
sudo install -m 0644 fortiview-bridge.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now fortiview-bridge
sudo systemctl status fortiview-bridge
```

The supplied unit uses `/usr/bin/python3` and a systemd dynamic user. Adjust the Python path if needed. Keep the non-secret configuration readable by the service; keep `secrets.env` readable only by root. These installation commands assume a new deployment—preserve existing configuration when upgrading.

### Choose the network placement

Loopback binding works when Grafana and the bridge share a network namespace. A Grafana container's `127.0.0.1` normally refers to the container, not its host.

For a separate host or container network, set `bind` to the bridge's reachable IP and set `BRIDGE_TOKEN` in its environment. Non-loopback binding requires this token. Configure the same token in Infinity's secure bearer-authentication settings. Use private connectivity and HTTPS through a reverse proxy for access across hosts; the Python service itself serves HTTP. Restrict access to the Grafana server. All callers share the bridge's FAZ account and configured ADOM.

## 3. Verify the bridge before configuring Grafana

For the loopback configuration above:

```bash
curl --fail-with-body http://127.0.0.1:8788/health
curl --fail-with-body http://127.0.0.1:8788/api/devices
```

`/health` checks only that the bridge is running. `/api/devices` checks FAZ authentication and inventory access. Neither proves that historical FortiView queries work.

Test an actual historical query, using the last 24 hours in this example:

```bash
TO_MS=$(python3 -c 'import time; print(int(time.time() * 1000))')
FROM_MS=$((TO_MS - 86400000))

curl --fail-with-body --get http://127.0.0.1:8788/api/websites \
  --data-urlencode "from=$FROM_MS" \
  --data-urlencode "to=$TO_MS" \
  --data-urlencode "device=All_Devices"
```

Use a known completed period if recent data is unavailable or you need a reproducible comparison. When bridge authentication is enabled, add an `Authorization: Bearer <bridge-token>` header to **every** request, including the health check.

## 4. Configure the Infinity data source

Use the bridge address as the data source base URL. Start with an HTTP timeout of 120 seconds and, if desired, use `/health` as the custom health-check URL. Configure bearer authentication if enabled on the bridge. The FAZ credentials belong only on the bridge.

For queries, use **JSON**, **URL**, **GET**, the **backend JSONata parser**, and root selector **`rows`**. Define explicit column types. Infinity supports nested JSON selection and server-side JSONata processing; see the [Infinity JSON documentation](https://grafana.com/docs/plugins/yesoreyeram-infinity-datasource/latest/data-formats/json/).

Example query URLs relative to the base URL:

```text
/api/websites?from=${__from}&to=${__to}&device=All_Devices
/api/timeseries?from=${__from}&to=${__to}&device=All_Devices
```

To apply your own single-value Grafana variables:

```text
/api/websites?from=${__from}&to=${__to}&device=${fortigate:percentencode}&source=${endpoint:percentencode}&domain=${website:percentencode}
```

Create those variables yourself or substitute literal values. For a time-series query, select the time-series format and map `time` as **Time (UNIX ms)** (`timestamp_epoch` in query JSON), with metric columns as numbers. The bridge already converts FAZ epoch seconds to milliseconds.

## Bridge API reference

All endpoints use `GET` and return JSON. Data endpoints return records under `rows`; historical results also include a `meta` object describing the query result.

| Endpoint | Purpose | Useful fields in `rows` |
| --- | --- | --- |
| `/health` | Service availability only | No `rows`; returns `status` |
| `/api/devices` | FortiGate inventory for the configured ADOM | `text`, `value` (serial number) |
| `/api/sources` | Endpoint discovery from `source-website` | `text`, `value`, `srcip`, `dev_src_agg`, `f_user` |
| `/api/websites` | Domain results from `website-domain` | `domain`, `agg_webcat`, `sessions`, `session_block`, `bandwidth` |
| `/api/timeseries` | Native buckets from `website-line` | `time`, `sessions`, `session_pass`, `session_block`, `traffic_in`, `traffic_out` |
| `/api/summary` | Totals computed from domain results | `websites`, `sessions`, `session_block`, `bandwidth` |

Inventory and health endpoints need no parameters. All other endpoints accept:

| Parameter | Meaning |
| --- | --- |
| `from`, `to` | Required Unix timestamps in **milliseconds**; start must precede end, maximum range 31 days |
| `device` | One FortiGate serial/device identifier; defaults to `All_Devices` |
| `source` | Optional exact endpoint IPv4 or IPv6 address |
| `domain` | Optional exact domain or IP label; no wildcard or full URL |

`All` clears an optional filter and selects all devices for `device`. Lists and arbitrary FAZ filter expressions are not accepted. Configure one ADOM per bridge instance. Use `text` and `value` from inventory/source results for Grafana variables; refresh source queries when the time range or selected FortiGate changes.

## Operational behavior and troubleshooting

- **Query limits:** identical historical queries are cached for 60 seconds; FAZ jobs are serialized. Results are paged in batches of 1,000 up to the configured row limit. The bridge rejects known incomplete results instead of showing partial totals. Narrow filters before increasing limits or refresh frequency.
- **Session renewal:** the bridge signs in again once when it detects an expired session. Some FAZ builds report this as status `-11`, which can also indicate a permission problem. A repeated failure is returned to the caller.
- **Errors:** HTTP `400` indicates invalid input, `401` indicates missing or incorrect bridge authentication, `502` indicates a FAZ/API failure, and `504` indicates a query timeout. Inspect the JSON `error` field. Service logs are available with `journalctl -u fortiview-bridge`.
- **Empty results:** check the ADOM, filters, time range, and corresponding FortiView results. **Dropdown showing only All:** verify that the variable query references the actual Infinity data source rather than an unresolved import placeholder.
- **Metric meaning:** `bandwidth`, `traffic_in`, and `traffic_out` represent bytes here, not rates. Sessions are not page views. Use domain results for website totals and line results for trends; source-view totals can differ. Selecting several FortiGates may count traffic observed more than once.

Validate with one FortiGate, one endpoint, and a completed period using the same filters and timezone in FAZ and Grafana. Check a real data query, then reload Grafana and verify that the results remain available. Local tests can be run with `python3 -m unittest -v test_adapter.py`; they do not replace testing against the target FAZ release.

For API changes or additional views, consult the version-matched [Fortinet Developer Network documentation](https://fndn.fortinet.net/). Extending the bridge requires confirming each view's parameters, response fields, and metric meanings.
