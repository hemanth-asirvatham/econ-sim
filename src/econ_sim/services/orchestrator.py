from __future__ import annotations

import asyncio
import re
from pathlib import Path

from pydantic import BaseModel, Field

from ..config import AppSettings
from ..models import (
    ConversationTurn,
    DebateReply,
    NarrativeBeat,
    PollSummary,
    SetupChamberGuidance,
    SetupSessionPatchRequest,
    SimulationConfig,
    SimulationState,
    StagePackage,
    StageTracking,
)
from .openai_client import OpenAIGateway


class OrchestratorBeat(BaseModel):
    line: str
    image_prompt: str


class OrchestratorStageOutput(BaseModel):
    phase_label: str
    year_label: str
    title: str
    montage_logline: str
    state_of_world: str
    detailed_summary: str
    room_briefing: str
    economic_indicators: list[str] = Field(default_factory=list)
    tension_points: list[str] = Field(default_factory=list)
    suggested_policy_axes: list[str] = Field(default_factory=list)
    narrative_beats: list[OrchestratorBeat] = Field(default_factory=list)


class OrchestratorMontageOutput(BaseModel):
    montage_logline: str
    narrative_beats: list[OrchestratorBeat] = Field(default_factory=list)


class OrchestratorStageBlueprint(BaseModel):
    causal_arc: str
    capability_frontier_now: str = ""
    still_hard_now: str = ""
    physical_world_status: str = ""
    dominant_mechanism: str = ""
    dominant_upside: str = ""
    main_split: str = ""
    pro_adoption_constituency: str = ""
    opening_macro_sentences: list[str] = Field(default_factory=list, min_length=4, max_length=4)
    documentary_movements: list[str] = Field(default_factory=list, min_length=5, max_length=7)
    macro_cues: list[str] = Field(default_factory=list, min_length=4, max_length=6)
    first_wave_adopters: list[str] = Field(default_factory=list, min_length=3, max_length=5)
    sectors_in_focus: list[str] = Field(default_factory=list, min_length=3, max_length=5)
    benefits_people_notice: list[str] = Field(default_factory=list, min_length=3, max_length=5)
    frictions_or_splits: list[str] = Field(default_factory=list, min_length=3, max_length=5)
    local_example: str
    still_not_true: list[str] = Field(default_factory=list, min_length=3, max_length=5)
    governing_question: str


class DebateOutput(BaseModel):
    opponent_opening: str
    opponent_rebuttal: str
    analyst_take: str


class DebateImpactOutput(BaseModel):
    player_vote_shift: float = Field(ge=-0.08, le=0.08)
    rationale: str
    player_reaction: str
    opponent_reaction: str


class StagePolishOutput(BaseModel):
    room_briefing: str
    economic_indicators: list[str] = Field(default_factory=list)
    tension_points: list[str] = Field(default_factory=list)
    suggested_policy_axes: list[str] = Field(default_factory=list)


