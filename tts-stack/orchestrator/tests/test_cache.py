import os
import sys

sys.path.insert(0, os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..")))

from orchestrator.cache import Cache, canonical_key


def test_store_lookup_hit(tmp_path):
    c = Cache(str(tmp_path), cap_bytes=10_000_000)
    key = canonical_key("hello world", "kokoro-v0.10.0", "af_heart", 1.0, "d1")
    blob = b"OpusData" * 100
    path = c.store(key, blob, 6000, {"voice": "af_heart"})
    assert c.lookup(key) == path
    assert open(path, "rb").read() == blob


def test_miss_returns_none(tmp_path):
    c = Cache(str(tmp_path), cap_bytes=10_000_000)
    assert c.lookup("nope" * 10) is None


def test_over_cap_evicts_oldest(tmp_path):
    c = Cache(str(tmp_path), cap_bytes=1000)
    k1 = canonical_key("first", "e", "v", 1.0, "d1")
    k2 = canonical_key("second", "e", "v", 1.0, "d1")
    p1 = c.store(k1, b"x" * 800, 1000, {})
    c.store(k2, b"y" * 800, 1000, {})
    assert c.lookup(k1) is None  # evicted
    assert not os.path.exists(p1)
    assert c.lookup(k2) is not None


def test_pinned_survives_eviction(tmp_path):
    c = Cache(str(tmp_path), cap_bytes=1000)
    k1 = canonical_key("keep me", "e", "v", 1.0, "d1")
    p1 = c.store(k1, b"x" * 800, 1000, {})
    c.pinned.add(k1)
    c.store(canonical_key("other", "e", "v", 1.0, "d1"), b"y" * 800, 1000, {})
    assert c.lookup(k1) == p1


def test_image_tag_bump_invalidates(tmp_path):
    c = Cache(str(tmp_path), cap_bytes=10_000_000)
    k_old = canonical_key("same text", "kokoro-v0.10.0", "af_heart", 1.0, "d1")
    c.store(k_old, b"z" * 10, 100, {})
    k_new = canonical_key("same text", "kokoro-v0.11.0", "af_heart", 1.0, "d1")
    assert k_new != k_old
    assert c.lookup(k_new) is None


def test_speed_quantized(tmp_path):
    assert (canonical_key("t", "e", "v", 1.0, "d1")
            == canonical_key("t", "e", "v", 1.00, "d1"))


def test_same_text_different_model_different_key(tmp_path):
    from orchestrator.cache import canonical_key
    a = canonical_key("hello world", "e", "v", 1.0, "d1", "kokoro")
    b = canonical_key("hello world", "e", "v", 1.0, "d1", "chatterbox")
    assert a != b
