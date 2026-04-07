from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from econ_sim.app import build_director, resolve_asset_file
from econ_sim.config import AppSettings
from econ_sim.models import (
    AdvisorMode,
    AuditoriumMode,
    CouncilAdvisorDraft,
    CouncilAdvisorBeat,
    CouncilAdvisorProfile,
    CouncilSpeakerDecision,
    CouncilTurnPlan,
    CouncilTurnRequest,
    ConversationSyncRequest,
    ConversationTurn,
    ConversationTurnInput,
    DocumentaryFeaturette,
    NarrativeBeat,
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
    TownHallOpponentReplyDraft,
    TownHallOpponentReplyRequest,
    TownHallQuestionDraft,
    TownHallQuestionRequest,
)
from econ_sim.services import gabriel_service as gabriel_service_module
from econ_sim.services.gabriel_service import GabrielService
from econ_sim.services.orchestrator import StagePolishOutput


def _standard_council_roster() -> list[CouncilAdvisorProfile]:
    return [
        CouncilAdvisorProfile(
            key="capacity",
            name="Rowan",
            room_role="Economy",
            country_role="national economic and industrial strategy director",
            remit=(
                "tracks prices, household purchasing power, machine-income flows, capability diffusion, "
                "industrial buildout, competition, compute access, and which concrete gains households or "
                "smaller firms would fight to keep if politics tried to choke useful capacity and supply chains"
            ),
            voice="cedar",
            viewpoint="leans pro-diffusion when the gains are real, but can back restraint around bottlenecks or concentrated capture",
        ),
        CouncilAdvisorProfile(
            key="innovation",
            name="Leila",
            room_role="Innovation",
            country_role="science, innovation, and frontier systems advisor",
            remit=(
                "tracks research speed, robotics rollout, compute bottlenecks, talent pipelines, laboratory and "
                "startup diffusion, and which institutional changes, standards, or public interfaces would unlock "
                "more real capability instead of merely protecting incumbents"
            ),
            voice="marin",
            viewpoint="pushes capability forward first, but worries about cartelized chokepoints and frozen frontier access",
        ),
        CouncilAdvisorProfile(
            key="politics",
            name="Mateo",
            room_role="Politics",
            country_role="political strategy director",
            remit=(
                "tracks coalition mood, legitimacy, polling movement, debate framing, public tolerance for change, "
                "and which lines the player can actually defend in public without sounding evasive, bloodless, abstract, or overconfident"
            ),
            voice="ash",
            viewpoint="sharp on voter interpretation, willing to defend speed when people see gains and punishing caution when it sounds fake",
        ),
        CouncilAdvisorProfile(
            key="state",
            name="Amina",
            room_role="Security",
            country_role="national security and state-capacity advisor",
            remit=(
                "tracks infrastructure, strategic dependence, war and coercion risk, resilience, defense-industrial "
                "readiness, alliances, and what the state can actually execute, procure, secure, ration, or defend in the real world"
            ),
            voice="shimmer",
            viewpoint="starts from resilience and execution, but can favor openness when it clearly makes the state stronger",
        ),
    ]


@pytest.mark.asyncio
async def test_resume_incomplete_simulations_requeues_initializing_runs(tmp_path: Path):
    settings = AppSettings(dummy_openai=True, runs_dir=tmp_path).prepare()
    director = build_director(settings)
    initializing = await director.create_simulation(SimulationCreateRequest())
    ready = await director.create_simulation(SimulationCreateRequest())
    ready.status = SimulationStatus.stage_ready
    await director.store.save(ready)

    resumed: list[str] = []

    async def fake_prepare_stage(simulation_id: str) -> None:
        resumed.append(simulation_id)

    director._prepare_stage = fake_prepare_stage  # type: ignore[method-assign]
    director._tasks.clear()

    await director.resume_incomplete_simulations()
    await asyncio.sleep(0)

    assert resumed == [initializing.simulation_id]


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
async def test_create_simulation_infers_later_opening_from_premise(tmp_path: Path):
    settings = AppSettings(dummy_openai=True, runs_dir=tmp_path).prepare()
    director = build_director(settings)

    state = await director.create_simulation(
        SimulationCreateRequest(
            premise=(
                "Start about fifteen years from now in a much stranger AGI society where households no longer organize life "
                "around a normal job week, rival compute blocs shape geopolitics, and the economy works in radically different ways from today."
            ),
        )
    )

    assert director.orchestrator._starting_phase_anchor(
        state.config.premise,
        state.config.topic_lens,
        state.config.stakes,
    ) >= 3

    await director.wait_for_pending(state.simulation_id)
    loaded = await director.get_simulation(state.simulation_id)

    assert director.orchestrator._starting_phase_anchor(
        loaded.config.premise,
        loaded.config.topic_lens,
        loaded.config.stakes,
    ) >= 3


@pytest.mark.asyncio
async def test_prepare_stage_queues_featurettes_without_blocking_stage_ready(tmp_path: Path, monkeypatch):
    settings = AppSettings(dummy_openai=True, runs_dir=tmp_path).prepare()
    director = build_director(settings)
    queued: list[tuple[str, int]] = []

    monkeypatch.setattr(
        director,
        "_queue_stage_featurettes",
        lambda simulation_id, stage_index: queued.append((simulation_id, stage_index)),
    )

    state = await director.create_simulation(SimulationCreateRequest())
    await director.wait_for_pending(state.simulation_id)
    loaded = await director.get_simulation(state.simulation_id)

    assert loaded.status == SimulationStatus.stage_ready
    assert queued == [(loaded.simulation_id, 0)]
    assert loaded.stages[0].featurettes_status == "queued"
    assert loaded.stages[0].featurettes == []


@pytest.mark.asyncio
async def test_prepare_stage_featurettes_merges_ready_assets_without_overwriting_stage(tmp_path: Path, monkeypatch):
    settings = AppSettings(dummy_openai=True, runs_dir=tmp_path).prepare()
    director = build_director(settings)
    monkeypatch.setattr(director, "_queue_stage_featurettes", lambda simulation_id, stage_index: None)

    state = await director.create_simulation(SimulationCreateRequest())
    await director.wait_for_pending(state.simulation_id)
    loaded = await director.get_simulation(state.simulation_id)
    stage = loaded.stages[loaded.active_stage_index].model_copy(deep=True)
    stage.authored_room_briefing = (
        "Families now defend the dividend and the civic agent they use every day. "
        "The split is whether those gains stay broad or harden into tollbooths."
    )
    stage.room_briefing = "Short fallback that should not win."

    async def fake_compose_stage_featurettes(*, state, stage):
        return [
            DocumentaryFeaturette(
                subject="Household routines",
                question="How does this chapter change ordinary household coordination?",
                title="The New Week",
                logline="How the chapter changes ordinary household coordination.",
                status="generating",
                narrative_beats=[
                    NarrativeBeat(
                        line="Households relied on machine help to coordinate the basics.",
                        image_prompt="Painterly households and civic systems in motion.",
                    )
                ],
            )
        ]

    async def fake_materialize_featurette_media(*, featurette, asset_dir):
        asset_dir.mkdir(parents=True, exist_ok=True)
        beat = featurette.narrative_beats[0]
        image_path = asset_dir / "featurette-00.png"
        audio_path = asset_dir / "featurette-00.mp3"
        image_path.write_bytes(b"png")
        audio_path.write_bytes(b"mp3")
        beat.image_path = str(image_path)
        beat.audio_path = str(audio_path)

    monkeypatch.setattr(director.orchestrator, "compose_stage_featurettes", fake_compose_stage_featurettes)
    monkeypatch.setattr(director.orchestrator, "materialize_featurette_media", fake_materialize_featurette_media)

    await director._prepare_stage_featurettes(loaded.simulation_id, stage.index)
    refreshed = await director.get_simulation(loaded.simulation_id)
    refreshed_stage = refreshed.stages[refreshed.active_stage_index]

    assert refreshed_stage.title == stage.title
    assert refreshed_stage.featurettes_status == "ready"
    assert len(refreshed_stage.featurettes) == 1
    assert refreshed_stage.featurettes[0].status == "ready"
    assert refreshed_stage.featurettes[0].narrative_beats[0].image_url.startswith("/assets/")
    assert refreshed_stage.featurettes[0].narrative_beats[0].audio_url.startswith("/assets/")


