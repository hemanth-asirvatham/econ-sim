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
    town_hall_prompt = director.realtime_prompts.town_hall_instructions(loaded, [])
    town_hall_question_prompt = director.realtime_prompts.town_hall_question_generation_instructions(
        loaded,
        stage.sample_citizens[0],
        [],
    )
    council_prompt = director.realtime_prompts.advisor_instructions(
        loaded,
        stage.state_of_world,
        [ConversationTurn(speaker="user", text="What options should the room test and what belongs on the board?", mode="voice")],
        advisor_mode=AdvisorMode.council,
    )

    stage_instructions = director.orchestrator._stage_instructions(loaded.config)
    montage_instructions = director.orchestrator._montage_instructions()

    assert "If the player asks how play works, answer in one practical line" in setup_prompt
    assert "broad national" in setup_prompt
    assert "no extra lens unless they ask for one" in setup_prompt
    assert "Keep replies short and conversational" in setup_prompt
    assert "Treat any example lists in the prompt as menus, not content to copy." in stage_instructions
    assert "Stay macro-first" in stage_instructions
    assert "Lead with a real gain before the strain" in stage_instructions
    assert "Decide the settlement once and carry it through" in stage_instructions
    assert "let the first paragraph surprise a 2026 listener" in stage_instructions
    assert "Prefer broad capability, prices, who gets access, who owns the systems, how public services run, state capacity, family routine, and geopolitics" in stage_instructions
    assert "Avoid consultant filler, vague futurist language, slogan writing" in stage_instructions
    assert "If old labor indicators no longer explain security, say what does instead." in stage_instructions
    assert "If adults no longer organize life around a standard job week" in stage_instructions
    assert "Do not let every later chapter collapse into service convenience or cheaper expert help" in stage_instructions
    assert "Build one arc the viewer can retell" in montage_instructions
    assert "Keep the opening macro-first. In the opening half" in montage_instructions
    assert "Sound like future history when the chapter is later or stranger" in montage_instructions
    assert "Do not march mechanically through identical beat jobs" in montage_instructions
    assert "Let a beat linger when that makes the narration feel human and legible" in montage_instructions
    assert "One early beat may simply name the new normal in a blunt line" in montage_instructions
    assert "Avoid listy sector tours, office cliches, queue cliches, vague futurism, named-place filler" in montage_instructions
    assert "Not every chapter should climax on convenience or service quality" in montage_instructions
    assert "Do not open with a token local color vignette like one farmer, one diner, or one town" in montage_instructions
    assert "Quick read:" in strategy_prompt
    assert "Citizen worth visiting:" in strategy_prompt
    assert "Working policy board:" in strategy_prompt
    assert "Most replies should be 1 short sentence" in advisor_prompt
    assert "Use plain words first." in advisor_prompt
    assert "If you say access, leverage, concentration, trust, or security" in advisor_prompt
    assert "If the player is asking what is happening in the country, answer the country question first." in advisor_prompt
    assert "If the player asks a broad world question, answer with one macro read and one lived consequence" in advisor_prompt
    assert "If the player says to go to the street, go to the debate, go to town hall" in advisor_prompt
    assert "If you need more detail, call get_world_briefing, run a poll, or send them to a citizen." in advisor_prompt
    assert "If the player asks about a 10-20 year future, answer from the settlement first" in advisor_prompt
    assert "Settlement in force:" in advisor_prompt
    assert "Only update the policy board when asked" in advisor_prompt
    assert "Board labels must stay short and concrete" in advisor_prompt
    assert "Recent conversation context:" not in advisor_prompt
    assert "Reply in 1 short sentence by default, sometimes 2" in citizen_prompt
    assert "Do not use academic or policy language you would not actually say out loud." in citizen_prompt
    assert "Talk in first person and start from my own life" in citizen_prompt
    assert "If asked something broad, answer from one thing I actually saw or dealt with and stop there unless the player follows up." in citizen_prompt
    assert "Your durable campaign themes:" in debate_prompt
    assert "Usually answer in 2 short sentences" in debate_prompt
    assert "Occupy the sharpest credible contrast for this stage and electorate" in debate_prompt
    assert "When your lane is pro-capability, argue from concrete gains people already use or want soon" in debate_prompt
    assert "Do not accept the player's premise and merely trim it" in debate_prompt
    assert "If you concede a point, keep the concession short" in debate_prompt
    assert "Settlement in force:" in debate_prompt
    assert "Speak as one current audience member at a time" in town_hall_prompt
    assert "In later or radical stages, questions can target the new settlement directly" in town_hall_prompt
    assert "Settlement in force:" in town_hall_prompt
    assert "Do not narrate yourself as moderator" in town_hall_prompt
    assert "Derive the question first from this voter's current update" in town_hall_question_prompt
    assert "Keep it concrete and single-threaded." in town_hall_question_prompt
    assert "Never leave the sentence hanging on a bare verb or unfinished clause." in town_hall_question_prompt
    assert "Prefer public names like monthly machine check, public AI help line, or monthly help credits" in town_hall_question_prompt
    assert "small council of senior advisors in a live room" in council_prompt
    assert "If the player is asking about a radically different future, speak in terms of income, access, ownership, public services, security, and daily routine" in council_prompt
    assert "Speak like people in a private strategy meeting" in council_prompt
    assert "One advisor leads at a time by default" in council_prompt
    assert "different specialists" in council_prompt
    assert "only the best-placed advisor should answer" in council_prompt
    assert "the player can interrupt at any time" in council_prompt
    assert "if the player asks what you think, answer like a live cabinet meeting" in council_prompt
    assert "when more than one advisor speaks, prefix each line with the advisor's first name" in council_prompt
    assert "do not do round-robin recap, moderator narration, theatrical bickering" in council_prompt
    assert "answer the country's problem first, not the room's process" in council_prompt
    assert "JSON-looking text" in council_prompt
    assert "plain speech beats smart-sounding fog" in council_prompt
    assert "if a speaker uses shorthand like access, trust, leverage, or security" in council_prompt
    assert "every speaking line needs one real mechanism" in council_prompt
    assert "explain it in plain words like payments, queues, ownership" in council_prompt
    assert "prefer public names like monthly machine check, public AI help line, or monthly help credits" in council_prompt
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
    assert "the player's future brief is not optional color" in prompt
    assert "a richer summary of about 420-620 words, written as 3 or 4 short paragraphs" in prompt
    assert "natural documentary exposition is better than memo formatting" in prompt
    assert "do not use markdown headers, memo labels, or section titles like Capability frontier or Economic picture" in prompt
    assert "surface at least 2 of them as active settled facts in the opening" in prompt
    assert "the world-state paragraph should open macro-first with 3 or 4 clean sentences" in prompt
    assert "the world-state paragraph should include several concrete macro cues" in prompt
    assert "the opening should move in a short script arc: capability first, then spread, then lived gain, then constraint, then the split" in prompt
    assert "if the future brief explicitly says the old job week stopped organizing life or that rival compute blocs shape power" in prompt
    assert "do not write final montage beats or image prompts here" in prompt
    assert "comma-heavy inventory feel and no paragraph that sounds like a list of talking points" in prompt
    assert "Gains that stuck:" in prompt
    assert "Open question now:" in prompt
    assert "Previous player platform:" not in prompt
    assert "Build one arc the viewer can retell" in director.orchestrator._montage_instructions()
    assert "make the capability, the defended gain, the stubborn limit, and the baseline that organizes daily security easy to hear" in director.orchestrator._montage_instructions()
    assert "Do not make every beat sound like a thesis sentence." in director.orchestrator._montage_instructions()
    assert "Do not spend tokens writing final documentary beats in this pass" in director.orchestrator._stage_instructions(loaded.config)
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
    blueprint_instructions = director.orchestrator._stage_blueprint_instructions(SimulationCreateRequest(**config))
    stage_instructions = director.orchestrator._stage_instructions(SimulationCreateRequest(**config))
    assert "Choose one dominant mechanism, one dominant gain people would defend" in blueprint_instructions
    assert "Before you outline, silently decide the settlement itself" in blueprint_instructions
    assert "Keep the world coherent, but do not preserve familiar baselines just to calm the chapter down." in blueprint_instructions
    assert "the opening needs one blunt fact a 2026 audience would find genuinely new" in blueprint_instructions
    assert "A far-future chapter that still sounds banal, managerial, or present-anchored is a miss." in blueprint_instructions
    assert "Write the blueprint in plain English." in blueprint_instructions
    assert "Do not let every later chapter resolve into service quality and convenience" in blueprint_instructions
    assert "Stay macro-first" in stage_instructions
    assert "Prefer broad capability, prices, who gets access, who owns the systems, how public services run, state capacity, family routine, and geopolitics" in stage_instructions
    assert "Avoid consultant filler, vague futurist language, slogan writing" in stage_instructions


