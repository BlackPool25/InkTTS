import os
import sys

sys.path.insert(0, os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..")))

import pytest

from orchestrator.render import Breaker, RenderError, render_sentence


def _ok(url, body):
    return b"FAKEOPUS"


def _down(url, body):
    raise RenderError("transient", "conn refused")


def test_kokoro_path_no_words_none_degraded():
    b = Breaker()
    r = render_sentence("Hello world.", "af_heart", 1.0, "kokoro", "doc1",
                        b, "http://k", "http://c", post=_ok)
    assert r["audio"] == b"FAKEOPUS" and r["words"] is None
    assert r["degraded"] is False


def test_chatterbox_path_synthetic():
    b = Breaker()
    r = render_sentence("Hello world.", "emily", 1.0, "chatterbox", "doc1",
                        b, "http://k", "http://c", post=_ok)
    assert r["synthetic"] is True and r["degraded"] is False


def test_chatterbox_down_falls_back_loud():
    b = Breaker()
    calls = []
    def post(url, body):
        calls.append(url)
        if "8004" in url or url.startswith("http://c"):
            raise RenderError("transient", "down")
        return b"FAKEOPUS"
    r = render_sentence("Hi.", "emily", 1.0, "chatterbox", "doc1",
                        b, "http://k", "http://c", post=post)
    assert r["degraded"] is True and r["audio"] == b"FAKEOPUS"


def test_breaker_opens_after_three():
    b = Breaker()
    def flaky(url, body):
        if url.startswith("http://c"):
            raise RenderError("transient", "down")
        return b"X"
    for _ in range(3):
        render_sentence("Hi.", "emily", 1.0, "chatterbox", "doc9",
                        b, "http://k", "http://c", post=flaky)
    assert b.tripped("doc9") is True
    # 4th call routes straight to Kokoro: only kokoro URL seen.
    seen = []
    def post(url, body):
        seen.append(url); return b"X"
    render_sentence("Hi.", "emily", 1.0, "chatterbox", "doc9",
                    b, "http://k", "http://c", post=post)
    assert seen == ["http://k/v1/audio/speech"]


def test_poison_rejected():
    b = Breaker()
    with pytest.raises(RenderError):
        render_sentence("   ", "v", 1.0, "kokoro", "d", b, "k", "c", post=_ok)
    with pytest.raises(RenderError):
        render_sentence("x" * 401, "v", 1.0, "kokoro", "d", b, "k", "c",
                        post=_ok)


def test_identical_rerender_is_cache_hit():
    # Contract test: api.py must consult cache before render_sentence.
    # (render_sentence itself is pure; this pins the key inputs.)
    from orchestrator.cache import canonical_key
    k1 = canonical_key("Hello world.", "kokoro-v0.10.0", "af_heart", 1.0, "d1")
    k2 = canonical_key("Hello world.", "kokoro-v0.10.0", "af_heart", 1.0, "d1")
    assert k1 == k2


def test_voice_mapping_kokoro_filename_chatterbox_omitted():
    import json
    from orchestrator.render import _voice_for, render_sentence, Breaker
    assert _voice_for("kokoro", "af_heart") == "kokoro-voice-af_heart.gguf"
    assert _voice_for("kokoro", "") == "kokoro-voice-af_heart.gguf"
    assert _voice_for("chatterbox", "emily") is None
    seen = {}
    def post(url, body):
        seen.update(json.loads(body))
        return b"X"
    render_sentence("Hi.", "af_heart", 1.0, "kokoro", "d",
                    Breaker(), "http://k", "http://c", post=post)
    assert seen["voice"] == "kokoro-voice-af_heart.gguf"
    seen.clear()
    render_sentence("Hi.", "emily", 1.0, "chatterbox", "d",
                    Breaker(), "http://k", "http://c", post=post)
    assert "voice" not in seen