@pytest.mark.asyncio
async def test_polish_stage_after_poll_preserves_authored_room_brief_when_model_omits_new_brief(tmp_path: Path, monkeypatch):
    settings = AppSettings(dummy_openai=True, runs_dir=tmp_path).prepare()
    director = build_director(settings)
    state = await director.create_simulation(SimulationCreateRequest())
    await director.wait_for_pending(state.simulation_id)
    loaded = await director.get_simulation(state.simulation_id)
    stage = loaded.stages[loaded.active_stage_index].model_copy(deep=True)
    stage.authored_room_briefing = (
        "Families now defend the dividend and the civic agent they use every day. "
        "The split is whether those gains stay broad or harden into tollbooths."
    )
    stage.room_briefing = "Short fallback that should not win."

    director.orchestrator.settings.dummy_openai = False

    async def fake_parse(**kwargs):
        return (
            StagePolishOutput(
                room_briefing="",
                economic_indicators=[],
                tension_points=[],
                suggested_policy_axes=[],
            ),
            None,
        )

    monkeypatch.setattr(director.orchestrator.gateway, "parse", fake_parse)

    polished = await director.orchestrator.polish_stage_after_poll(
        stage=stage.model_copy(deep=True),
        tracking=stage.tracking,
        poll_summaries=stage.poll_summaries,
        sample_citizens=stage.sample_citizens,
    )

    assert polished.room_briefing == stage.authored_room_briefing


@pytest.mark.asyncio
async def test_call_gabriel_suppresses_console_output_and_sets_quiet_flags(tmp_path: Path, monkeypatch, capsys):
    settings = AppSettings(runs_dir=tmp_path).prepare()
    service = GabrielService(settings)
    seen: dict[str, object] = {}

    async def fake_poll(**kwargs):
        seen.update(kwargs)
        print("LOUD GABRIEL BANNER")
        return {"ok": True}

    monkeypatch.setattr(gabriel_service_module, "_GABRIEL_MODULE", SimpleNamespace(poll=fake_poll))

    result = await service._call_gabriel("poll", save_dir=str(tmp_path))

    assert result == {"ok": True}
    assert seen["quiet"] is True
    assert seen["verbose"] is False
    assert seen["print_example_prompt"] is False
    assert seen["status_report_interval"] is None
    captured = capsys.readouterr()
    assert "LOUD GABRIEL BANNER" not in captured.out


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
async def test_setup_session_skip_ahead_stays_natural_language_only(tmp_path: Path):
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

    assert director.orchestrator._starting_phase_anchor(
        updated.config.premise,
        updated.config.topic_lens,
        updated.config.stakes,
    ) >= 3
    assert all("starting_world_mode" not in change for change in updated.guidance.applied_updates)


@pytest.mark.asyncio
async def test_setup_session_future_brief_stays_in_premise_without_turning_into_topic_lens(tmp_path: Path):
    settings = AppSettings(dummy_openai=True, runs_dir=tmp_path).prepare()
    director = build_director(settings)
    session = await director.create_setup_session()

    updated = await director.turn_setup_session(
        session.setup_session_id,
        SetupSessionTurnRequest(
            text=(
                "Start about fifteen years from now in a genuinely transformed AGI society. "
                "I want a much stranger political economy, altered household routines, and painterly documentary reels."
            )
        ),
    )

    assert director.orchestrator._starting_phase_anchor(
        updated.config.premise,
        updated.config.topic_lens,
        updated.config.stakes,
    ) >= 3
    assert "fifteen years" in updated.config.premise.lower()
    assert updated.config.topic_lens == ""
    assert updated.config.population_description == settings.default_population_description


@pytest.mark.asyncio
async def test_setup_session_future_world_request_stays_in_premise_not_topic_lens(tmp_path: Path):
    settings = AppSettings(dummy_openai=True, runs_dir=tmp_path).prepare()
    director = build_director(settings)
    session = await director.create_setup_session()

    updated = await director.turn_setup_session(
        session.setup_session_id,
        SetupSessionTurnRequest(
            text=(
                "Start this campaign around fifteen to twenty years in the future in a United States structurally remade by advanced AI, robotics, "
                "compute politics, and geopolitical realignment. Let the documentary be painterly and impressionist."
            )
        ),
    )

    assert director.orchestrator._starting_phase_anchor(
        updated.config.premise,
        updated.config.topic_lens,
        updated.config.stakes,
    ) >= 3
    assert "fifteen to twenty years in the future" in updated.config.premise.lower()
    assert updated.config.topic_lens == ""
    assert "structurally remade by advanced ai" not in updated.config.population_description.lower()
    assert "painterly" in (updated.config.visual_style or "").lower()


@pytest.mark.asyncio
async def test_setup_session_broad_future_brief_with_values_stays_out_of_topic_lens(tmp_path: Path):
    settings = AppSettings(dummy_openai=True, runs_dir=tmp_path).prepare()
    director = build_director(settings)
    session = await director.create_setup_session()

    updated = await director.turn_setup_session(
        session.setup_session_id,
        SetupSessionTurnRequest(
            text=(
                "Let's start about fifteen years from now in the United States. "
                "I care about bargaining power, rents, sovereignty, public provision, daily life, and the new institutions that replaced the old baseline."
            )
        ),
    )

    assert director.orchestrator._starting_phase_anchor(
        updated.config.premise,
        updated.config.topic_lens,
        updated.config.stakes,
    ) >= 3
    assert "fifteen years from now" in updated.config.premise.lower()
    assert updated.config.topic_lens == ""
    assert updated.config.population_description == settings.default_population_description


