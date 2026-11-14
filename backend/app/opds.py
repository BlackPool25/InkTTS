"""OPDS 1.2 catalog + search feeds (Task 8).

Minimal Atom feeds KOReader can walk: catalog lists every novel with an
acquisition link to its M4B; search filters title/author. Auth is NOT here —
handlers re-verify the per-device token via opds_auth (fail-closed 401),
and Caddy only exempts /api/v1/opds* from the login wall.
"""
from __future__ import annotations

from datetime import datetime, timezone
from xml.sax.saxutils import escape

PROFILE = "application/atom+xml;profile=opds-catalog"
ACQUISITION_REL = "http://opds-spec.org/acquisition"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def resolve_token(query_token: str | None,
                  authorization: str | None) -> str | None:
    """?token= first, else `Authorization: Bearer <token>`. Never logged."""
    if query_token:
        return query_token
    if authorization:
        parts = authorization.split(None, 1)
        if len(parts) == 2 and parts[0].lower() == "bearer":
            return parts[1]
    return None


def _updated(novel) -> str:
    ts = getattr(novel, "updated_at", None)
    if ts is None:
        return utc_now_iso()
    return ts.isoformat(timespec="seconds")


def entry_xml(novel, base_url: str, token: str | None) -> str:
    """One novel -> OPDS acquisition entry (M4B; Opus chapters stay in-app)."""
    base = base_url.rstrip("/")
    m4b = f"{base}/api/v1/opds/books/{novel.id}.m4b?token={token or ''}"
    author = escape(getattr(novel, "author", "") or "Unknown")
    return (
        "  <entry>\n"
        f"    <title>{escape(novel.title)}</title>\n"
        f"    <id>urn:inkcast:novel:{novel.id}</id>\n"
        f"    <author><name>{author}</name></author>\n"
        f"    <updated>{escape(_updated(novel))}</updated>\n"
        f'    <content type="text">{escape(getattr(novel, "title", ""))}</content>\n'
        f'    <link rel="{ACQUISITION_REL}" href="{escape(m4b)}" type="audio/mp4"/>\n'
        "  </entry>"
    )


def catalog_feed(novels, base_url: str, token: str | None,
                 self_path: str = "/api/v1/opds/catalog.xml",
                 title: str = "InkCast") -> str:
    base = base_url.rstrip("/")
    lines = [
        '<?xml version="1.0" encoding="utf-8"?>',
        '<feed xmlns="http://www.w3.org/2005/Atom">',
        f"  <title>{escape(title)}</title>",
        "  <id>urn:inkcast:catalog</id>",
        f"  <updated>{utc_now_iso()}</updated>",
        f'  <link rel="self" href="{escape(base + self_path)}?token={token or ""}" type="{PROFILE}"/>',
        f'  <link rel="search" href="{escape(base)}/api/v1/opds/search?q={{searchTerms}}&amp;token={token or ""}" type="{PROFILE}"/>',
        *(entry_xml(n, base, token) for n in novels),
        "</feed>",
    ]
    return "\n".join(lines) + "\n"


def search_feed(novels, query: str, base_url: str,
                token: str | None) -> str:
    """Casefold substring over title/author; empty query returns everything."""
    q = (query or "").casefold().strip()
    hits = ([n for n in novels
             if q in f"{n.title} {getattr(n, 'author', '')}".casefold()]
            if q else list(novels))
    return catalog_feed(hits, base_url, token,
                        self_path="/api/v1/opds/search",
                        title=f"InkCast search: {query or ''}".rstrip())