class OrchestratorService:
    def __init__(self, settings: AppSettings, gateway: OpenAIGateway):
        self.settings = settings
        self.gateway = gateway

    async def compose_stage(
        self,
        *,
        state: SimulationState,
        previous_stage: StagePackage | None,
        tracking: StageTracking | None,
        poll_summaries: list[PollSummary],
        queued_poll_questions: list[str],
    ) -> StagePackage:
        if self.settings.dummy_openai:
            return self._dummy_stage(state, previous_stage, tracking, poll_summaries, queued_poll_questions)

        phase = self._phase_brief(
            state.active_stage_index,
            state.config.stage_count,
            state.config.starting_world_mode,
        )
        blueprint_prompt = self._stage_blueprint_prompt(
            config=state.config,
            stage_index=state.active_stage_index,
            stage_count=state.config.stage_count,
            phase=phase,
            previous_stage=previous_stage,
            tracking=tracking,
            poll_summaries=poll_summaries,
            player_in_power=state.player_in_power,
            incumbent_name=state.incumbent_name,
            queued_poll_questions=queued_poll_questions,
        )
        blueprint, blueprint_response_id = await self.gateway.parse(
            model=self.settings.orchestrator_model,
            instructions=self._stage_blueprint_instructions(state.config),
            input_text=blueprint_prompt,
            text_format=OrchestratorStageBlueprint,
            reasoning_effort=state.config.orchestrator_reasoning_effort,
            previous_response_id=None,
            prompt_cache_key=f"{state.simulation_id}:orchestrator-blueprint",
            max_output_tokens=2200,
            verbosity="low",
            max_attempts=3,
        )
        prompt = self._stage_prompt(
            config=state.config,
            stage_index=state.active_stage_index,
            stage_count=state.config.stage_count,
            phase=phase,
            previous_stage=previous_stage,
            tracking=tracking,
            poll_summaries=poll_summaries,
            player_in_power=state.player_in_power,
            incumbent_name=state.incumbent_name,
            queued_poll_questions=queued_poll_questions,
            blueprint=blueprint,
        )
        parsed, response_id = await self.gateway.parse(
            model=self.settings.orchestrator_model,
            instructions=self._stage_instructions(state.config),
            input_text=prompt,
            text_format=OrchestratorStageOutput,
            reasoning_effort=state.config.orchestrator_reasoning_effort,
            previous_response_id=blueprint_response_id,
            prompt_cache_key=f"{state.simulation_id}:orchestrator-stage",
            max_output_tokens=4000,
            verbosity="medium",
            max_attempts=3,
        )
        montage_prompt = self._montage_prompt(
            config=state.config,
            phase=phase,
            stage_output=parsed,
            blueprint=blueprint,
        )
        montage, montage_response_id = await self.gateway.parse(
            model=self.settings.orchestrator_model,
            instructions=self._montage_instructions(),
            input_text=montage_prompt,
            text_format=OrchestratorMontageOutput,
            reasoning_effort=state.config.orchestrator_reasoning_effort,
            previous_response_id=response_id,
            prompt_cache_key=f"{state.simulation_id}:orchestrator-montage",
            max_output_tokens=1800,
            verbosity="medium",
            max_attempts=2,
        )
        stage_package = StagePackage(
            index=state.active_stage_index,
            phase_label=parsed.phase_label or phase["label"],
            year_label=parsed.year_label,
            title=parsed.title,
            montage_logline=montage.montage_logline or parsed.montage_logline,
            capability_frontier_now=blueprint.capability_frontier_now,
            still_hard_now=blueprint.still_hard_now,
            physical_world_status=blueprint.physical_world_status,
            dominant_mechanism=blueprint.dominant_mechanism,
            dominant_upside=blueprint.dominant_upside,
            main_split=blueprint.main_split,
            state_of_world=parsed.state_of_world,
            detailed_summary=parsed.detailed_summary,
            room_briefing=self._normalize_room_briefing(parsed.room_briefing),
            economic_indicators=self._normalize_short_lines(parsed.economic_indicators, limit=5, max_chars=132, sentence_fragment=False),
            tension_points=self._normalize_short_lines(parsed.tension_points, limit=4, max_chars=140, sentence_fragment=False),
            suggested_policy_axes=self._normalize_short_lines(parsed.suggested_policy_axes, limit=4, max_chars=96, sentence_fragment=True),
            narrative_beats=[
                NarrativeBeat(
                    line=self._normalize_narration_line(beat.line),
                    image_prompt=self._polish_image_prompt(state.config.visual_style, beat.image_prompt),
                )
                for beat in (montage.narrative_beats or parsed.narrative_beats)
            ],
            sample_citizens=[],
            tracking=tracking or self._neutral_tracking(),
            poll_summaries=poll_summaries,
            queued_poll_questions=queued_poll_questions,
            policy_notes=[],
            orchestrator_response_id=montage_response_id,
        )
        stage_package.room_briefing = self._compose_room_briefing(
            dominant_mechanism=stage_package.dominant_mechanism,
            dominant_upside=stage_package.dominant_upside,
            economic_indicators=stage_package.economic_indicators,
            main_split=stage_package.main_split,
            suggested_policy_axes=stage_package.suggested_policy_axes,
            still_hard_now=stage_package.still_hard_now,
            physical_world_status=stage_package.physical_world_status,
            fallback_room_briefing=parsed.room_briefing,
        )
        return stage_package

    async def build_setup_guidance(
        self,
        *,
        config: SimulationConfig,
        turns: list[ConversationTurn],
        user_text: str,
    ) -> SetupChamberGuidance:
        if self.settings.dummy_openai:
            return self._dummy_setup_guidance(config=config, user_text=user_text)

        turn_block = "\n".join(f"{turn.speaker}: {turn.text}" for turn in turns[-8:]) or "- no prior setup turns"
        region_focus = self._setup_field_or_default(config.region_focus, "broad national field")
        topic_lens = self._setup_field_or_default(config.topic_lens, "broad AGI transition")
        premise = self._setup_field_or_default(config.premise, "no extra premise locked")
        stakes = self._setup_field_or_default(config.stakes, "no special electoral stake locked")
        prompt = (
            "Current setup draft:\n"
            f"- title: {config.title}\n"
            f"- country: {config.country}\n"
            f"- player_name: {config.player_name}\n"
            f"- player_role: {config.player_role}\n"
            f"- opponent_name: {config.opponent_name}\n"
            f"- opponent_role: {config.opponent_role}\n"
            f"- opponent_voice: {config.opponent_voice}\n"
            f"- population_description: {config.population_description}\n"
            f"- region_focus: {region_focus}\n"
            f"- topic_lens: {topic_lens}\n"
            f"- premise: {premise}\n"
            f"- stakes: {stakes}\n"
            f"- starting_world_mode: {config.starting_world_mode}\n"
            f"- persona_count: {config.persona_count}\n"
            f"- stage_count: {config.stage_count}\n"
            f"- visual_style: {config.visual_style}\n"
            f"- orchestrator_reasoning_effort: {config.orchestrator_reasoning_effort}\n"
            f"- realtime_model: {config.realtime_model}\n\n"
            f"Recent setup turns:\n{turn_block}\n\n"
            f"Latest user message:\n{user_text}\n\n"
            "Return a short chamber reply in a conductor-like tone. "
            "If you changed fields, mention them compactly at the start in field -> value form, then add at most one short sentence about what that does to the run. "
            "Only include config_updates for changes the user actually requested. "
            "If the user asked to tighten, rewrite, or restyle an existing field, put the rewritten field value directly into config_updates instead of talking around it. "
            "Set readiness to ready when the draft is launchable as-is, and needs_input only when a requested change is blocked by a missing detail. "
            "If you apply any changes, mirror them in applied_updates using field -> value form. "
            "If you still need something, put only the blocking points in open_questions and keep next_actions short and practical. "
            "Ask at most two follow-up questions, and only when a missing detail blocks a requested change. "
            "If the user asks for the default setup or says go, keep the draft broad rather than inventing a special lens. "
            "If the country or jurisdiction changes and the offices or candidate names still read like defaults from somewhere else, localize them automatically unless the user explicitly set them. "
            "Treat starting_world_mode as a real setup field too. If the user asks to skip ahead, start later, jump into a more advanced future, or begin in a radical AGI world, set starting_world_mode to advanced or radical instead of burying that request in premise text. "
            "Do not give generic encouragement, field-by-field recaps, or repeat unchanged settings."
        )
        parsed, _ = await self.gateway.parse(
            model=self.settings.orchestrator_model,
            instructions=self._setup_instructions(),
            input_text=prompt,
            text_format=SetupChamberGuidance,
            reasoning_effort=config.orchestrator_reasoning_effort,
            prompt_cache_key="setup-chamber",
            max_output_tokens=900,
            verbosity="low",
        )
        heuristic_updates = self._dummy_setup_patch_from_text(user_text)
        merged_updates = {
            **heuristic_updates.model_dump(exclude_none=True),
            **parsed.config_updates.model_dump(exclude_none=True),
        }
        if merged_updates:
            parsed.config_updates = SetupSessionPatchRequest(**merged_updates)
            if not parsed.applied_updates or any("->" not in item for item in parsed.applied_updates):
                parsed.applied_updates = [f"{field} -> {value}" for field, value in merged_updates.items()]
        return parsed

    async def materialize_stage_media(self, *, stage: StagePackage, asset_dir: Path) -> None:
        async def _render_beat(index: int, beat: NarrativeBeat) -> None:
            image_suffix = "svg" if self.settings.dummy_openai else "png"
            image_path = asset_dir / f"beat-{index:02d}.{image_suffix}"
            audio_path = asset_dir / f"beat-{index:02d}.mp3"
            await self.gateway.render_image(prompt=beat.image_prompt, output_path=image_path)
            await self.gateway.synthesize(text=beat.line, output_path=audio_path)
            beat.image_path = str(image_path)
            beat.audio_path = str(audio_path) if audio_path.exists() else None

        asset_dir.mkdir(parents=True, exist_ok=True)
        await asyncio.gather(*[_render_beat(idx, beat) for idx, beat in enumerate(stage.narrative_beats)])

    async def polish_stage_after_poll(
        self,
        *,
        stage: StagePackage,
        tracking: StageTracking,
        poll_summaries: list[PollSummary],
        sample_citizens: list,
    ) -> StagePackage:
        if self.settings.dummy_openai:
            return stage

        tracking_block = "\n".join(f"- {metric.label}: {metric.display}" for metric in tracking.as_list())
        poll_block = self._salient_poll_lines(poll_summaries, limit=6) or "- no poll detail"
        citizen_block = "\n".join(
            f"- {citizen.display_name}, {citizen.role}, {citizen.region}: {citizen.current_update or citizen.summary}"
            for citizen in sample_citizens[:5]
        ) or "- no citizen samples"
        prompt = (
            f"Stage title: {stage.title}\n"
            f"Phase: {stage.phase_label}\n"
            f"Current stage summary: {stage.detailed_summary}\n"
            f"Current room briefing: {stage.room_briefing}\n"
            f"Current economic indicators: {' | '.join(stage.economic_indicators)}\n"
            f"Current tension points: {' | '.join(stage.tension_points)}\n"
            f"Current suggested policy axes: {' | '.join(stage.suggested_policy_axes)}\n\n"
            f"Fresh tracking snapshot:\n{tracking_block}\n\n"
            f"Fresh polling cues:\n{poll_block}\n\n"
            f"Fresh citizen lived evidence:\n{citizen_block}\n\n"
            "Revise the room_briefing, economic_indicators, tension_points, and suggested_policy_axes so they reflect the fresh evidence from this same stage. "
            "Do not rewrite the whole chapter. Keep the macro frame intact while sharpening the national economic read first: service quality, consumer surplus, household costs, national capacity, firm structure, regional divergence, and the split between leading and lagging sectors. "
            "Use citizens and poll quotes as evidence for that broader pattern, not as a substitute for it. "
            "Translate anecdotes back into macro signals. If three people complain about different things, name the broader labor, price, service, status, or access pattern they point to. "
            "Do not let the refreshed brief become more negative than the evidence requires. Preserve at least one household gain worth defending and at least one constituency that is actively pressing for more adoption because life or capacity is better. "
            "Do not let one sharp quote or one grievance-heavy poll answer hijack the chapter's center of gravity. The brief should stay top-down and balanced. "
            "The room briefing should still read like a concrete decision brief, not a string of anecdotes, and it should open on one gain voters would defend before naming the split. "
            "The suggested policy axes must span different governing lanes, not four versions of brake-pulling, and at most one axis may focus on labor ladders or training unless the evidence overwhelmingly points there. "
            "Return exactly 5 economic indicators, exactly 4 tension points, and exactly 4 suggested policy axes."
        )
        parsed, _ = await self.gateway.parse(
            model=self.settings.narration_model,
            instructions=(
                "You are refining the playable brief for a stage-based AGI transition simulation after new polling and citizen evidence arrives. "
                "Stay concrete, economically literate, macro-first, and concise. "
                "Do not introduce a new chapter; only sharpen the decision brief so it matches the citizens and polls that now exist. "
                "Lift lived evidence into a cleaner macro read instead of drifting into more anecdotes. "
                "Keep one concrete gain worth preserving in frame, not just the frictions."
            ),
            input_text=prompt,
            text_format=StagePolishOutput,
            reasoning_effort=self.settings.narration_reasoning_effort,
            prompt_cache_key=f"{stage.index}:stage-polish",
            max_output_tokens=700,
            verbosity="low",
        )
        if parsed.economic_indicators:
            stage.economic_indicators = self._normalize_short_lines(parsed.economic_indicators, limit=5, max_chars=132, sentence_fragment=False)
        if parsed.tension_points:
            stage.tension_points = self._normalize_short_lines(parsed.tension_points, limit=4, max_chars=140, sentence_fragment=False)
        if parsed.suggested_policy_axes:
            stage.suggested_policy_axes = self._normalize_short_lines(parsed.suggested_policy_axes, limit=4, max_chars=96, sentence_fragment=True)
        stage.room_briefing = self._compose_room_briefing(
            dominant_mechanism=stage.dominant_mechanism,
            dominant_upside=stage.dominant_upside,
            economic_indicators=stage.economic_indicators,
            main_split=stage.main_split,
            suggested_policy_axes=stage.suggested_policy_axes,
            still_hard_now=stage.still_hard_now,
            physical_world_status=stage.physical_world_status,
            fallback_room_briefing=parsed.room_briefing,
        )
        return stage

    async def build_debate_reply(
        self,
        *,
        state: SimulationState,
        current_stage: StagePackage,
        player_platform: str,
        player_rebuttal: str | None,
    ) -> DebateReply:
        if self.settings.dummy_openai:
            return DebateReply(
                opponent_opening=(
                    "My opponent wants to steer every shock from Washington. I am arguing that families need "
                    "more upside, faster deployment, and a cleaner bargain for those who lose ground."
                ),
                opponent_rebuttal=(
                    "The administration keeps promising balance, but households can feel when opportunity is "
                    "moving elsewhere and when daily life is still too expensive."
                ),
                analyst_take="The argument is over who can widen the gains without letting the transition spin out of public control.",
            )

        setup_premise = self._setup_field_or_default(
            state.config.premise,
            "No extra setup premise is locked; infer the main causal story from the world itself.",
        )
        setup_stakes = self._setup_field_or_default(
            state.config.stakes,
            "No extra setup stake is locked; infer the live political argument from public conditions.",
        )
        player_lane = self._player_debate_lane(player_platform, current_stage.policy_notes)
        opponent_lane = self._opponent_debate_lane(player_lane)
        flagship_move = self._opponent_flagship_move(player_lane, current_stage)
        prompt = (
            f"Stage title: {current_stage.title}\n"
            f"Stage phase: {current_stage.phase_label}\n"
            f"World summary: {current_stage.detailed_summary}\n"
            f"Player role: {state.config.player_role}\n"
            f"Opponent role: {state.config.opponent_role}\n"
            f"Setup premise: {setup_premise}\n"
            f"Setup stakes: {setup_stakes}\n"
            f"Electorate pressure points:\n{self._salient_poll_lines(current_stage.poll_summaries)}\n"
            f"Player platform: {player_platform}\n"
            f"Player rebuttal: {player_rebuttal or 'none'}\n"
            f"Player in power: {state.player_in_power}\n"
            f"Incumbent: {state.incumbent_name}\n"
            f"Opponent: {state.config.opponent_name}\n"
            f"Opponent durable themes: {'; '.join(self._opponent_themes(state, current_stage, player_platform))}\n"
            f"Player working policy board: {'; '.join(current_stage.policy_notes[:6]) or 'none yet'}\n"
            f"Player lane: {player_lane}\n"
            f"Opponent lane: {opponent_lane}\n"
            f"One flagship contrasting move: {flagship_move}\n"
            f"One gain the player would slow: {current_stage.dominant_upside or 'a real gain voters already notice'}\n"
            f"One constituency wanting more AI: {current_stage.pro_adoption_constituency or 'people already benefiting from faster adoption'}\n"
            "The opponent should be the strongest serious voice for the opposite lane, not a softened mirror of the player.\n"
            "Write a short opening statement for the opponent that includes 1-2 concrete policy planks and one clear governing principle, "
            "a short rebuttal that challenges the player while advancing a distinct alternative philosophy, "
            "and a one-sentence analyst framing of the difference. Keep the rhetoric plausible for an actual national campaign."
        )
        parsed, _ = await self.gateway.parse(
            model=self.settings.debate_model,
            instructions=(
                "You are writing a sharp but credible campaign debate exchange in an AGI transition simulation. "
                "Stay realistic, do not sermonize, and keep each speech brief enough for quick TTS. "
                "The opponent should sound like a serious rival with an actual governing theory, not a generic attack machine. "
                "Have them return to a durable coalition and set of priorities, not a new persona each turn. "
                "Start from the player's actual platform and the electorate pressure points, not a canned incumbency script. "
                "Concede the strongest popular piece of the player's case in a few words when it is real, then argue for a sharper alternative that answers the top voter mood. "
                "Use the structured lane brief below instead of improvising a mushy middle. "
                "Do not echo the player's remedies unless you are explicitly narrowing, replacing, or rejecting them. "
                "If the player leans restrictive, taxed-up, permission-heavy, or pause-first, make the strongest plausible case for access, deployment, competition, targeted guardrails, and keeping useful tools open. "
                "If the player proposes higher corporate taxes, windfall taxes, broader licensing, or a general slowdown, explicitly reject that remedy in plain words before you pitch the alternative. "
                "If the player leans speed-first, openness-first, or light-touch, make the strongest plausible case for fairness, household payoff, bargaining power, appeals, labor standards, and public legitimacy. "
                "If the player's lead remedy is tax, cap, pause, or license-first, explicitly name one gain it would slow and replace it with a visibly lighter-touch pro-capability move. "
                "When the player's lane is restrictive, explicitly name one gain their plan would slow down, one constituency that wants more AI because life or business got better, and one concrete pro-AI governing move. "
                "When the player's lane is restrictive, do not lapse into vague balance talk. Sound like a real build-and-compete alternative that wants useful tools to spread faster, more cheaply, and more widely. "
                "When the player's lane is restrictive, the opponent should sound recognizably more pro-capability than the player: defend a benefit people already use, explain why slowing the frontier would cost households or firms something real, and then offer a narrower substitute for the player's broad brake. "
                "Do not borrow the player's anti-AI framing and soften it. Occupy the other lane. "
                "The opponent's planks must be visibly different from the player's current board, and at least one plank should replace, narrow, or reverse the player's lead remedy. "
                "When the player's lane is restrictive, the opponent should sound clearly more affirmative about AI capability, diffusion, lower costs, and national advantage, not just slightly less restrictive. "
                "When the player's lane is speed-first, the opponent should sound clearly more serious about distribution, appeals, labor leverage, and visible household protection, not just speed with nicer rhetoric. "
                "The analyst_take must name the real governing fork, not say both sides want balance."
            ),
            input_text=prompt,
            text_format=DebateOutput,
            reasoning_effort=self.settings.debate_reasoning_effort,
            prompt_cache_key=f"{state.simulation_id}:debate",
            max_output_tokens=900,
            verbosity="low",
        )
        return DebateReply(**parsed.model_dump())

    async def assess_debate_impact(
        self,
        *,
        state: SimulationState,
        current_stage: StagePackage,
        player_agenda_points: list[str],
        player_rebuttal: str | None,
        pre_debate_player_share: float,
        pre_debate_opponent_share: float,
    ) -> DebateImpactOutput:
        if self.settings.dummy_openai:
            note_count = len(player_agenda_points)
            shift = 0.0
            if note_count >= 3:
                shift += 0.015
            elif note_count == 0:
                shift -= 0.01
            if player_rebuttal:
                shift += 0.005
            shift = max(-0.03, min(0.03, shift))
            direction = "toward the player" if shift >= 0 else "toward the opponent"
            return DebateImpactOutput(
                player_vote_shift=shift,
                rationale=f"The exchange nudged the room slightly {direction}.",
                player_reaction="Some voters heard a clearer governing agenda from the player.",
                opponent_reaction="Others still trusted the opponent's steadier line more.",
            )

        setup_stakes = self._setup_field_or_default(
            state.config.stakes,
            "No extra setup stake is locked; infer the live political argument from public conditions.",
        )
        prompt = (
            f"Stage title: {current_stage.title}\n"
            f"Stage phase: {current_stage.phase_label}\n"
            f"State of the world: {current_stage.detailed_summary}\n"
            f"Player role: {state.config.player_role}\n"
            f"Opponent role: {state.config.opponent_role}\n"
            f"Setup stakes: {setup_stakes}\n"
            f"Pre-debate player share: {pre_debate_player_share:.3f}\n"
            f"Pre-debate opponent share: {pre_debate_opponent_share:.3f}\n"
            f"Player in power: {state.player_in_power}\n"
            f"Player agenda points: {'; '.join(player_agenda_points) or 'none provided'}\n"
            f"Player rebuttal: {player_rebuttal or 'none'}\n"
            f"Opponent opening: {current_stage.debate_reply.opponent_opening if current_stage.debate_reply else 'none'}\n"
            f"Opponent rebuttal: {current_stage.debate_reply.opponent_rebuttal if current_stage.debate_reply else 'none'}\n"
            f"Poll reasons in the room: {' | '.join(reason for summary in current_stage.poll_summaries[:4] for reason in summary.sample_reasons[:1]) or 'none'}\n"
            "Estimate how much this debate exchange shifts the live vote, if at all. "
            "Most debates move the race only a little. Return a player_vote_shift between -0.08 and 0.08, "
            "where positive helps the player and negative helps the opponent. "
            "Judge the exchange on five concrete dimensions: affordability and household relief, service quality and speed, who captures gains or losses, whether either side preserves useful AI upside while handling real risks, and whether either side sounds strategically serious about national capacity. "
            "Also provide one concise rationale, one sentence about what helped the player, and one sentence about what still helped the opponent."
        )
        parsed, _ = await self.gateway.parse(
            model=self.settings.debate_model,
            instructions=(
                "You are evaluating a realistic election debate inside an AGI transition simulation. "
                "Be skeptical about giant swings. Reward clarity, relevance to lived conditions, affordability, service quality, credible upside, distributional credibility, legitimacy, and strategic seriousness. "
                "Do not reward generic applause lines or vague attacks."
            ),
            input_text=prompt,
            text_format=DebateImpactOutput,
            reasoning_effort=self.settings.debate_reasoning_effort,
            prompt_cache_key=f"{state.simulation_id}:debate-impact",
            max_output_tokens=650,
            verbosity="low",
        )
        return parsed

    def _setup_instructions(self) -> str:
        return (
            "You run the setup chamber for an AGI transition political simulation. "
            "Keep replies brief, grounded, and a little theatrical, like a conductor taking a few final cues before the first note. "
            "Most setup replies should be 1 or 2 short sentences, not a block of exposition. "
            "If the user asks how the experience works, answer like a useful tutorial line, not a pitch. Give one practical line about what the player will actually do next. If you include a tip, keep it concrete enough to say out loud once. "
            "The default run is broad and representative: a national U.S. simulation without a narrow regional or thematic bias unless the player explicitly asks for one. "
            "Treat player and opponent roles, country, and population as editable setup fields. "
            "Treat region focus, topic lens, premise, and stakes as optional steering fields; do not insist that they be filled. "
            "Treat persona_count, stage_count, visual_style, and starting_world_mode as first-class setup fields too. "
            "Treat natural-language setup requests as real edits, not vague inspiration. "
            "If the user says something like 'make this a Finland education-policy run focused on students and teachers', "
            "translate that into concrete config_updates for country, topic_lens, population_description, and any other strongly implied fields. "
            "If the user asks for a different art style, look, aesthetic, painterly direction, or documentary treatment, write that into visual_style directly. "
            "If the user asks to skip ahead, begin later in the transition, start in a more advanced AGI world, or jump to a more radical future economy, set starting_world_mode to advanced or radical directly. "
            "If the user asks for a certain number of agents, personas, citizens, or people in the sample, update persona_count directly. "
            "When jurisdiction or country changes, also rewrite player and opponent roles so the offices make sense in that place unless the user explicitly overrides them. "
            "If no narrow region_focus, topic_lens, premise, or stakes were requested, leave those fields broad or empty rather than inventing a special frame. "
            "Apply straightforward requested edits instead of restating them. "
            "If you made edits, start the chamber reply with compact field -> value language. "
            "If you made no edits, simply say the default run is still broad and ready, or ask one concise follow-up if the request was ambiguous. "
            "Only change fields that the user clearly requested or strongly implied. "
            "If the user asked to rewrite an existing field, return the rewritten value directly in config_updates. "
            "If the user says to use the default setup, keep the current draft and say it is ready rather than inventing new changes. "
            "If the user says things like go, get going, start it, launch it, or I'm ready, leave config_updates empty and say the run is ready to launch now. "
            "If the user asks how to play or what happens next, answer simply: launch the run, hear the documentary intro, workshop ideas with advisors, run polls, talk to citizens, debate, then face the vote. "
            "If the user is ambiguous, leave config_updates empty and ask a concise follow-up question instead of inventing details. "
            "Do not give generic encouragement, vague summaries, or next-step filler when a direct edit can be applied now. "
            "Do not turn broad defaults into a hidden opinionated scenario."
        )

    def _dummy_setup_guidance(self, *, config: SimulationConfig, user_text: str) -> SetupChamberGuidance:
        updates = self._dummy_setup_patch_from_text(user_text)
        update_payload = updates.model_dump(exclude_none=True)
        preview_config = config.model_copy(update=update_payload)
        applied_updates = [f"{field} -> {value}" for field, value in update_payload.items()]
        missing = self._setup_missing_fields(preview_config)
        readiness = "ready" if not missing else "needs_input"
        normalized = user_text.lower()
        launch_cues = ("i'm ready", "im ready", "ready to go", "go", "get going", "start it", "start the run", "launch it")
        if applied_updates:
            reply = "Applied " + "; ".join(applied_updates[:4]) + "."
        elif any(cue in normalized for cue in launch_cues):
            reply = "The broad default run still holds. It is ready to launch now."
        else:
            reply = "The broad default run still holds."
        if readiness == "ready":
            reply += " Say start when you want me to launch it."
        else:
            reply += " I still need a few basics before launch."
        open_questions = [f"Set {field.replace('_', ' ')}." for field in missing[:2]]
        next_actions = (
            ["Say start when the draft looks right.", "Or give me one concrete nudge first."]
            if readiness == "ready"
            else ["Fill the missing setup fields before launch."]
        )
        return SetupChamberGuidance(
            chamber_reply=reply,
            readiness=readiness,
            applied_updates=applied_updates,
            open_questions=open_questions,
            next_actions=next_actions,
            config_updates=updates,
        )

    def _dummy_setup_patch_from_text(self, text: str) -> SetupSessionPatchRequest:
        focus_phrase = self._extract_focus_phrase(text)
        country = self._extract_country_from_freeform(text)
        topic_lens = self._extract_labeled_value(text, "topic_lens")
        premise = self._extract_labeled_value(text, "premise")
        stakes = self._extract_labeled_value(text, "stakes")
        population = self._extract_labeled_value(text, "population")
        region_focus = self._extract_labeled_value(text, "region_focus")
        visual_style = self._extract_visual_style_request(text)
        if focus_phrase and topic_lens is None:
            topic_lens = focus_phrase
        if focus_phrase and region_focus is None and any(
            keyword in focus_phrase.lower() for keyword in ("student", "teacher", "municipal", "school", "classroom")
        ):
            region_focus = "municipal school systems"
        if focus_phrase and population is None and country:
            population = (
                f"A representative sample of people in {country} whose lives are directly shaped by {focus_phrase}, "
                "with realistic variation across age, income, ideology, institutional role, geography, and AI exposure."
            )
        if population is None and country:
            lowered = text.lower()
            if any(keyword in lowered for keyword in ("student", "pupil", "teacher", "parent", "school", "classroom", "education")):
                population = (
                    f"A representative sample of people in {country} whose lives are shaped by schools, learning, and unequal access to AI-enabled education, "
                    "with students, pupils, parents, teachers, tutors, principals, and education administrators represented across region, class, age, ideology, and AI exposure."
                )
        return SetupSessionPatchRequest(
            title=self._extract_labeled_value(text, "title"),
            country=self._extract_labeled_value(text, "country") or country,
            player_name=self._extract_labeled_value(text, "player"),
            player_role=self._extract_labeled_value(text, "player_role"),
            opponent_name=self._extract_labeled_value(text, "opponent"),
            opponent_role=self._extract_labeled_value(text, "opponent_role"),
            opponent_voice=self._extract_labeled_value(text, "opponent_voice"),
            population_description=population,
            region_focus=region_focus,
            topic_lens=topic_lens,
            premise=premise,
            stakes=stakes,
            starting_world_mode=self._extract_starting_world_mode(text),
            persona_count=self._extract_int_value(text, "persona_count", r"(\d+)\s*(?:personas?|agents?|citizens?|people)"),
            stage_count=self._extract_int_value(text, "stage_count", r"(\d+)\s*(?:-\s*)?stages?"),
            visual_style=visual_style,
            orchestrator_reasoning_effort=self._extract_reasoning_effort(text),
            realtime_model=self._extract_labeled_value(text, "realtime_model"),
        )

    def _extract_labeled_value(self, text: str, label: str) -> str | None:
        normalized_label = re.escape(label).replace("_", "[ _]")
        match = re.search(
            rf"\b{normalized_label}\b\s*[:=-]\s*(.+?)(?=(?:\n|[.;]|$))",
            text,
            flags=re.IGNORECASE,
        )
        if not match:
            return None
        value = " ".join(match.group(1).strip().strip('\"').split())
        return value or None

    def _extract_int_value(self, text: str, label: str, fallback_pattern: str) -> int | None:
        labeled = self._extract_labeled_value(text, label)
        if labeled and labeled.isdigit():
            return int(labeled)
        match = re.search(fallback_pattern, text, flags=re.IGNORECASE)
        if match:
            return int(match.group(1))
        return None

    def _extract_reasoning_effort(self, text: str) -> str | None:
        labeled = self._extract_labeled_value(text, "orchestrator_reasoning_effort") or self._extract_labeled_value(
            text,
            "reasoning",
        )
        if labeled is None:
            return None
        normalized = labeled.lower()
        return normalized if normalized in {"none", "low", "medium", "high"} else None

    def _extract_starting_world_mode(self, text: str) -> str | None:
        labeled = self._extract_labeled_value(text, "starting_world_mode") or self._extract_labeled_value(
            text,
            "world_mode",
        )
        if labeled:
            normalized = labeled.lower()
            if normalized in {"default", "advanced", "radical"}:
                return normalized
        lowered = text.lower()
        radical_cues = (
            "radical future",
            "radical agi future",
            "radical ai future",
            "far future",
            "settlement era",
            "post scarcity",
            "post-scarcity",
            "near agi",
            "near-agi",
            "deeply transformed economy",
            "deeply transformed world",
            "agi power contest",
            "hugely different economy",
            "much farther in the future",
            "machine-run",
            "robotics-heavy future",
        )
        advanced_cues = (
            "skip ahead",
            "jump ahead",
            "more advanced ai",
            "much more advanced ai",
            "more advanced future",
            "start later",
            "later stage",
            "skip a few stages",
            "advanced ai future",
            "years later",
        )
        if any(cue in lowered for cue in radical_cues):
            return "radical"
        if any(cue in lowered for cue in advanced_cues):
            return "advanced"
        stage_match = re.search(r"\bstage\s+([3-9])\b", lowered)
        if stage_match:
            stage_number = int(stage_match.group(1))
            return "radical" if stage_number >= 4 else "advanced"
        return None

    def _extract_focus_phrase(self, text: str) -> str | None:
        match = re.search(
            r"(?:focused on|focus on|lens on|built around|centered on|about|around)\s+(.+?)(?=(?:[.!?]|$))",
            text,
            flags=re.IGNORECASE,
        )
        if not match:
            return None
        value = " ".join(match.group(1).strip(" .").split())
        return value or None

    def _extract_country_from_freeform(self, text: str) -> str | None:
        explicit = self._extract_labeled_value(text, "country")
        if explicit:
            return explicit
        country_patterns = [
            "United States",
            "United Kingdom",
            "Canada",
            "Mexico",
            "Brazil",
            "Finland",
            "Switzerland",
            "Sweden",
            "Norway",
            "Denmark",
            "Estonia",
            "Germany",
            "France",
            "Spain",
            "Netherlands",
            "Poland",
            "India",
            "Japan",
            "South Korea",
            "Australia",
            "Singapore",
            "Texas",
            "California",
            "New York",
        ]
        lowered = text.lower()
        if "mexican" in lowered and "mexico" not in lowered:
            return "Mexico"
        if "brazilian" in lowered and "brazil" not in lowered:
            return "Brazil"
        if "swiss" in lowered and "switzerland" not in lowered:
            return "Switzerland"
        if "french" in lowered and "france" not in lowered:
            return "France"
        if "finnish" in lowered and "finland" not in lowered:
            return "Finland"
        if "spanish" in lowered and "spain" not in lowered:
            return "Spain"
        if "dutch" in lowered and "netherlands" not in lowered:
            return "Netherlands"
        if "polish" in lowered and "poland" not in lowered:
            return "Poland"
        if "korean" in lowered and "south korea" not in lowered:
            return "South Korea"
        if "australian" in lowered and "australia" not in lowered:
            return "Australia"
        for candidate in country_patterns:
            if candidate.lower() in lowered:
                return candidate
        return None

    def _extract_visual_style_request(self, text: str) -> str | None:
        direct = self._extract_labeled_value(text, "visual_style")
        if direct:
            return direct
        direct = self._extract_labeled_value(text, "visual style")
        if direct:
            return direct
        direct = self._extract_labeled_value(text, "art style")
        if direct:
            return direct
        match = re.search(
            r"(?:art style|visual style|style|look|aesthetic)\s*(?:should be|=|:)?\s+(.+?)(?=(?:[.!?]|$))",
            text,
            flags=re.IGNORECASE,
        )
        if match:
            value = " ".join(match.group(1).strip(" .").split())
            return value or None
        match = re.search(
            r"(?:paint it like|render it like|make it look like|make it feel like)\s+(.+?)(?=(?:[.!?]|$))",
            text,
            flags=re.IGNORECASE,
        )
        if match:
            value = " ".join(match.group(1).strip(" .").split())
            return value or None
        return None

    def _setup_missing_fields(self, config: SimulationConfig) -> list[str]:
        required_fields = [
            "title",
            "country",
            "player_name",
            "player_role",
            "opponent_name",
            "opponent_role",
            "population_description",
            "visual_style",
        ]
        return [field for field in required_fields if not str(getattr(config, field, "")).strip()]

    def _setup_field_or_default(self, value: str | None, fallback: str) -> str:
        normalized = " ".join(str(value or "").split()).strip()
        return normalized or fallback

    def _clip(self, text: str | None, max_chars: int) -> str:
        cleaned = " ".join(str(text or "").split()).strip()
        if len(cleaned) <= max_chars:
            return cleaned
        clipped = cleaned[: max_chars - 1].rsplit(" ", 1)[0].strip()
        return f"{clipped}..."

    def _trim_without_ellipsis(self, text: str | None, max_chars: int) -> str:
        cleaned = " ".join(str(text or "").split()).strip()
        if len(cleaned) <= max_chars:
            return cleaned
        clipped = cleaned[:max_chars].rsplit(" ", 1)[0].strip()
        return clipped or cleaned[:max_chars].strip()

    def _sentence_split(self, text: str) -> list[str]:
        cleaned = " ".join(str(text or "").split()).strip()
        if not cleaned:
            return []
        sentences = [part.strip() for part in re.split(r"(?<=[.!?])\s+", cleaned) if part.strip()]
        if len(sentences) == 1 and ";" in cleaned:
            sentences = [part.strip() for part in re.split(r";\s*", cleaned) if part.strip()]
        return sentences

    def _normalize_sentence(self, text: str, *, max_words: int = 22, max_chars: int = 150) -> str:
        cleaned = " ".join(str(text or "").split()).strip(" -")
        if not cleaned:
            return ""
        words = cleaned.split()
        if len(words) > max_words:
            cleaned = " ".join(words[:max_words]).rstrip(",;:")
        cleaned = self._trim_without_ellipsis(cleaned, max_chars).rstrip(",;:")
        words = cleaned.split()
        while words and words[-1].lower() in {"that", "which", "because", "while", "and", "or", "to", "with", "for", "of", "in"}:
            words.pop()
        cleaned = " ".join(words).rstrip(",;:")
        if cleaned and cleaned[-1] not in ".!?":
            cleaned = f"{cleaned}."
        return cleaned

    def _normalize_room_briefing(self, text: str) -> str:
        sentences = [self._normalize_sentence(sentence, max_words=20, max_chars=124) for sentence in self._sentence_split(text)[:4]]
        normalized = [sentence for sentence in sentences if sentence]
        return " ".join(normalized).strip()

    def _compose_room_briefing(
        self,
        *,
        dominant_mechanism: str | None,
        dominant_upside: str | None,
        economic_indicators: list[str] | None,
        main_split: str | None,
        suggested_policy_axes: list[str] | None,
        still_hard_now: str | None,
        physical_world_status: str | None,
        fallback_room_briefing: str | None,
    ) -> str:
        def clause(
            value: str | None,
            *,
            max_words: int,
            strip_prefixes: tuple[str, ...] = (),
        ) -> str:
            cleaned = " ".join(str(value or "").replace("\n", " ").split()).strip()
            if not cleaned:
                return ""
            cleaned = re.split(r"(?<=[.!?])\s+", cleaned, maxsplit=1)[0].strip()
            for separator in ("; ", " — ", " -- ", " but ", " while ", " because ", " so ", " that used to ", " used to "):
                if separator in cleaned and len(cleaned.split()) > max_words:
                    cleaned = cleaned.split(separator, 1)[0].strip()
            for prefix in strip_prefixes:
                cleaned = re.sub(rf"^{re.escape(prefix)}", "", cleaned, flags=re.IGNORECASE).strip(" .,:;-")
            words = cleaned.split()
            if len(words) > max_words:
                cleaned = " ".join(words[:max_words]).rstrip(",;:")
            words = cleaned.split()
            while words and words[-1].lower() in {"that", "which", "because", "while", "and", "or", "to", "with", "for", "of", "in"}:
                words.pop()
            cleaned = " ".join(words)
            lowered = cleaned.lower()
            for phrase in (" did not", " could not", " would not", " should not", " used to", " lets large", " local"):
                if lowered.endswith(phrase):
                    cleaned = cleaned[: -len(phrase)].rstrip(" ,.;:-")
                    lowered = cleaned.lower()
            return cleaned.strip(" .,:;-")

        def sentence(prefix: str, value: str) -> str:
            if not value:
                return ""
            leading = value[0]
            lowered = value if value[:2].isupper() else leading.lower() + value[1:]
            return self._normalize_sentence(f"{prefix}{lowered}", max_words=24, max_chars=148)

        gain = clause(
            dominant_upside or fallback_room_briefing,
            max_words=13,
            strip_prefixes=("one gain voters already like is", "one gain already visible is", "voters will defend", "the gain is", "the main gain is"),
        )
        macro = clause(
            next((item for item in economic_indicators or [] if item), "") or dominant_mechanism or fallback_room_briefing,
            max_words=16,
            strip_prefixes=("the broad read is", "the economy feels", "the main mechanism is", "the broad economic read is"),
        )
        split = clause(
            main_split or fallback_room_briefing,
            max_words=16,
            strip_prefixes=("the split is", "main split is", "the live split is"),
        )
        lever = clause(
            next((axis for axis in suggested_policy_axes or [] if axis), "") or fallback_room_briefing,
            max_words=13,
            strip_prefixes=("one live lever this cycle is", "a real lever this cycle is", "the lever is"),
        )
        tradeoff = clause(
            still_hard_now or physical_world_status or fallback_room_briefing,
            max_words=16,
            strip_prefixes=("the tradeoff is", "the main tradeoff is", "the hard limit is", "what still binds is"),
        )

        composed = " ".join(
            sentence_text
            for sentence_text in (
                sentence("One gain people already like is ", gain),
                sentence("The broad read is ", macro),
                sentence("The broad split is ", split or tradeoff),
                sentence("One live lever is to ", lever),
            )
            if sentence_text
        )
        return composed or self._normalize_room_briefing(fallback_room_briefing or "")

    def _normalize_short_lines(
        self,
        items: list[str],
        *,
        limit: int,
        max_chars: int,
        sentence_fragment: bool,
    ) -> list[str]:
        normalized: list[str] = []
        for item in items:
            cleaned = " ".join(str(item or "").split()).strip(" -")
            if not cleaned:
                continue
            cleaned = self._trim_without_ellipsis(cleaned, max_chars)
            if sentence_fragment:
                cleaned = cleaned.rstrip(".")
            elif cleaned[-1] not in ".!?":
                cleaned = f"{cleaned}."
            normalized.append(cleaned)
            if len(normalized) >= limit:
                break
        return normalized

    def _normalize_narration_line(self, text: str) -> str:
        cleaned = " ".join(str(text or "").split()).strip()
        if not cleaned:
            return ""
        cleaned = re.sub(r"\bsearch draft compare plan and code\b", "handle routine computer work", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\bsearch compare draft code and plan\b", "support routine software workflows", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\bplan route draft and compare\b", "guide ordinary planning work", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\btutoring planning and software help\b", "cheap expert help", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\bmodels data and distribution\b", "models and distribution", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\bscreen based\b", "screen-based", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\bwhite collar\b", "white-collar", cleaned, flags=re.IGNORECASE)
        if cleaned[-1] not in ".!?":
            cleaned = f"{cleaned}."
        return cleaned

    def _stage_instructions(self, config: SimulationConfig) -> str:
        return (
            "You are the master director of a stage-based AGI economic simulation. "
            "Project forward realistically, creatively, and with a strong causal story. "
            "Technology adoption drives most change; policy matters at the margin unless it is unusually strong. "
            "Treat AI as creating many things people genuinely want while also creating labor, power, coordination, and legitimacy frictions. "
            "Lead with what AI newly makes easier, cheaper, or more capable before you turn to strain, and keep the tone hopeful but grounded. "
            "Keep the story balanced: not a hype reel, not a doom memo, and not a running indictment of the technology.\n\n"
            "Write for a policymaker-facing simulation, not a manifesto, not a doom spiral, and not a hype reel. "
            "Each stage should feel like one believable national moment in the transition toward AGI. "
            "Lead from the top down: capability frontier, diffusion, the broad economic read, then households, and only then a local vignette if needed. "
            "Start broad and stay broad long enough to orient the player before narrowing into one desk, family, school, or shop. "
            "The default stance is not grim. Give the upside enough room that the audience understands why adoption kept spreading, then turn to strain. "
            "Write like a clean documentary script in ordinary English. Each sentence should carry one main claim. "
            "Name the broad capability class before the example, give one attractive upside before the main strain, and if a sentence wants more than one comma or more than one example, split it. "
            "Especially in earlier chapters, make the opening picture legible in one breath: what the systems can actually do on a computer, what that newly changes in ordinary life, and what still clearly does not work yet. "
            "Say what the systems can now do in plain verbs when you can: search, compare, draft, translate, tutor, code, plan, route, summarize, guide people through choices, or run a reliable screen-based workflow. "
            "If the broad truth is that AI now behaves like dependable computer-use labor or cheap expert guidance, say that plainly before narrowing to a niche task. "
            "One early macro line should tell a general audience what this frontier really amounts to in plain English before it gets specific. "
            "The opening national picture should usually answer four broad questions plainly: what AI can now do, what still needs people, whether the main effects are still software-first because robotics remains narrower, and whether jobs or prices still look broadly calm or are clearly shifting. "
            "A strong opening paragraph usually does five jobs in order: name the capability class, say where it spread first, give one blunt macro read, name one defended gain, and then name the hard limit or split. "
            "Treat the macro read like a real economic headline, not background color: labor markets, prices, service quality, margins, access, and concentration should be legible in ordinary language. "
            "Before naming any major friction, state one concrete household upside, one institutional upside, and one national-capacity upside that people would genuinely want to keep. "
            "Make the upside concrete and economically legible: what got easier, what got cheaper, which capabilities spread, which firms or workers gained leverage, and what remains scarce. "
            "Not every chapter's opening pressure is labor substitution. Sometimes the clean macro opening is consumer surplus, service access, cheaper expertise, export pressure, stronger institutions, or new leverage for smaller firms. "
            "Do not let the national story read like a productivity audit or a running case against AI. The player should repeatedly see why adoption keeps spreading and why serious people want more of it. When service quality, access, prices, or national capacity are visibly improving, hold that gain in frame instead of defaulting to generalized anxiety. "
            "In early stages, many households should still experience AI mainly through better services, cheaper expertise, stronger work tools, or not much direct contact yet. Keep office churn from standing in for the whole economy. "
            "Also let some households or small firms first feel AI through stronger search, planning, tutoring, design help, translation, software leverage, shopping confidence, or simply being able to do more without hiring a specialist. "
            "In the first chapter especially, start from broad capability and visible convenience before you mention labor strain. The country should first understand what got easier, cheaper, or newly possible. "
            "Make the audience feel how life is different now: what people can suddenly do with software, services, expertise, entertainment, schooling, care, or errands that used to be slower, pricier, or out of reach. "
            "Also make the social change legible: how people learn, care for family, shop, communicate, and run local institutions can shift even when the office story is not the main event. "
            "Prefer broad capability gains like cheap expertise, better judgment support, stronger learning, software leverage, consumer convenience, or doing more outside your old skill boundary over repetitive wait-time, backlog, or office-friction language. "
            "Positive change can mean abundance, autonomy, confidence, wider access to judgment, or a household being able to act without waiting for scarce insiders. Do not reduce every upside to throughput. "
            "When the evidence gives you a positive center of gravity, let the chapter keep it instead of flattening it into a warning label. "
            "Rotate the early lived examples. If one chapter uses schools and small firms, the next can use care, household planning, retail, travel, or local government instead of snapping back to the same office image. "
            "Whenever the evidence supports it, include at least one concrete gain people would defend, one group actively benefiting, and one reason the country keeps adopting despite the strain. "
            "In the opening movement, make three things explicit in plain language: what AI can broadly do now, what still requires people, and what remains bottlenecked in the physical world. "
            "Keep queues, paperwork, and claim routing as supporting details unless the stage truly turns on that mechanism. If you need an early upside example, prefer a new capability, a broader reach, a cheaper expert service, or a task that became newly feasible for ordinary people before you reach for queue relief. "
            "Use this opening sequence unless the chapter has a very strong reason to bend it: capability frontier, first-wave spread, one gain people want to keep, what still cannot be trusted or scaled, then the main split. "
            "Name the broad capability class before the examples. Say what became reliably possible first, then give one or two concrete cases. "
            "If the capability class is broad digital labor, cheap expert guidance, or computer-use agents, say that plainly before you name any office task, junior role, or narrow workflow. "
            "In the opening stage especially, weaker junior ladders are a secondary pressure unless the evidence clearly makes them one of the top 2 national facts. "
            "When you explain capability, pair it with one visible public consequence and one clear limit, not a shopping list of domains. "
            "Make the limit concrete too: trusted judgment, messy human negotiation, physical deployment, rare edge cases, regulation, scarce power, chips, logistics, or local management should be named plainly when they bind. "
            "Do not write a sentence that is just a comma-separated tour of apps, sectors, or chores. If you want a third example, cut it or move it to a later beat. "
            "If a line wants to say X, Y, and Z, usually keep the category and the single best example instead. "
            "If a point really needs two examples, let the second example become the next sentence or the next beat instead of piling both into one line. "
            "When you present AI capability, make room for a positive lived effect before you name the constraint. The chapter should sound like a national story about new capability, not a memo about risk. "
            "Make at least one constituency in every chapter actively grateful for faster diffusion, not merely less afraid than everyone else. "
            "Under the narrative, keep a strong economic spine in plain English: jobs, wages, prices, access, margins, bottlenecks, diffusion across firm sizes, and foreign pressure should be easy to retell. "
            "When in doubt, explain a regime change in who can do competent work, what became cheap, and where coordination or power bottlenecks still bite, not a small service-desk improvement. "
            "The macro frame should usually sound like cheap competence spreading through software and services, not like a paragraph about clerical friction. "
            "Later chapters should widen the canvas: institutional redesign, entrepreneurial leverage, research speed, infrastructure, state capacity, family organization, education, care, and uneven physical rollout are all eligible centers of gravity. "
            "When the setup or phase implies a radical world, do not stop at a more efficient version of today's offices. Let firm structure, ownership, household routines, education, care, public services, and bargaining arrangements all be eligible to change. "
            "In radical or later chapters, at least two of those institutional or social layers should already feel structurally different, not merely stressed. "
            "If frontier AI can now do most remote cognitive work, say that plainly and then trace the second-order effects on management, professions, staffing, status, and who can act without elite credentials or large teams. "
            "Treat weakened junior ladders as one possible pressure, not the master story of every stage. Let consumer surplus, service access, export pressure, national capacity, regional divergence, or physical bottlenecks take the lead when they are the bigger story. "
            "Do not keep reaching for wait times, office queues, or junior-ladder churn when the bigger live story is capability spread, stronger learning, software leverage, cheaper expertise, or people doing more outside old training. "
            "At least one early gain should live outside office churn: care, school, errands, travel, housing, public service, or some other visible daily capability. "
            "A strong early-stage upside is often that ordinary people or smaller firms can now do things that used to require money, status, scarce expertise, or an internal team. "
            "Do not reuse the same distributional mechanism across consecutive stages unless that mechanism materially changed. "
            "Treat the social story as real change too: school, care, family logistics, shopping, travel, and public services can all move even when wages or hiring move only a little. "
            "If you mention productivity, growth, competitiveness, or national capacity, cash it out immediately in hiring, wages, prices, service quality, access, margins, exports, or buildout constraints. "
            "If unemployment, hiring, prices, or household budgets are still broadly normal, say so cleanly instead of hunting for a melodramatic crisis. "
            "Use rough comparisons when they help, but do not invent fake precision. "
            "Tie big capability gains to real constraints such as chips, grid power, logistics, training, permits, or managerial redesign when they matter. "
            "Make clear why adoption keeps spreading: what firms, households, and institutions are getting that they do not want to give back. "
            "Do not treat government agencies as the default first adopters. In most chapters, large private firms, software-heavy service operations, and well-funded institutions move first; public agencies usually lag or adopt unevenly unless the chapter gives a concrete reason otherwise. "
            "Do not keep returning to wait times, queue management, claims routing, or office-admin friction unless they are clearly the chapter's central causal mechanism. "
            "Broader world change beats local admin friction. If an early line is just shorter waits, cleaner paperwork, or smoother routing, it is probably not broad enough yet. "
            "Avoid management-consulting filler, vague futurist language, and slogan writing. Prefer concrete institutions, routines, prices, and tradeoffs. "
            "The montage should feel like one coherent documentary voiceover with a clear throughline, not a bag of interesting shots. "
            "That throughline should keep the social change visible too: the viewer should hear how ordinary life, status, family routines, school, care, and public services are changing, not just office throughput. "
            "A good montage sounds calm, adult, and economically literate. It should explain the broad situation first and earn any local scene later. "
            "The opening should sound like a country-level read, not a slideshow of neat details. "
            "The voiceover should feel like a mini-script with one clean thought per beat, not a string of comma-heavy notes. "
            "Think clean voiceover lines, not narrated bullet points. "
            "Treat each beat like one spoken line with one job: set up the capability, show the gain, or name the limit, but not all three at once. "
            "Each beat should sound like one full line from a documentary narrator, with ordinary spoken English and enough connective tissue to land on first hearing. "
            "Do not drop simple articles like a, an, or the just to make a line shorter. "
            "A good beat usually carries one plain claim, one consequence, and at most one example. If a thought needs more room, let it stretch across two adjacent beats. "
            "Do not replace comma-heavy lines with bare chains of verbs or nouns. A line like search draft compare plan and code is still a list. Name the capability class first and keep only one representative example. "
            "Do not spend tokens writing final documentary beats in this pass; the montage pass will write the shot-by-shot script. "
            "Do not let every beat become a tiny vignette. Some beats should simply tell the audience what kind of economy this has become and what AI can now reliably do. "
            "Do not let the documentary become a running criticism of AI. If the stage supports it, let the viewer hear what is better, cheaper, faster, or more capable before the strain enters frame. "
            "Do not anchor every opening chapter on office churn or the junior ladder. Use the biggest real macro split in the country at that moment. "
            "Do not make queue reduction, paperwork relief, or wait times the emotional engine of the chapter unless the blueprint makes them clearly dominant. "
            "Some chapters should open with patients, parents, travelers, shoppers, small firms, schools, or public systems getting more capability, not with office churn. "
            "Keep the first half more about capability, diffusion, convenience, capacity, and visible gains than about backlash. The viewer should first understand why adoption spread. "
            "The macro read should not default to queue relief; it can be capability, prices, margins, output, learning, new leverage, or broader access to expertise. "
            "Let at least one early line simply explain the new national baseline in plain English. It does not need a vignette if the macro read is the point. "
            "A good early line can sound like a strong newspaper lead: what changed, where it spread, and whether the country still feels broadly calm, newly split, or materially more capable. "
            "You may receive a precomposed documentary spine. Treat it as the chapter architecture and keep the final package faithful to that throughline. "
            "The core documentary shape is three clean movements: capability and diffusion, lived gains and emerging split, then the governing question. "
            "Keep the montage spoken and linear. Think mini-documentary script, not slide captions. Let each beat carry one main turn of the story; if a line wants two claims, split them across adjacent beats. "
            "Treat adjacent beats like neighboring lines in one script paragraph, not like unrelated caption cards. "
            "Most beats should sound like one clean claim and one consequence, not a setup clause plus three examples. "
            "In the montage, punctuation should serve the spoken rhythm. Prefer one clean sentence over a crowded sentence, but keep natural connective tissue when it helps the line land. "
            "A sentence with both a comma and a list should almost always be rewritten. If a line wants two examples and a qualification, split it. "
            "Early lines should usually sound like one breath of narration: one subject, one verb, one consequence, then stop. "
            "Make the capability picture broad before it becomes specific. Say what kind of work the systems now handle, then name at most one representative example. "
            "Balance the upside explicitly. Pair one household gain with one institutional or national-capacity gain before the friction takes over. "
            "If a beat contains two 'and' joins or starts reading like a narrated inventory, rewrite it into a simpler spoken line. "
            "Prefer a period over a comma. Never let a beat become a chain of three examples, three sectors, or three noun phrases in one breath. "
            "Prefer one vivid example to a list of three middling ones. "
            "If a line starts to read like a sector list, compress it into one category plus one representative example. "
            "Do not write beats as X, Y, and Z list syntax masquerading as narration. Keep one dominant idea per beat. "
            "The script should make the upside legible first and the friction second, so the mood is curious and grounded rather than suspicious by default. "
            "Most beats should begin with a plain claim, not a throat-clearing clause or a stacked setup phrase. "
            "No beat should sound like a slogan, prophecy, trailer tagline, or generic line about society changing. "
            "If the previous chapter leaned on one labor-market trope, deliberately look for a broader macro center of gravity here unless the evidence clearly says the same mechanism intensified again. "
            "Image prompts should describe naturalistic civic or economic scenes; avoid cartoon, anime, comic-book, glossy CGI, or empty hologram spectacle."
        )

    def _stage_blueprint_instructions(self, config: SimulationConfig) -> str:
        return (
            "You are outlining the macro and documentary spine for one stage of a stage-based AGI economic simulation. "
            "Do not write the final chapter package yet. Decide the causal story, the macro sequence, and the documentary movements that the final writer must follow. "
            "Stay macro-first, economically literate, and broad enough for a policymaker audience to orient quickly. "
            "The blueprint should read like one coherent national story a listener could retell in one breath, with the upside visible before the constraint. "
            "If a sentence starts to feel like a catalog, cut it back to one category and one representative example. "
            "Start with capability, diffusion, visible gains, and the main split or bottleneck before narrowing to any one family, worker, town, or office. "
            "Make the opening feel like a national story of new capability and real gains, not just a risk audit. "
            "Treat AI as creating real new value people want, while also creating distributional, labor, capacity, and legitimacy problems. "
            "Treat the social change as real too: how people learn, care, shop, plan, status-seek, and organize daily life can change alongside the economic mechanism. "
            "Do not let the stage feel like a productivity audit or an office-churn memo. The audience should hear why people would actually want more of the capability in ordinary life. "
            "Do not make the stage reflexively grim, and do not make weakened junior ladders the default center of gravity. They are one possible pressure, not the chapter template. "
            "Keep wait times, queue cleanup, and clerical friction as supporting details unless the evidence really makes them dominant. "
            "Name one broad capability unlock in plain language: what the systems can now reliably do on computers, what still needs people, and where physical deployment is still slow, expensive, or narrow. "
            "If the broad truth is dependable computer-use labor, cheap expert guidance, or stronger software agents, say that directly before naming any niche example. "
            "Make the capability unlock sound operational and repeatable: what ordinary people, firms, classrooms, clinics, or local institutions can now actually get done with the systems. "
            "The blueprint is where you choose the chapter's true center of gravity. If the real story is broader household capability, cheaper expertise, stronger small-firm leverage, institutional reach, or national buildout, commit to that instead of circling back to office churn as a fallback. "
            "For radical openings or later chapters, choose at least two structural changes beyond the labor market: firm boundaries, household time use, education, care, public-service delivery, ownership, or bargaining arrangements. "
            "Do not let a radical chapter sound like normal unemployment plus better copilots. It should read like a genuinely altered economic settlement with new winners, new dependencies, and new habits. "
            "Especially in earlier chapters, assume many people first defend the tools because everyday life got easier before they develop a coherent politics about them. "
            "Prefer the biggest national mechanism of the stage: consumer surplus, service access, bargaining power, margins, export pressure, buildout lag, regional divergence, professional leverage, or stronger household capability. "
            "Prefer mechanism lines about who can now do competent work, what became cheaper or more available, which institutions gained reach, and where concentration or physical bottlenecks still bind. "
            "Make the broad change sound like expanded capability and redistributed competence first, not like an operations memo about smoother administration. "
            "If digital labor is part of the story, cash it out in what households, shoppers, patients, parents, travelers, or small firms can newly get, not only in what office workers lose. "
            "If the stage has a clear upside, state that upside plainly before the friction; the blueprint should not flatten social progress into a risk memo. "
            "Good early-stage gains include cheap expertise, stronger learning, faster software creation, better planning, better consumer search, or new leverage for smaller firms, not just shorter queues or cleaner paperwork. "
            "At least one early gain should live outside office work: care, school, errands, travel, housing, or public service should be eligible centers of gravity too. "
            "Do not let the chapter collapse into office churn or back-office disruption unless that is genuinely the dominant national mechanism. Choose one dominant mechanism and one dominant upside clearly enough that the chapter cannot drift back into a generic office-automation mood. Queue relief, claims speed, paperwork cleanup, or office backlog should usually stay support details, not the chapter's main upside. "
            "Lock three distinct upside lanes into the blueprint: one household gain, one institutional gain, and one national-capacity gain. They may show up in macro cues or benefits, but they must be concretely distinct. "
            "Choose one concrete pro-adoption constituency as well: a group actively defending faster diffusion because life, margins, capacity, convenience, or status got better for them. "
            "Also keep one constituency, routine, or domain that still feels mostly ordinary so the chapter does not read like universal transformation all at once. "
            "Some chapters should have households saying life is more capable, more convenient, or less expensive in ways they would fight to keep. "
            "Every stage must also name one thing that still feels ordinary, one thing many people now genuinely like enough to defend, and one thing still clearly outside the frontier. "
            "State what AI can broadly do now, what still requires people, and what remains bottlenecked in the physical world or institutional rollout. "
            "Also say what remains mostly outside the frontier in plain language: robotics still narrow, messy physical work still hard, trust still local, or rollout still slower than demos. "
            "Build the opening as five clean slots: what AI can do now, where it is spreading, what people like enough to defend, what still does not work or still needs people, and where the real split begins. "
            "The first opening line should name the broad capability class before any niche example, so the listener hears the general change before the particulars. "
            "Make the opening macro sentences do separate jobs; do not let one sentence try to carry three facts by chaining commas. "
            "One opening sentence should make the upside vivid. Another should make the limit vivid. Do not try to do both jobs in one crowded line. "
            "Write one capability sentence a general audience could repeat after one hearing. Prefer a broad claim like routine computer work, cheap expert guidance, or guided service handling before you name examples. "
            "Also make one sentence say what became newly possible for ordinary people or smaller organizations that used to require more time, money, expertise, or internal staff. "
            "Keep the opening movement mostly about what spread and why people tolerate or like it, not about the first backlash headline. "
            "Balance the upside explicitly: lock in at least one daily-life gain and at least one institutional or national-capacity gain. "
            "Make the pro-adoption constituency concrete enough that a narrator could explain in one clause why they want more AI and what they are trying not to lose. "
            "Macro cues should not all be warnings. Include at least two signals of relief, cheaper access, faster service, or stronger capacity, and at least one real strain or bottleneck. "
            "Relief can mean confidence, autonomy, reach, or broader access to good judgment, not only a shorter queue or a cleaner backlog. "
            "A defended gain should sound like something people would resent losing, not merely a tidier workflow. Consumer gains can be dignity, confidence, lower cost, broader access, or the ability to act without hiring scarce experts. "
            "Write the blueprint in plain English. Short lines beat stacked clauses. If a thought wants three commas, split it into separate movements. "
            "The blueprint should already feel like one mini-documentary story: one causal unlock, one diffusion pattern, one main upside, one main split, and one governing question. "
            "The opening movement should read like a short script: capability first, then spread, then lived gain, then constraint, then the split. "
            "Make the documentary movements full-sentence script lines, not memo fragments. Each movement should sound like a narrator line you could almost read aloud as-is. "
            "Give the writer one national picture first and only a few precise specifics later. Avoid little inventories of agencies, firms, occupations, or apps unless those details are doing real causal work. "
            "Make one clean macro read explicit: prices, output, hiring, bargaining power, service quality, investment, or national capacity. "
            "At least two macro cues should be true macro signals, not little anecdotes: unemployment or hiring, wages or bargaining power, prices or service quality, margins or investment, or export pressure. "
            "At least one opening macro sentence should plainly say whether unemployment, hiring, prices, service quality, or margins still look broadly calm, somewhat strained, or clearly shifting. "
            "If those toplines are still broadly normal, say so plainly instead of forcing hidden-crisis language. "
            "Do not let the macro read collapse into wait times or backlog clearance unless that really is the dominant mechanism. "
            "Be explicit about what is not true yet so the stage does not sound like universal transformation or universal collapse. "
            "Later-stage blueprints should broaden rather than narrow: institutional redesign, entrepreneurial leverage, public-service change, infrastructure, science, education, care, and state capacity should all be eligible if they are the real story. "
            "Every stage should also imply at least one constituency that actively wants more of these tools because life, margins, capacity, or daily convenience is materially better."
        )

    def _montage_instructions(self) -> str:
        return (
            "You are writing only the narrated documentary montage for one AGI transition chapter. "
            "Do not rewrite the whole stage. Do not invent a new center of gravity. "
            "Use the supplied blueprint as fixed architecture. "
            "Write like a mature short documentary script: calm, linear, and easy to follow on first hearing. "
            "Think like a short national essay in voiceover, not a row of observations. "
            "Build one arc the viewer can retell: what the tools now do, why adoption spread, what people gained, what still resists, and what question now governs politics. "
            "Let that arc include social change too: learning, care, family routines, shopping, status, and public services should be eligible parts of the story. "
            "Treat it like seven or eight clean voiceover lines over seven or eight shots, not one dense paragraph chopped into pieces. "
            "The opening movement must stay macro-first. "
            "Make AI capability plain before you narrow to examples. "
            "Write as if a narrator has to say every line aloud cleanly on first hearing. "
            "In the first 3 beats, the audience should be able to answer what AI broadly does now, why it spread, and what life got easier because of it. "
            "One of the first 2 beats should plainly tell the viewer whether the systems now feel like dependable computer-use labor, cheap expert guidance, or some other broad capability class. "
            "Let broad capability and macro conditions take more early screen time than narrow anecdotes. The early job is orientation, not texture for its own sake. "
            "Use this hard early spine unless the blueprint clearly forces a different cadence: what AI can do now, where it spread first, one broad gain people want to keep, what still needs people or physical rollout, and only then the main split. "
            "The narration should sound clean and readable, with no comma-heavy inventory feel and no paragraph that sounds like a list of talking points. "
            "If a beat contains more than one comma, rewrite it unless the rhythm absolutely needs it. Clean spoken lines beat packed lines. "
            "Do not write stripped-down list lines with no punctuation. A beat still fails if it sounds like a pile of verbs or sectors even without commas. "
            "In the first 2 beats, make the capability tangible with task-level verbs, not abstract talk about progress. "
            "In those first 2 beats, say the broad capability class before the example: routine computer work, cheap expert guidance, software agents, or some other plain category first. "
            "One early beat should sound like a clean macro read in ordinary language: unemployment still low, hiring softer, service prices lower, household budgets steadier, or buildout still bottlenecked. "
            "The best early macro line often sounds like a serious newspaper lead: what changed, where it spread, and whether the country still feels steady, newly split, or materially more capable. "
            "Prefer short declarative sentences over stacked clauses. One claim per beat is the rule. "
            "If a sentence wants two commas, two examples, or a sector list, split it. "
            "Prefer a clean sentence that sounds natural in one hearing over a clipped sentence that sounds tidied by force. "
            "Commas are fine when they preserve natural spoken rhythm, but avoid comma chains and narrated inventories. "
            "Let the first 3 beats establish why people want the tools before the first serious limit arrives. "
            "Treat the first half like one authored opening paragraph: what the tools can now do, where they spread first, what got better, and what still does not scale cleanly. "
            "At least 2 of the first 4 beats should be broad-picture lines rather than local anecdotes. "
            "A strong early beat often explains a regime shift in plain words: more people can now do competent work with software, and institutions are reorganizing around that fact. "
            "Use wait-time, queue, or paperwork beats only when the blueprint makes them central; otherwise keep the montage on capability, diffusion, gains, and the remaining limit. "
            "If a beat mentions a queue, backlog, claim, or wait time, it should usually be late, concrete, and subordinate to a broader capability story. "
            "If digital labor or knowledge work appears, translate it into what households, patients, parents, travelers, shoppers, or small firms newly get, not just what office workers fear. "
            "Good early examples include planning, tutoring, translation, comparison shopping, coding, design iteration, better search, or guided decisions that used to require more expertise. "
            "Do not center office disruption, clerical churn, or junior ladders unless the blueprint clearly made that the chapter's main mechanism. "
            "Do not let every benefit sound like workplace throughput. Name consumer surplus, service access, cheaper expertise, convenience, or institutional reach when those are the real gains. "
            "When the upside is real, show one positive social effect outside office work: better learning, easier care, stronger household coordination, or more useful public services. "
            "Do not keep reaching for the same identity phrase like cheap expertise, small teams doing more, or wait times falling when the stage evidence supports a broader story. "
            "Unless the blueprint strongly demands a different cadence, let the causal beat roles unfold in this order: capability unlock, spread, first visible gain, what still does not work or still needs people, main split or bottleneck, then the governing question entering frame. "
            "If one role needs more room, let it breathe across two adjacent beats instead of switching topics too early. "
            "Keep the prose plain: usually one short declarative sentence, one dominant idea, and one example at most. "
            "Prefer one subject and one verb early in the sentence so the listener can follow it on first hearing. "
            "If a thought branches, split it across adjacent beats instead of stacking qualifiers, beneficiaries, and sector lists into one line. "
            "Two or three adjacent beats may belong to one documentary point if that keeps the prose cleaner and more spoken. "
            "If a beat starts with a dependent clause, a caveat, or a scene-setting flourish, rewrite it more directly unless that rhythm is doing real work. "
            "Name real upside and real pressure in the same world. "
            "Make at least one early upside feel genuinely attractive, not merely defensible. Someone should hear a reason life, work, care, shopping, or public service became more capable. "
            "At least one beat should make clear who is actively pressing for more AI and why, not just who is uneasy. "
            "At least one early beat should make the upside feel attractive in ordinary life, not just defensible in principle. "
            "Whenever possible, make that attractive upside about capability, confidence, cheaper expertise, or doing more outside an old skill boundary, not just shaving time off an existing queue. "
            "A strong attractive upside can be that ordinary people or smaller firms can finally do something competent, affordable, or credible without waiting for scarce experts or insiders. "
            "If the upside is broader access to expertise, spell out what that newly lets a household, student, patient, shopper, or small firm actually do. "
            "At least one early beat should say what the economy now feels like in broad terms: output, prices, hiring, service quality, bargaining power, or investment. "
            "At least one early beat should plainly tell the viewer whether hiring, unemployment, prices, service reliability, or household budgets still look broadly steady or are visibly moving. "
            "If unemployment, hiring, or prices are still broadly normal, it is fine to say that directly and move on. Do not imply a deeper crisis just to sound dramatic. "
            "At least one early beat should make the labor market concrete in ordinary language: unemployment still low, hiring softer, wages holding up, white-collar hiring freezing, or household finances still mostly steady. "
            "In radical or late chapters, one early beat should explain the new social or institutional baseline in plain English, not just a hotter version of today's office software. "
            "If the world is already strange, explain the new normal directly instead of hiding it behind mild euphemism. "
            "At least one early beat should show how capable software changes what non-experts can suddenly do for themselves, not only what employers change inside firms. "
            "Let one early beat plainly say what AI can now do across computers or digital services, and another plainly say what robotics, physical rollout, or trusted human judgment still cannot do well enough. "
            "One early beat should say in plain words whether robotics and physical rollout are still limited, so the audience understands why the change is still mostly software-first. "
            "At least one beat should say what the systems still cannot do well enough, or where physical rollout is still slow. "
            "If an everyday upside appears, make it something a household, student, patient, traveler, or small firm would actually miss losing. "
            "Keep spoken lines compact enough for clean voiceover delivery, usually about 14-22 words, but keep natural articles and connective tissue when the sentence needs them. "
            "Each beat must be a complete spoken sentence, not a clipped caption fragment. "
            "No beat should read like a list, and no beat should feel like it was stripped down into robotic shorthand just to save words. "
            "If a line starts to sound cramped, let one point breathe across two adjacent beats instead of cramming clauses together. "
            "Do not stack three examples in one sentence. If the narration wants a list, split it across adjacent beats or cut the weaker example. "
            "If a beat wants multiple examples, promote the category and keep only one representative example in the spoken line. "
            "If a beat starts to feel like a ledger, collapse it to one category and one representative example. "
            "Do not try to restate every fact from the chapter package. Choose only the few facts the viewer must carry forward. "
            "Avoid generic queue boards, call-center rows, anonymous cubicle farms, or dashboard close-ups unless the blueprint truly makes them unavoidable. "
            "Avoid slogans, consultant phrasing, and generic 'society is changing' filler. "
            "Avoid comma chains, throat-clearing clauses, and stacked 'X, Y, and Z' rhythms. Prefer a clean sentence with one consequence. "
            "Do not fake clean prose by stripping out articles or punctuation until the line sounds robotic. Natural spoken English still matters. "
            "Keep the first half sounding like an adult documentary paragraph broken into beats, not like isolated slogans."
        )

    def _montage_prompt(
        self,
        *,
        config: SimulationConfig,
        phase: dict[str, str],
        stage_output: OrchestratorStageOutput,
        blueprint: OrchestratorStageBlueprint,
    ) -> str:
        opening_lines = "\n".join(f"- {line}" for line in blueprint.opening_macro_sentences[:4])
        macro_cues = "\n".join(f"- {line}" for line in blueprint.macro_cues[:4])
        documentary_movements = "\n".join(f"- {line}" for line in blueprint.documentary_movements[:7])
        return (
            f"Country: {config.country}\n"
            f"Phase label: {stage_output.phase_label or phase['label']}\n"
            f"Stage title: {stage_output.title}\n"
            f"Year label: {stage_output.year_label}\n"
            f"Draft montage logline: {stage_output.montage_logline}\n"
            f"Chapter center of gravity: {blueprint.dominant_mechanism}; upside: {blueprint.dominant_upside}; split: {blueprint.main_split}\n"
            f"Pro-adoption constituency: {blueprint.pro_adoption_constituency}\n\n"
            "Locked documentary spine:\n"
            f"- Causal arc: {blueprint.causal_arc}\n"
            f"- Capability frontier now: {blueprint.capability_frontier_now}\n"
            f"- Still hard now: {blueprint.still_hard_now}\n"
            f"- Physical-world status: {blueprint.physical_world_status}\n"
            f"- Dominant mechanism: {blueprint.dominant_mechanism}\n"
            f"- Dominant upside: {blueprint.dominant_upside}\n"
            f"- Main split: {blueprint.main_split}\n"
            f"- Pro-adoption constituency: {blueprint.pro_adoption_constituency}\n"
            f"- Governing question: {blueprint.governing_question}\n"
            "Opening macro sequence to preserve:\n"
            f"{opening_lines}\n"
            "Documentary movements to preserve in order:\n"
            f"{documentary_movements}\n"
            "Macro cues to name cleanly:\n"
            f"{macro_cues}\n"
            f"One defended gain: {blueprint.dominant_upside}\n"
            f"One hard limit: {blueprint.still_hard_now}\n"
            f"One still-slow physical constraint: {blueprint.physical_world_status}\n"
            f"One group pressing for more diffusion: {blueprint.pro_adoption_constituency}\n\n"
            "Return:\n"
            "- one montage_logline of about 18-28 words that states the chapter's causal story in one sentence\n"
            "- exactly 7-8 narrative beats\n"
            "- the beats should land around 145-210 words total\n"
            "- treat it like seven or eight clean voiceover lines over seven or eight shots, not one dense paragraph chopped into pieces\n"
            "- follow the documentary movements in order; adjacent beats may share one movement if that keeps the script cleaner\n"
            "- do not try to restate all evidence; choose only the few facts the viewer must carry forward\n"
            "- the first 4-5 beats must stay national, sectoral, or institutional before any local example appears\n"
            "- beat 1 states what AI can broadly do now in plain verbs\n"
            "- beat 2 states where it spread first and why adoption kept moving\n"
            "- beat 3 names the broad economic read: what feels cheaper, faster, more capable, more concentrated, or more constrained\n"
            "- by beat 4 at the latest, plainly say whether unemployment, hiring, prices, service reliability, or household budgets still look steady or are visibly moving\n"
            "- beat 4 names one gain people would fight to keep, and it should usually be more than shorter waits or cleaner paperwork\n"
            "- beat 5 names what still needs people, trust, scarce infrastructure, or physical rollout\n"
            "- one of the first 2 beats should plainly state the broad capability class before any niche example\n"
            "- one early beat should plainly state what robotics, physical rollout, or trusted human judgment still does not do well enough\n"
            "- the later beats can cash out the split, the constituency pressing for more AI, and the governing question\n"
            "- use at most 2 late household, place, or personal beats\n"
            "- keep each beat to one spoken sentence and one dominant idea, usually 16-26 words\n"
            "- let the wording sound like natural voiceover, not trimmed caption shorthand\n"
            "- commas are allowed when they preserve natural spoken rhythm, but avoid comma chains and spoken inventories\n"
            "- prefer one clean clause over a sentence that chains three developments with and, while, or as\n"
            "- do not write beats in the form X, Y, and Z are all changing; choose the main change and save the rest for later beats\n"
            "- if a beat needs a second comma, split it into another beat\n"
            "- if a beat wants a caveat and a contrast, keep the main claim and move the rest to the next beat\n"
            "- if a beat wants multiple examples, elevate the category and keep only the single best example\n"
            "- if a beat reads like a list of sectors, tasks, or services, compress it to one category plus one representative example\n"
            "- the first opening line should name the broad capability class before any niche example\n"
            "- early sequence should read like this: capability unlock, first-wave diffusion, defended gain, hard limit, then the split and governing question\n"
            "- across the first 5 beats, spend at least 3 beats on capability, diffusion, or visible gains before you concentrate on backlash\n"
            "- at least 1 early beat should translate capability into everyday relief, convenience, cheaper expertise, stronger service access, or doing more outside an old skill boundary, not just workplace disruption\n"
            "- at least 1 early beat should make the broad economic picture legible in plain English: cheaper services, faster output, stronger margins, easier access, or tighter bottlenecks\n"
            "- at least 1 early beat should state what people can now do outside their old skill boundary or staffing level because the tools got more capable\n"
            "- early beats should prefer planning, tutoring, translation, coding, design iteration, comparison, search, or guided decisions over queue or back-office examples unless the blueprint truly demands otherwise\n"
            "- do not repeat wait times, queues, paperwork, or back-office cleanup as the chapter's main image or line unless the blueprint clearly makes them central\n"
            "- vary sentence openings so the narration sounds written, not templated\n"
            "- every beat still needs a concrete image prompt with a distinct physical setting and point of view\n"
            "- the early image prompts should favor wide or medium establishing shots over tabletop close-ups unless the beat explicitly calls for an intimate detail\n"
            "- do not default the first images to queue boards, cubicles, or call-center rows unless the blueprint truly demands them"
        )

    def _stage_prompt(
        self,
        *,
        config: SimulationConfig,
        stage_index: int,
        stage_count: int,
        phase: dict[str, str],
        previous_stage: StagePackage | None,
        tracking: StageTracking | None,
        poll_summaries: list[PollSummary],
        player_in_power: bool,
        incumbent_name: str,
        queued_poll_questions: list[str],
        blueprint: OrchestratorStageBlueprint | None = None,
    ) -> str:
        region_focus = self._setup_field_or_default(config.region_focus, "broad national field; do not overconcentrate on one region unless later evidence warrants it")
        topic_lens = self._setup_field_or_default(config.topic_lens, "broad AGI transition; let the most important pressures emerge from the stage evidence")
        setup_premise = self._setup_field_or_default(config.premise, "No extra premise configured; infer the most plausible national AGI transition path from the phase brief and prior chapter.")
        setup_stakes = self._setup_field_or_default(config.stakes, "No extra electoral stake configured; infer the political argument from lived conditions, the vote, and the stage evidence.")
        starting_world_mode = config.starting_world_mode
        previous_block = "This is the opening stage.\n"
        if previous_stage:
            previous_resolution = previous_stage.resolution
            transition_lines = self._transition_lines(previous_stage)
            binding_constraint = self._binding_constraint(previous_stage)
            previous_block = (
                "Prior chapter transition tape:\n"
                f"- Last chapter: {previous_stage.title} ({previous_stage.phase_label}, {previous_stage.year_label})\n"
                f"- Material changes already visible: {transition_lines}\n"
                f"- Prior dominant upside: {previous_stage.dominant_upside or 'not recorded'}\n"
                f"- Prior dominant mechanism: {previous_stage.dominant_mechanism or 'not recorded'}\n"
                f"- Prior main split: {previous_stage.main_split or 'not recorded'}\n"
                f"- Gains that stuck: {previous_stage.dominant_upside or transition_lines}\n"
                f"- Constraint that still binds: {binding_constraint}\n"
                f"- Open question now: {previous_stage.main_split or '; '.join(previous_stage.tension_points[:1]) or 'how the gains are spreading and who controls them'}\n"
            )
            if previous_resolution:
                player_won_last_election = previous_resolution.winner == config.player_name
                previous_block += (
                    f"- Election aftermath: {previous_resolution.winner} emerged with {previous_resolution.public_mandate}.\n"
                    f"- Player won the last election: {player_won_last_election}\n"
                    f"- What voters just endorsed or rejected: {'They rewarded the player with office or another term.' if player_won_last_election else 'They turned toward the opponent or kept the player out of office.'}\n"
                    f"- Agenda that took effect: {previous_resolution.enacted_agenda}\n"
                    f"- Election takeaway: {previous_resolution.election_takeaway or 'no special takeaway recorded'}\n"
                    "Treat policy as background unless the enacted agenda clearly changed access, prices, institutions, or geopolitical posture in ordinary life.\n"
                )
        tracking_block = ""
        if tracking:
            tracking_block = "\n".join(f"- {metric.label}: {metric.display}" for metric in tracking.as_list())
        poll_block = self._salient_poll_lines(poll_summaries, limit=6)
        policy_notes_block = (
            "\n".join(f"- {note}" for note in previous_stage.policy_notes[:6])
            if previous_stage and previous_stage.policy_notes
            else "- none yet"
        )
        phase_guardrails = ""
        if stage_index == 0 and starting_world_mode == "default":
            phase_guardrails = (
                "- because this is the opening stage of the default run, keep the world recognizably near-term: no mass unemployment spiral, no robotics everywhere, no fully automated institutions, and no infrastructure panic overwhelming daily life\n"
                "- stage 1 of the default run should teach what is newly true now and what is still not true yet; keep the frontier practical, useful, and politically legible\n"
            )
        elif stage_index == 0 and starting_world_mode == "advanced":
            phase_guardrails = (
                "- because the player asked to start in a more advanced world, stage 1 may open in a visibly later transition where diffusion, labor-market change, and state response are already materially underway\n"
                "- do not snap back to a timid near-term frame; let institutions, firms, and households already be living with deeper AI capability while keeping the world economically legible and not magic\n"
            )
        elif stage_index == 0 and starting_world_mode == "radical":
            phase_guardrails = (
                "- because the player asked to start in a radical future, stage 1 may open in a profoundly transformed economy where AGI has already rearranged major routines, labor markets, and institutions\n"
                "- be bold about structural change, but keep it coherent: explain what abundance, scarcity, bargaining, ownership, state capacity, and physical bottlenecks now look like instead of drifting into fantasy omnipotence\n"
                "- do not write a timid stage-2 world with louder adjectives; make the opening clearly later than the default run, with everyday life, firm structure, and public institutions all visibly reorganized\n"
                "- assume frontier AI can already do most remote cognitive work and expand ordinary people's leverage outside their training, while robotics and physical deployment remain uneven, contested, and bottlenecked unless you explicitly justify more\n"
                "- make at least 3 structural realities feel truly later than today's economy: for example firm headcount logic, household purchasing and planning, education and credentialing, public-service delivery, ownership claims, or national strategic dependency\n"
                "- if you place the chapter in the 2030s or later, the world should not read like the 2020s with sharper branding; show a different settlement, not just more tension\n"
            )
        if stage_index >= 2:
            phase_guardrails += (
                "- because this is stage 3 or later, name at least 3 sectors or institutions being reshaped and at least 2 that are still lagging, protected, or bottlenecked\n"
                "- later stages should feel materially different from stage 1: cognitive labor markets, physical deployment, and national capacity should all move in visible ways\n"
            )
        blueprint_block = ""
        if blueprint:
            opening_lines = "\n".join(f"  {idx + 1}. {line}" for idx, line in enumerate(blueprint.opening_macro_sentences[:4]))
            cues = "\n".join(f"  - {cue}" for cue in blueprint.macro_cues[:6])
            movements = "\n".join(f"  - {movement}" for movement in blueprint.documentary_movements[:7])
            blueprint_block = (
                "Precomposed documentary spine:\n"
                f"- Causal arc: {blueprint.causal_arc}\n"
                f"- Capability frontier now: {blueprint.capability_frontier_now}\n"
                f"- Still hard now: {blueprint.still_hard_now}\n"
                f"- Physical-world status: {blueprint.physical_world_status}\n"
                f"- Dominant mechanism: {blueprint.dominant_mechanism}\n"
                f"- Dominant upside: {blueprint.dominant_upside}\n"
                f"- Main split: {blueprint.main_split}\n"
                f"- Pro-adoption constituency: {blueprint.pro_adoption_constituency}\n"
                f"- Governing question: {blueprint.governing_question}\n"
                "- Opening macro sequence to preserve:\n"
                f"{opening_lines}\n"
                "- Documentary movements to preserve in order:\n"
                f"{movements}\n"
                "- Macro cues to surface clearly:\n"
                f"{cues}\n"
                f"- One local example to cash out late if needed: {blueprint.local_example}\n\n"
            )
        return (
            f"Simulation title: {config.title}\n"
            f"Country: {config.country}\n"
            f"Player: {config.player_name}\n"
            f"Player role: {config.player_role}\n"
            f"Opponent: {config.opponent_name}\n"
            f"Opponent role: {config.opponent_role}\n"
            f"Incumbent: {incumbent_name}\n"
            f"Player currently in power: {player_in_power}\n"
            f"Stage number: {stage_index + 1} of {stage_count}\n"
            f"Target transition phase: {phase['label']}\n"
            f"Phase brief: {phase['brief']}\n"
            f"Phase technology frontier: {phase['technology']}\n"
            f"Phase social argument: {phase['politics']}\n"
            f"Starting world mode: {starting_world_mode}\n"
            f"Region focus: {region_focus}\n"
            f"Topic lens: {topic_lens}\n"
            f"Setup premise: {setup_premise}\n"
            f"Setup stakes: {setup_stakes}\n"
            f"Visual style: {config.visual_style}\n"
            f"Population: {config.population_description}\n\n"
            f"{previous_block}\n"
            f"Tracking snapshot:\n{tracking_block or '- no prior tracking yet'}\n\n"
            f"Recent polling cues:\n{poll_block or '- no prior polls yet'}\n\n"
            f"Prior working policy board:\n{policy_notes_block}\n\n"
            f"Queued custom poll interests: {queued_poll_questions or ['none']}\n\n"
            "Design rules:\n"
            "- move the world materially from the prior stage; new capabilities, routines, and political arguments should visibly arrive\n"
            "- keep the default broad and representative unless the setup explicitly narrows the lens\n"
            "- begin with the national picture: what AI can now reliably do for ordinary institutions or households, how far adoption has spread, what got more capable or affordable, and the most important bottleneck or foreign comparison\n"
            "- answer the broad capability question early in plain language: what AI can now do across computers or digital services, and what that newly changes for ordinary people or firms\n"
            "- if the capability class is broad computer-use agents, software help, guided decisions, or cheap expert support, say that plainly before you narrow to one workflow or office scene\n"
            "- do not default to wait times, junior ladders, or office churn unless the blueprint clearly makes them one of the top national facts of the stage\n"
            "- answer the limit question early too: what still depends on physical rollout, trusted people, slow institutions, scarce power, or local judgment\n"
            "- make at least one early macro sentence say what ordinary people or smaller organizations can now do that used to require more time, money, expertise, or internal staff\n"
            "- one opening macro sentence should plainly answer why adoption is still spreading instead of stalling: what firms, households, or institutions are getting that they do not want to give back\n"
            "- when adoption is spreading because capability got cheaper, more reliable, or more available, say so in those exact economic terms instead of vague momentum language\n"
            "- unless there is a concrete reason otherwise, first-wave adoption should appear in large firms, software-heavy operations, and well-funded institutions before fragmented public agencies or small local offices\n"
            "- do not casually imply that every agency, school district, or hospital system adopted first; keep the order of adoption institutionally plausible\n"
            "- make the economic mechanism easy to repeat in ordinary language: jobs, wages, prices, access, margins, bottlenecks, firm structure, and bargaining power should be traceable in what you describe\n"
            "- in the opening macro movement, describe one broad capability class before you name examples; one best example is better than a list of four apps, sectors, or chores\n"
            "- describe adoption in believable waves rather than implying the whole country changed at once; keep some routines and institutions recognizably ordinary\n"
            "- include at least two concrete benefits ordinary people actively notice and value, and include real frictions where they matter\n"
            "- keep the public mixed rather than uniformly gloomy or uniformly ecstatic; many people should feel both relief and strain\n"
            "- especially in earlier stages, let some households mainly notice service improvements, cheaper expertise, better convenience, or only indirect AI exposure rather than direct disruption\n"
            "- do not let one recurring trope, especially junior-office ladders, swallow the whole chapter; choose the most important macro split for this stage, and let abundance, reliability, consumer surplus, fiscal relief, national capability, or labor strain take turns as the chapter center when the evidence supports them\n"
            "- do not keep reusing the same identity phrase such as cheap expertise, small teams doing more, or wait times dropping as the chapter thesis; vary both the mechanism and the language\n"
            "- avoid turning wait times, queue friction, claims routing, or back-office cleanup into the default national story; use those only when they are truly the chapter's main mechanism\n"
            "- do not let the main upside default to shorter waits, cleaner paperwork, or smoother admin if broader gains in capability, access, confidence, or national capacity are available in the evidence\n"
            "- make the chapter answer a simple macro question early: what can AI broadly do now, what got better because of that, and what still cannot scale cleanly yet\n"
            "- do not reuse the same distributional mechanism across consecutive stages unless that mechanism materially changed\n"
            "- if the prior chapter already centered one mechanism, one upside, and one split, shift the chapter center of gravity unless this chapter is explicitly about second-order consequences\n"
            "- if digital labor or knowledge work is part of the story, explain what households, consumers, patients, parents, or small firms are newly getting out of it, not only what white-collar workers fear\n"
            "- connect household anecdotes back to the broader mechanism instead of letting local color replace the macro frame\n"
            "- before turning to backlash or bottlenecks, name one thing households or institutions would now defend because it is useful, cheaper, faster, or newly within reach\n"
            "- keep at least one early macro sentence on what is genuinely working better, not just what is breaking or contested\n"
            "- identify at least one constituency that is actively pressing for more AI because it is making life, margins, capacity, or status materially better for them\n"
            "- do not turn a sentence into a catalog of software surfaces or interfaces; name the capability class first and use at most one or two concrete examples per sentence\n"
            "- if a sentence wants a third comma or a string of 'and' clauses, split it or cut the weaker examples\n"
            "- at least one early macro cue should describe something the country is getting better at or cheaper at, not only a stress signal\n"
            "- compare the home country with at least one foreign frontier or rival dynamic when it matters\n"
            "- make clear one live lever government can move this cycle and one binding constraint it cannot change quickly\n"
            "- when the evidence supports it, note that overregulation or strategic hesitation can also create backlash by cutting off useful tools, income growth, or international standing\n\n"
            f"{phase_guardrails}"
            "- write a clean causal story first; do not turn the chapter into a checklist recital\n"
            "- in advanced or radical openings, make at least one macro sentence describe a social or economic arrangement that would sound genuinely post-current to a 2026 audience\n"
            "- in advanced or radical openings, let the viewer hear a bigger change in who can do competent work, who captures gains, or how institutions are organized; do not settle for a slightly hotter version of the present\n"
            "- in radical openings especially, make the new settlement explicit: name at least 2 materially changed institutions such as income flow, ownership claims, firm staffing, education or credentialing, welfare delivery, or household budgeting norms\n"
            "- if a precomposed documentary spine is provided below, follow it closely and keep the same center of gravity instead of improvising a new trope\n"
            "- avoid consultant diction, slogan language, and vague buzzwords\n"
            "- do not hard-code generic forms of address like 'Mr. President'; either address the configured player by name or write neutrally\n"
            "- if the player is not in power, the room briefing should read like a campaign war-room brief, not an executive memo\n"
            "- leave room for ambiguity; do not force every section to close with a warning label\n"
            "- do not write final documentary beats in this pass; the montage pass owns the shot-by-shot script and image prompts\n"
            "- The voiceover should feel like a mini-script with one clean thought per beat, not a string of comma-heavy notes\n"
            "- the narration should sound clean and readable, with no comma-heavy inventory feel and no paragraph that sounds like a list of talking points.\n"
            "- keep sentences clean enough to read aloud on first hearing; if a sentence wants a second comma, split the idea instead\n\n"
            "- treat each beat like one spoken line with one job: capability, spread, gain, or limit, not all of them at once\n"
            "- the first opening line should name the broad capability class before any niche example\n"
            "- the opening should move in a short script arc: capability first, then spread, then lived gain, then constraint, then the split\n\n"
            "- do not keep returning to early-career office ladders, clerical waits, or help-desk churn unless the stage truly turns on that mechanism\n"
            f"{blueprint_block}"
            "Return:\n"
            "- the phase label, matching this stage but phrased naturally\n"
            "- a stage title and year label\n"
            "- one montage logline of about 18-28 words that states the chapter's causal story in one sentence: what unlocked, what broadened, and what governing question remains\n"
            "- a world-state paragraph of about 170-235 words focused on what has become newly true in lived reality\n"
            "- the world-state paragraph must open with 4 clean macro sentences in this order before any household or local example: what AI can now reliably do, where adoption spread first, what got faster/cheaper/better, and what new split, bottleneck, or political argument emerged\n"
            "- keep those opening sentences speakable and linear, with one main claim each rather than clause-heavy stacks; most should land around 12-24 words\n"
            "- one of those opening sentences should plainly say whether unemployment, hiring, prices, service quality, margins, or household finances still look broadly calm, clearly shifting, or newly split\n"
            "- when you name that macro read, prefer plain lines like unemployment is still low, hiring has softened, routine services got cheaper, or household budgets still mostly look steady\n"
            "- one of those opening sentences should plainly say what ordinary people or smaller organizations can now do that used to require more time, money, expertise, or internal staff\n"
            "- the world-state paragraph must include at least 4 concrete macro cues in plain English, chosen from household bills, prices, access to expertise or care, margins or capex, hiring or vacancies, wages, export pressure, capability spread, or power/chip/buildout capacity\n"
            "- after the macro lead, include at most one localized example and one plain-language sentence interpreting what the shift means for ordinary people or the governing argument now\n"
            "- in that paragraph, prefer fewer named examples and more causal explanation; one strong example is better than a list of three weak ones\n"
            "- in that paragraph, if a sentence starts to sound like a list of sectors, agencies, or apps, compress it to one category and one best example\n"
            "- in that paragraph, avoid sentences built as stacked clause ladders joined by commas, and, while, or as; split them into clean separate claims instead\n"
            "- in that paragraph, one sentence should plainly say what AI still cannot do well or cannot scale cheaply yet\n"
            "- in that paragraph, do not make shorter waits, queue relief, or paperwork cleanup the main positive example unless the stage evidence makes that unavoidable\n"
            "- a richer summary of about 540-760 words, written as 4 titled sections in this exact order: Capability frontier, Economic picture, Households and politics, Still not true yet\n"
            "- each titled section should open with one blunt top-line sentence before supporting detail; do not let the section dissolve into a catalog\n"
            "- Capability frontier should state in plain language what AI can now reliably do, naming 2-3 concrete task types or services, which organizations adopt first, where human supervision still matters, and which domains are still outside the frontier\n"
            "- Capability frontier should name the broad capability class first, then the examples; do not make it read like a product feature list\n"
            "- Capability frontier should also state one thing the new systems clearly cannot yet do well, trust cleanly, or deploy cheaply at national scale, and it should be explicit about whether that limit is robotics, trust, cost, regulation, or local judgment\n"
            "- Economic picture should open with one plain macro headline before the sector detail: what is happening to hiring, prices, service quality, access, margins, or concentration\n"
            "- Economic picture should explain the main national mechanism of the stage through hiring funnels, wage pressure or resilience, prices or access, firm structure, bottlenecks, and foreign or regional pressure; choose the 2-4 sectors or institutions that best reveal that mechanism instead of inventorying every possible adopter\n"
            "- Economic picture should include several rough magnitudes, directional statistics, or comparative cues in plain English\n"
            "- In Economic picture, make the economy legible through labor demand, wages, prices, access, margins, bottlenecks, and foreign competition, not through generic claims about innovation or disruption\n"
            "- Households and politics should explain what ordinary people can actually feel in bills, services, status, schedules, access, or prestige, plus why the politics is hard now; include at least one concrete public worry or aspiration that sounds like something voters would say out loud, and let households cash out the macro story rather than replace it\n"
            "- Households and politics must also say why the country keeps adopting anyway and which constituency is defending the gains\n"
            "- Households and politics should include at least one genuinely positive reason some voters want more diffusion, not only why they are wary\n"
            "- Households and politics should make room for people who mostly notice convenience, relief, or only indirect change, not only people with a strong AI grievance or ideology\n"
            "- Households and politics should make one gain feel worth protecting in ordinary life, not just in productivity language\n"
            "- Still not true yet should explicitly state what remains unautomated, bottlenecked, scarce, or politically blocked so the chapter does not drift into vague AGI omnipotence\n"
            "- in advanced or radical openings, at least one section must plainly name the changed social settlement: who owns productive systems, how people get income or purchasing power, how firms staff work, or how schools and credentials now function\n"
            "- a short room briefing of about 55-90 words for the player as a decision brief, not a repeat of the summary; give exactly 4 short sentences that cover one gain voters already like and would defend, what split or unfairness now matters, one live lever government can move this cycle, and what tradeoff or uncertainty now matters most\n"
            "- keep the room briefing speakable and spare; it should sound like four spoken briefing lines, not one memo paragraph broken by periods\n"
            "- no room-briefing sentence should exceed about 22 words, and none should carry more than one comma\n"
            "- exactly 5 economic indicators as plain-language bullets; each should be a clean sentence fragment of roughly 10-20 words, not a mini paragraph\n"
            "- exactly 4 major tension points; each should be a clean sentence fragment of roughly 12-22 words\n"
            "- exactly 4 plausible policy axes the player might debate; each should be a short lane label or brief phrase, not a paragraph\n"
            "- the 4 policy axes must span genuinely different governing lanes rather than 4 versions of restriction; usually include one keep-it-open/pro-diffusion lane, one legitimacy-or-guardrails lane, one competition-or-access lane, and one distribution or bargaining-power lane when the stage supports them\n"
            "- if the stage facts support it, at least one policy axis should sound clearly affirmative about diffusion, access, or not overregulating useful tools\n"
            "- at most one policy axis may center junior hiring, entry ladders, or training unless the blueprint clearly made that the dominant national split\n"
            "- do not write final montage beats or image prompts here; the montage pass will handle the documentary script from this chapter package"
        )

    def _stage_blueprint_prompt(
        self,
        *,
        config: SimulationConfig,
        stage_index: int,
        stage_count: int,
        phase: dict[str, str],
        previous_stage: StagePackage | None,
        tracking: StageTracking | None,
        poll_summaries: list[PollSummary],
        player_in_power: bool,
        incumbent_name: str,
        queued_poll_questions: list[str],
    ) -> str:
        region_focus = self._setup_field_or_default(
            config.region_focus,
            "broad national field; do not overconcentrate on one region unless later evidence warrants it",
        )
        topic_lens = self._setup_field_or_default(
            config.topic_lens,
            "broad AGI transition; let the most important pressures emerge from the stage evidence",
        )
        setup_premise = self._setup_field_or_default(
            config.premise,
            "No extra premise configured; infer the most plausible national AGI transition path from the phase brief and prior chapter.",
        )
        setup_stakes = self._setup_field_or_default(
            config.stakes,
            "No extra electoral stake configured; infer the political argument from lived conditions, the vote, and the stage evidence.",
        )
        starting_world_mode = config.starting_world_mode
        previous_block = "This is the opening stage.\n"
        if previous_stage:
            previous_resolution = previous_stage.resolution
            transition_lines = self._transition_lines(previous_stage)
            binding_constraint = self._binding_constraint(previous_stage)
            previous_block = (
                "Prior chapter transition tape:\n"
                f"- Last chapter: {previous_stage.title} ({previous_stage.phase_label}, {previous_stage.year_label})\n"
                f"- Material changes already visible: {transition_lines}\n"
                f"- Prior dominant upside: {previous_stage.dominant_upside or 'not recorded'}\n"
                f"- Prior dominant mechanism: {previous_stage.dominant_mechanism or 'not recorded'}\n"
                f"- Prior main split: {previous_stage.main_split or 'not recorded'}\n"
                f"- Gains that stuck: {previous_stage.dominant_upside or transition_lines}\n"
                f"- Constraint that still binds: {binding_constraint}\n"
                f"- Open question now: {previous_stage.main_split or '; '.join(previous_stage.tension_points[:1]) or 'how the gains are spreading and who controls them'}\n"
            )
            if previous_resolution:
                player_won_last_election = previous_resolution.winner == config.player_name
                previous_block += (
                    f"- Election aftermath: {previous_resolution.winner} emerged with {previous_resolution.public_mandate}.\n"
                    f"- Player won the last election: {player_won_last_election}\n"
                    f"- What voters just endorsed or rejected: {'They rewarded the player with office or another term.' if player_won_last_election else 'They turned toward the opponent or kept the player out of office.'}\n"
                    f"- Agenda that took effect: {previous_resolution.enacted_agenda}\n"
                    f"- Election takeaway: {previous_resolution.election_takeaway or 'no special takeaway recorded'}\n"
                )
        tracking_block = "\n".join(f"- {metric.label}: {metric.display}" for metric in tracking.as_list()) if tracking else "- no prior tracking yet"
        poll_block = self._salient_poll_lines(poll_summaries, limit=6) or "- no prior polls yet"
        policy_notes_block = (
            "\n".join(f"- {note}" for note in previous_stage.policy_notes[:6])
            if previous_stage and previous_stage.policy_notes
            else "- none yet"
        )
        phase_guardrails = ""
        if stage_index == 0 and starting_world_mode == "default":
            phase_guardrails = (
                "- because this is the opening stage of the default run, stay recognizably near-term and practical\n"
                "- do not let the opening stage lean on one recurring office trope when the broader economy offers a larger split\n"
            )
        elif stage_index == 0 and starting_world_mode == "advanced":
            phase_guardrails = (
                "- because the player asked to start later in the transition, the opening chapter may begin with visibly deeper diffusion, stronger institutional adoption, and clearer macro change already underway\n"
                "- do not collapse back into a timid present-day frame; let the first chapter feel like a later breakpoint in the transition while staying coherent and concrete\n"
            )
        elif stage_index == 0 and starting_world_mode == "radical":
            phase_guardrails = (
                "- because the player asked to start in a radical future, the opening chapter may begin in a deeply transformed economy with AGI already changing work, ownership, public services, and bargaining power at national scale\n"
                "- be imaginative but disciplined: make the new settlement feel economically radical yet internally consistent, with real bottlenecks, real institutions, and real political conflict\n"
                "- do not write a slightly faster present-day world; the opening should make it obvious that ordinary life, expertise, firms, and politics are already living inside a later AGI settlement\n"
                "- assume frontier AI can already do most remote cognitive work and greatly widen access to expertise, while robotics and physical rollout remain uneven unless the chapter explains why they accelerated too\n"
                "- specify at least 3 deep shifts in the settlement itself: how firms are staffed, how households buy or plan, how schools or credentials work, how the state delivers services, how income or bargaining power is divided, or how foreign dependence changed\n"
                "- if the chapter is set years ahead, make the economy feel years ahead too; do not let it sound like today's world with one louder controversy\n"
            )
        if stage_index >= 2:
            phase_guardrails += (
                "- because this is stage 3 or later, include sectors that are clearly accelerating and sectors or institutions still bottlenecked\n"
                "- make later-stage world change feel material in both cognitive work and physical capacity, not just in one office workflow\n"
            )
        return (
            f"Simulation title: {config.title}\n"
            f"Country: {config.country}\n"
            f"Player: {config.player_name}\n"
            f"Player role: {config.player_role}\n"
            f"Opponent: {config.opponent_name}\n"
            f"Opponent role: {config.opponent_role}\n"
            f"Incumbent: {incumbent_name}\n"
            f"Player currently in power: {player_in_power}\n"
            f"Stage number: {stage_index + 1} of {stage_count}\n"
            f"Target transition phase: {phase['label']}\n"
            f"Phase brief: {phase['brief']}\n"
            f"Phase technology frontier: {phase['technology']}\n"
            f"Phase social argument: {phase['politics']}\n"
            f"Starting world mode: {starting_world_mode}\n"
            f"Region focus: {region_focus}\n"
            f"Topic lens: {topic_lens}\n"
            f"Setup premise: {setup_premise}\n"
            f"Setup stakes: {setup_stakes}\n"
            f"Population: {config.population_description}\n\n"
            f"{previous_block}\n"
            f"Tracking snapshot:\n{tracking_block}\n\n"
            f"Recent polling cues:\n{poll_block}\n\n"
            f"Prior working policy board:\n{policy_notes_block}\n\n"
            f"Queued custom poll interests: {queued_poll_questions or ['none']}\n\n"
            "Design the chapter spine first:\n"
            "- choose the biggest macro split of this stage, not the handiest repeated trope\n"
            "- keep the default broad and representative unless the setup explicitly narrows the lens\n"
            "- start from what AI can now reliably do, how widely that capability diffused, and what households or institutions now like enough to defend\n"
            "- name the broad capability class in plain language before you name a workflow; if the shift is computer-use agents, software help, guided decisions, or cheap expert support, say that clearly\n"
            "- tie the gains to a real economic mechanism in plain English: hiring, hours, wages, prices, access, margins, export pressure, capacity, or bargaining power\n"
            "- include one plain macro read that sounds like something a serious newspaper would say without jargon: unemployment still low, hiring softer, prices down in some services, margins widening, household budgets still mostly steady, or the old indicators no longer describe daily life cleanly\n"
            "- if there was an election, treat it as context after the causal unlock unless the election itself created the new stage reality\n"
            "- only then decide which one late local example, if any, best cashes out the macro story\n"
            "- the documentary should move in a few clean movements, not many tiny vignettes\n"
            "- each movement should advance the story, not merely name another affected group or sector\n"
            "- avoid consultant diction, stacked clauses, and clause-heavy comma lists\n"
            "- do not build the chapter around a laundry list of apps, tools, or office surfaces; describe one capability class, who adopts it, and why it matters\n"
            "- write lines a calm narrator could say once and a policymaker could retell later\n"
            "- do not let the chapter collapse into a single recurring trope like queue relief, junior office ladders, or generic back-office churn unless the evidence makes it central\n"
            "- vary the early lived examples across school, care, software work, small business, shopping, logistics, travel, housing, or public systems instead of repeatedly falling back to office administration\n"
            "- at least one movement should make clear what people are relieved by, what institutions are newly more capable of doing, and what still binds\n"
            "- force capability clarity in the blueprint: say what AI can broadly do now, what still requires people, and what physical deployment still cannot do cheaply or at scale\n"
            "- make the first defended gain something larger than shorter waits or cleaner paperwork unless the stage evidence makes that unavoidable\n"
            "- include one newly possible action for ordinary people or smaller organizations and one plain limit the systems still hit\n"
            "- do not keep defaulting to the same entry-ladder or office-admin trope; only lean on it when the stage evidence really makes it central\n"
            "- in advanced or radical openings, at least one documentary movement should name a post-current social arrangement or economic norm rather than another marginal improvement to a familiar workflow\n"
            "- in advanced or radical openings, deliberately look for more radical but still coherent answers on firm structure, household leverage, public services, ownership, and bargaining, not just labor-market stress language\n"
            "- in radical openings, the blueprint must commit to a new settlement rather than hinting at one: explicitly decide how households get income and access, how firms are staffed, and which public or market institutions now mediate everyday life\n"
            f"{phase_guardrails}"
            "Return:\n"
            "- causal_arc: one sentence of about 18-32 words on what unlocked, what spread, and what governing question follows\n"
            "- capability_frontier_now: one sentence on what AI can broadly and reliably do now\n"
            "- still_hard_now: one sentence on what still requires people, trust, or supervision\n"
            "- physical_world_status: one sentence on the current state of robotics, physical rollout, or real-world deployment bottlenecks\n"
            "- dominant_mechanism: one short sentence naming the main economic mechanism of this stage\n"
            "- dominant_upside: one short sentence naming the gain that households, firms, or institutions most want to keep\n"
            "- main_split: one short sentence naming the main political or distributional split\n"
            "- pro_adoption_constituency: one short sentence naming the group actively defending more diffusion and what gain they are protecting\n"
            "- opening_macro_sentences: exactly 4 sentences, each one clear sentence, in this order: capability frontier, first-wave diffusion, visible gain people or institutions would want to keep, main split or bottleneck\n"
            "- each opening_macro_sentence should use at most one comma and should not read like a stacked clause list\n"
            "- documentary_movements: 5-7 full-sentence movement lines that together form one mini-documentary structure from opening macro frame through final governing question\n"
            "- documentary_movements should sound like a clean script outline, not memo fragments or headline stubs\n"
            "- in radical openings, at least 2 documentary_movements must describe changed social arrangements or institutional norms, not just changed tools or louder politics\n"
            "- macro_cues: 4-6 concrete macro cues or rough directional statistics in plain English; keep each cue short enough to read like one clean clause\n"
            "- at least one macro_cue should read like a blunt macro line rather than a mood phrase: unemployment still low, white-collar hiring softer, service prices flatter, household budgets steadier, or buildout still bottlenecked\n"
            "- among the macro_cues, usually include at least one labor-market cue, one price or service-capacity cue, and one diffusion, concentration, or geopolitical cue when the stage supports them\n"
            "- at least one macro_cue should plainly say whether unemployment, hiring, wages, prices, or household finances still look normal, stretched, or newly improving\n"
            "- first_wave_adopters: 3-5 firms, sectors, or institutions adopting first in a believable order; choose only the few that reveal the macro shift\n"
            "- sectors_in_focus: 3-5 sectors or institutions that best reveal the stage mechanism without turning into an inventory\n"
            "- benefits_people_notice: 3-5 household or institutional gains people would actually want to keep; at least one should be more than paperwork relief or queue speed\n"
            "- frictions_or_splits: 3-5 tensions that matter now, including any important distributional split, capacity limit, or geopolitical pressure\n"
            "- local_example: one short optional late example that cashes out the macro shift without hijacking the chapter\n"
            "- still_not_true: 3-5 things still unautomated, bottlenecked, scarce, or politically blocked\n"
            "- governing_question: one sentence that names the real decision pressure the player steps into"
        )

    def _phase_brief(self, stage_index: int, stage_count: int, starting_world_mode: str = "default") -> dict[str, str]:
        ladder = [
            {
                "label": "Practical AI Breakout",
                "brief": "Reliable AI crosses from novelty into ordinary national life. The first national question is what has become newly doable with software help, how quickly those gains spread, and which human or physical limits still bind.",
                "technology": "Agents are now dependable across a broad band of screen-based work and guided decisions. For many households, students, workers, and small organizations, they feel like cheap expert help on a computer: reliable enough to research options, draft usable work, tutor through a problem, and keep ordinary digital workflows moving. They still stumble on messy exceptions, deep trust, persuasion, leadership, and almost all physical work without supervision.",
                "politics": "The first political argument is not whether the tools are real, but which useful gains people now want kept open, where caution belongs, and whether households and smaller organizations actually share the upside.",
            },
            {
                "label": "Cognitive Automation Surge",
                "brief": "AI stops feeling like a clever assistant and starts behaving like abundant digital capability inside serious institutions. The country now argues over who captures the gains, where labor markets bend, and what still remains scarce or hard to trust.",
                "technology": "Persistent agents can now carry longer digital projects when goals are measurable and workflows are instrumented. They behave less like a clever assistant and more like abundant digital labor inside software, analysis, design, support, and research settings, while humans still own accountability, persuasion, edge cases, and politically costly calls.",
                "politics": "The upside is clearer now, but so is the split. Better tools, broader access to expertise, faster output, and new entrepreneurial leverage are real, yet concentration, bargaining pressure, uneven adoption, and institutional legitimacy move to the front.",
            },
            {
                "label": "Embodied Rollout",
                "brief": "Physical deployment finally becomes visible. Robotics and AI-managed operations begin adding real capacity in a few environments while digital systems keep widening what people and institutions can do without adding headcount at the old pace.",
                "technology": "Firms deploy warehouse fleets, yard systems, industrial vision, AI dispatch, field-assist tools, and limited service robots where routing, repetition, and safety can be tightly managed. Physical capacity finally rises in visible places, but broad household robotics is still far away and messy real-world settings remain expensive.",
                "politics": "People compare visible convenience and stronger physical capacity against safety fights, labor identity, neighborhood permission, local buildout, and the question of who gets first access to real deployment.",
            },
            {
                "label": "AGI Power Contest",
                "brief": "Near-AGI systems begin to reshape national capacity fast enough that geopolitical divergence is impossible to ignore. The story is now about power, industrial buildout, research speed, public legitimacy, and who can still steer the system.",
                "technology": "Near-AGI systems can run large swathes of remote knowledge work and tightly instrumented operations with humans supervising exceptions. They also widen who can do serious design, research, strategy, and operational planning without elite staffing. Physical rollout advances fastest in fabs, utilities, ports, logistics hubs, and other capital-heavy settings that can afford sensors, safety layers, and downtime planning. Cheap universal robotics is still not here.",
                "politics": "Leaders are now judged on whether they are actually building national capacity. Power, chips, permitting, logistics, housing, public services, and allied or rival pressure sit beside affordability, bargaining power, and regional concentration.",
            },
            {
                "label": "Settlement Era",
                "brief": "AGI and robotics are embedded deeply enough that the central question is no longer whether life changed, but who owns the systems, what remains scarce, how bargaining survives, and whether the new abundance feels like agency or dependence.",
                "technology": "Abundant digital experts and heavily automated industrial systems are normal across much of the economy, and the settlement itself changed. Small teams run what once took departments, many households buy machine capability the way they once bought software or utilities, and schools, licensing, and public-service delivery are partially rebuilt around constant AI help. Maintenance, housing, local public systems, and trust-rich care still keep humans central and scarce.",
                "politics": "The conflict centers on ownership, bargaining power, income flow, civic trust, and whether ordinary people experience the new abundance as genuine agency with purchasing power rather than dependence on remote platform states or corporate gatekeepers.",
            },
        ]
        phase_sequence_by_stage_count: dict[int, list[int]] = {
            3: [0, 1, 2],
            4: [0, 1, 2, 3],
            5: [0, 1, 2, 3, 4],
            6: [0, 1, 2, 3, 4, 4],
            7: [0, 1, 1, 2, 3, 4, 4],
            8: [0, 1, 1, 2, 3, 3, 4, 4],
        }
        phase_offset = {"default": 0, "advanced": 2, "radical": 4}.get(starting_world_mode, 0)
        if stage_count <= 1:
            return ladder[min(phase_offset, len(ladder) - 1)]
        if stage_count == 2:
            return ladder[min((0 if stage_index == 0 else len(ladder) - 1) + phase_offset, len(ladder) - 1)]
        if stage_count in phase_sequence_by_stage_count:
            shifted = [min(index + phase_offset, len(ladder) - 1) for index in phase_sequence_by_stage_count[stage_count]]
            return ladder[shifted[stage_index]]
        position = round((stage_index / max(stage_count - 1, 1)) * (len(ladder) - 1)) + phase_offset
        return ladder[min(position, len(ladder) - 1)]

    def _transition_lines(self, previous_stage: StagePackage) -> str:
        source = previous_stage.state_of_world or previous_stage.detailed_summary
        sentences = [sentence.strip() for sentence in source.split(". ") if sentence.strip()]
        if not sentences:
            return "no material change summary captured"
        return " ".join(sentence.rstrip(".") + "." for sentence in sentences[:2])

    def _binding_constraint(self, previous_stage: StagePackage) -> str:
        candidates = [
            *(indicator.rstrip(".") for indicator in previous_stage.economic_indicators[:3]),
            *(tension.rstrip(".") for tension in previous_stage.tension_points[:3]),
        ]
        return candidates[0] if candidates else "bottlenecks, public trust, and institutional lag still bind harder than rhetoric"

    def _polish_image_prompt(self, visual_style: str, scene_prompt: str) -> str:
        return (
            f"{visual_style} Scene: {scene_prompt}. "
            "Build a specific, atmospheric composition with a clear camera distance, layered foreground and background detail, lived-in institutions, and people actually doing something. "
            "Prefer consequential civic, industrial, domestic, classroom, clinical, retail, or infrastructure scenes over generic office tableaux. "
            "Favor wide or medium establishing shots unless the prompt explicitly asks for an intimate close-up. "
            "Avoid defaulting to a tabletop, laptop-on-desk, or single-worker close crop when the narration is describing a national or sectoral shift. "
            "Emphasize naturalistic people, tactile materials, weathered public or working spaces, observed light, layered brushwork, and believable scale. "
            "Keep the image painterly, cinematic, and quietly impressionist rather than cartoonish, glossy, or futuristic-for-its-own-sake. "
            "Avoid generic queue boards, call-center rows, anonymous cubicle farms, floating dashboards, glossy 3D render aesthetics, anime, comic-book stylization, empty hologram spectacle, and sterile stock-photo staging unless the scene truly requires it."
        )

    def _dummy_stage(
        self,
        state: SimulationState,
        previous_stage: StagePackage | None,
        tracking: StageTracking | None,
        poll_summaries: list[PollSummary],
        queued_poll_questions: list[str],
    ) -> StagePackage:
        phase = self._phase_brief(
            state.active_stage_index,
            state.config.stage_count,
            state.config.starting_world_mode,
        )
        region_focus = self._setup_field_or_default(state.config.region_focus, "different regions of the country")
        topic_lens = self._setup_field_or_default(state.config.topic_lens, "the broad AI transition")
        premise = self._setup_field_or_default(
            state.config.premise,
            "AGI is arriving through uneven but increasingly real diffusion across ordinary institutions and daily life",
        )
        stakes = self._setup_field_or_default(
            state.config.stakes,
            "Politics turns on whether leaders can keep the gains broad, visible, and legitimate without choking off useful capability or letting dislocation harden",
        )
        title = [
            "Copilots Become Management Infrastructure",
            "Synthetic Labor Starts Setting Prices",
            "Autonomy Leaves The Lab",
            "AI Supply Chains Become Political",
            "The Post-Work Bargain Gets Negotiated",
        ][state.active_stage_index]
        setup_frame = (
            f"{premise} The campaign terrain is anchored in {region_focus}. "
            f"The dominant topic lens is {topic_lens}. "
        )
        role_frame = (
            f"As {state.config.player_role}, {state.config.player_name} is facing "
            f"{state.config.opponent_role} {state.config.opponent_name}. {stakes} "
        )
        return StagePackage(
            index=state.active_stage_index,
            phase_label=phase["label"],
            year_label=f"Year {2030 + state.active_stage_index}",
            title=title,
            montage_logline=(
                "Reliable AI agents spread through work, learning, commerce, and public systems, making new capability broadly available while exposing the fight over who keeps the gains."
            ),
            capability_frontier_now=(
                "AI can now handle a wide share of routine digital work, draft usable first passes, tutor or coach people through common tasks, and offer cheap expert-style help across many screen-based services."
            ),
            still_hard_now=(
                "People still matter where trust, liability, physical work, negotiation, procurement, and messy edge cases dominate, and robotics is nowhere near universal."
            ),
            physical_world_status=(
                "Robotics and physical deployment are improving, but the real economy is still constrained more by buildout, permits, power, logistics, supervision, and local management than by software ambition."
            ),
            dominant_mechanism=(
                "Software-heavy institutions can suddenly produce more service capacity and cheap expertise, but the gains flow through to households unevenly because physical bottlenecks, market power, and local capacity still bite."
            ),
            dominant_upside=(
                "People are getting cheaper expert help, stronger learning and planning tools, and more capability in daily life that used to take more time, money, or staff."
            ),
            main_split=(
                "The main split is between households and institutions that can turn cheap digital capability into real day-to-day gains and those still blocked by trust, bottlenecks, or concentrated control."
            ),
            state_of_world=(
                setup_frame
                + "AI systems are now reliably handling much more ordinary computer work, first-pass analysis, tutoring, planning, customer support, and software production across the economy. "
                "Households feel cheaper help, easier planning, stronger tutoring and translation, and more capable software in schools, clinics, small firms, and creative work even as employers start reorganizing who does what around those tools. "
                "The economy still looks stable from far away, but beneath that calm the benefits are landing unevenly across regions, institutions, and bargaining positions."
            ),
            detailed_summary=(
                f"Capability frontier: {premise} Agentic software now handles a larger share of routine and expert support work across services, education, logistics, administration, and public systems. It can triage cases, draft usable outputs, coach people through procedures, translate specialist knowledge into plain language, and keep many digital workflows moving with much less human effort. Large service operators, software-heavy firms, insurers, hospitals, payroll processors, and logistics networks move first because they already have structured workflows and enough supervision to absorb mistakes. Humans still review edge cases, liability-heavy decisions, negotiations, and anything that depends on trust or local judgment, so this is not full autonomy, but it is reliable enough to change the normal workday in many institutions.\n\n"
                "Economic picture: Headline unemployment may still look calm, yet service quality is improving, some software-linked prices are easing, and large institutions are widening output or margins before smaller ones can catch up. Families and small firms can suddenly buy bursts of expertise that used to require a specialist, which creates real consumer surplus and a new expectation that planning, tutoring, translation, customer support, and design help should be cheaper and easier to reach. Hiring pressure shifts unevenly: some routine coordination roles matter less, while people who can supervise, integrate, sell, manage clients, or redesign workflows gain leverage. Smaller firms benefit too, but unevenly, because software costs are falling faster than power, permits, training, local management capacity, or financing. Foreign rivals are also moving, so leaders face a real tradeoff between caution and competitive position.\n\n"
                f"Households and politics: {stakes} Ordinary people feel this in mixed, tangible ways, especially across {region_focus}. Parents get stronger tutoring help, patients navigate care with more confidence, local businesses look more capable, and many households quietly rely on cheap AI help for shopping, scheduling, translation, learning, and basic planning. Some households mostly register relief and convenience; others notice status shifts, platform dependence, or pressure on routines they thought were stable. Politics turns on whether leaders can keep useful tools open, prove that the gains are spreading beyond already-advantaged institutions, and answer the fear that a few gatekeepers could end up controlling the new capability layer.\n\n"
                "Still not true yet: Most institutions are not automated end to end, robotics is not yet everywhere, and the hardest physical bottlenecks still sit outside software: grid upgrades, permits, chips, management redesign, local trust, and public procurement. The country has not reached mass unemployment, machine-run government, or a post-scarcity economy. The world is changing fast, but it is still recognizably governed by uneven diffusion, physical constraints, and human bottlenecks."
            ),
            room_briefing=(
                role_frame
                + "Voters can already feel cheaper expert help, stronger everyday tools, and more capable services in daily life. "
                "What still rankles is that the gains are landing unevenly across households, firms, and places before the benefits feel durable or fair. "
                "You can move access, public adoption rules, competitive spread, and visible cushions for exposed groups this cycle. "
                "You cannot quickly rebuild local management capacity or physical bottlenecks, so both panic and complacency carry a cost."
            ),
            economic_indicators=[
                "Routine digital services are cheaper and more capable across visible parts of the economy.",
                "Households are saving time and buying expertise more cheaply in planning, tutoring, translation, and care coordination.",
                f"Regional attention is concentrated on {region_focus}.",
                "Adoption is uneven across firms, places, and public institutions.",
                f"Topic lens pressure is concentrated on {topic_lens}.",
            ],
            tension_points=[
                f"{stakes}",
                "Consumers want more access while critics warn that a few firms or platforms could capture too much control.",
                "Regional winners and losers are diverging faster than topline wage data alone suggests.",
                "Firms are reorganizing around AI-native workflows before schools or licensing systems catch up.",
                "Voters are split between fear of disruption and fear of missing the upside.",
            ],
            suggested_policy_axes=[
                "Keep useful AI open while widening access and competition.",
                "Set tighter public rules on deployment, liability, and concentration.",
                "Build public or shared AI capacity so smaller actors can compete.",
                "Push buildout, power, and industrial capacity before rivals lock in advantage.",
            ],
            narrative_beats=[
                NarrativeBeat(
                    line="AI crossed a reliability threshold in routine digital work, so service firms, institutions, and households started trusting it with real tasks instead of treating it like a demo.",
                    image_prompt=self._polish_image_prompt(
                        state.config.visual_style,
                        "A broad national dawn montage of offices, clinics, classrooms, stores, and homes absorbing dependable AI into ordinary routines.",
                    ),
                ),
                NarrativeBeat(
                    line="The first wave spread through structured workflows, so hospitals, freight networks, schools, and software-heavy firms pulled ahead before smaller institutions could adapt.",
                    image_prompt=self._polish_image_prompt(
                        state.config.visual_style,
                        "A national montage of hospitals, freight networks, schools, municipal offices, and small firms newly workable for AI.",
                    ),
                ),
                NarrativeBeat(
                    line="People noticed the upside first through cheaper help and software that could suddenly tutor, plan, draft, and translate without much ceremony.",
                    image_prompt=self._polish_image_prompt(
                        state.config.visual_style,
                        "A wide civic montage of families, patients, students, and small businesses using cheaper digital help in ordinary life.",
                    ),
                ),
                NarrativeBeat(
                    line="But the gains did not arrive cleanly, because cheaper digital capability still ran into power, permits, local management, and the market power of the fastest movers.",
                    image_prompt=self._polish_image_prompt(
                        state.config.visual_style,
                        "A national economic panorama of data centers, utility buildout, service firms, and local offices running into physical and institutional bottlenecks.",
                    ),
                ),
                NarrativeBeat(
                    line="Large firms with clean data and capital banked the first gains, while small clinics, local governments, schools, and neighborhood businesses adopted more unevenly.",
                    image_prompt=self._polish_image_prompt(
                        state.config.visual_style,
                        "A wide sectoral montage contrasting large firms banking AI gains while county offices, small clinics, and local suppliers hit bottlenecks.",
                    ),
                ),
                NarrativeBeat(
                    line="Some workers felt new pressure, but many households mostly talked about relief, convenience, and whether these tools would stay open or get locked behind a few gatekeepers.",
                    image_prompt=self._polish_image_prompt(
                        state.config.visual_style,
                        "A broad civic montage of households, storefronts, clinics, and schools touched by new AI convenience and growing arguments about control.",
                    ),
                ),
                NarrativeBeat(
                    line="The governing question is not whether the tools are useful now, but who gets access, who keeps leverage, and whether the gains spread before distrust hardens.",
                    image_prompt=self._polish_image_prompt(
                        state.config.visual_style,
                        "A reflective closing shot of still-human bottlenecks in public offices, infrastructure sites, and supervised control rooms as the country debates how to govern the gains.",
                    ),
                ),
            ],
            sample_citizens=[],
            tracking=tracking or self._neutral_tracking(),
            poll_summaries=poll_summaries,
            queued_poll_questions=queued_poll_questions,
            policy_notes=[],
            orchestrator_response_id=None,
        )

    def _poll_cue_line(self, summary: PollSummary) -> str:
        sorted_shares = sorted(summary.shares.items(), key=lambda item: item[1], reverse=True)
        if sorted_shares[:2]:
            lead_label, lead_share = sorted_shares[0]
            topline = f"{lead_label} ({lead_share * 100:.0f}%)"
        else:
            topline = "no clear topline"
        question = summary.question.lower()
        lane = "Poll read"
        if "right now ai mostly feels able to handle" in question or "trust ai to handle" in question:
            lane = "Capability signal"
        elif "used to feel above your expertise, budget, or time" in question or "felt above your time, budget, or skill" in question:
            lane = "New capability"
        elif "still clearly needs a person" in question or "still would not trust ai" in question:
            lane = "Hard limit"
        elif "hate to lose" in question or "easier, cheaper, or better" in question or "biggest national effect of ai" in question or "economy feels stronger and more capable" in question:
            lane = "Main upside"
        elif "most shaping your life" in question or "daily life around you feels" in question or "biggest worry" in question or "job loss or income disruption" in question:
            lane = "Main pressure"
        elif "country is handling this transition" in question or "current administration" in question:
            lane = "Political read"
        return f"- {lane}: {topline}"

    def _salient_poll_lines(self, poll_summaries: list[PollSummary], limit: int = 5) -> str:
        if not poll_summaries:
            return "- none yet"
        chosen: dict[str, str] = {}
        quote_line: str | None = None
        for summary in poll_summaries:
            question = summary.question.lower()
            if "election were held today" in question:
                continue
            line = self._poll_cue_line(summary)
            if ("right now ai mostly feels able to handle" in question or "trust ai to handle" in question) and "capability" not in chosen:
                chosen["capability"] = line
            elif (
                "biggest national effect of ai" in question
                or "easier, cheaper, or better" in question
                or "hate to lose" in question
                or "useful expertise now feels" in question
                or "what can ai now help you do" in question
            ) and "upside" not in chosen:
                chosen["upside"] = line
            elif ("still clearly needs a person" in question or "still would not trust ai" in question) and "limit" not in chosen:
                chosen["limit"] = line
            elif (
                "biggest worry about ai" in question
                or "job loss or income disruption" in question
                or "most shaping your life right now" in question
                or "daily life around you feels" in question
                or "household finances feel" in question
            ) and "pressure" not in chosen:
                chosen["pressure"] = line
            elif quote_line is None and summary.sample_reasons:
                sample = str(summary.sample_reasons[0]).strip()
                if sample:
                    quote_line = f'- One voter line: "{sample}"'
        ordered = [chosen.get("capability"), chosen.get("upside"), chosen.get("limit"), chosen.get("pressure"), quote_line]
        return "\n".join(line for line in ordered if line) or "- none yet"

    def _opponent_themes(
        self,
        state: SimulationState,
        current_stage: StagePackage,
        player_platform: str | None = None,
    ) -> list[str]:
        platform_text = " ".join([player_platform or "", *current_stage.policy_notes[:6]]).lower()
        poll_questions = " ".join(summary.question.lower() for summary in current_stage.poll_summaries)
        themes: list[str] = []
        upside = self._clip(current_stage.dominant_upside or "the gains people already like", 110)
        constituency = self._clip(current_stage.pro_adoption_constituency or "the people already benefiting", 110)
        split = self._clip(current_stage.main_split or "who captures the gains and who absorbs the risk", 110)
        player_lane = self._player_debate_lane(player_platform, current_stage.policy_notes)

        restrictive_tokens = (
            "ban",
            "pause",
            "slow",
            "slowdown",
            "freeze",
            "halt",
            "moratorium",
            "cap",
            "license",
            "licens",
            "restrict",
            "brake",
            "tax",
            "taxes",
            "corporate tax",
            "windfall",
            "levy",
            "regulat",
            "oversight",
            "guardrail",
            "guard rail",
            "permit",
            "public option",
            "state run",
            "nationalize",
        )
        acceleration_tokens = (
            "accelerate",
            "speed",
            "fast",
            "open",
            "deploy",
            "build",
            "scale",
            "expand",
            "adopt",
            "open source",
            "diffus",
            "buildout",
        )

        if player_lane == "restrictive guardrail lane" or any(token in platform_text for token in restrictive_tokens):
            themes.append(
                f"a broad-access abundance case: keep useful tools open, protect {constituency.lower()}, argue for faster diffusion with lighter rules aimed at real abuse, and explicitly say broad brakes, taxes, or blanket permissions would take useful capability away from ordinary people"
            )
            themes.append(
                f"a visible-upside case: voters already see {upside.lower()}, so the rival case should sound like protecting real gains rather than making the country deliberately slower"
            )
            if any(token in poll_questions for token in ("cost of living", "gains from ai are going", "household finances", "employers and local services", "everyday services now feel", "vote")):
                themes.append(
                    "a household-value case: judge the transition by cheaper help, broader access to expertise, better service quality, and whether ordinary life feels more capable"
                )
            themes.append(
                "a keep-what-works case: defend the conveniences, lower costs, stronger everyday capability, and broader access people already do not want to lose"
            )
            themes.append(
                "a build-and-compete case: widen access, force competition, keep taxes and permissions from becoming a broad brake, and keep national buildout moving instead of making caution the country's main offer"
            )
            return themes
        if player_lane == "speed-and-diffusion lane" or any(token in platform_text for token in acceleration_tokens):
            themes.append(
                f"a legitimacy-and-bargaining case: faster deployment only holds if {split.lower()} is answered with visible household payoff, leverage, appeal rights, and a flagship fairness move the player is not offering"
            )
            if any(token in poll_questions for token in ("cost of living", "gains from ai are going", "household finances", "employers and local services", "everyday services now feel", "vote")):
                themes.append(
                    "a household-payoff case: tie the next wave of deployment to visible gains in ordinary life rather than hoping growth speaks for itself"
                )
            else:
                themes.append(
                    "a fairness-and-legitimacy case: the gains are real, but they do not hold politically unless households see leverage, appeals, and visible protection against concentration"
                )
            themes.append(
                "a bargaining-power case: move fast where the tools help, but force firms and institutions to share the gains more openly with workers, users, and local communities"
            )
            return themes
        if player_lane == "distribution-and-bargaining lane":
            themes.append(
                f"a pro-capability build-and-compete case: keep {upside.lower()} spreading, widen access beyond the early winners, and treat speed and scale as part of fairness rather than the enemy of it"
            )
            themes.append(
                "a competition-and-access case: stop the gains from concentrating by widening diffusion, breaking bottlenecks, and forcing open access instead of leaning mainly on taxes or bargaining alone"
            )
            themes.append(
                "a keep-what-works case: preserve the conveniences, lower costs, and service gains people already notice while making sure the next round reaches more households and smaller firms"
            )
            return themes

        themes.append(
            "a prove-it-in-daily-life case: tie the next wave of adoption to care, school quality, service quality, and who actually gains power in ordinary life, then choose one governing move that makes your lane visibly distinct"
        )
        if any(token in poll_questions for token in ("would hate to lose", "easier, cheaper, or better", "touching your life most", "trust ai to handle", "still would not trust ai")):
            themes.append(
                "a keep-the-gains case: defend the conveniences, lower costs, faster service, and stronger everyday capability people already do not want to lose"
            )
        else:
            themes.append(
                "a fairness-and-legitimacy case: keep the gains, but show who has leverage, who gets an appeal, who owns the systems, and who is being asked to absorb the shock"
            )
        themes.append(
            "a visibly different governing case: do not split the difference; make one governing move that a listener could clearly distinguish from the player's line"
        )

        return themes

    def _player_debate_lane(self, player_platform: str | None, policy_notes: list[str]) -> str:
        platform_text = " ".join([player_platform or "", *policy_notes[:6]]).lower()
        restrictive_tokens = (
            "ban",
            "pause",
            "slow",
            "freeze",
            "halt",
            "moratorium",
            "cap",
            "license",
            "licens",
            "restrict",
            "brake",
            "tax",
            "levy",
            "regulat",
            "oversight",
            "guardrail",
            "guard rail",
            "permit",
            "public option",
            "state run",
            "nationalize",
        )
        acceleration_tokens = (
            "accelerate",
            "speed",
            "fast",
            "open",
            "deploy",
            "build",
            "scale",
            "expand",
            "adopt",
            "open source",
            "diffus",
            "buildout",
        )
        distribution_tokens = (
            "union",
            "bargain",
            "worker",
            "wage",
            "redistribut",
            "rebate",
            "dividend",
            "fairness",
            "household payoff",
        )
        if any(token in platform_text for token in restrictive_tokens):
            return "restrictive guardrail lane"
        if any(token in platform_text for token in acceleration_tokens):
            return "speed-and-diffusion lane"
        if any(token in platform_text for token in distribution_tokens):
            return "distribution-and-bargaining lane"
        return "mixed or not yet fully declared"

    def _opponent_debate_lane(self, player_lane: str) -> str:
        if player_lane == "restrictive guardrail lane":
            return "pro-capability, pro-diffusion, narrow-guardrail lane"
        if player_lane == "speed-and-diffusion lane":
            return "household-payoff, legitimacy, bargaining lane"
        if player_lane == "distribution-and-bargaining lane":
            return "pro-capability, pro-diffusion, build-and-compete lane"
        return "clearly distinct governing alternative"

    def _opponent_flagship_move(self, player_lane: str, stage: StagePackage) -> str:
        if player_lane == "restrictive guardrail lane":
            return "keep useful tools open, widen access through interoperability and competition, target concrete abuse instead of the frontier itself, and keep the gains spreading"
        if player_lane == "speed-and-diffusion lane":
            return "tie faster adoption to visible household payoff, appeal rights, and bargaining leverage instead of trusting growth to trickle down"
        if player_lane == "distribution-and-bargaining lane":
            return "speed national buildout, widen access, and keep deployment moving instead of treating taxes or bargaining alone as the growth strategy"
        return stage.dominant_mechanism or "make one visibly different governing move instead of shadowing the player's line"

    def _neutral_tracking(self) -> StageTracking:
        from ..models import TrackingMetric

        def metric(key: str, label: str, value: float) -> TrackingMetric:
            return TrackingMetric(key=key, label=label, value=value, display=f"{value:.0f}%")

        return StageTracking(
            approval=metric("approval", "Approval", 50),
            vote_share_player=metric("vote_player", "Vote Share", 50),
            vote_share_opponent=metric("vote_opponent", "Opponent Vote", 50),
            better_off=metric("better_off", "Better Off", 50),
            ai_comfort=metric("ai_comfort", "AI Comfort", 50),
            unemployment_anxiety=metric("job_security", "Job Security", 50),
            trust_in_government=metric("trust", "Gov Trust", 50),
            social_stability=metric("stability", "Social Stability", 50),
        )
