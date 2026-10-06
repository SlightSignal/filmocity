"""Local editor HTTP/WebSocket access policy, independent of endpoint dispatch."""
import hmac
import ipaddress
from urllib.parse import urlsplit


def _canonical_host(host):
    """Normalize numeric literals only; never resolve or trust new DNS names."""
    try:
        return str(ipaddress.ip_address(host))
    except ValueError:
        return host.lower()


def authority(value, scheme):
    """Parse a single HTTP authority; reject ambiguous headers and credentials."""
    if not isinstance(value, str) or not value or any(c.isspace() or ord(c) < 32 for c in value):
        raise ValueError('Invalid authority')
    parsed = urlsplit('//' + value)
    if parsed.username is not None or parsed.password is not None or parsed.path or parsed.query or parsed.fragment:
        raise ValueError('Invalid authority')
    host = parsed.hostname
    if not host or any(c in host for c in ('/', '\\', ',', '%')):
        raise ValueError('Invalid host')
    port = parsed.port
    if port is not None and not 1 <= port <= 65535:
        raise ValueError('Invalid port')
    return _canonical_host(host), port or (443 if scheme == 'https' else 80)


def origin_tuple(value):
    if not isinstance(value, str) or value == 'null':
        raise ValueError('Invalid origin')
    parsed = urlsplit(value)
    if parsed.scheme not in ('http', 'https') or parsed.path or parsed.query or parsed.fragment:
        raise ValueError('Invalid origin')
    return parsed.scheme, *authority(parsed.netloc, parsed.scheme)


def same_token(given, expected):
    return isinstance(given, str) and hmac.compare_digest(given.encode('utf-8'), expected.encode('utf-8'))


def access_error(connection, token):
    """Return (HTTP status, message), or None. No state or cookies are changed.

    Browser requests must originate from the exact serving origin. Local SDKs
    may omit Origin, but still use the server's own Host and any configured
    access token. The OS-provided accepted socket address is the LAN authority;
    arbitrary DNS names are never inferred from an incoming Host header.
    """
    headers = connection.headers
    if len(headers.getlist('host')) != 1 or len(headers.getlist('origin')) > 1:
        return 400, 'A single Host and Origin are required'
    scheme = connection.scope.get('scheme', 'http')
    scheme = {'ws': 'http', 'wss': 'https'}.get(scheme, scheme)
    try:
        host, port = authority(headers['host'], scheme)
    except (ValueError, KeyError):
        return 400, 'Invalid Host'
    server = connection.scope.get('server')
    allowed = {'localhost', '127.0.0.1', '::1'}
    if server:
        socket_host = _canonical_host(str(server[0]))
        if socket_host not in ('0.0.0.0', '::'):
            allowed.add(socket_host)
    if host not in allowed:
        return 403, 'Host is not this Filmocity server'
    if server and isinstance(server[1], int) and port != server[1]:
        return 403, 'Host port is not this Filmocity server'
    origin = headers.get('origin')
    if origin is not None:
        try:
            if origin_tuple(origin) != (scheme, host, port):
                return 403, 'A same-origin Filmocity request is required'
        except ValueError:
            return 403, 'A same-origin Filmocity request is required'
    if headers.get('sec-fetch-site', '').lower() == 'cross-site':
        return 403, 'Cross-site Filmocity requests are not allowed'
    public_version = connection.scope.get('type') == 'http' and connection.scope.get('path') == '/api/version' and connection.scope.get('method') in ('GET', 'HEAD')
    if token and not public_version:
        auth = headers.get('authorization', '')
        bearer = auth[7:] if auth.lower().startswith('bearer ') else None
        candidates = (bearer, connection.query_params.get('token'), connection.cookies.get('filmocity_token'))
        if not any(same_token(given, token) for given in candidates):
            return 401, 'Access token required (open the Filmocity URL with ?token=... once)'
    return None
