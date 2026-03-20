from __future__ import annotations

from pathlib import Path

import pytest

from econ_sim.app import build_director
from econ_sim.config import AppSettings
from econ_sim.models import (
    AdvisorMode,
    CouncilAdvisorBeat,
    CouncilTurnPlan,
    CouncilTurnRequest,
    ConversationSyncRequest,
    ConversationTurnInput,
    PollSummary,
    QueuePollRequest,
    RealtimeRole,
    RealtimeSessionRequest,
    ResolveStageRequest,
    RoomName,
    SetupSessionCreateRequest,
    SetupSessionPatchRequest,
    SetupSessionStatus,
    SetupSessionTurnRequest,
    SimulationCreateRequest,
    SimulationStatus,
    TownHallQuestionDraft,
    TownHallQuestionRequest,
)


@pytest.mark.asyncio
async def test_create_simulation_and_prepare_stage(tmp_path: Path):
    settings = AppSettings(dummy_openai=True, runs_dir=tmp_path).prepare()
    director = build_director(settings)
    state = await director.create_simulation(
        SimulationCreateRequest(
            title="Test Sim",
            player_name="President Morgan Hale",
            player_role="incumbent premier",
            opponent_name="Governor Elena Cross",
            opponent_role="outsider governor",
            region_focus="Great Lakes factory towns and fast-growing Sun Belt suburbs",
            topic_lens="power prices, apprenticeship ladders, and hospital wait times",
            premise="AI deployment is arriving faster than state capacity can absorb it",
            stakes="Voters are choosing between visible speed and visible fairness",
            persona_count=24,
            stage_count=4,
        )
    )
    await director.wait_for_pending(state.simulation_id)
    loaded = await director.get_simulation(state.simulation_id)
    assert loaded.status == SimulationStatus.stage_ready
    assert loaded.stages
    assert loaded.stages[0].phase_label
    assert loaded.stages[0].sample_citizens
    assert loaded.stages[0].sample_citizens[0].voice
    assert loaded.stages[0].policy_notes == []
    assert loaded.focused_citizen_id == loaded.stages[0].sample_citizens[0].citizen_id
    assert loaded.current_polls
    assert loaded.config.player_role == "incumbent premier"
    assert loaded.config.region_focus == "Great Lakes factory towns and fast-growing Sun Belt suburbs"
    assert "Great Lakes factory towns and fast-growing Sun Belt suburbs" in loaded.stages[0].state_of_world
    assert "Voters are choosing between visible speed and visible fairness" in loaded.stages[0].room_briefing


@pytest.mark.asyncio
async def test_setup_session_turn_updates_config_and_persists(tmp_path: Path):
    settings = AppSettings(dummy_openai=True, runs_dir=tmp_path).prepare()
    director = build_director(settings)
    session = await director.create_setup_session(SetupSessionCreateRequest(country="United States"))

    updated = await director.turn_setup_session(
        session.setup_session_id,
        SetupSessionTurnRequest(
            text=(
                "player: President Morgan Hale. opponent: Governor Elena Cross. "
                "country: Canada. player_role: caretaker prime minister. opponent_role: insurgent premier. "
                "region_focus: Ontario manufacturing cities and prairie logistics hubs. "
                "topic_lens: power bills, housing permits, and nurse staffing. "
                "premise: AI services are spreading faster than provincial institutions can absorb them. "
                "stakes: Voters are deciding whether speed without legitimacy is still a win. "
                "stage_count: 4. persona_count: 32."
            )
        ),
    )

    assert updated.status == SetupSessionStatus.ready
    assert updated.config.country == "Canada"
    assert updated.config.player_role == "caretaker prime minister"
    assert updated.config.opponent_role == "insurgent premier"
    assert updated.config.region_focus == "Ontario manufacturing cities and prairie logistics hubs"
    assert updated.config.topic_lens == "power bills, housing permits, and nurse staffing"
    assert updated.config.premise == "AI services are spreading faster than provincial institutions can absorb them"
    assert updated.config.stakes == "Voters are deciding whether speed without legitimacy is still a win"
    assert updated.config.stage_count == 4
    assert updated.config.persona_count == 32
    assert updated.guidance is not None
    assert updated.guidance.chamber_reply.startswith("Applied ")
    assert "player_name -> President Morgan Hale" in updated.guidance.chamber_reply
    assert any("country -> Canada" == change for change in updated.guidance.applied_updates)
    assert any("region_focus -> Ontario manufacturing cities and prairie logistics hubs" == change for change in updated.guidance.applied_updates)
    assert updated.turns[-1].speaker == "assistant"

    reloaded_director = build_director(settings)
    persisted = await reloaded_director.get_setup_session(session.setup_session_id)
    assert persisted.config.country == "Canada"
    assert persisted.config.stakes == "Voters are deciding whether speed without legitimacy is still a win"
    assert persisted.turns[-2].text.startswith("player:")