def test_radical_prompts_require_settlement_structure_without_present_day_anchors(tmp_path: Path):
    settings = AppSettings(dummy_openai=True, runs_dir=tmp_path).prepare()
    director = build_director(settings)
    radical_config = SimulationCreateRequest(
        premise="AGI has already reorganized everyday life around a new settlement.",
    )

    stage_instructions = director.orchestrator._stage_instructions(radical_config)
    blueprint_instructions = director.orchestrator._stage_blueprint_instructions(radical_config)
    montage_instructions = director.orchestrator._montage_instructions()
    phase = director.orchestrator._phase_brief(
        0,
        radical_config.stage_count,
        director.orchestrator._effective_world_mode(radical_config),
    )
    blueprint_prompt = director.orchestrator._stage_blueprint_prompt(
        config=radical_config,
        stage_index=0,
        stage_count=radical_config.stage_count,
        phase=phase,
        previous_stage=None,
        tracking=None,
        poll_summaries=[],
        player_in_power=True,
        incumbent_name=radical_config.player_name,
        queued_poll_questions=[],
    )
    stage_prompt = director.orchestrator._stage_prompt(
        config=radical_config,
        stage_index=0,
        stage_count=radical_config.stage_count,
        phase=phase,
        previous_stage=None,
        tracking=None,
        poll_summaries=[],
        player_in_power=True,
        incumbent_name=radical_config.player_name,
        queued_poll_questions=[],
    )

    assert "Do not let a radical chapter sound like normal unemployment plus better copilots." in blueprint_instructions
    assert "If the chapter is years ahead, change how people live before you change how commentators describe it." in blueprint_instructions
    assert "cheap expert help can be one effect, but the blueprint should center the deeper settlement" in blueprint_instructions
    assert "household_income_system" in blueprint_prompt
    assert "capability_access_norm" in blueprint_prompt
    assert "firm_structure_norm" in blueprint_prompt
    assert "ownership_regime" in blueprint_prompt
    assert "public_service_norm" in blueprint_prompt
    assert "surface at least 2 of them as settled facts in the opening" in blueprint_prompt
    assert "prefer the new income-and-access settlement over familiar labor-market shorthand" in stage_prompt
    assert "unemployment is still low" not in stage_prompt
    assert "hiring has softened" not in stage_prompt
    assert "household budgets still mostly look steady" not in stage_prompt
    assert "Build one arc the viewer can retell" in montage_instructions
    assert "If the world is already strange, explain the new normal directly" in montage_instructions
    assert "Do not make every beat sound like a thesis sentence." in montage_instructions
    assert "Start from an already changed settlement" in stage_instructions
    assert "If the chapter could plausibly describe a mildly advanced 2026 with slightly better software, it is not far enough." in stage_instructions
    assert "Do not let every later chapter collapse into service convenience or cheaper expert help" in stage_instructions
    assert "Build one arc the viewer can retell" in director.orchestrator._montage_instructions()
    assert "Name the changed settlement directly instead of hinting around it." in director.orchestrator._stage_blueprint_instructions(radical_config)


