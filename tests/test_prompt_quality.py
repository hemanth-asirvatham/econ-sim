from __future__ import annotations

from pathlib import Path
from types import MethodType

import pytest

from econ_sim.app import build_director
from econ_sim.config import AppSettings
from econ_sim.models import AdvisorMode, ConversationTurn, PollSummary, RealtimeRole, RealtimeSessionRequest, ResolveStageRequest, SimulationCreateRequest


@pytest.mark.asyncio
async def test_advisor_and_debate_prompts_include_operational_context(tmp_path: Path):
    settings = AppSettings(dummy_openai=True, runs_dir=tmp_path).prepare()
    director = build_director(settings)
    state = await director.create_simulation(SimulationCreateRequest())
    await director.wait_for_pending(state.simulation_id)
    loaded = await director.get_simulation(state.simulation_id)
    stage = loaded.stages[loaded.active_stage_index]
    assert stage.montage_logline

    setup_session = await director.create_setup_session()
    setup_prompt = director.realtime_prompts.setup_instructions(setup_session, [])
    advisor_prompt = director.realtime_prompts.advisor_instructions(loaded, stage.state_of_world, [])
    strategy_prompt = director.realtime_prompts.advisor_instructions(
        loaded,
        stage.state_of_world,
        [ConversationTurn(speaker="user", text="What options should we test, who should I talk to, and what belongs on the board?")],
    )
    citizen_prompt = director.realtime_prompts.citizen_instructions(loaded, stage.sample_citizens[0], [])
    debate_prompt = director.realtime_prompts.debate_instructions(loaded, [])
    council_prompt = director.realtime_prompts.advisor_instructions(
        loaded,
        stage.state_of_world,
        [ConversationTurn(speaker="user", text="What options should the room test and what belongs on the board?", mode="voice")],
        advisor_mode=AdvisorMode.council,
    )

    assert "If the player asks how play works, answer in one practical line" in setup_prompt
    assert "broad national" in setup_prompt
    assert "no extra lens unless they ask for one" in setup_prompt
    assert "Keep replies short and conversational" in setup_prompt
    assert "Lead with what AI newly makes easier, cheaper, or more capable before you turn to strain" in director.orchestrator._stage_instructions(loaded.config)
    assert "Give the upside enough room that the audience understands why adoption kept spreading, then turn to strain." in director.orchestrator._stage_instructions(loaded.config)
    assert "capability frontier, diffusion, the broad economic read" in director.orchestrator._stage_instructions(loaded.config)
    assert "Do not let the national story read like a productivity audit or a running case against AI" in director.orchestrator._stage_instructions(loaded.config)
    assert "Keep office churn from standing in for the whole economy" in director.orchestrator._stage_instructions(loaded.config)
    assert "social change" in director.orchestrator._stage_instructions(loaded.config)
    assert "productivity audit" in director.orchestrator._stage_instructions(loaded.config)
    assert "The montage should feel like one coherent documentary voiceover with a clear throughline" in director.orchestrator._stage_instructions(loaded.config)
    assert "A strong opening paragraph usually does five jobs in order" in director.orchestrator._stage_instructions(loaded.config)
    assert "Positive change can mean abundance, autonomy, confidence" in director.orchestrator._stage_instructions(loaded.config)
    assert "social change too" in director.orchestrator._montage_instructions()
    assert "The best early macro line often sounds like a serious newspaper lead" in director.orchestrator._montage_instructions()
    assert "one positive social effect outside office work" in director.orchestrator._montage_instructions()
    assert "Quick read:" in strategy_prompt
    assert "Citizen worth visiting:" in strategy_prompt
    assert "Working policy board:" in strategy_prompt
    assert "Most replies should be 1 short sentence" in advisor_prompt
    assert "one broad capability and one practical consequence" in advisor_prompt
    assert "Use plain words like bills, hiring, prices, outages, pay, care, school, and votes." in advisor_prompt
    assert "Do not default to a regulation pitch" in advisor_prompt
    assert "Do not keep snapping back to wait times, paperwork, or office churn" in advisor_prompt
    assert "If the player is asking what is happening in the country, answer the country question first." in advisor_prompt
    assert "If the player asks a broad world question, answer with one macro read and one lived consequence" in advisor_prompt
    assert "If the player sounds exploratory" in advisor_prompt
    assert "If you need more detail, call get_world_briefing, run a poll, or send them to a citizen." in advisor_prompt
    assert "Only update the policy board when asked" in advisor_prompt
    assert "Board labels must stay short and concrete" in advisor_prompt
    assert "Recent conversation context:" not in advisor_prompt
    assert "Reply in 1 short sentence by default, sometimes 2" in citizen_prompt
    assert "Talk in first person and start from my own life" in citizen_prompt
    assert "Many early-stage citizens should have no strong AI ideology at all" in citizen_prompt
    assert "If asked something broad, answer from one thing I actually saw or dealt with and stop there unless the player follows up." in citizen_prompt
    assert "If AI is part of the story, anchor broad answers" in citizen_prompt
    assert "If AI is not the live thing, stay with the rent, shift, school issue, family routine, or one normal week instead." in citizen_prompt
    assert "Do not volunteer an AI take every turn." in citizen_prompt
    assert "If AI is not salient in the moment, let it stay implied or absent." in citizen_prompt
    assert "Your durable campaign themes:" in debate_prompt
    assert "Usually answer in 2 short sentences" in debate_prompt
    assert "If the player leans restrictive, taxed-up, paused, or permission-heavy, sound like a real pro-diffusion rival" in debate_prompt
    assert "When your lane is pro-capability, argue from concrete gains people already use or want soon" in debate_prompt
    assert "Do not accept the player's premise and merely trim it" in debate_prompt
    assert "If you concede a point, keep the concession short" in debate_prompt
    assert "small council of senior advisors in a live room" in council_prompt
    assert "Speak like people in a private strategy meeting" in council_prompt
    assert "usually only 1 advisor speaks; sometimes 2 if a real tradeoff matters" in council_prompt
    assert "different specialists" in council_prompt
    assert "only the best-placed advisor should answer" in council_prompt
    assert "the player can interrupt at any time" in council_prompt
    assert "if the player asks what you think, answer like a live cabinet meeting" in council_prompt
    assert "when more than one advisor speaks, prefix each line with the advisor's first name" in council_prompt
    assert "do not do round-robin recap, moderator narration, theatrical bickering" in council_prompt
    assert "answer the country's problem first, not the room's process" in council_prompt
    assert "do not produce tool-call-looking JSON, stage directions, or bracketed process chatter" in council_prompt
    assert "do not let the room default to wait times, office churn, or junior ladders" in council_prompt
    assert "should usually write any missing wording and use run_poll_now right away" in council_prompt
    assert "Stage policy lanes worth debating:" in council_prompt
    assert "report_council_floor" not in council_prompt


