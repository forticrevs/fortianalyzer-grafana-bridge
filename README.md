# FortiAnalyzer Grafana Bridge

Query historical FortiView website data from Grafana through a small Python HTTP service.

FortiView uses asynchronous queries. The bridge signs in to FortiAnalyzer, submits a query, waits for completion, retrieves the results, and returns JSON to Grafana's Infinity data source. Credentials stay on the bridge; historical data stays in FortiAnalyzer.

```text
Grafana Infinity → Python bridge → FortiAnalyzer JSON-RPC API
```

The bridge returns website domains, source endpoints, session counts, transferred bytes, and time series. Queries can filter by FortiGate, source IP, exact domain, and date range.

## Setup

You need Python 3.10 or later, Grafana with the Infinity plugin, and a FAZ API account with access to the required ADOM. No extra Python packages are needed.

```bash
git clone https://github.com/forticrevs/fortianalyzer-grafana-bridge.git
cd fortianalyzer-grafana-bridge
cp config.example.json config.json
```

Edit `config.json` with your FAZ address, username, and ADOM. Supply `FAZ_PASSWORD` through the process environment, then start the bridge:

```bash
python3 adapter.py --config config.json
```

It listens on `127.0.0.1:8788` by default. Point Infinity at the bridge and use JSON queries with the backend JSONata parser and root selector `rows`.

The **[integration guide](docs/INTEGRATION.md)** covers persistent installation, certificate trust, container networking, bridge authentication, and query examples. A [systemd unit](fortiview-bridge.service) is included.

## API

| Endpoint | Returns |
| --- | --- |
| `/health` | Service status; does not check FAZ access |
| `/api/devices` | FortiGate inventory |
| `/api/sources` | Source endpoints |
| `/api/websites` | Website domains and metrics |
| `/api/timeseries` | Native FortiView time buckets |
| `/api/summary` | Totals from the website results |

Historical queries require `from` and `to` in Unix milliseconds. Optional filters are `device`, `source`, and `domain`. One bridge instance serves one ADOM.

An optional [example dashboard](examples/dashboard.json) is included. Select your Infinity data source when importing it, or use the endpoints in your own dashboards.

## Limits

- Uses FAZ session authentication and FortiView `apiver: 3`. Check view availability and permissions on your FAZ release; firmware compatibility is not certified by the local tests.
- Defaults to a 31-day maximum query range, 10,000 rows, and a 60-second cache. Queries run serially, so keep refresh intervals reasonable.
- Sessions are not page views. Traffic values are bytes per bucket, not rates. Several FortiGates can observe the same traffic.
- Endpoint-view totals can differ from website totals. Use website results for website reporting.
- All callers share the configured FAZ account's access. Keep the service private, verify FAZ certificates, and use bridge authentication and TLS when crossing hosts. See [SECURITY.md](SECURITY.md).

## Tests and contributions

```bash
python3 -m unittest discover -v
```

Tests use synthetic data and do not contact an appliance. To regenerate the example dashboard, run `python3 examples/build_dashboard.py`.

For fixes or additional views, include tests and document the expected API behavior. Use synthetic examples in issues and pull requests; do not include credentials, logs, or other operational data.

## License

[MIT](LICENSE). This is a community project, not an official Fortinet or Grafana integration.