def test_later_world_premise_unlocks_radical_guidance_without_any_mode_field(tmp_path: Path):
    settings = AppSettings(dummy_openai=True, runs_dir=tmp_path).prepare()
    director = build_director(settings)
    config = SimulationCreateRequest(
        premise=(
            "Start about fifteen years from now in a much stranger AGI society where households no longer organize life "
            "around a normal job week and rival compute blocs shape geopolitics."
        ),
    )

    stage_instructions = director.orchestrator._stage_instructions(config)
    blueprint_instructions = director.orchestrator._stage_blueprint_instructions(config)
    phase = director.orchestrator._phase_brief(
        0,
        config.stage_count,
        director.orchestrator.resolve_starting_world_mode(config.premise, config.topic_lens, config.stakes),
    )

    assert "Start from an already changed settlement" in stage_instructions
    assert "Do not let a radical chapter sound like normal unemployment plus better copilots." in blueprint_instructions
    assert phase["label"] == "Settlement Opening"


@pytest.mark.asyncio
async def test_featurette_prompt_pushes_specific_questions_and_distinct_choices(tmp_path: Path):
    settings = AppSettings(dummy_openai=True, runs_dir=tmp_path).prepare()
    director = build_director(settings)
    state = await director.create_simulation(
        SimulationCreateRequest(
            premise="The country already lives inside a different AI settlement.",
        )
    )
    await director.wait_for_pending(state.simulation_id)
    loaded = await director.get_simulation(state.simulation_id)
    stage = loaded.stages[loaded.active_stage_index]

    prompt = director.orchestrator._featurette_prompt(config=loaded.config, stage=stage)
    instructions = director.orchestrator._featurette_instructions()

    assert "Keep the 3 viewer questions that would most help someone understand this future." in prompt
    assert "one short natural-language question the reel answers for the player" in prompt
    assert "Make the three reels materially different from each other in both question and mechanism." in instructions
    assert "at least 2 reels should leave software-workflow land" in instructions
    assert "Do not make every beat sound like a neat thesis sentence." in instructions
    assert "Do not make the set feel like a curriculum." in instructions
    assert "not an empty field and not a generic placeholder" in instructions


