from __future__ import annotations

import hmac
import os

from fastapi import Request
from fastapi.responses import JSONResponse

TOKEN_HEADER = "X-Aura-Token"

# Loopback names the sidecar is legitimately reached by. Anything else in a
# Host header means the request was aimed at a name that resolves here —
# the DNS-rebinding case — rather than at the local app.
_ALLOWED_HOSTS = frozenset({"127.0.0.1", "localhost", "[::1]", "::1"})

# Unauthenticated so the desktop shell's readiness poll needs no secret
# ordering: it polls before it would have anything to send.
_OPEN_PATHS = frozenset({"/healthz"})


def _host_is_local(request: Request) -> bool:
    host = request.headers.get("host")
    if host is None:
        return True  # HTTP/1.0 and some clients omit it; the bind is already loopback
    return host.rsplit(":", 1)[0] in _ALLOWED_HOSTS if ":" in host else host in _ALLOWED_HOSTS


async def local_auth_middleware(request: Request, call_next):
    """Require a per-launch token, when one is configured.

    Inert while AURA_API_TOKEN is unset, which keeps the developer uvicorn
    workflow and the existing test suite working unchanged — the desktop
    shell is what sets it.

    Loopback binding alone is not enough to justify skipping this. Every
    other process on the machine can reach the port, and a browser page can
    POST to 127.0.0.1: CORS stops the page reading the response, not the
    request being sent, so for endpoints with side effects an unreadable
    response is no protection at all.
    """
    expected = os.environ.get("AURA_API_TOKEN")
    if not expected or request.url.path in _OPEN_PATHS:
        return await call_next(request)

    # Token first, then host. A request with no token is unauthenticated
    # whatever its Host header claims, and answering 401 for it keeps the
    # two checks from masking each other.
    presented = request.headers.get(TOKEN_HEADER, "")
    if not hmac.compare_digest(presented, expected):
        return JSONResponse({"detail": "invalid or missing token"}, status_code=401)

    if not _host_is_local(request):
        return JSONResponse({"detail": "invalid host"}, status_code=400)

    return await call_next(request)