@pytest.mark.asyncio
async def test_setup_session_broad_future_world_with_schools_keeps_broad_population(tmp_path: Path):
    settings = AppSettings(dummy_openai=True, runs_dir=tmp_path).prepare()
    director = build_director(settings)
    session = await director.create_setup_session()

    updated = await director.turn_setup_session(
        session.setup_session_id,
        SetupSessionTurnRequest(
            text=(
                "Set this fifteen to twenty years from now in a United States already remade by advanced AI, robotics, compute chokepoints, and geopolitical realignment. "
                "Let daily life, household income, schools, care, corporate structure, local government, and war all be eligible to change together."
            )
        ),
    )

    assert director.orchestrator._starting_phase_anchor(
        updated.config.premise,
        updated.config.topic_lens,
        updated.config.stakes,
    ) >= 3
    assert updated.config.topic_lens == ""
    assert updated.config.population_description == settings.default_population_description


def test_population_frame_does_not_narrow_from_future_premise(tmp_path: Path):
    settings = AppSettings(dummy_openai=True, runs_dir=tmp_path).prepare()
    director = build_director(settings)

    population = director._population_frame_for(
        country="United States",
        region_focus="",
        topic_lens="",
        premise=(
            "Fifteen to twenty years from now the country is structurally remade by advanced AI, robotics, "
            "new income systems, and altered state power."
        ),
    )

    assert population == settings.default_population_description


@pytest.mark.asyncio
async def test_setup_session_future_after_transition_brief_infers_later_opening(tmp_path: Path):
    settings = AppSettings(dummy_openai=True, runs_dir=tmp_path).prepare()
    director = build_director(settings)
    session = await director.create_setup_session()

    updated = await director.turn_setup_session(
        session.setup_session_id,
        SetupSessionTurnRequest(
            text=(
                "Start this chapter fifteen to twenty years after a major AGI and robotics transition, in a society where daily life and the state may both work very differently."
            )
        ),
    )

    assert director.orchestrator._starting_phase_anchor(
        updated.config.premise,
        updated.config.topic_lens,
        updated.config.stakes,
    ) >= 3
    assert "fifteen to twenty years after" in updated.config.premise.lower()
    assert updated.config.topic_lens == ""


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


def test_normalize_council_speech_fixes_lowercase_fragment_breaks(tmp_path: Path):
    settings = AppSettings(dummy_openai=True, runs_dir=tmp_path).prepare()
    director = build_director(settings)

    first = director._normalize_council_speech(
        "Cheap digital help is starting to work like cheap software used to: families, freelancers. small businesses can now get research, drafting, booking, support, and admin work done faster without hiring a full staff."
    )
    assert "freelancers, small businesses" in first
    assert ". small businesses" not in first
    assert first.endswith(".")

    second = director._normalize_council_speech(
        "A normal household lives on four streams: a monthly federal machine check, local service credits for things like care or transit, some savings income if they have it. occasional paid human work."
    )
    assert "it, occasional paid human work" in second
    assert ". occasional paid human work" not in second
    assert second.endswith(".")

    third = director._normalize_council_speech(
        "Households live on four streams: a monthly machine check, local credits for approved services, a little savings income, and occasional paid human work. the win is you no longer need a full office payroll for routine expert help."
    )
    assert third == "Households live on four streams: a monthly machine check, local credits for approved services, a little savings income, and occasional paid human work."

    fourth = director._normalize_council_speech(
        "Keep the civic account simple and fix it fast when it freezes someone out."
    )
    assert fourth == "Keep the public account simple and fix it fast when it freezes someone out."


def test_normalize_town_hall_question_collapses_repeated_words(tmp_path: Path):
    settings = AppSettings(dummy_openai=True, runs_dir=tmp_path).prepare()
    director = build_director(settings)

    question = director._normalize_town_hall_question_text(
        "Honestly, if the opening is still still there after the AI screen, what happens next"
    )
    cue = director._normalize_town_hall_cue("Still still waiting on a real human answer")

    assert question == "Honestly, if the opening is still there after the AI screen, what happens next?"
    assert cue == "Still waiting on a real human answer."


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
async def test_prepare_stage_autogenerates_dynamic_council_roster(tmp_path: Path):
    settings = AppSettings(dummy_openai=True, runs_dir=tmp_path).prepare()
    director = build_director(settings)
    state = await director.create_simulation(SimulationCreateRequest(country="Mexico", premise="Start later in a machine-heavy settlement with a sharper focus on public services and household security."))
    await director.wait_for_pending(state.simulation_id)
    loaded = await director.get_simulation(state.simulation_id)

    assert loaded.config.council_roster
    assert loaded.config.council_roster[0].name not in {"Rowan", "Leila", "Mateo", "Amina"}
    assert all(advisor.voice for advisor in loaded.config.council_roster)


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
    assert captured[0]["capture_only"] is True
    assert captured[1]["create_response"] is True
    assert captured[1]["capture_only"] is False
    council_tool_names = [tool["name"] for tool in captured[0]["tools"]]
    solo_tool_names = [tool["name"] for tool in captured[1]["tools"]]
    assert captured[0]["tools"] == []
    assert "Do not answer the player" in str(captured[0]["instructions"])
    assert "report_council_floor" not in council_tool_names
    assert "report_council_floor" not in solo_tool_names


@pytest.mark.asyncio
async def test_council_prompts_push_for_real_arguments_not_slogans(tmp_path: Path):
    settings = AppSettings(dummy_openai=True, runs_dir=tmp_path).prepare()
    director = build_director(settings)
    state = await director.create_simulation(SimulationCreateRequest())
    await director.wait_for_pending(state.simulation_id)
    loaded = await director.get_simulation(state.simulation_id)
    stage = loaded.stages[loaded.active_stage_index]

    live_prompt = director.realtime_prompts.advisor_instructions(
        loaded,
        stage.state_of_world,
        [],
        advisor_mode=AdvisorMode.council,
    )
    turn_prompt = director.realtime_prompts.council_turn_generation_instructions(
        loaded,
        [],
    )

    assert "if a line could fit on a campaign sticker, it is too empty" in live_prompt
    assert "plain speech beats smart-sounding fog" in live_prompt
    assert "This council runs in three moving parts." in turn_prompt
    assert "Do not ask advisors to report urgency." in turn_prompt
    assert "Keep each spoken beat one lane wide, concrete, and ready for audio." in turn_prompt
    assert "Council roster:" in turn_prompt


def test_resolve_asset_file_finds_frontend_bundle_and_run_media(tmp_path: Path):
    settings = AppSettings(dummy_openai=True, runs_dir=tmp_path).prepare()
    run_asset = tmp_path / "sim_test" / "stage-01" / "beat-00.png"
    run_asset.parent.mkdir(parents=True, exist_ok=True)
    run_asset.write_bytes(b"png")

    frontend_root = tmp_path / "web-dist"
    frontend_asset = frontend_root / "assets" / "index-test.js"
    frontend_asset.parent.mkdir(parents=True, exist_ok=True)
    frontend_asset.write_text("console.log('test');", encoding="utf-8")
    frontend_path = resolve_asset_file(frontend_asset.name, settings, web_dist=frontend_root)
    media_path = resolve_asset_file("sim_test/stage-01/beat-00.png", settings)

    assert frontend_path == frontend_asset.resolve()
    assert media_path == run_asset.resolve()


