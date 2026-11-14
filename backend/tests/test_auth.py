"""Task 4 auth tests: OPDS token create/verify, 401 logic, fail-closed."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlmodel import Session, SQLModel

from backend.app.opds_auth import authorize_opds_request, is_exempt_path
from backend.app.tokens import create_opds_token, verify_opds_token


@pytest.fixture()
def session(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path}/t.db")
    SQLModel.metadata.create_all(eng)
    with Session(eng) as s:
        yield s


def test_create_then_verify(session):
    tok = create_opds_token(session, "koreader")
    row = verify_opds_token(session, tok)
    assert row is not None and row.device == "koreader"


def test_wrong_token_rejected(session):
    tok = create_opds_token(session, "koreader")
    assert verify_opds_token(session, tok + "x") is None
    assert verify_opds_token(session, "nope") is None


def test_empty_token_fail_closed(session):
    create_opds_token(session, "koreader")
    assert verify_opds_token(session, None) is None
    assert verify_opds_token(session, "") is None


def test_wrong_api_token_401(monkeypatch):
    monkeypatch.setenv("API_TOKEN", "correct-token")
    from backend.app.main import require_token
    with pytest.raises(HTTPException) as e:
        require_token("wrong-token")
    assert e.value.status_code == 401
    with pytest.raises(HTTPException) as e2:
        require_token(None)
    assert e2.value.status_code == 401
    require_token("correct-token")  # no raise


def test_api_token_unset_fail_closed(monkeypatch):
    monkeypatch.delenv("API_TOKEN", raising=False)
    from backend.app.main import require_token
    with pytest.raises(HTTPException) as e:
        require_token("anything")
    assert e.value.status_code == 500


def test_exempt_paths(session):
    assert is_exempt_path("/api/v1/opds/catalog.xml")
    assert is_exempt_path("/api/v1/opds/search")
    assert not is_exempt_path("/api/v1/books")
    assert not is_exempt_path("/api/v1/admin/backup")
    tok = create_opds_token(session, "koreader")
    assert authorize_opds_request("/api/v1/opds/catalog.xml", tok, session)
    assert not authorize_opds_request("/api/v1/opds/catalog.xml", "bad", session)
    # Token never authorizes non-exempt paths
    assert not authorize_opds_request("/api/v1/books", tok, session)
