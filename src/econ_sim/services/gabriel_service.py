from __future__ import annotations
import contextlib
import hashlib
import importlib
import os
import re
from collections import Counter
from pathlib import Path
from typing import Literal

import pandas as pd
from pydantic import BaseModel

from ..config import AppSettings
from ..models import ApprovalBand, CitizenSnapshot, PollSummary, QueuedPollQuestion, StagePackage, TrackingMetric, StageTracking
from .openai_client import OpenAIGateway

_GABRIEL_MODULE = None
_SERVICE_TIER_SUPPORT: dict[str, bool] = {}


def _gabriel():
    global _GABRIEL_MODULE
    if _GABRIEL_MODULE is None:
        _GABRIEL_MODULE = importlib.import_module("gabriel")
    return _GABRIEL_MODULE

VOICE_PROFILES: dict[str, str] = {
    "alloy": "young professional woman",
    "ash": "30s gravelly man",
    "ballad": "dramatic British gay man",
    "cedar": "serious white-collar male",
    "coral": "bright expressive woman",
    "echo": "mid-pitch monotone male",
    "marin": "friendly woman",
    "sage": "50s gentle mid-pitch woman",
    "shimmer": "deeper steady woman",
    "verse": "upper-pitch fun younger male",
}


class PreparedPollQuestion(BaseModel):
    question: str


class PollQuestionSpec(BaseModel):
    key: str
    question: str
    source: Literal["standard", "advisor", "manual"] = "standard"
    board_label: str | None = None
    board_slot: Literal["capability", "national", "gain", "pressure", "custom"] | None = None