@pytest.mark.asyncio
async def test_debate_realtime_session_uses_town_hall_floor_prompt_when_requested(tmp_path: Path):
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

    session = await director.create_realtime_session(
        loaded.simulation_id,
        RealtimeSessionRequest(role=RealtimeRole.debate, auditorium_mode=AuditoriumMode.town_hall),
    )

    assert session.session_type == "debate"
    assert session.session_variant == "town_hall"
    assert captured[0]["create_response"] is False
    assert captured[0]["capture_only"] is True
    assert "Your only job is to hear the player's answer clearly" in str(captured[0]["instructions"])
    assert "Ignore applause, chair noise, audience murmur" in str(captured[0]["instructions"])


@pytest.mark.asyncio
async def test_generate_town_hall_opponent_reply_persists_opponent_turn(tmp_path: Path):
    settings = AppSettings(dummy_openai=True, runs_dir=tmp_path).prepare()
    director = build_director(settings)
    state = await director.create_simulation(SimulationCreateRequest())
    await director.wait_for_pending(state.simulation_id)
    loaded = await director.get_simulation(state.simulation_id)
    stage = loaded.stages[loaded.active_stage_index]
    citizen = stage.sample_citizens[0]

    await director.generate_town_hall_question(
        loaded.simulation_id,
        TownHallQuestionRequest(citizen_id=citizen.citizen_id, mode="voice"),
    )
    await director.sync_conversation(
        loaded.simulation_id,
        ConversationSyncRequest(
            role=RealtimeRole.debate,
            auditorium_mode=AuditoriumMode.town_hall,
            turns=[ConversationTurnInput(speaker="user", text="I want faster gains, but I also want a backstop if this strips bargaining power.", mode="voice")],
        ),
    )

    async def fake_parse(**kwargs):
        return (
            TownHallOpponentReplyDraft(
                reply="Then build the backstop without freezing the gains. Keep the tools open, force real competition, and make the payout visible when the upside pools upward."
            ),
            None,
        )

    director.gateway.parse = fake_parse  # type: ignore[method-assign]

    response = await director.generate_town_hall_opponent_reply(
        loaded.simulation_id,
        TownHallOpponentReplyRequest(mode="voice"),
    )

    assert response.reply_turn.speaker == "assistant"
    assert response.reply_turn.speaker_name == loaded.config.opponent_name
    assert response.reply_turn.speaker_voice == loaded.config.opponent_voice
    assert "freeze the gains" in response.reply_turn.text.lower() or "freezing the gains" in response.reply_turn.text.lower()


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
async def test_generate_council_turn_commits_transcript_and_board_notes(tmp_path: Path):
    settings = AppSettings(dummy_openai=True, runs_dir=tmp_path).prepare()
    director = build_director(settings)
    state = await director.create_simulation(SimulationCreateRequest(council_roster=_standard_council_roster()))
    await director.wait_for_pending(state.simulation_id)
    loaded = await director.get_simulation(state.simulation_id)

    async def fake_parse(**kwargs):
        text_format = kwargs["text_format"]
        if text_format is CouncilAdvisorDraft:
            return (
                CouncilAdvisorDraft(
                    advisor_key="capacity",
                    advisor_name="Rowan",
                    text="Keep the broad gains open while we watch concentration.",
                    board_notes=["Keep consumer AI open"],
                ),
                "advisor-response-id",
            )
        assert text_format is CouncilSpeakerDecision
        return (
            CouncilSpeakerDecision(
                next_speaker="Rowan",
                reason="capability and buildout",
                yield_after_turn=False,
                board_notes=["Keep consumer AI open"],
                contrast=[],
            ),
            "decider-response-id",
        )

    director.gateway.parse = fake_parse  # type: ignore[method-assign]

    response = await director.generate_council_turn(
        loaded.simulation_id,
        CouncilTurnRequest(text="What does the room think?", mode="voice"),
    )

    updated = await director.get_simulation(loaded.simulation_id)
    assert [turn.text for turn in updated.conversation_threads.get(response.thread_key, [])] == [
        "What does the room think?",
        "Keep the broad gains open while we watch concentration.",
    ]
    assert updated.stages[updated.active_stage_index].policy_notes == ["Keep consumer AI open."]
    assert response.board_notes == ["Keep consumer AI open."]
    assert response.yield_after_turn is False
    assert response.next_speaker == "capacity"
    assert [turn.speaker_name for turn in response.turns] == ["Rowan"]
    assert [turn.text for turn in response.turns] == ["Keep the broad gains open while we watch concentration."]


@pytest.mark.asyncio
async def test_generate_council_turn_can_continue_without_new_player_text(tmp_path: Path):
    settings = AppSettings(dummy_openai=True, runs_dir=tmp_path).prepare()
    director = build_director(settings)
    state = await director.create_simulation(SimulationCreateRequest(council_roster=_standard_council_roster()))
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
        text_format = kwargs["text_format"]
        if text_format is CouncilSpeakerDecision:
            return (
                CouncilSpeakerDecision(
                    next_speaker="Leila",
                    reason="household fairness pressure now matters more",
                    yield_after_turn=True,
                    contrast=[],
                ),
                "decider-response-id",
            )
        if text_format is CouncilAdvisorDraft:
            captured_input_texts.append(str(kwargs["input_text"]))
            return (
                CouncilAdvisorDraft(
                    advisor_key="innovation",
                    advisor_name="Leila",
                    text="Then answer the family question directly, because people will forgive speed before they forgive feeling cut out.",
                ),
                "advisor-response-id",
            )
        raise AssertionError(f"unexpected parse type {text_format}")

    director.gateway.parse = fake_parse  # type: ignore[method-assign]

    response = await director.generate_council_turn(
        loaded.simulation_id,
        CouncilTurnRequest(continue_dialogue=True, mode="voice"),
    )

    assert len(captured_input_texts) == 1
    assert captured_input_texts[0].startswith("Take the next live beat in the current council exchange.")
    assert "The president explicitly wants the room to argue it out." in captured_input_texts[0]
    assert "Speak naturally and directly." in captured_input_texts[0]
    assert response.lead == "Leila"
    assert response.next_speaker == "innovation"
    assert response.yield_after_turn is False
    assert [turn.speaker_name for turn in response.turns] == ["Leila"]
    assert [turn.text for turn in response.turns] == [
        "Then answer the family question directly, because people will forgive speed before they forgive feeling cut out."
    ]


