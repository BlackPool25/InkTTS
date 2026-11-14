"""Kokoro-82M ONNX TTS engine (CPU only). No cloning, no uploads, no GPU.

Interface contract (frozen for render.py):
    engine.synthesize(sentence) -> (pcm16_mono_24k_bytes, sample_rate)

Model behavior: loads `KOKORO_MODEL_PATH` (default
/data/models/kokoro-v1.0.onnx) plus the sibling voices-v1.0.bin style table
via onnxruntime CPU with OMP_NUM_THREADS=2. Session is recycled every
~20 sentences to bound arena growth; synthesis overlaps I/O via a small
async prefetch queue in render.py, not here.

Real path (model + onnxruntime present): text -> espeak-ng `--ipa`
phonemes (one CLI call per clause so `,;:.!?` survive as pause tokens) ->
token ids via the kokoro vocab below (copy of the upstream config.json,
graph carries no embedded vocab: get_modelmeta() custom_metadata_map is
empty) -> feed {tokens int64 [[0, *ids, 0]], style [1,256] row
min(n,510)-1 of voices[voice], speed [1]}. Input names/dtypes are read
from sess.get_inputs() after load (only the tokens/input_ids alias is
assumed, mirroring upstream kokoro-onnx).

Test/CI behavior: if onnxruntime or the model file is absent, the engine
falls back to a deterministic sine-tone stub at the same sample rate so
`pytest backend/tests/test_render.py` stays hermetic. The stub is clearly
audible as a tone (never shipped silently in prod: `is_stub` is exposed
and render.py logs it). Any OTHER real-path failure (missing voices file,
unknown voice, espeak error, alien graph) raises LOUD — it never stubs.
"""
from __future__ import annotations

import math
import os
import re
import struct
import subprocess

SAMPLE_RATE = 24000
RECYCLE_EVERY = 20  # session recycle ~20 sentences
MODEL_VER = "kokoro-82M-onnx-v1"
MAX_PHONEMES = 510  # voices table has one style row per phoneme count
DEFAULT_MODEL_PATH = "/data/models/kokoro-v1.0.onnx"
ESPEAK_VOICE = "en-us"

# Copy of the kokoro vocab (upstream kokoro-onnx config.json, 114 tokens).
# Index 0 is absent: it is the pad id the graph expects around token ids.
_VOCAB: dict[str, int] = {
    ';': 1,
    ':': 2,
    ',': 3,
    '.': 4,
    '!': 5,
    '?': 6,
    '—': 9,
    '…': 10,
    '"': 11,
    '(': 12,
    ')': 13,
    '“': 14,
    '”': 15,
    ' ': 16,
    '̃': 17,
    'ʣ': 18,
    'ʥ': 19,
    'ʦ': 20,
    'ʨ': 21,
    'ᵝ': 22,
    'ꭧ': 23,
    'A': 24,
    'I': 25,
    'O': 31,
    'Q': 33,
    'S': 35,
    'T': 36,
    'W': 39,
    'Y': 41,
    'ᵊ': 42,
    'a': 43,
    'b': 44,
    'c': 45,
    'd': 46,
    'e': 47,
    'f': 48,
    'h': 50,
    'i': 51,
    'j': 52,
    'k': 53,
    'l': 54,
    'm': 55,
    'n': 56,
    'o': 57,
    'p': 58,
    'q': 59,
    'r': 60,
    's': 61,
    't': 62,
    'u': 63,
    'v': 64,
    'w': 65,
    'x': 66,
    'y': 67,
    'z': 68,
    'ɑ': 69,
    'ɐ': 70,
    'ɒ': 71,
    'æ': 72,
    'β': 75,
    'ɔ': 76,
    'ɕ': 77,
    'ç': 78,
    'ɖ': 80,
    'ð': 81,
    'ʤ': 82,
    'ə': 83,
    'ɚ': 85,
    'ɛ': 86,
    'ɜ': 87,
    'ɟ': 90,
    'ɡ': 92,
    'ɥ': 99,
    'ɨ': 101,
    'ɪ': 102,
    'ʝ': 103,
    'ɯ': 110,
    'ɰ': 111,
    'ŋ': 112,
    'ɳ': 113,
    'ɲ': 114,
    'ɴ': 115,
    'ø': 116,
    'ɸ': 118,
    'θ': 119,
    'œ': 120,
    'ɹ': 123,
    'ɾ': 125,
    'ɻ': 126,
    'ʁ': 128,
    'ɽ': 129,
    'ʂ': 130,
    'ʃ': 131,
    'ʈ': 132,
    'ʧ': 133,
    'ʊ': 135,
    'ʋ': 136,
    'ʌ': 138,
    'ɣ': 139,
    'ɤ': 140,
    'χ': 142,
    'ʎ': 143,
    'ʒ': 147,
    'ʔ': 148,
    'ˈ': 156,
    'ˌ': 157,
    'ː': 158,
    'ʰ': 162,
    'ʲ': 164,
    '↓': 169,
    '→': 171,
    '↗': 172,
    '↘': 173,
    'ᵻ': 177,
}

