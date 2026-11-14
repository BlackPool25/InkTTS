"""Real-model contract tests: skipped unless the Kokoro weights are present.

Hermetic CI (no model file / no onnxruntime) skips everything marked
`needs_model`; the stub-path test below it always runs so the suite stays
green without the model too.
"""
import importlib.util
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import pytest

MODEL = os.environ.get("KOKORO_MODEL_PATH", "/data/models/kokoro-v1.0.onnx")
_HAS_RT = importlib.util.find_spec("onnxruntime") is not None
_HAS_MODEL = os.path.exists(MODEL)
needs_model = pytest.mark.skipif(
    not (_HAS_RT and _HAS_MODEL),
    reason="real Kokoro model absent (hermetic CI)")


@needs_model
def test_input_contract():
    from backend.app.tts import KokoroEngine
    e = KokoroEngine()
    assert e.is_stub is False
    assert {"style", "speed"} <= set(e.input_names)
    assert "tokens" in e.input_names or "input_ids" in e.input_names


@needs_model
def test_voice_table():
    from backend.app.tts import KokoroEngine
    e = KokoroEngine()
    assert len(e.available_voices) == 54
    assert "af_heart" in e.available_voices


@needs_model
def test_unknown_voice_fails_loud():
    from backend.app.tts import KokoroEngine
    with pytest.raises(ValueError, match="not in"):
        KokoroEngine(voice="no_such_voice")


@needs_model
def test_one_sentence_smoke():
    from backend.app.tts import KokoroEngine
    e = KokoroEngine(voice="af_heart")
    pcm, sr = e.synthesize("Hello world.")
    assert sr == 24000
    assert len(pcm) % 2 == 0
    dur = len(pcm) / 2 / sr
    assert 0.3 < dur < 25.0
    import struct
    n = len(pcm) // 2
    peak = max(abs(s[0]) for s in struct.iter_unpack("<h", pcm[:2 * min(n, 48000)]))
    assert peak > 500  # real signal, not silence


def test_stub_without_model_stays_green():
    """Hermetic path: missing model file -> audible stub, never a raise."""
    from backend.app.tts import KokoroEngine
    e = KokoroEngine(model_path="/nonexistent/kokoro-xxx.onnx")
    assert e.is_stub is True
    pcm, sr = e.synthesize("Hello world.")
    assert sr == 24000 and len(pcm) > 0