@pytest.mark.asyncio
async def test_generate_council_turn_trims_persisted_council_context(tmp_path: Path):
    settings = AppSettings(dummy_openai=True, runs_dir=tmp_path).prepare()
    director = build_director(settings)
    state = await director.create_simulation(SimulationCreateRequest(council_roster=_standard_council_roster()))
    await director.wait_for_pending(state.simulation_id)
    loaded = await director.get_simulation(state.simulation_id)

    long_turns = []
    for index in range(14):
        if index % 2 == 0:
            long_turns.append(ConversationTurnInput(speaker="user", text=f"Question {index}", mode="voice"))
        else:
            long_turns.append(
                ConversationTurnInput(
                    speaker="assistant",
                    speaker_name="Rowan",
                    speaker_voice="cedar",
                    text=f"Reply {index}",
                    mode="voice",
                )
            )

    sync = await director.sync_conversation(
        loaded.simulation_id,
        ConversationSyncRequest(
            role=RealtimeRole.advisor,
            advisor_mode=AdvisorMode.council,
            turns=long_turns,
        ),
    )

    async def fake_parse(**kwargs):
        text_format = kwargs["text_format"]
        if text_format is CouncilSpeakerDecision:
            return (
                CouncilSpeakerDecision(
                    next_speaker="Rowan",
                    reason="Rowan has the clearest next beat.",
                    yield_after_turn=False,
                    contrast=[],
                ),
                "decider-response-id",
            )
        if text_format is CouncilAdvisorDraft:
            return (
                CouncilAdvisorDraft(
                    advisor_key="capacity",
                    advisor_name="Rowan",
                    text="Start with the cheap gains people already feel.",
                ),
                "advisor-response-id",
            )
        raise AssertionError(f"unexpected parse type {text_format}")

    director.gateway.parse = fake_parse  # type: ignore[method-assign]

    response = await director.generate_council_turn(
        loaded.simulation_id,
        CouncilTurnRequest(
            text="What is the first thing I should say back to them?",
            mode="voice",
            provisional_turns=[
                ConversationTurnInput(
                    speaker=turn.speaker,
                    speaker_name=turn.speaker_name,
                    speaker_voice=turn.speaker_voice,
                    text=turn.text,
                    mode=turn.mode,
                )
                for turn in (sync.simulation.conversation_threads.get(sync.thread_key, []))
            ],
        ),
    )

    updated = await director.get_simulation(loaded.simulation_id)
    thread = updated.conversation_threads[response.thread_key]
    assert len(thread) == director._COUNCIL_CONTEXT_TURN_LIMIT
    assert thread[-2].text == "What is the first thing I should say back to them?"
    assert thread[-1].text == "Start with the cheap gains people already feel."
    assert thread[0].text == "Question 4"


@pytest.mark.asyncio
async def test_generate_council_turn_explicit_disagreement_adds_stronger_room_fight_guidance(tmp_path: Path):
    settings = AppSettings(dummy_openai=True, runs_dir=tmp_path).prepare()
    director = build_director(settings)
    state = await director.create_simulation(SimulationCreateRequest(council_roster=_standard_council_roster()))
    await director.wait_for_pending(state.simulation_id)
    loaded = await director.get_simulation(state.simulation_id)

    captured: dict[str, object] = {}

    async def fake_parse(**kwargs):
        text_format = kwargs["text_format"]
        captured.setdefault("max_output_tokens", []).append(kwargs["max_output_tokens"])
        if text_format is CouncilSpeakerDecision:
            captured["decider_input_text"] = kwargs["input_text"]
            captured["decider_instructions"] = kwargs["instructions"]
            return (
                CouncilSpeakerDecision(
                    next_speaker="Rowan",
                    reason="Rowan opens and Leila should answer next",
                    yield_after_turn=False,
                    contrast=["Leila"],
                ),
                "decider-response-id",
            )
        if text_format is CouncilAdvisorDraft:
            captured.setdefault("candidate_instructions", []).append(kwargs["instructions"])
            captured.setdefault("candidate_input_texts", []).append(kwargs["input_text"])
            return (
                CouncilAdvisorDraft(
                    advisor_key="capacity",
                    advisor_name="Rowan",
                    text="If we freeze deployment into administered pricing first, we lock scarce capacity into the incumbents already sitting on the best systems.",
                ),
                "advisor-response-id",
            )
        raise AssertionError(f"unexpected parse type {text_format}")

    director.gateway.parse = fake_parse  # type: ignore[method-assign]

    response = await director.generate_council_turn(
        loaded.simulation_id,
        CouncilTurnRequest(
            text="I want the room to really disagree. Let the labor and industry advisers argue about whether to spread machine capacity through public utility pricing or market competition first.",
            mode="voice",
        ),
    )

    assert captured["max_output_tokens"] == [96, 120]
    assert len(captured["candidate_input_texts"]) == 1
    assert "The president explicitly wants the room to argue it out." in str(captured["candidate_input_texts"][0])
    assert "Speak naturally and directly." in str(captured["candidate_input_texts"][0])
    assert "You already have the floor." in str(captured["candidate_instructions"][0])
    assert "Because you already have the floor, default to a real spoken line instead of yielding." in str(captured["candidate_instructions"][0])
    assert "If the player clearly asked for a poll, a room move, or a board change" in str(captured["candidate_instructions"][0])
    assert "You are the floor arbiter for a live strategy council." in str(captured["decider_instructions"])
    assert "Choose the next floor owner now." in str(captured["decider_input_text"])
    assert [turn.speaker_name for turn in response.turns] == ["Rowan"]
    assert response.contrast == ["Leila"]


@pytest.mark.asyncio
async def test_generate_council_turn_uses_dynamic_roster(tmp_path: Path):
    settings = AppSettings(dummy_openai=True, runs_dir=tmp_path).prepare()
    director = build_director(settings)
    state = await director.create_simulation(
        SimulationCreateRequest(
            council_roster=[
                CouncilAdvisorProfile(
                    key="growth",
                    name="Nova",
                    room_role="Innovation",
                    country_role="frontier diffusion advisor",
                    remit="tracks product diffusion, tooling, and how ordinary households see gains",
                    voice="marin",
                ),
                CouncilAdvisorProfile(
                    key="stability",
                    name="Iris",
                    room_role="State",
                    country_role="resilience and public-services advisor",
                    remit="tracks resilience, public trust, and execution risk",
                    voice="shimmer",
                ),
            ]
        )
    )
    await director.wait_for_pending(state.simulation_id)
    loaded = await director.get_simulation(state.simulation_id)

    async def fake_parse(**kwargs):
        text_format = kwargs["text_format"]
        instructions = str(kwargs["instructions"])
        if text_format is CouncilSpeakerDecision:
            return (
                CouncilSpeakerDecision(
                    next_speaker="growth",
                    reason="Nova best advances the visible gain",
                    contrast=["stability"],
                ),
                "decider-response-id",
            )
        if text_format is CouncilAdvisorDraft:
            if "Nova" in instructions:
                return (
                    CouncilAdvisorDraft(
                        advisor_key="growth",
                        advisor_name="Nova",
                        text="Keep the new capability flowing so households and small firms actually feel it.",
                    ),
                    "advisor-response-id",
                )
            return (
                CouncilAdvisorDraft(
                    advisor_key="stability",
                    advisor_name="Iris",
                    text="",
                ),
                "advisor-response-id",
            )
        raise AssertionError(f"unexpected parse type {text_format}")

    director.gateway.parse = fake_parse  # type: ignore[method-assign]

    response = await director.generate_council_turn(
        loaded.simulation_id,
        CouncilTurnRequest(text="What should we do first?", mode="text"),
    )

    assert response.next_speaker == "growth"
    assert response.lead == "Nova"
    assert [turn.speaker_name for turn in response.turns] == ["Nova"]
    assert [turn.speaker_name for turn in response.turns] == ["Nova"]
    assert response.turns[0].speaker_voice == "marin"


