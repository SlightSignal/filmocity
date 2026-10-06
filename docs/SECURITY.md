# Local access policy

Filmocity defaults to a loopback listener. It is a local editor for trusted users and SDK clients. Source `0.47.0-rc.1` adds the following HTTP and WebSocket request checks; the previously installed `0.47.0-dev` baseline remains a separate artifact.

- Exactly one Host header and at most one Origin header are accepted. Host must be a known loopback name/address or the OS-provided address of the accepted server socket, with the serving port. An arbitrary DNS name in Host never becomes trusted. Numeric IPv6 spellings are normalized without DNS resolution; percent-scoped literals are rejected.
- When Origin is present, its HTTP/HTTPS scheme, normalized host and port must match the serving request. WebSocket `ws`/`wss` schemes use the corresponding HTTP/HTTPS origin. Explicit `Sec-Fetch-Site: cross-site` requests are refused. Trusted local SDK clients may omit Origin; this is not authentication against other local processes.
- If a token is configured, API calls, private renders/proxies/thumbnails/fonts, other static resources and WebSockets require a matching bearer, query token or `filmocity_token` cookie. Opening the same-origin editor URL with the token sets an HttpOnly, SameSite=Strict cookie for subsequent resources; HTTPS also sets Secure. Denied requests do not set it. Responses use no-referrer and, with a token, private/no-store caching.
- `GET /api/version` permits an originless identity probe without the token, while still applying Host/origin checks. It reports application/process/workspace/build identity; it does not authorize editing or prove browser security.

Keep the default loopback binding. If deliberately binding to a LAN interface, configure a token and use the actual socket IP/port. Unencrypted HTTP transmits tokens and project data in plaintext; a token does not provide transport encryption. Reverse-proxy/DNS deployment and Internet exposure are not qualified by this policy or by the source tests.

The checks have source ASGI and explicit socket-scope regressions. They are not a demonstrated browser attack, Private Network Access evaluation, penetration test or a secure Internet-service claim. Fresh packaged/native acceptance still belongs to the exact RC artifact. See [release readiness](RELEASE_READINESS.md).