@pytest.mark.asyncio
async def test_stage_prompt_uses_macro_first_documentary_contract(tmp_path: Path):
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
    resolved = await director.resolve_stage(
        loaded.simulation_id,
        ResolveStageRequest(player_platform="Keep consumer AI open, speed grid buildout, and expand wage insurance."),
    )
    previous_stage = resolved.stages[0]
    phase = director.orchestrator._phase_brief(resolved.active_stage_index, resolved.config.stage_count)

    prompt = director.orchestrator._stage_prompt(
        config=resolved.config,
        stage_index=resolved.active_stage_index,
        stage_count=resolved.config.stage_count,
        phase=phase,
        previous_stage=previous_stage,
        tracking=previous_stage.tracking,
        poll_summaries=previous_stage.poll_summaries,
        player_in_power=resolved.player_in_power,
        incumbent_name=resolved.incumbent_name,
        queued_poll_questions=[],
    )

    assert "one montage logline of about 18-32 words" not in prompt
    assert "one montage logline of about 18-28 words" in prompt
    assert "return 8-10 narrative beats" not in prompt
    assert "return 7-8 narrative beats" not in prompt
    assert "the beats must read as one coherent documentary passage" not in prompt
    assert "the full voiceover should land around 150-210 words total" not in prompt
    assert "at least half the beats should use zero commas" not in prompt
    assert "Agenda that took effect:" in prompt
    assert "Capability frontier, Economic picture, Households and politics, Still not true yet" in prompt
    assert "the world-state paragraph must open with 4 clean macro sentences in this order" in prompt
    assert "the world-state paragraph must include at least 4 concrete macro cues" in prompt
    assert "begin with the national picture" in prompt
    assert "the opening should move in a short script arc: capability first, then spread, then lived gain, then constraint, then the split" in prompt
    assert "do not write final montage beats or image prompts here" in prompt
    assert "comma-heavy inventory feel and no paragraph that sounds like a list of talking points" in prompt
    assert "Gains that stuck:" in prompt
    assert "Open question now:" in prompt
    assert "Previous player platform:" not in prompt
    assert "Prefer short declarative sentences over stacked clauses. One claim per beat is the rule." in director.orchestrator._montage_instructions()
    assert "Let the first 3 beats establish why people want the tools before the first serious limit arrives." in director.orchestrator._montage_instructions()
    assert "At least one early beat should say what the economy now feels like in broad terms" in director.orchestrator._montage_instructions()
    assert "Keep spoken lines compact enough for clean voiceover delivery, usually about 14-22 words" in director.orchestrator._montage_instructions()
    assert "Each beat should sound like one full line from a documentary narrator" in director.orchestrator._stage_instructions(loaded.config)
    assert "Do not spend tokens writing final documentary beats in this pass" in director.orchestrator._stage_instructions(loaded.config)