@pytest.mark.asyncio
async def test_generate_council_turn_keeps_provisional_advisor_context_on_new_user_turn(tmp_path: Path):
    settings = AppSettings(dummy_openai=True, runs_dir=tmp_path).prepare()
    director = build_director(settings)
    state = await director.create_simulation(
        SimulationCreateRequest(
            council_roster=[
                CouncilAdvisorProfile(
                    key="growth",
                    name="Nova",
                    room_role="Innovation",
                    country_role="frontier diffusion advisor",
                    remit="tracks product diffusion, tooling, and how ordinary households see gains",
                    voice="marin",
                ),
                CouncilAdvisorProfile(
                    key="stability",
                    name="Iris",
                    room_role="State",
                    country_role="resilience and public-services advisor",
                    remit="tracks resilience, public trust, and execution risk",
                    voice="shimmer",
                ),
                CouncilAdvisorProfile(
                    key="politics",
                    name="Mateo",
                    room_role="Politics",
                    country_role="coalition and public mandate advisor",
                    remit="tracks coalition mood, rhetoric, and debate risk",
                    voice="ash",
                ),
            ]
        )
    )
    await director.wait_for_pending(state.simulation_id)
    loaded = await director.get_simulation(state.simulation_id)

    captured: dict[str, list[str]] = {"candidate_instructions": []}

    async def fake_parse(**kwargs):
        text_format = kwargs["text_format"]
        instructions = str(kwargs["instructions"])
        if text_format is CouncilSpeakerDecision:
            return (
                CouncilSpeakerDecision(
                    next_speaker="growth",
                    reason="Nova has the clean next beat",
                ),
                "decider-response-id",
            )
        if text_format is CouncilAdvisorDraft:
            captured["candidate_instructions"].append(instructions)
            return (
                CouncilAdvisorDraft(
                    advisor_key="growth",
                    advisor_name="Nova",
                    text="Keep the lines open where households are already seeing the gain.",
                ),
                "advisor-response-id",
            )
        raise AssertionError(f"unexpected parse type {text_format}")

    director.gateway.parse = fake_parse  # type: ignore[method-assign]

    provisional_turns = [
        ConversationTurnInput(
            speaker="assistant",
            speaker_name="Iris",
            speaker_voice="shimmer",
            text="If the grid and procurement stay brittle, the visible gains will not hold.",
            mode="voice",
        )
    ]
    await director.generate_council_turn(
        loaded.simulation_id,
        CouncilTurnRequest(
            text="So where do you want to push first?",
            mode="voice",
            provisional_turns=provisional_turns,
        ),
    )

    assert captured["candidate_instructions"]
    assert "Most recent spoken advisor line:\n- If the grid and procurement stay brittle, the visible gains will not hold." in captured["candidate_instructions"][0]


def test_council_turn_helpers_detect_room_fights_and_trailing_beats(tmp_path: Path):
    settings = AppSettings(dummy_openai=True, runs_dir=tmp_path).prepare()
    director = build_director(settings)

    assert director._turn_requests_council_fight("Let the room debate among yourselves for a minute.")
    assert director._turn_requests_council_fight("I want the room to really disagree.")
    assert not director._turn_requests_council_fight("Keep this simple.")

    trailing_count = director._trailing_council_advisor_beats(
        [
            director._assistant_turns_from_council_plan(  # type: ignore[attr-defined]
                CouncilTurnPlan(
                    lead="Rowan",
                    reason="demo",
                    advisors=[
                        CouncilAdvisorBeat(name="Rowan", urgency=9, speak=True, text="Keep the buildout moving or the rents stay private."),
                        CouncilAdvisorBeat(name="Leila", urgency=8, speak=False, text=""),
                        CouncilAdvisorBeat(name="Mateo", urgency=4, speak=False, text=""),
                        CouncilAdvisorBeat(name="Amina", urgency=3, speak=False, text=""),
                    ],
                ),
                "text",
            )[0],
            director._assistant_turns_from_council_plan(  # type: ignore[attr-defined]
                CouncilTurnPlan(
                    lead="Leila",
                    reason="demo",
                    advisors=[
                        CouncilAdvisorBeat(name="Leila", urgency=9, speak=True, text="Then say who pays while you wait for that competition to show up."),
                        CouncilAdvisorBeat(name="Rowan", urgency=6, speak=False, text=""),
                        CouncilAdvisorBeat(name="Mateo", urgency=4, speak=False, text=""),
                        CouncilAdvisorBeat(name="Amina", urgency=3, speak=False, text=""),
                    ],
                ),
                "text",
            )[0],
        ]
    )
    assert trailing_count == 2


@pytest.mark.asyncio
async def test_targeted_council_roster_prefers_named_advisor(tmp_path: Path):
    settings = AppSettings(dummy_openai=True, runs_dir=tmp_path).prepare()
    director = build_director(settings)
    state = await director.create_simulation(SimulationCreateRequest(council_roster=_standard_council_roster()))

    roster = director._targeted_council_roster(
        state,
        [
            ConversationTurn(
                speaker="user",
                text="Leila, what does the compute and robotics bottleneck actually look like here?",
                mode="text",
            )
        ],
    )

    assert roster is not None
    assert [advisor.key for advisor in roster] == ["innovation"]


@pytest.mark.asyncio
async def test_targeted_council_roster_prefers_semantic_match_when_question_is_specific(tmp_path: Path):
    settings = AppSettings(dummy_openai=True, runs_dir=tmp_path).prepare()
    director = build_director(settings)
    state = await director.create_simulation(SimulationCreateRequest(council_roster=_standard_council_roster()))

    roster = director._targeted_council_roster(
        state,
        [
            ConversationTurn(
                speaker="user",
                text="What happens to prices and household purchasing power if we slow this down too hard?",
                mode="text",
            )
        ],
    )

    assert roster is not None
    assert [advisor.key for advisor in roster] == ["capacity"]


@pytest.mark.asyncio
async def test_targeted_council_roster_can_narrow_to_top_two_semantic_matches(tmp_path: Path):
    settings = AppSettings(dummy_openai=True, runs_dir=tmp_path).prepare()
    director = build_director(settings)
    state = await director.create_simulation(SimulationCreateRequest(council_roster=_standard_council_roster()))

    roster = director._targeted_council_roster(
        state,
        [
            ConversationTurn(
                speaker="user",
                text="What is happening to purchasing power, and how is the coalition mood moving?",
                mode="text",
            )
        ],
    )

    assert roster is not None
    assert [advisor.key for advisor in roster] == ["capacity", "politics"]


