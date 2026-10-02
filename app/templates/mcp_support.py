"""Shared private Streamable HTTP transport for creator and runtime MCP servers."""
import os
import secrets
from pathlib import Path

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.responses import JSONResponse


def load_token(environment, path):
    token = os.getenv(environment, '').strip()
    if not token:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, 'w') as file:
                file.write(secrets.token_urlsafe(32) + '\n')
        except FileExistsError:
            pass
        token = path.read_text().strip()
    if len(token) < 24:
        raise ValueError(f'{environment} must contain at least 24 characters')
    return token


def server(name, instructions, hosts_environment):
    allowed = ['localhost:*', '127.0.0.1:*', '[::1]:*']
    allowed += [host.strip() for host in os.getenv(hosts_environment, '').split(',') if host.strip()]
    return FastMCP(name, instructions=instructions, stateless_http=True, json_response=True,
                   transport_security=TransportSecuritySettings(allowed_hosts=allowed))


class BearerAuth:
    def __init__(self, app, token):
        self.app, self.token = app, token.encode()

    async def __call__(self, scope, receive, send):
        if scope['type'] == 'http':
            headers = dict(scope.get('headers', []))
            supplied = headers.get(b'authorization', b'')
            scheme, _, value = supplied.partition(b' ')
            if scheme.lower() != b'bearer' or not secrets.compare_digest(value, self.token):
                response = JSONResponse({'error': 'MCP bearer token required'}, status_code=401,
                                        headers={'WWW-Authenticate': 'Bearer'})
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)