def test_later_stage_prompt_demands_named_sectoral_change(tmp_path: Path):
    settings = AppSettings(dummy_openai=True, runs_dir=tmp_path).prepare()
    director = build_director(settings)
    config = SimulationCreateRequest().model_dump()
    phase = director.orchestrator._phase_brief(3, 5)

    prompt = director.orchestrator._stage_prompt(
        config=SimulationCreateRequest(**config),
        stage_index=3,
        stage_count=5,
        phase=phase,
        previous_stage=None,
        tracking=None,
        poll_summaries=[],
        player_in_power=True,
        incumbent_name="President Lena Park",
        queued_poll_questions=[],
    )

    assert "name at least 3 sectors or institutions being reshaped and at least 2 that are still lagging, protected, or bottlenecked" in prompt
    assert "later stages should feel materially different from stage 1" in prompt
    assert "one live lever government can move this cycle" in prompt
    assert "what AI can now reliably do" in prompt
    assert "Make the opening feel like a national story of new capability and real gains, not just a risk audit." in director.orchestrator._stage_blueprint_instructions(
        SimulationCreateRequest(**config)
    )
    assert "not just shorter queues or cleaner paperwork" in director.orchestrator._stage_blueprint_instructions(
        SimulationCreateRequest(**config)
    )
    assert "capability frontier, diffusion, the broad economic read" in director.orchestrator._stage_instructions(
        SimulationCreateRequest(**config)
    )
    assert "one coherent documentary voiceover with a clear throughline" in director.orchestrator._stage_instructions(
        SimulationCreateRequest(**config)
    )
    assert "If a line wants to say X, Y, and Z" in director.orchestrator._stage_instructions(
        SimulationCreateRequest(**config)
    )
    assert "If a beat contains two 'and' joins or starts reading like a narrated inventory" in director.orchestrator._stage_instructions(
        SimulationCreateRequest(**config)
    )
    assert "Think clean voiceover lines, not narrated bullet points." in director.orchestrator._stage_instructions(
        SimulationCreateRequest(**config)
    )
    assert "Most beats should sound like one clean claim and one consequence" in director.orchestrator._stage_instructions(
        SimulationCreateRequest(**config)
    )
    assert "punctuation should serve the spoken rhythm" in director.orchestrator._stage_instructions(
        SimulationCreateRequest(**config)
    )
    assert "No beat should sound like a slogan, prophecy, trailer tagline" in director.orchestrator._stage_instructions(
        SimulationCreateRequest(**config)
    )
    assert "The default stance is not grim." in director.orchestrator._stage_instructions(
        SimulationCreateRequest(**config)
    )
    assert "what the systems can actually do on a computer" in director.orchestrator._stage_instructions(
        SimulationCreateRequest(**config)
    )
    assert "many households should still experience AI mainly through better services" in director.orchestrator._stage_instructions(
        SimulationCreateRequest(**config)
    )
    assert "In the opening movement, make three things explicit in plain language" in director.orchestrator._stage_instructions(
        SimulationCreateRequest(**config)
    )
    assert "Treat weakened junior ladders as one possible pressure" in director.orchestrator._stage_instructions(
        SimulationCreateRequest(**config)
    )
    assert "When in doubt, explain a regime change in who can do competent work" in director.orchestrator._stage_instructions(
        SimulationCreateRequest(**config)
    )
    assert "cheap competence spreading through software and services" in director.orchestrator._stage_instructions(
        SimulationCreateRequest(**config)
    )
    assert "Do not reuse the same distributional mechanism across consecutive stages" in director.orchestrator._stage_instructions(
        SimulationCreateRequest(**config)
    )
    assert "Let at least one early line simply explain the new national baseline in plain English." in director.orchestrator._stage_instructions(
        SimulationCreateRequest(**config)
    )
    assert "Make one clean macro read explicit: prices, output, hiring, bargaining power, service quality, investment, or national capacity." in director.orchestrator._stage_blueprint_instructions(
        SimulationCreateRequest(**config)
    )
    assert "A defended gain should sound like something people would resent losing" in director.orchestrator._stage_blueprint_instructions(
        SimulationCreateRequest(**config)
    )
    assert "one thing that still feels ordinary" in director.orchestrator._stage_blueprint_instructions(
        SimulationCreateRequest(**config)
    )
    assert "still feels mostly ordinary so the chapter does not read like universal transformation all at once" in director.orchestrator._stage_blueprint_instructions(
        SimulationCreateRequest(**config)
    )
    assert "Queue relief, claims speed, paperwork cleanup, or office backlog should usually stay support details, not the chapter's main upside." in director.orchestrator._stage_blueprint_instructions(
        SimulationCreateRequest(**config)
    )
    assert "Build one arc the viewer can retell" in director.orchestrator._montage_instructions()
    assert "No beat should read like a list, and no beat should feel like it was stripped down into robotic shorthand" in director.orchestrator._montage_instructions()
    assert "Commas are fine when they preserve natural spoken rhythm" in director.orchestrator._montage_instructions()
    assert "Make the opening macro sentences do separate jobs" in director.orchestrator._stage_blueprint_instructions(
        SimulationCreateRequest(**config)
    )