# Word chunks vs pause-punctuation runs. `--ipa` drops punctuation, so
# clauses are phonemised separately and the surviving punct chars are
# re-inserted as pause tokens (mirrors upstream preserve_punctuation).
_SEG = re.compile(r"[,;:.!?…—\"“”()]+|[^\s,;:.!?…—\"“”()]+")
_PAUSE = frozenset(",;:.!?…—\"“”()")


class TTSEngine:
    def synthesize(self, sentence: str) -> tuple[bytes, int]:
        raise NotImplementedError


class KokoroEngine(TTSEngine):
    def __init__(self, model_path: str = "", voice: str = "af_heart",
                 speed: float = 1.0, voices_path: str = "") -> None:
        self.model_path = model_path or os.environ.get(
            "KOKORO_MODEL_PATH", DEFAULT_MODEL_PATH)
        self.voices_path = voices_path or os.environ.get(
            "KOKORO_VOICES_PATH",
            os.path.join(os.path.dirname(self.model_path), "voices-v1.0.bin"))
        self.voice = voice
        self.speed = speed
        self._uses = 0
        self._session = None
        self._sess_opts = None
        self._voices = None
        self.input_names: list[str] = []
        self.available_voices: list[str] = []
        self.is_stub = True
        self._try_load()
        if not self.is_stub and voice not in self._voices:
            raise ValueError(
                f"voice {voice!r} not in {self.voices_path} "
                f"({len(self.available_voices)} voices: "
                f"{', '.join(self.available_voices[:12])}...)")

    def _try_load(self) -> None:
        try:
            import onnxruntime as ort  # type: ignore
        except Exception:
            print("tts: onnxruntime absent, stub tone (RTF bench only)")
            return
        if not os.path.exists(self.model_path):
            print(f"tts: model absent ({self.model_path}), stub tone "
                  f"(RTF bench only)")
            return
        import numpy as np  # type: ignore

        self._sess_opts = ort.SessionOptions()
        self._sess_opts.intra_op_num_threads = int(
            os.environ.get("OMP_NUM_THREADS", "2"))
        self._sess_opts.inter_op_num_threads = 1
        # Real-path failures below raise LOUD: stub is only for a
        # missing runtime/model, never for a broken real setup.
        self._session = ort.InferenceSession(
            self.model_path, sess_options=self._sess_opts,
            providers=["CPUExecutionProvider"])
        self.input_names = [i.name for i in self._session.get_inputs()]
        if not os.path.exists(self.voices_path):
            raise FileNotFoundError(
                f"voices file missing: {self.voices_path} (seed it next "
                f"to the model, e.g. voices-v1.0.bin)")
        self._voices = np.load(self.voices_path, allow_pickle=False)
        self.available_voices = sorted(self._voices.files)
        self.is_stub = False

    def _recycle(self) -> None:
        # Build the replacement BEFORE swapping: concurrent synthesizers
        # (render.py overlaps 4) must never observe _session=None, which
        # would silently ship stub tone inside real audio.
        self._uses += 1
        if self._uses % RECYCLE_EVERY == 0:
            import onnxruntime as ort  # type: ignore

            new_session = ort.InferenceSession(
                self.model_path, sess_options=self._sess_opts,
                providers=["CPUExecutionProvider"])
            self._session = new_session

    def synthesize(self, sentence: str) -> tuple[bytes, int]:
        self._recycle()
        if self._session is not None:
            return self._run_model(sentence)
        return self._stub(sentence), SAMPLE_RATE

    def _phonemize(self, text: str) -> str:
        """espeak-ng `--ipa` per clause; punct runs become pause tokens."""
        parts: list[str] = []
        for chunk in _SEG.findall(text.strip()):
            if not chunk:
                continue
            if chunk[0] in _PAUSE:  # regex alt: punct runs vs word runs
                parts.append("".join(c for c in chunk if c in _VOCAB))
            else:
                try:
                    r = subprocess.run(
                        ["espeak-ng", "-v", ESPEAK_VOICE, "--ipa", "-q",
                         chunk],
                        capture_output=True, text=True, timeout=30)
                except FileNotFoundError as e:
                    raise RuntimeError(
                        "espeak-ng binary missing in image") from e
                if r.returncode != 0:
                    raise RuntimeError(
                        f"espeak-ng failed on {chunk!r}: {r.stderr.strip()}")
                parts.append("".join(
                    c for c in r.stdout if c in _VOCAB or c.isspace()))
        # Reference joins lines then collapses whitespace the same way.
        return " ".join(" ".join(parts).split())

    def _tokens_of(self, phonemes: str) -> list[int]:
        return [i for i in map(_VOCAB.get, phonemes) if i is not None]

    def _style_for(self, n_tokens: int):
        import numpy as np  # type: ignore

        voice = self._voices[self.voice]  # (510, 1, 256)
        return np.asarray(voice[min(n_tokens, len(voice)) - 1],
                          dtype=np.float32)

    def _infer(self, ids: list[int]):
        import numpy as np  # type: ignore

        sess = self._session
        dtypes = {i.name: i.type for i in sess.get_inputs()}
        tokens_key = ("input_ids" if "input_ids" in dtypes else "tokens")
        speed = self.speed
        if dtypes.get("speed", "") in ("tensor(int32)", "tensor(int64)"):
            speed = max(1, round(speed))  # int-exported graph (cf v1.1-zh)
        feed = {
            tokens_key: np.array([[0, *ids, 0]], dtype=np.int64),
            "style": np.asarray(self._style_for(len(ids)), dtype=np.float32),
            "speed": np.array([speed], dtype=np.float32),
        }
        feed = {k: v for k, v in feed.items() if k in dtypes}
        out = sess.run(None, feed)
        return np.asarray(out[0], dtype=np.float32).ravel()

    def _run_model(self, sentence: str) -> tuple[bytes, int]:
        import numpy as np  # type: ignore

        phonemes = self._phonemize(sentence)
        ids = self._tokens_of(phonemes)
        if not ids:
            raise ValueError(
                f"nothing speakable: {sentence!r} produced no phonemes")
        if len(ids) <= MAX_PHONEMES:
            pcm = self._infer(ids)
        else:
            # Rare (25s-cap sentences): split words across windows,
            # one style row per window like upstream; concat waveforms.
            print(f"tts: {len(ids)} phonemes > {MAX_PHONEMES}, "
                  f"windowing ({self.voice})")
            words = phonemes.split(" ")
            chunks, cur = [], ""
            for w in words:
                nxt = (cur + " " + w).strip()
                if len(self._tokens_of(nxt)) > MAX_PHONEMES and cur:
                    chunks.append(cur)
                    cur = w
                else:
                    cur = nxt
            chunks.append(cur)
            pcm = np.concatenate(
                [self._infer(self._tokens_of(c)) for c in chunks])
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
