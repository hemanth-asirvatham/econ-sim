from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable
from pathlib import Path

from pydantic import BaseModel, Field

from ..config import AppSettings
from ..models import (
    CouncilAdvisorProfile,
    ConversationTurn,
    DebateReply,
    DocumentaryFeaturette,
    MacroStatistic,
    NarrativeBeat,
    PollSummary,
    SetupChamberGuidance,
    SetupSessionPatchRequest,
    SimulationConfig,
    SimulationState,
    StagePackage,
    StageTracking,
)
from .council import COUNCIL_VOICE_POOL
from .openai_client import OpenAIGateway

COUNCIL_FEMININE_HINTS = {
    "aisha",
    "amina",
    "amy",
    "ana",
    "andrea",
    "anya",
    "bella",
    "claire",
    "diana",
    "elena",
    "fatima",
    "grace",
    "hannah",
    "iris",
    "julia",
    "leila",
    "lena",
    "lucia",
    "maya",
    "maria",
    "naomi",
    "nina",
    "olivia",
    "priya",
    "rose",
    "sara",
    "sophia",
    "zoe",
}
COUNCIL_MASCULINE_HINTS = {
    "adrian",
    "alex",
    "andrew",
    "ben",
    "daniel",
    "darius",
    "david",
    "elias",
    "gabriel",
    "henry",
    "jonah",
    "marcus",
    "mateo",
    "michael",
    "noah",
    "rowan",
    "sam",
    "thomas",
    "tom",
    "victor",
}
COUNCIL_FEMININE_VOICES = ("marin", "shimmer", "sage")
COUNCIL_MASCULINE_VOICES = ("cedar", "ash", "verse")


class OrchestratorBeat(BaseModel):
    line: str
    image_prompt: str


class OrchestratorStageOutput(BaseModel):
    year_label: str = ""
    title: str = ""
    montage_logline: str = ""
    world_brief: str = ""
    macro_stats: dict[str, MacroStatistic] = Field(default_factory=dict)
    narrative_beats: list[OrchestratorBeat] = Field(default_factory=list)


class OrchestratorFeaturetteOutput(BaseModel):
    subject: str = Field(min_length=2)
    question: str = Field(min_length=12)
    title: str = Field(min_length=4)
    logline: str = Field(min_length=16)
    narrative_beats: list[OrchestratorBeat] = Field(default_factory=list, min_length=3, max_length=4)


class OrchestratorFeaturetteSetOutput(BaseModel):
    featurettes: list[OrchestratorFeaturetteOutput] = Field(default_factory=list, min_length=3, max_length=3)


class DebateOutput(BaseModel):
    opponent_opening: str
    opponent_rebuttal: str
    analyst_take: str


class DebateImpactOutput(BaseModel):
    player_vote_shift: float = Field(ge=-0.08, le=0.08)
    rationale: str
    player_reaction: str
    opponent_reaction: str


class CouncilRosterOutput(BaseModel):
    council_roster: list[CouncilAdvisorProfile] = Field(default_factory=list, min_length=3, max_length=5)


