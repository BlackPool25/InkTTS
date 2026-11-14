"""Per-device OPDS exemption tokens. Readers (KOReader etc.) can't log in.

Zero user accounts, zero password hashing: the login wall is Authelia+Caddy
(premade, audited). This module only mints/verifies long random per-device
tokens stored in the opds_tokens table. Wave 2 wires it into OPDS routes +
Caddy exemption in main.py (NOT this task — main.py is frozen).
"""
import secrets

from sqlmodel import Session, select

from .models import OpdsToken

TOKEN_BYTES = 32  # token_urlsafe(32) -> 43 chars, ~256 bits


def create_opds_token(session: Session, device: str = "") -> str:
    """Mint a per-device token, persist it, return the plaintext (shown once)."""
    token = secrets.token_urlsafe(TOKEN_BYTES)
    row = OpdsToken(token=token, device=device)
    session.add(row)
    session.commit()
    session.refresh(row)
    return row.token


def verify_opds_token(session: Session, provided: str | None) -> OpdsToken | None:
    """Fail-closed: empty/unknown token -> None. Constant-time compare."""
    if not provided:
        return None
    # Small table (one row per device): compare against all rows with
    # compare_digest so no early-exit length oracle per candidate.
    rows = session.exec(select(OpdsToken)).all()
    for row in rows:
        if secrets.compare_digest(provided, row.token):
            return row
    return None
