from __future__ import annotations

import asyncio
import re
from pathlib import Path

from pydantic import BaseModel, Field

from ..config import AppSettings
from ..models import (
    ConversationTurn,
    DebateReply,
    DocumentaryFeaturette,
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


class OrchestratorFeaturetteOutput(BaseModel):
    subject: str = Field(min_length=2)
    question: str = Field(min_length=12)
    title: str = Field(min_length=4)
    logline: str = Field(min_length=16)
    narrative_beats: list[OrchestratorBeat] = Field(default_factory=list, min_length=3, max_length=4)


class OrchestratorFeaturetteSetOutput(BaseModel):
    featurettes: list[OrchestratorFeaturetteOutput] = Field(default_factory=list, min_length=3, max_length=3)


class OrchestratorStageBlueprint(BaseModel):
    causal_arc: str
    capability_frontier_now: str = ""
    still_hard_now: str = ""
    physical_world_status: str = ""
    dominant_mechanism: str = ""
    dominant_upside: str = ""
    main_split: str = ""
    pro_adoption_constituency: str = ""
    household_income_system: str = ""
    capability_access_norm: str = ""
    firm_structure_norm: str = ""
    ownership_regime: str = ""
    public_service_norm: str = ""
    opening_macro_sentences: list[str] = Field(default_factory=list)
    documentary_movements: list[str] = Field(default_factory=list)
    macro_cues: list[str] = Field(default_factory=list, min_length=3, max_length=6)
    first_wave_adopters: list[str] = Field(default_factory=list)
    sectors_in_focus: list[str] = Field(default_factory=list)
    benefits_people_notice: list[str] = Field(default_factory=list)
    frictions_or_splits: list[str] = Field(default_factory=list)
    local_example: str = ""
    still_not_true: list[str] = Field(default_factory=list)
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

    def _future_year_signal(self, text: str | None) -> int | None:
        lowered = " ".join(str(text or "").split()).lower()
        if not lowered:
            return None
        word_years = {
            "eight": 8,
            "ten": 10,
            "twelve": 12,
            "fifteen": 15,
            "sixteen": 16,
            "seventeen": 17,
            "eighteen": 18,
            "nineteen": 19,
            "twenty": 20,
        }
        numeric_match = re.search(
            r"\b(\d{1,2})(?:\s*(?:-|to)\s*(\d{1,2}))?\s+years?\s+(?:from now|ahead|in the future|after)\b",
            lowered,
        )
        if numeric_match:
            upper = numeric_match.group(2) or numeric_match.group(1)
            return int(upper)
        word_match = re.search(
            r"\b(eight|ten|twelve|fifteen|sixteen|seventeen|eighteen|nineteen|twenty)"
            r"(?:\s*(?:-|to)\s*(eight|ten|twelve|fifteen|sixteen|seventeen|eighteen|nineteen|twenty))?"
            r"\s+years?\s+(?:from now|ahead|in the future|after)\b",
            lowered,
        )
        if word_match:
            upper = word_match.group(2) or word_match.group(1)
            return word_years.get(upper)
        return None

    def _text_wants_later_world(self, text: str | None) -> bool:
        lowered = " ".join(str(text or "").split()).lower()
        if not lowered:
            return False
        if (years := self._future_year_signal(lowered)) is not None:
            return years >= 8
        return bool(
            any(
                cue in lowered
                for cue in (
                    "skip ahead",
                    "start later",
                    "far future",
                    "radical future",
                    "stranger future",
                    "stranger agi society",
                    "much stranger",
                    "deeply transformed",
                    "different economy",
                    "different world",
                    "radically different",
                    "new settlement",
                    "machine dividends",
                    "compute rationing",
                    "civic ai utility",
                    "civic ai utilities",
                    "allied compute blocs",
                    "rival compute blocs",
                    "normal job week",
                    "post-job-week",
                )
            )
        )

    def _content_reasoning_effort(self, config: SimulationConfig) -> str:
        requested = str(config.orchestrator_reasoning_effort or "low").lower()
        starting_world_mode = self._effective_world_mode(config)
        if requested == "high":
            return "high"
        if requested == "medium":
            return requested
        if starting_world_mode == "radical":
            return "high"
        if starting_world_mode == "advanced":
            return "medium"
        if self._text_wants_later_world(config.premise):
            return "high"
        if str(config.premise or "").strip():
            return "medium"
        return "low"

    def _setup_reasoning_effort(self, config: SimulationConfig, user_text: str) -> str:
        if self._text_wants_later_world(user_text):
            return "high"
        return self._content_reasoning_effort(config)

    def _radical_settlement_menu(self) -> str:
        return (
            "In later-settlement openings, make the social operating system legible fast: how households secure ordinary life, "
            "what many adults now do instead of the old job week, which institution or platform mediates everyday access, "
            "who captures the key rents or chokepoints, and which scarcity, rivalry, or political fight still rules the country. "
            "Examples like monthly machine checks, public AI help lines, rationed compute time, automated city services, bloc rivalry, or platform-run daily life are prompts, not content to copy. "
            "The viewer should quickly understand how people live in this world, not just how offices changed. "
        )

    def resolve_starting_world_mode(self, *texts: str | None) -> str:
        rank = {"default": 0, "advanced": 1, "radical": 2}
        inferred = "default"
        for text in texts:
            candidate = self._extract_starting_world_mode(str(text or ""))
            if candidate and rank[candidate] > rank[inferred]:
                inferred = candidate
        return inferred

    def _effective_world_mode(self, config: SimulationConfig) -> str:
        return self.resolve_starting_world_mode(
            config.premise,
            config.topic_lens,
            config.stakes,
        )

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
            self._effective_world_mode(state.config),
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
        blueprint, _ = await self.gateway.parse(
            model=self.settings.orchestrator_model,
            instructions=self._stage_blueprint_instructions(state.config),
            input_text=blueprint_prompt,
            text_format=OrchestratorStageBlueprint,
            reasoning_effort=self._content_reasoning_effort(state.config),
            previous_response_id=None,
            prompt_cache_key=f"{state.simulation_id}:orchestrator-blueprint",
            max_output_tokens=1300,
            verbosity="medium",
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
            reasoning_effort=self._content_reasoning_effort(state.config),
            previous_response_id=None,
            prompt_cache_key=f"{state.simulation_id}:orchestrator-stage",
            max_output_tokens=2600,
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
            reasoning_effort=self._content_reasoning_effort(state.config),
            previous_response_id=response_id,
            prompt_cache_key=f"{state.simulation_id}:orchestrator-montage",
            max_output_tokens=950,
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
            household_income_system=blueprint.household_income_system,
            capability_access_norm=blueprint.capability_access_norm,
            firm_structure_norm=blueprint.firm_structure_norm,
            ownership_regime=blueprint.ownership_regime,
            public_service_norm=blueprint.public_service_norm,
            state_of_world=self._normalize_summary_prose(parsed.state_of_world, max_paragraphs=1),
            detailed_summary=self._normalize_summary_prose(parsed.detailed_summary, max_paragraphs=4),
            room_briefing=self._normalize_room_briefing(parsed.room_briefing),
            authored_room_briefing=" ".join(str(parsed.room_briefing or "").split()).strip(),
            economic_indicators=self._normalize_short_lines(parsed.economic_indicators, limit=5, max_chars=132, sentence_fragment=False),
            tension_points=self._normalize_short_lines(parsed.tension_points, limit=4, max_chars=140, sentence_fragment=False),
            suggested_policy_axes=self._normalize_short_lines(parsed.suggested_policy_axes, limit=4, max_chars=132, sentence_fragment=True),
            authored_policy_axes=self._normalize_authored_lines(parsed.suggested_policy_axes, limit=6),
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
        stage_package.room_briefing = self._resolve_room_briefing(
            authored_room_briefing=parsed.room_briefing,
            dominant_mechanism=stage_package.dominant_mechanism,
            dominant_upside=stage_package.dominant_upside,
            economic_indicators=stage_package.economic_indicators,
            main_split=stage_package.main_split,
            suggested_policy_axes=stage_package.suggested_policy_axes,
            still_hard_now=stage_package.still_hard_now,
            physical_world_status=stage_package.physical_world_status,
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
        setup_direction = self._setup_direction_block(config)
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
            f"Setup direction from the player:\n{setup_direction}\n"
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
            "If the user asks to skip ahead, start later, begin ten or fifteen years from now, or open inside a much stranger AI settlement, preserve that language in premise and infer the later starting point internally instead of replying in field jargon. "
            "Temporal or world-setting cues usually belong in premise, not topic_lens. Do not turn 'about fifteen years from now' or 'in a transformed AGI future' into a topical lens unless the user also names a real policy domain. "
            "Do not give generic encouragement, field-by-field recaps, or repeat unchanged settings."
        )
        parsed, _ = await self.gateway.parse(
            model=self.settings.orchestrator_model,
            instructions=self._setup_instructions(),
            input_text=prompt,
            text_format=SetupChamberGuidance,
            reasoning_effort=self._setup_reasoning_effort(config, user_text),
            prompt_cache_key="setup-chamber",
            max_output_tokens=900,
            verbosity="low",
        )
        heuristic_updates = self._dummy_setup_patch_from_text(user_text)
        merged_updates = {
            **heuristic_updates.model_dump(exclude_none=True),
            **parsed.config_updates.model_dump(exclude_none=True),
        }
        resolved_world_mode = self.resolve_starting_world_mode(
            user_text,
            str(merged_updates.get("premise") or ""),
            str(merged_updates.get("topic_lens") or ""),
            str(merged_updates.get("stakes") or ""),
        )
        removed_auto_fields: list[str] = []
        explicit_topic_lens = self._extract_labeled_value(user_text, "topic_lens")
        broad_future_request = self._text_wants_later_world(user_text)
        focus_phrase = self._extract_focus_phrase(user_text)
        if "topic_lens" in merged_updates and not explicit_topic_lens:
            candidate = str(merged_updates["topic_lens"] or "")
            if candidate and (
                (
                    broad_future_request
                    and not (focus_phrase and self._focus_phrase_is_narrow_topic(focus_phrase))
                )
                or not self._focus_phrase_is_narrow_topic(candidate)
            ):
                merged_updates.pop("topic_lens", None)
                removed_auto_fields.append("topic_lens")
        explicit_population = self._extract_labeled_value(user_text, "population")
        if (
            broad_future_request
            and "population_description" in merged_updates
            and not explicit_population
            and not explicit_topic_lens
            and not (focus_phrase and self._focus_phrase_is_narrow_topic(focus_phrase))
        ):
            merged_updates.pop("population_description", None)
            removed_auto_fields.append("population_description")
        if "population_description" in merged_updates and not explicit_population:
            candidate = " ".join(str(merged_updates["population_description"] or "").split()).lower()
            if self._text_wants_later_world(candidate):
                merged_updates.pop("population_description", None)
                removed_auto_fields.append("population_description")
        if merged_updates:
            parsed.config_updates = SetupSessionPatchRequest(**merged_updates)
            parsed.applied_updates = [f"{field} -> {value}" for field, value in merged_updates.items()]
            if removed_auto_fields or any(field not in " ".join(parsed.chamber_reply.split()) for field in merged_updates.keys()):
                tail = (
                    "The opening frame now starts from a more changed settlement."
                    if resolved_world_mode in {"advanced", "radical"}
                    else "The draft now reflects that nudge."
                )
                parsed.chamber_reply = f"Applied {'; '.join(parsed.applied_updates[:4])}. {tail}"
        return parsed

    async def materialize_stage_media(self, *, stage: StagePackage, asset_dir: Path) -> None:
        await self._materialize_narrative_media(stage.narrative_beats, asset_dir=asset_dir, prefix="beat")

    async def compose_stage_featurettes(
        self,
        *,
        state: SimulationState,
        stage: StagePackage,
    ) -> list[DocumentaryFeaturette]:
        if self.settings.dummy_openai:
            return self._dummy_featurettes(state, stage)

        prompt = self._featurette_prompt(config=state.config, stage=stage)
        parsed, _ = await self.gateway.parse(
            model=self.settings.orchestrator_model,
            instructions=self._featurette_instructions(),
            input_text=prompt,
            text_format=OrchestratorFeaturetteSetOutput,
            reasoning_effort=self._content_reasoning_effort(state.config),
            prompt_cache_key=f"{state.simulation_id}:featurettes:{stage.index}",
            max_output_tokens=2200,
            verbosity="medium",
            max_attempts=2,
        )
        featurettes: list[DocumentaryFeaturette] = []
        for entry in parsed.featurettes[:3]:
            question = self._normalize_question(entry.question, max_words=18, max_chars=128)
            if not question:
                question = self._featurette_question_fallback(
                    subject=entry.subject,
                    title=entry.title,
                    stage=stage,
                )
            featurettes.append(
                DocumentaryFeaturette(
                    subject=self._trim_without_ellipsis(entry.subject, 48),
                    question=question,
                    title=self._trim_without_ellipsis(entry.title, 68),
                    logline=self._normalize_sentence(entry.logline, max_words=26, max_chars=180),
                    status="generating",
                    narrative_beats=[
                        NarrativeBeat(
                            line=self._normalize_narration_line(beat.line),
                            image_prompt=self._polish_image_prompt(state.config.visual_style, beat.image_prompt),
                        )
                        for beat in entry.narrative_beats[:4]
                        if self._normalize_narration_line(beat.line)
                    ],
                )
            )
        return [featurette for featurette in featurettes if featurette.narrative_beats]

    async def materialize_featurette_media(
        self,
        *,
        featurette: DocumentaryFeaturette,
        asset_dir: Path,
    ) -> None:
        await self._materialize_narrative_media(featurette.narrative_beats, asset_dir=asset_dir, prefix="featurette")

    async def _materialize_narrative_media(
        self,
        beats: list[NarrativeBeat],
        *,
        asset_dir: Path,
        prefix: str,
    ) -> None:
        image_semaphore = asyncio.Semaphore(3)
        audio_semaphore = asyncio.Semaphore(6)

        async def _render_beat(index: int, beat: NarrativeBeat) -> None:
            image_suffix = "svg" if self.settings.dummy_openai else "png"
            image_path = asset_dir / f"{prefix}-{index:02d}.{image_suffix}"
            audio_path = asset_dir / f"{prefix}-{index:02d}.mp3"

            async def _render_image() -> None:
                async with image_semaphore:
                    await self.gateway.render_image(prompt=beat.image_prompt, output_path=image_path)

            async def _render_audio() -> None:
                async with audio_semaphore:
                    await self.gateway.synthesize(text=beat.line, output_path=audio_path)

            await asyncio.gather(_render_image(), _render_audio())
            beat.image_path = str(image_path)
            beat.audio_path = str(audio_path) if audio_path.exists() else None

        asset_dir.mkdir(parents=True, exist_ok=True)
        await asyncio.gather(*[_render_beat(idx, beat) for idx, beat in enumerate(beats)])

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
        settlement_block = "\n".join(
            line
            for line in (
                f"- Household security: {stage.household_income_system}" if stage.household_income_system else "",
                f"- Everyday access: {stage.capability_access_norm}" if stage.capability_access_norm else "",
                f"- Firm structure: {stage.firm_structure_norm}" if stage.firm_structure_norm else "",
                f"- Ownership: {stage.ownership_regime}" if stage.ownership_regime else "",
                f"- Public services: {stage.public_service_norm}" if stage.public_service_norm else "",
            )
            if line
        ) or "- no altered settlement was recorded yet"
        settlement_first = self._stage_reads_like_later_settlement(stage)
        macro_refresh = (
            "Keep the macro frame intact while sharpening the settlement read first: household security, who gets paid, who gets access, who owns the chokepoints, how firms are staffed, how public services now work, and what replaced the old job week as the main baseline of security. "
            "Use citizens and polls to show how that altered settlement actually feels, not to drag the stage back into generic service convenience language."
            if settlement_first
            else "Keep the macro frame intact while sharpening the national economic read first: service quality, consumer surplus, household costs, national capacity, firm structure, regional divergence, and the split between leading and lagging sectors."
        )
        gain_discipline = (
            "Preserve at least one gain people would defend and at least one live dependence, chokepoint, or power fight they can already feel. "
            "Do not let one grievance-heavy quote collapse a materially changed society back into bland present-day terms."
            if settlement_first
            else "Preserve at least one household gain worth defending and at least one constituency that is actively pressing for more adoption because life or capacity is better."
        )
        prompt = (
            f"Stage title: {stage.title}\n"
            f"Phase: {stage.phase_label}\n"
            f"Current stage summary: {stage.detailed_summary}\n"
            f"Current room briefing: {stage.authored_room_briefing or stage.room_briefing}\n"
            f"Current economic indicators: {' | '.join(stage.economic_indicators)}\n"
            f"Current tension points: {' | '.join(stage.tension_points)}\n"
            f"Current suggested policy axes: {' | '.join(stage.authored_policy_axes or stage.suggested_policy_axes)}\n\n"
            f"Settlement in force:\n{settlement_block}\n\n"
            f"Fresh tracking snapshot:\n{tracking_block}\n\n"
            f"Fresh polling cues:\n{poll_block}\n\n"
            f"Fresh citizen lived evidence:\n{citizen_block}\n\n"
            "Revise the room_briefing, economic_indicators, tension_points, and suggested_policy_axes so they reflect the fresh evidence from this same stage. "
            f"Do not rewrite the whole chapter. {macro_refresh} "
            "Use citizens and poll quotes as evidence for that broader pattern, not as a substitute for it. "
            "Translate anecdotes back into macro signals. If three people complain about different things, name the broader labor, price, service, status, or access pattern they point to. "
            f"Do not let the refreshed brief become more negative than the evidence requires. {gain_discipline} "
            "Do not let one sharp quote or one grievance-heavy poll answer hijack the chapter's center of gravity. The brief should stay top-down and balanced. "
            "The room briefing should still read like a concrete decision brief, not a string of anecdotes, and it should open on one gain voters would defend before naming the split. "
            "Keep the room briefing in complete, plain spoken lines that still make sense read aloud. "
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
                "Keep one concrete gain worth preserving in frame, not just the frictions. "
                "If the stage already lives inside a later settlement, preserve that reality instead of backing away into present-day normalcy."
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
            stage.suggested_policy_axes = self._normalize_short_lines(parsed.suggested_policy_axes, limit=4, max_chars=132, sentence_fragment=True)
            stage.authored_policy_axes = self._normalize_authored_lines(parsed.suggested_policy_axes, limit=6)
        if parsed.room_briefing:
            stage.authored_room_briefing = " ".join(str(parsed.room_briefing).split()).strip()
        stage.room_briefing = self._resolve_room_briefing(
            authored_room_briefing=parsed.room_briefing or stage.authored_room_briefing or stage.room_briefing,
            dominant_mechanism=stage.dominant_mechanism,
            dominant_upside=stage.dominant_upside,
            economic_indicators=stage.economic_indicators,
            main_split=stage.main_split,
            suggested_policy_axes=stage.suggested_policy_axes,
            still_hard_now=stage.still_hard_now,
            physical_world_status=stage.physical_world_status,
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
        flagship_move = self._opponent_flagship_move(player_lane, current_stage, player_platform)
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
            f"Read of player emphasis: {player_lane}\n"
            f"Needed contrast: {opponent_lane}\n"
            f"One flagship contrasting move: {flagship_move}\n"
            f"One gain the player would slow: {current_stage.dominant_upside or 'a real gain voters already notice'}\n"
            f"One constituency wanting more AI: {current_stage.pro_adoption_constituency or 'people already benefiting from faster adoption'}\n"
            "The opponent should make the sharpest credible contrast for this stage and electorate, not a softened mirror of the player.\n"
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
                "Use the structured contrast brief below as guidance, but synthesize the actual case from this stage instead of falling back to a stock ideology. "
                "Do not echo the player's remedies unless you are explicitly narrowing, replacing, or rejecting them. "
                "Make the sharpest credible contrast for this stage and electorate. That contrast may turn on pace, concentration, public provision, competition, household payoff, bargaining power, resilience, or legitimacy. "
                "If the player proposes a broad brake, one plausible contrast is narrower rules, more competition, or faster diffusion, but only if the stage evidence supports that case. "
                "If the player proposes speed-first diffusion, one plausible contrast is visible household payoff, bargaining leverage, or public recourse, but only if that is the live pressure. "
                "If the player already sounds mixed, find the missing governing choice instead of forcing a prefab opposite lane. "
                "Name one gain the player's approach could endanger, one constituency your rival case is protecting, and one governing move that sounds visibly different from the player's board. "
                "Do not lapse into vague balance talk or mirror the player in softer words. "
                "The opponent's planks must be visibly different from the player's current board, and at least one plank should replace, narrow, or reverse the player's lead remedy. "
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
            "Treat persona_count, stage_count, and visual_style as editable setup fields too. "
            "Treat natural-language setup requests as real edits, not vague inspiration. "
            "If the user says something like 'make this a Finland education-policy run focused on students and teachers', "
            "translate that into concrete config_updates for country, topic_lens, population_description, and any other strongly implied fields. "
            "If the user asks for a different art style, look, aesthetic, painterly direction, or documentary treatment, write that into visual_style directly. "
            "If the user asks to skip ahead, begin later in the transition, start ten years from now, or open inside a stranger future economy, preserve that natural-language request in premise and also infer the starting point internally. "
            "Do not mistake a future-setting sentence for a topic lens. A sentence about how strange the world should be belongs in premise unless the user also named a concrete domain like health care, housing, or schools. "
            "If the user asks for a certain number of agents, personas, citizens, or people in the sample, update persona_count directly. "
            "When jurisdiction or country changes, also rewrite player and opponent roles so the offices make sense in that place unless the user explicitly overrides them. "
            "If no narrow region_focus, topic_lens, premise, or stakes were requested, leave those fields broad or empty rather than inventing a special frame. "
            "Apply straightforward requested edits instead of restating them. "
            "If you made edits, briefly confirm the one or two biggest changes in natural speech; terse field -> value fragments are fine only when they stay readable out loud. "
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
        future_setup_brief = self._extract_future_setup_brief(text)
        focus_phrase_is_topic = bool(focus_phrase and self._focus_phrase_is_narrow_topic(focus_phrase))
        if focus_phrase_is_topic and topic_lens is None:
            topic_lens = focus_phrase
        if focus_phrase_is_topic and region_focus is None and any(
            keyword in focus_phrase.lower() for keyword in ("student", "teacher", "municipal", "school", "classroom")
        ):
            region_focus = "municipal school systems"
        if premise is None and future_setup_brief:
            premise = future_setup_brief
        if focus_phrase_is_topic and population is None and country:
            population = (
                f"A representative sample of people in {country} whose lives are directly shaped by {focus_phrase}, "
                "with realistic variation across age, income, ideology, institutional role, geography, and AI exposure."
            )
        if population is None and country:
            lowered = text.lower()
            if (
                not self._text_wants_later_world(text)
                and any(keyword in lowered for keyword in ("student", "pupil", "teacher", "parent", "school", "classroom", "education"))
            ):
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
        lowered = text.lower()
        years = self._future_year_signal(lowered)
        if years is not None:
            if years >= 14:
                return "radical"
            if years >= 8:
                return "advanced"
        radical_cues = (
            "radical future",
            "radical agi future",
            "radical ai future",
            "far future",
            "well after the transition",
            "after the transition",
            "settlement era",
            "new settlement",
            "changed settlement",
            "changed ai settlement",
            "post scarcity",
            "post-scarcity",
            "near agi",
            "near-agi",
            "deeply transformed economy",
            "deeply transformed world",
            "transformed agi society",
            "much stranger agi society",
            "stranger agi society",
            "different civilization",
            "agi power contest",
            "hugely different economy",
            "much farther in the future",
            "machine-run",
            "robotics-heavy future",
            "already reorganized everyday life",
            "no longer organize life around a normal job week",
            "no longer organize life around the old job week",
            "old job order",
            "old labor order",
            "rival compute blocs",
            "compute blocs shape geopolitics",
            "radically different ways from today",
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
        if self._text_wants_later_world(lowered):
            if any(
                cue in lowered
                for cue in (
                    "settlement",
                    "daily life",
                    "ordinary security",
                    "old job",
                    "old labor",
                    "households live inside",
                    "already lives inside",
                    "already live inside",
                )
            ):
                return "radical"
            return "advanced"
        stage_match = re.search(r"\bstage\s+([3-9])\b", lowered)
        if stage_match:
            stage_number = int(stage_match.group(1))
            return "radical" if stage_number >= 4 else "advanced"
        return None

    def infer_starting_world_mode(self, text: str | None) -> str | None:
        normalized = " ".join(str(text or "").split()).strip()
        if not normalized:
            return None
        return self._extract_starting_world_mode(normalized)

    def _extract_future_setup_brief(self, text: str) -> str | None:
        sentences = self._sentence_split(text)
        if not sentences:
            cleaned = " ".join(text.split()).strip()
            sentences = [cleaned] if cleaned else []
        cue_phrases = (
            "skip ahead",
            "jump ahead",
            "start later",
            "later in the transition",
            "more advanced ai",
            "advanced ai world",
            "advanced ai future",
            "radical future",
            "radical agi future",
            "radical ai future",
            "far future",
            "future economy",
            "different economy",
            "different world",
            "deeply transformed",
            "much farther in the future",
            "10 years in the future",
            "15 years in the future",
            "20 years in the future",
            "10 years from now",
            "15 years from now",
            "20 years from now",
            "ten years in the future",
            "fifteen years in the future",
            "twenty years in the future",
            "ten years from now",
            "fifteen years from now",
            "twenty years from now",
        )
        picked: list[str] = []
        for sentence in sentences:
            lowered = sentence.lower()
            if any(cue in lowered for cue in cue_phrases) or re.search(
                r"\b(?:\d{1,2}|eight|ten|twelve|fifteen|sixteen|seventeen|eighteen|nineteen|twenty)"
                r"(?:\s*(?:-|to)\s*(?:\d{1,2}|eight|ten|twelve|fifteen|sixteen|seventeen|eighteen|nineteen|twenty))?"
                r"\s+years?\s+(?:from now|ahead|in the future|after)\b",
                lowered,
            ):
                picked.append(" ".join(sentence.split()))
        if not picked:
            return None
        combined = " ".join(dict.fromkeys(picked))
        clipped = self._trim_without_ellipsis(combined, 360)
        if clipped and clipped[-1] not in ".!?":
            clipped += "."
        return clipped or None

    def _extract_focus_phrase(self, text: str) -> str | None:
        patterns = (
            r"(?:focused on|focus on|lens on|built around|centered on)\s+(.+?)(?=(?:[.!?]|$))",
            r"(?:make this|keep this|frame this|set this|run this)\s+about\s+(.+?)(?=(?:[.!?]|$))",
        )
        for pattern in patterns:
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if not match:
                continue
            value = " ".join(match.group(1).strip(" .").split())
            if value:
                return value
        return None

    def _focus_phrase_is_narrow_topic(self, focus_phrase: str) -> bool:
        normalized = " ".join(str(focus_phrase or "").lower().split())
        if not normalized:
            return False
        if re.search(r"\b(?:\d{1,2}|ten|fifteen|twenty)\s+years?\s+(?:from now|ahead|in the future)\b", normalized):
            return False
        broad_future_cues = (
            "future",
            "far future",
            "later stage",
            "start later",
            "skip ahead",
            "advanced ai",
            "radical ai",
            "radical agi",
            "structurally remade",
            "different economy",
            "different world",
            "daily life",
            "new income systems",
            "new household routines",
            "new forms of state power",
            "geopolitical realignment",
            "compute politics",
            "blocs or regions",
        )
        return not any(cue in normalized for cue in broad_future_cues)

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
        match = re.search(
            r"(?:documentary|art|imagery|visuals?)\s+(?:should be|to be|be|feel)\s+(.+?)(?=(?:[.!?]|$))",
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

    def _natural_start_point_note(self, config: SimulationConfig) -> str:
        starting_world_mode = self._effective_world_mode(config)
        if starting_world_mode == "radical":
            return "The setup points toward a later and more structurally changed AI society; begin from that altered settlement rather than easing in from the present."
        if starting_world_mode == "advanced":
            return "The setup points later into the transition, with deeper AI diffusion and institutional change already underway."
        return "Start near the present and let the stages grow stranger only as the setup and chapter evidence justify it."

    def _setup_direction_block(self, config: SimulationConfig) -> str:
        lines: list[str] = []
        if str(config.region_focus or "").strip():
            lines.append(f"- regional emphasis: {config.region_focus}")
        if str(config.topic_lens or "").strip():
            lines.append(f"- topical emphasis: {config.topic_lens}")
        if str(config.premise or "").strip():
            lines.append("- player-authored future brief to honor unless the chapter itself explains why reality stayed more familiar:")
            lines.append(f'  "{config.premise}"')
            lines.append("- treat that brief as a live creative prior, not background flavor")
        if str(config.stakes or "").strip():
            lines.append(f"- political stakes: {config.stakes}")
        lines.append(f"- inferred start point from the setup: {self._natural_start_point_note(config)}")
        return "\n".join(lines)

    def _stage_reads_like_later_settlement(self, stage: StagePackage) -> bool:
        settlement_fields = [
            stage.household_income_system,
            stage.capability_access_norm,
            stage.firm_structure_norm,
            stage.ownership_regime,
            stage.public_service_norm,
        ]
        if any(str(field or "").strip() for field in settlement_fields):
            return True
        combined = " ".join(
            [
                str(stage.phase_label or ""),
                str(stage.state_of_world or ""),
                str(stage.detailed_summary or ""),
                str(stage.main_split or ""),
            ]
        ).lower()
        cues = (
            "job week",
            "machine dividend",
            "service credit",
            "public ai utility",
            "toll road",
            "compute",
            "agent fleet",
            "ration",
            "platform rent",
            "platform royalty",
            "settlement",
            "blocs",
        )
        return any(cue in combined for cue in cues)

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

    def _strip_trailing_connector_words(self, text: str) -> str:
        cleaned = " ".join(str(text or "").split()).strip(" ,.;:-")
        if not cleaned:
            return ""
        trailing_connectors = {
            "that",
            "which",
            "because",
            "while",
            "and",
            "or",
            "to",
            "with",
            "for",
            "of",
            "in",
            "through",
            "via",
            "across",
            "around",
            "into",
            "onto",
            "over",
            "under",
            "between",
            "among",
            "inside",
            "outside",
            "within",
            "without",
            "by",
            "from",
            "at",
            "on",
            "off",
            "up",
            "down",
            "out",
        }
        words = cleaned.split()
        while len(words) > 1 and words[-1].lower() in trailing_connectors:
            words.pop()
        return " ".join(words).strip(" ,.;:-")

    def _collapse_adjacent_word_repeats(self, text: str) -> str:
        collapsed = " ".join(str(text or "").split()).strip()
        if not collapsed:
            return ""
        pattern = re.compile(r"\b([A-Za-z][A-Za-z'-]*)\b(?:\s+\1\b)+", flags=re.IGNORECASE)
        while True:
            updated = pattern.sub(lambda match: match.group(1), collapsed)
            if updated == collapsed:
                return updated
            collapsed = updated

    def _plain_language_cleanup(self, text: str) -> str:
        cleaned = " ".join(str(text or "").split()).strip()
        if not cleaned:
            return ""
        replacements = (
            (r"\bcivic ai accounts\b", "public AI accounts"),
            (r"\bcivic ai account\b", "public AI account"),
            (r"\bcivic accounts\b", "public accounts"),
            (r"\bcivic account\b", "public account"),
            (r"\bmachine dividends\b", "monthly machine checks"),
            (r"\bmachine dividend\b", "monthly machine check"),
            (r"\bservice credits\b", "monthly help credits"),
            (r"\bservice credit\b", "monthly help credit"),
            (r"\bpublic ai utilities\b", "public AI systems run like basic services"),
            (r"\bpublic ai utility\b", "public AI system run like a basic service"),
            (r"\baccess rights\b", "guaranteed access"),
            (r"\bbargaining power\b", "room to refuse bad terms"),
        )
        for pattern, replacement in replacements:
            cleaned = re.sub(pattern, replacement, cleaned, flags=re.IGNORECASE)
        return " ".join(cleaned.split()).strip()

    def _normalize_sentence(self, text: str, *, max_words: int = 22, max_chars: int = 150) -> str:
        cleaned = self._plain_language_cleanup(self._collapse_adjacent_word_repeats(text)).strip(" -")
        if not cleaned:
            return ""
        words = cleaned.split()
        if len(words) > max_words:
            cleaned = " ".join(words[:max_words]).rstrip(",;:")
        cleaned = self._trim_without_ellipsis(cleaned, max_chars).rstrip(",;:")
        cleaned = self._strip_trailing_connector_words(cleaned)
        if cleaned and cleaned[-1] not in ".!?":
            cleaned = f"{cleaned}."
        return cleaned

    def _normalize_question(self, text: str, *, max_words: int = 18, max_chars: int = 128) -> str:
        cleaned = self._collapse_adjacent_word_repeats(text).strip(" -")
        if not cleaned:
            return ""
        words = cleaned.split()
        if len(words) > max_words:
            cleaned = " ".join(words[:max_words]).rstrip(",;:.?")
        cleaned = self._trim_without_ellipsis(cleaned, max_chars).rstrip(",;:.?")
        cleaned = self._strip_trailing_connector_words(cleaned)
        if not cleaned:
            return ""
        return f"{cleaned}?"

    def _featurette_question_fallback(
        self,
        *,
        subject: str | None,
        title: str | None,
        stage: StagePackage,
    ) -> str:
        normalized_subject = " ".join(str(subject or "").split()).strip()
        normalized_title = " ".join(str(title or "").split()).strip()
        if normalized_subject and not re.fullmatch(r"reel\s+\d+", normalized_subject, flags=re.IGNORECASE):
            return self._normalize_question(f"What changed about {normalized_subject.lower()} in this future?")
        if normalized_title:
            return self._normalize_question(f"What does {normalized_title.lower()} reveal about this future?")
        if stage.main_split:
            return self._normalize_question(f"How does this reel explain {stage.main_split.rstrip('.?')}")
        return "What part of this future does this reel explain?"

    def _normalize_summary_prose(self, text: str, *, max_paragraphs: int) -> str:
        cleaned = str(text or "").replace("**", "").strip()
        if not cleaned:
            return ""
        cleaned = re.sub(
            r"(?im)^(capability frontier|economic picture|household life|households and politics|political picture|politics|world state|state of the world|governing question|still not true yet)\s*[:\-]\s*",
            "",
            cleaned,
        )
        cleaned = re.sub(
            r"(?im)^(capability frontier|economic picture|household life|households and politics|political picture|politics|world state|state of the world|governing question|still not true yet)\s*$",
            "",
            cleaned,
        )
        cleaned = self._plain_language_cleanup(cleaned)
        paragraphs = [
            self._plain_language_cleanup(" ".join(part.split()))
            for part in re.split(r"\n\s*\n", cleaned)
            if self._plain_language_cleanup(" ".join(part.split())).strip()
        ][:max_paragraphs]
        return "\n\n".join(paragraphs).strip()

    def _normalize_room_briefing(self, text: str) -> str:
        normalized: list[str] = []
        for sentence in self._sentence_split(text)[:4]:
            cleaned = self._plain_language_cleanup(" ".join(str(sentence or "").split())).strip(" -")
            if not cleaned:
                continue
            speakable = self._normalize_sentence(cleaned, max_words=24, max_chars=148)
            if speakable:
                normalized.append(speakable)
        return " ".join(normalized).strip()

    def _room_briefing_is_speakable(self, text: str) -> bool:
        sentences = self._sentence_split(text)
        if not sentences or len(sentences) > 5:
            return False
        total_words = 0
        for sentence in sentences:
            cleaned = " ".join(str(sentence or "").split()).strip()
            if not cleaned:
                continue
            total_words += len(cleaned.split())
            if len(cleaned.split()) > 28:
                return False
            if cleaned.count(",") > 2 or ";" in cleaned or ":" in cleaned:
                return False
        return total_words <= 108

    def _normalize_authored_lines(self, items: list[str], *, limit: int) -> list[str]:
        normalized: list[str] = []
        for item in items:
            cleaned = self._plain_language_cleanup(" ".join(str(item or "").split())).strip(" -")
            if not cleaned:
                continue
            normalized.append(cleaned.rstrip("."))
            if len(normalized) >= limit:
                break
        return normalized

    def _resolve_room_briefing(
        self,
        *,
        authored_room_briefing: str | None,
        dominant_mechanism: str | None,
        dominant_upside: str | None,
        economic_indicators: list[str] | None,
        main_split: str | None,
        suggested_policy_axes: list[str] | None,
        still_hard_now: str | None,
        physical_world_status: str | None,
    ) -> str:
        authored_raw = " ".join(str(authored_room_briefing or "").split()).strip()
        if authored_raw and len(authored_raw.split()) >= 14:
            return authored_raw
        authored = self._normalize_room_briefing(authored_room_briefing or "")
        if authored:
            return authored
        return self._compose_room_briefing(
            dominant_mechanism=dominant_mechanism,
            dominant_upside=dominant_upside,
            economic_indicators=economic_indicators,
            main_split=main_split,
            suggested_policy_axes=suggested_policy_axes,
            still_hard_now=still_hard_now,
            physical_world_status=physical_world_status,
            fallback_room_briefing=authored_room_briefing,
        )

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
            cleaned = self._strip_trailing_connector_words(cleaned)
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

        def gain_fragment(value: str) -> str:
            if not value:
                return ""
            stripped = re.sub(
                r"^(?:(?:[A-Za-z-]+)\s+){0,5}?want to keep\s+",
                "",
                value,
                count=1,
                flags=re.IGNORECASE,
            ).strip(" .,:;-")
            return stripped or value

        def split_sentence(value: str) -> str:
            if not value:
                return ""
            lowered = value.lower()
            if lowered.startswith("the fight is over "):
                focus = value[len("the fight is over ") :].strip(" .,:;-")
                return sentence("The broad split is over ", focus)
            if lowered.startswith(("who ", "whether ", "how ", "which ", "where ")):
                return sentence("The broad split is over ", value)
            return sentence("The broad split is ", value)

        def lever_sentence(value: str) -> str:
            if not value:
                return ""
            first_word = value.split(" ", 1)[0].lower()
            verb_like = {
                "accelerate",
                "expand",
                "keep",
                "build",
                "open",
                "fund",
                "link",
                "guarantee",
                "mandate",
                "treat",
                "universalize",
                "socialize",
                "cap",
                "license",
                "tax",
                "tie",
            }
            prefix = "One live lever is to " if first_word in verb_like else "One live lever is "
            return sentence(prefix, value)

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
            strip_prefixes=("the split is", "main split is", "the main split is", "the core split is", "core split is", "the live split is"),
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
        gain_sentence = sentence("One gain people already like is ", gain_fragment(gain))
        macro_sentence = sentence("The broad read is ", macro)
        split_sentence_text = split_sentence(split or tradeoff)
        lever_sentence_text = lever_sentence(lever)
        tradeoff_sentence = sentence("What still binds is ", tradeoff)

        if any(token in (split or "").lower() for token in ("fight", "whether", "who ", "how ", "control", "access", "ownership")):
            ordered = [
                split_sentence_text,
                gain_sentence,
                macro_sentence,
                lever_sentence_text or tradeoff_sentence,
            ]
        elif any(token in (gain or "").lower() for token in ("monthly", "machine", "public", "platform", "compute", "ration", "robot", "border", "war", "allowance")):
            ordered = [
                gain_sentence,
                macro_sentence,
                split_sentence_text,
                tradeoff_sentence or lever_sentence_text,
            ]
        else:
            ordered = [
                macro_sentence,
                gain_sentence,
                split_sentence_text,
                lever_sentence_text or tradeoff_sentence,
            ]

        composed_parts: list[str] = []
        for sentence_text in ordered:
            if not sentence_text or sentence_text in composed_parts:
                continue
            composed_parts.append(sentence_text)
            if len(composed_parts) >= 4:
                break
        if tradeoff_sentence and tradeoff_sentence not in composed_parts and len(composed_parts) < 4:
            composed_parts.append(tradeoff_sentence)
        composed = " ".join(composed_parts)
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
            cleaned = self._plain_language_cleanup(" ".join(str(item or "").split())).strip(" -")
            if not cleaned:
                continue
            cleaned = self._trim_without_ellipsis(cleaned, max_chars)
            cleaned = self._strip_trailing_connector_words(cleaned)
            if not cleaned:
                continue
            if sentence_fragment:
                cleaned = cleaned.rstrip(".")
            elif cleaned[-1] not in ".!?":
                cleaned = f"{cleaned}."
            normalized.append(cleaned)
            if len(normalized) >= limit:
                break
        return normalized

    def _normalize_narration_line(self, text: str) -> str:
        cleaned = self._plain_language_cleanup(self._collapse_adjacent_word_repeats(text)).strip()
        if not cleaned:
            return ""
        cleaned = re.sub(r"\bsearch draft compare plan and code\b", "handle routine computer work", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\bsearch compare draft code and plan\b", "support routine software workflows", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\bplan route draft and compare\b", "guide ordinary planning work", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\btutoring planning and software help\b", "dependable help on a screen", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\bmodels data and distribution\b", "models and distribution", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\bscreen based\b", "screen-based", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\bwhite collar\b", "white-collar", cleaned, flags=re.IGNORECASE)
        # Some live generations split list fragments with periods before lowercase words
        # (for example "coding. compliance"), which sounds broken in voiceover. Treat
        # those as accidental clause breaks instead of full sentence stops.
        cleaned = re.sub(r"(?<=[a-z0-9])\.\s+(?=[a-z])", ", ", cleaned)
        cleaned = re.sub(r"\s*;\s*", ". ", cleaned)
        cleaned = re.sub(r"\s*[–—]\s*", ". ", cleaned)
        sentences = [part.strip() for part in re.split(r"(?<=[.!?])\s+", cleaned) if part.strip()]
        if len(sentences) > 3:
            cleaned = " ".join(sentences[:3]).strip()
        if cleaned[-1] not in ".!?":
            cleaned = f"{cleaned}."
        return cleaned

    def _featurette_instructions(self) -> str:
        return (
            "You are writing optional documentary side reels for the same AGI transition chapter. "
            "Think of them as short educational featurettes the player can choose from while the main chapter is already live. "
            "Return three reels that answer three different questions a curious player would naturally ask about this world. "
            "Treat any examples in the prompt as menus, not templates. "
            "Each reel should teach one system, bargain, or pressure clearly in 3 or 4 beats, not restate the main montage. "
            "Make the three reels materially different from each other in both question and mechanism. "
            "At least one reel should usually explain everyday life or household security unless the chapter strongly points elsewhere. "
            "In later or stranger chapters, it is fully valid to explain machine income, public AI help run like a basic service, altered work rhythms, sovereign blocs, rationing, public-service automation, or other changed arrangements if they are truly live here. "
            "At least one reel in a far-future chapter should explain a social or economic arrangement that would sound materially post-current to a 2026 audience while still feeling coherent and concrete. "
            "If the chapter already lives inside a changed settlement, at least 2 reels should leave software-workflow land and explain household life, public authority, ownership, security, or bloc conflict when those are the live forces here. "
            "Be plainspoken, vivid, and specific. Avoid named-place filler, memo prose, vague futurism, slogan lines, classroom stiffness, and consultant fog. "
            "Avoid bureaucratic labels like service credit, machine dividend, access rights, or bargaining power unless the same line immediately translates them into ordinary speech. "
            "Prefer public names like monthly machine check, public AI help line, or monthly help credits when the simpler wording works. "
            "Prefer public names like monthly machine check, public AI account for basic help, or monthly help credits when the world needs a label. "
            "Use short clean lines. If a sentence wants a third clause, split it. "
            "Let the writing breathe. One reel can open with a system image, another with a household consequence, another with a chokepoint or institutional consequence. "
            "A strong set often includes one kitchen-table explainer, one reel about power or control, and one reel about a concrete everyday system, unless this chapter clearly points somewhere else. "
            "Do not make the set feel like a curriculum. It should feel like three smart side documentaries, not three boxes on a syllabus. "
            "The reels do not need to divide neatly into household, institutions, and politics; choose the three questions that actually unlock this world. "
            "Explain the mechanism like you are talking to an interested friend in one pass, not like you are naming a framework. "
            "Avoid insider shorthand when you can. If a beat uses a term like utility, leverage, dependence, or control, define it in the same sentence or next clause with plain words about money, access, staffing, ownership, prices, or who can say yes or no. "
            "Do not make every beat sound like a neat thesis sentence. Let one beat be a blunt fact, another a lived consequence, another the institutional catch. "
            "Titles should sound inviting without becoming theatrical. "
            "The question field must be a specific viewer-facing question in natural language, not an empty field and not a generic placeholder. "
            "Do not default to a token town, farm, or factory vignette unless that place is doing real causal work. "
            "Image prompts should stay painterly and civic, with impressionist abstraction, visible institutions, and real human activity. Lean toward a Cezanne-Monet-Matisse hybrid instead of glossy realism, CGI sheen, empty hologram spectacle, or cartoon imagery."
        )

    def _featurette_prompt(self, *, config: SimulationConfig, stage: StagePackage) -> str:
        settlement_lines = [
            ("Household security", stage.household_income_system),
            ("Everyday access", stage.capability_access_norm),
            ("Firm staffing", stage.firm_structure_norm),
            ("Ownership", stage.ownership_regime),
            ("Public services", stage.public_service_norm),
        ]
        settlement_block = "\n".join(
            f"- {label}: {self._clip(value, 132)}"
            for label, value in settlement_lines
            if str(value or "").strip()
        ) or "- The chapter still reads as a nearer-term transition."
        citizen_lines = "\n".join(
            f"- {citizen.display_name}, {citizen.role} in {citizen.region}: {self._clip(citizen.current_update or citizen.summary, 150)}"
            for citizen in stage.sample_citizens[:4]
        ) or "- No citizen snapshots yet."
        return (
            f"Country: {config.country}\n"
            f"Stage: {stage.phase_label}\n"
            f"Title: {stage.title}\n"
            f"Main montage logline: {stage.montage_logline}\n"
            f"World summary: {stage.detailed_summary}\n"
            f"Room brief: {stage.authored_room_briefing or stage.room_briefing}\n"
            f"Dominant mechanism: {stage.dominant_mechanism}\n"
            f"Dominant upside: {stage.dominant_upside}\n"
            f"Main split: {stage.main_split}\n"
            f"Still hard: {stage.still_hard_now or stage.physical_world_status}\n"
            f"Visual style: {config.visual_style}\n\n"
            "Settlement details in frame:\n"
            f"{settlement_block}\n\n"
            "Policy axes and public pressures:\n"
            f"- {'; '.join(stage.authored_policy_axes or stage.suggested_policy_axes[:4]) or 'No fixed board yet.'}\n"
            f"{self._salient_poll_lines(stage.poll_summaries, limit=4)}\n\n"
            "Sample lived evidence:\n"
            f"{citizen_lines}\n\n"
            "Return exactly 3 featurettes.\n"
            "The set should feel like a small shelf of optional educational reels the player can choose between.\n"
            "Keep the 3 viewer questions that would most help someone understand this future.\n"
            "Each featurette should answer a different question about how this economy now works.\n"
            "Make the set feel like three genuinely different reasons to click deeper, not two real reels plus a paraphrase.\n"
            "For each featurette, provide:\n"
            "- a short subject label of 2 to 5 words\n"
            "- one short natural-language question the reel answers for the player\n"
            "- a title that sounds like a documentary chapter card\n"
            "- a logline of about 18 to 30 words\n"
            "- 3 or 4 narrative beats in coherent order\n"
            "- fresh prose and a fresh mechanism, not warmed-over stage summary lines\n"
            "- plain, understandable detail: what changed, why it matters, and what money, access, staffing, ownership, or control channel now does the work\n"
            "- a distinct opening move and rhythm for each reel\n"
            "- at most one local vignette beat per featurette unless a place is doing real causal work\n"
            "- at least one reel should teach a social or economic arrangement that would sound materially post-current to a 2026 viewer, not just a stronger version of today's workflow tools"
        )

    def _stage_instructions(self, config: SimulationConfig) -> str:
        starting_world_mode = self._effective_world_mode(config)
        instructions = [
            "You are writing one chapter of a national AGI transition simulation.",
            "Project forward with coherent causality, not timid present-day extrapolation and not empty sci-fi spectacle.",
            "Treat any example lists in the prompt as menus, not content to copy.",
            "Write in plain documentary English that a policymaker or ordinary voter could repeat after one hearing.",
            "Stay macro-first: say what AI can now do, where it spread, what became genuinely better, what still binds, and what conflict now organizes politics.",
            "Lead with a real gain before the strain so the audience understands why households, firms, or institutions kept adopting these systems.",
            "Decide the settlement once and carry it through.",
            "Before you describe capability in detail, silently lock four blunt baseline facts: how people get money, what replaced the old weekly routine, what mediates everyday access, and what power fight now dominates.",
            "Know how households secure ordinary life, what many adults do with their time, who controls everyday access, what still stays scarce, and what conflict now organizes politics.",
            "In a later-world chapter, let the first paragraph surprise a 2026 listener with one normal fact about income, routine, ownership, or public authority that would sound clearly post-current.",
            "Prefer broad capability, prices, who gets access, who owns the systems, how public services run, state capacity, family routine, and geopolitics over office churn, queue relief, or junior-ladder cliches.",
            "Avoid future-jargon, consultant throat-clearing, and any sentence that sounds smarter than it is clear.",
            "Prefer public names like monthly machine check, public AI help line, or monthly help credits when the simpler wording works.",
            "Prefer public names like monthly machine check, public AI account for basic help, or monthly help credits when the world needs a label.",
            "Make the upside concrete in ordinary life or institutional life: cheaper expertise, stronger small-firm leverage, better learning, easier care, more capable public service, or another lived improvement.",
            "Say what still requires people, trust, scarce infrastructure, or physical deployment in plain words.",
            "If the setup points to a later or stranger world, commit to a coherent settlement instead of a hotter version of 2026.",
            "If the chapter could plausibly describe a mildly advanced 2026 with slightly better software, it is not far enough.",
            "In a later-world opening, 'capable help got cheap' is not a full settlement by itself. Show what that price collapse changed in money, routine, public authority, ownership, or time use.",
            "Let income, access, ownership, firm structure, public services, family routine, and geopolitics all be eligible to change together when the setup supports it.",
            "If adults no longer organize life around a standard job week, say what replaced it and how people now secure time, status, and room to refuse a bad deal.",
            "If old labor indicators no longer explain security, say what does instead.",
            "Plain explanation beats coy futurism. If households live on monthly machine checks, public accounts that pay for basic help, rationed compute, public AI systems run like a basic service, or another concrete access system, say that directly.",
            "For later or stranger worlds, the first paragraph should quickly answer four plain questions: how money arrives, how people get capable systems, what adults do with their time, and who can stop or meter the system.",
            "If the setup supports it, let borders, wars, alliance systems, municipal government, welfare delivery, household structure, or daily time use change with the economics instead of sitting as background color.",
            "Avoid consultant filler, vague futurist language, slogan writing, and named-place color that does no causal work.",
            "Do not let every later chapter collapse into service convenience or cheaper expert help if the deeper change is income flows, ownership, housing, city power, war, or family budgets.",
            "In later-settlement openings, cheap expert help may be part of the world, but it should not be the whole headline if income, ownership, state power, or daily routine changed more deeply.",
            "Keep the prose linear and spoken. One clear claim beats a dense clever sentence.",
            "Do not spend tokens writing final documentary beats in this pass; the montage pass will write the shot-by-shot script.",
        ]
        if starting_world_mode in {"advanced", "radical"}:
            instructions.extend(
                [
                    "Let the opening chapter sound materially post-current, not like the present with louder adjectives.",
                    "Make the altered settlement legible in plain language early instead of hinting around it.",
                    "In the opening paragraph, make at least 2 settlement facts sound normal inside the world rather than speculative.",
                ]
            )
        if starting_world_mode == "radical":
            instructions.extend(
                [
                    "Start from an already changed settlement rather than a transition story that still sounds anchored in the 2020s.",
                    "Do not default to unemployment or hiring as the headline unless you also explain why those old metrics still organize security here.",
                    self._radical_settlement_menu().strip(),
                    "If the world is already strange, say the new normal plainly instead of backing away into mild present-day framing.",
                ]
            )
        return " ".join(instructions)

    def _stage_blueprint_instructions(self, config: SimulationConfig) -> str:
        starting_world_mode = self._effective_world_mode(config)
        instructions = [
            "You are outlining the macro and documentary spine for one AGI transition chapter.",
            "Do not write the final package yet. Decide the causal story, the opening macro sequence, the main upside, the main split, and the governing question.",
            "Treat any example lists as menus, not fixed content.",
            "Keep it macro-first and broad enough for a policymaker audience to orient quickly.",
            "Choose one dominant mechanism, one dominant gain people would defend, one dominant split, and one constituency actively pressing for more diffusion.",
            "Start with capability, diffusion, visible gains, and the bottleneck before any local vignette.",
            "Before you outline capability, lock one altered social baseline in plain language: how people get money, what replaced the old workweek for many adults, what mediates access, and what power conflict now organizes the country.",
            "Before you outline, silently decide the settlement itself: what replaced the old job week for many people, how households secure ordinary life, who controls access, what stays scarce, and what political fight follows from that.",
            "Prefer broad mechanisms like cheaper prices, wider access to expertise, ownership, buildout, public provision, concentration, or state capacity over office churn and queue cleanup.",
            "State plainly what AI can broadly do now, what still needs people, and what remains slow or hard in the physical world.",
            "Make the upside concrete in ordinary life and institutional life. The chapter should explain why people would actually want more of the capability.",
            "Keep the world coherent, but do not preserve familiar baselines just to calm the chapter down.",
            "Write the blueprint in plain English. If a line starts to feel like a catalog, cut it back to one category and one representative example.",
            "Do not default to a named-place vignette unless it is doing real causal work.",
            "If the chapter sits well into the future, assume some familiar baselines stopped explaining life cleanly and decide what replaced them.",
            "If the chapter is years ahead, change how people live before you change how commentators describe it.",
            "In a later-world chapter, the opening needs one blunt fact a 2026 audience would find genuinely new but still understandable.",
            "Do not let every later chapter resolve into service quality and convenience if the real change is who owns production, how households live, how cities govern, or how blocs fight over capacity.",
            "In a later-world opening, cheap expert help can be one effect, but the blueprint should center the deeper settlement if income, ownership, public authority, or daily routine changed more than that.",
            "If older categories like unemployment or hiring no longer organize ordinary security, do not resurrect them just to sound familiar.",
            "A structurally changed chapter should sound changed in the first few sentences through income, access, routine, ownership, or public authority, not only through more capable software.",
            "Do not accept 'the tools got much better' as the full explanation. Decide what new social order that created.",
            "If the setup supports it, the chapter may involve new state forms, machine-administered welfare, border hardening, urban rationing, public AI systems run like a basic service, municipal machine systems, or rewired family routines; keep those changes economically legible and plainly described.",
            "At least one of household security, everyday access, firm structure, ownership, or public-service delivery should sound genuinely new to a 2026 listener in a later-world chapter.",
            "A far-future chapter that still sounds banal, managerial, or present-anchored is a miss.",
        ]
        if starting_world_mode in {"advanced", "radical"}:
            instructions.extend(
                [
                    "Name the changed settlement directly instead of hinting around it.",
                    "The audience should hear different income flows, access channels, firm structure, ownership, or public-service delivery, not just stronger software.",
                    "Be willing to rewrite time use, welfare systems, state form, corporate structure, and geopolitics when the setup supports it, but make those changes legible in plain English.",
                ]
            )
        if starting_world_mode == "radical":
            instructions.extend(
                [
                    "Do not let a radical chapter sound like normal unemployment plus better copilots.",
                    "Prefer the settlement baseline over familiar labor shorthand when old job metrics no longer explain security well.",
                ]
            )
        return " ".join(instructions)

    def _montage_instructions(self) -> str:
        return (
            "You are writing only the narrated documentary montage for one AGI transition chapter. "
            "Do not rewrite the whole stage and do not invent a new center of gravity. Use the blueprint as fixed architecture. "
            "Treat examples as menus, not templates. Generate fresh documentary lines from the blueprint. "
            "Write like a mature short documentary script: calm, linear, specific, and easy to follow on first hearing. "
            "Build one arc the viewer can retell. "
            "Keep the opening macro-first. In the opening half, make the capability, the defended gain, the stubborn limit, and the baseline that organizes daily security easy to hear. "
            "Sound like future history when the chapter is later or stranger, not like a tech demo, a risk memo, or a campaign ad. "
            "The narrator should sound like one thoughtful observer walking through a changed country, not like a generator filling chapter slots. "
            "If the world is already strange, explain the new normal directly. "
            "You do not need to open with a tidy setup line or a local vignette; it is fine to open with the strangest stable fact in the country if that is the clearest orientation. "
            "Keep lines plainspoken and speakable. One beat should usually carry one main turn of the story. "
            "Favor short declarative sentences over stacked clauses. If a line wants three commas, rewrite it. "
            "If a beat uses an abstract term, immediately cash it out in plain words about money, access, time, ownership, control, or ordinary routine. "
            "If a beat uses a bureaucratic label like service credit, machine dividend, or public AI utility, define it in ordinary speech right away. "
            "Prefer public names like monthly machine check, public AI help line, or monthly help credits over world-internal labels when the simpler wording works. "
            "Avoid shorthand like sovereignty, legitimacy, or bargaining power unless the same beat translates it into everyday consequences people can picture. "
            "Do not march mechanically through identical beat jobs if the clearest script wants one idea to breathe across two adjacent beats. "
            "Let a beat linger when that makes the narration feel human and legible; do not force equal-sized beat jobs. "
            "Do not give every beat the same sentence shape or the same explanatory rhythm; the montage should sound composed, not templated. "
            "Do not make every beat sound like a thesis sentence. Mix blunt facts, lived consequences, and clear institutional explanation. "
            "Do not start every other beat with a year marker, transition phrase, or thesis-style setup. Vary the openings so it sounds like real narration, not a slide deck. "
            "One early beat may simply name the new normal in a blunt line before the script starts unpacking it. "
            "In a later or stranger chapter, one of the first two beats should sound like a blunt fact about the new social order, not just a stronger product pitch. "
            "If a later-world chapter has one startling but coherent economic fact, let the viewer hear it plainly instead of saving it for the end. "
            "Do not force the same documentary move in every line; vary between system description, lived consequence, stubborn limit, and political split as the chapter demands. "
            "If the strangest thing about the chapter is the new normal itself, let an early beat say that directly before you narrow to any vignette. "
            "Avoid listy sector tours, office cliches, queue cliches, vague futurism, named-place filler, and clipped caption prose. "
            "Not every chapter should climax on convenience or service quality; if the live change is sovereignty, rationing, family time, war pressure, or machine ownership, let the montage say that plainly. "
            "Do not open with a token local color vignette like one farmer, one diner, or one town unless that scene reveals the governing system better than a macro opening would. "
            "At least one early beat should show what ordinary people or small organizations can now do that used to require more money, expertise, or staff. "
            "At least one beat should say what still resists automation, trusted judgment, or physical rollout. "
            "If the world is already strange, explain the new normal directly instead of backing away into mild present-day language."
        )

    def _montage_prompt(
        self,
        *,
        config: SimulationConfig,
        phase: dict[str, str],
        stage_output: OrchestratorStageOutput,
        blueprint: OrchestratorStageBlueprint,
    ) -> str:
        opening_lines = "\n".join(f"- {line}" for line in blueprint.opening_macro_sentences[:4]) or "- Keep the opening macro-first and settlement-first."
        macro_cues = "\n".join(f"- {line}" for line in blueprint.macro_cues[:4]) or "- Name prices, access, household security, capacity, or power clearly."
        documentary_movements = "\n".join(f"- {line}" for line in blueprint.documentary_movements[:7]) or "- Build a clean documentary arc from capability and gain to split and governing question."
        radical_opening_rules = (
            "- because this run starts from a radical world, the opening should make the settlement legible before any local vignette: how households secure ordinary life, how firms or agencies organize capability, what everyday access channel people live through, and what conflict or power order now governs politics\n"
            "- in radical openings, say what many people now do with their day if the old job structure no longer explains ordinary life\n"
            "- in radical openings, named places should usually stay out unless a specific place is doing unique causal work\n"
        ) if self._effective_world_mode(config) == "radical" else ""
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
            f"- Household income and security system: {blueprint.household_income_system}\n"
            f"- Everyday access channel: {blueprint.capability_access_norm}\n"
            f"- Firm staffing norm: {blueprint.firm_structure_norm}\n"
            f"- Ownership and chokepoint regime: {blueprint.ownership_regime}\n"
            f"- Public-service delivery norm: {blueprint.public_service_norm}\n"
            f"- Governing question: {blueprint.governing_question}\n"
            "Opening cues worth drawing from:\n"
            f"{opening_lines}\n"
            "Possible documentary movements to draw from:\n"
            f"{documentary_movements}\n"
            "Macro cues to name cleanly:\n"
            f"{macro_cues}\n"
            f"One defended gain: {blueprint.dominant_upside}\n"
            f"One hard limit: {blueprint.still_hard_now}\n"
            f"One still-slow physical constraint: {blueprint.physical_world_status}\n"
            f"One group pressing for more diffusion: {blueprint.pro_adoption_constituency}\n\n"
            "Return:\n"
            "- one montage_logline of about 18-28 words that states the chapter's causal story in one sentence\n"
            "- return 6 to 8 narrative beats, whichever count gives the cleanest documentary rhythm for this chapter\n"
            "- the beats should land around 135-220 words total\n"
            "- treat it like six to eight clean voiceover lines over six to eight shots, not one dense paragraph chopped into pieces\n"
            "- use the documentary movements as loose architecture, not fixed lines to paraphrase\n"
            "- do not try to restate all evidence; choose only the few facts the viewer must carry forward\n"
            "- keep the opening half national, sectoral, or institutional before any local example appears\n"
            "- in that opening half, make the viewer hear the capability class, one defended gain, one real limit, and the baseline that best organizes security and leverage here\n"
            "- if one altered economic or civic fact would immediately tell a 2026 viewer they are in a different settlement, let one of the first 3 beats say that plainly\n"
            f"{radical_opening_rules}"
            "- the later beats can cash out the split, the constituency pressing for more AI, and the governing question\n"
            "- use at most 2 late household, place, or personal beats\n"
            "- keep each beat to one dominant spoken idea, usually one sentence and occasionally two short sentences when that sounds more natural\n"
            "- do not make all beats the same length or cadence if the cleaner documentary rhythm wants one short blunt line beside one slightly fuller explanation\n"
            "- let the wording sound like natural voiceover, not trimmed caption shorthand\n"
            "- commas are allowed when they preserve natural spoken rhythm, but avoid comma chains and spoken inventories\n"
            "- prefer one clean clause over a sentence that chains three developments with and, while, or as\n"
            "- if a beat wants multiple examples, elevate the category and keep the single best example\n"
            "- if a beat starts sounding like a sector list, compress it to one category and one concrete image\n"
            "- one early or middle beat should translate capability into everyday relief, leverage, cheaper expertise, or doing more outside an old skill boundary\n"
            "- do not repeat wait times, queues, paperwork, or back-office cleanup as the chapter's main image unless the blueprint clearly makes them central\n"
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
        setup_direction = self._setup_direction_block(config)
        starting_world_mode = self._effective_world_mode(config)
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
                "- the setup implies the opening chapter begins later in the transition, with institutions, firms, and households already living with deeper AI diffusion\n"
                "- do not snap back to a timid near-term frame; make at least one changed social or economic norm legible in plain language\n"
                "- name which institutions, income flows, firm staffing patterns, or public-service channels are already different\n"
            )
        elif stage_index == 0 and starting_world_mode == "radical":
            phase_guardrails = (
                "- the setup implies the opening chapter begins inside a later and more structurally changed AI society, not a mildly advanced version of the present\n"
                "- be bold about structural change, but keep it coherent: explain what abundance, scarcity, bargaining, ownership, state capacity, and physical bottlenecks now look like instead of drifting into fantasy omnipotence\n"
                "- make the opening clearly later than the default run, with everyday life, firm structure, public institutions, and power politics already reorganized\n"
                "- assume frontier AI can already do most remote cognitive work and expand ordinary people's leverage outside their training, while robotics and physical deployment remain uneven, contested, and bottlenecked unless you explicitly justify more\n"
                f"- {self._radical_settlement_menu()}\n"
                "- by the fourth opening macro sentence, the player should understand how households secure ordinary life, what institution mediates everyday AI access, what replaced older work routines for many people, and what conflict now orders politics\n"
                "- do not use unemployment staying low as the default serious-sounding macro frame unless the chapter clearly explains why it still matters; prefer monthly machine income, public AI help, household purchasing power, public provision, who owns the chokepoints, or the fact that old indicators no longer map cleanly onto security\n"
                "- if you place the chapter in the 2030s or later, the world should not read like the 2020s with sharper branding; show a different civilization, not just more tension\n"
                "- begin from a transformed baseline where the settlement itself is already different\n"
            )
        if stage_index >= 2:
            phase_guardrails += (
                "- because this is stage 3 or later, name at least 3 sectors or institutions being reshaped and at least 2 that are still lagging, protected, or bottlenecked\n"
                "- later stages should feel materially different from stage 1: cognitive labor markets, physical deployment, and national capacity should all move in visible ways\n"
            )
        blueprint_block = ""
        if blueprint:
            cues = "\n".join(f"  - {cue}" for cue in blueprint.macro_cues[:6])
            blueprint_block = (
                "Blueprint facts already locked:\n"
                f"- Causal arc: {blueprint.causal_arc}\n"
                f"- Capability frontier now: {blueprint.capability_frontier_now}\n"
                f"- Still hard now: {blueprint.still_hard_now}\n"
                f"- Physical-world status: {blueprint.physical_world_status}\n"
                f"- Dominant mechanism: {blueprint.dominant_mechanism}\n"
                f"- Dominant upside: {blueprint.dominant_upside}\n"
                f"- Main split: {blueprint.main_split}\n"
                f"- Pro-adoption constituency: {blueprint.pro_adoption_constituency}\n"
                f"- Household income and security system: {blueprint.household_income_system}\n"
                f"- Everyday access channel: {blueprint.capability_access_norm}\n"
                f"- Firm staffing norm: {blueprint.firm_structure_norm}\n"
                f"- Ownership and chokepoint regime: {blueprint.ownership_regime}\n"
                f"- Public-service delivery norm: {blueprint.public_service_norm}\n"
                f"- Governing question: {blueprint.governing_question}\n"
                "- Macro cues to surface clearly:\n"
                f"{cues or '  - none yet'}\n\n"
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
            f"Setup direction from the player:\n{setup_direction}\n"
            f"Visual style: {config.visual_style}\n"
            f"Population: {config.population_description}\n\n"
            f"{previous_block}\n"
            f"Tracking snapshot:\n{tracking_block or '- no prior tracking yet'}\n\n"
            f"Recent polling cues:\n{poll_block or '- no prior polls yet'}\n\n"
            f"Prior working policy board:\n{policy_notes_block}\n\n"
            f"Queued custom poll interests: {queued_poll_questions or ['none']}\n\n"
            "Design rules:\n"
            "- move the world materially from the prior stage; new capabilities, routines, and political arguments should visibly arrive\n"
            "- keep the scope national and socially mixed unless the setup explicitly narrows the lens, but do not use that as a reason to flatten the world back toward today's baseline\n"
            "- the player's future brief is not optional color; honor it unless this chapter itself explains why reality stayed more familiar\n"
            "- if the player's future brief names concrete changed routines, institutions, or geopolitical conditions, surface at least 2 of them as active settled facts in the opening instead of swapping them out for a safer generic AI-economy story\n"
            "- open macro-first: what AI can broadly do now, where it spread, what improved, what still binds, and what conflict now organizes politics\n"
            "- name the broad capability class before examples, and prefer one strong example over a list\n"
            "- make the economic mechanism easy to repeat in ordinary language: prices, access, who gets paid, margins, buildout, ownership, service quality, or security\n"
            "- before writing, silently answer five settlement questions: what replaced the old job week for many people, how households secure ordinary life, what adults now do with their time, who controls everyday access, and what scarcity or threat still rules the chapter\n"
            "- explain what ordinary people or smaller organizations can now do that used to require more time, money, expertise, or staff\n"
            "- do not default to wait times, office churn, or junior ladders unless the chapter truly turns on them\n"
            "- describe adoption in believable waves rather than implying the whole country changed at once\n"
            "- name at least one defended gain and at least one real bottleneck or tradeoff\n"
            "- if the world is later or stranger, make the settlement legible in income, access, staffing, public services, ownership, or geopolitics instead of writing a louder version of the present\n"
            "- if the chapter sits well into the future, change how family budgets, care routines, schooling, local services, or public authority work before you fall back to workplace metaphors\n"
            "- do not let every later or stranger chapter fall back to convenience, service quality, or clerical relief if ownership, war, housing, city power, borders, or time use changed more deeply\n"
            "- if the cleanest chapter is about public AI help run like a utility, municipal machine government, border hardening, wartime buildout, sovereign compute zones, new household bargaining, or another changed social order, say that plainly instead of pulling back to safer business language\n"
            "- keep the prose plain, causal, and documentary-natural; avoid consultant diction, slogan writing, and named-place filler that does no causal work\n"
            "- write a clean causal story first; do not turn the chapter into a checklist recital\n\n"
            f"{phase_guardrails}"
            "- if the setup opens well ahead of the present, make at least one macro sentence describe a social or economic arrangement that would sound genuinely post-current to a 2026 audience\n"
            "- if the setup opens well ahead of the present, let the viewer hear a bigger change in who can do competent work, who captures gains, or how institutions are organized; do not settle for a slightly hotter version of the present\n"
            "- if the future brief explicitly says the old job week stopped organizing life or that rival compute blocs shape power, make those facts legible early rather than burying them behind present-day labor metrics\n"
            "- if blueprint facts are provided below, use them as factual scaffolding, not as final prose; keep the same world and governing conflict, but write the chapter in fresh documentary language\n"
            "- do not hard-code generic forms of address like 'Mr. President'; either address the configured player by name or write neutrally\n"
            "- if the player is not in power, the room briefing should read like a campaign war-room brief, not an executive memo\n"
            "- leave room for ambiguity; do not force every section to close with a warning label\n"
            "- do not write final documentary beats in this pass; the montage pass owns the shot-by-shot script and image prompts\n"
            "- the narration should sound clean and readable, with no comma-heavy inventory feel and no paragraph that sounds like a list of talking points.\n"
            "- keep sentences clean enough to read aloud on first hearing; if a sentence wants a second comma, split the idea instead\n"
            "- the opening should move in a short script arc: capability first, then spread, then lived gain, then constraint, then the split\n"
            f"{blueprint_block}"
            "Return:\n"
            "- the phase label, matching this stage but phrased naturally\n"
            "- a stage title and year label\n"
            "- one montage logline of about 18-28 words that states the chapter's causal story in one sentence: what unlocked, what broadened, and what governing question remains\n"
            "- a world-state paragraph of about 150-220 words focused on what has become newly true in lived reality\n"
            "- the world-state paragraph should open macro-first with 3 or 4 clean sentences before any local example; together they should make the capability frontier, spread, defended gain, and live split or bottleneck legible on first hearing\n"
            "- those opening sentences should make clear what AI can now reliably do before the paragraph narrows to examples\n"
            "- keep those opening sentences speakable and linear, with one main claim each rather than clause-heavy stacks; most should land around 12-24 words\n"
            "- one early sentence should plainly situate the macro baseline: whichever measure best explains everyday security here, whether prices, service quality, margins, household purchasing power, public provision, access, or ownership concentration\n"
            "- in later-settlement openings, prefer the new income-and-access settlement over familiar labor-market shorthand\n"
            "- in later-settlement openings, at least 2 of those first 4 sentences should describe a changed settlement in ordinary language rather than a more intense version of today's labor market\n"
            "- in later-settlement openings, at least 1 of those first 2 sentences should name either the household-security arrangement, the everyday access channel, or the power bottleneck in plain language\n"
            "- if competent help got cheap, do not stop there; say what that changed about ordinary life, public services, firm shape, or who now controls the bottleneck\n"
            "- one early sentence should plainly say what ordinary people or smaller organizations can now do that used to require more time, money, expertise, or internal staff\n"
            "- the world-state paragraph should include several concrete macro cues in plain English, chosen from household bills, prices, access to expertise or care, margins or capex, hiring or vacancies, wages, export pressure, capability spread, or power/chip/buildout capacity\n"
            "- after the macro lead, use at most one localized example if it genuinely sharpens the chapter; otherwise stay systemic\n"
            "- in that paragraph, prefer fewer named examples and more causal explanation; one strong example is better than a list of three weak ones\n"
            "- in that paragraph, one sentence should plainly say what AI still cannot do well or cannot scale cheaply yet\n"
            "- a richer summary of about 420-620 words, written as 3 or 4 short paragraphs that move from capability and settlement into economic life, then households and politics, then what still resists or remains untrue\n"
            "- do not label those paragraphs with headers unless clarity truly demands it; natural documentary exposition is better than memo formatting\n"
            "- do not use markdown headers, memo labels, or section titles like Capability frontier or Economic picture inside the detailed summary\n"
            "- the summary should keep the broad capability class clear, make the settlement legible, explain the main economic mechanism, explain what ordinary people actually feel, and say what still binds\n"
            "- in later-settlement openings, at least one paragraph must plainly name the changed social settlement: who owns productive systems, how people get income or purchasing power, how firms staff work, how schools or credentials function, or how public services are mediated\n"
            "- if the deeper story is no longer service convenience, say so plainly and let ownership, housing, schooling, war pressure, city services, or family time take more space\n"
            "- a short room briefing for the player as a decision brief, usually in 3 or 4 short spoken lines, though 2 is fine when the room is very clear; cover one gain voters already like and would defend, what split or unfairness now matters, one live lever government can move this cycle, and the tradeoff or uncertainty that matters most, but do not force the same order every time\n"
            "- keep the room briefing speakable and spare; it should sound like briefing lines across a table, not a memo paragraph broken by periods or slot labels\n"
            "- return 4 to 6 economic indicators as plain-language bullets; each should be a clean sentence fragment of roughly 10-20 words, not a mini paragraph\n"
            "- return 3 to 5 major tension points; each should be a clean sentence fragment of roughly 12-24 words\n"
            "- return 4 or 5 plausible policy axes the player might debate; each should be a short lane label or brief phrase, not a paragraph\n"
            "- the policy axes must span genuinely different governing lanes rather than 4 versions of restriction; usually include one keep-it-open/pro-diffusion lane, one legitimacy-or-guardrails lane, one competition-or-access lane, and one distribution or bargaining-power lane when the stage supports them\n"
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
        setup_direction = self._setup_direction_block(config)
        starting_world_mode = self._effective_world_mode(config)
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
                "- the setup implies the opening chapter begins later in the transition, with visibly deeper diffusion, stronger institutional adoption, and clearer macro change already underway\n"
                "- do not collapse back into a timid present-day frame; let the first chapter feel like a later breakpoint in the transition while staying coherent and concrete\n"
                "- name which institutions, income flows, firm staffing patterns, or public-service channels are already different\n"
            )
        elif stage_index == 0 and starting_world_mode == "radical":
            phase_guardrails = (
                "- the setup implies the opening chapter begins after the old labor order has already been rewritten by machine capability, new entitlements, new ownership claims, or a new state form\n"
                "- be imaginative but disciplined: pick one coherent settlement and show how income, access, staffing, public services, daily routines, and geopolitics fit together inside it\n"
                "- do not write a slightly faster present-day world; make the opening feel like a later settlement where households, firms, and the state already live by different rules\n"
                "- assume frontier AI can already do most remote cognitive work and widely mediate services, but let the chapter decide whether that became a public utility, a private toll road, a sovereign bloc, a wartime mobilization system, or something stranger\n"
                f"- {self._radical_settlement_menu()}\n"
                "- by the end of the opening blueprint, it should already be clear what replaced the old baseline of jobs, who controls access, which institutions mediate everyday life, and what conflict now orders politics\n"
                "- do not use unemployment staying low as the safe default macro line; prefer machine income, public AI help, access, ownership concentration, compute access, public entitlements, sovereignty, or the fact that old indicators no longer summarize security well\n"
                "- if the chapter is set years ahead, make the economy feel years ahead too; do not let it sound like today's world with one louder controversy\n"
                "- begin from a transformed baseline where the settlement itself is already different\n"
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
            f"Setup direction from the player:\n{setup_direction}\n"
            f"Population: {config.population_description}\n\n"
            f"{previous_block}\n"
            f"Tracking snapshot:\n{tracking_block}\n\n"
            f"Recent polling cues:\n{poll_block}\n\n"
            f"Prior working policy board:\n{policy_notes_block}\n\n"
            f"Queued custom poll interests: {queued_poll_questions or ['none']}\n\n"
            "Design the chapter spine first:\n"
            "- choose the biggest macro split of this stage, not the handiest repeated trope\n"
            "- keep the scope national and socially mixed unless the setup explicitly narrows the lens, but do not use that as a reason to flatten the world back toward today's baseline\n"
            "- the player's future brief is not optional color; honor it unless the chapter itself explains why reality stayed more familiar\n"
            "- if the player's future brief names concrete changed routines, institutions, or geopolitical conditions, surface at least 2 of them as settled facts in the opening instead of replacing them with a safer generic AI-economy story\n"
            "- start from what AI can now reliably do, how widely that capability diffused, and what households or institutions now like enough to defend\n"
            "- name the broad capability class in plain language before you name a workflow or app surface\n"
            "- tie the gains to a real economic mechanism in plain English: prices, access, who gets paid, ownership, buildout, service quality, margins, or security\n"
            "- force capability clarity: say what AI can broadly do now, what still requires people, and what physical rollout still cannot do cheaply or at scale\n"
            "- include one newly possible action for ordinary people or smaller organizations and one plain limit the systems still hit\n"
            "- make the first defended gain larger than shorter waits or cleaner paperwork unless the stage evidence makes that unavoidable\n"
            "- do not keep defaulting to office-admin tropes when a broader capability or settlement story is available\n"
            "- if the setup opens well ahead of the present, name a genuinely post-current social arrangement or economic norm instead of a marginal improvement to a familiar workflow\n"
            "- if the setup opens inside a later settlement, the blueprint must commit to that settlement rather than hinting around it\n"
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
            "- household_income_system: one short sentence naming how households now get purchasing power or baseline security in this settlement\n"
            "- capability_access_norm: one short sentence naming the ordinary channel through which people or firms access AI capability day to day\n"
            "- firm_structure_norm: one short sentence naming how firms staff or organize around machine capability in this stage\n"
            "- ownership_regime: one short sentence naming who controls the key rents, chokepoints, or ownership claims in this settlement\n"
            "- public_service_norm: one short sentence naming how public services or civic institutions now deliver AI-shaped help, if that matters in this chapter\n"
            "- opening_macro_sentences: 3 or 4 clear macro sentences if they help, making capability frontier, diffusion, defended gain, and main split legible without sounding pre-written\n"
            "- documentary_movements: 4 to 6 optional movement lines if they help later montage work; keep them loose and documentary-natural rather than slot-filled\n"
            "- macro_cues: 3 to 5 concrete macro cues or rough directional statistics in plain English, including at least one blunt baseline read and one cue about price, service capacity, concentration, or geopolitics\n"
            "- governing_question: one sentence that names the real decision pressure the player steps into"
        )

    def _phase_brief(self, stage_index: int, stage_count: int, starting_world_mode: str = "default") -> dict[str, str]:
        ladder = [
            {
                "label": "Practical AI Breakout",
                "brief": "Reliable AI crosses from novelty into ordinary national life. The first national question is what has become newly doable with software help, how quickly those gains spread, and which human or physical limits still bind.",
                "technology": "Agents are now dependable across a broad band of screen-based work and guided decisions. For many households, students, workers, and small organizations, they feel like dependable help on a computer: reliable enough to research options, draft usable work, tutor through a problem, and keep ordinary digital workflows moving. They still stumble on messy exceptions, deep trust, persuasion, leadership, and almost all physical work without supervision.",
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
                "brief": "Near-AGI systems reshape national power fast enough that geopolitics, alliance systems, and domestic legitimacy start moving together. The question is no longer just growth. It is who controls capability, who gets first access, what must be built or rationed, and which institutions can still steer the country.",
                "technology": "Near-AGI systems can run most remote knowledge work and much of the planning, research, compliance, and operational coordination that once required elite staffing. Physical rollout advances through fabs, grids, ports, logistics hubs, defense supply chains, and municipal utility systems first. Cheap universal robotics is still not here, but machine-managed infrastructure, procurement, and service allocation now shape daily life.",
                "politics": "Leaders are judged on whether they can turn capability into national resilience without letting access harden into toll roads, emergency rationing, or regional abandonment. Chips, power, housing, ports, migration pressure, public-service capacity, allied dependence, and internal legitimacy all sit inside the same fight.",
            },
            {
                "label": "Settlement Era",
                "brief": "AGI and robotics are embedded deeply enough that the argument is no longer whether life changed, but what kind of society emerged around the machines. Households, firms, schools, city services, and border regimes may all run by new rules. The central fight is whether the new abundance feels like agency, dependence, membership, or managed scarcity.",
                "technology": "Abundant digital experts and heavily automated industrial systems are normal across much of the economy, and the settlement itself changed. Small human cores direct what once took departments. Many households now live on some mix of a monthly machine check, basic AI help provided like a utility, platform payouts, and irregular human work instead of a stable job week. Schools, licensing, municipal services, and welfare delivery are rebuilt around constant machine mediation. Maintenance, housing, old infrastructure, messy streets, and trust-rich care still keep humans central and scarce.",
                "politics": "The conflict centers on who owns the systems, who can meter or cut off access, where the money flows, and whether ordinary people live inside open public systems, private toll roads, rival compute blocs, or some unstable mix of them. Daily convenience is no longer enough. The live question is who can say no, who can walk away, and who actually shares the surplus.",
            },
        ]
        if starting_world_mode == "radical" and stage_index == 0:
            return {
                "label": "Settlement Opening",
                "brief": "The country is already living inside a later AGI settlement. Decide what kind of settlement emerged, how households secure ordinary life, what many people now do instead of the old job baseline, what scarcity still governs daily life, and which conflict now dominates.",
                "technology": "Frontier systems may already run most remote cognitive work and large parts of routine coordination. Decide which services became standing machine infrastructure, which institutions still rely on humans, how everyday access is mediated, and how far robotics spread before cost, trust, geography, or politics slowed it.",
                "politics": "Choose the main fight inside this settlement: public infrastructure versus private toll road, monthly machine checks versus platform rents, open access versus chokepoints, room to refuse bad terms versus dependence, rival blocs or war mobilization versus domestic abundance, or another coherent split that emerges from the chapter.",
            }
        phase_sequence_by_mode: dict[str, dict[int, list[int]]] = {
            "default": {
                3: [0, 1, 2],
                4: [0, 1, 2, 3],
                5: [0, 1, 2, 3, 4],
                6: [0, 1, 2, 3, 4, 4],
                7: [0, 1, 1, 2, 3, 4, 4],
                8: [0, 1, 1, 2, 3, 3, 4, 4],
            },
            "advanced": {
                3: [1, 2, 3],
                4: [1, 2, 3, 4],
                5: [1, 2, 3, 4, 4],
                6: [1, 2, 3, 4, 4, 4],
                7: [1, 2, 2, 3, 4, 4, 4],
                8: [1, 2, 2, 3, 3, 4, 4, 4],
            },
            "radical": {
                3: [2, 3, 4],
                4: [2, 3, 4, 4],
                5: [2, 3, 4, 4, 4],
                6: [2, 3, 3, 4, 4, 4],
                7: [2, 3, 3, 4, 4, 4, 4],
                8: [2, 3, 3, 4, 4, 4, 4, 4],
            },
        }
        phase_sequence_by_stage_count = phase_sequence_by_mode.get(starting_world_mode, phase_sequence_by_mode["default"])
        if stage_count <= 1:
            return ladder[phase_sequence_by_stage_count[3][0]]
        if stage_count == 2:
            short_sequence = phase_sequence_by_stage_count[3]
            return ladder[short_sequence[0 if stage_index == 0 else -1]]
        if stage_count in phase_sequence_by_stage_count:
            return ladder[phase_sequence_by_stage_count[stage_count][stage_index]]
        progression = round((stage_index / max(stage_count - 1, 1)) * (len(ladder) - 1))
        return ladder[max(0, min(progression, len(ladder) - 1))]

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
            "Build a specific, atmospheric composition with a clear camera distance, strong silhouettes, layered foreground and background forms, lived-in institutions, and people actually doing something. "
            "Prefer consequential civic, industrial, domestic, classroom, clinical, retail, or infrastructure scenes over generic office tableaux. "
            "Favor wide or medium establishing shots unless the prompt explicitly asks for an intimate close-up. "
            "Avoid defaulting to a tabletop, laptop-on-desk, or single-worker close crop when the narration is describing a national or sectoral shift. "
            "Render it as a painterly oil-or-gouache civic impression, not literal reportage. "
            "Lean toward a Cezanne-Monet-Matisse hybrid: Cezanne structure, Monet atmosphere, Matisse color blocks. "
            "Emphasize visible brushstrokes, planar color masses, softened edges, abstracted faces and hands, atmospheric light, and selective detail over photoreal texture. "
            "Push the image toward semi-abstract impressionism: bold shapes first, human gesture second, fine literal detail last. "
            "Let bold color blocks, flattened depth, softened anatomy, and partial abstraction carry the scene so it feels authored and cinematic rather than photographic. "
            "Let faces read as gestures and color notes rather than detailed portraits, and let architecture dissolve slightly at the edges instead of resolving into literal crisp realism. "
            "Avoid generic queue boards, call-center rows, anonymous cubicle farms, floating dashboards, glossy 3D render aesthetics, anime, comic-book stylization, empty hologram spectacle, sterile stock-photo staging, literal facial detail, photoreal skin texture, or ugly literalist realism unless the scene truly requires it."
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
            self._effective_world_mode(state.config),
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

    def _dummy_featurettes(self, state: SimulationState, stage: StagePackage) -> list[DocumentaryFeaturette]:
        return [
            DocumentaryFeaturette(
                subject="Household routines",
                question="How do households actually secure ordinary life in this economy?",
                title="What The New Competence Bought At Home",
                logline="A side reel on how families use cheap machine help for planning, care, learning, and ordinary coordination before politics fully catches up.",
                status="generating",
                narrative_beats=[
                    NarrativeBeat(
                        line="Families first felt the shift as competent help that could suddenly plan, compare, explain, and coach through ordinary decisions.",
                        image_prompt=self._polish_image_prompt(
                            state.config.visual_style,
                            "Painterly households using AI help for bills, schoolwork, care scheduling, and ordinary planning across kitchens, buses, and living rooms.",
                        ),
                    ),
                    NarrativeBeat(
                        line="That did not erase strain, but it moved expertise closer to people who used to need more money, time, or status to get it.",
                        image_prompt=self._polish_image_prompt(
                            state.config.visual_style,
                            "A civic domestic montage of households gaining access to tutoring, shopping advice, translation, and care coordination.",
                        ),
                    ),
                    NarrativeBeat(
                        line="The deeper fight became whether those gains stayed broad or narrowed into another rented layer of everyday life.",
                        image_prompt=self._polish_image_prompt(
                            state.config.visual_style,
                            "An impressionist domestic-political scene of households balancing convenience against platform dependence and rising access control.",
                        ),
                    ),
                ],
            ),
            DocumentaryFeaturette(
                subject="State capacity",
                question="Which public systems became more capable, and which ones still lagged?",
                title="The Agencies That Could Finally Keep Up",
                logline="A side reel on which public systems actually gained capacity, which still lagged, and why the state felt newly capable in some lanes and brittle in others.",
                status="generating",
                narrative_beats=[
                    NarrativeBeat(
                        line="Public agencies gained capacity unevenly, with the best-run systems using machine help to extend expert judgment instead of just clearing queues.",
                        image_prompt=self._polish_image_prompt(
                            state.config.visual_style,
                            "Painterly civic institutions using AI in permits, transit, clinics, classrooms, and emergency planning without glossy futurist effects.",
                        ),
                    ),
                    NarrativeBeat(
                        line="Where procurement, power, and local management held, the state looked more competent; where they failed, software alone could not rescue delivery.",
                        image_prompt=self._polish_image_prompt(
                            state.config.visual_style,
                            "An impressionist split-screen of capable public-service delivery against brittle local infrastructure and procurement bottlenecks.",
                        ),
                    ),
                    NarrativeBeat(
                        line="Citizens stopped asking only whether the tools worked and started asking which institutions deserved to control them.",
                        image_prompt=self._polish_image_prompt(
                            state.config.visual_style,
                            "A civic auditorium and agency montage about legitimacy, access, and control over public AI systems.",
                        ),
                    ),
                ],
            ),
            DocumentaryFeaturette(
                subject="Small firms",
                question="What changed for small operators once cheap machine capability became normal?",
                title="The Two-Person Company",
                logline="A side reel on how very small firms gained reach, where they still hit walls, and why local business politics changed with them.",
                status="generating",
                narrative_beats=[
                    NarrativeBeat(
                        line="Tiny firms stopped buying isolated software tools and started leasing whole layers of competent digital labor.",
                        image_prompt=self._polish_image_prompt(
                            state.config.visual_style,
                            "Painterly small businesses using AI for vendors, design, compliance, customer service, and planning in shops, studios, and mixed-use streets.",
                        ),
                    ),
                    NarrativeBeat(
                        line="That widened ambition as much as margins, because two people could now attempt work that once required a fuller office.",
                        image_prompt=self._polish_image_prompt(
                            state.config.visual_style,
                            "An impressionist montage of two-person firms building products, negotiating suppliers, and serving customers with machine support.",
                        ),
                    ),
                    NarrativeBeat(
                        line="The new limit was less talent than dependence on compute contracts, payment rails, and whichever platforms owned the best capability.",
                        image_prompt=self._polish_image_prompt(
                            state.config.visual_style,
                            "A civic-economic scene of small operators gaining power while still depending on compute providers, payment systems, and agent marketplaces.",
                        ),
                    ),
                ],
            ),
        ]

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
                or "cost of living" in question
                or "most unfair about where the gains" in question
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
        mood = self._salient_poll_lines(current_stage.poll_summaries).replace("\n", " | ").strip()
        upside = self._clip(current_stage.dominant_upside or "the gains people already like", 110)
        constituency = self._clip(current_stage.pro_adoption_constituency or "the people already benefiting", 110)
        split = self._clip(current_stage.main_split or "who captures the gains and who absorbs the risk", 110)
        player_lane = self._player_debate_lane(player_platform, current_stage.policy_notes)
        flagship_move = self._opponent_flagship_move(player_lane, current_stage, player_platform)
        themes = [
            f"protect or widen the visible gain: {upside.lower()}",
            f"answer the live split: {split.lower()}",
        ]
        if mood and mood != "- none yet":
            themes.append(f"answer the current voter mood instead of an ideology script: {mood.lower()}")
        themes.extend(
            [
                f"make the contrast land through a visibly different move such as {flagship_move.lower()}",
                f"the contrast should sharpen around {self._opponent_debate_lane(player_lane).lower()}",
                f"keep one constituency in frame: {constituency.lower()}",
            ]
        )
        return themes[:5]

    def _player_debate_lane(self, player_platform: str | None, policy_notes: list[str]) -> str:
        scores = self._debate_signal_scores(player_platform, policy_notes)
        descriptors: list[str] = []
        if scores["restriction"] >= 2 and scores["restriction"] > scores["pace"] + 1:
            descriptors.append("broad-brake leaning")
        elif scores["pace"] >= 2 and scores["pace"] > scores["restriction"] + 1:
            descriptors.append("pace-and-diffusion leaning")
        if scores["distribution"] >= 2:
            descriptors.append("distribution-heavy")
        if scores["state"] >= 2:
            descriptors.append("public-system heavy")
        elif scores["competition"] >= 2:
            descriptors.append("competition-first")
        if scores["security"] >= 2:
            descriptors.append("resilience-heavy")
        return ", ".join(descriptors[:2]) or "mixed or not yet fully declared"

    def _opponent_debate_lane(self, player_lane: str) -> str:
        if "broad-brake" in player_lane:
            return "pace, competition, and narrower remedies instead of a general brake"
        if "pace-and-diffusion" in player_lane:
            return "household payoff, leverage, or legitimacy instead of speed alone"
        if "distribution-heavy" in player_lane:
            return "access, competition, or buildout rather than redistribution alone"
        if "public-system heavy" in player_lane:
            return "contestability, mixed provision, or open access instead of one fixed channel"
        if "resilience-heavy" in player_lane:
            return "civilian payoff, flexibility, or allied diffusion instead of bunker logic alone"
        return "the missing governing choice this stage makes unavoidable"

    def _opponent_flagship_move(self, player_lane: str, stage: StagePackage, player_platform: str | None = None) -> str:
        contrast_axis = self._pick_contrast_axis(
            [*stage.suggested_policy_axes, stage.main_split, stage.dominant_mechanism],
            player_platform or " ".join(stage.policy_notes[:6]),
        )
        if contrast_axis:
            return contrast_axis
        if "broad-brake" in player_lane:
            return "keep visible gains moving with narrower abuse rules instead of a broad brake"
        if "pace-and-diffusion" in player_lane:
            return "tie the next wave to visible household payoff and recourse"
        if "distribution-heavy" in player_lane:
            return "widen access and expand capacity instead of only reallocating the gains after the fact"
        return stage.dominant_mechanism or "make one visibly different governing move instead of shadowing the player's line"

    def _debate_signal_scores(self, player_platform: str | None, policy_notes: list[str]) -> dict[str, int]:
        platform_text = " ".join([player_platform or "", *policy_notes[:6]]).lower()
        signal_groups = {
            "restriction": (
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
            ),
            "pace": (
                "accelerate",
                "speed",
                "fast",
                "deploy",
                "build",
                "scale",
                "expand",
                "adopt",
                "diffus",
                "buildout",
            ),
            "distribution": (
                "union",
                "bargain",
                "worker",
                "wage",
                "redistribut",
                "rebate",
                "dividend",
                "fairness",
                "household payoff",
                "labor standard",
            ),
            "state": (
                "public option",
                "state run",
                "public utility",
                "public service",
                "procurement",
                "guarantee",
                "nationalize",
            ),
            "competition": (
                "competition",
                "interoperability",
                "portability",
                "open access",
                "contestability",
                "open source",
                "antitrust",
            ),
            "security": (
                "resilience",
                "allied",
                "supply",
                "infrastructure",
                "strategic",
                "grid",
                "critical",
            ),
        }
        return {
            label: sum(1 for token in tokens if token in platform_text)
            for label, tokens in signal_groups.items()
        }

    def _pick_contrast_axis(self, candidates: list[str | None], platform_text: str | None) -> str:
        platform_tokens = {token for token in re.findall(r"[a-z]{4,}", (platform_text or "").lower())}
        ranked: list[tuple[int, int, int, str]] = []
        for index, candidate in enumerate(candidates):
            cleaned = " ".join(str(candidate or "").split()).strip()
            if not cleaned:
                continue
            candidate_tokens = {token for token in re.findall(r"[a-z]{4,}", cleaned.lower())}
            overlap = len(candidate_tokens & platform_tokens)
            ranked.append((overlap, index, -len(candidate_tokens), cleaned))
        if not ranked:
            return ""
        ranked.sort()
        return ranked[0][3]

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