def test_later_settlement_premise_relaxes_opening_stage_near_term_guardrails(tmp_path: Path):
    settings = AppSettings(dummy_openai=True, runs_dir=tmp_path).prepare()
    director = build_director(settings)
    config = SimulationCreateRequest(
        premise=(
            "Start well after the transition, in a society where households live inside a changed AI settlement "
            "and the old job order no longer explains ordinary security."
        )
    )
    phase = director.orchestrator._phase_brief(0, config.stage_count, director.orchestrator._effective_world_mode(config))

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

    assert "Setup direction from the player:" in prompt
    assert "Setup direction from the player:" in blueprint_prompt
    assert phase["label"] == "Settlement Opening"
    assert "how households secure ordinary life" in phase["brief"]
    assert "Decide which services became standing machine infrastructure" in phase["technology"]
    assert "Choose the main fight inside this settlement" in phase["politics"]
    assert "inferred start point from the setup" in prompt
    assert "inferred start point from the setup" in blueprint_prompt
    assert "the setup implies the opening chapter begins inside a later and more structurally changed AI society" in prompt
    assert "the setup implies the opening chapter begins after the old labor order has already been rewritten" in blueprint_prompt
    assert "if the setup opens inside a later settlement, the blueprint must commit to that settlement" in blueprint_prompt
    assert "how households secure ordinary life" in prompt
    assert "by the end of the opening blueprint, it should already be clear what replaced the old baseline of jobs" in blueprint_prompt
    assert "do not use unemployment staying low as the default serious-sounding macro frame" in prompt
    assert "do not use unemployment staying low as the safe default macro line" in blueprint_prompt
    assert "the world should not read like the 2020s with sharper branding" in prompt
    assert "do not let it sound like today's world with one louder controversy" in blueprint_prompt
    assert "radical mode" not in prompt
    assert "radical mode" not in blueprint_prompt
    assert "keep the world recognizably near-term" not in prompt
    assert "stay recognizably near-term and practical" not in blueprint_prompt
    assert "Name the changed settlement directly instead of hinting around it." in director.orchestrator._stage_blueprint_instructions(config)
    assert "The audience should hear different income flows, access channels, firm structure, ownership, or public-service delivery" in director.orchestrator._stage_blueprint_instructions(config)
    assert "make the economic mechanism easy to repeat in ordinary language" in prompt
    assert "describe adoption in believable waves" in prompt
    assert "those opening sentences should make clear what AI can now reliably do" in prompt
    assert "if a beat wants three examples, keep the single best example" not in prompt
    assert "do not write final montage beats or image prompts here" in prompt
    assert "one sentence should plainly say what AI still cannot do well or cannot scale cheaply yet" in prompt
    assert "the narration should sound clean and readable" in prompt
    assert "the opening should move in a short script arc" in prompt
    assert "avoid consultant diction" in prompt
    assert "one gain voters already like and would defend" in prompt
    assert "at most one policy axis may center junior hiring, entry ladders, or training" in prompt
    assert "keep the room briefing speakable and spare" in prompt
    assert "a short room briefing for the player as a decision brief" in prompt
    assert "it should sound like briefing lines across a table" in prompt
    assert "in 3 or 4 short spoken lines" in prompt
    assert "one strong example is better than a list of three weak ones" in prompt
    assert "If the world is already strange, explain the new normal directly" in director.orchestrator._montage_instructions()
    assert "Start from an already changed settlement" in director.orchestrator._stage_instructions(config)
    assert "Prefer the settlement baseline over familiar labor shorthand when old job metrics no longer explain security well." in director.orchestrator._stage_blueprint_instructions(config)
    assert "in later-settlement openings, at least 2 of those first 4 sentences should describe a changed settlement in ordinary language" in prompt
    assert "A viewer should be able to summarize the montage in one sentence" not in director.orchestrator._stage_instructions(
        config
    )


