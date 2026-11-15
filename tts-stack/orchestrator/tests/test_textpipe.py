import os
import sys

sys.path.insert(0, os.path.normpath(os.path.join(os.path.dirname(__file__),
                                                "..", "..")))

from orchestrator.textpipe import (build_lrc, gate_paragraph,
                                   process_chapter, request_chunks,
                                   split_sentences)


def test_split_abbrev_acronym_numbers():
    sents = split_sentences("Mr. FBI paid $5 for 3 eggs at the corner store on fifth street. She left the market quickly with a full basket of fresh vegetables and bread.")
    assert sents[0] == "Mr. FBI paid $5 for 3 eggs at the corner store on fifth street."
    assert sents[1] == "She left the market quickly with a full basket of fresh vegetables and bread."


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


def test_stub_fragments_merge_forward():
    sents, _ = process_chapter([
        '"My Lord! Please! You cannot simply go off on your own!" Fabian\u2019s voice erupted in panic.'
    ])
    assert len(sents) == 1
    assert "My Lord!" in sents[0]["text"] and "Fabian" in sents[0]["text"]


def test_trailing_stub_merges_back():
    sents, _ = process_chapter([
        'He walked home slowly through the rain and thought of nothing at all in particular. Yes indeed.'
    ])
    assert len(sents) == 1


def test_full_sentences_stay_split():
    a = 'The harbor bells rang at dawn across the quiet water while the sailors mended their heavy fishing nets.'
    b = 'Gulls scattered screaming over the grey rooftops of the sleeping town below the white cliffs.'
    sents, _ = process_chapter([a + ' ' + b])
    assert len(sents) == 2


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


def test_rules_from_text_arrow_format():
    from orchestrator.pron import rules_from_text, apply
    rules = rules_from_text("FBI => F B I\n# comment\nUlrich: Ulrich")
    assert len(rules) == 2
    assert apply("FBI agent Ulrich", rules) == "F B I agent Ulrich"


def test_manifest_accepts_pron_and_versions_cache_key():
    from fastapi.testclient import TestClient
    from orchestrator import api as A
    c = TestClient(A.app)
    r = c.post("/manifest", json={"doc_id": "d-pron",
                                  "sentences": [{"text": "FBI agent walked slowly."}],
                                  "pron": "FBI => F B I"})
    assert r.status_code == 200
    man = r.json()
    assert man["accepted"] >= 1
    from orchestrator import server as S
    assert S.DOC_META["d-pron"]["dict_ver"].startswith("u")
    r2 = c.post("/manifest", json={"doc_id": "d-nopron",
                                   "sentences": [{"text": "FBI agent walked slowly."}]})
    assert S.DOC_META["d-nopron"]["dict_ver"] == "d1"
    assert S.DOC_META["d-pron"]["dict_ver"] != "d1"