@pytest.mark.asyncio
async def test_generate_council_turn_continuation_uses_floor_decider_and_single_speaker_path(tmp_path: Path):
    settings = AppSettings(dummy_openai=True, runs_dir=tmp_path).prepare()
    director = build_director(settings)
    state = await director.create_simulation(SimulationCreateRequest(council_roster=_standard_council_roster()))
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
                    text="If we freeze the rollout here, the gains stay trapped with the incumbents already sitting on the best systems.",
                    mode="voice",
                ),
            ],
        ),
    )

    parse_calls: list[object] = []

    async def fake_parse(**kwargs):
        text_format = kwargs["text_format"]
        parse_calls.append(text_format)
        if text_format is CouncilSpeakerDecision:
            return (
                CouncilSpeakerDecision(
                    next_speaker="innovation",
                    reason="Leila has the next live beat.",
                    yield_after_turn=False,
                    board_notes=["Keep frontier access open"],
                ),
                "decider-response-id",
            )
        if text_format is CouncilAdvisorDraft:
            return (
                CouncilAdvisorDraft(
                    advisor_key="innovation",
                    advisor_name="Leila",
                    text="Then say which public interface keeps frontier access open instead of rationing it through the same few firms.",
                ),
                "advisor-response-id",
            )
        raise AssertionError(f"unexpected parse type {text_format}")

    director.gateway.parse = fake_parse  # type: ignore[method-assign]

    response = await director.generate_council_turn(
        loaded.simulation_id,
        CouncilTurnRequest(continue_dialogue=True, mode="voice"),
    )

    assert parse_calls.count(CouncilAdvisorDraft) == 1
    assert parse_calls.count(CouncilSpeakerDecision) == 1
    assert response.lead == "Leila"
    assert response.next_speaker == "innovation"
    assert response.yield_after_turn is False
    assert response.board_notes == ["Keep frontier access open."]
    assert [turn.speaker_name for turn in response.turns] == ["Leila"]


@pytest.mark.asyncio
async def test_council_candidate_roster_broadens_on_continuation_after_first_advisor_beat(tmp_path: Path):
    settings = AppSettings(dummy_openai=True, runs_dir=tmp_path).prepare()
    director = build_director(settings)
    state = await director.create_simulation(SimulationCreateRequest(council_roster=_standard_council_roster()))

    roster = director._council_candidate_roster(
        state,
        [
            ConversationTurn(
                speaker="user",
                text="What happens to prices and household purchasing power if we slow this down too hard?",
                mode="text",
            ),
            ConversationTurn(
                speaker="assistant",
                speaker_name="Rowan",
                speaker_voice="cedar",
                text="If you slow the buildout too hard, households lose the cheap help before they gain a new income floor.",
                mode="voice",
            ),
        ],
        continue_dialogue=True,
        trailing_advisor_beats=1,
    )

    assert [advisor.key for advisor in roster] == ["innovation", "politics", "state"]


@pytest.mark.asyncio
async def test_generate_council_turn_room_fight_continuation_does_not_yield_too_early(tmp_path: Path):
    settings = AppSettings(dummy_openai=True, runs_dir=tmp_path).prepare()
    director = build_director(settings)
    state = await director.create_simulation(SimulationCreateRequest(council_roster=_standard_council_roster()))
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
                    text="If we freeze diffusion now, the visible gains stay trapped with the firms already holding the best systems.",
                    mode="voice",
                ),
            ],
        ),
    )

    async def fake_parse(**kwargs):
        text_format = kwargs["text_format"]
        if text_format is CouncilSpeakerDecision:
            return (
                CouncilSpeakerDecision(
                    next_speaker="innovation",
                    reason="Leila still carries the clearest unresolved objection.",
                    yield_after_turn=False,
                    contrast=[],
                ),
                "decider-response-id",
            )
        if text_format is CouncilAdvisorDraft:
            instructions = str(kwargs["instructions"])
            if "Leila" in instructions:
                return (
                    CouncilAdvisorDraft(
                        advisor_key="innovation",
                        advisor_name="Leila",
                        text="Then say what keeps access broad, because a slowdown without a public on-ramp just hardens the same private choke points.",
                    ),
                    "advisor-response-id",
                )
            return (
                CouncilAdvisorDraft(
                    advisor_key="capacity",
                    advisor_name="Rowan",
                    text="",
                ),
                "advisor-response-id",
            )
        raise AssertionError(f"unexpected parse type {text_format}")

    director.gateway.parse = fake_parse  # type: ignore[method-assign]

    response = await director.generate_council_turn(
        loaded.simulation_id,
        CouncilTurnRequest(continue_dialogue=True, mode="voice"),
    )

    assert response.next_speaker == "innovation"
    assert response.lead == "Leila"
    assert response.yield_after_turn is False
    assert [turn.speaker_name for turn in response.turns] == ["Leila"]


def test_orchestrator_normalize_council_roster_realigns_common_name_voice_pairs(tmp_path: Path):
    settings = AppSettings(dummy_openai=True, runs_dir=tmp_path).prepare()
    director = build_director(settings)

    normalized = director.orchestrator._normalize_council_roster([
        CouncilAdvisorProfile(
            key="darius",
            name="Darius",
            room_role="Industry",
            country_role="industrial strategy advisor",
            remit="tracks frontier deployment into the real economy",
            voice="shimmer",
            viewpoint="pushes diffusion",
        ),
        CouncilAdvisorProfile(
            key="maya",
            name="Maya",
            room_role="Security",
            country_role="security advisor",
            remit="tracks critical infrastructure and state resilience",
            voice="ash",
            viewpoint="pushes risk containment",
        ),
    ])

    assert normalized[0].voice in {"cedar", "ash", "verse"}
    assert normalized[1].voice in {"marin", "shimmer", "sage"}


@pytest.mark.asyncio
async def test_generate_town_hall_question_persists_to_town_hall_thread(tmp_path: Path):
    settings = AppSettings(dummy_openai=True, runs_dir=tmp_path).prepare()
    director = build_director(settings)
    state = await director.create_simulation(SimulationCreateRequest())
    await director.wait_for_pending(state.simulation_id)
    loaded = await director.get_simulation(state.simulation_id)
    target_citizen = loaded.stages[loaded.active_stage_index].sample_citizens[0]
    target_citizen.town_hall_question = ""
    target_citizen.town_hall_cue = ""
    await director.store.save(loaded)

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
        TownHallQuestionRequest(citizen_id=target_citizen.citizen_id, mode="voice"),
    )

    updated = await director.get_simulation(loaded.simulation_id)
    persisted_turns = updated.conversation_threads.get(response.thread_key, [])
    assert response.thread_key.endswith(":debate:town_hall")
    assert len(persisted_turns) == 1
    assert persisted_turns[0].speaker == "assistant"
    assert persisted_turns[0].speaker_name == target_citizen.display_name
    assert persisted_turns[0].text == "If AI handles more of the screen work, what does your plan do for people like me?"
    assert response.question_turn.text == "If AI handles more of the screen work, what does your plan do for people like me?"

@pytest.mark.asyncio
async def test_generate_town_hall_question_prefers_seeded_citizen_question(tmp_path: Path):
    settings = AppSettings(dummy_openai=True, runs_dir=tmp_path).prepare()
    director = build_director(settings)
    state = await director.create_simulation(SimulationCreateRequest())
    await director.wait_for_pending(state.simulation_id)
    loaded = await director.get_simulation(state.simulation_id)
    stage = loaded.stages[loaded.active_stage_index]
    stage.sample_citizens[0].town_hall_question = "My civic account covers rent, but who fixes it when the system freezes me out?"
    stage.sample_citizens[0].town_hall_cue = "dependency on civic accounts"
    await director.store.save(loaded)

    async def fake_parse(**kwargs):
        raise AssertionError("seeded town-hall questions should not re-enter the model path")

    director.gateway.parse = fake_parse  # type: ignore[method-assign]

    response = await director.generate_town_hall_question(
        loaded.simulation_id,
        TownHallQuestionRequest(citizen_id=stage.sample_citizens[0].citizen_id, mode="voice"),
    )

    assert response.question_turn.text == "My public account covers rent, but who fixes it when the system freezes me out?"
    assert response.cue == "dependency on public accounts."