@pytest.mark.asyncio
async def test_setup_session_turn_interprets_freeform_country_and_focus(tmp_path: Path):
    settings = AppSettings(dummy_openai=True, runs_dir=tmp_path).prepare()
    director = build_director(settings)
    session = await director.create_setup_session()

    updated = await director.turn_setup_session(
        session.setup_session_id,
        SetupSessionTurnRequest(
            text=(
                "Make this a Finland education-policy run focused on students, teachers, and municipalities. "
                "I want the main lens to be AI changing tutoring, grading, school administration, and equal access."
            )
        ),
    )

    assert updated.config.country == "Finland"
    assert updated.config.region_focus == "municipal school systems"
    assert "students, teachers, and municipalities" in updated.config.topic_lens
    assert "Finland" in updated.config.population_description
    assert any(change.startswith("country -> Finland") for change in updated.guidance.applied_updates)


@pytest.mark.asyncio
async def test_setup_session_mexico_nudge_carries_through_to_started_simulation(tmp_path: Path):
    settings = AppSettings(dummy_openai=True, runs_dir=tmp_path).prepare()
    director = build_director(settings)
    session = await director.create_setup_session()

    updated = await director.turn_setup_session(
        session.setup_session_id,
        SetupSessionTurnRequest(
            text=(
                "Make this a broad Mexico simulation. Keep the rest of the setup default and launch-ready."
            )
        ),
    )

    assert updated.config.country == "Mexico"

    started = await director.start_setup_session(session.setup_session_id)
    await director.wait_for_pending(started.simulation.simulation_id)
    live = await director.get_simulation(started.simulation.simulation_id)

    assert live.config.country == "Mexico"
    assert "Mexico" in live.config.population_description


@pytest.mark.asyncio
async def test_setup_session_skip_ahead_sets_starting_world_mode(tmp_path: Path):
    settings = AppSettings(dummy_openai=True, runs_dir=tmp_path).prepare()
    director = build_director(settings)
    session = await director.create_setup_session()

    updated = await director.turn_setup_session(
        session.setup_session_id,
        SetupSessionTurnRequest(
            text=(
                "Skip ahead a few stages and start in a radical AGI future where the economy is already deeply transformed."
            )
        ),
    )

    assert updated.config.starting_world_mode == "radical"
    assert any(change == "starting_world_mode -> radical" for change in updated.guidance.applied_updates)


@pytest.mark.asyncio
async def test_setup_session_turn_interprets_swiss_adjective_and_retitles_defaults(tmp_path: Path):
    settings = AppSettings(dummy_openai=True, runs_dir=tmp_path).prepare()
    director = build_director(settings)
    session = await director.create_setup_session()

    updated = await director.turn_setup_session(
        session.setup_session_id,
        SetupSessionTurnRequest(
            text=(
                "Make this the Swiss education system with students, teachers, parents, and cantonal administrators in frame. "
                "Focus on AI tutoring, language equity, and the apprenticeship ladder."
            )
        ),
    )

    assert updated.config.country == "Switzerland"
    assert updated.config.player_role == "incumbent federal councillor"
    assert updated.config.opponent_role == "cantonal alliance leader"
    assert updated.config.player_name.startswith("Federal Councillor ")
    assert updated.config.opponent_name.startswith("Cantonal Alliance Leader ")
    assert "Switzerland" in updated.config.population_description


@pytest.mark.asyncio
async def test_setup_session_turn_interprets_agent_count_art_style_and_french_adjective(tmp_path: Path):
    settings = AppSettings(dummy_openai=True, runs_dir=tmp_path).prepare()
    director = build_director(settings)
    session = await director.create_setup_session()

    updated = await director.turn_setup_session(
        session.setup_session_id,
        SetupSessionTurnRequest(
            text=(
                "Make this the French education system with pupils, teachers, and parents in frame. "
                "Use 250 agents and make it feel like a warm watercolor civic documentary."
            )
        ),
    )

    assert updated.config.country == "France"
    assert updated.config.persona_count == 250
    assert "watercolor civic documentary" in (updated.config.visual_style or "")
    assert "France" in updated.config.population_description


