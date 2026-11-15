import os
import sys

sys.path.insert(0, os.path.normpath(os.path.join(os.path.dirname(__file__),
                                                "..", "..")))

from orchestrator.textpipe import (build_lrc, gate_paragraph,
                                   process_chapter, request_chunks,
                                   split_sentences)


def test_split_abbrev_acronym_numbers():
    sents = split_sentences("Mr. FBI paid $5 for 3 eggs. She left.")
    assert sents[0] == "Mr. FBI paid $5 for 3 eggs."
    assert sents[1] == "She left."


def test_turnstile_blocked():
    g = gate_paragraph('<iframe src="https://challenges.cloudflare.com/turnstile"> Verifying you are human')
    assert g.ok is False and g.error_class == "challenge"


def test_stale_iuam_strings_not_sufficient_alone():
    # Stale copy alone must not be the ONLY signal — but kept as belt.
    g = gate_paragraph("Just a moment while we verify.")
    assert g.ok is False  # still blocked (belt), real pages carry iframe sigs


def test_block_markers_and_short():
    assert gate_paragraph("Chapter locked. Please subscribe.").error_class == "blocked"
    assert gate_paragraph("Hi.").error_class == "empty"


def test_process_chapter_partial_credit():
    sents, problems = process_chapter([
        "The lanterns swayed in the evening wind over the quiet harbor town.",
        "Just a moment.",
        "He walked home slowly through the rain.",
    ])
    assert len(sents) >= 2 and len(problems) == 1
    assert problems[0]["error_class"] in ("challenge", "empty")


def test_dialogue_with_read_more_survives():
    sents, _ = process_chapter([
        "\"You should read more books,\" she said, and left the library quietly."
    ])
    assert len(sents) == 1 and "read more" in sents[0]["text"]


def test_request_chunks_budget():
    sents = [{"idx": i, "text": "Word " * 30} for i in range(5)]
    chunks = request_chunks(sents)
    assert len(chunks) > 1
    for ch in chunks:
        assert sum(len(s["text"]) for s in ch) <= 200 or len(ch) == 1


def test_lrc_kokoro_and_chatterbox():
    entries = [
        {"idx": 0, "text": "Hello world.",
         "words": [{"w": "Hello", "start": 0.0, "end": 0.4},
                   {"w": "world.", "start": 0.4, "end": 0.8}]},
        {"idx": 1, "text": "Plain line.", "words": None, "start": 0.8},
    ]
    lrc = build_lrc(entries)
    assert "<0.00>Hello" in lrc and "[00:00.80]Plain line." in lrc