def test_radical_starting_world_mode_relaxes_opening_stage_near_term_guardrails(tmp_path: Path):
    settings = AppSettings(dummy_openai=True, runs_dir=tmp_path).prepare()
    director = build_director(settings)
    config = SimulationCreateRequest(starting_world_mode="radical")
    phase = director.orchestrator._phase_brief(0, config.stage_count, config.starting_world_mode)

    prompt = director.orchestrator._stage_prompt(
        config=config,
        stage_index=0,
        stage_count=config.stage_count,
        phase=phase,
        previous_stage=None,
        tracking=None,
        poll_summaries=[],
        player_in_power=True,
        incumbent_name="President Lena Park",
        queued_poll_questions=[],
    )
    blueprint_prompt = director.orchestrator._stage_blueprint_prompt(
        config=config,
        stage_index=0,
        stage_count=config.stage_count,
        phase=phase,
        previous_stage=None,
        tracking=None,
        poll_summaries=[],
        player_in_power=True,
        incumbent_name="President Lena Park",
        queued_poll_questions=[],
    )

    assert "Starting world mode: radical" in prompt
    assert "Starting world mode: radical" in blueprint_prompt
    assert "because the player asked to start in a radical future" in prompt
    assert "because the player asked to start in a radical future" in blueprint_prompt
    assert "make at least 3 structural realities feel truly later than today's economy" in prompt
    assert "specify at least 3 deep shifts in the settlement itself" in blueprint_prompt
    assert "the world should not read like the 2020s with sharper branding" in prompt
    assert "do not let it sound like today's world with one louder controversy" in blueprint_prompt
    assert "keep the world recognizably near-term" not in prompt
    assert "stay recognizably near-term and practical" not in blueprint_prompt
    assert "One opening sentence should make the upside vivid." in director.orchestrator._stage_blueprint_instructions(
        config
    )
    assert "Another should make the limit vivid." in director.orchestrator._stage_blueprint_instructions(
        config
    )
    assert "Prefer mechanism lines about who can now do competent work" in director.orchestrator._stage_blueprint_instructions(
        config
    )
    assert "expanded capability and redistributed competence first" in director.orchestrator._stage_blueprint_instructions(
        config
    )
    assert "Include at least two signals of relief, cheaper access, faster service, or stronger capacity" in director.orchestrator._stage_blueprint_instructions(
        config
    )
    assert "one dominant idea, and one example at most." in director.orchestrator._montage_instructions()
    assert "Prefer one subject and one verb early in the sentence" in director.orchestrator._montage_instructions()
    assert "say the broad capability class before the example" in director.orchestrator._montage_instructions()
    assert "If a thought branches, split it across adjacent beats" in director.orchestrator._montage_instructions()
    assert "what AI can do now, where it spread first, one broad gain people want to keep" in director.orchestrator._montage_instructions()
    assert "The narration should sound clean and readable, with no comma-heavy inventory feel and no paragraph that sounds like a list of talking points." in director.orchestrator._montage_instructions()
    assert "Make the documentary movements full-sentence script lines" in director.orchestrator._stage_blueprint_instructions(
        config
    )
    assert "make the economic mechanism easy to repeat in ordinary language" in prompt
    assert "describe adoption in believable waves" in prompt
    assert "one opening macro sentence should plainly answer why adoption is still spreading instead of stalling" in prompt
    assert "- the first opening line should name the broad capability class before any niche example" in prompt
    assert "if a beat wants three examples, keep the single best example" not in prompt
    assert "do not write final montage beats or image prompts here" in prompt
    assert "one sentence should plainly say what AI still cannot do well or cannot scale cheaply yet" in prompt
    assert "at least one early macro cue should describe something the country is getting better at or cheaper at" in prompt
    assert "The voiceover should feel like a mini-script with one clean thought per beat" in prompt
    assert "- treat each beat like one spoken line with one job" in prompt
    assert "social change too" in director.orchestrator._montage_instructions()
    assert "avoid consultant diction" in prompt
    assert "leave room for ambiguity" in prompt
    assert "naming 2-3 concrete task types or services" in prompt
    assert "let some households mainly notice service improvements" in prompt
    assert "one gain voters already like and would defend" in prompt
    assert "at most one policy axis may center junior hiring, entry ladders, or training" in prompt
    assert "keep the room briefing speakable and spare" in prompt
    assert "a short room briefing of about 55-90 words" in prompt
    assert "it should sound like four spoken briefing lines" in prompt
    assert "no room-briefing sentence should exceed about 22 words" in prompt
    assert "if a sentence starts to sound like a list of sectors, agencies, or apps, compress it to one category and one best example" in prompt
    assert "At least one early beat should say what the economy now feels like in broad terms" in director.orchestrator._montage_instructions()
    assert "A strong early beat often explains a regime shift in plain words" in director.orchestrator._montage_instructions()
    assert "Use wait-time, queue, or paperwork beats only when the blueprint makes them central" in director.orchestrator._montage_instructions()
    assert "If a beat starts with a dependent clause, a caveat, or a scene-setting flourish, rewrite it more directly" in director.orchestrator._montage_instructions()
    assert "If a sentence wants two commas, two examples, or a sector list, split it." in director.orchestrator._montage_instructions()
    assert "The opening movement should read like a short script: capability first, then spread, then lived gain, then constraint, then the split." in director.orchestrator._stage_blueprint_instructions(
        config
    )
    assert "Later-stage blueprints should broaden rather than narrow" in director.orchestrator._stage_blueprint_instructions(
        config
    )
    assert "A viewer should be able to summarize the montage in one sentence" not in director.orchestrator._stage_instructions(
        config
    )