@pytest.mark.asyncio
async def test_setup_session_turn_accepts_small_testing_run_sizes(tmp_path: Path):
    settings = AppSettings(dummy_openai=True, runs_dir=tmp_path).prepare()
    director = build_director(settings)
    session = await director.create_setup_session()

    updated = await director.turn_setup_session(
        session.setup_session_id,
        SetupSessionTurnRequest(
            text="Use the broad default U.S. run, but keep it to 12 personas and 3 stages for testing.",
        ),
    )

    assert updated.config.persona_count == 12
    assert updated.config.stage_count == 3
    assert any(change == "persona_count -> 12" for change in updated.guidance.applied_updates)
    assert any(change == "stage_count -> 3" for change in updated.guidance.applied_updates)


@pytest.mark.asyncio
async def test_setup_session_country_shift_retitles_default_ticket(tmp_path: Path):
    settings = AppSettings(dummy_openai=True, runs_dir=tmp_path).prepare()
    director = build_director(settings)
    session = await director.create_setup_session()

    updated = await director.patch_setup_session(
        session.setup_session_id,
        SetupSessionPatchRequest(country="Finland"),
    )

    assert updated.config.country == "Finland"
    assert updated.config.player_role == "incumbent prime minister"
    assert updated.config.opponent_role == "opposition leader"
    assert updated.config.player_name.startswith("Prime Minister ")
    assert updated.config.opponent_name.startswith("Opposition Leader ")


@pytest.mark.asyncio
async def test_patch_setup_session_updates_exact_fields(tmp_path: Path):
    settings = AppSettings(dummy_openai=True, runs_dir=tmp_path).prepare()
    director = build_director(settings)
    session = await director.create_setup_session()

    updated = await director.patch_setup_session(
        session.setup_session_id,
        SetupSessionPatchRequest(
            title="North Atlantic AGI Election",
            player_role="coalition prime minister",
            opponent_role="opposition premier",
            region_focus="Atlantic port cities and inland manufacturing towns",
            topic_lens="power prices, export bottlenecks, and junior-office hiring",
            premise="AI deployment is widening the gap between ports, capitals, and interior towns",
            stakes="The government may lose trust if speed only benefits already-capable regions",
            visual_style="Cold campaign documentary with industrial harbors and parliamentary interiors",
        ),
    )

    assert updated.config.title == "North Atlantic AGI Election"
    assert updated.config.player_role == "coalition prime minister"
    assert updated.config.region_focus == "Atlantic port cities and inland manufacturing towns"
    assert updated.config.visual_style.startswith("Cold campaign documentary")
    assert updated.guidance is not None
    assert updated.guidance.chamber_reply.startswith("Applied ")
    assert "title -> North Atlantic AGI Election" in updated.guidance.chamber_reply
    assert "title -> North Atlantic AGI Election" in updated.guidance.applied_updates
    assert "premise -> AI deployment is widening the gap between ports, capitals, and interior towns" in updated.guidance.applied_updates


@pytest.mark.asyncio
async def test_start_setup_session_creates_simulation_from_session(tmp_path: Path):
    settings = AppSettings(dummy_openai=True, runs_dir=tmp_path).prepare()
    director = build_director(settings)
    session = await director.create_setup_session()
    session = await director.patch_setup_session(
        session.setup_session_id,
        SetupSessionPatchRequest(
            player_name="President Morgan Hale",
            player_role="caretaker prime minister",
            opponent_name="Governor Elena Cross",
            opponent_role="market-first provincial premier",
            country="Canada",
            region_focus="Ontario manufacturing cities and prairie logistics hubs",
            topic_lens="power bills, apprenticeship ladders, and emergency-room wait times",
            premise="AI adoption is outpacing provincial capacity and local trust",
            stakes="The election turns on whether visible competence can keep legitimacy intact",
            stage_count=4,
            persona_count=24,
        ),
    )

    started = await director.start_setup_session(session.setup_session_id)
    await director.wait_for_pending(started.simulation.simulation_id)

    live = await director.get_simulation(started.simulation.simulation_id)
    persisted_session = await director.get_setup_session(session.setup_session_id)
    assert persisted_session.status == SetupSessionStatus.started
    assert persisted_session.started_simulation_id == live.simulation_id
    assert live.config.player_name == "President Morgan Hale"
    assert live.config.player_role == "caretaker prime minister"
    assert live.config.opponent_name == "Governor Elena Cross"
    assert live.config.opponent_role == "market-first provincial premier"
    assert live.config.country == "Canada"
    assert live.config.region_focus == "Ontario manufacturing cities and prairie logistics hubs"
    assert live.config.topic_lens == "power bills, apprenticeship ladders, and emergency-room wait times"
    assert live.config.premise == "AI adoption is outpacing provincial capacity and local trust"
    assert live.config.stakes == "The election turns on whether visible competence can keep legitimacy intact"
    assert live.config.stage_count == 4
    assert live.config.persona_count == 24
    assert "Ontario manufacturing cities and prairie logistics hubs" in live.stages[0].detailed_summary
    assert "The election turns on whether visible competence can keep legitimacy intact" in live.stages[0].room_briefing