@pytest.mark.asyncio
async def test_generate_town_hall_question_falls_back_and_persists_to_citizen_snapshot(tmp_path: Path):
    settings = AppSettings(dummy_openai=True, runs_dir=tmp_path).prepare()
    director = build_director(settings)
    state = await director.create_simulation(SimulationCreateRequest())
    await director.wait_for_pending(state.simulation_id)
    loaded = await director.get_simulation(state.simulation_id)
    stage = loaded.stages[loaded.active_stage_index]
    stage.sample_citizens[0].town_hall_question = ""
    stage.sample_citizens[0].town_hall_cue = ""
    stage.sample_citizens[0].current_worries = "My public account pays most of our rent, but it freezes long enough to blow up the week."
    await director.store.save(loaded)

    async def fail_parse(**kwargs):
        raise RuntimeError("model unavailable")

    director.gateway.parse = fail_parse  # type: ignore[method-assign]

    response = await director.generate_town_hall_question(
        loaded.simulation_id,
        TownHallQuestionRequest(citizen_id=stage.sample_citizens[0].citizen_id, mode="voice"),
    )

    updated = await director.get_simulation(loaded.simulation_id)
    refreshed_citizen = updated.stages[updated.active_stage_index].sample_citizens[0]
    assert response.question_turn.text == "My public account pays most of our rent, but it freezes long enough to blow up the week. What would change for people like me next month?"
    assert refreshed_citizen.town_hall_question == response.question_turn.text
    assert refreshed_citizen.town_hall_cue


@pytest.mark.asyncio
async def test_generate_town_hall_question_cleans_adjacent_word_repetition(tmp_path: Path):
    settings = AppSettings(dummy_openai=True, runs_dir=tmp_path).prepare()
    director = build_director(settings)
    state = await director.create_simulation(SimulationCreateRequest())
    await director.wait_for_pending(state.simulation_id)
    loaded = await director.get_simulation(state.simulation_id)
    stage = loaded.stages[loaded.active_stage_index]
    stage.sample_citizens[0].town_hall_question = ""
    stage.sample_citizens[0].town_hall_cue = ""
    await director.store.save(loaded)

    async def fake_parse(**kwargs):
        return (
            TownHallQuestionDraft(
                question="If the opening is still still there by the time my son finishes school, what are you changing first",
                cue="jobs still still thin out",
            ),
            "response-id",
        )

    director.gateway.parse = fake_parse  # type: ignore[method-assign]

    response = await director.generate_town_hall_question(
        loaded.simulation_id,
        TownHallQuestionRequest(citizen_id=stage.sample_citizens[0].citizen_id, mode="voice"),
    )

    assert response.question_turn.text == "If the opening is still there by the time my son finishes school, what are you changing first?"
    assert response.cue == "jobs still thin out."


@pytest.mark.asyncio
async def test_generate_town_hall_question_falls_back_when_model_line_ends_dangling(tmp_path: Path):
    settings = AppSettings(dummy_openai=True, runs_dir=tmp_path).prepare()
    director = build_director(settings)
    state = await director.create_simulation(SimulationCreateRequest())
    await director.wait_for_pending(state.simulation_id)
    loaded = await director.get_simulation(state.simulation_id)
    stage = loaded.stages[loaded.active_stage_index]
    stage.sample_citizens[0].town_hall_question = ""
    stage.sample_citizens[0].current_worries = "My monthly AI payment covers rent, but two platforms can still raise prices overnight."
    await director.store.save(loaded)

    async def fake_parse(**kwargs):
        return (
            TownHallQuestionDraft(
                question="Honestly, what are you going to do so a few AI companies can't just jack up",
                cue="prices can still jump",
            ),
            "response-id",
        )

    director.gateway.parse = fake_parse  # type: ignore[method-assign]

    response = await director.generate_town_hall_question(
        loaded.simulation_id,
        TownHallQuestionRequest(citizen_id=stage.sample_citizens[0].citizen_id, mode="voice"),
    )

    assert response.question_turn.text == "My monthly AI payment covers rent, but two platforms can still raise prices overnight. What would change for people like me next month?"


@pytest.mark.asyncio
async def test_generate_town_hall_question_uses_seed_question_when_live_rewrite_breaks(tmp_path: Path):
    settings = AppSettings(dummy_openai=True, runs_dir=tmp_path).prepare()
    director = build_director(settings)
    state = await director.create_simulation(SimulationCreateRequest())
    await director.wait_for_pending(state.simulation_id)
    loaded = await director.get_simulation(state.simulation_id)
    stage = loaded.stages[loaded.active_stage_index]
    stage.sample_citizens[0].town_hall_question = "What is the guaranteed way to get a real person to review my case before the opening is gone?"
    await director.store.save(loaded)

    async def fake_parse(**kwargs):
        raise AssertionError("seeded town-hall questions should not re-enter the model path")

    director.gateway.parse = fake_parse  # type: ignore[method-assign]

    response = await director.generate_town_hall_question(
        loaded.simulation_id,
        TownHallQuestionRequest(citizen_id=stage.sample_citizens[0].citizen_id, mode="voice"),
    )

    assert response.question_turn.text == "What is the guaranteed way to get a real person to review my case before the opening is gone?"


@pytest.mark.asyncio
async def test_generate_town_hall_question_ignores_clipped_seed_and_uses_fallback(tmp_path: Path):
    settings = AppSettings(dummy_openai=True, runs_dir=tmp_path).prepare()
    director = build_director(settings)
    state = await director.create_simulation(SimulationCreateRequest())
    await director.wait_for_pending(state.simulation_id)
    loaded = await director.get_simulation(state.simulation_id)
    stage = loaded.stages[loaded.active_stage_index]
    stage.sample_citizens[0].town_hall_question = "How are you going to stop a few AI companies from jacking up the price later once we're all stuck using?"
    stage.sample_citizens[0].current_worries = "My monthly AI payment covers rent, but two platforms can still raise prices overnight."
    await director.store.save(loaded)

    async def fake_parse(**kwargs):
        return (
            TownHallQuestionDraft(
                question="What are you actually going to do so a few AI companies can't just jack",
                cue="platform pricing still feels fragile",
            ),
            "response-id",
        )

    director.gateway.parse = fake_parse  # type: ignore[method-assign]

    response = await director.generate_town_hall_question(
        loaded.simulation_id,
        TownHallQuestionRequest(citizen_id=stage.sample_citizens[0].citizen_id, mode="voice"),
    )

    assert response.question_turn.text == "My monthly AI payment covers rent, but two platforms can still raise prices overnight. What would change for people like me next month?"
