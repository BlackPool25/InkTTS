"""Kokoro-82M ONNX TTS engine (CPU only). No cloning, no uploads, no GPU.

Interface contract (frozen for render.py):
    engine.synthesize(sentence) -> (pcm16_mono_24k_bytes, sample_rate)

Model behavior: loads `KOKORO_MODEL_PATH` (default data/models/kokoro-82M.onnx)
via onnxruntime CPU with OMP_NUM_THREADS=2. Session is recycled every
~20 sentences to bound arena growth; synthesis overlaps I/O via a small
async prefetch queue in render.py, not here.

Test/CI behavior: if onnxruntime or the model file is absent, the engine
falls back to a deterministic sine-tone stub at the same sample rate so
`pytest backend/tests/test_render.py` stays hermetic. The stub is clearly
audible as a tone (never shipped silently in prod: `is_stub` is exposed
and render.py logs it).
"""
from __future__ import annotations

import math
import os
import struct

SAMPLE_RATE = 24000
RECYCLE_EVERY = 20  # session recycle ~20 sentences
MODEL_VER = "kokoro-82M-onnx-v1"


class TTSEngine:
    def synthesize(self, sentence: str) -> tuple[bytes, int]:
        raise NotImplementedError


class KokoroEngine(TTSEngine):
    def __init__(self, model_path: str = "", voice: str = "af_heart",
                 speed: float = 1.0) -> None:
        self.model_path = model_path or os.environ.get(
            "KOKORO_MODEL_PATH", "/data/models/kokoro-82M.onnx")
        self.voice = voice
        self.speed = speed
        self._uses = 0
        self._session = None
        self._sess_opts = None
        self.is_stub = True
        self._try_load()

    def _try_load(self) -> None:
        try:
            import onnxruntime as ort  # type: ignore

            self._sess_opts = ort.SessionOptions()
            self._sess_opts.intra_op_num_threads = int(
                os.environ.get("OMP_NUM_THREADS", "2"))
            self._sess_opts.inter_op_num_threads = 1
            if os.path.exists(self.model_path):
                self._session = ort.InferenceSession(
                    self.model_path, sess_options=self._sess_opts,
                    providers=["CPUExecutionProvider"])
                self.is_stub = False
        except Exception:
            self._session = None
            self.is_stub = True

    def _recycle(self) -> None:
        # Drop and lazily rebuild the session every ~20 sentences.
        self._uses += 1
        if self._uses % RECYCLE_EVERY == 0:
            self._session = None
            if not self.is_stub:
                self._try_load()

    def synthesize(self, sentence: str) -> tuple[bytes, int]:
        self._recycle()
        if self._session is not None:
            return self._run_model(sentence)
        return self._stub(sentence), SAMPLE_RATE

    def _run_model(self, sentence: str) -> tuple[bytes, int]:
        # Real inference path (reached only when model + onnxruntime exist).
        # Phonemise upstream already handledpron; feed raw text.
        import numpy as np  # type: ignore

        sess = self._session
        feed = {"text": np.array([sentence]),
                "voice": np.array([self.voice]),
                "speed": np.array([self.speed], dtype=np.float32)}
        out = sess.run(None, {k: v for k, v in feed.items()
                              if k in [i.name for i in sess.get_inputs()]})
        pcm = np.asarray(out[0], dtype=np.float32).ravel()
        pcm16 = (np.clip(pcm, -1.0, 1.0) * 32767).astype(np.int16)
        return pcm16.tobytes(), SAMPLE_RATE

    def _stub(self, sentence: str) -> bytes:
        # Deterministic tone: duration scales with text length so timing
        # tests exercise real (nonzero) per-sentence durations.
        # 0.06s floor + 0.012s/char, capped by the 25s sentence cap.
        dur = min(25.0, 0.06 + 0.012 * max(1, len(sentence))) / self.speed
        n = max(1, int(SAMPLE_RATE * dur))
        freq = 220.0
        frames = []
        for i in range(n):
            env = min(1.0, i / (0.01 * SAMPLE_RATE),
                      (n - i) / (0.05 * SAMPLE_RATE))
            v = int(12000 * env * math.sin(2 * math.pi * freq * i / SAMPLE_RATE))
            frames.append(struct.pack("<h", v))
        return b"".join(frames)