@pytest.mark.asyncio
async def test_resolve_stage_advances(tmp_path: Path):
    settings = AppSettings(dummy_openai=True, runs_dir=tmp_path).prepare()
    director = build_director(settings)
    state = await director.create_simulation(
        SimulationCreateRequest(
            player_name="President Morgan Hale",
            opponent_name="Governor Elena Cross",
        )
    )
    await director.wait_for_pending(state.simulation_id)
    updated = await director.resolve_stage(
        state.simulation_id,
        ResolveStageRequest(player_platform="We will speed deployment and share the gains."),
    )
    assert updated.status in {SimulationStatus.initializing, SimulationStatus.completed}


@pytest.mark.asyncio
async def test_resolve_stage_falls_back_to_draft_agenda_and_records_election_shift(tmp_path: Path):
    settings = AppSettings(dummy_openai=True, runs_dir=tmp_path).prepare()
    director = build_director(settings)
    state = await director.create_simulation(
        SimulationCreateRequest(
            player_name="President Morgan Hale",
            opponent_name="Governor Elena Cross",
        )
    )
    await director.wait_for_pending(state.simulation_id)

    updated = await director.resolve_stage(
        state.simulation_id,
        ResolveStageRequest(player_platform="", player_rebuttal="I want the gains to stay broad and visible."),
    )
    resolved_stage = updated.stages[0]

    assert resolved_stage.resolution is not None
    assert resolved_stage.resolution.player_agenda_points
    assert resolved_stage.resolution.election_takeaway
    assert resolved_stage.resolution.post_debate_vote_share_player is not None


@pytest.mark.asyncio
async def test_move_room_focus_tool_updates_state(tmp_path: Path):
    settings = AppSettings(dummy_openai=True, runs_dir=tmp_path).prepare()
    director = build_director(settings)
    state = await director.create_simulation(
        SimulationCreateRequest(
            player_name="President Morgan Hale",
            opponent_name="Governor Elena Cross",
        )
    )
    await director.wait_for_pending(state.simulation_id)
    loaded = await director.get_simulation(state.simulation_id)
    citizen = loaded.stages[0].sample_citizens[1]
    result = await director.execute_tool(
        loaded.simulation_id,
        role=RealtimeRole.advisor,
        tool_name="move_room_focus",
        payload={"room": "citizens", "citizen_id": citizen.citizen_id},
    )
    assert result.ok
    updated = await director.get_simulation(loaded.simulation_id)
    assert updated.current_room == RoomName.citizens
    assert updated.focused_citizen_id == citizen.citizen_id


@pytest.mark.asyncio
async def test_focus_citizen_by_name_tool_matches_display_name(tmp_path: Path):
    settings = AppSettings(dummy_openai=True, runs_dir=tmp_path).prepare()
    director = build_director(settings)
    state = await director.create_simulation(
        SimulationCreateRequest(
            player_name="President Morgan Hale",
            opponent_name="Governor Elena Cross",
        )
    )
    await director.wait_for_pending(state.simulation_id)
    loaded = await director.get_simulation(state.simulation_id)
    citizen = loaded.stages[0].sample_citizens[0]
    first_name = citizen.display_name.split()[0]
    result = await director.execute_tool(
        loaded.simulation_id,
        role=RealtimeRole.advisor,
        tool_name="focus_citizen_by_name",
        payload={"citizen_name": first_name},
    )
    assert result.ok
    updated = await director.get_simulation(loaded.simulation_id)
    assert updated.current_room == RoomName.citizens
    assert updated.focused_citizen_id == citizen.citizen_id


@pytest.mark.asyncio
async def test_advisor_sync_does_not_mutate_policy_board_without_tool_call(tmp_path: Path):
    settings = AppSettings(dummy_openai=True, runs_dir=tmp_path).prepare()
    director = build_director(settings)
    state = await director.create_simulation(
        SimulationCreateRequest(
            player_name="President Morgan Hale",
            opponent_name="Governor Elena Cross",
        )
    )
    await director.wait_for_pending(state.simulation_id)
    await director.sync_conversation(
        state.simulation_id,
        ConversationSyncRequest(
            role=RealtimeRole.advisor,
            turns=[
                ConversationTurnInput(
                    speaker="user",
                    text="Put this on the board: keep consumer AI open, speed grid permits, and offer wage insurance for workers displaced by automation.",
                    mode="text",
                )
            ],
        ),
    )
    updated = await director.get_simulation(state.simulation_id)
    assert updated.stages[updated.active_stage_index].policy_notes == []


