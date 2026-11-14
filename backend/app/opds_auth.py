"""OPDS exemption-path checker. Wave 2 wiring (in main.py, NOT here):

    if is_exempt_path(request.url.path):
        row = verify_opds_token(session, request.query_params.get("token"))
        if row is None: raise 401
    else:
        <Authelia wall already enforced by Caddy forward-auth>

Caddy already proxies /api/v1/opds* without forward-auth (ops/Caddyfile);
this checker is the in-app half so a token can't leak to non-OPDS paths.
"""
from sqlmodel import Session

from .tokens import verify_opds_token

EXEMPT_PREFIX = "/api/v1/opds/"


def is_exempt_path(path: str) -> bool:
    """True only for OPDS routes. Everything else stays behind the wall."""
    return path == "/api/v1/opds/catalog.xml" or path.startswith(EXEMPT_PREFIX)


def authorize_opds_request(path: str, token: str | None, session: Session) -> bool:
    """Fail-closed: non-exempt path -> False; exempt -> token must verify."""
    if not is_exempt_path(path or ""):
        return False
    return verify_opds_token(session, token) is not None