def test_radical_image_prompt_pushes_painterly_impressionist_direction(tmp_path: Path):
    settings = AppSettings(dummy_openai=True, runs_dir=tmp_path).prepare()
    director = build_director(settings)

    image_prompt = director.orchestrator._polish_image_prompt(
        "Lyrical civic impressionism",
        "A municipal benefits hall where families, clerks, and AI kiosks coordinate daily services",
    )

    assert "Render it as a painterly oil-or-gouache civic impression, not literal reportage." in image_prompt
    assert "Cezanne structure, Monet atmosphere, Matisse color blocks." in image_prompt
    assert "strong silhouettes" in image_prompt
    assert "selective detail over photoreal texture" in image_prompt
    assert "abstracted faces and hands" in image_prompt
    assert "stock-photo staging" in image_prompt


@pytest.mark.asyncio
async def test_summary_normalizer_strips_stiff_section_headers_and_featurettes_get_question_fallbacks(tmp_path: Path):
    settings = AppSettings(dummy_openai=True, runs_dir=tmp_path).prepare()
    director = build_director(settings)
    state = await director.create_simulation(SimulationCreateRequest())
    await director.wait_for_pending(state.simulation_id)
    loaded = await director.get_simulation(state.simulation_id)
    stage = loaded.stages[loaded.active_stage_index]

    normalized = director.orchestrator._normalize_summary_prose(
        "**Capability frontier**\nAI now handles routine expert support.\n\nEconomic picture: Households buy machine help like a utility.",
        max_paragraphs=4,
    )
    assert "**" not in normalized
    assert "Capability frontier" not in normalized
    assert "Economic picture:" not in normalized
    assert normalized.startswith("AI now handles routine expert support.")
    assert "Households buy machine help like a utility." in normalized

    assert director.orchestrator._featurette_question_fallback(
        subject="Household services",
        title="The Wallet at the Kitchen Table",
        stage=stage,
    ) == "What changed about household services in this future?"
    assert director.orchestrator._normalize_narration_line(
        "Machine stipends cover the basics; people spend more time steering local projects, and the old job week no longer organizes life the same way"
    ).startswith("Machine stipends cover the basics.")
    assert director.orchestrator._normalize_narration_line(
        "The system feels cheap at first, which is why cities tolerate it even when appeals still break down"
    ) == "The system feels cheap at first, which is why cities tolerate it even when appeals still break down."
    assert director.orchestrator._normalize_narration_line(
        "By 2041 the machine systems handle coding. compliance. routine claims review."
    ) == "By 2041 the machine systems handle coding, compliance, routine claims review."