def test_orchestrator_normalizers_keep_briefs_and_lines_spoken(tmp_path: Path):
    settings = AppSettings(dummy_openai=True, runs_dir=tmp_path).prepare()
    director = build_director(settings)

    room_brief = director.orchestrator._normalize_room_briefing(
        "Voters like the gains and they do not want to lose them, especially in daily life where the tools are already useful. "
        "But the pressure is concentrating in a few firms and public trust is still thin. "
        "You can widen access through competition policy and targeted procurement instead of broad brakes. "
        "The real uncertainty is whether faster diffusion beats concentration before the politics turns sour. "
        "A fifth sentence should not survive."
    )
    assert room_brief.count(".") <= 4
    assert "A fifth sentence should not survive" not in room_brief

    narration = director.orchestrator._normalize_narration_line(
        "Routine computer work now gets done by software that can search draft compare plan and code"
    )
    assert narration.endswith(".")
    assert "handle routine computer work" in narration


@pytest.mark.asyncio
async def test_advisor_realtime_session_uses_compact_live_context(tmp_path: Path):
    settings = AppSettings(dummy_openai=True, runs_dir=tmp_path).prepare()
    director = build_director(settings)
    state = await director.create_simulation(SimulationCreateRequest())
    await director.wait_for_pending(state.simulation_id)
    loaded = await director.get_simulation(state.simulation_id)
    stage = loaded.stages[loaded.active_stage_index]
    captured: dict[str, str] = {}

    async def fake_create_realtime_session(self, **kwargs):
        captured["instructions"] = kwargs["instructions"]
        return "dummy-client-secret", kwargs["model"]

    director.gateway.create_realtime_session = MethodType(fake_create_realtime_session, director.gateway)

    await director.create_realtime_session(
        loaded.simulation_id,
        RealtimeSessionRequest(role=RealtimeRole.advisor),
    )

    assert "One thing working:" in captured["instructions"]
    assert "One live change:" in captured["instructions"]
    assert "Still uncertain:" in captured["instructions"]
    assert "Quick read:" not in captured["instructions"]
    assert director.realtime_prompts._clip(stage.room_briefing, 180) not in captured["instructions"]
    assert director.realtime_prompts._clip(stage.state_of_world, 180) not in captured["instructions"]


