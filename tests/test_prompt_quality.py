from __future__ import annotations

import pytest

from econ_sim.app import build_director
from econ_sim.config import AppSettings
from econ_sim.models import AdvisorMode, ConversationTurn, SimulationCreateRequest


@pytest.mark.asyncio
async def test_stage_prompt_uses_minimal_world_brief_contract(tmp_path):
    settings = AppSettings(dummy_openai=True, runs_dir=tmp_path).prepare()
    director = build_director(settings)
    state = await director.create_simulation(SimulationCreateRequest())
    await director.wait_for_pending(state.simulation_id)
    loaded = await director.get_simulation(state.simulation_id)
    stage = loaded.stages[loaded.active_stage_index]
    phase = director.orchestrator._phase_brief(loaded.active_stage_index, loaded.config.stage_count)

    prompt = director.orchestrator._stage_prompt(
        config=loaded.config,
        stage_index=loaded.active_stage_index,
        stage_count=loaded.config.stage_count,
        phase=phase,
        previous_stage=stage,
        tracking=stage.tracking,
        poll_summaries=stage.poll_summaries,
        player_in_power=loaded.player_in_power,
        incumbent_name=loaded.incumbent_name,
        queued_poll_questions=[],
    )

    assert "world_brief" in prompt
    assert "narrative_beats" in prompt
    assert "room_briefing" not in prompt
    assert "economic_indicators" not in prompt
    assert "tension_points" not in prompt
    assert "suggested_policy_axes" not in prompt
    assert "capability_frontier_now" not in prompt
    assert "dominant_upside" not in prompt
    assert "main_split" not in prompt
    assert "household_income_system" not in prompt


@pytest.mark.asyncio
async def test_realtime_prompts_accept_world_brief_smoke(tmp_path):
    settings = AppSettings(dummy_openai=True, runs_dir=tmp_path).prepare()
    director = build_director(settings)
    state = await director.create_simulation(SimulationCreateRequest())
    await director.wait_for_pending(state.simulation_id)
    loaded = await director.get_simulation(state.simulation_id)
    stage = loaded.stages[loaded.active_stage_index]

    advisor_prompt = director.realtime_prompts.advisor_instructions(
        loaded,
        stage.world_brief,
        [ConversationTurn(speaker="user", text="What is actually changing here?")],
    )
    council_prompt = director.realtime_prompts.advisor_instructions(
        loaded,
        stage.world_brief,
        [ConversationTurn(speaker="user", text="What should the room focus on?")],
        advisor_mode=AdvisorMode.council,
    )

    assert advisor_prompt
    assert council_prompt
    assert "How life works now:" in advisor_prompt
    assert "small council of senior advisors" in council_prompt


@pytest.mark.asyncio
async def test_featurette_prompt_is_derived_from_world_brief(tmp_path):
    settings = AppSettings(dummy_openai=True, runs_dir=tmp_path).prepare()
    director = build_director(settings)
    state = await director.create_simulation(SimulationCreateRequest())
    await director.wait_for_pending(state.simulation_id)
    loaded = await director.get_simulation(state.simulation_id)
    stage = loaded.stages[loaded.active_stage_index]

    prompt = director.orchestrator._featurette_prompt(config=loaded.config, stage=stage)

    assert stage.world_brief.split(".")[0] in prompt
    assert "suggested_policy_axes" not in prompt
    assert "dominant_mechanism" not in prompt
    assert "state_of_world" not in prompt


def test_room_briefing_keeps_valid_article_phrases(tmp_path):
    settings = AppSettings(dummy_openai=True, runs_dir=tmp_path).prepare()
    director = build_director(settings)

    sentences = [
        "In the United States, most paid screen work no longer requires a worker at a screen.",
        "Service tier decides how fast households can fight billing errors and search for income.",
    ]

    for sentence in sentences:
        assert director.orchestrator._normalize_sentence(sentence, max_words=42, max_chars=270) == sentence