class OrchestratorService:
    def __init__(self, settings: AppSettings, gateway: OpenAIGateway):
        self.settings = settings
        self.gateway = gateway

    def _future_year_signal(self, text: str | None) -> int | None:
        lowered = " ".join(str(text or "").split()).lower()
        if not lowered:
            return None
        word_years = {
            "four": 4,
            "five": 5,
            "six": 6,
            "seven": 7,
            "eight": 8,
            "nine": 9,
            "ten": 10,
            "eleven": 11,
            "twelve": 12,
            "thirteen": 13,
            "fourteen": 14,
            "fifteen": 15,
            "sixteen": 16,
            "seventeen": 17,
            "eighteen": 18,
            "nineteen": 19,
            "twenty": 20,
        }
        word_year_pattern = "|".join(word_years)
        numeric_match = re.search(
            r"\b(?:in\s+)?(\d{1,2})(?:\s*(?:-|to)\s*(\d{1,2}))?\s+years?\s+(?:from now|ahead|in the future|after|out|later|down the line|down the road)\b|\bin\s+(\d{1,2})\s+years?\b",
            lowered,
        )
        if numeric_match:
            upper = numeric_match.group(3) or numeric_match.group(2) or numeric_match.group(1)
            return int(upper)
        word_match = re.search(
            rf"\b(?:in\s+)?({word_year_pattern})"
            rf"(?:\s*(?:-|to)\s*({word_year_pattern}))?"
            r"\s+years?\s+(?:from now|ahead|in the future|after|out|later|down the line|down the road)\b"
            rf"|\bin\s+({word_year_pattern})\s+years?\b",
            lowered,
        )
        if word_match:
            upper = word_match.group(3) or word_match.group(2) or word_match.group(1)
            return word_years.get(upper)
        return None

    def _advanced_capability_signal(self, text: str | None) -> bool:
        lowered = " ".join(str(text or "").split()).lower()
        if self._deep_digital_automation_signal(lowered):
            return True
        return bool(
            lowered
            and any(
                cue in lowered
                for cue in (
                    "ai can do most computer work",
                    "ai can do most remote work",
                    "ai can do most screen work",
                    "most computer work",
                    "most remote work",
                    "all remote computer work",
                    "all things on a computer",
                    "everything that can be done on a computer",
                    "fully automate cognitive work",
                    "cognitive labor",
                    "cognitive labour",
                    "substrate of the economy",
                    "substrate for the economy",
                    "machine labor runs",
                    "machine labour runs",
                )
            )
        )

    def _deep_digital_automation_signal(self, text: str | None) -> bool:
        lowered = " ".join(str(text or "").split()).lower()
        if re.search(
            r"\b(?:most|all|nearly all|almost all|a lot of)\s+(?:of\s+)?(?:what\s+)?(?:remote|knowledge|screen|computer|laptop|cognitive)\s+(?:workers\s+)?(?:do|work|labor|labour|tasks|jobs)\b",
            lowered,
        ):
            return True
        if re.search(
            r"\b(?:remote|knowledge|screen|computer|laptop|cognitive)\s+(?:work|tasks|labor|labour)\s+(?:is|are|has been|have been|gets?|can be)\s+(?:mostly\s+|largely\s+|broadly\s+|fully\s+)?automated\b",
            lowered,
        ):
            return True
        if re.search(
            r"\b(?:agents?|ai|machine systems?)\s+(?:can|could|now|already)?\s*(?:run|do|handle|automate)\s+(?:most|all|nearly all|almost all|a lot)\s+(?:of\s+)?(?:the\s+)?(?:tasks?|work|things|jobs)\s+(?:that\s+)?(?:happen|happens|live|lives|are done|is done)\s+on\s+(?:a\s+)?(?:laptop|screen|computer)\b",
            lowered,
        ):
            return True
        if re.search(r"\b(?:normal|old|standard)\s+(?:job|work)\s+week\s+(?:is|has become|stopped being|is no longer)\s+(?:no longer\s+)?(?:central|the center|organizing|normal)\b", lowered):
            return True
        return False

    def _text_wants_later_world(self, text: str | None) -> bool:
        lowered = " ".join(str(text or "").split()).lower()
        if not lowered:
            return False
        if (years := self._future_year_signal(lowered)) is not None:
            return years >= 6 or (years >= 4 and self._advanced_capability_signal(lowered))
        if self._advanced_capability_signal(lowered):
            return True
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
        setup_text = " ".join(
            part.strip()
            for part in (
                str(config.premise or ""),
                str(config.stakes or ""),
                str(config.topic_lens or ""),
                str(config.region_focus or ""),
            )
            if str(part).strip()
        )
        if requested == "high":
            return "high"
        if requested == "medium":
            return requested
        if self._text_wants_later_world(setup_text):
            return "medium"
        if setup_text:
            return "medium"
        return "low"

    def _setup_reasoning_effort(self, config: SimulationConfig, user_text: str) -> str:
        if self._text_wants_later_world(user_text):
            return "medium"
        return self._content_reasoning_effort(config)

    async def build_council_roster(self, config: SimulationConfig) -> list[CouncilAdvisorProfile]:
        if self.settings.dummy_openai:
            return self._dummy_council_roster(config)

        prompt = (
            f"Country or jurisdiction: {config.country}\n"
            f"Player role: {config.player_role}\n"
            f"Opponent role: {config.opponent_role}\n"
            f"Population frame: {config.population_description}\n"
            f"Region focus: {config.region_focus or 'broad coverage'}\n"
            f"Topic lens: {config.topic_lens or 'broad AGI transition'}\n"
            f"Premise: {config.premise or 'no special premise locked'}\n"
            f"Political stakes: {config.stakes or 'no special stake locked'}\n"
            f"Stage count: {config.stage_count}\n"
            f"Natural starting point: {self._natural_start_point_note(config)}\n"
            f"Story memo: {self._setup_story_memo(config)}\n"
        )
        parsed, _ = await self.gateway.parse(
            model=self.settings.orchestrator_model,
            instructions=self._council_roster_instructions(),
            input_text=prompt,
            text_format=CouncilRosterOutput,
            reasoning_effort=config.orchestrator_reasoning_effort,
            prompt_cache_key=f"council-roster:{config.country.lower().replace(' ', '-')}",
            max_output_tokens=1100,
            verbosity="low",
        )
        normalized = self._normalize_council_roster(parsed.council_roster)
        return normalized or self._dummy_council_roster(config)

    def _phase_anchor_from_text(self, text: str | None) -> int:
        lowered = " ".join(str(text or "").split()).lower()
        if not lowered:
            return 0
        if (years := self._future_year_signal(lowered)) is not None:
            if years >= 14:
                year_anchor = 4
            elif years >= 10:
                year_anchor = 3
            elif years >= 6:
                year_anchor = 2
            elif years >= 4:
                year_anchor = 1
            else:
                year_anchor = 0
            if self._deep_digital_automation_signal(lowered):
                return max(year_anchor, 3)
            if self._advanced_capability_signal(lowered):
                return max(year_anchor, 2)
            return year_anchor
        if self._deep_digital_automation_signal(lowered):
            return 3
        if self._advanced_capability_signal(lowered):
            return 2
        if any(
            cue in lowered
            for cue in (
                "radical future",
                "radical agi future",
                "radical ai future",
                "well after the transition",
                "after the transition",
                "new settlement",
                "changed settlement",
                "agi settlement",
                "substrate of the economy",
                "fully automate cognitive work",
                "all things on a computer",
                "everything that can be done on a computer",
                "already reorganized everyday life",
                "already lives inside",
                "already live inside",
                "old job order",
                "old labor order",
                "no longer organize life around a normal job week",
                "no longer organize life around the old job week",
                "machine checks",
                "monthly machine check",
                "public ai help line",
                "public ai systems",
                "rival compute blocs",
                "compute blocs shape geopolitics",
                "structurally remade",
                "different civilization",
            )
        ):
            return 4
        if any(
            cue in lowered
            for cue in (
                "near agi",
                "near-agi",
                "agi power contest",
                "hugely different economy",
                "much stranger agi society",
                "stranger agi society",
                "deeply transformed economy",
                "deeply transformed world",
                "transformed agi society",
                "robotics-heavy future",
                "several chapters into the agi transition",
                "several chapters into the transition",
                "run much routine remote work",
                "most routine remote work",
                "routine remote work and public-service coordination",
                "normal job week",
            )
        ):
            return 3
        if any(
            cue in lowered
            for cue in (
                "skip ahead",
                "jump ahead",
                "start later",
                "later in the transition",
                "more advanced ai",
                "advanced ai future",
                "advanced ai world",
                "embodied rollout",
                "physical rollout",
                "robotics starts to enter",
                "robotics is visible",
                "visible in logistics and industrial corridors",
                "digital agents are powerful enough",
                "public-service coordination",
                "different economy",
                "different world",
            )
        ):
            return 2
        stage_match = re.search(r"\bstage\s+([2-9])\b", lowered)
        if stage_match:
            stage_number = int(stage_match.group(1))
            return min(4, max(1, stage_number - 1))
        return 1 if self._text_wants_later_world(lowered) else 0

    def _starting_phase_anchor(self, *texts: str | None) -> int:
        return max((self._phase_anchor_from_text(text) for text in texts), default=0)

    def _setup_implies_later_settlement(self, config: SimulationConfig) -> bool:
        return self._starting_phase_anchor(
            config.premise,
            config.topic_lens,
            config.stakes,
            config.population_description,
        ) > 0 or self._text_wants_later_world(
            " ".join(
                part.strip()
                for part in (
                    str(config.premise or ""),
                    str(config.stakes or ""),
                    str(config.topic_lens or ""),
                    str(config.population_description or ""),
                )
                if str(part).strip()
            )
        )

    def _macro_cue_line(self, *, later_world_requested: bool) -> str:
        return (
            "- macro_stats: a small JSON object of 3 to 6 flexible dashboard facts that make this particular world legible. "
            "Choose the stats to fit the run, not a fixed template. For a national economy, this will usually include rough unemployment, labor force participation, inflation, real GDP growth, and one capability, compute, defense, infrastructure, or household-access stat. "
            "For a school board or ministry, choose education stats instead. Each stat should have label, value, and a short plain-English detail. Avoid false precision.\n"
        )

    def _clean_macro_stats(
        self,
        macro_stats: dict[str, MacroStatistic] | None,
        *,
        config: SimulationConfig,
        stage_index: int,
    ) -> dict[str, MacroStatistic]:
        cleaned: dict[str, MacroStatistic] = {}
        for raw_key, stat in (macro_stats or {}).items():
            key = re.sub(r"[^a-z0-9_]+", "_", str(raw_key or "").strip().lower()).strip("_")
            label = " ".join(str(stat.label or "").split())
            value = " ".join(str(stat.value or "").split())
            detail = " ".join(str(stat.detail or "").split())
            if not key:
                key = re.sub(r"[^a-z0-9_]+", "_", label.lower()).strip("_")
            if not key or not label or not value:
                continue
            cleaned[key[:42]] = MacroStatistic(
                label=self._trim_without_ellipsis(label, 44),
                value=self._trim_without_ellipsis(value, 24),
                detail=self._clip(detail, 92) if detail else "",
            )
            if len(cleaned) >= 6:
                break
        if cleaned:
            return cleaned
        return self._fallback_macro_stats(config=config, stage_index=stage_index)

    def _fallback_macro_stats(self, *, config: SimulationConfig, stage_index: int) -> dict[str, MacroStatistic]:
        lens = " ".join(
            part.lower()
            for part in (
                config.country,
                config.region_focus,
                config.topic_lens,
                config.population_description,
                config.premise,
            )
            if part
        )
        if any(term in lens for term in ("school", "student", "education", "classroom", "teacher", "university")):
            return {
                "learning_gap": MacroStatistic(label="Learning gap", value="narrowing", detail="AI tutoring spreads unevenly by district access."),
                "teacher_time": MacroStatistic(label="Teacher time", value="+8%", detail="More hours shift from grading to coaching and classroom judgment."),
                "screen_time": MacroStatistic(label="AI screen time", value="3.4 h/day", detail="Families argue over helpful practice versus dependency."),
                "public_trust": MacroStatistic(label="Parent trust", value="mixed", detail="Approval turns on transparency and child safety."),
            }
        growth = 2.8 + stage_index * 0.7
        unemployment = max(3.2, 4.8 + stage_index * 0.5)
        participation = max(59.0, 62.4 - stage_index * 0.4)
        inflation = max(1.6, 3.2 - stage_index * 0.25)
        return {
            "unemployment": MacroStatistic(label="Unemployment", value=f"{unemployment:.1f}%", detail="Screen-work churn is visible before new income forms settle."),
            "labor_force_participation": MacroStatistic(label="Labor force", value=f"{participation:.1f}%", detail="Some adults leave old ladders for care, study, ventures, or waiting."),
            "real_gdp_growth": MacroStatistic(label="Real GDP", value=f"+{growth:.1f}%", detail="Cheap digital labor lifts output, but physical bottlenecks still bite."),
            "inflation": MacroStatistic(label="Inflation", value=f"{inflation:.1f}%", detail="Services ease while power, housing, and hardware stay tight."),
        }

    async def compose_stage(
        self,
        *,
        state: SimulationState,
        previous_stage: StagePackage | None,
        tracking: StageTracking | None,
        poll_summaries: list[PollSummary],
        queued_poll_questions: list[str],
        progress_callback: Callable[[str, str, int], Awaitable[None]] | None = None,
    ) -> StagePackage:
        if self.settings.dummy_openai:
            return self._dummy_stage(state, previous_stage, tracking, poll_summaries, queued_poll_questions)

        phase = self._phase_brief(
            state.active_stage_index,
            state.config.stage_count,
            self._starting_phase_anchor(
                state.config.premise,
                state.config.topic_lens,
                state.config.stakes,
            ),
        )
        if progress_callback:
            await progress_callback(
                "Writing the next world state",
                "Writing one coherent chapter about how capability, institutions, households, and politics now fit together.",
                36,
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
        )
        parsed, response_id = await self.gateway.parse(
            model=self.settings.orchestrator_model,
            instructions=self._stage_instructions(state.config),
            input_text=prompt,
            text_format=OrchestratorStageOutput,
            reasoning_effort=self._content_reasoning_effort(state.config),
            previous_response_id=None,
            prompt_cache_key=f"{state.simulation_id}:orchestrator-stage-v4",
            max_output_tokens=1800,
            verbosity="low",
            max_attempts=1,
        )
        if progress_callback:
            await progress_callback(
                "Preparing the documentary opening",
                "Reusing the same chapter draft for the reel, briefing, and room context.",
                50,
            )
        world_brief = self._normalize_summary_prose(parsed.world_brief, max_paragraphs=5)
        stage_package = StagePackage(
            index=state.active_stage_index,
            phase_label=phase["label"],
            year_label=parsed.year_label or self._fallback_year_label(previous_stage, state.active_stage_index),
            title=parsed.title or self._fallback_stage_title(phase["label"], state.active_stage_index),
            montage_logline=self._normalize_sentence(
                parsed.montage_logline or self._fallback_montage_logline(world_brief),
                max_words=28,
                max_chars=196,
            ),
            world_brief=world_brief,
            room_briefing="",
            narrative_beats=[
                NarrativeBeat(
                    line=self._normalize_narration_line(beat.line),
                    image_prompt=self._polish_image_prompt(state.config.visual_style, beat.image_prompt),
                )
                for beat in parsed.narrative_beats
            ],
            sample_citizens=[],
            tracking=tracking or self._neutral_tracking(),
            macro_stats=self._clean_macro_stats(
                parsed.macro_stats,
                config=state.config,
                stage_index=state.active_stage_index,
            ),
            poll_summaries=poll_summaries,
            queued_poll_questions=queued_poll_questions,
            policy_notes=[],
            orchestrator_response_id=response_id,
        )
        stage_package.room_briefing = self._resolve_room_briefing(
            drafted_room_briefing=None,
            world_brief=stage_package.world_brief,
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
            f"- council_roster: {[advisor.model_dump() for advisor in config.council_roster]}\n"
            f"Setup direction from the player:\n{setup_direction}\n"
            "Treat the setup conversation as the flexible input surface; the user can steer country, state, institution, and future horizon in plain language. "
            "Do not look for a separate world-mode toggle.\n"
            f"- persona_count: {config.persona_count}\n"
            f"- stage_count: {config.stage_count}\n"
            f"- visual_style: {config.visual_style}\n"
            f"- orchestrator_reasoning_effort: {config.orchestrator_reasoning_effort}\n"
            f"- realtime_model: {config.realtime_model}\n\n"
            f"Recent setup turns:\n{turn_block}\n\n"
            f"Latest user message:\n{user_text}\n\n"
            "Return a short chamber reply in a conductor-like tone. "
            "If you changed fields, speak naturally in one short sentence; do not expose internal field names or field -> value fragments in chamber_reply. "
            "Only include config_updates for changes the user actually requested. "
            "If the user asked to tighten, rewrite, or restyle an existing field, put the rewritten field value directly into config_updates instead of talking around it. "
            "If the user asks to change the advisor panel, return a complete council_roster array rather than a partial note. "
            "Each council_roster entry should include key, name, room_role, country_role, remit, voice, and viewpoint. "
            "Set readiness to ready when the draft is launchable as-is, and needs_input only when a requested change is blocked by a missing detail. "
            "If you apply any changes, mirror them in applied_updates using field -> value form for structured bookkeeping only. "
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
        heuristic_country = heuristic_updates.country
        explicit_population = self._extract_labeled_value(user_text, "population")
        if heuristic_country:
            merged_updates["country"] = heuristic_country
            if "population_description" in merged_updates and not explicit_population:
                population_candidate = " ".join(str(merged_updates["population_description"] or "").split()).lower()
                if heuristic_country.lower() not in population_candidate:
                    merged_updates.pop("population_description", None)
        resolved_phase_anchor = self._starting_phase_anchor(
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
        if "visual_style" in merged_updates and self._visual_style_update_is_negated_comparison(
            user_text,
            str(merged_updates.get("visual_style") or ""),
        ):
            merged_updates.pop("visual_style", None)
            removed_auto_fields.append("visual_style")
        if merged_updates:
            parsed.config_updates = SetupSessionPatchRequest(**merged_updates)
            parsed.applied_updates = [f"{field} -> {value}" for field, value in merged_updates.items()]
            if (
                removed_auto_fields
                or "->" in parsed.chamber_reply
                or any(field in " ".join(parsed.chamber_reply.split()) for field in merged_updates.keys())
            ):
                parsed.chamber_reply = self._setup_chamber_reply_for_updates(merged_updates, resolved_phase_anchor)
        return parsed

    async def materialize_stage_media(self, *, stage: StagePackage, asset_dir: Path) -> None:
        await self._materialize_narrative_media(stage.narrative_beats, asset_dir=asset_dir, prefix="beat")

    def _setup_chamber_reply_for_updates(self, updates: dict[str, object], phase_anchor: int) -> str:
        if not updates:
            return "The broad default setup still holds. Say start when you want to begin."
        if phase_anchor >= 3 and "premise" in updates:
            return "We will open inside a more changed AI society, not a warmed-over version of today. Say start when you want to begin."
        if phase_anchor >= 1 and "premise" in updates:
            return "We will begin later in the transition and let that shape the opening world. Say start when you want to begin."
        if "country" in updates:
            country = str(updates.get("country") or "that jurisdiction").strip()
            return f"We will stage the run around {country} and keep the rest broad unless you narrow it. Say start when you want to begin."
        if "population_description" in updates:
            return "I will build the sample around that population and keep the scenario flexible. Say start when you want to begin."
        if "council_roster" in updates:
            return "I will reshape the advisory table around that cast. Say start when you want to begin."
        if "visual_style" in updates:
            return "I will carry that visual treatment into the documentary and rooms. Say start when you want to begin."
        if "persona_count" in updates or "stage_count" in updates:
            return "I adjusted the run size for this playtest. Say start when you want to begin."
        return "The setup now reflects that nudge. Say start when you want to begin."

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
            if self.settings.dummy_openai:
                image_suffix = "svg"
            else:
                output_format = (self.settings.image_output_format or "png").lower()
                image_suffix = "jpg" if output_format in {"jpeg", "jpg"} else output_format
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
            f"World summary: {current_stage.world_brief}\n"
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
            f"One gain the player would slow: {self._stage_upside_hint(current_stage.world_brief) or 'a real gain voters already notice'}\n"
            f"One constituency wanting more AI: {self._stage_constituency_hint(current_stage.world_brief) or 'people already benefiting from faster adoption'}\n"
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
                "Steelman the player's case before you oppose it: say the strongest version of their concern in plain words, then answer it. "
                "Do not echo the player's remedies unless you are explicitly narrowing, replacing, or rejecting them. "
                "Make the sharpest credible contrast for this stage and electorate. That contrast may turn on pace, concentration, public provision, competition, household payoff, bargaining power, resilience, or legitimacy. "
                "If the player proposes a broad brake, one plausible contrast is narrower rules, more competition, or faster diffusion, but only if the stage evidence supports that case. "
                "If the player proposes speed-first diffusion, one plausible contrast is visible household payoff, bargaining leverage, or public recourse, but only if that is the live pressure. "
                "If the player is restrictive, make the strongest case for useful capability, open access, or faster diffusion that their line would slow, and then explain the smallest guardrail that would still protect the public. "
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
            f"State of the world: {current_stage.world_brief}\n"
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
            "The setup conversation itself is the flexible input: use it to shape country, state, institution, population, and future horizon in plain language. "
            "Keep replies brief, grounded, and a little theatrical, like a conductor taking a few final cues before the first note. "
            "Most setup replies should be 1 or 2 short sentences, not a block of exposition. "
            "If the user asks how the experience works, answer like a useful tutorial line, not a pitch. Give one practical line about what the player will actually do next. If you include a tip, keep it concrete enough to say out loud once. "
            "The default run is broad and representative: a national U.S. simulation without a narrow regional or thematic bias unless the player explicitly asks for one. "
            "Treat player and opponent roles, country, and population as editable setup fields. "
            "Treat the advisor roster as editable too. "
            "Treat region focus, topic lens, premise, and stakes as optional steering fields; do not insist that they be filled. "
            "Treat persona_count, stage_count, and visual_style as editable setup fields too. "
            "Treat natural-language setup requests as real edits, not vague inspiration. "
            "If the user says something like 'make this a Finland education-policy run focused on students and teachers' or 'set this in a Texas state agency or a municipal hospital', "
            "translate that into concrete config_updates for country, region_focus, topic_lens, population_description, and any other strongly implied fields. "
            "If the user asks for different advisors, more or fewer seats, different specialties, or different names and viewpoints, translate that into a full council_roster update with key, name, room_role, country_role, remit, voice, and viewpoint for each advisor. "
            "If the user asks for a different art style, look, aesthetic, painterly direction, or documentary treatment, write that into visual_style directly. "
            "Do not treat negative content comparisons like 'do not make it feel like 2026 with better chatbots' as visual_style edits. Those belong in premise or stage guidance. "
            "If the user asks to skip ahead, begin later in the transition, start ten years from now, or open inside a stranger future economy, preserve that natural-language request in premise and also infer the starting point internally. "
            "Do not mistake a future-setting sentence for a topic lens. A sentence about how strange the world should be belongs in premise unless the user also named a concrete domain, institution, or service area like health care, housing, schools, or city government. "
            "If the user asks for a certain number of agents, personas, citizens, or people in the sample, update persona_count directly. "
            "When jurisdiction or country changes, also rewrite player and opponent roles so the offices make sense in that place unless the user explicitly overrides them. "
            "If no narrow region_focus, topic_lens, premise, or stakes were requested, leave those fields broad or empty rather than inventing a special frame. "
            "Do not invent a separate world-mode toggle or mode field; carry the natural-language setup through directly. "
            "Apply straightforward requested edits instead of restating them. "
            "If you made edits, briefly confirm the one or two biggest changes in natural speech; do not say field -> value fragments out loud. "
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

    def _council_roster_instructions(self) -> str:
        voice_guide = (
            "cedar = steady lower male voice; "
            "marin = friendly woman; "
            "ash = gravelly man; "
            "shimmer = deeper steady woman; "
            "sage = gentle mid-pitch woman; "
            "verse = lighter younger male voice"
        )
        return (
            "You are designing the advisory table for an AGI transition political simulation. "
            "Return a complete council_roster array with 3 to 5 advisors. Four is the normal default unless the setting clearly wants fewer or more. "
            "This is a private working table, not a television panel. The cast should feel like real people the player would actually want in the room for this specific country, institution, and future. "
            "Use the setup context to decide the specialties, names, and points of view. Do not fall back to the same stock U.S. presidential quartet unless the context genuinely calls for it. "
            "If the setting is a default U.S. run, keep the roster grounded in that context; if the player points to another country, state, city, ministry, school system, university, or hospital, let that setting shape the cast. "
            "If the run is national and broad, include a believable mix across economic distribution, capability or innovation, coalition or political reading, and state capacity or security. "
            "If the run is narrower, like a school ministry, state government, city, or later machine-heavy settlement, adapt the roster so the specialties actually fit that setting. "
            "When the future is later or stranger, at least one advisor should understand the settlement directly: income flows, access rules, ownership, public systems, time use, dependency, or geopolitical order in that world. "
            "At least one advisor should clearly defend useful diffusion, abundance, or not breaking what is working. At least one should see risk, execution limits, or concentrated capture. "
            "Avoid making everyone pro-regulation, everyone anti-regulation, or everyone the same style of wonk. "
            "Each advisor must include key, name, room_role, country_role, remit, voice, and viewpoint. "
            "Name should usually be one short spoken first name or a very short full name that is easy to hear and repeat aloud. "
            "room_role should be short, like Economy, Learning, Security, Families, Industry, or Politics. "
            "country_role should sound like a believable job title for the setting. "
            "remit should explain what that person actually watches and why they matter. "
            "viewpoint should make their default instinct legible in one sentence without turning them into a cartoon ideologue. "
            f"Use only these voices: {', '.join(COUNCIL_VOICE_POOL)}. Reuse voices only if needed because there are more advisors than voices. "
            f"Voice guide: {voice_guide}. "
            "Pick a voice that feels naturally consistent with the advisor's name, age vibe, and public presence so the cast does not sound jarringly mismatched. "
            "Keep keys lowercase slug-like and unique. Keep names unique. Keep remits materially distinct. "
            "Do not add any prose outside the JSON."
        )

    def _setup_story_memo(self, config: SimulationConfig) -> str:
        parts: list[str] = []
        if str(config.region_focus or "").strip():
            parts.append(f"Regional emphasis: {config.region_focus}.")
        if str(config.topic_lens or "").strip():
            parts.append(f"Topic emphasis: {config.topic_lens}.")
        if str(config.premise or "").strip():
            parts.append(f"Player premise: {config.premise} Treat that as a live creative prior, not background flavor.")
        if str(config.stakes or "").strip():
            parts.append(f"Political stakes: {config.stakes}.")
        if getattr(config, "council_roster", None):
            roster_summary = "; ".join(
                f"{advisor.name} on {advisor.room_role.lower()} ({advisor.country_role})"
                for advisor in config.council_roster[:6]
            )
            if roster_summary:
                parts.append(f"Advisory cast in frame: {roster_summary}.")
        if not parts:
            parts.append("The setup stays broad and national unless the player narrows it.")
        return " ".join(parts)

    def _normalize_council_roster(self, roster: list[CouncilAdvisorProfile]) -> list[CouncilAdvisorProfile]:
        normalized: list[CouncilAdvisorProfile] = []
        used_keys: set[str] = set()
        used_names: set[str] = set()
        used_voices: set[str] = set()

        def voice_group(voice: str) -> str | None:
            if voice in COUNCIL_FEMININE_VOICES:
                return "f"
            if voice in COUNCIL_MASCULINE_VOICES:
                return "m"
            return None

        def name_voice_hint(name: str) -> str | None:
            first_name = re.sub(r"[^a-z]+", "", name.lower().split()[0])
            if first_name in COUNCIL_FEMININE_HINTS:
                return "f"
            if first_name in COUNCIL_MASCULINE_HINTS:
                return "m"
            return None

        def pick_voice(preferred_group: str | None, requested_voice: str, index: int) -> str:
            pool = COUNCIL_FEMININE_VOICES if preferred_group == "f" else COUNCIL_MASCULINE_VOICES if preferred_group == "m" else COUNCIL_VOICE_POOL
            if requested_voice in pool and requested_voice not in used_voices:
                return requested_voice
            available = [voice for voice in pool if voice not in used_voices]
            if available:
                return available[index % len(available)]
            if requested_voice in pool:
                return requested_voice
            return pool[index % len(pool)]

        for index, advisor in enumerate(roster):
            name = " ".join(str(advisor.name or "").split()).strip()
            room_role = " ".join(str(advisor.room_role or "").split()).strip()
            country_role = " ".join(str(advisor.country_role or "").split()).strip()
            remit = " ".join(str(advisor.remit or "").split()).strip()
            viewpoint = " ".join(str(advisor.viewpoint or "").split()).strip()
            if not all((name, room_role, country_role, remit)):
                continue
            normalized_name = name.lower()
            if normalized_name in used_names:
                continue
            key_seed = str(advisor.key or name).strip().lower()
            key = re.sub(r"[^a-z0-9]+", "_", key_seed).strip("_") or f"advisor_{index + 1}"
            original_key = key
            suffix = 2
            while key in used_keys:
                key = f"{original_key}_{suffix}"
                suffix += 1
            voice = str(advisor.voice or "").strip().lower()
            if voice not in COUNCIL_VOICE_POOL:
                voice = COUNCIL_VOICE_POOL[index % len(COUNCIL_VOICE_POOL)]
            preferred_group = name_voice_hint(name)
            if preferred_group and voice_group(voice) != preferred_group:
                voice = pick_voice(preferred_group, voice, index)
            elif voice in used_voices:
                voice = pick_voice(preferred_group, voice, index)
            normalized.append(
                CouncilAdvisorProfile(
                    key=key,
                    name=name,
                    room_role=room_role,
                    country_role=country_role,
                    remit=remit,
                    voice=voice,
                    viewpoint=viewpoint,
                )
            )
            used_keys.add(key)
            used_names.add(normalized_name)
            used_voices.add(voice)
        return normalized[:5]

    def _dummy_council_roster(self, config: SimulationConfig) -> list[CouncilAdvisorProfile]:
        anchor = " ".join(
            part.strip().lower()
            for part in (
                config.country,
                config.topic_lens or "",
                config.premise or "",
                config.stakes or "",
            )
            if part and part.strip()
        )
        phase_anchor = self._starting_phase_anchor(config.premise, config.topic_lens, config.stakes)
        education_mode = any(token in anchor for token in ("school", "education", "student", "teacher", "learning"))
        local_state_mode = any(token in anchor for token in ("state", "governor", "city", "municipal", "local"))
        names = self._dummy_council_names(config)
        if education_mode:
            seed = [
                ("learning", names[0], "Learning", "learning systems advisor", "tracks classroom adoption, tutoring quality, teacher workload, and what students or families actually feel first", "pushes for useful tools when they clearly widen access or lighten teacher load"),
                ("families", names[1], "Families", "family and equity advisor", "tracks who gets help at home, who falls behind, what parents trust, and where machine help changes homework, discipline, or child care", "cares most about whether ordinary families can actually use the new tools without being sorted by money or time"),
                ("operations", names[2], "Operations", "school operations and finance advisor", "tracks procurement, staffing, device access, transport, and which promises can really be delivered across uneven schools", "starts from what the system can actually execute this year rather than what sounds inspiring"),
                ("politics", names[3], "Politics", "public mandate advisor", "tracks coalition mood, legitimacy, union pressure, parent trust, and which lines the player can defend in public", "likes visible gains but punishes anything that sounds fake, rushed, or unfair"),
            ]
        elif local_state_mode:
            seed = [
                ("economy", names[0], "Economy", "regional economic advisor", "tracks local firms, prices, power bills, permitting, labor demand, and whether small operators can actually feel new machine capacity", "leans toward diffusion when it lowers costs and widens local room to compete"),
                ("services", names[1], "Services", "public service delivery advisor", "tracks schools, care, permits, benefits, dispatch, and whether citizens see the state as more capable or just more automated", "backs practical upgrades but distrusts brittle rollouts and fake savings"),
                ("infrastructure", names[2], "Infrastructure", "infrastructure and resilience advisor", "tracks grid, water, logistics, housing, disaster readiness, and the physical bottlenecks software cannot wish away", "pushes buildout and redundancy before elegant policy promises"),
                ("politics", names[3], "Politics", "political strategy advisor", "tracks coalition mood, legitimacy, regional identity, and how ordinary people read machine gains against local winners and losers", "cares about whether the story sounds fair, concrete, and locally believable"),
            ]
        elif phase_anchor >= 3:
            seed = [
                ("settlement", names[0], "Settlement", "social settlement advisor", "tracks how households now get income, access, care, and bargaining power inside the new machine-heavy order", "protects any arrangement that leaves ordinary people with real room to refuse bad terms"),
                ("capacity", names[1], "Capacity", "productive systems advisor", "tracks compute, power, robotics rollout, industrial expansion, and where faster buildout still creates visible gains people would fight to keep", "leans pro-diffusion when abundance is real and broadly felt"),
                ("state", names[2], "State", "state capacity and security advisor", "tracks procurement, strategic dependence, allied supply, coercion risk, and which institutions can still secure essential systems in the altered settlement", "starts from resilience and execution but will favor openness when it makes the polity stronger"),
                ("politics", names[3], "Politics", "political coalition advisor", "tracks legitimacy, class settlement, resentment, and how voters read the new order when daily life no longer resembles the old labor market", "wants a settlement people can narrate as fair, durable, and worth defending"),
            ]
        else:
            seed = [
                ("economy", names[0], "Economy", "economic and distribution advisor", "tracks prices, household purchasing power, business formation, machine-income flows, and which gains households would hate to lose", "leans pro-diffusion when the gains are broad and tangible, but worries about concentrated capture"),
                ("innovation", names[1], "Innovation", "innovation and frontier systems advisor", "tracks research speed, capability diffusion, tooling access, robotics rollout, and whether smaller firms and public institutions can actually use the frontier", "pushes capability forward first but dislikes chokepoints and cartelized access"),
                ("politics", names[2], "Politics", "political strategy advisor", "tracks coalition mood, public tolerance for change, rhetorical traps, and which lines the player can defend in public without sounding evasive or bloodless", "sharp on voter mood and willing to defend speed when people can see the gain"),
                ("state", names[3], "Security", "state capacity and national resilience advisor", "tracks infrastructure, strategic dependence, resilience, war risk, and what the state can truly procure, secure, or defend in practice", "starts from resilience and execution, then asks what openness makes the country stronger"),
            ]
        roster = [
            CouncilAdvisorProfile(
                key=key,
                name=name,
                room_role=room_role,
                country_role=country_role,
                remit=remit,
                voice=COUNCIL_VOICE_POOL[index % len(COUNCIL_VOICE_POOL)],
                viewpoint=viewpoint,
            )
            for index, (key, name, room_role, country_role, remit, viewpoint) in enumerate(seed)
        ]
        return self._normalize_council_roster(roster)

    def _dummy_council_names(self, config: SimulationConfig) -> list[str]:
        pools = [
            ["Mira", "Jonas", "Sana", "Elio", "Talia", "Ruben"],
            ["Noor", "Mateo", "Iris", "Felix", "Leena", "Omar"],
            ["Ana", "Diego", "Lucia", "Rafael", "Ines", "Tomas"],
            ["Lina", "Arun", "Clara", "Niko", "Sofia", "Yara"],
        ]
        signature = f"{config.country}|{config.topic_lens}|{config.premise}|{config.stakes}"
        offset = sum(ord(char) for char in signature) % len(pools)
        return pools[offset]

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
            reply = self._setup_chamber_reply_for_updates(
                update_payload,
                self._starting_phase_anchor(
                    user_text,
                    str(update_payload.get("premise") or ""),
                    str(update_payload.get("topic_lens") or ""),
                    str(update_payload.get("stakes") or ""),
                ),
            )
        elif any(cue in normalized for cue in launch_cues):
            reply = "The broad default run still holds. It is ready to launch now."
        else:
            reply = "The broad default run still holds."
        if readiness == "ready":
            if not reply.endswith("Say start when you want to begin."):
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
        if region_focus is None:
            region_focus = self._infer_explicit_region_focus(text, focus_phrase)
        visual_style = self._extract_visual_style_request(text)
        future_setup_brief = self._extract_future_setup_brief(text)
        focus_phrase_is_topic = bool(focus_phrase and self._focus_phrase_is_narrow_topic(focus_phrase))
        if focus_phrase_is_topic and topic_lens is None:
            topic_lens = focus_phrase
        if premise is None and future_setup_brief:
            premise = future_setup_brief
        if focus_phrase_is_topic and population is None and country:
            population = (
                f"A representative sample of people in {country} whose lives are directly shaped by {focus_phrase}, "
                "with realistic variation across age, income, ideology, institutional role, geography, and AI exposure."
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
            "ai can do most computer work",
            "ai can do most remote work",
            "most computer work",
            "most remote work",
            "all things on a computer",
            "everything that can be done on a computer",
            "fully automate cognitive work",
            "cognitive labor",
            "cognitive labour",
            "substrate of the economy",
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
            if any(cue in lowered for cue in cue_phrases) or self._advanced_capability_signal(lowered) or re.search(
                r"\b(?:in\s+)?(?:\d{1,2}|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty)"
                r"(?:\s*(?:-|to)\s*(?:\d{1,2}|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty))?"
                r"\s+years?\s+(?:from now|ahead|in the future|after|out|later|down the line|down the road)\b"
                r"|\bin\s+(?:\d{1,2}|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty)\s+years?\b",
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

    def _infer_explicit_region_focus(self, text: str, focus_phrase: str | None) -> str | None:
        lowered = " ".join(text.lower().split())
        focus_lower = " ".join((focus_phrase or "").lower().split())
        education_scope = any(
            cue in lowered or cue in focus_lower
            for cue in ("education", "school", "schools", "students", "teachers", "school administration")
        )
        if "municipalities" in lowered or "municipal" in lowered:
            return "municipal school systems" if education_scope else "municipal governments"
        if "school boards" in lowered:
            return "school boards"
        return None

    def _focus_phrase_is_narrow_topic(self, focus_phrase: str) -> bool:
        normalized = " ".join(str(focus_phrase or "").lower().split())
        if not normalized:
            return False
        if self._future_year_signal(normalized) is not None:
            return False
        broad_future_cues = (
            "future",
            "far future",
            "later stage",
            "start later",
            "skip ahead",
            "advanced ai",
            "most computer work",
            "most remote work",
            "all things on a computer",
            "everything that can be done on a computer",
            "cognitive labor",
            "cognitive labour",
            "substrate of the economy",
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
        if re.search(r"\b(?:u\.?\s*s\.?|u\.?\s*s\.?\s*a\.?)\b", lowered) or "american" in lowered:
            return "United States"
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
            if self._match_has_negated_visual_comparison(text, match):
                return None
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

    def _match_has_negated_visual_comparison(self, text: str, match: re.Match[str]) -> bool:
        prefix = text[max(0, match.start() - 32) : match.start()].lower()
        return bool(re.search(r"\b(?:do\s+not|don't|dont|never|not)\s*$", prefix))

    def _visual_style_update_is_negated_comparison(self, user_text: str, value: str) -> bool:
        cleaned_value = " ".join(str(value or "").split()).strip()
        if not cleaned_value:
            return False
        pattern = re.compile(
            r"\b(?:do\s+not|don't|dont|never|not)\s+"
            r"(?:make\s+it\s+)?(?:feel|look|seem)\s+like\s+"
            + re.escape(cleaned_value),
            flags=re.IGNORECASE,
        )
        return bool(pattern.search(user_text))

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
        phase_anchor = self._starting_phase_anchor(
            config.premise,
            config.topic_lens,
            config.stakes,
        )
        if phase_anchor >= 3:
            return "The setup points toward a later and more structurally changed AI society; begin from that altered settlement rather than easing in from the present, and make the new baseline of income, access, daily routine, ownership, and political order legible immediately."
        if phase_anchor >= 1:
            return "The setup points later into the transition, with deeper AI diffusion and institutional change already underway; do not retreat to a timid near-present frame once the player asked for a later world."
        return "Even the broad default run should begin at the first unmistakably changed settlement, not at a barely-updated 2026. Let the opening chapter show a real break in capability, prices, staffing, daily routine, and political conflict before you narrow to any local vignette."

    def _setup_direction_block(self, config: SimulationConfig) -> str:
        return self._setup_story_memo(config)

    def _stage_reads_like_later_settlement(self, stage: StagePackage) -> bool:
        combined = " ".join([str(stage.phase_label or ""), str(stage.world_brief or "")]).lower()
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

    def _trim_with_sentence_fallback(self, text: str | None, max_chars: int, *, slack: int = 52) -> str:
        cleaned = " ".join(str(text or "").split()).strip()
        if len(cleaned) <= max_chars:
            return cleaned
        sentences = self._sentence_split(cleaned)
        if sentences:
            first_sentence = sentences[0].strip()
            if 4 <= len(first_sentence.split()) and len(first_sentence) <= max_chars + slack:
                return first_sentence
        return self._trim_without_ellipsis(cleaned, max_chars)

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
            "if",
            "when",
            "unless",
            "before",
            "after",
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

    def _strip_trailing_fragment_words(self, text: str) -> str:
        cleaned = " ".join(str(text or "").split()).strip(" ,.;:-")
        if not cleaned:
            return ""
        weak_clause_starters = {
            "creating",
            "increasing",
            "leaving",
            "making",
            "moving",
            "raising",
            "reducing",
            "reinforcing",
            "shifting",
            "tightening",
            "widening",
        }
        last_comma = cleaned.rfind(",")
        if last_comma >= 0:
            tail = cleaned[last_comma + 1 :].strip(" ,.;:-")
            tail_words = tail.split()
            if tail_words and len(tail_words) <= 8 and tail_words[0].lower().strip(".,;:") in weak_clause_starters:
                cleaned = cleaned[:last_comma].strip(" ,.;:-")
        weak_final_words = {
            "made",
            "make",
            "makes",
            "became",
            "become",
            "becomes",
            "turned",
            "turn",
            "turns",
            "left",
            "leave",
            "leaves",
            "forced",
            "force",
            "forces",
            "let",
            "lets",
            "kept",
            "keep",
            "keeps",
            "moved",
            "move",
            "moves",
            "gave",
            "give",
            "gives",
            "took",
            "take",
            "takes",
            "built",
            "build",
            "builds",
            "owned",
            "own",
            "owns",
            "can",
            "could",
            "would",
            "should",
            "may",
            "might",
            "pushed",
            "push",
            "pushes",
            "is",
            "are",
            "was",
            "were",
            "have",
            "has",
            "had",
            "increasing",
            "shifting",
            "reinforcing",
            "staffed",
            "opaque",
            "a",
            "an",
            "the",
            "to",
            "of",
            "for",
            "and",
            "in",
            "on",
            "at",
            "by",
            "with",
            "about",
            "from",
            "into",
            "over",
            "under",
        }
        weak_tail_nouns = {
            "household",
            "households",
            "public",
            "private",
            "school",
            "schools",
            "work",
            "care",
            "service",
            "services",
            "economic",
            "political",
            "civic",
            "regional",
            "national",
        }
        words = cleaned.split()
        while len(words) > 3:
            last = words[-1].lower().strip(".,;:")
            previous = words[-2].lower().strip(".,;:") if len(words) > 1 else ""
            if last in weak_final_words or (previous in weak_final_words and last in weak_tail_nouns):
                words.pop()
                continue
            break
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
        cleaned = re.sub(r"\bmonthly\s+federal\s+monthly\s+machine\s+check\b", "monthly federal machine check", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\bfederal\s+monthly\s+machine\s+check\b", "federal machine check", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\bmonthly\s+public\s+monthly\s+machine\s+check\b", "monthly public machine check", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\bmonthly\s+monthly\s+(machine\s+checks?|help\s+credits?)\b", r"monthly \1", cleaned, flags=re.IGNORECASE)
        return " ".join(cleaned.split()).strip()

    def _capitalize_sentence_start(self, text: str) -> str:
        stripped = text.lstrip()
        if not stripped:
            return text
        prefix = text[: len(text) - len(stripped)]
        return f"{prefix}{stripped[:1].upper()}{stripped[1:]}"

    def _soften_comma_inventory(self, text: str) -> str:
        cleaned = " ".join(str(text or "").split()).strip()
        if cleaned.count(",") < 3:
            return cleaned

        def split_transition(match: re.Match[str]) -> str:
            return f". {self._capitalize_sentence_start(match.group(1))} "

        # Only split at real discourse turns. Splitting generic comma chains after
        # the fact tends to create choppy voiceover fragments like "Read. Write.",
        # and subordinators like "while" often become sentence fragments.
        cleaned = re.sub(
            r",\s+\b(but|so)\b\s+",
            split_transition,
            cleaned,
            count=1,
            flags=re.IGNORECASE,
        )
        return " ".join(cleaned.split()).strip()

    def _normalize_sentence(self, text: str, *, max_words: int = 22, max_chars: int = 150) -> str:
        cleaned = self._plain_language_cleanup(self._collapse_adjacent_word_repeats(text)).strip(" -")
        if not cleaned:
            return ""
        cleaned = self._trim_with_sentence_fallback(cleaned, max_chars).rstrip(",;:")
        has_sentence_ending = bool(cleaned and cleaned[-1] in ".!?")
        words = cleaned.split()
        if len(words) > max_words + 4 and not has_sentence_ending:
            cleaned = " ".join(words[:max_words]).rstrip(",;:")
        cleaned = self._strip_trailing_connector_words(cleaned)
        cleaned = self._strip_trailing_fragment_words(cleaned)
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
        cleaned = self._trim_with_sentence_fallback(cleaned, max_chars).rstrip(",;:.?")
        cleaned = self._strip_trailing_connector_words(cleaned)
        cleaned = self._strip_trailing_fragment_words(cleaned)
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
        world_question = self._stage_split_hint(stage.world_brief)
        if world_question:
            return self._normalize_question(f"How does this reel explain {world_question.rstrip('.?')}")
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
        paragraphs = [
            re.sub(
                r"(?<=[.!?])\s+([a-z])",
                lambda match: f" {match.group(1).upper()}",
                paragraph.strip(),
            )
            for paragraph in paragraphs
        ]
        return "\n\n".join(paragraphs).strip()

    def _normalize_room_briefing(self, text: str) -> str:
        normalized: list[str] = []
        for sentence in self._sentence_split(text)[:4]:
            cleaned = self._plain_language_cleanup(" ".join(str(sentence or "").split())).strip(" -")
            if not cleaned:
                continue
            speakable = self._normalize_sentence(cleaned, max_words=32, max_chars=210)
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

    def _room_briefing_is_generic(self, text: str) -> bool:
        lowered = " ".join(str(text or "").split()).strip().lower()
        if not lowered:
            return True
        generic_phrases = (
            "the gain people would fight to keep",
            "visible in daily life",
            "the fight organizing politics",
            "the main fight is over",
            "the world is changing fast",
        )
        return any(phrase in lowered for phrase in generic_phrases)

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

    def _stage_opening_paragraph(self, world_brief: str) -> str:
        paragraphs = [part.strip() for part in re.split(r"\n\s*\n", world_brief) if part.strip()]
        if paragraphs:
            return self._normalize_summary_prose(paragraphs[0], max_paragraphs=1)
        return self._normalize_summary_prose(world_brief, max_paragraphs=1)

    def _world_brief_sentences(self, world_brief: str) -> list[str]:
        return [part.strip() for part in re.split(r"(?<=[.!?])\s+", world_brief) if part.strip()]

    def _stage_capability_hint(self, world_brief: str) -> str:
        opening = self._stage_opening_paragraph(world_brief)
        for source in (opening, world_brief):
            if not source:
                continue
            for sentence in self._world_brief_sentences(source):
                normalized = " ".join(sentence.split()).strip()
                if not normalized:
                    continue
                tail = normalized.rstrip(".!?").lower()
                if len(normalized.split()) < 6 or tail.endswith((" is", " are", " was", " were", " has", " have")):
                    continue
                return self._normalize_sentence(normalized, max_words=26, max_chars=180)
        return ""

    def _stage_upside_hint(self, world_brief: str) -> str:
        sentences = self._world_brief_sentences(world_brief)
        for sentence in sentences:
            lowered = sentence.lower()
            if any(
                token in lowered
                for token in (
                    "got cheaper",
                    "cost far less",
                    "purchasing power rose",
                    "widely defended",
                    "families also receive",
                    "same day",
                    "feel more like utilities",
                )
            ):
                return self._normalize_sentence(sentence, max_words=24, max_chars=168)
        for sentence in sentences:
            lowered = sentence.lower()
            if any(token in lowered for token in ("people can now", "households can now", "small firms can now", "schools can now", "families can now", "became normal", "became cheaper", "became easier")):
                return self._normalize_sentence(sentence, max_words=24, max_chars=168)
        for sentence in sentences:
            lowered = sentence.lower()
            if any(token in lowered for token in ("public ai account", "monthly machine check", "machine check", "cheap automated service")):
                return self._normalize_sentence(sentence, max_words=24, max_chars=168)
        return ""

    def _stage_split_hint(self, world_brief: str) -> str:
        sentences = self._world_brief_sentences(world_brief)
        for sentence in sentences:
            lowered = sentence.lower()
            if lowered.startswith(("that is now the central political fight", "that is the opening political split", "that is the fight", "this is the fight")):
                continue
            if any(
                token in lowered
                for token in (
                    "argument is over",
                    "control versus access",
                    "public utility",
                    "licensed empire",
                    "who controls",
                    "who gets",
                    "who owns",
                    "fight is over",
                    "split",
                    "conflict",
                    "premium tiers",
                    "gap shapes class",
                    "access stays unequal",
                    "own the machines",
                    "dignity and power",
                )
            ):
                return self._normalize_sentence(sentence, max_words=24, max_chars=168)
        return ""

    def _stage_constraint_hint(self, world_brief: str) -> str:
        sentences = self._world_brief_sentences(world_brief)
        for sentence in sentences:
            lowered = sentence.lower()
            if any(
                token in lowered
                for token in (
                    "chokepoint",
                    "dependence",
                    "vulnerable to service rules",
                    "rationed",
                    "harder to govern",
                    "bottleneck",
                    "scarcity",
                    "cannot",
                    "can't",
                    "slow",
                )
            ):
                return self._normalize_sentence(self._board_line_rewrite(sentence), max_words=24, max_chars=168)
        return ""

    def _stage_settlement_hints(self, world_brief: str) -> tuple[str, str, str]:
        all_sentences = [part.strip() for part in re.split(r"(?<=[.!?])\s+", world_brief) if part.strip()]

        def overlap(left: str, right: str) -> int:
            left_tokens = {token for token in re.findall(r"[a-z]{4,}", left.lower())}
            right_tokens = {token for token in re.findall(r"[a-z]{4,}", right.lower())}
            return len(left_tokens & right_tokens)

        def pick_sentence(
            patterns: tuple[str, ...],
            *,
            max_words: int = 22,
            max_chars: int = 168,
            avoid: str = "",
        ) -> str:
            best = ""
            best_score = -1
            for sentence in all_sentences:
                lowered = sentence.lower()
                score = sum(2 for pattern in patterns if re.search(pattern, lowered))
                if not score:
                    continue
                if any(
                    token in lowered
                    for token in (
                        "old office week",
                        "public model account",
                        "public ai account",
                        "basic tier",
                        "productivity rebate",
                        "monthly",
                        "queue time",
                        "part time human work",
                        "machine systems",
                        "public service",
                        "state now runs",
                        "home triage",
                    )
                ):
                    score += 2
                if avoid and overlap(sentence, avoid) >= 5:
                    score -= 3
                normalized = self._normalize_sentence(self._board_line_rewrite(sentence), max_words=max_words, max_chars=max_chars)
                if normalized and score > best_score:
                    best = normalized
                    best_score = score
            return best

        household = pick_sentence(
            (
                r"\bhousehold(s)?\b",
                r"\bbills?\b",
                r"\bincome\b",
                r"\bproductivity rebate\b",
                r"\bdividend\b",
                r"\bmonthly\b",
                r"\bmachine[- ]income\b",
                r"\bmachine systems\b",
                r"\bpart time human work\b",
                r"\bhousehold floor\b",
                r"\bold office week\b",
                r"\bsecurity\b",
                r"\bbudget\b",
            ),
        )
        access = pick_sentence(
            (
                r"\baccess\b",
                r"\bpublic model account\b",
                r"\bpublic ai account\b",
                r"\bbasic tier\b",
                r"\butility\b",
                r"\bsubscription\b",
                r"\bplatform\b",
                r"\bcompute\b",
                r"\bqueue\b",
                r"\btier\b",
                r"\bpremium\b",
                r"\bservice\b",
                r"\bchannel\b",
            ),
            avoid=household,
        )
        if access and household and overlap(access, household) >= 5:
            access = pick_sentence(
                (
                    r"\baccess\b",
                    r"\bpublic model account\b",
                    r"\bpublic ai account\b",
                    r"\bbasic tier\b",
                    r"\butility\b",
                    r"\bsubscription\b",
                    r"\bplatform\b",
                    r"\bcompute\b",
                    r"\bqueue\b",
                    r"\btier\b",
                    r"\bpremium\b",
                    r"\bservice\b",
                    r"\bchannel\b",
                ),
                avoid=household,
            )
        public_service = pick_sentence(
            (
                r"\bschool(s)?\b",
                r"\bclinic(s)?\b",
                r"\bbenefits?\b",
                r"\blicensing\b",
                r"\btax disputes?\b",
                r"\bpublic service\b",
                r"\bagency\b",
                r"\bmunicipal\b",
                r"\bclassroom\b",
                r"\bhospital\b",
                r"\bcasework\b",
                r"\bhome triage\b",
                r"\btutors?\b",
            ),
        )
        return public_service, household, access

    def _stage_institution_hints(self, world_brief: str) -> tuple[str, str]:
        sentences = [part.strip() for part in re.split(r"(?<=[.!?])\s+", world_brief) if part.strip()]
        firm = next(
            (
                self._normalize_sentence(self._board_line_rewrite(sentence), max_words=22, max_chars=168)
                for sentence in sentences
                if any(
                    token in sentence.lower()
                    for token in (
                        "firm",
                        "staff",
                        "teams",
                        "small business",
                        "departments",
                        "agent-run",
                        "employer",
                        "shop",
                        "office",
                        "bursts of human judgment",
                        "contract oversight",
                        "middle management",
                        "cut their ladders",
                        "top end",
                    )
                )
            ),
            "",
        )
        ownership = ""
        ownership_patterns = (
            r"\b(?:control|toll|rent|chokepoint|tenant|tenants)\b",
            r"\b(?:ownership|owner|owners)\b",
        )
        for pattern in ownership_patterns:
            for index, sentence in enumerate(sentences):
                lowered = sentence.lower()
                if lowered.startswith("ownership also narrowed") and index + 1 < len(sentences):
                    sentence = sentences[index + 1]
                    lowered = sentence.lower()
                if not re.search(pattern, lowered):
                    continue
                ownership = self._normalize_sentence(self._board_line_rewrite(sentence), max_words=22, max_chars=168)
                if ownership:
                    break
            if ownership:
                break
        return firm, ownership

    def _stage_mechanism_hint(self, world_brief: str) -> str:
        for sentence in self._world_brief_sentences(world_brief):
            lowered = sentence.lower()
            if any(
                token in lowered
                for token in (
                    "public ai account",
                    "monthly machine check",
                    "queue",
                    "priority",
                    "platform",
                    "subscription",
                    "appeal",
                    "permit",
                    "power",
                    "compute",
                    "grid",
                    "robot",
                    "ownership",
                )
            ):
                return self._normalize_sentence(self._board_line_rewrite(sentence), max_words=22, max_chars=164)
        return self._stage_opening_paragraph(world_brief)

    def _stage_constituency_hint(self, world_brief: str) -> str:
        sentences = self._world_brief_sentences(world_brief)
        for sentence in sentences:
            lowered = sentence.lower()
            if any(token in lowered for token in ("small firms", "households", "students", "parents", "patients", "shoppers", "exporters", "municipal", "families", "builders", "owners")):
                return self._normalize_sentence(sentence, max_words=20, max_chars=150)
        return ""

    def _stage_physical_hint(self, world_brief: str) -> str:
        sentences = self._world_brief_sentences(world_brief)
        for sentence in sentences:
            lowered = sentence.lower()
            if any(token in lowered for token in ("robot", "robotics", "power", "grid", "port", "housing", "construction", "warehouse", "energy", "fab", "materials")):
                return self._normalize_sentence(sentence, max_words=22, max_chars=164)
        return ""

    def _stage_dependency_hint(self, world_brief: str) -> str:
        for sentence in self._world_brief_sentences(world_brief):
            lowered = sentence.lower()
            if any(
                token in lowered
                for token in (
                    "public account keeps people included",
                    "vulnerable to service rules",
                    "permissioned life",
                    "queue",
                    "fused federal-platform stack",
                    "dependence",
                )
            ):
                return self._normalize_sentence(self._board_line_rewrite(sentence), max_words=24, max_chars=168)
        return ""

    def _dedupe_lines(self, items: list[str], *, limit: int) -> list[str]:
        cleaned: list[str] = []
        seen: set[str] = set()
        for item in items:
            line = " ".join(str(item or "").split()).strip()
            if not line:
                continue
            key = re.sub(r"[^a-z0-9]+", " ", line.lower()).strip()
            if not key or key in seen:
                continue
            seen.add(key)
            cleaned.append(line)
            if len(cleaned) >= limit:
                break
        return cleaned

    def _pick_world_sentence(
        self,
        world_brief: str,
        patterns: tuple[str, ...],
        *,
        max_words: int = 24,
        max_chars: int = 168,
    ) -> str:
        sentences = self._world_brief_sentences(world_brief)
        for sentence in sentences:
            lowered = sentence.lower()
            if any(re.search(pattern, lowered) for pattern in patterns):
                return self._normalize_sentence(self._board_line_rewrite(sentence), max_words=max_words, max_chars=max_chars)
        return ""

    def _world_has(self, world_brief: str, *patterns: str) -> bool:
        lowered = world_brief.lower()
        return any(re.search(pattern, lowered) for pattern in patterns)

    def _derive_policy_axes(self, world_brief: str) -> list[str]:
        lowered = world_brief.lower()
        axes: list[str] = []
        def has(pattern: str) -> bool:
            return bool(re.search(pattern, lowered))
        if any(token in lowered for token in ("public ai", "account", "access", "utility", "subscription", "school", "clinic", "small firm", "small business")):
            axes.append("Broaden access to capable systems across households, schools, clinics, and small firms")
        if any(token in lowered for token in ("productivity rebate", "dividend", "machine check", "machine-income", "household floor", "public model account")):
            axes.append("Decide what the new household floor is and who gets it automatically")
        if has(r"\b(?:ownership|owner|owners|platform|platforms|control|chokepoint|rent|compute bloc|compute blocs|utility toll)\b"):
            axes.append("Limit chokepoint control and spread ownership of the gains")
        if any(token in lowered for token in ("power", "grid", "energy", "fab", "warehouse", "port", "housing", "robot", "robotics", "construction", "materials")):
            axes.append("Build the physical bottlenecks faster: power, compute, housing, logistics, and deployment capacity")
        if any(token in lowered for token in ("income", "check", "allowance", "dividend", "security", "bargaining", "wage", "pay")):
            axes.append("Protect household security and bargaining power as the old labor bargain gives way")
        if any(token in lowered for token in ("scam", "fraud", "liability", "trust", "safety", "recourse", "misinformation")):
            axes.append("Set clear liability, fraud control, and public recourse where AI replaces trusted judgment")
        if not axes:
            axes.extend(
                [
                    "Keep useful deployment broad while preserving public recourse",
                    "Turn visible gains into durable household security",
                    "Keep adoption from hardening into concentrated platform power",
                ]
            )
        elif len(axes) == 1:
            axes.extend(
                [
                    "Turn visible gains into durable household security",
                    "Keep adoption from hardening into concentrated platform power",
                ]
            )
        elif len(axes) == 2:
            axes.append("Translate new capability into institutions that ordinary people can actually use")
        while len(axes) < 4:
            for fallback in (
                "Keep useful deployment broad while preserving public recourse",
                "Turn visible gains into durable household security",
                "Keep adoption from hardening into concentrated platform power",
                "Translate new capability into institutions that ordinary people can actually use",
                "Build through the physical bottlenecks before abundance turns into rationing",
            ):
                if fallback not in axes:
                    axes.append(fallback)
                if len(axes) >= 4:
                    break
        axes = self._dedupe_lines(axes, limit=4)
        return self._normalize_short_lines(axes, limit=4, max_chars=132, sentence_fragment=True)

    def _resolve_room_briefing(
        self,
        *,
        drafted_room_briefing: str | None,
        world_brief: str,
    ) -> str:
        authored_raw = " ".join(str(drafted_room_briefing or "").split()).strip()
        authored_sentences = self._sentence_split(authored_raw)
        if (
            authored_raw
            and len(authored_raw.split()) >= 14
            and len(authored_sentences) >= 3
            and self._room_briefing_is_speakable(authored_raw)
            and not self._room_briefing_is_generic(authored_raw)
        ):
            return authored_raw
        authored = self._normalize_room_briefing(drafted_room_briefing or "")
        if authored and len(self._sentence_split(authored)) >= 3 and not self._room_briefing_is_generic(authored):
            return authored
        return self._compose_room_briefing(
            world_brief=world_brief,
            fallback_room_briefing=drafted_room_briefing,
        )

    def _compose_room_briefing(
        self,
        *,
        world_brief: str,
        fallback_room_briefing: str | None,
    ) -> str:
        sentences = self._world_brief_sentences(world_brief)
        used: set[int] = set()

        def room_sentence(value: str | None, *, max_words: int = 38, max_chars: int = 250) -> str:
            cleaned = self._board_line_rewrite(value or "")
            if not cleaned:
                return ""
            cleaned = re.split(r"(?<=[.!?])\s+", cleaned, maxsplit=1)[0].strip()
            words = cleaned.split()
            if len(words) < 7:
                return ""
            if cleaned.lower().startswith(("that is the opening political split", "that is the fight", "this is the fight")):
                return ""
            if cleaned.lower().startswith(("they ", "it ", "that ", "those ", "these ", "this ", "he ", "she ", "there ")):
                return ""
            return self._normalize_sentence(cleaned, max_words=max_words, max_chars=max_chars)

        def take_matching(patterns: tuple[str, ...], *, max_words: int = 38, max_chars: int = 250) -> str:
            for index, sentence in enumerate(sentences):
                if index in used:
                    continue
                lowered = sentence.lower()
                if any(re.search(pattern, lowered) for pattern in patterns):
                    normalized = room_sentence(sentence, max_words=max_words, max_chars=max_chars)
                    if normalized:
                        used.add(index)
                        return normalized
            return ""

        opening = room_sentence(self._stage_opening_paragraph(world_brief) or fallback_room_briefing, max_words=42, max_chars=270)
        gain = take_matching(
            (
                r"\bhousehold(s)?\b",
                r"\bpublic ai account\b",
                r"\bmachine check\b",
                r"\bcheaper\b",
                r"\bpurchasing power\b",
                r"\butilities than purchases\b",
                r"\bsame[- ]day\b",
                r"\bsecurity\b",
                r"\bservice\b",
            ),
        ) or room_sentence(self._stage_upside_hint(world_brief))
        split = take_matching(
            (
                r"\bpublic utility\b",
                r"\bprivate\b",
                r"\bfight\b",
                r"\bargument\b",
                r"\bwho gets\b",
                r"\bwho owns\b",
                r"\bwho controls\b",
                r"\bqueue\b",
                r"\bpriority\b",
                r"\bdivide\b",
                r"\bpremium tiers?\b",
                r"\bgap shapes class\b",
                r"\bdignity and power\b",
            ),
        ) or room_sentence(self._stage_split_hint(world_brief))
        bottleneck = take_matching(
            (
                r"\bbottleneck\b",
                r"\bconstraint\b",
                r"\bpower\b",
                r"\bcompute\b",
                r"\bchips?\b",
                r"\btransmission\b",
                r"\bwater\b",
                r"\bhousing\b",
                r"\blogistics\b",
                r"\bfab\b",
            ),
        ) or room_sentence(self._stage_constraint_hint(world_brief))

        ordered = [opening, gain, split, bottleneck]
        composed_parts: list[str] = []
        for sentence_text in ordered:
            if sentence_text and sentence_text not in composed_parts:
                composed_parts.append(sentence_text)
        composed = " ".join(composed_parts[:3])
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
        seen: set[str] = set()
        for item in items:
            cleaned = self._board_line_rewrite(item or "")
            if not cleaned:
                continue
            cleaned = self._trim_with_sentence_fallback(cleaned, max_chars)
            cleaned = self._strip_trailing_connector_words(cleaned)
            cleaned = self._strip_trailing_fragment_words(cleaned)
            cleaned = self._strip_trailing_connector_words(cleaned)
            if not cleaned:
                continue
            if len(re.findall(r"[a-z0-9']+", cleaned.lower())) < 7 and not re.search(
                r"\b(?:account|check|rebate|dividend|income|queue|compute|power|rent|wage|school|clinic|permit|benefit|robot|grid|chip|utility|platform|price|housing|tier|access|ownership|household|service|office)\b",
                cleaned,
                flags=re.IGNORECASE,
            ):
                continue
            if sentence_fragment:
                cleaned = cleaned.rstrip(".")
            elif cleaned[-1] not in ".!?":
                cleaned = self._capitalize_sentence_start(cleaned)
                cleaned = f"{cleaned}."
            else:
                cleaned = self._capitalize_sentence_start(cleaned)
            key = re.sub(r"[^a-z0-9]+", " ", cleaned.lower()).strip()
            if not key or key in seen:
                continue
            seen.add(key)
            normalized.append(cleaned)
            if len(normalized) >= limit:
                break
        return normalized

    def _board_line_rewrite(self, text: str) -> str:
        cleaned = self._plain_language_cleanup(" ".join(str(text or "").split())).strip(" -")
        if not cleaned:
            return ""
        rewrites = (
            (
                r"^A capable AI service can do almost all screen work end to end:.*$",
                "AI services now handle most screen work end to end.",
            ),
            (
                r"^Robot fleets handle a large share of .*$",
                "Robot fleets now handle much of logistics, factory tending, and routine physical upkeep.",
            ),
            (
                r"^Money usually arrives from three streams:.*$",
                "Many households now live on a mix of machine checks, public supports, and bursts of human work.",
            ),
            (
                r"^A family's money usually arrives in three streams:.*$",
                "Many households now live on public transfers, bursts of human work, and whatever local ownership they still hold.",
            ),
            (
                r"^A family[’']s money usually arrives in three streams:.*$",
                "Many households now live on public transfers, bursts of human work, and whatever local ownership they still hold.",
            ),
            (
                r"^Employment statistics still exist, but they no longer explain who feels secure\.$",
                "Old labor metrics still exist, but they no longer explain who feels secure.",
            ),
            (
                r"^Security now depends more on access than on occupation\.$",
                "Security now depends more on access than on occupation.",
            ),
            (
                r"^A handful of cloud, energy, and robotics groups control much of the productive stack, and their contracts shape what states, schools, and hospitals can actually do\.$",
                "A few cloud, energy, and robotics groups still control the productive stack.",
            ),
            (
                r"^A few companies still control much of the model training stack, chip design, cloud fabric, and agent marketplaces, while states fight to host the power and packaging that make autonomy cheap\.$",
                "A few companies still control the model, cloud, and chip stack.",
            ),
            (
                r"^They are arguing over who gets first claim on its output\.$",
                "The fight is over who gets first claim on machine output.",
            ),
            (
                r"^The other says heavy public claims will slow innovation just as China and its partners push hard on chips, industrial robotics, and strategic minerals\.$",
                "Heavy public claims could slow innovation while rival blocs push hard on chips and industrial robotics.",
            ),
            (
                r"^One side wants the machine economy treated like a public utility:.*$",
                "One side wants the machine economy treated like a public utility.",
            ),
            (
                r"^The fight is over whether compute acts like a public utility with dividends and reserved access, or like a toll road owned by a few platforms.*$",
                "The fight is over whether compute stays a public utility or hardens into a toll road.",
            ),
            (
                r"^Most adults have a public AI account, a monthly machine check, and some claim on cheap automated service even when they do not have a boss\.$",
                "Most adults now have a public AI account and some kind of machine-income floor.",
            ),
            (
                r"^Most adults have a public AI account, a monthly machine check, and some claim on cheap automated service.*$",
                "Most adults now have a public AI account and some kind of machine-income floor.",
            ),
            (
                r"^Software, accounting, tutoring, translation, claims filing, tax prep, scheduling, and basic legal drafting became so cheap that they feel more like utilities than purchases\.$",
                "Routine digital services became so cheap they feel more like utilities than purchases.",
            ),
            (
                r"^The numbers people watch are compute allowance, housing cost, utility reliability, queue priority, and whether their county still has fast access to a top-tier model\.$",
                "People now watch compute allowance, housing cost, utility reliability, and queue priority more than old labor statistics.",
            ),
        )
        for pattern, replacement in rewrites:
            if re.match(pattern, cleaned, flags=re.IGNORECASE):
                return replacement
        if ":" in cleaned and len(cleaned) > 176:
            head = cleaned.split(":", 1)[0].strip()
            if len(head.split()) >= 6:
                cleaned = head
        cleaned = re.sub(r"\bthat once took weeks is usually resolved the same day\b", "now clears the same day", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\bFor many households, the old office week is gone\b", "For many households, the old office week is gone", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\bThe bottleneck everyone can feel is energy and compute\b", "The felt bottleneck is energy and compute", cleaned, flags=re.IGNORECASE)
        return " ".join(cleaned.split()).strip()

    def _normalize_narration_line(self, text: str) -> str:
        cleaned = self._plain_language_cleanup(self._collapse_adjacent_word_repeats(text)).strip()
        if not cleaned:
            return ""
        cleaned = re.sub(
            r"\bthe cheapest expert they have ever\.$",
            "the cheapest expert they have ever had.",
            cleaned,
            flags=re.IGNORECASE,
        )
        cleaned = re.sub(
            r"\.\s+research,\s*software,\s*planning,\s*compliance,?\s*and\s*routine\s*management\b",
            ", from research and software to compliance and routine management",
            cleaned,
            flags=re.IGNORECASE,
        )
        cleaned = re.sub(
            r"(?<=[.!?])\s+(?:and|so)\s+([a-z])",
            lambda match: f" {match.group(1).upper()}",
            cleaned,
            flags=re.IGNORECASE,
        )
        cleaned = re.sub(r"\bmodels data and distribution\b", "models and distribution", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\bscreen based\b", "screen-based", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\bwhite collar\b", "white-collar", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(
            r"\bwhere\s+(?:a\s+|the\s+|one\s+)?(?:real\s+|human\s+)?(?:person|official|worker|doctor|teacher|judge)\s+(?:still|must|has\s+to|needs\s+to)?\.?$",
            "where someone still has to own the judgment.",
            cleaned,
            flags=re.IGNORECASE,
        )
        cleaned = self._soften_comma_inventory(cleaned)
        fragment_parts = [part.strip() for part in re.split(r"\.\s+", cleaned) if part.strip()]
        if (
            len(fragment_parts) >= 2
            and all(len(part.split()) <= 4 for part in fragment_parts[1:])
            and not any(
                re.search(r"\b(?:is|are|was|were|be|been|being|have|has|had|can|could|will|would|should|must|do|does|did)\b", part.lower())
                for part in fragment_parts[1:]
            )
        ):
            cleaned = " and ".join(part.rstrip(".") for part in fragment_parts[:4])
        cleaned = re.sub(r"\s*;\s*", ". ", cleaned)
        cleaned = re.sub(r"\s*[–—]\s*", ". ", cleaned)
        sentences = [part.strip() for part in re.split(r"(?<=[.!?])\s+", cleaned) if part.strip()]
        if len(sentences) > 3:
            cleaned = " ".join(sentences[:3]).strip()
        cleaned = self._soften_comma_inventory(cleaned)
        cleaned = self._strip_trailing_fragment_words(cleaned)
        cleaned = re.sub(
            r"(?<=[.!?])\s+([a-z])",
            lambda match: f" {match.group(1).upper()}",
            cleaned,
        )
        if cleaned[-1] not in ".!?":
            cleaned = f"{cleaned}."
        return cleaned

    def _featurette_instructions(self) -> str:
        return (
            "You are writing optional documentary side reels for the same AGI transition chapter. "
            "Think of them as short optional mini-documentaries the player can open while the main chapter is already live. "
            "Return three reels that reveal three different parts of the world a curious player would naturally want to understand better. "
            "Each reel should make one surprising arrangement feel graspable in 3 or 4 beats, not restate the main montage. "
            "Across the set, vary the angle and rhythm. One reel might clarify household life, another who owns the gains, another how a public or physical system actually works now. "
            "If the chapter is far from the present, let at least one reel show an arrangement that would have sounded extraordinary in 2026 but feels normal here. "
            "Do not stop at office automation or faster admin when the world itself changed more deeply than that. "
            "Be plainspoken, vivid, and specific. Avoid named-place filler, memo prose, vague futurism, slogan lines, classroom stiffness, and consultant fog. "
            "Each reel should contain one surprising claim, one lived scene, one mechanism, and one unresolved pressure. "
            "Do not make every beat sound like a thesis sentence. Open beats with an observed fact, scene, or reversal when you can. "
            "Do not make the set feel like a curriculum. It should feel like three smart side documentaries. "
            "Explain the mechanism like you are talking to an interested friend in one pass. "
            "Titles should sound inviting without becoming theatrical. "
            "The question field must be a specific viewer-facing question in natural language. It is not an empty field and not a generic placeholder. "
            "Image prompts should stay painterly and civic, with impressionist abstraction, visible institutions, and real human activity. Lean toward a Cezanne-Monet-Matisse hybrid with thicker layered brushstrokes, softened faces, planar color, and occasional pointillist light instead of glossy realism, CGI sheen, empty hologram spectacle, or cartoon imagery."
        )

    def _featurette_prompt(self, *, config: SimulationConfig, stage: StagePackage) -> str:
        citizen_lines = "\n".join(
            f"- {citizen.display_name}, {citizen.role} in {citizen.region}: {self._clip(citizen.current_update or citizen.summary, 150)}"
            for citizen in stage.sample_citizens[:4]
        ) or "- No citizen snapshots yet."
        return (
            f"Country: {config.country}\n"
            f"Setup direction from the player:\n{self._setup_direction_block(config)}\n"
            f"Stage: {stage.phase_label}\n"
            f"Title: {stage.title}\n"
            f"Year label: {stage.year_label}\n"
            f"Main montage logline: {stage.montage_logline}\n"
            f"World brief: {stage.world_brief}\n"
            f"Visual style: {config.visual_style}\n\n"
            "Public pressures already visible:\n"
            f"{self._salient_poll_lines(stage.poll_summaries, limit=4)}\n\n"
            "Sample lived evidence:\n"
            f"{citizen_lines}\n\n"
            "Return exactly 3 featurettes.\n"
            "Make them feel like three genuinely different reasons to click deeper, not a curriculum and not paraphrases of each other.\n"
            "At least one reel should help a player feel the baseline of the world directly: what pays the bills now, what grants access, what people do with their days, or what replaced the old week.\n"
            "If the chapter is far enough from the present, let at least one reel reveal an arrangement that would have sounded extraordinary in 2026 but feels normal here.\n"
            "If one reel follows everyday life, make it about how ordinary life is actually secured now, not a bundle of handy AI chores.\n"
            "For each featurette, provide:\n"
            "- a short subject label of 2 to 5 words\n"
            "- one short natural-language question the reel answers for the player\n"
            "- a title that sounds like a documentary chapter card\n"
            "- a logline of about 18 to 30 words\n"
            "- 3 or 4 narrative beats in coherent order\n"
            "- fresh prose and a fresh mechanism, not warmed-over stage summary lines\n"
            "- begin with a scene, fact, or reversal rather than a thesis sentence when possible\n"
            "- plain, understandable detail: what changed, why it matters, and what money, access, staffing, ownership, or control channel now does the work\n"
            "- one clear mechanism and one human consequence per beat\n"
            "- at most one local vignette beat per featurette unless a place is doing real causal work\n"
            "- often the best set includes a reel that reveals a social or economic arrangement that sounds materially post-current to a 2026 viewer, not just a stronger version of today's workflow tools"
        )

    def _stage_instructions(self, config: SimulationConfig) -> str:
        later_world_requested = self._setup_implies_later_settlement(config)
        instructions = [
            "You are writing one chapter of an AGI transition simulation.",
            "Most of the job is to write one lucid mini-essay about the world as it is now.",
            "Treat the required fields as light wrappers around one authored chapter, not as separate chores.",
            "Honor the player's natural-language setup directly. There is no hidden mode and no fixed staircase.",
            "Write like future reportage in plain English, not like a briefing deck.",
            "Start with the new baseline: what capable systems can broadly do now, what became ordinary, and what stopped being the main thing people organize life around.",
            "Then show how households, firms, schools, clinics, public offices, and politics actually live inside that baseline.",
            "Five to ten years ahead should often feel materially unlike 2026. If AI can do most screen work, let professions, middle layers, household routine, public services, and geopolitics change accordingly.",
            "Assume a fast AI timeline by default. Do not retreat to mild tool-improvement stories unless the player's setup clearly demands a slower world.",
            "Lead with a real gain before the strain so the audience understands why adoption kept going.",
            "Explain arrangements plainly: how people get money, access, expertise, schooling, care, permits, status, or security now.",
            "If older metrics like unemployment or the normal job week no longer organize life, say what people watch instead.",
            "By later chapters, do not keep replaying the same transfer-and-queue story unless it is still plainly the core of the country. Advance the arrangement into class structure, regional growth, ownership, public-service form, family routine, or geopolitical leverage.",
            "If a chapter repeats a prior motif, make the repetition meaningful by showing what institution, class map, or daily routine formed around it.",
            "A later chapter should often make a 2026 reader feel that the social order itself moved, not just the workflow tools.",
            "Use rough numbers only when they clarify. Avoid consultant fog, slogan writing, and comma-heavy inventories.",
            "Do not reuse the same opening formula from chapter to chapter. Avoid stock openers like 'The new baseline is...' unless it is genuinely the strongest sentence that time.",
            "Avoid house abstractions like settlement, bottleneck, live split, or causal story unless someone in the world would naturally say them.",
            "Prefer concrete subjects: bills, compute allowance, queue rights, staffing ratios, depot shifts, permit delays, clinic capacity, school time, ownership claims, monthly transfers.",
            "Write complete spoken sentences. Advisors, citizens, boards, and reels will inherit this chapter.",
        ]
        if later_world_requested:
            instructions.extend(
                [
                    "Start from an already changed world.",
                    "If the setup points later or stranger, let the opening sound materially post-current rather than like the present with louder adjectives.",
                    "Make the altered baseline easy to feel early: how money arrives, how access is mediated, what many adults do with their time, and what power struggle matters most.",
                    "Do not default to unemployment or hiring as the headline unless those older metrics still organize security here.",
                    "The first paragraph should make the changed baseline easy to picture before it narrows.",
                ]
            )
        return " ".join(instructions)

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
    ) -> str:
        setup_direction = self._setup_direction_block(config)
        later_world_requested = self._setup_implies_later_settlement(config)
        continuity_block = (
            "This is the opening chapter. Build directly from the user's setup language and the natural starting note. "
            f"Natural opening note: {self._natural_start_point_note(config)}."
        )
        if previous_stage:
            previous_resolution = previous_stage.resolution
            transition_lines = self._transition_lines(previous_stage)
            binding_constraint = self._binding_constraint(previous_stage)
            prior_gain = self._stage_upside_hint(previous_stage.world_brief) or transition_lines
            prior_split = self._stage_split_hint(previous_stage.world_brief) or (
                "how the gains are spreading and who controls them"
            )
            continuity_block = (
                f"The last chapter was {previous_stage.title} ({previous_stage.phase_label}, {previous_stage.year_label}). "
                f"Since then, these changes are plainly visible: {transition_lines}. "
                f"One gain that stuck is {prior_gain}. "
                f"What still binds is {binding_constraint}. "
                f"The live unresolved question now is {prior_split}. "
            )
            if previous_resolution:
                player_won_last_election = previous_resolution.winner == config.player_name
                continuity_block += (
                    f"{previous_resolution.winner} emerged from the last election with {previous_resolution.public_mandate}. "
                    f"{'Voters rewarded the player with office or another term.' if player_won_last_election else 'Voters turned toward the opponent or kept the player out of office.'} "
                    f"The agenda that actually took effect was: {previous_resolution.enacted_agenda}. "
                    f"Election takeaway: {previous_resolution.election_takeaway or 'No special takeaway was recorded.'} "
                    "Treat policy as background unless it clearly changed access, prices, institutions, or geopolitical posture in ordinary life."
                )
        tracking_block = ", ".join(f"{metric.label} {metric.display}" for metric in tracking.as_list()) if tracking else "no prior tracking yet"
        poll_block = self._salient_poll_lines(poll_summaries, limit=5) or "no prior polls yet"
        policy_notes_block = (
            "; ".join(previous_stage.policy_notes[:6])
            if previous_stage and previous_stage.policy_notes
            else "none yet"
        )
        chapter_guardrails = []
        if stage_index == 0 and not later_world_requested:
            chapter_guardrails.extend(
                [
                    "- this is the opening stage of the default run, so stay near-term but not timid: the capability break should already feel socially and economically real",
                    "- do not jump straight to collapse or universal robot saturation, but do show a genuine shift in leverage, access, and lived routine",
                ]
            )
        elif stage_index == 0 and later_world_requested:
            chapter_guardrails.extend(
                [
                    "- the setup already points later into the transition, so do not ease back toward the present out of caution",
                    "- make at least one changed social or economic norm legible immediately: income, access, staffing, routine, public service, ownership, or geopolitics",
                ]
            )
        if stage_index >= 1:
            chapter_guardrails.extend(
                [
                    "- by chapter two onward, the world should already move beyond a normal office-week baseline unless the earlier chapter clearly kept that world intact",
                    "- name the new order plainly: what pays bills, how people get access, what replaced old staffing ladders, and what bottleneck or power fight now matters",
                ]
            )
        if stage_index >= max(1, stage_count - 2):
            chapter_guardrails.extend(
                [
                    "- by this point the world may be radically different from 2026 if the mechanism supports it; let changed institutions and daily routines show up plainly",
                    "- do not preserve a normal office-world baseline by inertia once the chapter has moved past it",
                ]
            )
        chapter_guardrail_block = "\n".join(chapter_guardrails)
        public_memo = (
            f"Current public read: {tracking_block}. "
            f"Recent polling cues: {poll_block}. "
            f"Prior working board: {policy_notes_block}. "
            f"Queued custom poll interests: {'; '.join(queued_poll_questions) if queued_poll_questions else 'none'}."
        )
        setup_memo = (
            f"This run is set in {config.country}. "
            f"The player is {config.player_name}, {config.player_role}, facing {config.opponent_name}, {config.opponent_role}. "
            f"The sampled population is {config.population_description}. "
            f"The player's setup direction was: {setup_direction}."
        )
        chapter_memo = (
            f"This is chapter {stage_index + 1} of {stage_count}. "
            f"The chapter posture is {phase['label']}. {phase['brief']}"
        )
        return (
            f"Simulation title: {config.title}\n\n"
            f"{setup_memo}\n\n"
            f"{chapter_memo}\n\n"
            f"Continuity before this chapter:\n{continuity_block}\n\n"
            f"Public mood before this chapter:\n{public_memo}\n\n"
            f"Visual style memo: {config.visual_style}\n\n"
            "Write the next stage as one clean chapter. The backend mainly needs a strong title, year_label, montage_logline, one strong world_brief, and the opening reel beats. Put almost all of the intelligence into the world_brief.\n\n"
            "Write the world_brief like a short future-history dispatch from inside the country. Open with the most important changed fact in the world, phrased plainly and freshly. Then show how households, firms, public institutions, and politics live inside it. End with the live struggle over power, access, money, time, or legitimacy.\n"
            "Move the world materially from the prior stage or, in chapter one, from the setup baseline. If the setup already starts later in the transition, do not walk back toward the present out of caution.\n"
            "Do not force stock AGI arrangements. Name only the rails that are truly live in this world, whether they are stranger or more ordinary than expected. If AI now does most screen work, say what replaced the old junior routes, middle layers, and service queues. If old labor metrics no longer explain security, say what people watch instead. Let policy matter, but let technology diffusion and social uptake do most of the world-moving unless policy is overwhelming.\n"
            "If the prior chapter already established one organizing metric or pressure, do not merely restate it. Show what social class, institution, routine, geography, or ownership pattern formed around it by now.\n"
            "Do not let every later chapter collapse into the same monthly transfer plus human queue framing unless the whole point is that those two numbers now govern the country. Even then, show what that did to class structure, family life, business form, or political geography.\n"
            "This should read like living economic history in motion, not a caution memo and not a list of trends. Use 1 or 2 rough numbers only when they clarify. Avoid buzzwords, slogan writing, consultant fog, and comma-heavy inventories. Write complete declarative sentences only.\n"
            f"{chapter_guardrail_block}\n\n"
            "Return only:\n"
            "- title: a short evocative stage title\n"
            "- year_label: the year or time marker\n"
            "- montage_logline: one sentence of about 16-28 words naming the chapter's causal story\n"
            "- world_brief: 3 to 5 short paragraphs, usually about 320-620 words total, written as one free-flowing essay about what the world is like now\n"
            f"{self._macro_cue_line(later_world_requested=later_world_requested)}"
            "- narrative_beats: 7 or 8 short documentary lines with matching image prompts for the opening reel\n"
            "- each narrative beat should be one complete, clean spoken line of roughly 10-22 words, not a list chopped into pieces or a thesis sentence\n"
            "- every narrative beat must stand alone when read aloud; no dangling endings like 'where a person still' or clipped subordinate clauses\n"
            "- across the reel, mix the changed macro picture with lived routine, institutional change, and at least one broader physical or geopolitical pressure when it matters\n"
            "- image prompts should stay painterly, civic, and impressionist, with no readable text or dashboard numerals"
        )

    def _fallback_stage_title(self, phase_label: str, stage_index: int) -> str:
        label = " ".join(str(phase_label or "").split()).strip()
        return label or f"Stage {stage_index + 1}"

    def _fallback_year_label(self, previous_stage: StagePackage | None, stage_index: int) -> str:
        if previous_stage and str(previous_stage.year_label or "").strip():
            digits = re.search(r"(\d{4})", previous_stage.year_label)
            if digits:
                return str(int(digits.group(1)) + 1)
            return previous_stage.year_label
        return str(2026 + stage_index)

    def _fallback_montage_logline(self, world_brief: str) -> str:
        opening = self._stage_opening_paragraph(world_brief)
        if opening:
            first_sentence = re.split(r"(?<=[.!?])\s+", opening, maxsplit=1)[0].strip()
            if first_sentence:
                return first_sentence
        return "The country is learning what this AI order now makes newly possible, newly cheap, and newly contested."

    def _phase_brief(self, stage_index: int, stage_count: int, starting_phase_anchor: int = 0) -> dict[str, str]:
        later_start = starting_phase_anchor > 0
        if stage_count <= 1:
            return {
                "label": "Single Chapter",
                "brief": "Write one self-contained chapter with a clear baseline, one gain worth defending, one real bottleneck, and one live political split.",
            }

        if stage_index == 0:
            if later_start:
                return {
                    "label": "Changed Opening",
                    "brief": "Open from an already changed AI society. Make the altered baseline legible immediately rather than walking up to it from the present.",
                }
            return {
                "label": "Opening Turn",
                "brief": "Show the first chapter where the transition is plainly real in everyday life. Establish the gain, the bottleneck, and the first split without flattening the world back toward the 2020s or preserving old office norms by habit.",
            }

        ratio = stage_index / max(stage_count - 1, 1)
        if stage_index >= stage_count - 1:
            return {
                "label": "Late Chapter",
                "brief": "Let the world become as different as the accumulated capability, adoption, politics, and bottlenecks honestly imply. If the old social bargain is no longer central, say what replaced it.",
            }
        if later_start or ratio >= 0.66:
            return {
                "label": "Deep Transition",
                "brief": "Multiple institutions should now be reordering at once. Allow changed routines, income channels, power structures, physical bottlenecks, and geopolitical pressure to become explicit when the chapter supports them.",
            }
        if ratio >= 0.33:
            return {
                "label": "Acceleration",
                "brief": "Move beyond better tools. Show diffusion crossing institutions, status ladders, access rules, family routine, and regional power, with winners and losers that are easy to picture.",
            }
        return {
            "label": "Early Break",
            "brief": "The first wave has landed. Show what is newly dependable, who is already reorganizing around it, and what strain is starting to show up behind the gains.",
        }

    def _transition_lines(self, previous_stage: StagePackage) -> str:
        source = previous_stage.world_brief
        sentences = [sentence.strip() for sentence in source.split(". ") if sentence.strip()]
        if not sentences:
            return "no material change summary captured"
        return " ".join(sentence.rstrip(".") + "." for sentence in sentences[:2])

    def _binding_constraint(self, previous_stage: StagePackage) -> str:
        world_brief = str(previous_stage.world_brief or "")
        candidates = [
            self._stage_constraint_hint(world_brief),
            self._stage_dependency_hint(world_brief),
            self._stage_split_hint(world_brief),
            self._stage_physical_hint(world_brief),
        ]
        for candidate in candidates:
            cleaned = " ".join(str(candidate or "").split()).strip().rstrip(".")
            if cleaned:
                return cleaned
        return "bottlenecks, public trust, and institutional lag still bind harder than rhetoric"

    def _polish_image_prompt(self, visual_style: str, scene_prompt: str) -> str:
        return (
            f"{visual_style} Scene: {scene_prompt}. "
            "Build a specific, atmospheric composition with a clear camera distance, strong silhouettes, layered foreground and background forms, lived-in institutions, and people actually doing something. "
            "Keep the scene readable at first glance: daylight, warm interior light, or clear industrial light; avoid muddy underexposure, heavy vignette, and indistinct smoky darkness. "
            "Prefer consequential civic, industrial, domestic, classroom, clinical, retail, or infrastructure scenes over generic office tableaux. "
            "Favor wide or medium establishing shots unless the prompt explicitly asks for an intimate close-up. "
            "Avoid defaulting to a tabletop, laptop-on-desk, or single-worker close crop when the narration is describing a national or sectoral shift. "
            "Render it as a painterly oil-or-gouache civic impression, not literal reportage. "
            "Lean toward a Cezanne-Monet-Matisse family: Cezanne structure, Monet atmosphere, Matisse color blocks, with occasional pointillist light. "
            "Let different frames lean a little differently inside that family so the reel feels coherent without becoming visually repetitive. "
            "Emphasize visible brushstrokes, thicker layered paint handling, planar color masses, softened edges, abstracted faces and hands, atmospheric light, and selective detail over photoreal texture. "
            "Push the image toward semi-abstract impressionism: bold shapes first, human gesture second, fine literal detail last. "
            "Make the main subject legible even without readable signs or numbers. "
            "Let faces read as gestures and color notes rather than detailed portraits, and let architecture dissolve slightly at the edges instead of resolving into crisp realism. "
            "If the scene includes a monitor, sign, phone, paper, dashboard, map, storefront label, chart, or control room display, render it as painterly marks or abstract shapes with no crisp readable text or precise numbers. "
            "Never make legible UI, ticker text, spreadsheet cells, dashboard numerals, or literal screen labels the visual focus. "
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
            self._starting_phase_anchor(
                state.config.premise,
                state.config.topic_lens,
                state.config.stakes,
            ),
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
            "Copilots Become Civic Infrastructure",
            "Synthetic Labor Starts Repricing Services",
            "Autonomy Leaves The Lab",
            "AI Supply Chains Become Political",
            "The New Settlement Hardens",
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
                "Reliable AI agents become part of the country's productive floor, widening capability while exposing who controls access to the new machine surplus."
            ),
            world_brief=(
                f"{premise} Agentic systems now handle ordinary computer work at the scale of abundant labor. They draft, research, code, schedule, compare, purchase, and coordinate across whole workflows. Large firms moved first, but the surprising political fact is how quickly households and small organizations began treating cheap capability as part of ordinary life.\n\n"
                f"Across {region_focus}, the economic bargain is no longer only about jobs. It is about who gets the machine account, who pays the compute bill, who owns the customer channel, and which public office can reverse a cutoff. Some prices ease because services need fewer paid hours. Some incomes wobble because old coordination jobs carry less scarcity. Many adults spend more time supervising systems, caring for family, building small ventures, learning, or waiting for access rules to settle.\n\n"
                f"{stakes} People defend the gains because they can do more without begging scarce experts for help. They also resent dependence on platforms, employer systems, and public portals that can quietly meter capability. The politics is not simply pro-AI against anti-AI. It is a fight over whether the new productive floor becomes public capacity, private rent, or a messy mix of both.\n\n"
                "The physical world has not vanished. Power, chips, housing, ports, robots, local trust, and public authority still bind the transition. That is why this stage can feel both richer and more fragile at once."
            ),
            room_briefing=(
                role_frame
                + "Voters can already feel cheaper expert help, stronger everyday tools, and more capable services in daily life. "
                "What still rankles is that the gains are landing unevenly across households, firms, and places before the benefits feel durable or fair. "
                "You can move access, public adoption rules, competitive spread, and visible cushions for exposed groups this cycle. "
                "You cannot quickly rebuild local management capacity or physical bottlenecks, so both panic and complacency carry a cost."
            ),
            narrative_beats=[
                NarrativeBeat(
                    line="AI became cheap enough to feel like a new productive floor.",
                    image_prompt=self._polish_image_prompt(
                        state.config.visual_style,
                        "A broad national dawn montage of offices, clinics, classrooms, stores, and homes absorbing dependable AI into ordinary routines.",
                    ),
                ),
                NarrativeBeat(
                    line="A small team can now do what used to take a department.",
                    image_prompt=self._polish_image_prompt(
                        state.config.visual_style,
                        "A national montage of hospitals, freight networks, schools, municipal offices, and small firms newly workable for AI.",
                    ),
                ),
                NarrativeBeat(
                    line="People defend the tools because they make expertise feel closer.",
                    image_prompt=self._polish_image_prompt(
                        state.config.visual_style,
                        "A wide civic montage of families, patients, students, and small businesses using cheaper digital help in ordinary life.",
                    ),
                ),
                NarrativeBeat(
                    line="The bottleneck moved from advice to access.",
                    image_prompt=self._polish_image_prompt(
                        state.config.visual_style,
                        "A national economic panorama of data centers, utility buildout, service firms, and local offices running into physical and institutional bottlenecks.",
                    ),
                ),
                NarrativeBeat(
                    line="The new fight is who owns the channel that meters capability.",
                    image_prompt=self._polish_image_prompt(
                        state.config.visual_style,
                        "A wide sectoral montage contrasting large firms banking AI gains while county offices, small clinics, and local suppliers hit bottlenecks.",
                    ),
                ),
                NarrativeBeat(
                    line="Some adults work fewer standard hours and spend more time supervising systems, caring, learning, or trying small ventures.",
                    image_prompt=self._polish_image_prompt(
                        state.config.visual_style,
                        "A broad civic montage of households, storefronts, clinics, and schools touched by new AI convenience and growing arguments about control.",
                    ),
                ),
                NarrativeBeat(
                    line="The governing question is whether the machine surplus becomes public capacity or private rent.",
                    image_prompt=self._polish_image_prompt(
                        state.config.visual_style,
                        "A reflective closing shot of still-human bottlenecks in public offices, infrastructure sites, and supervised control rooms as the country debates how to govern the gains.",
                    ),
                ),
            ],
            sample_citizens=[],
            tracking=tracking or self._neutral_tracking(),
            macro_stats=self._fallback_macro_stats(config=state.config, stage_index=state.active_stage_index),
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
                logline="A side reel on how families keep bills paid, care arranged, and leverage intact when capable systems become ordinary but dependence shifts underneath them.",
                status="generating",
                narrative_beats=[
                    NarrativeBeat(
                        line="Families first felt the shift in the boring parts of security: comparing repairs, contesting bills, planning care, and seeing which subscriptions or services had quietly become essential.",
                        image_prompt=self._polish_image_prompt(
                            state.config.visual_style,
                            "Painterly households checking bills, repair quotes, care plans, and monthly services across kitchens, buses, apartment hallways, and living rooms.",
                        ),
                    ),
                    NarrativeBeat(
                        line="That did not erase strain, but it moved bargaining power toward people who used to need more money, time, or status just to understand what they were being offered.",
                        image_prompt=self._polish_image_prompt(
                            state.config.visual_style,
                            "A civic domestic montage of households comparing contracts, insurance messages, school options, and care schedules with thick-brushstroke impressionist texture.",
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
        upside = self._clip(
            self._stage_upside_hint(current_stage.world_brief) or "the gains people already like",
            110,
        )
        constituency = self._clip(
            self._stage_constituency_hint(current_stage.world_brief)
            or "the people already benefiting",
            110,
        )
        split = self._clip(
            self._stage_split_hint(current_stage.world_brief)
            or "who captures the gains and who absorbs the risk",
            110,
        )
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
        platform_text = player_platform or " ".join(stage.policy_notes[:6])
        candidates = [
            *self._derive_policy_axes(stage.world_brief),
            self._stage_split_hint(stage.world_brief),
            self._stage_mechanism_hint(stage.world_brief),
        ]
        if "broad-brake" in player_lane:
            contrast_axis = self._pick_diffusion_contrast_axis(candidates, platform_text)
            if contrast_axis:
                return contrast_axis
            return "keep visible gains moving with narrower abuse rules instead of a broad brake"
        contrast_axis = self._pick_contrast_axis(
            candidates,
            platform_text,
        )
        if contrast_axis:
            return contrast_axis
        if "pace-and-diffusion" in player_lane:
            return "tie the next wave to visible household payoff and recourse"
        if "distribution-heavy" in player_lane:
            return "widen access and expand capacity instead of only reallocating the gains after the fact"
        return self._stage_mechanism_hint(stage.world_brief) or (
            "make one visibly different governing move instead of shadowing the player's line"
        )

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

    def _pick_diffusion_contrast_axis(self, candidates: list[str | None], platform_text: str | None) -> str:
        platform_tokens = {token for token in re.findall(r"[a-z]{4,}", (platform_text or "").lower())}
        diffusion_tokens = {
            "access",
            "build",
            "buildout",
            "capacity",
            "competition",
            "competitive",
            "diffusion",
            "diffuse",
            "entrepreneur",
            "innovation",
            "open",
            "prices",
            "price",
            "productivity",
            "small",
            "speed",
        }
        brake_tokens = {
            "ban",
            "cap",
            "freeze",
            "halt",
            "license",
            "licensing",
            "moratorium",
            "pause",
            "regulation",
            "restrict",
            "restriction",
            "tax",
        }
        ranked: list[tuple[int, int, int, int, str]] = []
        for index, candidate in enumerate(candidates):
            cleaned = " ".join(str(candidate or "").split()).strip()
            if not cleaned:
                continue
            lowered = cleaned.lower()
            candidate_tokens = {token for token in re.findall(r"[a-z]{4,}", lowered)}
            diffusion_score = sum(1 for token in diffusion_tokens if token in lowered or token in candidate_tokens)
            brake_score = sum(1 for token in brake_tokens if token in lowered or token in candidate_tokens)
            if diffusion_score <= 0 or brake_score > diffusion_score:
                continue
            overlap = len(candidate_tokens & platform_tokens)
            ranked.append((-diffusion_score, brake_score, overlap, index, cleaned))
        if not ranked:
            return ""
        ranked.sort()
        return ranked[0][4]

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