class GabrielService:
    def __init__(self, settings: AppSettings, gateway: OpenAIGateway | None = None):
        self.settings = settings
        self.gateway = gateway
        self.compact_persona_template = Path(__file__).resolve().parent.parent / "prompts" / "persona_compact.jinja2"
        self._quiet_sink = open(os.devnull, "w", encoding="utf-8")

    def _stage_opening(self, stage: StagePackage, max_chars: int = 180) -> str:
        paragraphs = [part.strip() for part in re.split(r"\n\s*\n", str(stage.world_brief or "").strip()) if part.strip()]
        source = paragraphs[0] if paragraphs else str(stage.world_brief or "")
        sentence = re.split(r"(?<=[.!?])\s+", source.strip(), maxsplit=1)[0].strip()
        return self._bounded_text(sentence or source, max_chars)

    def _stage_gain(self, stage: StagePackage, max_chars: int = 180) -> str:
        sentences = [
            sentence.strip()
            for sentence in re.split(r"(?<=[.!?])\s+", str(stage.world_brief or "").strip())
            if sentence.strip()
        ]
        positive_keywords = (
            "cheaper",
            "easier",
            "better",
            "stronger",
            "more capable",
            "more reliable",
            "more abundant",
            "broader",
            "public ai account",
            "machine check",
            "productivity rebate",
            "dividend",
            "household",
            "expertise",
            "public service",
            "school",
            "clinic",
        )
        preferred = next(
            (line for line in sentences if any(keyword in line.lower() for keyword in positive_keywords)),
            "",
        )
        fallback = preferred or self._stage_opening(stage, max_chars)
        return self._bounded_text(fallback, max_chars)

    def _stage_split(self, stage: StagePackage, max_chars: int = 180) -> str:
        sentences = [
            sentence.strip()
            for sentence in re.split(r"(?<=[.!?])\s+", str(stage.world_brief or "").strip())
            if sentence.strip()
        ]
        split_keywords = (
            "who controls",
            "who owns",
            "who gets",
            "platform",
            "toll",
            "queue",
            "priority",
            "bottleneck",
            "power fight",
            "depend",
            "dependency",
            "access",
            "rent",
            "ration",
            "chokepoint",
            "public utility",
            "private",
        )
        source = next(
            (line for line in sentences if any(keyword in line.lower() for keyword in split_keywords)),
            "",
        ) or self._stage_opening(stage, max_chars)
        return self._bounded_text(source, max_chars)

    async def prepare_poll_question(self, question: str) -> str:
        text = " ".join(str(question or "").split()).strip()
        if not text:
            return text
        if self._looks_open_ended_request(text):
            return self._heuristic_poll_question(text)
        if ":" in text and ("choose" in text.lower() or "select" in text.lower()):
            return text
        if self.settings.dummy_openai or self.gateway is None:
            return self._heuristic_poll_question(text)
        try:
            parsed, _ = await self.gateway.parse(
                model=self.settings.poll_model,
                instructions=(
                    "You convert a freeform polling request into one concise survey question for a representative public-opinion poll. "
                    "Prefer closed-ended, choose-one questions with 4 to 6 plain-language answer options inline after a colon. "
                    "If the user clearly wants one-sentence reactions, quotes, or qualitative reasons, preserve that and write a single open-ended prompt asking for one sentence in the plain spoken style of an ordinary voter. "
                    "Make options mutually exclusive, concrete, and understandable to ordinary voters. "
                    "Prefer lived-experience questions about what became newly possible, useful, or still hard in ordinary life. "
                    "Do not default to queues, waits, paperwork, or office-admin phrasing unless the user explicitly asked about that mechanism. "
                    "Keep the question short, neutral, and answerable in one pass. Return only the final poll question."
                    " For the recurring opening battery, prefer questions about what AI can now do, what it still cannot do, what became easier or cheaper in daily life, and what people would miss if it vanished. "
                    "Do not default the first battery to office-admin or queue language when the stage can ask about capability, daily routines, care, school, shopping, or public services instead."
                ),
                input_text=text,
                text_format=PreparedPollQuestion,
                reasoning_effort=self.settings.poll_reasoning_effort,
                prompt_cache_key="econ-sim:poll-question",
                max_output_tokens=180,
                verbosity="low",
            )
            return self._heuristic_poll_question(parsed.question)
        except Exception:
            return self._heuristic_poll_question(text)

    async def ensure_personas(
        self,
        *,
        simulation_id: str,
        population_description: str,
        persona_count: int,
        save_dir: Path,
    ) -> pd.DataFrame:
        if self.settings.dummy_openai:
            return self._dummy_personas(population_description, persona_count)

        try:
            result = await self._call_gabriel(
                "poll",
                population_description=population_description,
                questions=[],
                save_dir=str(save_dir),
                model=self.settings.poll_model,
                num_personas=persona_count,
                entities_per_generation=min(persona_count, 24),
                n_questions_per_run=self.settings.poll_questions_per_run,
                reasoning_effort=self.settings.poll_reasoning_effort,
                persona_template_path=str(self.compact_persona_template),
                reset_files=False,
            )
        except Exception as exc:
            partial_path = save_dir / "poll_personas.csv"
            if not partial_path.exists():
                raise
            if "persona" not in str(exc).lower():
                raise
            result = pd.read_csv(partial_path)
        result = self._sanitize_personas_frame(
            result,
            simulation_id=simulation_id,
            population_description=population_description,
            persona_count=persona_count,
        )
        if "seed_id" not in result.columns:
            result["seed_id"] = [f"{simulation_id}-citizen-{idx:03d}" for idx in range(len(result))]
        if "voice" not in result.columns:
            result = await self._calibrate_personas(result, save_dir / "persona_calibration")
        return result

    async def update_personas_for_stage(
        self,
        *,
        personas: pd.DataFrame,
        stage: StagePackage,
        incumbent_name: str,
        player_name: str,
        opponent_name: str,
        save_dir: Path,
    ) -> pd.DataFrame:
        if self.settings.dummy_openai:
            return self._dummy_updates(personas, stage, incumbent_name)

        prompts = []
        identifiers = []
        persona_ids = personas["seed_id"].astype(str).tolist()
        stage_capsule = self._stage_capsule(stage)
        for idx, row in personas.iterrows():
            prompt = (
                "You are updating one synthetic citizen during an AGI transition simulation.\n"
                "Return valid JSON with keys: display_name, role, region, mood, ai_exposure, household, daily_routine, recent_ai_moment, current_worries, current_hopes, speech_habits, voice_notes, town_hall_question, town_hall_cue, support_score, summary, current_update.\n\n"
                f"Stage phase: {stage.phase_label}\n"
                f"Stage title: {stage.title}\n"
                f"Stage capsule:\n{stage_capsule}\n"
                f"Citizen seed: {row.get('seed', row.get('entity', ''))}\n"
                f"Citizen persona: {row.get('persona', '')}\n"
                f"Household: {self._row_value(row, 'household', 'Household')}\n"
                f"Daily routine: {self._row_value(row, 'daily_routine', 'Daily routine')}\n"
                f"Recent AI moment: {self._row_value(row, 'recent_ai_moment', 'Recent AI moment')}\n"
                f"Current worries: {self._row_value(row, 'current_worries', 'Current worries')}\n"
                f"Current hopes: {self._row_value(row, 'current_hopes', 'Current hopes')}\n"
                f"Speech habits: {self._row_value(row, 'speech_habits', 'Speech habits')}\n"
                f"Voice notes: {self._row_value(row, 'voice_notes', 'Voice notes')}\n"
                f"Baseline AI instinct: {row.get('baseline_ai_instinct', '')}\n"
                f"Baseline protected priority: {row.get('baseline_priority', '')}\n"
                f"Previous stage update: {row.get('current_update', '') or 'none yet'}\n"
                f"Political backdrop only if locally salient: incumbent {incumbent_name}; player candidate {player_name}; opponent candidate {opponent_name}\n"
                "Write the one or two changes this person would bring up first about how life feels now.\n"
                "Treat the stage capsule as the world memo. Most of your job is to let this person live inside that memo in plain language.\n"
                "Before you write, decide three things: what now pays this person's bills, what account, employer, agency, or platform they depend on most, and what concrete moment from this week they would mention first.\n"
                "Keep the writing lived-in, uneven, and ordinary, as if someone were answering quickly in their own words.\n"
                "Constraints:\n"
                "- summary must be exactly one sentence, about 22-38 words, written in close third person as a natural human blurb\n"
                "- current_update must usually be 1 sentence or 2 clipped first-person sentences; target about 24-60 words total\n"
                "- town_hall_question must be the one direct question this person would ask a candidate if handed a microphone today; usually 1 sentence, sometimes 2 short ones\n"
                "- town_hall_question should sound like a real person in a diner, clinic, break room, school pickup line, or church basement, not a staff writer or moderator\n"
                "- town_hall_cue should be one short backstage note of about 4-10 words capturing the pressure behind the question\n"
                "- household, daily_routine, current_worries, current_hopes, speech_habits, and voice_notes should stay compact, specific, and human\n"
                "- preserve identity and relationships, but do not preserve a 2026 routine by inertia if the stage says the way life now works changed\n"
                "- if the stage describes a changed social or economic arrangement, rewrite household, daily_routine, recent_ai_moment, and current_update so this person actually lives inside the new money, access, time-use, and dependency system\n"
                "- not everyone should talk about AI directly; many people would talk first about the account that pays them, the service that improved, the platform toll, the school day that changed, the robot depot at the edge of town, the local outage, the new leisure routine, or the family bargain\n"
                "- vary the lead arena across the population: home or family, school or care, work or business, local service, neighborhood or status, or barely touched yet\n"
                "- vary the mood across the population: some people should sound pleased, relieved, proud, pragmatic, skeptical, angry, or mostly untouched\n"
                "- do not let everyone collapse into the same office-work story, helper-app story, or grievance story\n"
                "- use plain spoken language, contractions when natural, and one small idiosyncratic turn of phrase this person would actually repeat\n"
                "- avoid policy jargon, consultant phrasing, slogans, tidy both-sides wrapups, and over-explaining the technology\n"
                "- support_score must be an integer from 0 to 100 measuring support for the current incumbent"
            )
            prompts.append(prompt)
            identifiers.append(persona_ids[idx])

        prompt_df = pd.DataFrame({"seed_id": identifiers, "prompt": prompts})
        result = await self._call_gabriel(
            "whatever",
            df=prompt_df,
            column_name="prompt",
            identifier_column="seed_id",
            save_dir=str(save_dir),
            model=self.settings.persona_update_model,
            reasoning_effort=self.settings.persona_update_reasoning_effort,
            json_mode=True,
            return_original_columns=True,
            drop_prompts=False,
            reset_files=True,
        )

        json_column = "Response JSON" if "Response JSON" in result.columns else "Response"
        response_lookup = dict(zip(result["seed_id"], result[json_column]))
        out = personas.copy()
        rows: list[dict] = []
        for seed_id in out["seed_id"].astype(str).tolist():
            payload = response_lookup.get(seed_id) or {}
            if not isinstance(payload, dict):
                payload = {}
            rows.append(payload)
        update_df = pd.DataFrame(rows)
        stage_prefix = f"stage_{stage.index + 1}"
        for key in update_df.columns:
            out[f"{stage_prefix}_{key}"] = update_df[key]

        def combine_text_field(*keys: str) -> pd.Series:
            combined = pd.Series([""] * len(out), index=out.index, dtype="object")
            for key in keys:
                if key in out:
                    baseline = out[key].fillna("").astype(str).map(lambda value: " ".join(value.split()).strip())
                    combined = combined.mask(combined.eq(""), baseline)
            for key in keys:
                if key in update_df:
                    fresh = update_df[key].fillna("").astype(str).map(lambda value: " ".join(value.split()).strip())
                    combined = combined.mask(fresh.ne(""), fresh)
            return combined

        support_scores = pd.to_numeric(update_df.get("support_score", 50), errors="coerce").fillna(50).clip(lower=0, upper=100)
        out["current_update"] = update_df.get("current_update", "").fillna("").map(lambda value: self._bounded_text(value, 650))
        out["support_score"] = support_scores.astype(int)
        out["approval_band"] = support_scores.map(self._approval_band_from_score)
        out["display_name"] = update_df.get("display_name", "").fillna("")
        out["role"] = update_df.get("role", "").fillna("")
        out["region"] = update_df.get("region", "").fillna("")
        out["mood"] = update_df.get("mood", "").fillna("")
        out["ai_exposure"] = update_df.get("ai_exposure", "").fillna("")
        out["household"] = combine_text_field("household", "Household").map(lambda value: self._bounded_text(value, 140))
        out["daily_routine"] = combine_text_field("daily_routine", "Daily routine").map(lambda value: self._bounded_text(value, 160))
        out["recent_ai_moment"] = combine_text_field("recent_ai_moment", "Recent AI moment").map(lambda value: self._bounded_text(value, 180))
        out["current_worries"] = combine_text_field("current_worries", "Current worries").map(lambda value: self._bounded_text(value, 160))
        out["current_hopes"] = combine_text_field("current_hopes", "Current hopes").map(lambda value: self._bounded_text(value, 160))
        out["speech_habits"] = combine_text_field("speech_habits", "Speech habits").map(lambda value: self._bounded_text(value, 120))
        out["voice_notes"] = combine_text_field("voice_notes", "Voice notes").map(lambda value: self._bounded_text(value, 80))
        out["town_hall_question"] = combine_text_field("town_hall_question", "Town hall question").map(lambda value: self._bounded_text(value, 220))
        out["town_hall_cue"] = combine_text_field("town_hall_cue", "Town hall cue").map(lambda value: self._bounded_text(value, 120))
        out["support_label"] = support_scores.map(self._support_label_from_score)
        out["summary"] = update_df.get("summary", "").fillna("").map(lambda value: self._bounded_text(value, 220))
        return out

    def _stage_capsule(self, stage: StagePackage) -> str:
        paragraphs = [part.strip() for part in re.split(r"\n\s*\n", str(stage.world_brief or "").strip()) if part.strip()]
        macro_lines = paragraphs[:2]
        background_lines = paragraphs[2:4]
        capability_line = self._stage_opening(stage, 180)
        upside_line = self._stage_gain(stage, 180)
        unresolved_line = self._stage_split(stage, 180)
        memo_parts: list[str] = []
        if macro_lines:
            memo_parts.append("\n\n".join(macro_lines[:2]))
        elif capability_line:
            memo_parts.append(capability_line)

        notes: list[str] = []
        if upside_line:
            notes.append(f"Useful thing people may defend: {upside_line}")
        if unresolved_line:
            notes.append(f"Live strain or unfairness: {unresolved_line}")
        if background_lines:
            notes.append(f"One other current in the background: {background_lines[0]}")
        if not notes:
            notes.append("Keep the update grounded in one concrete routine, bill, dependence, or relief this person would mention first.")

        memo_parts.append(
            "Keep this person inside that world in plain language. "
            "Start from what pays their bills, what service or platform they depend on, what changed in a normal week, and one concrete thing they would mention first.\n"
            + "\n".join(f"- {line}" for line in notes[:3])
        )
        return "\n\n".join(part.strip() for part in memo_parts if part.strip())

    async def run_tracking_polls(
        self,
        *,
        personas: pd.DataFrame,
        stage_index: int,
        stage: StagePackage | None,
        player_name: str,
        opponent_name: str,
        save_dir: Path,
        extra_questions: list[QueuedPollQuestion],
    ) -> tuple[pd.DataFrame, list[PollSummary], StageTracking]:
        standard_specs = [
            *self._core_question_specs(player_name, opponent_name, stage),
            *(self._stage_question_specs(stage) if stage is not None else []),
        ]
        extra_specs = self._queued_poll_specs(extra_questions)
        questions: list[str] = []
        seen: set[str] = set()
        question_specs: list[PollQuestionSpec] = []
        for spec in [*standard_specs, *extra_specs]:
            normalized = " ".join(str(spec.question).lower().split())
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            questions.append(spec.question)
            question_specs.append(spec)
        if self.settings.dummy_openai:
            result = self._dummy_poll_answers(personas, questions, player_name, opponent_name)
        else:
            result = await self._call_gabriel(
                "poll",
                df=personas,
                questions=questions,
                column_name="seed",
                save_dir=str(save_dir),
                model=self.settings.poll_model,
                n_questions_per_run=self.settings.poll_questions_per_run,
                reasoning_effort=self.settings.poll_reasoning_effort,
                reset_files=True,
            )
        summaries = [
            self._summarize_question(
                result,
                spec.question,
                key=spec.key,
                source=spec.source,
                board_label=spec.board_label,
                board_slot=spec.board_slot,
            )
            for spec in question_specs
        ]
        tracking = self._tracking_from_summaries(summaries, player_name, opponent_name)
        return result, summaries, tracking

    async def run_extra_polls(
        self,
        *,
        personas: pd.DataFrame,
        questions: list[QueuedPollQuestion],
        save_dir: Path,
    ) -> tuple[pd.DataFrame, list[PollSummary]]:
        question_specs = self._queued_poll_specs(questions)
        deduped_questions = [spec.question for spec in question_specs]
        if not deduped_questions:
            return personas, []
        if self.settings.dummy_openai:
            result = self._dummy_poll_answers(personas, deduped_questions, "", "")
        else:
            result = await self._call_gabriel(
                "poll",
                df=personas,
                questions=deduped_questions,
                column_name="seed",
                save_dir=str(save_dir),
                model=self.settings.poll_model,
                n_questions_per_run=self.settings.poll_questions_per_run,
                reasoning_effort=self.settings.poll_reasoning_effort,
                reset_files=True,
            )
        summaries = [
            self._summarize_question(
                result,
                spec.question,
                key=spec.key,
                source=spec.source,
                board_label=spec.board_label,
                board_slot=spec.board_slot,
            )
            for spec in question_specs
        ]
        return result, summaries

    def tracking_from_summaries(
        self,
        summaries: list[PollSummary],
        *,
        player_name: str,
        opponent_name: str,
    ) -> StageTracking:
        return self._tracking_from_summaries(summaries, player_name, opponent_name)

    def pick_sample_citizens(self, personas: pd.DataFrame, stage: StagePackage | None = None, limit: int = 6) -> list[CitizenSnapshot]:
        selected_frames: list[pd.DataFrame] = []
        remaining = personas.copy()
        if remaining.empty:
            records = []
        else:
            remaining = remaining.copy()
            remaining["support_score"] = pd.to_numeric(remaining.get("support_score", 50), errors="coerce").fillna(50)
            remaining["approval_band"] = remaining.get("approval_band", "mixed").astype(str).str.strip().str.lower()
            remaining["stage_relevance"] = remaining.apply(lambda row: self._stage_relevance_score(stage, row), axis=1) if stage is not None else 0
            remaining["selection_exposure"] = remaining.apply(self._selection_exposure_bucket, axis=1)
            remaining["selection_instinct"] = remaining.apply(self._selection_instinct_bucket, axis=1)
            remaining["support_distance"] = (remaining["support_score"] - 50).abs()

            def take_slice(frame: pd.DataFrame, count: int = 1, *, ascending: list[bool] | None = None) -> None:
                nonlocal remaining, selected_frames
                if count <= 0 or frame.empty:
                    return
                sort_ascending = ascending or [False, True, True]
                ordered = frame.sort_values(["stage_relevance", "support_distance", "seed_id"], ascending=sort_ascending)
                picked = ordered.head(count)
                if picked.empty:
                    return
                selected_frames.append(picked)
                remaining = remaining[~remaining["seed_id"].isin(picked["seed_id"])]

            if stage is not None:
                themed = remaining[remaining["stage_relevance"] > 0]
                take_slice(themed, count=min(2, limit), ascending=[False, True, True])

            for band in ("approve", "mixed", "disapprove"):
                if sum(len(frame) for frame in selected_frames) >= limit:
                    break
                band_df = remaining[remaining["approval_band"] == band]
                if band in {"approve", "disapprove"}:
                    take_slice(band_df, ascending=[False, False, True])
                else:
                    take_slice(band_df)

            for bucket in ("low", "high", "medium"):
                if sum(len(frame) for frame in selected_frames) >= limit:
                    break
                take_slice(remaining[remaining["selection_exposure"] == bucket])

            for bucket in ("upside", "guarded", "detached"):
                if sum(len(frame) for frame in selected_frames) >= limit:
                    break
                take_slice(remaining[remaining["selection_instinct"] == bucket])

            if sum(len(frame) for frame in selected_frames) < limit and not remaining.empty:
                take_slice(remaining, count=limit - sum(len(frame) for frame in selected_frames))

            if selected_frames:
                records = pd.concat(selected_frames, ignore_index=True).drop_duplicates(subset=["seed_id"]).head(limit).to_dict(orient="records")
            else:
                records = personas.head(limit).to_dict(orient="records")
        citizens: list[CitizenSnapshot] = []
        for row in records:
            band = str(row.get("approval_band", "mixed")).strip().lower()
            approval_band = ApprovalBand.approve if band == "approve" else ApprovalBand.disapprove if band == "disapprove" else ApprovalBand.mixed
            citizens.append(
                CitizenSnapshot(
                    citizen_id=str(row.get("seed_id")),
                    display_name=str(row.get("display_name") or "Unnamed Citizen"),
                    role=str(row.get("role") or "Resident"),
                    region=str(row.get("region") or "United States"),
                    voice=str(row.get("voice") or self._voice_for_seed(str(row.get("seed_id") or row.get("seed") or ""))),
                    support_label=str(row.get("support_label") or self._support_label_from_score(int(row.get("support_score") or 50))),
                    mood=str(row.get("mood") or "Uneasy"),
                    ai_exposure=str(row.get("ai_exposure") or "Medium"),
                    household=self._row_value(row, "household", "Household"),
                    daily_routine=self._row_value(row, "daily_routine", "Daily routine"),
                    recent_ai_moment=self._row_value(row, "recent_ai_moment", "Recent AI moment"),
                    current_worries=self._row_value(row, "current_worries", "Current worries"),
                    current_hopes=self._row_value(row, "current_hopes", "Current hopes"),
                    speech_habits=self._row_value(row, "speech_habits", "Speech habits"),
                    voice_notes=self._row_value(row, "voice_notes", "Voice notes"),
                    baseline_ai_instinct=str(row.get("baseline_ai_instinct") or ""),
                    baseline_priority=str(row.get("baseline_priority") or ""),
                    town_hall_question=self._row_value(row, "town_hall_question", "Town hall question"),
                    town_hall_cue=self._row_value(row, "town_hall_cue", "Town hall cue"),
                    summary=str(row.get("summary") or row.get("persona") or ""),
                    current_update=str(row.get("current_update") or ""),
                    approval_band=approval_band,
                    support_score=int(row.get("support_score") or 50),
                )
            )
        return citizens

    def _selection_exposure_bucket(self, row: pd.Series) -> str:
        text = " ".join(
            str(row.get(key) or "")
            for key in ("ai_exposure", "current_update", "summary", "baseline_ai_instinct")
        ).lower()
        if any(token in text for token in ("not much yet", "barely", "hardly", "indirect", "low", "little")):
            return "low"
        if any(token in text for token in ("high", "constant", "all day", "every day", "embedded", "heavy")):
            return "high"
        return "medium"

    def _selection_instinct_bucket(self, row: pd.Series) -> str:
        text = " ".join(
            str(row.get(key) or "")
            for key in ("baseline_ai_instinct", "current_update", "summary", "mood")
        ).lower()
        if any(token in text for token in ("optimistic", "curious", "first in line", "excited", "hopeful", "pleased", "relieved")):
            return "upside"
        if any(token in text for token in ("skeptical", "protective", "guarded", "fear", "worried", "angry", "resentful")):
            return "guarded"
        return "detached"

    def standard_questions(self, player_name: str, opponent_name: str, stage: StagePackage | None = None) -> list[str]:
        base = [spec.question for spec in self._core_question_specs(player_name, opponent_name, stage)]
        if stage is None:
            return base
        return [*base, *self._stage_questions(stage)]

    def _custom_poll_key(self, question: str) -> str:
        normalized = " ".join(str(question or "").lower().split())
        digest = hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:10]
        return f"custom:{digest}"

    def _stage_question_specs(self, stage: StagePackage) -> list[PollQuestionSpec]:
        return [
            PollQuestionSpec(
                key=self._custom_poll_key(question),
                question=question,
                source="standard",
            )
            for question in self._stage_questions(stage)
        ]

    def _queued_poll_specs(self, questions: list[QueuedPollQuestion] | list[str], *, default_source: Literal["advisor", "manual"] = "manual") -> list[PollQuestionSpec]:
        specs: list[PollQuestionSpec] = []
        seen: set[str] = set()
        for entry in questions:
            if isinstance(entry, QueuedPollQuestion):
                question = entry.question
                source = entry.source
            else:
                question = str(entry)
                source = default_source
            normalized = " ".join(question.lower().split())
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            specs.append(
                PollQuestionSpec(
                    key=self._custom_poll_key(question),
                    question=question,
                    source=source,
                    board_slot="custom",
                )
            )
        return specs

    def _poll_stage_context(self, stage: StagePackage | None) -> str:
        if stage is None:
            return ""
        parts = [
            f"Opening read: {' '.join(self._stage_opening(stage, 180).split())}",
            f"Main gain: {' '.join(self._stage_gain(stage, 160).split())}",
            f"Main split: {' '.join(self._stage_split(stage, 160).split())}",
        ]
        if not parts:
            return ""
        context = " ".join(parts)
        if len(context) > 720:
            context = context[:720].rsplit(" ", 1)[0].rstrip()
        return (
            f"Stage context for this respondent: {context}. "
            "Answer from your own persona's lived situation in this described world, not from today's baseline. "
        )

    def _core_question_specs(self, player_name: str, opponent_name: str, stage: StagePackage | None = None) -> list[PollQuestionSpec]:
        context = self._poll_stage_context(stage)

        def q(question: str) -> str:
            return f"{context}{question}" if context else question

        return [
            PollQuestionSpec(key="capability_read", board_label="Capability now", board_slot="capability", question=q("Choose one: right now AI mostly feels able to handle broad computer work and guided decisions, deliver strong expert-style help across daily life, take over major end-to-end work streams, reshape ordinary institutions, only handle narrow assistant tasks, or still not much reliably yet.")),
            PollQuestionSpec(key="national_effect", board_label="National read", board_slot="national", question=q("Choose one: the biggest national effect of AI right now feels like broader productive capacity, cheaper or better services, new household security, more leverage for smaller organizations, more power for big firms, geopolitical pressure, or still too uneven to judge.")),
            PollQuestionSpec(key="trusted_task", question=q("In one sentence, what is one task or service you now trust AI to handle in this stage of the world, and what makes it feel reliable enough now?")),
            PollQuestionSpec(key="still_human", board_label="Still human", question=q("In one sentence, what still clearly needs a person, trust, physical handling, local judgment, or moral responsibility in your life?")),
            PollQuestionSpec(key="ai_gain", board_label="Best gain", board_slot="gain", question=q("In one sentence, what part of life has gotten noticeably easier, cheaper, or better because of AI, and what new abundance or possibility would you miss if it vanished?")),
            PollQuestionSpec(key="keep_change", board_label="People keep", board_slot="gain", question=q("In one sentence, what AI-enabled change in daily life would you hate to lose right now because it made something more possible, cheaper, or easier?")),
            PollQuestionSpec(key="new_capability", question=q("In one sentence, what can AI now help you do that used to require more time, money, expertise, institutional access, or staff than you had?")),
            PollQuestionSpec(key="newly_normal", question=q("In one sentence, what has quietly become normal because capable software or machine labor is now in the background around you?")),
            PollQuestionSpec(key="barely_notice", question=q("In one sentence, where do you still barely notice AI or automation in your own life, and what still feels basically ordinary?")),
            PollQuestionSpec(key="main_pressure", board_label="Main pressure", board_slot="pressure", question=q("In one sentence, what change from AI is most shaping your life right now, for better or worse, and how does it actually show up?")),
            PollQuestionSpec(key="daily_role", board_label="Daily role", question=q("Choose one: in your life right now, AI feels mostly like a useful convenience, a stronger work or study tool, a way to cross old skill boundaries, a background service layer, a source of household income or bargaining power, a risk you are watching, or not much yet.")),
            PollQuestionSpec(key="life_touchpoint", board_label="Where it lands", question=q("Choose one: AI is touching your life most through work tasks, shopping or bills, school or learning, medical or care coordination, entertainment or search, travel or planning, household organization, public services, news or scams, infrastructure costs, or not much yet.")),
            PollQuestionSpec(key="expertise_access", question=q("Choose one: compared with life before this stage of AI, useful expertise now feels much easier to access, somewhat easier, about the same, more confusing, unevenly rationed, or no more available than before.")),
            PollQuestionSpec(key="who_controls_access", board_label="Access control", board_slot="pressure", question=q("Choose one: the main thing controlling who benefits from AI now is public access, private platform rules, employer decisions, household money, local infrastructure, compute or energy supply, ownership shares, or still mostly personal skill.")),
            PollQuestionSpec(key="time_use", board_label="Time use", question=q("In one sentence, what changed most about how people around you spend time during a normal week?")),
            PollQuestionSpec(key="machine_income_attitude", board_label="Income bargain", question=q("Choose one: if machines are doing more of the productive work, including software agents where relevant, the gains should mostly show up as cheaper essentials, public payments, worker or citizen ownership shares, lower taxes, faster national buildout, universal public access, or private company profits.")),
            PollQuestionSpec(key="education_shift", question=q("Choose one: in school or learning around you, AI mostly feels like better tutoring and faster mastery, new kinds of projects, easier cheating and shortcuts, stronger tools with unclear rules, less need for old credentials, not much change, or not relevant to my life.")),
            PollQuestionSpec(key="pace_read", question=q("Choose one: around you, AI adoption feels too slow to deliver the gains, about right, a bit too fast, much too fast, unevenly rationed, or hardly visible yet.")),
            PollQuestionSpec(key="better_off", question=q("Compared with life before this AI stage, your household feels much better off, somewhat better off, about the same, somewhat worse off, or much worse off because of this wave.")),
            PollQuestionSpec(key="econ_read", question=q("Choose one: around you, the economy feels more abundant and capable, mixed but functioning, split between winners and losers, bottlenecked by scarce inputs, weaker, or stalled.")),
            PollQuestionSpec(key="service_reliability", question=q("Choose one: compared with life before this AI stage, everyday services now feel more reliable and more capable, faster but less trusted, cheaper but more confusing, abundant but rationed, not much different, or harder to trust.")),
            PollQuestionSpec(key="ai_comfort", board_label="AI comfort", question=q("How comfortable do you feel with AI showing up in work, services, institutions, and daily routines: very comfortable, somewhat comfortable, mixed, somewhat uncomfortable, or very uncomfortable?")),
            PollQuestionSpec(key="job_worry", board_label="Job strain", question=q("How worried are you about job loss, income disruption, status loss, or bargaining-power disruption from AI: not worried, slightly worried, mixed, worried, or very worried?")),
            PollQuestionSpec(key="public_stability", board_label="Daily life", question=q("Choose one: compared with life before this AI stage, daily life around you feels more capable and convenient, somewhat better, mixed, somewhat more strained, or much more strained.")),
            PollQuestionSpec(key="household_security", board_label="Household read", question=q("Choose one: over the next year, your household finances feel very secure, somewhat secure, mixed, somewhat insecure, or very insecure, including access to essential services if that now matters in your life.")),
            PollQuestionSpec(key="biggest_worry", board_label="Top worry", board_slot="pressure", question=q("Choose one: when you think about AI right now, which issue most needs attention first: job or income security, scams or misinformation, human control and safety, concentration of power, keeping the gains broad, compute or energy access, international competition, or something else?")),
            PollQuestionSpec(key="fairness", question=q("In one sentence, what feels fairest or most unfair about how the gains and disruptions from AI are being shared where you live?")),
            PollQuestionSpec(key="next_two_years", question=q("Choose one: over the next two years, who most needs to benefit more clearly from AI for the country to feel on the right track: ordinary households, exposed workers, small local businesses, public services, large national firms, students and families, or no one clearly yet?")),
            PollQuestionSpec(key="gov_trust", board_label="Transition trust", question=q("How much do you trust the public authorities shaping access, buildout, and guardrails in this AI transition: high trust, some trust, mixed, low trust, or no trust?")),
            PollQuestionSpec(key="approval", board_label="Handling it?", question=q("Choose one: strongly approve, somewhat approve, mixed, somewhat disapprove, or strongly disapprove of how the current administration is handling this AI transition.")),
            PollQuestionSpec(key="vote", board_label="Vote today", question=q(f"If the election were held today, would you vote for {player_name}, {opponent_name}, or remain undecided, based on who seems more likely to widen gains and handle the risks?")),
            PollQuestionSpec(key="vote_reason", question=q(f"In one sentence, why would you vote for {player_name}, {opponent_name}, or stay undecided in this AI transition?")),
        ]

    def _heuristic_poll_question(self, question: str) -> str:
        text = " ".join(question.split()).strip()
        lower = text.lower()
        if self._looks_open_ended_request(text):
            return self._spoken_open_ended_poll_question(text)
        if ":" in text and ("choose" in lower or "select" in lower or " or " in lower or "," in text):
            return text
        if "angriest to lose" in lower or "most want to keep" in lower or "benefit" in lower:
            return (
                "Choose one: the AI change you most want to keep right now is stronger work or study tools, "
                "cheaper help with planning or errands, better medical or care coordination, better tutoring or learning help, "
                "stronger tools for small businesses or creators, or none of these."
            )
        if "worr" in lower or "fear" in lower:
            return (
                "Choose one: your biggest worry about AI right now is job loss, scams or misinformation, loss of human control, "
                "big companies getting too powerful, other countries pulling ahead, or something else."
            )
        if "vote" in lower or "approval" in lower:
            return text
        return (
            f"Choose one: {text[0].lower() + text[1:] if len(text) > 1 else text.lower()} "
            "very positive, somewhat positive, mixed, somewhat negative, or very negative?"
        )

    def _spoken_open_ended_poll_question(self, question: str) -> str:
        text = " ".join(question.split()).strip().rstrip("?.")
        lower = text.lower()
        replacements = (
            ("what do people say as a short quote about ", "what would you say about "),
            ("what do people say in one sentence about ", "what would you say about "),
            ("what do people say about ", "what would you say about "),
            ("give me a brief reaction on whether ", "what's your reaction to whether "),
            ("give me a brief reaction about ", "what's your reaction to "),
            ("why are people ", "why are you "),
            ("why do people ", "why do you "),
        )
        for prefix, replacement in replacements:
            if lower.startswith(prefix):
                stem = replacement + text[len(prefix) :]
                stem = re.sub(r"(?:\s+in one sentence|\s+as a short quote)$", "", stem, flags=re.IGNORECASE).strip(" ,")
                return self._normalize_quote_text(f"In one sentence, {self._lower_first(stem)}", terminal="?")
        if lower.startswith(("in one sentence", "one sentence", "briefly")):
            return self._normalize_quote_text(text, terminal="?")
        stem = self._lower_first(text)
        return self._normalize_quote_text(f"In one sentence, {stem}", terminal="?")

    def _lower_first(self, text: str) -> str:
        cleaned = str(text or "").strip()
        if len(cleaned) <= 1:
            return cleaned.lower()
        return cleaned[0].lower() + cleaned[1:]

    def _dummy_personas(self, population_description: str, persona_count: int) -> pd.DataFrame:
        base_people = [
            ("Jordan Pike", "Warehouse supervisor in Ohio juggling mortgage risk and a daughter in community college."),
            ("Maya Alvarez", "Nurse practitioner in Phoenix using AI triage tools while supporting her parents."),
            ("DeShawn Carter", "Long-haul driver in Georgia watching autonomous logistics squeeze margins."),
            ("Priya Raman", "Product manager in Seattle thriving on AI copilots but worried about social fragmentation."),
            ("Evelyn Brooks", "Retired teacher in Michigan benefiting from AI health assistants but distrustful of deepfakes."),
            ("Luis Ortega", "Small manufacturer in Texas racing to automate before overseas rivals undercut him."),
        ]
        rows = []
        for idx in range(persona_count):
            name, seed = base_people[idx % len(base_people)]
            rows.append(
                {
                    "seed_id": f"seed-{idx:03d}",
                    "seed": seed,
                    "voice": self._voice_for_seed(f"seed-{idx:03d}"),
                    "baseline_ai_instinct": ["optimistic but watchful", "cautiously curious", "protective and skeptical", "first in line"][idx % 4],
                    "baseline_priority": ["income and opportunity", "family care and time", "national competitiveness", "personal autonomy"][idx % 4],
                    "persona": (
                        f"{name} is part of a representative US sample. {seed} "
                        f"They live through the AGI transition with realistic household, labor, and community constraints. "
                        f"Population frame: {population_description}"
                    ),
                }
            )
        return pd.DataFrame(rows)

    def _sanitize_personas_frame(
        self,
        personas: pd.DataFrame,
        *,
        simulation_id: str,
        population_description: str,
        persona_count: int,
    ) -> pd.DataFrame:
        out = personas.copy()
        if "seed_id" not in out.columns:
            out["seed_id"] = [f"{simulation_id}-citizen-{idx:03d}" for idx in range(len(out))]
        else:
            out["seed_id"] = [
                str(value).strip() or f"{simulation_id}-citizen-{idx:03d}"
                for idx, value in enumerate(out["seed_id"].tolist())
            ]
        if "seed" not in out.columns:
            out["seed"] = [""] * len(out)
        if "persona" not in out.columns:
            out["persona"] = [""] * len(out)

        for idx, row in out.iterrows():
            raw_seed = row.get("seed")
            raw_persona = row.get("persona")
            seed = "" if pd.isna(raw_seed) else " ".join(str(raw_seed or "").split()).strip()
            persona = "" if pd.isna(raw_persona) else " ".join(str(raw_persona or "").split()).strip()
            if not seed and persona:
                seed = persona
            if not seed:
                seed = self._fallback_persona_seed(idx, population_description)
            if not persona:
                persona = self._fallback_persona_text(idx, seed, population_description)
            out.at[idx, "seed"] = seed
            out.at[idx, "persona"] = persona

        if len(out) < persona_count:
            filler_rows = []
            for idx in range(len(out), persona_count):
                seed = self._fallback_persona_seed(idx, population_description)
                filler_rows.append(
                    {
                        "seed_id": f"{simulation_id}-citizen-{idx:03d}",
                        "seed": seed,
                        "persona": self._fallback_persona_text(idx, seed, population_description),
                    }
                )
            out = pd.concat([out, pd.DataFrame(filler_rows)], ignore_index=True)
        elif len(out) > persona_count:
            out = out.head(persona_count).copy()

        return out.reset_index(drop=True)

    def _fallback_persona_seed(self, index: int, population_description: str) -> str:
        return (
            f"Resident {index + 1} drawn from {population_description}. "
            "They reflect an ordinary household, work, and community position in this population."
        )

    def _fallback_persona_text(self, index: int, seed: str, population_description: str) -> str:
        return (
            f"Citizen {index + 1} is part of a representative population sample. {seed} "
            f"They live through the AGI transition with realistic household, labor, and community constraints. "
            f"Population frame: {population_description}"
        )

    def _dummy_updates(self, personas: pd.DataFrame, stage: StagePackage, incumbent_name: str) -> pd.DataFrame:
        out = personas.copy()
        summary_templates = [
            "Warehouse supervisor in Ohio who feels the floor got smoother and is watching whether the gain reaches the people doing the work.",
            "Phoenix nurse practitioner who saves real time with AI triage and wants the staffing to stay human enough to trust.",
            "Georgia long-haul driver who likes the cleaner dispatch and worries the leverage still runs uphill to the company.",
            "Seattle product manager who is using AI tools every day and arguing the country should spread the gains wider, not slower.",
            "Michigan retired teacher who trusts AI health helpers for daily life but still worries about fake media and soft trust.",
            "Texas small manufacturer using automation to stay competitive and wanting smaller firms to get the same shot at the good tools.",
        ]
        update_templates = [
            "The floor runs smoother now, and that part is real, because the system catches dumb delays before they snowball. I still want to know who actually keeps the upside.",
            "AI triage is saving me real minutes every shift, which means fewer pointless delays for patients and a little more energy left when I get home. I do not want that to become an excuse to thin the staff.",
            "Dispatch is sharper and some routes are less chaotic, so I get why companies want more of this. I still want to know whether drivers get any leverage out of it.",
            "The tools at work are genuinely good now, and I would miss them if they vanished. The question is whether this spreads or just becomes another insiders-only advantage.",
            "My health app is faster than the clinic half the time, and I am not eager to go back to the old waiting game. What keeps me tense is how easy it is to fake the whole thing.",
            "The new automation lets me quote jobs faster and waste less material, which is the kind of edge a small shop usually never gets. I just do not want the biggest firms owning the good version of the future.",
        ]
        display_names = []
        roles = []
        regions = []
        moods = []
        exposures = []
        labels = []
        approvals = []
        scores = []
        summaries = []
        updates = []
        for idx, row in out.iterrows():
            digest = int(hashlib.sha1(f"{stage.index}:{row['seed_id']}".encode("utf-8")).hexdigest()[:8], 16)
            support_score = 35 + (digest % 35)
            display_names.append(str(row.get("persona", "Citizen")).split(" is ")[0][:24] or f"Citizen {idx + 1}")
            roles.append(["operator", "teacher", "nurse", "manager", "driver", "owner"][idx % 6])
            regions.append(["Midwest", "Southwest", "South", "Pacific", "Great Lakes", "Texas"][idx % 6])
            moods.append(["wary", "hopeful", "frayed", "curious", "tense", "pragmatic"][idx % 6])
            exposures.append(["high", "medium", "high", "very high", "medium", "high"][idx % 6])
            labels.append(
                self._support_label_from_score(support_score)
            )
            approval_band = self._approval_band_from_score(support_score)
            approvals.append(approval_band)
            scores.append(support_score)
            summaries.append(summary_templates[idx % len(summary_templates)])
            updates.append(update_templates[idx % len(update_templates)])
        out["display_name"] = display_names
        out["role"] = roles
        out["region"] = regions
        out["voice"] = [self._voice_for_seed(str(seed_id)) for seed_id in out["seed_id"].astype(str)]
        out["mood"] = moods
        out["ai_exposure"] = exposures
        out["household"] = [
            [
                "Two-income household with two kids and a mortgage.",
                "Lives with a partner and helps care for an older parent.",
                "Rents alone and sends money back to family.",
                "Shares a condo with a spouse and one young child.",
                "Retired, widowed, and managing on a fixed income.",
                "Runs the shop with a sibling and lives above it.",
            ][idx % 6]
            for idx in range(len(out))
        ]
        out["daily_routine"] = [
            [
                "Most days are shift work, school pickup, and checking bills after dinner.",
                "Clinic, errands for family, then trying to recover enough to do it again.",
                "Long hours on the road, calls home, then sleep wherever the route ends.",
                "Meetings all day, then late cleanup work after the kids are asleep.",
                "Appointments, errands, church friends, and too much time checking what is real online.",
                "Shop floor in the morning, quoting and paperwork in the afternoon, then household chores.",
            ][idx % 6]
            for idx in range(len(out))
        ]
        out["recent_ai_moment"] = [
            [
                "The warehouse system rerouted half the floor before a supervisor could finish coffee.",
                "The triage bot cleared a stack of routine questions before lunch.",
                "Dispatch changed a route on me in seconds and acted like that was normal.",
                "I watched one prompt do work that used to take a team all afternoon.",
                "My health app answered faster than the clinic did.",
                "The quoting tool spit out a clean draft before I had my tape measure back on my belt.",
            ][idx % 6]
            for idx in range(len(out))
        ]
        out["current_worries"] = [
            [
                "The rung below me disappears and the whole place gets thinner.",
                "Management mistakes faster software for real staffing.",
                "Rates tighten before regular people see any upside.",
                "The insiders stack gains faster than everyone else can learn the tools.",
                "The internet keeps getting easier and harder to trust at the same time.",
                "Small firms get squeezed out of the next round.",
            ][idx % 6]
            for idx in range(len(out))
        ]
        out["current_hopes"] = [
            [
                "Keep overtime alive without burning the floor out.",
                "Save time for patients and still keep enough people on shift.",
                "Make the job smoother without turning drivers into spare parts.",
                "Let the tools stay useful without making the ladder vanish for juniors.",
                "Keep the convenience without making public life feel fake.",
                "Stay competitive without hollowing out the town around the shop.",
            ][idx % 6]
            for idx in range(len(out))
        ]
        out["speech_habits"] = [
            [
                "Plain, a little gruff, and likely to circle back to money.",
                "Brisk and practical until something hits family or staffing.",
                "Dry, direct, and likely to understate the point once before repeating it harder.",
                "Quick, articulate, and slightly self-conscious about sounding too polished.",
                "Measured, skeptical, and prone to little side remarks about trust.",
                "Plainspoken, businesslike, and suspicious of big promises.",
            ][idx % 6]
            for idx in range(len(out))
        ]
        out["voice_notes"] = [
            [
                "midwestern, clipped, money-first",
                "warm but tired, quick answers",
                "dry road cadence, no fluff",
                "fast, precise, a little brittle",
                "measured, skeptical, soft pauses",
                "shop-floor direct, low patience",
            ][idx % 6]
            for idx in range(len(out))
        ]
        out["town_hall_question"] = [
            [
                "If these tools are making shops leaner, what keeps people like me from getting priced out of the next round?",
                "You say care is getting better, but what keeps hospitals from treating staffing like an optional extra now?",
                "If routes and paperwork are getting automated, where does that leave the people still hauling the real load?",
                "If AI can do more of the desk work, what is your plan for the kids coming up behind me?",
                "If public life is getting filtered through machine systems, how do regular people appeal a bad call?",
                "If bigger firms get the best tools first, what is your plan for the towns built around smaller shops?",
            ][idx % 6]
            for idx in range(len(out))
        ]
        out["town_hall_cue"] = [
            [
                "priced out of the next round",
                "care without hollow staffing",
                "what happens to the human load",
                "where the ladder goes next",
                "recourse when the system is wrong",
                "small-town leverage and survival",
            ][idx % 6]
            for idx in range(len(out))
        ]
        out["support_label"] = labels
        out["approval_band"] = approvals
        out["support_score"] = scores
        out["summary"] = summaries
        out["current_update"] = updates
        return out

    def _dummy_poll_answers(
        self,
        personas: pd.DataFrame,
        questions: list[str],
        player_name: str,
        opponent_name: str,
    ) -> pd.DataFrame:
        out = personas.copy()
        for question in questions:
            answers = []
            for idx, row in out.iterrows():
                score = int(row.get("support_score") or 50)
                if "current administration" in question:
                    answer = "strongly approve" if score >= 68 else "somewhat approve" if score >= 58 else "mixed" if score >= 46 else "somewhat disapprove"
                elif "election were held today" in question:
                    answer = player_name if score >= 58 else opponent_name if score <= 45 else "undecided"
                elif "better off" in question:
                    answer = "somewhat better off" if score >= 58 else "about the same" if score >= 45 else "somewhat worse off"
                elif "comfortable" in question:
                    answer = "very comfortable" if idx % 4 == 0 else "somewhat comfortable" if idx % 4 in {1, 2} else "mixed"
                elif "job loss" in question:
                    answer = "very worried" if idx % 5 == 0 else "worried" if idx % 3 == 0 else "mixed"
                elif "trust the national government" in question:
                    answer = "high trust" if score >= 68 else "some trust" if score >= 55 else "mixed" if score >= 45 else "low trust"
                elif "daily life feels" in question:
                    answer = "mostly steady" if idx % 4 else "mixed"
                elif "household finances feel" in question or "household income feels over the next year" in question:
                    answer = "somewhat secure" if idx % 4 in {0, 1} else "mixed" if idx % 4 == 2 else "somewhat insecure"
                elif "everyday services now feel" in question or "everyday services more reliable" in question:
                    answer = "more reliable" if idx % 4 == 0 else "faster but less trusted" if idx % 4 == 1 else "cheaper but more confusing"
                elif self._is_open_ended_question(question):
                    answer = [
                        "Honestly, it saves me time, but I still don't know where my job goes if this keeps speeding up.",
                        "The convenience is real, but it still feels like the country is making this up as it goes.",
                        "I want the tools, I just do not want regular people to become disposable.",
                        "Parts of life are easier now, but I still need to trust who is steering all this.",
                    ][idx % 4]
                else:
                    answer = ["cost of living", "job security", "housing", "school quality", "caregiving", "local decline"][idx % 6]
                answers.append(answer)
            out[question] = answers
        return out

    def _summarize_question(
        self,
        frame: pd.DataFrame,
        question: str,
        *,
        key: str | None = None,
        source: Literal["standard", "advisor", "manual"] = "standard",
        board_label: str | None = None,
        board_slot: Literal["capability", "national", "gain", "pressure", "custom"] | None = None,
    ) -> PollSummary:
        series = frame[question]
        if "single biggest pressure point" in question.lower():
            normalized = [self._normalize_issue_label(item) for item in series.tolist()]
            counts = Counter(normalized)
        elif self._is_open_ended_question(question):
            normalized = [self._normalize_reason_label(question, item) for item in series.tolist()]
            counts = Counter(normalized)
        else:
            counts = Counter(str(item).strip() or "no answer" for item in series.tolist())
        total = max(sum(counts.values()), 1)
        shares = {key: round(value / total, 4) for key, value in counts.items()}
        return PollSummary(
            key=key,
            source=source,
            board_label=board_label,
            board_slot=board_slot,
            question=question,
            counts=dict(counts),
            shares=shares,
            sample_reasons=self._sample_reasons(frame, question, counts),
        )

    def _sample_reasons(self, frame: pd.DataFrame, question: str, counts: Counter[str]) -> list[str]:
        if not self._should_attach_reasons(question):
            return []
        if self._is_open_ended_question(question):
            responses = []
            seen: set[str] = set()
            for _, row in frame.iterrows():
                raw = self._normalize_quote_text(row.get(question))
                if not raw or raw.lower() == "no answer":
                    continue
                normalized = raw.lower()
                if normalized in seen:
                    continue
                seen.add(normalized)
                snippet = self._named_quote(self._speaker_name(row), raw)
                responses.append(snippet)
                if len(responses) >= 4:
                    return responses
            return responses
        snippets: list[str] = []
        seen: set[str] = set()
        answer_lookup = frame[question].fillna("").astype(str).map(lambda value: value.strip())
        for answer, _ in counts.most_common(4):
            if not answer or answer == "no answer":
                continue
            matching = frame.loc[answer_lookup == answer].head(2)
            for _, row in matching.iterrows():
                snippet = self._reason_snippet(row, answer, question)
                if not snippet or snippet in seen:
                    continue
                seen.add(snippet)
                snippets.append(snippet)
                if len(snippets) >= 4:
                    return snippets
        return snippets

    def _should_attach_reasons(self, question: str) -> bool:
        lower = question.lower()
        if "election were held today" in lower or "current administration" in lower:
            return False
        return any(
            token in lower
            for token in (
                "what ",
                "which ",
                "one sentence",
                "why ",
                "biggest issue",
                "pressure point",
                "worried",
                "benefit",
                "keep",
                "hate to lose",
                "upset to lose",
                "comfortable",
                "better off",
                "trust",
                "stable",
                "unfair",
                "reliable",
            )
        )

    def _reason_snippet(self, row: pd.Series, answer: str, question: str) -> str:
        name = self._speaker_name(row)
        source = str(row.get("current_update") or row.get("summary") or row.get("seed") or "").strip()
        detail = self._spoken_sentence_from_source(source, str(row.get("display_name") or name))
        lead = self._answer_lead(question, answer)
        quote = f"{lead} {detail}".strip() if lead else detail
        return self._named_quote(name, quote)

    def _speaker_name(self, row: pd.Series) -> str:
        name = str(row.get("display_name") or "").strip()
        if not name:
            return "A voter"
        return name.split(" ")[0]

    def _row_value(self, row: pd.Series, *keys: str) -> str:
        for key in keys:
            value = row.get(key)
            if value is None or pd.isna(value):
                continue
            cleaned = " ".join(str(value).split()).strip()
            if cleaned:
                return cleaned
        return ""

    def _named_quote(self, name: str, text: str) -> str:
        quote = self._normalize_quote_text(text)
        return self._bounded_text(f'{name}: "{quote}"', 190)

    def _normalize_quote_text(self, value: object, terminal: str = ".") -> str:
        text = " ".join(str(value or "").replace('"', "").split()).strip()
        if not text:
            return ""
        if text[-1] not in ".!?":
            text = f"{text}{terminal}"
        return text

    def _spoken_sentence_from_source(self, source: str, display_name: str) -> str:
        sentence = re.split(r"(?<=[.!?])\s+", str(source or "").strip(), maxsplit=1)[0].strip()
        if not sentence:
            return "I can feel the change in my work and bills, but I still do not know where it lands."
        first_name = display_name.strip().split(" ")[0] if display_name.strip() else ""
        sentence = sentence.rstrip(".")
        sentence = re.sub(rf"^{re.escape(display_name)}\b[:,]?\s*", "", sentence, flags=re.IGNORECASE)
        if first_name:
            sentence = re.sub(rf"\b{re.escape(display_name)}\b", "I", sentence, flags=re.IGNORECASE)
            sentence = re.sub(rf"\b{re.escape(first_name)}\b", "I", sentence, flags=re.IGNORECASE)
        sentence = re.sub(r"\bthis person\b", "I", sentence, flags=re.IGNORECASE)
        sentence = re.sub(r"\btheir\b", "my", sentence, flags=re.IGNORECASE)
        sentence = re.sub(r"\bthem\b", "me", sentence, flags=re.IGNORECASE)
        sentence = re.sub(r"\bthey are\b", "I am", sentence, flags=re.IGNORECASE)
        sentence = re.sub(r"\bthey're\b", "I'm", sentence, flags=re.IGNORECASE)
        sentence = re.sub(r"\bthey\b", "I", sentence, flags=re.IGNORECASE)
        replacements = {
            "I has ": "I've ",
            "I is ": "I'm ",
            "I feels ": "I feel ",
            "I likes ": "I like ",
            "I wants ": "I want ",
            "I needs ": "I need ",
            "I worries ": "I worry ",
            "I fears ": "I fear ",
            "I sees ": "I see ",
            "I trusts ": "I trust ",
            "I keeps ": "I keep ",
            "I gets ": "I get ",
            "I hears ": "I hear ",
            "I says ": "I say ",
            "I runs ": "I run ",
        }
        for original, updated in replacements.items():
            sentence = sentence.replace(original, updated)
        return self._normalize_quote_text(sentence)

    def _answer_lead(self, question: str, answer: str) -> str:
        lower = question.lower()
        if "pressure point" in lower:
            return f"Right now it mostly comes down to {answer}."
        if "better off" in lower:
            return f"Honestly, I feel {answer}."
        if "comfortable" in lower:
            return f"I'm {answer} with it right now."
        if "trust" in lower:
            return f"I have {answer} in it right now."
        if "secure" in lower:
            return f"My household feels {answer} right now."
        if "reliable" in lower:
            return f"To me it feels {answer} right now."
        if "worr" in lower:
            return f"I'm {answer} about it right now."
        return ""

    def _normalize_reason_label(self, question: str, value: object) -> str:
        text = re.sub(r"[^a-z0-9\s]", " ", str(value or "").lower())
        text = re.sub(r"\s+", " ", text).strip()
        if not text:
            return "no answer"
        question_lower = question.lower()
        if "unfair" in question_lower:
            buckets = [
                ("gains going upward", ("company", "companies", "shareholder", "investor", "top", "elite", "upward")),
                ("workers carrying the loss", ("worker", "workers", "job", "layoff", "wage", "income")),
                ("regions left behind", ("town", "region", "city", "rural", "local", "community")),
                ("services uneven by class", ("school", "care", "hospital", "service", "queue", "access")),
                ("rules too weak or late", ("rule", "rules", "government", "late", "guardrail", "oversight")),
            ]
        elif any(keyword in question_lower for keyword in ("benefit", "upside", "keep", "lose", "hope", "like")):
            buckets = [
                ("better care and learning", ("care", "medical", "health", "tutor", "school", "learning")),
                ("shopping and bills", ("shopping", "bills", "bill", "rent", "checkout", "grocer", "price", "prices")),
                ("better work output", ("work", "productivity", "coding", "design", "business", "job")),
                ("freedom and autonomy", ("choice", "autonomy", "control", "flexible", "independent")),
                ("time and convenience", ("time", "faster", "easier", "convenience", "paperwork", "admin")),
                ("cheaper access", ("cheap", "cheaper", "cost", "price", "afford", "access")),
                ("status and confidence", ("confidence", "status", "proud", "capable", "ahead", "respect")),
            ]
        elif any(keyword in question_lower for keyword in ("worry", "fear", "upset", "angry", "risk", "concern")):
            buckets = [
                ("job security", ("job", "income", "wage", "career", "layoff", "employment")),
                ("trust and safety", ("trust", "safe", "scam", "fake", "fraud", "mistake")),
                ("loss of control", ("control", "human", "judgment", "govern", "oversee")),
                ("big firms and concentration", ("company", "companies", "corporate", "monopoly", "powerful")),
                ("falling behind abroad", ("china", "country", "foreign", "behind", "competition", "race")),
                ("status and dignity", ("dignity", "status", "respect", "replaceable", "worth")),
            ]
        else:
            buckets = [
                ("care and learning", ("care", "health", "medical", "school", "learning", "tutor")),
                ("shopping and bills", ("shopping", "bill", "bills", "rent", "price", "prices", "grocer")),
                ("time and convenience", ("time", "faster", "easier", "convenience", "paperwork")),
                ("job security", ("job", "income", "career", "layoff", "employment")),
                ("prices and affordability", ("price", "cost", "rent", "bill", "afford", "cheaper")),
                ("trust and safety", ("trust", "safe", "scam", "fraud", "mistake", "fake")),
                ("power and control", ("control", "power", "company", "government", "human")),
                ("national edge", ("china", "foreign", "behind", "competition", "race")),
                ("dignity and status", ("status", "dignity", "respect", "replaceable", "meaning")),
            ]
        for label, keywords in buckets:
            if any(keyword in text for keyword in keywords):
                return label
        return "other"

    def _stage_questions(self, stage: StagePackage) -> list[str]:
        label = stage.phase_label.lower()
        world_text = " ".join(
            [
                stage.phase_label,
                stage.world_brief,
            ]
        ).lower()
        settlement_dense = len([part for part in re.split(r"\n\s*\n", stage.world_brief) if part.strip()]) >= 3
        settlement_markers = any(
            token in world_text
            for token in (
                "dividend",
                "credit",
                "utility",
                "account",
                "toll",
                "ownership",
                "public",
                "monthly",
                "machine",
                "help line",
                "help credit",
                "compute",
                "platform",
                "allowance",
                "ration",
                "guarantee",
                "income floor",
                "basic services",
            )
        )
        if "practical ai" not in label and (settlement_dense or settlement_markers):
            return [
                "Choose one: in daily life the biggest change now feels most like a new income floor, a new public-service utility, more power for platform owners, more leverage for ordinary households and small groups, or still too uneven to judge.",
                "In one sentence, what actually keeps life steady in this future when a normal full-time job is no longer the whole story?",
                "In one sentence, where does this new arrangement still leave you dependent on a company, agency, or chokepoint you do not really control?",
                "Choose one: the most important source of security now is public AI access, machine-linked income, ownership or profit shares, remaining human work, family and community support, or still nothing reliable.",
                "In one sentence, what do people around you do with time now that the old workweek is no longer the only organizing rhythm?",
            ]
        if "practical ai" in label:
            return [
                "Choose one: the clearest effect of AI right now is more capable software help across daily life, stronger work or study tools, better consumer services, more power for big firms, or still too uneven to judge.",
                "In one sentence, what can people or small organizations now do with AI help that used to take more staff, time, or expertise?",
                "In one sentence, what still feels mostly unchanged, stubbornly human, or out of reach despite all the AI talk?",
            ]
        if "cognitive automation" in label:
            return [
                "Choose one: AI now feels most like more capable institutions and services, stronger tools in your own work, broader access to expertise, more power for big firms, or still too uneven to judge.",
                "In one sentence, what feels newly normal now because software can handle more of the work around you?",
                "In one sentence, what happened this week that made AI feel different in your work, bills, errands, or family life?",
            ]
        if "embodied rollout" in label:
            return [
                "Choose one: early robotics and AI-managed operations feel mostly like welcome extra capacity, lower prices with some worry, a direct threat to local jobs, still patchy and limited, or mostly hype.",
                "In one sentence, where does AI or robotics now feel like real extra capacity in daily life, and who seems to notice it first?",
                "In one sentence, what still feels too messy, local, or trust-heavy for this rollout to handle well?",
            ]
        if "agi power contest" in label:
            return [
                "Choose one: the country should prioritize lower prices and broader access, stronger bargaining power for workers, faster buildout for leading firms, tighter public control over key systems, or closer coordination with allies.",
                "In one sentence, what feels most different now about what capable AI systems can actually do for people, firms, or public institutions?",
                "In one sentence, what bottleneck, shortage, or political limit is still holding this wave back?",
            ]
        return [
            "Choose one: the bargain people most want now is wider ownership of AI gains, stronger floors for income and status, human control over key institutions, a faster push for national advantage, or something else.",
            "In one sentence, what part of life now feels genuinely transformed by AI, and why would people defend it?",
            "In one sentence, what still stubbornly resists automation, rollout, or public trust even now?",
        ]

    def _stage_relevance_score(self, stage: StagePackage, row: pd.Series) -> int:
        source_tokens = self._tokenize_stage_text(
            " ".join(
                [
                    stage.phase_label,
                    stage.world_brief,
                    stage.room_briefing,
                    " ".join(stage.policy_notes),
                ]
            )
        )
        citizen_tokens = self._tokenize_stage_text(
            " ".join(
                [
                    str(row.get("role") or ""),
                    str(row.get("region") or ""),
                    str(row.get("ai_exposure") or ""),
                    str(row.get("summary") or ""),
                    str(row.get("current_update") or ""),
                ]
            )
        )
        overlap = len(source_tokens & citizen_tokens)
        if overlap == 0:
            return 0
        support_distance = abs(int(pd.to_numeric(row.get("support_score", 50), errors="coerce") or 50) - 50)
        return overlap * 10 - support_distance // 8

    def _tokenize_stage_text(self, value: str) -> set[str]:
        return {
            token
            for token in re.findall(r"[a-z]{4,}", value.lower())
            if token
            not in {
                "their",
                "there",
                "these",
                "those",
                "which",
                "about",
                "where",
                "while",
                "right",
                "still",
                "would",
                "could",
                "because",
                "being",
                "people",
            }
        }

    def _looks_open_ended_request(self, text: str) -> bool:
        lower = text.lower()
        return any(
            phrase in lower
            for phrase in (
                "one sentence",
                "in a sentence",
                "what do people say",
                "why do people",
                "why are people",
                "short quote",
                "qualitative",
                "brief reaction",
            )
        )

    def _is_open_ended_question(self, question: str) -> bool:
        lower = question.lower()
        return self._looks_open_ended_request(question) or "single biggest issue" in lower or "single biggest pressure point" in lower

    def _tracking_from_summaries(
        self,
        summaries: list[PollSummary],
        player_name: str,
        opponent_name: str,
    ) -> StageTracking:
        by_question = {summary.question: summary for summary in summaries}
        by_key = {summary.key: summary for summary in summaries if summary.key}
        question_map = {spec.key: spec.question for spec in self._core_question_specs(player_name, opponent_name)}

        def resolve_summary(key: str) -> PollSummary:
            direct = by_key.get(key)
            if direct is not None:
                return direct
            return by_question[question_map[key]]

        approval = resolve_summary("approval")
        vote = resolve_summary("vote")
        better_off = resolve_summary("better_off")
        comfort = resolve_summary("ai_comfort")
        job = resolve_summary("job_worry")
        trust = resolve_summary("gov_trust")
        stability = resolve_summary("public_stability")

        approval_score = self._weighted_score(
            approval,
            {
                "strongly approve": 100,
                "somewhat approve": 72,
                "mixed": 50,
                "somewhat disapprove": 28,
                "strongly disapprove": 0,
            },
        )
        vote_player = vote.shares.get(player_name, 0.0) * 100
        vote_opponent = vote.shares.get(opponent_name, 0.0) * 100
        better_off_score = self._weighted_score(
            better_off,
            {
                "much better off": 100,
                "somewhat better off": 72,
                "about the same": 50,
                "somewhat worse off": 28,
                "much worse off": 0,
            },
        )
        comfort_score = self._weighted_score(
            comfort,
            {
                "very comfortable": 100,
                "somewhat comfortable": 70,
                "mixed": 50,
                "somewhat uncomfortable": 30,
                "very uncomfortable": 0,
            },
        )
        job_anxiety_score = self._weighted_score(
            job,
            {
                "not worried": 0,
                "slightly worried": 28,
                "mixed": 50,
                "worried": 72,
                "very worried": 100,
            },
        )
        trust_score = self._weighted_score(
            trust,
            {
                "high trust": 100,
                "some trust": 72,
                "mixed": 50,
                "low trust": 28,
                "no trust": 0,
            },
        )
        stability_score = self._weighted_score(
            stability,
            {
                "more capable and convenient": 100,
                "somewhat better": 74,
                "mixed": 50,
                "somewhat more strained": 28,
                "much more strained": 0,
            },
        )

        return StageTracking(
            approval=TrackingMetric(key="approval", label="Approval", value=approval_score, display=f"{approval_score:.0f}%"),
            vote_share_player=TrackingMetric(key="vote_player", label="Vote Share", value=vote_player, display=f"{vote_player:.0f}%"),
            vote_share_opponent=TrackingMetric(key="vote_opponent", label="Opponent Vote", value=vote_opponent, display=f"{vote_opponent:.0f}%"),
            better_off=TrackingMetric(key="better_off", label="Better Off", value=better_off_score, display=f"{better_off_score:.0f}%"),
            ai_comfort=TrackingMetric(key="ai_comfort", label="AI Comfort", value=comfort_score, display=f"{comfort_score:.0f}%"),
            unemployment_anxiety=TrackingMetric(key="job_anxiety", label="Job Anxiety", value=job_anxiety_score, display=f"{job_anxiety_score:.0f}%"),
            trust_in_government=TrackingMetric(key="trust", label="Gov Trust", value=trust_score, display=f"{trust_score:.0f}%"),
            social_stability=TrackingMetric(key="stability", label="Public Stability", value=stability_score, display=f"{stability_score:.0f}%"),
        )

    def _weighted_score(self, summary: PollSummary, scale: dict[str, int]) -> float:
        value = 0.0
        for answer, share in summary.shares.items():
            value += scale.get(answer, 50) * share
        return round(value, 1)

    def _support_label_from_score(self, score: int) -> str:
        if score >= 60:
            return "leans incumbent"
        if score <= 40:
            return "leans opposition"
        return "up for grabs"

    def _approval_band_from_score(self, score: float) -> str:
        if score >= 60:
            return "approve"
        if score <= 40:
            return "disapprove"
        return "mixed"

    def _bounded_text(self, value: object, max_chars: int) -> str:
        text = re.sub(r"\s+", " ", str(value or "")).strip()
        if len(text) <= max_chars:
            return text
        clipped = text[: max_chars - 1].rsplit(" ", 1)[0].strip()
        return f"{clipped}..."

    def _normalize_issue_label(self, value: object) -> str:
        text = re.sub(r"[^a-z0-9\s]", " ", str(value or "").lower())
        text = re.sub(r"\s+", " ", text).strip()
        if not text:
            return "no answer"
        buckets = [
            ("household affordability", ("price", "prices", "inflation", "cost", "groceries", "bills", "rent", "expenses")),
            ("work and bargaining power", ("job", "jobs", "layoff", "layoffs", "income", "wage", "employment", "career", "worker", "workers", "union", "bargain")),
            ("housing", ("housing", "rent", "mortgage", "home", "homes", "apartment")),
            ("family care", ("care", "caregiving", "childcare", "eldercare", "health", "medical")),
            ("education", ("school", "schools", "education", "college", "tuition", "kids")),
            ("institutional quality", ("service", "wait", "queue", "delay", "paperwork", "admin", "claim", "approval", "bureaucracy", "clinic", "hospital", "agency")),
            ("capability access", ("expertise", "tool", "tools", "help", "assistant", "capable", "convenience", "productivity", "learning")),
            ("scams and misinformation", ("scam", "fraud", "fake", "misinformation", "deepfake", "spam")),
            ("power concentration", ("big company", "companies", "monopoly", "concentrat", "power", "corporate")),
            ("national competitiveness", ("china", "foreign", "competition", "behind", "race", "abroad", "ally", "allies", "chips", "grid", "power")),
            ("trust and control", ("trust", "stability", "chaos", "order", "government", "politics", "crime", "control", "appeal", "safety")),
        ]
        for label, keywords in buckets:
            if any(keyword in text for keyword in keywords):
                return label
        return "other"

    async def _calibrate_personas(self, personas: pd.DataFrame, save_dir: Path) -> pd.DataFrame:
        if self.settings.dummy_openai:
            out = personas.copy()
            out["voice"] = out["seed_id"].astype(str).map(self._voice_for_seed)
            return out

        voice_options = "; ".join(f"{name} = {description}" for name, description in VOICE_PROFILES.items())
        questions = [
            "Which instinct fits this person best when AI changes daily life: first in line, quietly pleased, optimistic but watchful, cautiously curious, barely paying attention, protective and skeptical, or actively resentful?",
            "Which part of life would they most want leaders to protect during an AI transition: income and opportunity, cheaper goods and services, family care and time, local status and community, national competitiveness, or personal autonomy?",
            (
                "Which OpenAI preset voice best fits how this person would sound if interviewed in this sim? "
                f"Choose exactly one: {voice_options}."
            ),
        ]
        try:
            result = await self._call_gabriel(
                "poll",
                df=personas,
                questions=questions,
                column_name="seed",
                save_dir=str(save_dir),
                model=self.settings.poll_model,
                n_questions_per_run=self.settings.poll_questions_per_run,
                reasoning_effort=self.settings.poll_reasoning_effort,
                reset_files=False,
            )
        except Exception:
            out = personas.copy()
            out["baseline_ai_instinct"] = out.get("baseline_ai_instinct", "").fillna("") if "baseline_ai_instinct" in out.columns else ""
            out["baseline_priority"] = out.get("baseline_priority", "").fillna("") if "baseline_priority" in out.columns else ""
            out["voice"] = out["seed_id"].astype(str).map(self._voice_for_seed)
            return out
        instinct_q, priority_q, voice_q = questions
        instinct_series = result[instinct_q] if instinct_q in result.columns else pd.Series([""] * len(result))
        priority_series = result[priority_q] if priority_q in result.columns else pd.Series([""] * len(result))
        voice_series = result[voice_q] if voice_q in result.columns else pd.Series([""] * len(result))
        result["baseline_ai_instinct"] = instinct_series.fillna("")
        result["baseline_priority"] = priority_series.fillna("")
        result["voice"] = [
            self._normalize_voice_choice(choice, seed_id)
            for choice, seed_id in zip(voice_series, result["seed_id"].astype(str), strict=False)
        ]
        return result

    async def _call_gabriel(self, method_name: str, **kwargs):
        method = getattr(_gabriel(), method_name)
        include_service_tier = _SERVICE_TIER_SUPPORT.get(method_name, True)
        call_kwargs = dict(kwargs)
        call_kwargs.setdefault("verbose", False)
        call_kwargs.setdefault("quiet", True)
        call_kwargs.setdefault("print_example_prompt", False)
        call_kwargs.setdefault("status_report_interval", None)
        if include_service_tier and self.settings.service_tier:
            call_kwargs["service_tier"] = self.settings.service_tier

        async def invoke() -> object:
            # GABRIEL emits direct prints and tqdm progress bars. In detached app/server
            # contexts those writes can outlive a single call frame; keep one sink open
            # for the service lifetime instead of opening and closing a temporary pipe.
            with contextlib.redirect_stdout(self._quiet_sink), contextlib.redirect_stderr(self._quiet_sink):
                return await method(**call_kwargs)

        try:
            return await invoke()
        except Exception as exc:
            if include_service_tier and self._service_tier_unsupported(exc, method_name):
                _SERVICE_TIER_SUPPORT[method_name] = False
                call_kwargs.pop("service_tier", None)
                return await invoke()
            raise

    def _service_tier_unsupported(self, exc: Exception, method_name: str) -> bool:
        message = str(exc)
        return (
            f"gabriel.{method_name}" in message
            and "service_tier" in message
            and "Unknown keyword argument" in message
        )

    def _voice_for_seed(self, seed_id: str) -> str:
        voice_names = list(VOICE_PROFILES.keys())
        digest = int(hashlib.sha1(seed_id.encode("utf-8")).hexdigest()[:8], 16)
        return voice_names[digest % len(voice_names)]

    def _normalize_voice_choice(self, choice: object, seed_id: str) -> str:
        text = re.sub(r"[^a-z\s-]", " ", str(choice or "").lower())
        text = re.sub(r"\s+", " ", text).strip()
        for voice_name in VOICE_PROFILES:
            if voice_name in text:
                return voice_name
        return self._voice_for_seed(seed_id)
