from __future__ import annotations

from econ_sim.app import build_director
from econ_sim.config import AppSettings


def test_room_briefing_keeps_valid_article_phrases(tmp_path):
    settings = AppSettings(dummy_openai=True, runs_dir=tmp_path).prepare()
    director = build_director(settings)

    sentences = [
        "In the United States, most paid screen work no longer requires a worker at a screen.",
        "Service tier decides how fast households can fight billing errors and search for income.",
    ]

    for sentence in sentences:
        assert director.orchestrator._normalize_sentence(sentence, max_words=42, max_chars=270) == sentence


def test_narration_line_cleans_mid_sentence_capitalized_conjunction(tmp_path):
    settings = AppSettings(dummy_openai=True, runs_dir=tmp_path).prepare()
    director = build_director(settings)

    assert (
        director.orchestrator._normalize_narration_line(
            "The account shows dollars on one side and model credits on the other and Both matter now."
        )
        == "The account shows dollars on one side and model credits on the other and both matter now."
    )