@pytest.mark.asyncio
async def test_update_policy_board_tool_supports_add_and_remove(tmp_path: Path):
    settings = AppSettings(dummy_openai=True, runs_dir=tmp_path).prepare()
    director = build_director(settings)
    state = await director.create_simulation(
        SimulationCreateRequest(
            player_name="President Morgan Hale",
            opponent_name="Governor Elena Cross",
        )
    )
    await director.wait_for_pending(state.simulation_id)
    await director.execute_tool(
        state.simulation_id,
        role=RealtimeRole.advisor,
        tool_name="update_policy_board",
        payload={"action": "clear"},
    )
    add_result = await director.execute_tool(
        state.simulation_id,
        role=RealtimeRole.advisor,
        tool_name="update_policy_board",
        payload={"action": "add", "notes": ["Keep consumer AI open", "Speed grid permits"]},
    )
    assert add_result.ok
    remove_result = await director.execute_tool(
        state.simulation_id,
        role=RealtimeRole.advisor,
        tool_name="update_policy_board",
        payload={"action": "remove", "notes": ["Keep consumer AI open"]},
    )
    assert remove_result.ok
    updated = await director.get_simulation(state.simulation_id)
    assert updated.stages[updated.active_stage_index].policy_notes == ["Speed grid permits."]


@pytest.mark.asyncio
async def test_clear_policy_board_keeps_board_blank_until_tool_is_used_again(tmp_path: Path):
    settings = AppSettings(dummy_openai=True, runs_dir=tmp_path).prepare()
    director = build_director(settings)
    state = await director.create_simulation(
        SimulationCreateRequest(
            player_name="President Morgan Hale",
            opponent_name="Governor Elena Cross",
        )
    )
    await director.wait_for_pending(state.simulation_id)
    await director.execute_tool(
        state.simulation_id,
        role=RealtimeRole.advisor,
        tool_name="update_policy_board",
        payload={"action": "set", "notes": ["Keep consumer AI open"]},
    )
    await director.execute_tool(
        state.simulation_id,
        role=RealtimeRole.advisor,
        tool_name="update_policy_board",
        payload={"action": "clear"},
    )
    await director.sync_conversation(
        state.simulation_id,
        ConversationSyncRequest(
            role=RealtimeRole.advisor,
            turns=[
                ConversationTurnInput(
                    speaker="user",
                    text="Let's do two things and put them on the board: fund transmission buildout and keep consumer AI access broad.",
                    mode="text",
                )
            ],
        ),
    )
    updated = await director.get_simulation(state.simulation_id)
    assert updated.stages[updated.active_stage_index].policy_notes == []


def test_normalize_policy_note_preserves_governing_mechanism(tmp_path: Path):
    settings = AppSettings(dummy_openai=True, runs_dir=tmp_path).prepare()
    director = build_director(settings)

    assert director._normalize_policy_note("How to speed grid interconnection while keeping reliability high") == "Speed grid interconnection."
    assert director._normalize_policy_note("Offer wage insurance for workers displaced by automation") == "Offer wage insurance for workers displaced by automation."
    assert director._normalize_policy_note("Require a human appeal path for AI benefit denials") == "Require appeals for AI denials."
    assert director._axis_to_policy_note(
        "Expand AI use in public-facing administration now versus slow deployment until training, data systems, and review capacity improve."
    ) == "Open public services to AI."


def test_normalize_policy_note_drops_conversational_filler(tmp_path: Path):
    settings = AppSettings(dummy_openai=True, runs_dir=tmp_path).prepare()
    director = build_director(settings)

    assert director._normalize_policy_note("Mhm") == ""
    assert director._normalize_policy_note("好的") == ""
    assert director._normalize_policy_note("Okay") == ""


@pytest.mark.asyncio
async def test_run_queued_polls_tool_returns_simulation_payload(tmp_path: Path):
    settings = AppSettings(dummy_openai=True, runs_dir=tmp_path).prepare()
    director = build_director(settings)
    state = await director.create_simulation(
        SimulationCreateRequest(
            player_name="President Morgan Hale",
            opponent_name="Governor Elena Cross",
        )
    )
    await director.wait_for_pending(state.simulation_id)
    await director.execute_tool(
        state.simulation_id,
        role=RealtimeRole.advisor,
        tool_name="queue_poll_question",
        payload={"question": "What do people say in one sentence about whether AI is helping them?"},
    )
    result = await director.execute_tool(
        state.simulation_id,
        role=RealtimeRole.advisor,
        tool_name="run_queued_polls",
        payload={},
    )
    assert result.ok
    assert "simulation" in result.data