def test_phase_briefs_stay_distinct_for_different_opening_contexts(tmp_path: Path):
    settings = AppSettings(dummy_openai=True, runs_dir=tmp_path).prepare()
    director = build_director(settings)

    default_labels = [director.orchestrator._phase_brief(index, 5)["label"] for index in range(5)]
    advanced_labels = [
        director.orchestrator._phase_brief(index, 5, "advanced")["label"] for index in range(5)
    ]
    radical_labels = [director.orchestrator._phase_brief(index, 5, "radical")["label"] for index in range(5)]

    assert default_labels == [
        "Practical AI Breakout",
        "Cognitive Automation Surge",
        "Embodied Rollout",
        "AGI Power Contest",
        "Settlement Era",
    ]
    assert advanced_labels == [
        "Cognitive Automation Surge",
        "Embodied Rollout",
        "AGI Power Contest",
        "Settlement Era",
        "Settlement Era",
    ]
    assert radical_labels == [
        "Settlement Opening",
        "AGI Power Contest",
        "Settlement Era",
        "Settlement Era",
        "Settlement Era",
    ]
    assert advanced_labels != radical_labels
    assert advanced_labels[0] != radical_labels[0]

    advanced_config = SimulationCreateRequest(
        premise="Start later in the transition, with deeper diffusion and more institutional change already underway."
    )
    advanced_phase = director.orchestrator._phase_brief(
        0,
        advanced_config.stage_count,
        director.orchestrator._effective_world_mode(advanced_config),
    )
    advanced_prompt = director.orchestrator._stage_prompt(
        config=advanced_config,
        stage_index=0,
        stage_count=advanced_config.stage_count,
        phase=advanced_phase,
        previous_stage=None,
        tracking=None,
        poll_summaries=[],
        player_in_power=True,
        incumbent_name="President Lena Park",
        queued_poll_questions=[],
    )
    advanced_blueprint_prompt = director.orchestrator._stage_blueprint_prompt(
        config=advanced_config,
        stage_index=0,
        stage_count=advanced_config.stage_count,
        phase=advanced_phase,
        previous_stage=None,
        tracking=None,
        poll_summaries=[],
        player_in_power=True,
        incumbent_name="President Lena Park",
        queued_poll_questions=[],
    )
    assert "the setup implies the opening chapter begins later in the transition" in advanced_prompt
    assert "the setup implies the opening chapter begins later in the transition" in advanced_blueprint_prompt
    assert "name which institutions, income flows, firm staffing patterns, or public-service channels are already different" in advanced_prompt


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
    split_fragment = director.orchestrator._normalize_narration_line(
        "Robot fleets are common in freight yards, fields. standard care settings."
    )
    assert split_fragment == "Robot fleets are common in freight yards, fields, standard care settings."

    policy_axes = director.orchestrator._normalize_short_lines(
        [
            "Accelerate embodied buildout where it raises national capacity, while widening access through",
            "Guarantee contestability in high-impact services with clear records, human review, appeals",
        ],
        limit=4,
        max_chars=96,
        sentence_fragment=True,
    )
    assert policy_axes[0] == "Accelerate embodied buildout where it raises national capacity, while widening access"
    assert not policy_axes[0].endswith("through")

    composed_brief = director.orchestrator._compose_room_briefing(
        dominant_mechanism="Physical rollout finally turns digital abundance into visible capacity gains in the real economy.",
        dominant_upside="Households and institutions want to keep cheaper competence plus more reliable delivery of goods and services.",
        economic_indicators=["Unemployment still low, but human hiring lags well behind output growth."],
        main_split="The fight is over who gets the first gains from deployment and who has power over the machine run systems they now depend on.",
        suggested_policy_axes=["Accelerate deployment and keep access open"],
        still_hard_now="People still matter where trust, persuasion, liability, messy judgment, and face to face care decide whether an outcome is accepted.",
        physical_world_status="",
        fallback_room_briefing="",
    )
    assert "want to keep cheaper competence" not in composed_brief
    assert "The broad split is over who gets the first gains" in composed_brief
    assert "One live lever is Accelerate deployment and keep access open." not in composed_brief

    core_split_brief = director.orchestrator._compose_room_briefing(
        dominant_mechanism="Households now budget around machine help as a routine service layer.",
        dominant_upside="People would fight to keep cheap competent guidance in ordinary life.",
        economic_indicators=["Machine-income flows matter more than wages alone in many household budgets."],
        main_split="The core split is whether synthetic provision should run like public infrastructure or private toll roads.",
        suggested_policy_axes=["Expand public AI utility access and household machine-income channels"],
        still_hard_now="High-trust services still need accountable institutions and appeals.",
        physical_world_status="",
        fallback_room_briefing="",
    )
    assert "The broad split is the core split is" not in core_split_brief
    assert "The broad split is over whether synthetic provision should run like public infrastructure or private toll roads." in core_split_brief

    noun_lane_brief = director.orchestrator._compose_room_briefing(
        dominant_mechanism="Households rely on machine help like a utility.",
        dominant_upside="People want to keep cheap expert help on tap.",
        economic_indicators=["Household service bills are lower in advice-heavy categories."],
        main_split="The fight is over who gets premium access.",
        suggested_policy_axes=["Universal machine-service access and public provision"],
        still_hard_now="Trust-heavy care still needs people.",
        physical_world_status="",
        fallback_room_briefing="",
    )
    assert "One live lever is universal machine-service access and public provision." in noun_lane_brief

    authored_brief = director.orchestrator._resolve_room_briefing(
        authored_room_briefing=(
            "Families now budget around machine subscriptions the way they once budgeted around utilities. "
            "The live fight is whether those rails stay contestable or harden into toll roads."
        ),
        dominant_mechanism="Machine-service utilities now shape daily budgeting.",
        dominant_upside="People do not want to lose cheap expert help on tap.",
        economic_indicators=["Household service bills are lower in advice-heavy categories."],
        main_split="The fight is over who controls the rails.",
        suggested_policy_axes=["Keep machine-service access contestable"],
        still_hard_now="Trust-heavy care still needs people.",
        physical_world_status="",
    )
    assert authored_brief.startswith("Families now budget around machine subscriptions")
    assert "One gain people already like is" not in authored_brief


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

    assert any("protect or widen the visible gain" in theme for theme in realtime_themes)
    assert any("answer the live split" in theme for theme in realtime_themes)
    assert any("pace, competition, and narrower remedies instead of a general brake" in theme for theme in orchestrator_themes)
    assert any("answer the current voter mood instead of an ideology script" in theme for theme in orchestrator_themes)


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
    assert director.orchestrator._player_debate_lane(
        "Raise corporate AI taxes hard, create a public option model stack, and license major deployments.",
        stage.policy_notes,
    ).startswith("broad-brake leaning")
    assert any("pace, competition, and narrower remedies instead of a general brake" in theme for theme in themes)


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