@pytest.mark.asyncio
async def test_opponent_themes_follow_platform_and_public_mood(tmp_path: Path):
    settings = AppSettings(dummy_openai=True, runs_dir=tmp_path).prepare()
    director = build_director(settings)
    state = await director.create_simulation(SimulationCreateRequest())
    await director.wait_for_pending(state.simulation_id)
    loaded = await director.get_simulation(state.simulation_id)
    stage = loaded.stages[loaded.active_stage_index]
    stage.policy_notes = [
        "Pause frontier deployment.",
        "License major model rollouts.",
    ]
    stage.poll_summaries = [
        PollSummary(
            question="How worried are you about the cost of living right now?",
            counts={"Very worried": 42, "Somewhat worried": 18},
            shares={"Very worried": 0.7, "Somewhat worried": 0.3},
            sample_reasons=[],
        ),
        PollSummary(
            question="What feels most unfair about where the gains from AI are going?",
            counts={"Big firms first": 36, "Well connected workers": 24},
            shares={"Big firms first": 0.6, "Well connected workers": 0.4},
            sample_reasons=[],
        ),
    ]

    realtime_themes = director.realtime_prompts._opponent_themes(loaded, stage)
    orchestrator_themes = director.orchestrator._opponent_themes(
        loaded,
        stage,
        "Pause frontier deployment and license major model rollouts.",
    )

    assert any("broad-access abundance" in theme for theme in realtime_themes)
    assert any("household-value" in theme for theme in realtime_themes)
    assert any("keep useful tools open" in theme for theme in orchestrator_themes)
    assert any(
        "open diffusion" in theme
        or "narrow abuse enforcement" in theme
        or "faster diffusion" in theme
        or "lighter rules" in theme
        for theme in orchestrator_themes
    )
    assert any("household-value" in theme for theme in orchestrator_themes)


@pytest.mark.asyncio
async def test_opponent_themes_treat_tax_heavy_platform_as_restrictive(tmp_path: Path):
    settings = AppSettings(dummy_openai=True, runs_dir=tmp_path).prepare()
    director = build_director(settings)
    state = await director.create_simulation(SimulationCreateRequest())
    await director.wait_for_pending(state.simulation_id)
    loaded = await director.get_simulation(state.simulation_id)
    stage = loaded.stages[loaded.active_stage_index]
    themes = director.orchestrator._opponent_themes(
        loaded,
        stage,
        "Raise corporate AI taxes hard, create a public option model stack, and license major deployments.",
    )
    assert any("keep useful tools open" in theme for theme in themes)


def test_phase_brief_keeps_adjacent_steps_for_shorter_campaigns(tmp_path: Path):
    settings = AppSettings(dummy_openai=True, runs_dir=tmp_path).prepare()
    director = build_director(settings)

    three_stage_labels = [director.orchestrator._phase_brief(index, 3)["label"] for index in range(3)]
    four_stage_labels = [director.orchestrator._phase_brief(index, 4)["label"] for index in range(4)]

    assert three_stage_labels == [
        "Practical AI Breakout",
        "Cognitive Automation Surge",
        "Embodied Rollout",
    ]
    assert four_stage_labels == [
        "Practical AI Breakout",
        "Cognitive Automation Surge",
        "Embodied Rollout",
        "AGI Power Contest",
    ]