@pytest.mark.asyncio
async def test_run_poll_now_tool_returns_simulation_payload(tmp_path: Path):
    settings = AppSettings(dummy_openai=True, runs_dir=tmp_path).prepare()
    director = build_director(settings)
    state = await director.create_simulation(
        SimulationCreateRequest(
            player_name="President Morgan Hale",
            opponent_name="Governor Elena Cross",
        )
    )
    await director.wait_for_pending(state.simulation_id)
    result = await director.execute_tool(
        state.simulation_id,
        role=RealtimeRole.advisor,
        tool_name="run_poll_now",
        payload={"question": "What are people noticing first about AI in daily life?"},
    )
    assert result.ok
    assert result.data["question"] == "What are people noticing first about AI in daily life?"
    assert result.data["prepared_question"]
    assert result.data["summary"]
    assert result.data["topline"]
    assert "simulation" in result.data
    assert result.data["poll_summaries"]


@pytest.mark.asyncio
async def test_run_polls_uses_incremental_extra_path_once_stage_has_baseline_polls(tmp_path: Path):
    settings = AppSettings(dummy_openai=True, runs_dir=tmp_path).prepare()
    director = build_director(settings)
    state = await director.create_simulation(
        SimulationCreateRequest(
            player_name="President Morgan Hale",
            opponent_name="Governor Elena Cross",
        )
    )
    await director.wait_for_pending(state.simulation_id)
    loaded = await director.get_simulation(state.simulation_id)
    stage = loaded.stages[loaded.active_stage_index]
    assert stage.poll_summaries

    await director.queue_poll(
        loaded.simulation_id,
        QueuePollRequest(question="Choose one: should the country keep widening cheap expert help, slow it down, or leave it alone for now?"),
    )

    async def fail_run_tracking_polls(**_: object):
        raise AssertionError("full tracking poll rerun should not be used for incremental extra questions")

    async def fake_run_extra_polls(*, personas, questions, save_dir):
        assert len(questions) == 1
        assert questions[0].question == "Choose one: should the country keep widening cheap expert help, slow it down, or leave it alone for now?"
        assert questions[0].source == "manual"
        frame = personas.copy()
        frame[questions[0].question] = ["keep widening"] * len(frame)
        return frame, [
            PollSummary(
                question=questions[0].question,
                source=questions[0].source,
                counts={"keep widening": len(frame)},
                shares={"keep widening": 1.0},
                sample_reasons=["A voter: \"Cheap expert help is the part I would fight to keep.\""],
            )
        ]

    director.gabriel_service.run_tracking_polls = fail_run_tracking_polls  # type: ignore[method-assign]
    director.gabriel_service.run_extra_polls = fake_run_extra_polls  # type: ignore[method-assign]

    response = await director.run_polls(loaded.simulation_id)

    questions = [summary.question for summary in response.poll_summaries]
    assert "Choose one: should the country keep widening cheap expert help, slow it down, or leave it alone for now?" in questions
    assert response.simulation.stages[response.simulation.active_stage_index].tracking.approval.display
    assert response.simulation.stages[response.simulation.active_stage_index].queued_poll_questions == []
    assert response.simulation.queued_poll_questions == []


@pytest.mark.asyncio
async def test_create_simulation_without_names_uses_randomized_ticket(tmp_path: Path):
    settings = AppSettings(dummy_openai=True, runs_dir=tmp_path).prepare()
    director = build_director(settings)

    state = await director.create_simulation(SimulationCreateRequest())

    assert state.config.player_name
    assert state.config.opponent_name
    assert state.config.opponent_voice


@pytest.mark.asyncio
async def test_debate_realtime_session_uses_stored_opponent_voice(tmp_path: Path):
    settings = AppSettings(dummy_openai=True, runs_dir=tmp_path).prepare()
    director = build_director(settings)
    state = await director.create_simulation(SimulationCreateRequest())
    await director.wait_for_pending(state.simulation_id)
    loaded = await director.get_simulation(state.simulation_id)

    session = await director.create_realtime_session(
        loaded.simulation_id,
        RealtimeSessionRequest(role=RealtimeRole.debate),
    )

    assert session.voice == loaded.config.opponent_voice


@pytest.mark.asyncio
async def test_advisor_council_realtime_session_reports_council_variant(tmp_path: Path):
    settings = AppSettings(dummy_openai=True, runs_dir=tmp_path).prepare()
    director = build_director(settings)
    state = await director.create_simulation(SimulationCreateRequest())
    await director.wait_for_pending(state.simulation_id)
    loaded = await director.get_simulation(state.simulation_id)

    session = await director.create_realtime_session(
        loaded.simulation_id,
        RealtimeSessionRequest(role=RealtimeRole.advisor, advisor_mode=AdvisorMode.council),
    )

    assert session.voice == settings.realtime_voice
    assert session.session_type == "advisor_council"
    assert session.session_variant == "council"


