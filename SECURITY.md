# Security and deployment scope

The bridge uses one configured FortiAnalyzer account and ADOM. It does not map individual Grafana users to different FAZ permissions. Restrict who can query the Grafana data source and who can reach the bridge.

- Keep FAZ credentials and bridge tokens in a secret manager, service environment, or private configuration file. Do not commit them.
- Keep FAZ certificate verification enabled. Trust a private CA using `ca_file` when needed.
- Prefer loopback access when Grafana and the bridge share a network namespace. Other bind addresses require a bridge token. The token also protects `/health`.
- The bundled Python HTTP server does not terminate TLS. Use private connectivity and an authenticated TLS reverse proxy for access across hosts; do not expose the service directly to the Internet.
- Returned data can include endpoint IPs, usernames, website domains, and appliance errors. Treat it as operational data. HTTP access logging is disabled to avoid recording query filters.
- Use a dedicated FAZ API account scoped to the required data. Verify the required FortiView and device-inventory operations on the target release.

For a suspected vulnerability, use GitHub's private vulnerability reporting if available. Otherwise, open an issue requesting a private contact channel without including exploit details, credentials, or operational data.