@pytest.mark.asyncio
async def test_advisor_realtime_session_uses_manual_response_for_council_only(tmp_path: Path):
    settings = AppSettings(dummy_openai=True, runs_dir=tmp_path).prepare()
    director = build_director(settings)
    state = await director.create_simulation(SimulationCreateRequest())
    await director.wait_for_pending(state.simulation_id)
    loaded = await director.get_simulation(state.simulation_id)

    captured: list[dict[str, object]] = []

    async def fake_create_realtime_session(**kwargs):
        captured.append(kwargs)
        return ("secret", str(kwargs.get("model", "")))

    director.gateway.create_realtime_session = fake_create_realtime_session  # type: ignore[method-assign]

    await director.create_realtime_session(
        loaded.simulation_id,
        RealtimeSessionRequest(role=RealtimeRole.advisor, advisor_mode=AdvisorMode.council),
    )
    await director.create_realtime_session(
        loaded.simulation_id,
        RealtimeSessionRequest(role=RealtimeRole.advisor, advisor_mode=AdvisorMode.solo),
    )

    assert captured[0]["create_response"] is False
    assert captured[1]["create_response"] is True
    council_tool_names = [tool["name"] for tool in captured[0]["tools"]]
    solo_tool_names = [tool["name"] for tool in captured[1]["tools"]]
    assert captured[0]["tools"] == []
    assert "Do not answer the player" in str(captured[0]["instructions"])
    assert "report_council_floor" not in council_tool_names
    assert "report_council_floor" not in solo_tool_names


@pytest.mark.asyncio
async def test_advisor_council_conversation_thread_is_separate_from_solo_thread(tmp_path: Path):
    settings = AppSettings(dummy_openai=True, runs_dir=tmp_path).prepare()
    director = build_director(settings)
    state = await director.create_simulation(SimulationCreateRequest())
    await director.wait_for_pending(state.simulation_id)

    solo_sync = await director.sync_conversation(
        state.simulation_id,
        ConversationSyncRequest(
            role=RealtimeRole.advisor,
            advisor_mode=AdvisorMode.solo,
            turns=[ConversationTurnInput(speaker="user", text="Keep this simple for now.", mode="text")],
        ),
    )
    council_sync = await director.sync_conversation(
        state.simulation_id,
        ConversationSyncRequest(
            role=RealtimeRole.advisor,
            advisor_mode=AdvisorMode.council,
            turns=[ConversationTurnInput(speaker="user", text="What does the room think?", mode="text")],
        ),
    )

    assert solo_sync.thread_key.endswith(":advisor")
    assert council_sync.thread_key.endswith(":advisor:council")


@pytest.mark.asyncio
async def test_generate_council_turn_is_provisional_until_synced(tmp_path: Path):
    settings = AppSettings(dummy_openai=True, runs_dir=tmp_path).prepare()
    director = build_director(settings)
    state = await director.create_simulation(SimulationCreateRequest())
    await director.wait_for_pending(state.simulation_id)
    loaded = await director.get_simulation(state.simulation_id)

    async def fake_parse(**kwargs):
        return (
            CouncilTurnPlan(
                lead="Rowan",
                reason="capability and buildout",
                board_notes=["Keep consumer AI open"],
                advisors=[
                    CouncilAdvisorBeat(name="Rowan", urgency=8, speak=True, text="Keep the broad gains open while we watch concentration."),
                    CouncilAdvisorBeat(name="Leila", urgency=6, speak=False, text=""),
                    CouncilAdvisorBeat(name="Mateo", urgency=4, speak=False, text=""),
                    CouncilAdvisorBeat(name="Amina", urgency=3, speak=False, text=""),
                ],
            ),
            "response-id",
        )

    director.gateway.parse = fake_parse  # type: ignore[method-assign]

    response = await director.generate_council_turn(
        loaded.simulation_id,
        CouncilTurnRequest(text="What does the room think?", mode="voice"),
    )

    updated = await director.get_simulation(loaded.simulation_id)
    assert updated.conversation_threads.get(response.thread_key, []) == []
    assert updated.stages[updated.active_stage_index].policy_notes == []
    assert response.board_notes == ["Keep consumer AI open."]
    assert response.yield_after_turn is False
    assert response.player_proxy_urgency == 0
    assert [turn.text for turn in response.turns] == ["Keep the broad gains open while we watch concentration."]

    await director.sync_conversation(
        loaded.simulation_id,
        ConversationSyncRequest(
            role=RealtimeRole.advisor,
            advisor_mode=AdvisorMode.council,
            turns=[
                ConversationTurnInput(speaker="user", text="What does the room think?", mode="voice"),
                ConversationTurnInput(
                    speaker="assistant",
                    speaker_name="Rowan",
                    speaker_voice="cedar",
                    text="Keep the broad gains open while we watch concentration.",
                    mode="voice",
                ),
            ],
            board_notes=response.board_notes,
        ),
    )

    committed = await director.get_simulation(loaded.simulation_id)
    assert len(committed.conversation_threads.get(response.thread_key, [])) == 2
    assert committed.stages[committed.active_stage_index].policy_notes == ["Keep consumer AI open."]


@pytest.mark.asyncio
async def test_generate_council_turn_can_continue_without_new_player_text(tmp_path: Path):
    settings = AppSettings(dummy_openai=True, runs_dir=tmp_path).prepare()
    director = build_director(settings)
    state = await director.create_simulation(SimulationCreateRequest())
    await director.wait_for_pending(state.simulation_id)
    loaded = await director.get_simulation(state.simulation_id)

    await director.sync_conversation(
        loaded.simulation_id,
        ConversationSyncRequest(
            role=RealtimeRole.advisor,
            advisor_mode=AdvisorMode.council,
            turns=[
                ConversationTurnInput(speaker="user", text="Let the room argue it out.", mode="voice"),
                ConversationTurnInput(
                    speaker="assistant",
                    speaker_name="Rowan",
                    speaker_voice="cedar",
                    text="Cheap competence is spreading faster than the state is updating the rules around it.",
                    mode="voice",
                ),
            ],
        ),
    )

    captured_input_texts: list[str] = []

    async def fake_parse(**kwargs):
        captured_input_texts.append(str(kwargs["input_text"]))
        return (
            CouncilTurnPlan(
                lead="Leila",
                reason="household fairness pressure now matters more",
                yield_after_turn=True,
                player_proxy_urgency=8,
                advisors=[
                    CouncilAdvisorBeat(name="Leila", urgency=9, speak=True, text="Then answer the family question directly, because people will forgive speed before they forgive feeling cut out."),
                    CouncilAdvisorBeat(name="Rowan", urgency=6, speak=False, text=""),
                    CouncilAdvisorBeat(name="Mateo", urgency=5, speak=False, text=""),
                    CouncilAdvisorBeat(name="Amina", urgency=4, speak=False, text=""),
                ],
            ),
            "response-id",
        )

    director.gateway.parse = fake_parse  # type: ignore[method-assign]

    response = await director.generate_council_turn(
        loaded.simulation_id,
        CouncilTurnRequest(continue_dialogue=True, mode="voice"),
    )

    assert len(captured_input_texts) == 1
    assert captured_input_texts[0].startswith("Council continuation turn.")
    assert "React directly to that last spoken line instead of restarting from the president's original prompt." in captured_input_texts[0]
    assert "If the room should now wait for the president, set yield_after_turn true and leave advisor text empty." in captured_input_texts[0]
    assert response.lead == "Leila"
    assert response.yield_after_turn is True
    assert response.player_proxy_urgency == 8
    assert [turn.text for turn in response.turns] == [
        "Then answer the family question directly, because people will forgive speed before they forgive feeling cut out."
    ]


@pytest.mark.asyncio
async def test_generate_town_hall_question_persists_to_shared_debate_thread(tmp_path: Path):
    settings = AppSettings(dummy_openai=True, runs_dir=tmp_path).prepare()
    director = build_director(settings)
    state = await director.create_simulation(SimulationCreateRequest())
    await director.wait_for_pending(state.simulation_id)
    loaded = await director.get_simulation(state.simulation_id)

    async def fake_parse(**kwargs):
        return (
            TownHallQuestionDraft(
                question="If AI handles more of the screen work, what does your plan do for people like me?",
                cue="Household security under capability gains",
            ),
            "response-id",
        )

    director.gateway.parse = fake_parse  # type: ignore[method-assign]

    response = await director.generate_town_hall_question(
        loaded.simulation_id,
        TownHallQuestionRequest(citizen_id=loaded.stages[loaded.active_stage_index].sample_citizens[0].citizen_id, mode="voice"),
    )

    updated = await director.get_simulation(loaded.simulation_id)
    persisted_turns = updated.conversation_threads.get(response.thread_key, [])
    assert len(persisted_turns) == 1
    assert persisted_turns[0].speaker == "assistant"
    assert persisted_turns[0].speaker_name == loaded.stages[loaded.active_stage_index].sample_citizens[0].display_name
    assert persisted_turns[0].text == "If AI handles more of the screen work, what does your plan do for people like me?"
    assert response.question_turn.text == "If AI handles more of the screen work, what does your plan do for people like me?"
