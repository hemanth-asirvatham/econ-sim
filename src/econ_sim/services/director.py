from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path

import pandas as pd

from ..config import AppSettings
from ..models import (
    AuditoriumMode,
    AdvisorMode,
    CouncilAdvisorAction,
    CitizenSnapshot,
    CouncilAdvisorDraft,
    CouncilAdvisorProfile,
    CouncilFloorPick,
    CouncilTurnRequest,
    CouncilTurnResponse,
    CouncilSpeakerDecision,
    SetupChamberGuidance,
    SetupSessionCreateRequest,
    SetupSessionDefaults,
    SetupSessionPatchRequest,
    SetupSessionStartResponse,
    SetupSessionState,
    SetupSessionStatus,
    SetupSessionTurnRequest,
    ConversationSyncRequest,
    ConversationSyncResponse,
    ConversationTurn,
    PreparationPhase,
    PollSummary,
    QueuePollRequest,
    QueuedPollQuestion,
    RealtimeRole,
    RealtimeSessionRequest,
    RealtimeSessionResponse,
    RealtimeToolResult,
    ResolveStageRequest,
    RoomName,
    RunPollsResponse,
    SimulationConfig,
    SimulationCreateRequest,
    SimulationState,
    SimulationStatus,
    StagePackage,
    StageProgress,
    StageResolution,
    TownHallOpponentReplyDraft,
    TownHallOpponentReplyRequest,
    TownHallOpponentReplyResponse,
    TownHallQuestionDraft,
    TownHallQuestionRequest,
    TownHallQuestionResponse,
    new_id,
    utc_now,
)
from ..storage import SimulationStore
from .gabriel_service import GabrielService
from .openai_client import OpenAIGateway
from .orchestrator import OrchestratorService
from .council import COUNCIL_ADVISORS
from .realtime import RealtimePromptFactory

COUNCIL_VOICES = {
    "Rowan": "cedar",
    "Leila": "marin",
    "Mateo": "ash",
    "Amina": "shimmer",
}

logger = logging.getLogger(__name__)


class SimulationDirector:
    _COUNCIL_CONTEXT_TURN_LIMIT = 12

    def __init__(
        self,
        *,
        settings: AppSettings,
        store: SimulationStore,
        gateway: OpenAIGateway,
        gabriel_service: GabrielService,
        orchestrator: OrchestratorService,
        realtime_prompts: RealtimePromptFactory,
    ):
        self.settings = settings
        self.store = store
        self.gateway = gateway
        self.gabriel_service = gabriel_service
        self.orchestrator = orchestrator
        self.realtime_prompts = realtime_prompts
        self._tasks: dict[str, asyncio.Task] = {}
        self._featurette_tasks: dict[tuple[str, int], asyncio.Task] = {}
        self._town_hall_tasks: dict[tuple[str, int], asyncio.Task] = {}
        self._tracking_tasks: dict[tuple[str, int], asyncio.Task] = {}

    async def resume_incomplete_simulations(self) -> None:
        stale_featurettes = []
        stale_tracking = []
        for simulation_id in await self.store.list_simulation_ids():
            existing_task = self._tasks.get(simulation_id)
            if existing_task is not None and not existing_task.done():
                continue
            try:
                state = await self.store.load(simulation_id)
            except Exception:
                continue
            if state.status == SimulationStatus.initializing:
                self._tasks[simulation_id] = asyncio.create_task(self._prepare_stage(simulation_id))
            elif state.status == SimulationStatus.stage_ready and state.progress.phase == PreparationPhase.polling:
                stale_tracking.append((state.updated_at, simulation_id, state.active_stage_index))
            for stage in state.stages:
                if stage.featurettes_status in {"queued", "generating"}:
                    stale_featurettes.append((state.updated_at, simulation_id, stage.index))
        for _updated_at, simulation_id, stage_index in sorted(stale_featurettes)[-2:]:
            self._queue_stage_featurettes(simulation_id, stage_index)
        for _updated_at, simulation_id, stage_index in sorted(stale_tracking)[-2:]:
            self._queue_stage_tracking_poll(simulation_id, stage_index)

    def _build_default_config(self) -> SimulationConfig:
        ticket = self.settings.random_candidate_ticket()
        player_role = "incumbent president"
        opponent_role = "challenger governor"
        return SimulationConfig(
            title="",
            country="United States",
            player_name=self._retitle_candidate_name(ticket["player_name"], player_role),
            player_role=player_role,
            opponent_name=self._retitle_candidate_name(ticket["opponent_name"], opponent_role),
            opponent_role=opponent_role,
            opponent_voice=ticket["opponent_voice"],
            population_description=self.settings.default_population_description,
            region_focus="",
            topic_lens="",
            premise="",
            stakes="",
            persona_count=min(self.settings.default_persona_count, 48),
            stage_count=self.settings.max_stage_count,
            visual_style=self.settings.default_visual_style,
            council_roster=[],
            orchestrator_reasoning_effort=self.settings.orchestrator_reasoning_effort,
            realtime_model=self.settings.realtime_model,
        )

    def _default_council_roster(self) -> list[CouncilAdvisorProfile]:
        return [
            CouncilAdvisorProfile(**advisor.__dict__)
            for advisor in COUNCIL_ADVISORS
        ]

    def _council_roster_for(self, state: SimulationState) -> list[CouncilAdvisorProfile]:
        roster = getattr(state.config, "council_roster", None) or []
        return list(roster) if roster else self._default_council_roster()

    def _should_generate_council_roster(self, config: SimulationConfig) -> bool:
        roster = list(getattr(config, "council_roster", None) or [])
        if not roster:
            return True
        if len(roster) != len(COUNCIL_ADVISORS):
            return False
        for current, default in zip(roster, COUNCIL_ADVISORS, strict=False):
            if (
                current.key != default.key
                or current.name != default.name
                or current.room_role != default.room_role
                or current.country_role != default.country_role
                or current.remit != default.remit
                or current.voice != default.voice
            ):
                return False
        return True

    def _council_advisor_profile_for(
        self,
        state: SimulationState,
        advisor_key_or_name: str,
    ) -> CouncilAdvisorProfile | None:
        key = advisor_key_or_name.strip()
        key_lower = key.lower()
        for advisor in self._council_roster_for(state):
            if (
                advisor.key == key
                or advisor.name == key
                or advisor.key.lower() == key_lower
                or advisor.name.lower() == key_lower
            ):
                return advisor
        return None

    def _council_voice_for(self, state: SimulationState, advisor_key_or_name: str) -> str:
        advisor = self._council_advisor_profile_for(state, advisor_key_or_name)
        if advisor is not None and advisor.voice.strip():
            return advisor.voice
        fallback = COUNCIL_VOICES.get(advisor_key_or_name)
        if fallback:
            return fallback
        if advisor is not None:
            return COUNCIL_VOICES.get(advisor.name, self.settings.realtime_voice)
        return self.settings.realtime_voice

    def _last_council_speaker_key(
        self,
        state: SimulationState,
        turns: list[ConversationTurn],
    ) -> str | None:
        for turn in reversed(turns):
            if turn.speaker != "assistant":
                continue
            speaker_name = str(turn.speaker_name or "").strip()
            if not speaker_name:
                continue
            advisor = self._council_advisor_profile_for(state, speaker_name)
            if advisor is not None:
                return advisor.key
        return None

    def _targeted_council_roster(
        self,
        state: SimulationState,
        turns: list[ConversationTurn],
    ) -> list[CouncilAdvisorProfile] | None:
        latest_user_turn = next((turn for turn in reversed(turns) if turn.speaker == "user" and turn.text.strip()), None)
        if latest_user_turn is None:
            return None
        normalized = f" {re.sub(r'[^a-z0-9\\s]', ' ', latest_user_turn.text.lower())} "
        if any(token in normalized for token in (" council ", " room ", " table ", " everyone ", " anybody ", " all of you ", " what do you all ")):
            return None
        roster = self._council_roster_for(state)
        matches = self._explicit_council_name_matches(state, latest_user_turn.text)
        if len(matches) == 1:
            return matches
        if 1 < len(matches) <= 3:
            return matches
        token_pattern = re.compile(r"[a-z0-9']+")
        stopwords = {
            "about",
            "after",
            "again",
            "around",
            "because",
            "being",
            "break",
            "bring",
            "council",
            "could",
            "country",
            "everyone",
            "going",
            "great",
            "guess",
            "maybe",
            "might",
            "other",
            "policy",
            "president",
            "really",
            "right",
            "should",
            "something",
            "speaker",
            "stage",
            "still",
            "table",
            "their",
            "there",
            "these",
            "thing",
            "think",
            "those",
            "through",
            "today",
            "tradeoff",
            "want",
            "what",
            "when",
            "where",
            "which",
            "while",
            "with",
            "would",
        }

        def stems(text: str) -> set[str]:
            result: set[str] = set()
            for token in token_pattern.findall(text.lower()):
                token = token.strip("'")
                if len(token) < 4 or token in stopwords:
                    continue
                result.add(token[:6])
            return result

        turn_stems = stems(latest_user_turn.text)
        if not turn_stems:
            return None
        scored: list[tuple[int, CouncilAdvisorProfile]] = []
        for advisor in roster:
            advisor_stems = stems(
                " ".join(
                    [
                        advisor.room_role,
                        advisor.country_role,
                        advisor.remit,
                        advisor.viewpoint,
                    ]
                )
            )
            score = len(turn_stems & advisor_stems)
            if score > 0:
                scored.append((score, advisor))
        scored.sort(key=lambda item: item[0], reverse=True)
        if not scored:
            return None
        top_score, top_advisor = scored[0]
        runner_up = scored[1][0] if len(scored) > 1 else 0
        if top_score >= 2 and top_score > runner_up:
            return [top_advisor]
        if len(scored) == 1 and top_score >= 1:
            return [top_advisor]
        if top_score >= 1:
            cutoff = max(1, top_score - 1)
            narrowed = [advisor for score, advisor in scored if score >= cutoff][:2]
            if narrowed:
                return narrowed
        return None

    def _explicit_council_name_matches(self, state: SimulationState, text: str) -> list[CouncilAdvisorProfile]:
        normalized = f" {re.sub(r'[^a-z0-9\\s]', ' ', text.lower())} "
        matches: list[CouncilAdvisorProfile] = []
        for advisor in self._council_roster_for(state):
            names = [advisor.name.lower()]
            first_name = advisor.name.split()[0].lower()
            if len(first_name) >= 4:
                names.append(first_name)
            if any(f" {name} " in normalized for name in names):
                matches.append(advisor)
        return matches

    def _direct_council_reference_speaker(
        self,
        state: SimulationState,
        turns: list[ConversationTurn],
        player_text: str,
    ) -> str | None:
        normalized = f" {re.sub(r'[^a-z0-9\\s]', ' ', player_text.lower())} "
        if not normalized.strip():
            return None
        if any(token in normalized for token in (" council ", " room ", " table ", " everyone ", " anybody ", " all of you ", " what do you all ")):
            return None
        targeted = self._targeted_council_roster(
            state,
            [ConversationTurn(speaker="user", text=player_text, mode="text")],
        )
        if targeted and len(targeted) == 1:
            return targeted[0].key
        roster = self._council_roster_for(state)
        for advisor in roster:
            room_role = f" {str(advisor.room_role or '').lower()} "
            if room_role.strip() and room_role in normalized:
                return advisor.key
            names = [advisor.name.lower()]
            first_name = advisor.name.split()[0].lower()
            if len(first_name) >= 4:
                names.append(first_name)
            if any(f" {name} " in normalized for name in names):
                return advisor.key
        direct_cues = (
            " you ",
            " your ",
            " what do you think ",
            " what do you make ",
            " what's your ",
            " whats your ",
            " your take ",
            " your view ",
            " your answer ",
            " can you ",
            " could you ",
            " do you ",
            " same question ",
            " how about you ",
        )
        if not any(cue in normalized for cue in direct_cues):
            return None
        return self._last_council_speaker_key(state, turns)

    def build_create_defaults(self) -> SimulationCreateRequest:
        return SimulationCreateRequest(**self._build_default_config().model_dump())

    def build_setup_defaults(self) -> SetupSessionDefaults:
        config = self._build_default_config()
        return SetupSessionDefaults(
            config=config,
            chamber_intro=(
                "Tell me what country, institution, or future you want to examine if you want to steer it. "
                "If the broad default run sounds right, just say go and I will launch it."
            ),
            suggested_prompts=[
                "Use the default broad U.S. run.",
                "Keep it national and representative, but raise the sample to 120 people.",
                "Make this a broad Mexico run with the same representative structure, but tune the politics to cross-border manufacturing, public services, and household security.",
                "Make this the Swiss education system and focus on students, teachers, families, and cantonal administrators without losing the broader social picture.",
                "Make this a Texas governor run and focus on grid power, logistics, and local manufacturing while keeping the electorate socially mixed.",
                "Start fifteen years from now in a genuinely changed AGI settlement where daily life, public services, and household security work very differently.",
                "visual_style: Lyrical civic impressionism with bold brushwork, softened edges, and abstracted industrial and household scenes.",
            ],
        )

    async def create_simulation(self, request: SimulationCreateRequest) -> SimulationState:
        simulation_id = new_id("sim")
        defaults = self.build_create_defaults()
        config = SimulationConfig(
            title=request.title or defaults.title,
            country=request.country or defaults.country,
            player_name=request.player_name or defaults.player_name or "President",
            player_role=request.player_role or defaults.player_role,
            opponent_name=request.opponent_name or defaults.opponent_name or "Governor",
            opponent_role=request.opponent_role or defaults.opponent_role,
            opponent_voice=request.opponent_voice or defaults.opponent_voice or self.settings.realtime_debate_voice,
            population_description=(
                request.population_description
                or self._population_frame_for(
                    country=request.country or defaults.country,
                    region_focus=request.region_focus or defaults.region_focus,
                    topic_lens=request.topic_lens or defaults.topic_lens,
                    premise=request.premise or defaults.premise,
                    existing=defaults.population_description,
                )
            ),
            region_focus=request.region_focus or defaults.region_focus,
            topic_lens=request.topic_lens or defaults.topic_lens,
            premise=request.premise or defaults.premise,
            stakes=request.stakes or defaults.stakes,
            persona_count=request.persona_count,
            stage_count=request.stage_count,
            visual_style=request.visual_style or defaults.visual_style or self.settings.default_visual_style,
            council_roster=request.council_roster or defaults.council_roster,
            orchestrator_reasoning_effort=self.settings.orchestrator_reasoning_effort,
            realtime_model=self.settings.realtime_model,
        )
        state = SimulationState(
            simulation_id=simulation_id,
            incumbent_name=config.player_name,
            config=config,
            standard_questions=self.gabriel_service.standard_questions(config.player_name, config.opponent_name),
            progress=StageProgress(
                phase=PreparationPhase.queued,
                label="Queued",
                detail="Waiting to initialize the opening stage.",
                percent=0,
            ),
        )
        await self.store.save(state)
        self._tasks[simulation_id] = asyncio.create_task(self._prepare_stage(simulation_id))
        return state

    async def get_simulation(self, simulation_id: str) -> SimulationState:
        state = await self.store.load(simulation_id)
        for stage in state.stages:
            self._decorate_asset_urls(stage)
        return state

    async def create_setup_session(self, request: SetupSessionCreateRequest | None = None) -> SetupSessionState:
        config = self._build_default_config()
        if request is not None:
            config = self._apply_setup_patch(config, request)
        session = SetupSessionState(
            setup_session_id=new_id("setup"),
            status=self._setup_status_for(config),
            config=config,
            guidance=self._setup_guidance_snapshot(
                config,
                chamber_reply=(
                    "The hall is set. Tell me what world, institution, or future you want to examine if you want to steer it, or just say start for the broad default run."
                ),
            ),
        )
        await self.store.save_setup_session(session)
        return session

    async def get_setup_session(self, setup_session_id: str) -> SetupSessionState:
        return await self.store.load_setup_session(setup_session_id)

    async def patch_setup_session(
        self,
        setup_session_id: str,
        request: SetupSessionPatchRequest,
    ) -> SetupSessionState:
        session = await self.get_setup_session(setup_session_id)
        previous_config = session.config
        session.config = self._apply_setup_patch(session.config, request)
        applied_updates = self._describe_config_delta(previous_config, session.config)
        session.status = self._setup_status_for(session.config, started=bool(session.started_simulation_id))
        session.guidance = self._setup_guidance_snapshot(
            session.config,
            chamber_reply=self._setup_patch_reply(session.config, applied_updates),
            applied_updates=applied_updates,
        )
        session.updated_at = utc_now()
        await self.store.save_setup_session(session)
        return session

    async def turn_setup_session(
        self,
        setup_session_id: str,
        request: SetupSessionTurnRequest,
    ) -> SetupSessionState:
        session = await self.get_setup_session(setup_session_id)
        text = " ".join(request.text.split()).strip()
        if not text:
            raise ValueError("text is required")
        session.turns = self._bounded_setup_turns(
            [*session.turns, ConversationTurn(speaker="user", text=text, mode="text")]
        )
        previous_config = session.config
        guidance = await self.orchestrator.build_setup_guidance(
            config=session.config,
            turns=session.turns,
            user_text=text,
        )
        session.config = self._apply_setup_patch(session.config, guidance.config_updates)
        applied_updates = guidance.applied_updates or self._describe_config_delta(previous_config, session.config)
        session.guidance = self._setup_guidance_snapshot(
            session.config,
            chamber_reply=guidance.chamber_reply,
            applied_updates=applied_updates,
            open_questions=guidance.open_questions,
            next_actions=guidance.next_actions,
            config_updates=guidance.config_updates,
            launch_now=guidance.launch_now,
        )
        session.turns = self._bounded_setup_turns(
            [*session.turns, ConversationTurn(speaker="assistant", text=session.guidance.chamber_reply, mode="text")]
        )
        session.status = self._setup_status_for(session.config, started=bool(session.started_simulation_id))
        session.updated_at = utc_now()
        await self.store.save_setup_session(session)
        return session

    async def start_setup_session(
        self,
        setup_session_id: str,
        request: SetupSessionPatchRequest | None = None,
    ) -> SetupSessionStartResponse:
        session = await self.get_setup_session(setup_session_id)
        if request is not None:
            session.config = self._apply_setup_patch(session.config, request)
            session.updated_at = utc_now()
            await self.store.save_setup_session(session)
        if session.started_simulation_id and await self.store.exists(session.started_simulation_id):
            simulation = await self.get_simulation(session.started_simulation_id)
            session.status = SetupSessionStatus.started
            await self.store.save_setup_session(session)
            return SetupSessionStartResponse(setup_session=session, simulation=simulation)

        simulation = await self.create_simulation(self._create_request_from_setup_config(session.config))
        launch_note = f"Started simulation {simulation.simulation_id}. The setup draft is now attached to that live run."
        session.started_simulation_id = simulation.simulation_id
        session.status = SetupSessionStatus.started
        session.guidance = self._setup_guidance_snapshot(session.config, chamber_reply=launch_note)
        session.turns = self._bounded_setup_turns(
            [*session.turns, ConversationTurn(speaker="assistant", text=launch_note, mode="text")]
        )
        session.updated_at = utc_now()
        await self.store.save_setup_session(session)
        return SetupSessionStartResponse(setup_session=session, simulation=simulation)

    async def queue_poll(self, simulation_id: str, request: QueuePollRequest) -> SimulationState:
        state = await self.get_simulation(simulation_id)
        prepared_question = await self.gabriel_service.prepare_poll_question(request.question)
        normalized = " ".join(prepared_question.lower().split())
        existing = {" ".join(item.question.lower().split()) for item in state.queued_poll_questions}
        if normalized not in existing:
            state.queued_poll_questions.append(QueuedPollQuestion(question=prepared_question, source=request.source))
        state.updated_at = utc_now()
        await self.store.save(state)
        return state

    async def run_polls(self, simulation_id: str) -> RunPollsResponse:
        state = await self.get_simulation(simulation_id)
        self._ensure_stage_ready(state)
        self._ensure_citizens_ready(state)
        personas = await self._load_personas(simulation_id)
        if personas is None:
            raise FileNotFoundError("personas not ready")
        current_stage = state.stages[state.active_stage_index]
        queued_questions = list(state.queued_poll_questions)
        if current_stage.poll_summaries and queued_questions:
            result_df, extra_summaries = await self.gabriel_service.run_extra_polls(
                personas=personas,
                questions=queued_questions,
                save_dir=self.store.poll_dir(simulation_id, state.active_stage_index),
            )
            summary_by_question: dict[str, PollSummary] = {
                self._poll_summary_identity(summary): summary for summary in current_stage.poll_summaries
            }
            ordered_questions = [self._poll_summary_identity(summary) for summary in current_stage.poll_summaries]
            for summary in extra_summaries:
                key = self._poll_summary_identity(summary)
                summary_by_question[key] = summary
                if key not in ordered_questions:
                    ordered_questions.append(key)
            summaries = [summary_by_question[key] for key in ordered_questions]
            tracking = self.gabriel_service.tracking_from_summaries(
                summaries,
                player_name=state.config.player_name,
                opponent_name=state.config.opponent_name,
            )
        else:
            result_df, summaries, tracking = await self.gabriel_service.run_tracking_polls(
                personas=personas,
                stage_index=state.active_stage_index,
                stage=current_stage,
                player_name=state.config.player_name,
                opponent_name=state.config.opponent_name,
                save_dir=self.store.poll_dir(simulation_id, state.active_stage_index),
                extra_questions=queued_questions,
            )
        current_stage.poll_summaries = summaries
        current_stage.tracking = tracking
        current_stage.queued_poll_questions = []
        state.approval_rating = tracking.approval.value
        state.current_polls = summaries
        state.queued_poll_questions = []
        state.updated_at = utc_now()
        await self._save_personas(simulation_id, result_df)
        await self.store.save(state)
        return RunPollsResponse(simulation=state, poll_summaries=summaries)

    async def resolve_stage(self, simulation_id: str, request: ResolveStageRequest) -> SimulationState:
        state = await self.get_simulation(simulation_id)
        self._ensure_stage_ready(state)
        if state.status == SimulationStatus.resolving:
            return state
        current_stage = state.stages[state.active_stage_index]
        platform_text = " ".join(str(request.player_platform or "").split()).strip()
        if not platform_text:
            raise RuntimeError("player platform is required before resolving the election")
        player_agenda_points = self._resolve_agenda_points(
            current_stage,
            platform_text,
            request.player_rebuttal,
        )
        normalized_platform = "\n".join(player_agenda_points)
        state.status = SimulationStatus.resolving
        state.progress = StageProgress(
            phase=PreparationPhase.resolving,
            label="Resolving election",
            detail="Calculating the political consequence of your platform and public mood.",
            percent=8,
        )
        current_stage.debate_reply = await self.orchestrator.build_debate_reply(
            state=state,
            current_stage=current_stage,
            player_platform=normalized_platform,
            player_rebuttal=request.player_rebuttal,
        )
        vote_summary = next(
            (summary for summary in current_stage.poll_summaries if "election were held today" in summary.question),
            None,
        )
        if vote_summary:
            pre_debate_player_votes = vote_summary.shares.get(state.config.player_name, 0.0)
            pre_debate_opponent_votes = vote_summary.shares.get(state.config.opponent_name, 0.0)
        else:
            pre_debate_player_votes = state.approval_rating / 100
            pre_debate_opponent_votes = 1 - pre_debate_player_votes
        debate_impact = await self.orchestrator.assess_debate_impact(
            state=state,
            current_stage=current_stage,
            player_agenda_points=player_agenda_points,
            player_rebuttal=request.player_rebuttal,
            pre_debate_player_share=pre_debate_player_votes,
            pre_debate_opponent_share=pre_debate_opponent_votes,
        )
        player_votes = min(0.85, max(0.15, pre_debate_player_votes + debate_impact.player_vote_shift))
        opponent_votes = min(0.85, max(0.15, 1 - player_votes))
        player_wins = player_votes >= opponent_votes
        opponent_agenda_points = self._extract_policy_points_from_text(
            current_stage.debate_reply.opponent_opening if current_stage.debate_reply else ""
        )[:4]
        state.player_in_power = player_wins
        state.incumbent_name = state.config.player_name if player_wins else state.config.opponent_name
        current_stage.resolution = StageResolution(
            player_platform=normalized_platform,
            player_rebuttal=request.player_rebuttal,
            player_agenda_points=player_agenda_points,
            opponent_agenda_points=opponent_agenda_points,
            winner=state.incumbent_name,
            enacted_agenda=(
                normalized_platform if player_wins else "\n".join(opponent_agenda_points) or current_stage.debate_reply.opponent_opening
            ),
            public_mandate=f"{state.config.player_name} {player_votes * 100:.0f}% vs {state.config.opponent_name} {opponent_votes * 100:.0f}%",
            election_takeaway=debate_impact.rationale,
            pre_debate_vote_share_player=pre_debate_player_votes,
            pre_debate_vote_share_opponent=pre_debate_opponent_votes,
            post_debate_vote_share_player=player_votes,
            post_debate_vote_share_opponent=opponent_votes,
        )
        if state.active_stage_index >= state.config.stage_count - 1:
            state.status = SimulationStatus.completed
            state.progress = StageProgress(
                phase=PreparationPhase.ready,
                label="Campaign complete",
                detail="The simulation has reached its final stage.",
                percent=100,
            )
            state.updated_at = utc_now()
            await self.store.save(state)
            return state
        state.active_stage_index += 1
        state.status = SimulationStatus.initializing
        state.progress = StageProgress(
            phase=PreparationPhase.queued,
            label="Queuing next stage",
            detail="Locking in the election result and preparing the next world state.",
            percent=2,
        )
        state.updated_at = utc_now()
        await self.store.save(state)
        self._tasks[simulation_id] = asyncio.create_task(self._prepare_stage(simulation_id))
        return state

    async def create_realtime_session(
        self,
        simulation_id: str,
        request: RealtimeSessionRequest,
    ) -> RealtimeSessionResponse:
        state = await self.get_simulation(simulation_id)
        self._ensure_stage_ready(state)
        current_stage = state.stages[state.active_stage_index]
        if request.role == RealtimeRole.advisor:
            thread_turns = self._thread_turns(state, request.role, None, request.advisor_mode)
            selected_voice = self.settings.realtime_voice
            if request.advisor_mode == AdvisorMode.council:
                instructions = self.realtime_prompts.council_capture_instructions(state, thread_turns)
                tools = []
                create_response = False
                capture_prompt = instructions
            else:
                instructions = self.realtime_prompts.advisor_instructions(
                    state,
                    current_stage.world_brief,
                    thread_turns,
                    advisor_mode=request.advisor_mode,
                )
                tools = self.realtime_prompts.tools_for(request.role, request.advisor_mode)
                create_response = True
                capture_prompt = None
        elif request.role == RealtimeRole.debate:
            if request.auditorium_mode == AuditoriumMode.town_hall:
                thread_turns = self._merged_auditorium_turns(state)
            else:
                thread_turns = self._thread_turns(
                    state,
                    request.role,
                    None,
                    AdvisorMode.solo,
                    request.auditorium_mode,
                )
            if request.auditorium_mode == AuditoriumMode.town_hall:
                instructions = self.realtime_prompts.town_hall_capture_instructions(state, thread_turns)
                tools = []
                create_response = False
                capture_prompt = instructions
            else:
                instructions = self.realtime_prompts.debate_instructions(state, thread_turns)
                tools = self.realtime_prompts.tools_for(request.role)
                create_response = request.auditorium_mode == AuditoriumMode.debate
                capture_prompt = None
            selected_voice = state.config.opponent_voice
        else:
            self._ensure_citizens_ready(state)
            citizens = {citizen.citizen_id: citizen for citizen in current_stage.sample_citizens}
            citizen = citizens.get(request.citizen_id or "")
            if citizen is None:
                raise KeyError(f"citizen '{request.citizen_id}' not found")
            thread_turns = self._thread_turns(state, request.role, citizen.citizen_id)
            instructions = self.realtime_prompts.citizen_instructions(state, citizen, thread_turns)
            tools = self.realtime_prompts.tools_for(request.role)
            selected_voice = citizen.voice
            create_response = True
            capture_prompt = None
        client_secret, model = await self.gateway.create_realtime_session(
            instructions=instructions,
            tools=tools,
            model=state.config.realtime_model,
            voice=selected_voice,
            capture_only=(
                (request.role == RealtimeRole.advisor and request.advisor_mode == AdvisorMode.council)
                or (request.role == RealtimeRole.debate and request.auditorium_mode == AuditoriumMode.town_hall)
            ),
            capture_prompt=capture_prompt,
            create_response=(
                False
                if (
                    (request.role == RealtimeRole.advisor and request.advisor_mode == AdvisorMode.council)
                    or (request.role == RealtimeRole.debate and request.auditorium_mode == AuditoriumMode.town_hall)
                )
                else request.auto_response if request.auto_response is not None else create_response
            ),
        )
        return RealtimeSessionResponse(
            client_secret=client_secret,
            model=model,
            voice=selected_voice,
            session_type=(
                "advisor_council"
                if request.role == RealtimeRole.advisor and request.advisor_mode == AdvisorMode.council
                else request.role.value
            ),
            session_variant=(
                request.advisor_mode.value
                if request.role == RealtimeRole.advisor
                else request.auditorium_mode.value if request.role == RealtimeRole.debate else None
            ),
        )

    async def create_setup_realtime_session(
        self,
        setup_session_id: str,
    ) -> RealtimeSessionResponse:
        session = await self.get_setup_session(setup_session_id)
        thread_turns = [
            ConversationTurn(speaker=turn.speaker, text=turn.text)
            for turn in session.turns[-8:]
            if turn.text.strip()
        ]
        instructions = self.realtime_prompts.setup_instructions(session, thread_turns)
        client_secret, model = await self.gateway.create_realtime_session(
            instructions=instructions,
            tools=[],
            model=session.config.realtime_model,
            voice="ballad",
        )
        return RealtimeSessionResponse(
            client_secret=client_secret,
            model=model,
            voice="ballad",
            session_type="setup",
        )

    async def sync_conversation(
        self,
        simulation_id: str,
        request: ConversationSyncRequest,
    ) -> ConversationSyncResponse:
        state = await self.get_simulation(simulation_id)
        self._ensure_stage_ready(state)
        if request.role == RealtimeRole.citizen:
            self._ensure_citizens_ready(state)
            self._ensure_citizen_exists(state, request.citizen_id)
        turns = [
            ConversationTurn(
                speaker=item.speaker,
                speaker_name=item.speaker_name,
                speaker_voice=item.speaker_voice,
                text=item.text.strip(),
                mode=item.mode,
            )
            for item in request.turns
            if item.text.strip()
        ]
        thread_key = self._thread_key(
            state.active_stage_index,
            request.role,
            request.citizen_id,
            request.advisor_mode,
            request.auditorium_mode,
        )
        if turns:
            self._append_turns(state, thread_key, turns)
        if request.board_notes is not None and request.role == RealtimeRole.advisor and request.advisor_mode == AdvisorMode.council:
            stage = state.stages[state.active_stage_index]
            stage.policy_notes = self._maybe_apply_council_board_notes(stage, request.board_notes)
        if turns or request.board_notes is not None:
            state.updated_at = utc_now()
            await self.store.save(state)
        return ConversationSyncResponse(simulation=state, thread_key=thread_key)

    async def generate_council_turn(
        self,
        simulation_id: str,
        request: CouncilTurnRequest,
    ) -> CouncilTurnResponse:
        state = await self.get_simulation(simulation_id)
        self._ensure_stage_ready(state)
        text = " ".join(request.text.split()).strip()
        if not text and not request.continue_dialogue:
            raise RuntimeError("council turn text is required")

        thread_key = self._thread_key(state.active_stage_index, RealtimeRole.advisor, None, AdvisorMode.council)
        prior_turns = list(state.conversation_threads.get(thread_key, []))
        provisional_turns = [
            ConversationTurn(
                speaker=item.speaker,
                speaker_name=item.speaker_name,
                speaker_voice=item.speaker_voice,
                text=item.text.strip(),
                mode=item.mode,
            )
            for item in request.provisional_turns
            if item.text.strip()
        ]
        working_turns = self._truncate_council_turns(self._merge_provisional_turns(prior_turns, provisional_turns))
        working_state = state
        if request.provisional_board_notes:
            working_state = state.model_copy(deep=True)
            working_stage = working_state.stages[working_state.active_stage_index]
            working_stage.policy_notes = self._maybe_apply_council_board_notes(working_stage, request.provisional_board_notes)
        trailing_advisor_beats = self._trailing_council_advisor_beats(working_turns, state=working_state)
        room_fight_requested = (
            self._turn_requests_council_fight(self._last_user_turn_text(working_turns))
            or self._turn_requests_council_fight(text)
        )
        preferred_speaker = request.preferred_speaker.strip()
        avoid_speaker = request.avoid_speaker.strip()
        direct_reference_speaker = self._direct_council_reference_speaker(working_state, working_turns, text)
        if direct_reference_speaker and not preferred_speaker:
            preferred_speaker = direct_reference_speaker
        if request.continue_dialogue and not avoid_speaker and not preferred_speaker and trailing_advisor_beats >= 1:
            last_speaker = self._last_council_speaker_key(working_state, working_turns)
            if last_speaker:
                avoid_speaker = last_speaker
        if direct_reference_speaker and avoid_speaker == direct_reference_speaker:
            avoid_speaker = ""
        tool_action_requested = self._council_tool_action_was_requested(text)
        policy_commitment_signal = self._turn_signals_policy_commitment(text)
        allow_tool_actions = (
            not request.continue_dialogue
            and request.commit
            and tool_action_requested
        )
        allow_board_actions = (
            not request.continue_dialogue
            and request.commit
            and (self._policy_board_change_was_requested(text) or policy_commitment_signal)
        )
        board_change_requested = bool(
            allow_board_actions
            and (
                request.provisional_board_notes
                or self._policy_board_change_was_requested(text)
                or policy_commitment_signal
            )
        )
        input_text = "Continue the council exchange from the latest spoken line."
        if request.continue_dialogue:
            if not working_turns:
                raise RuntimeError("council context is required before continuing dialogue")
            input_text = (
                "Council continuation turn. Everyone already heard the last spoken advisor line. "
                "React directly to that last spoken line instead of restarting from the president's original prompt. "
                "Pick the single best next voice. "
                "If the room should now wait for the president, choose player. "
                "If the room is still productively arguing among itself, keep yield_after_turn false and let the strongest reply take the next beat. "
                f"There have already been {trailing_advisor_beats} advisor beat(s) since the player last spoke. "
                "If the disagreement is already legible, yield instead of restating the same fight. "
                "If the player explicitly asked the room to fight it out and the tradeoff is still live, prefer one more genuinely new beat over a rushed summary."
            )
        else:
            user_turn = ConversationTurn(speaker="user", text=text, mode=request.mode)
            working_turns = self._truncate_council_turns(self._merge_provisional_turns(working_turns, [user_turn]))
            input_text = (
                f"Latest player turn: {text}\n"
                "Start the council response from that player turn. "
                "If the player asked the room to argue it out, let the first advisor beat land and then rely on continuation beats for the rest of the exchange."
            )
            if self._turn_requests_council_fight(text):
                input_text += (
                    " The player explicitly wants an internal argument. "
                    "Make the first beat substantive, and if a second advisor has the real objection, political warning, or strategic consequence, let that disagreement surface now or in the very next beat."
                )
        advisor_input_text = self._council_spoken_input_text(
            player_text=text,
            continue_dialogue=request.continue_dialogue,
            working_turns=working_turns,
        )
        decision = await self._decide_council_floor(
            simulation_id=simulation_id,
            state=working_state,
            working_turns=working_turns,
            input_text=input_text,
            continue_dialogue=request.continue_dialogue,
            trailing_advisor_beats=trailing_advisor_beats,
            room_fight_requested=room_fight_requested,
            preferred_speaker=preferred_speaker,
            avoid_speaker=avoid_speaker,
        )
        player_floor_values = {"player", "user", "you", "President", working_state.config.player_name}
        if not request.continue_dialogue and text.strip() and decision.next_speaker in player_floor_values:
            opening_roster = self._targeted_council_roster(working_state, working_turns) or self._council_roster_for(working_state)
            preferred_advisor = self._council_advisor_profile_for(working_state, preferred_speaker)
            selected_advisor = (
                preferred_advisor
                if preferred_advisor is not None and any(advisor.key == preferred_advisor.key for advisor in opening_roster)
                else next(iter(opening_roster), None)
            )
            if selected_advisor is not None:
                decision.next_speaker = selected_advisor.key
                decision.yield_after_turn = False
        display_lookup = {
            advisor.key: advisor.name
            for advisor in self._council_roster_for(working_state)
        }
        display_lookup.update({advisor.name: advisor.name for advisor in self._council_roster_for(working_state)})
        assistant_turns: list[ConversationTurn] = []
        board_notes: list[str] = []
        selected_label = display_lookup.get(decision.next_speaker, decision.next_speaker)
        if decision.next_speaker not in player_floor_values:
            draft_candidates: list[CouncilAdvisorProfile] = []
            for speaker_key_or_name in [decision.next_speaker]:
                advisor = self._council_advisor_profile_for(working_state, speaker_key_or_name)
                if advisor is None or any(existing.key == advisor.key for existing in draft_candidates):
                    continue
                draft_candidates.append(advisor)

            for draft_index, speaker_advisor in enumerate(draft_candidates):
                turn, drafted_notes, drafted_action = await self._draft_council_spoken_turn(
                    simulation_id=simulation_id,
                    state=working_state,
                    working_turns=working_turns,
                    advisor=speaker_advisor,
                    input_text=advisor_input_text,
                    allow_actions=allow_tool_actions,
                )
                if turn is None or not turn.text.strip():
                    continue
                selected_label = speaker_advisor.name
                decision.next_speaker = speaker_advisor.key
                if board_change_requested and drafted_notes and not board_notes:
                    normalized_drafted_notes = [
                        self._normalize_policy_note(note)
                        for note in drafted_notes
                        if str(note).strip()
                    ]
                    board_action = (
                        str(drafted_action.arguments.get("action", "add")).strip().lower()
                        if drafted_action and drafted_action.name == "update_policy_board"
                        else "add"
                    )
                    if board_action in {"set", "replace"}:
                        board_notes = self._dedupe_policy_notes(normalized_drafted_notes)[:5]
                    else:
                        board_notes = self._dedupe_policy_notes(
                            [*working_state.stages[working_state.active_stage_index].policy_notes, *normalized_drafted_notes]
                        )[:5]
                action_prefix = ""
                state_after_action, action_prefix = await self._maybe_execute_council_action(
                    simulation_id=simulation_id,
                    state=state,
                    action=drafted_action if allow_tool_actions else None,
                    player_text=text,
                )
                if state_after_action is not state:
                    state = state_after_action
                    current_stage = state.stages[state.active_stage_index]
                    if not board_notes:
                        board_notes = list(current_stage.policy_notes[:5])
                spoken_turn = turn
                if action_prefix.strip():
                    spoken_turn.text = self._normalize_council_speech(
                        f"{action_prefix.strip()} {spoken_turn.text}".strip()
                    )
                line_yields = self._council_line_yields_to_player(spoken_turn.text)
                assistant_turns = [spoken_turn]
                if line_yields:
                    decision.next_speaker = "player"
                    decision.yield_after_turn = True
                elif room_fight_requested:
                    decision.yield_after_turn = False
                if draft_index > 0:
                    decision.reason = (
                        decision.reason
                        or f"{speaker_advisor.name} had the first distinct line after the initial floor pick stalled."
                    )
                break
            if not assistant_turns:
                if not request.continue_dialogue and text:
                    fallback_draft = await self._fallback_opening_council_draft(
                        simulation_id=simulation_id,
                        state=working_state,
                        working_turns=working_turns,
                        input_text=advisor_input_text,
                    )
                    if fallback_draft is not None:
                        speaker_advisor = self._council_advisor_profile_for(working_state, fallback_draft.advisor_key)
                        if speaker_advisor is not None:
                            selected_label = speaker_advisor.name
                            decision.next_speaker = speaker_advisor.key
                            if board_change_requested and fallback_draft.board_notes and not board_notes:
                                normalized_fallback_notes = [
                                    self._normalize_policy_note(note)
                                    for note in fallback_draft.board_notes
                                    if str(note).strip()
                                ]
                                fallback_action = (
                                    str(fallback_draft.action.arguments.get("action", "add")).strip().lower()
                                    if fallback_draft.action and fallback_draft.action.name == "update_policy_board"
                                    else "add"
                                )
                                if fallback_action in {"set", "replace"}:
                                    board_notes = self._dedupe_policy_notes(normalized_fallback_notes)[:5]
                                else:
                                    board_notes = self._dedupe_policy_notes(
                                        [*working_state.stages[working_state.active_stage_index].policy_notes, *normalized_fallback_notes]
                                    )[:5]
                            spoken_turn = ConversationTurn(
                                speaker="assistant",
                                speaker_name=speaker_advisor.name,
                                speaker_voice=speaker_advisor.voice or self.settings.realtime_voice,
                                text=fallback_draft.text,
                                mode="voice",
                            )
                            state_after_action, action_prefix = await self._maybe_execute_council_action(
                                simulation_id=simulation_id,
                                state=state,
                                action=fallback_draft.action if allow_tool_actions else None,
                                player_text=text,
                            )
                            if state_after_action is not state:
                                state = state_after_action
                                current_stage = state.stages[state.active_stage_index]
                                if not board_notes:
                                    board_notes = list(current_stage.policy_notes[:5])
                            if action_prefix.strip():
                                spoken_turn.text = self._normalize_council_speech(
                                    f"{action_prefix.strip()} {spoken_turn.text}".strip()
                                )
                            if self._council_line_yields_to_player(spoken_turn.text):
                                decision.next_speaker = "player"
                                decision.yield_after_turn = True
                            assistant_turns = [spoken_turn]
                if not assistant_turns:
                    decision.yield_after_turn = True
                    decision.next_speaker = "player"
        if not assistant_turns and not request.continue_dialogue and text:
            fallback_roster = self._targeted_council_roster(working_state, working_turns) or self._council_roster_for(working_state)
            preferred_advisor = self._council_advisor_profile_for(working_state, preferred_speaker)
            fallback_advisor = (
                preferred_advisor
                if preferred_advisor is not None and any(advisor.key == preferred_advisor.key for advisor in fallback_roster)
                else next(iter(fallback_roster), None)
            )
            if fallback_advisor is not None:
                selected_label = fallback_advisor.name
                decision.next_speaker = fallback_advisor.key
                decision.yield_after_turn = False
                assistant_turns = [
                    ConversationTurn(
                        speaker="assistant",
                        speaker_name=fallback_advisor.name,
                        speaker_voice=fallback_advisor.voice or self.settings.realtime_voice,
                        text=self._fallback_council_opening_text(working_state, working_turns, fallback_advisor),
                        mode="voice",
                    )
                ]
        if decision.next_speaker in player_floor_values or not assistant_turns:
            decision.yield_after_turn = True
        contrast = [display_lookup.get(item, item) for item in decision.contrast[:2]]
        committed_turns = self._truncate_council_turns(self._merge_provisional_turns(prior_turns, provisional_turns))
        if not request.continue_dialogue and text:
            committed_turns = self._truncate_council_turns(self._merge_provisional_turns(
                committed_turns,
                [ConversationTurn(speaker="user", text=text, mode=request.mode)],
            ))
        if assistant_turns:
            committed_turns = self._truncate_council_turns([*committed_turns, *assistant_turns])

        state_changed = False
        if request.commit and committed_turns != prior_turns:
            state.conversation_threads[thread_key] = committed_turns
            state_changed = True

        current_stage = state.stages[state.active_stage_index]
        if request.commit and allow_board_actions and request.provisional_board_notes:
            provisional_notes = self._maybe_apply_council_board_notes(current_stage, request.provisional_board_notes)
            if provisional_notes != current_stage.policy_notes:
                current_stage.policy_notes = provisional_notes
                state_changed = True
        if request.commit and board_change_requested and board_notes:
            committed_notes = self._maybe_apply_council_board_notes(current_stage, board_notes)
            if committed_notes != current_stage.policy_notes:
                current_stage.policy_notes = committed_notes
                state_changed = True

        if state_changed:
            state.updated_at = utc_now()
            await self.store.save(state)
        return CouncilTurnResponse(
            simulation=state,
            thread_key=thread_key,
            lead=state.config.player_name if decision.next_speaker in player_floor_values else selected_label,
            next_speaker=decision.next_speaker,
            contrast=contrast,
            reason=decision.reason,
            yield_after_turn=decision.yield_after_turn,
            board_notes=board_notes,
            turns=assistant_turns,
            audio_base64=None,
            audio_format=None,
        )

    def _council_tool_action_was_requested(self, text: str) -> bool:
        normalized = " ".join(str(text or "").lower().split())
        if not normalized:
            return False
        if self._policy_board_change_was_requested(normalized):
            return True
        return bool(
            re.search(
                r"\b(?:poll|survey|ask voters|ask people|what do voters think|what do people think|run polls?|take a poll|conduct a poll|queued polls?|pending polls?)\b",
                normalized,
            )
            or re.search(
                r"\b(?:go|head|move|return|back|take|bring|switch)\b.{0,36}\b(?:briefing|advisor|war room|street|citizen|debate|auditorium|town hall)\b",
                normalized,
            )
            or re.search(
                r"\b(?:talk to|speak to|take me to|show me|bring me to)\b.{0,36}\b(?:citizen|voter|worker|student|teacher|parent|retiree|owner|person|someone|[A-Z][a-z]+)\b",
                text,
                flags=re.IGNORECASE,
            )
        )

    def _council_line_yields_to_player(self, text: str) -> bool:
        cleaned = " ".join(str(text or "").split()).strip().lower()
        if not cleaned:
            return False
        direct_floor_patterns = (
            r"\bwhat do you want\b",
            r"\bwhich way do you want to go\b",
            r"\bwhich do you want\b",
            r"\bshould we\b",
            r"\bdo you want me to\b",
            r"\bdo you want us to\b",
            r"\bare you willing to\b",
            r"\bcan you live with\b",
            r"\bwhat's your call\b",
            r"\bwhat is your call\b",
            r"\bwhere do you want\b",
            r"\bwhich room\b",
            r"\bwho do you want to talk to\b",
            r"\byour call\b",
            r"\bit is your call\b",
            r"\bi need you to decide\b",
            r"\byou need to decide\b",
            r"\byou should decide\b",
            r"\btell us which way\b",
            r"\btell me which way\b",
            r"\bpick one\b",
            r"\bchoose one\b",
            r"\bwhere do you land\b",
            r"\bwhere are you landing\b",
            r"\bwhat should we put on the board\b",
        )
        return any(re.search(pattern, cleaned) for pattern in direct_floor_patterns)

    async def _fallback_opening_council_draft(
        self,
        *,
        simulation_id: str,
        state: SimulationState,
        working_turns: list[ConversationTurn],
        input_text: str,
    ) -> CouncilAdvisorDraft | None:
        roster = self._targeted_council_roster(state, working_turns) or self._council_roster_for(state)
        if not roster:
            return None
        advisor = roster[0]
        turn, board_notes, action = await self._draft_council_spoken_turn(
            simulation_id=simulation_id,
            state=state,
            working_turns=working_turns,
            advisor=advisor,
            input_text=input_text,
            allow_actions=True,
        )
        if turn is None or not turn.text.strip():
            return None
        return CouncilAdvisorDraft(
            advisor_key=advisor.key,
            advisor_name=advisor.name,
            text=turn.text,
            reason="Fallback opening beat because the council stalled on a fresh player turn.",
            board_notes=board_notes,
            action=action,
        )

    def _fallback_council_opening_text(
        self,
        state: SimulationState,
        working_turns: list[ConversationTurn],
        advisor: CouncilAdvisorProfile,
    ) -> str:
        stage = state.stages[state.active_stage_index]
        last_user = self._last_user_turn_text(working_turns).lower()
        stats = list(stage.macro_stats.values())
        stat = next(
            (
                item
                for item in stats
                if any(token in item.label.lower() for token in ("income", "access", "power", "growth", "week", "service"))
            ),
            stats[0] if stats else None,
        )
        stat_clause = (
            f"{stat.label.lower()} is {stat.value}"
            if stat is not None and stat.label and stat.value
            else "the new machine-labor bargain is already visible in household accounts"
        )
        gain = self.realtime_prompts._stage_gain(stage, 150) or "people are already getting useful things they do not want to lose"
        split = self.realtime_prompts._stage_split(stage, 150) or "the live fight is who controls the new productive floor"
        constraint = self.realtime_prompts._stage_constraint(stage, 130) or "the real bottleneck is physical capacity and public legitimacy"
        remit = " ".join([advisor.room_role, advisor.country_role, advisor.remit, advisor.viewpoint]).lower()
        if "board" in last_user or "policy" in last_user:
            if any(token in remit for token in ("commerce", "industry", "firm", "platform", "access")):
                text = (
                    "I would start with the account or platform people touch every week. "
                    f"If that gatekeeper can meter the new productive floor, the board should say portability, switching rights, and a real appeal path. {stat_clause}."
                )
            elif any(token in remit for token in ("economic", "income", "household", "tax", "wage")):
                text = (
                    "I would write the household bargain in ordinary terms: keep the useful thing people now rely on, then make the payoff show up in rent, bills, time, or services. "
                    f"{gain}"
                )
            elif any(token in remit for token in ("omb", "capacity", "agency", "procurement", "vendor")):
                text = (
                    "I would put the procurement rule on the board: public systems have to be inspectable, swappable, and appealable before they become the gate to essentials. "
                    f"{constraint}"
                )
            else:
                text = (
                    "I would write the fork, not a slogan: protect the useful gain people already feel, then govern the exact place where leverage or scarcity sits. "
                    f"{split}"
                )
        else:
            text = (
                "My first read is to name the thing people would miss if it vanished, then name the chokepoint around it. "
                f"{gain} But {split}. With {stat_clause}, the policy should say who can refuse bad terms."
            )
        return self._normalize_council_candidate_text(state, text, advisor.name)

    def _fallback_council_board_notes(
        self,
        state: SimulationState,
        player_text: str,
    ) -> list[str]:
        stage = state.stages[state.active_stage_index]
        lower = " ".join([player_text, stage.world_brief, stage.room_briefing]).lower()
        gain = self.realtime_prompts._stage_gain(stage, 120).rstrip(".")
        split = self.realtime_prompts._stage_split(stage, 120).rstrip(".")
        access = self.realtime_prompts._stage_access_channel(stage, 96).rstrip(".")
        if re.search(r"\b(?:machine check|dividend|household floor|income floor|machine income|paid week)\b", lower):
            additions = [
                "Index monthly machine checks to realized compute and power rents.",
                "Keep public AI accounts portable when private tiers get scarce.",
                "Fund human appeal desks for cutoffs in benefits, health, and model credits.",
            ]
        elif re.search(r"\b(?:compute|model credit|queue|service account|public account|platform toll)\b", lower):
            additions = [
                "Guarantee a portable basic model account before premium compute queues are served.",
                "Auction premium compute only above a protected public service floor.",
                "Require fast appeal rights when essential model services are cut or repriced.",
            ]
        elif re.search(r"\b(?:power|grid|data center|transmission|interconnection|robot|factory)\b", lower):
            additions = [
                "Fast-track power buildouts that return warrants or bill credits to households.",
                "Tie data-center permits to local grid upgrades and transparent queue rules.",
                "Keep robotics deployment fast while pricing congestion where capacity is scarce.",
            ]
        else:
            additions = [
                self._normalize_policy_note(f"Protect {gain}") if gain else "Protect the useful AI service voters would hate to lose.",
                self._normalize_policy_note(f"Target {split}") if split else "Target the chokepoint instead of freezing broad AI use.",
                self._normalize_policy_note(f"Make {access} portable and appealable") if access else "Make the household payoff visible before resentment hardens.",
            ]
        return self._dedupe_policy_notes([*stage.policy_notes, *additions])[:5]

    def _council_candidate_roster(
        self,
        state: SimulationState,
        turns: list[ConversationTurn],
        *,
        continue_dialogue: bool,
        trailing_advisor_beats: int,
        preferred_speaker: str = "",
        avoid_speaker: str = "",
    ) -> list[CouncilAdvisorProfile]:
        roster = self._council_roster_for(state)
        if not roster:
            return []
        targeted = self._targeted_council_roster(state, turns)
        if not continue_dialogue:
            candidate_roster = list(targeted or roster)
        else:
            latest_user_text = next(
                (turn.text for turn in reversed(turns) if turn.speaker == "user" and turn.text.strip()),
                "",
            )
            explicit_named = self._explicit_council_name_matches(state, latest_user_text)
            candidate_roster = list(explicit_named) if len(explicit_named) > 1 and trailing_advisor_beats < 3 else list(roster)
        if (
            continue_dialogue
            and trailing_advisor_beats >= 1
            and not preferred_speaker.strip()
            and not (
                len(self._explicit_council_name_matches(state, self._last_user_turn_text(turns))) > 1
                and trailing_advisor_beats < 2
            )
        ):
            avoided = self._council_advisor_profile_for(state, avoid_speaker)
            if avoided is not None and len(candidate_roster) > 1:
                filtered = [advisor for advisor in candidate_roster if advisor.key != avoided.key]
                if filtered:
                    candidate_roster = filtered
        preferred = self._council_advisor_profile_for(state, preferred_speaker)
        if preferred is not None:
            ordered = [advisor for advisor in candidate_roster if advisor.key == preferred.key]
            ordered.extend(advisor for advisor in candidate_roster if advisor.key != preferred.key)
            if not ordered:
                ordered = [preferred]
            elif all(advisor.key != preferred.key for advisor in ordered):
                ordered = [preferred, *ordered]
            if ordered:
                candidate_roster = ordered
        return candidate_roster

    async def _decide_council_floor(
        self,
        *,
        simulation_id: str,
        state: SimulationState,
        working_turns: list[ConversationTurn],
        input_text: str,
        continue_dialogue: bool,
        trailing_advisor_beats: int,
        room_fight_requested: bool,
        preferred_speaker: str = "",
        avoid_speaker: str = "",
    ) -> CouncilSpeakerDecision:
        roster = self._council_candidate_roster(
            state,
            working_turns,
            continue_dialogue=continue_dialogue,
            trailing_advisor_beats=trailing_advisor_beats,
            preferred_speaker=preferred_speaker,
            avoid_speaker=avoid_speaker,
        )
        if not roster:
            return CouncilSpeakerDecision(
                next_speaker="player",
                reason="No advisor lane was clearly active.",
                yield_after_turn=True,
                board_notes=[],
                contrast=[],
            )
        if not continue_dialogue:
            preferred_advisor = self._council_advisor_profile_for(state, preferred_speaker)
            if preferred_advisor is not None and any(advisor.key == preferred_advisor.key for advisor in roster):
                return CouncilSpeakerDecision(
                    next_speaker=preferred_advisor.key,
                    reason="",
                    yield_after_turn=False,
                    board_notes=[],
                    contrast=[],
                )
        if len(roster) == 1:
            return CouncilSpeakerDecision(
                next_speaker=roster[0].key,
                reason="",
                yield_after_turn=False,
                board_notes=[],
                contrast=[],
            )
        if not continue_dialogue and not room_fight_requested and working_turns and working_turns[-1].speaker == "user":
            return CouncilSpeakerDecision(
                next_speaker=roster[0].key,
                reason="",
                yield_after_turn=False,
                board_notes=[],
                contrast=[],
            )
        if self._using_builtin_dummy_gateway():
            preferred_advisor = self._council_advisor_profile_for(state, preferred_speaker)
            avoided_advisor = self._council_advisor_profile_for(state, avoid_speaker)
            chosen = preferred_advisor if preferred_advisor in roster else None
            if chosen is None:
                chosen = next(
                    (
                        advisor
                        for advisor in roster
                        if avoided_advisor is None or advisor.key != avoided_advisor.key
                    ),
                    roster[0],
                )
            if continue_dialogue and trailing_advisor_beats >= (4 if room_fight_requested else 3):
                return CouncilSpeakerDecision(
                    next_speaker="player",
                    reason="",
                    yield_after_turn=True,
                    board_notes=[],
                    contrast=[],
                )
            return CouncilSpeakerDecision(
                next_speaker=chosen.key,
                reason="",
                yield_after_turn=False,
                board_notes=[],
                contrast=[],
            )
        parsed, _ = await self.gateway.parse(
            model=self.settings.council_decider_model,
            instructions=self.realtime_prompts.council_floor_decider_instructions(
                state,
                working_turns,
                roster,
                preferred_speaker=preferred_speaker,
                avoid_speaker=avoid_speaker,
            ),
            input_text=(
                f"{input_text}\n\n"
                f"Trailing advisor beats since the player last spoke: {trailing_advisor_beats}\n"
                "Choose the next floor owner now."
            ),
            text_format=CouncilFloorPick,
            reasoning_effort="none",
            prompt_cache_key=f"{simulation_id}:council:{state.active_stage_index}:floor-decider-v2",
            max_output_tokens=12,
            verbosity="low",
        )
        valid_keys = {advisor.key for advisor in roster} | {advisor.name for advisor in roster}
        next_speaker = str(getattr(parsed, "next_speaker", "") or "player").strip()
        player_floor_values = {"player", "user", "you", "President", state.config.player_name}
        preferred_advisor = self._council_advisor_profile_for(state, preferred_speaker)
        avoided_advisor = self._council_advisor_profile_for(state, avoid_speaker)
        last_active_advisor = self._council_advisor_profile_for(
            state,
            self._last_council_speaker_key(state, working_turns) or "",
        )
        roster_keys = {advisor.key for advisor in roster}

        def fallback_speaker_key() -> str:
            for candidate in [preferred_advisor, last_active_advisor]:
                if (
                    candidate is not None
                    and candidate.key in roster_keys
                    and (
                        avoided_advisor is None
                        or preferred_advisor is not None
                        or trailing_advisor_beats < 1
                        or candidate.key != avoided_advisor.key
                    )
                ):
                    return candidate.key
            return next(
                (
                    advisor.key
                    for advisor in roster
                    if (
                        avoided_advisor is None
                        or preferred_advisor is not None
                        or trailing_advisor_beats < 1
                        or advisor.key != avoided_advisor.key
                    )
                ),
                roster[0].key,
            )

        if next_speaker not in valid_keys and next_speaker not in player_floor_values:
            next_speaker = fallback_speaker_key()
        normalized_next_speaker = self._council_advisor_profile_for(state, next_speaker)
        if normalized_next_speaker is not None:
            next_speaker = normalized_next_speaker.key
        if preferred_advisor is not None and preferred_advisor.key in roster_keys and working_turns and working_turns[-1].speaker == "user":
            next_speaker = preferred_advisor.key
        if working_turns and working_turns[-1].speaker == "user" and next_speaker in player_floor_values:
            next_speaker = fallback_speaker_key()
        if continue_dialogue and room_fight_requested and trailing_advisor_beats == 0 and next_speaker in player_floor_values:
            next_speaker = fallback_speaker_key()
        if continue_dialogue and next_speaker in player_floor_values and trailing_advisor_beats < 2:
            last_advisor_line = next(
                (
                    turn.text
                    for turn in reversed(working_turns)
                    if turn.speaker == "assistant" and str(turn.text).strip()
                ),
                "",
            )
            if last_advisor_line and not self._council_line_yields_to_player(last_advisor_line):
                next_speaker = fallback_speaker_key()
        if (
            continue_dialogue
            and avoided_advisor is not None
            and next_speaker == avoided_advisor.key
            and len(roster) > 1
            and trailing_advisor_beats >= 1
            and preferred_advisor is None
        ):
            if preferred_advisor is not None and preferred_advisor.key != avoided_advisor.key:
                next_speaker = preferred_advisor.key
            else:
                next_speaker = next((advisor.key for advisor in roster if advisor.key != avoided_advisor.key), next_speaker)
        decision = CouncilSpeakerDecision(
            next_speaker=next_speaker,
            reason="",
            yield_after_turn=next_speaker in player_floor_values,
            board_notes=[],
            contrast=[],
            action=None,
        )
        if decision.next_speaker in player_floor_values:
            decision.yield_after_turn = True
        elif continue_dialogue and room_fight_requested:
            decision.yield_after_turn = False
        return decision

    def _council_spoken_input_text(
        self,
        *,
        player_text: str,
        continue_dialogue: bool,
        working_turns: list[ConversationTurn],
    ) -> str:
        last_user_turn = self._last_user_turn_text(working_turns)
        fight_prompt = player_text if player_text.strip() else last_user_turn
        if continue_dialogue:
            base = (
                "Take the next live beat in the current council exchange. "
                "React to the last spoken advisor line first. "
                "If the room is still productively disagreeing, stay inside that live disagreement instead of bouncing straight back to the president."
            )
        else:
            base = f"Player turn: {player_text.strip()}"
        if self._turn_requests_council_fight(fight_prompt):
            base += (
                "\nThe president explicitly wants the room to argue it out. "
                "Unless someone is clearly handing the floor back, prefer an actual advisor-to-advisor beat over a premature yield."
            )
        base += "\nSpeak naturally and directly."
        return base

    def _last_user_turn_text(self, turns: list[ConversationTurn]) -> str:
        for turn in reversed(turns):
            if turn.speaker == "user" and str(turn.text).strip():
                return str(turn.text).strip()
        return ""

    async def _draft_council_spoken_turn(
        self,
        *,
        simulation_id: str,
        state: SimulationState,
        working_turns: list[ConversationTurn],
        advisor: CouncilAdvisorProfile,
        input_text: str,
        allow_actions: bool,
    ) -> tuple[ConversationTurn | None, list[str], CouncilAdvisorAction | None]:
        if self._using_builtin_dummy_gateway():
            stage = state.stages[state.active_stage_index]
            last_user = self._last_user_turn_text(working_turns).lower()
            last_advisor = next(
                (
                    turn.text
                    for turn in reversed(working_turns)
                    if turn.speaker == "assistant" and str(turn.text).strip()
                ),
                "",
            ).lower()
            remit = " ".join([advisor.remit, advisor.country_role, advisor.viewpoint]).lower()
            if any(token in remit for token in ("security", "defense", "safety", "trust")):
                text = (
                    "I would keep useful AI accounts open, then put hard audits only on systems that can move money, benefits, weapons, or infrastructure. "
                    "That protects the public without turning every household helper into a licensed product."
                )
            elif any(token in remit for token in ("econom", "industry", "innovation", "science", "growth")):
                text = (
                    "I would buy down compute for households and small firms, because cheap machine labor is becoming part of basic productive capacity. "
                    "The risk is letting a few platforms meter the whole new workday."
                )
            elif any(token in remit for token in ("household", "labor", "family", "welfare", "service")):
                text = (
                    "I would ask who gets paid when software does the old office week. "
                    "If the answer is only platform owners, voters will see the convenience and still feel locked out of the bargain."
                )
            elif "regulat" in last_user or "slow" in last_user or "audit" in last_advisor:
                text = (
                    "Do not regulate the whole frontier as one object. "
                    "Draw the line where AI can shut off accounts, move funds, sign contracts, steer robots, or touch public infrastructure."
                )
            else:
                text = (
                    f"The live question is who controls the new order around {self.realtime_prompts._stage_gain(stage, 60) or 'the new capability'}. "
                    "I would name the access rule first, because people defend useful tools but punish chokepoints."
                )
            board_notes: list[str] = []
            if allow_actions and re.search(r"\b(?:board|add|write|put|change|update)\b", last_user):
                board_notes = [self._normalize_policy_note("Keep broad AI access open while auditing systems that move money, rights, or infrastructure.")]
            return (
                ConversationTurn(
                    speaker="assistant",
                    speaker_name=advisor.name,
                    speaker_voice=advisor.voice or self.settings.realtime_voice,
                    text=self._normalize_council_candidate_text(state, text, advisor.name),
                    mode="voice",
                ),
                board_notes,
                None,
            )
        parsed, _ = await self.gateway.parse(
            model=self.settings.council_draft_model,
            instructions=self.realtime_prompts.council_spoken_response_instructions(
                state,
                working_turns,
                advisor.name,
                allow_actions=allow_actions,
            ),
            input_text=input_text,
            text_format=CouncilAdvisorDraft,
            reasoning_effort="low",
            prompt_cache_key=f"{simulation_id}:council:{state.active_stage_index}:{advisor.key}:spoken-draft-v2",
            max_output_tokens=200,
            verbosity="low",
        )
        cleaned_text = self._normalize_council_candidate_text(state, parsed.text, advisor.name)
        if self._council_spoken_turn_needs_concrete_repair(cleaned_text, working_turns):
            repaired_text = await self._repair_council_spoken_turn(
                simulation_id=simulation_id,
                state=state,
                working_turns=working_turns,
                advisor=advisor,
                input_text=input_text,
                draft_text=cleaned_text,
            )
            if repaired_text:
                cleaned_text = repaired_text
        board_notes = (
            self._dedupe_policy_notes(
                [
                    self._normalize_policy_note(note)
                    for note in getattr(parsed, "board_notes", [])
                    if str(note).strip() and not self._policy_board_extraction_is_placeholder(note)
                ]
            )[:5]
            if allow_actions
            else []
        )
        action = self._normalize_council_action(parsed) if allow_actions else None
        if allow_actions and action and action.name == "update_policy_board":
            action_notes = action.arguments.get("notes") or []
            if isinstance(action_notes, list):
                board_notes = self._dedupe_policy_notes(
                    [
                        *board_notes,
                        *[
                            self._normalize_policy_note(note)
                            for note in action_notes
                            if str(note).strip() and not self._policy_board_extraction_is_placeholder(note)
                        ],
                    ]
                )[:5]
        last_user_turn = self._last_user_turn_text(working_turns)
        if (
            self._using_builtin_dummy_gateway()
            and allow_actions
            and cleaned_text
            and self._policy_board_change_was_requested(last_user_turn)
        ):
            requested_note_count = self._requested_policy_board_note_count(last_user_turn)
            extracted_notes = self._extract_policy_points_from_text(cleaned_text)[:5]
            if (
                requested_note_count
                and len(extracted_notes) >= requested_note_count
                and (
                    len(board_notes) < requested_note_count
                    or any(len(note.split()) < 5 for note in board_notes[:requested_note_count])
                )
            ):
                board_notes = extracted_notes[:requested_note_count]
            elif not board_notes or (requested_note_count and len(board_notes) < requested_note_count):
                if extracted_notes:
                    board_notes = self._dedupe_policy_notes([*board_notes, *extracted_notes])[:5]
            if requested_note_count and len(board_notes) < requested_note_count:
                board_notes = self._dedupe_policy_notes(
                    [
                        *board_notes,
                        *self._fallback_council_board_notes(state, last_user_turn),
                    ]
                )[:requested_note_count]
        if not cleaned_text:
            return None, board_notes, action
        return (
            ConversationTurn(
                speaker="assistant",
                speaker_name=advisor.name,
                speaker_voice=advisor.voice or self.settings.realtime_voice,
                text=cleaned_text,
                mode="voice",
            ),
            board_notes,
            action,
        )

    def _using_builtin_dummy_gateway(self) -> bool:
        return not self.gateway.live and getattr(self.gateway.parse, "__self__", None) is self.gateway

    def _council_spoken_turn_needs_concrete_repair(
        self,
        text: str,
        working_turns: list[ConversationTurn],
    ) -> bool:
        stripped = " ".join(text.split()).strip()
        last_user_turn = self._last_user_turn_text(working_turns).lower()
        repair_signals = (
            "push back",
            "be concrete",
            "more concrete",
            "specific proposal",
            "specific move",
            "give me a move",
            "what should we do",
            "what would you do",
            "what exactly would you do",
            "what is your plan",
            "what's your plan",
        )
        if not any(signal in last_user_turn for signal in repair_signals):
            return False
        if not stripped:
            return True
        lower = stripped.lower().replace("’", "'")
        words = re.findall(r"[a-z0-9']+", lower)
        if len(words) < 14:
            return True
        trailing_clause = re.search(
            r"\b(?:anyone|firms?|agencies|systems?|models?|people|workers|contractors)\s+(?:using|who\s+use|deploying|that\s+can|able\s+to)\b(?P<clause>[^.!?]*)[.!?]?$",
            lower,
        )
        if trailing_clause:
            clause = trailing_clause.group("clause")
            if not re.search(
                r"\b(?:must|should|has\s+to|have\s+to|needs?\s+to|cannot|can't|may\s+not|gets?\s+|pays?\s+|is\s+required|are\s+required|carries\s+liability|face[s]?\s+liability|is\s+audited|are\s+audited)\b",
                clause,
            ):
                return True
        if not lower.startswith(("i'd push back", "i would push back", "i push back", "i'd object", "i would object")):
            return False
        replacement_cues = (
            " instead ",
            " i would narrow ",
            " i'd narrow ",
            " i would split ",
            " i'd split ",
            " give every ",
            " require ",
            " fund ",
            " buy ",
            " tax ",
            " insure ",
            " guarantee ",
            " open ",
            " publish ",
            " prosecute ",
            " license only ",
        )
        return not any(cue in f" {lower} " for cue in replacement_cues)

    async def _repair_council_spoken_turn(
        self,
        *,
        simulation_id: str,
        state: SimulationState,
        working_turns: list[ConversationTurn],
        advisor: CouncilAdvisorProfile,
        input_text: str,
        draft_text: str,
    ) -> str:
        stage = state.stages[state.active_stage_index]
        last_user_turn = self._last_user_turn_text(working_turns)
        macro_stats = self.realtime_prompts._macro_stats_block(stage)
        world_memo = self.realtime_prompts._world_context_block(stage, memo_budget=760, brief_budget=2500)
        instructions = (
            f"You are repairing one live spoken line from {advisor.name}, the {advisor.country_role}. "
            "Return one CouncilAdvisorDraft JSON object. Only the text field matters. "
            "The current draft stopped at an objection or stayed too vague. Rewrite it as 1 or 2 short spoken sentences, about 22-56 words. "
            "Do not merely object. Name one replacement move, narrower guardrail, or concrete consequence in plain English. "
            "The replacement must say who gets something, who pays, who is blocked, what rule changes, or what public capacity is built. "
            "Use one exact noun, institution, bottleneck, or watched statistic from this chapter. "
            "Do not propose public accounts, audits, compute subsidies, licensing, or guardrails unless that object is actually live in the world memo or the player raised it. "
            "Avoid consultant language. Do not say lane, pillar, unlock, stakeholder, pressure-test, strategic posture, ecosystem, governance layer, framework, center of gravity, or policy package. "
            "No labels, no bullets, no moderator voice, no stage directions.\n\n"
            f"Advisor remit: {advisor.remit}\n"
            f"Stage: {stage.phase_label}\n"
            f"World memo:\n{world_memo}\n"
            f"Macro stats:\n{macro_stats}\n"
            f"Main upside: {self.realtime_prompts._stage_gain(stage, 150)}\n"
            f"Main split: {self.realtime_prompts._stage_split(stage, 150)}\n"
            f"Working board:\n{self.realtime_prompts._policy_board_block(stage.policy_notes)}"
        )
        parsed, _ = await self.gateway.parse(
            model=self.settings.council_draft_model,
            instructions=instructions,
            input_text=(
                f"Player turn:\n{last_user_turn}\n\n"
                f"Current bad draft:\n{draft_text}\n\n"
                f"Original generation cue:\n{input_text}\n\n"
                "Rewrite as the final line to speak aloud."
            ),
            text_format=CouncilAdvisorDraft,
            reasoning_effort="low",
            prompt_cache_key=f"{simulation_id}:council:{state.active_stage_index}:{advisor.key}:repair",
            max_output_tokens=120,
            verbosity="low",
        )
        repaired = self._normalize_council_candidate_text(state, parsed.text, advisor.name)
        if not repaired or len(re.findall(r"[a-z0-9']+", repaired.lower())) < 12:
            return self._fallback_council_concrete_repair_text(working_turns)
        if self._council_spoken_turn_needs_concrete_repair(repaired, working_turns):
            return self._fallback_council_concrete_repair_text(working_turns)
        return repaired

    def _fallback_council_concrete_repair_text(self, working_turns: list[ConversationTurn]) -> str:
        last_user = self._last_user_turn_text(working_turns).lower()
        if "public utility" in last_user:
            return (
                "I would narrow that by giving every household a basic public AI account, then requiring hard audits only where AI can move money, "
                "records, weapons, infrastructure, or benefits."
            )
        if "frontier" in last_user and "regulat" in last_user:
            return (
                "I would regulate the dangerous uses, not the whole frontier: keep broad AI accounts open, then require audits and liability where AI "
                "can move money, records, weapons, infrastructure, or benefits."
            )
        return ""

    def _normalize_council_action(self, source: CouncilAdvisorDraft | CouncilSpeakerDecision) -> CouncilAdvisorAction | None:
        raw_action = getattr(source, "action", None)
        if raw_action is None:
            return None
        if isinstance(raw_action, CouncilAdvisorAction):
            return raw_action
        if isinstance(raw_action, dict):
            try:
                return CouncilAdvisorAction.model_validate(raw_action)
            except Exception:
                return None
        return None

    def _should_execute_council_action(
        self,
        action: CouncilAdvisorAction | None,
        *,
        player_text: str,
    ) -> bool:
        if action is None:
            return False
        normalized = " ".join(str(player_text or "").lower().split())
        if action.name == "run_poll_now":
            return bool(
                re.search(
                    r"\b(?:poll|survey|what do voters think|what do people think|run a poll|check the poll|see the poll|ask voters|ask people|test with voters|take the temperature|would people buy this|would voters buy this)\b",
                    normalized,
                )
            )
        if action.name == "run_queued_polls":
            return bool(
                re.search(
                    r"\b(?:run|launch|do)\b.{0,24}\b(?:queued|those|all)\s+polls\b",
                    normalized,
                )
                or "poll them now" in normalized
                or "run the battery" in normalized
            )
        if action.name == "move_room_focus":
            return bool(
                re.search(
                    r"\b(?:go|head|move|return|back|take|bring)\b.{0,32}\b(?:briefing|advisor|war room|street|citizen|debate|auditorium|town hall)\b",
                    normalized,
                )
            )
        if action.name == "focus_citizen_by_name":
            return bool(
                re.search(
                    r"\b(?:talk to|speak to|take me to|show me|go to|bring me to)\b",
                    normalized,
                )
            )
        if action.name != "update_policy_board":
            return False
        board_was_requested = bool(
            re.search(
                r"\b(?:board|whiteboard|talking\s+points?|policy\s+ideas?|policy\s+board)\b",
                normalized,
            )
            or re.search(
                r"\b(?:put|write|add|keep|save|capture|note|mark|scratch|drop|remove|replace|change|rewrite)\s+(?:that|this|it|one|down|up)\b",
                normalized,
            )
            or re.search(
                r"\b(?:write|put|add|keep|save|capture|note)\s+(?:that|this|it)\s+down\b",
                normalized,
            )
        )
        if not board_was_requested:
            return False
        action_name = str(action.arguments.get("action", "add")).strip().lower()
        raw_notes = action.arguments.get("notes") or []
        notes = raw_notes if isinstance(raw_notes, list) else []
        if action_name in {"set", "add", "replace"} and not any(str(note).strip() for note in notes):
            return False
        if action_name == "clear":
            return any(
                cue in normalized
                for cue in (
                    "clear the board",
                    "clear board",
                    "erase the board",
                    "wipe the board",
                    "scratch everything",
                    "remove everything",
                    "take everything off",
                    "blank board",
                )
            )
        return True

    async def _execute_council_action(
        self,
        *,
        simulation_id: str,
        state: SimulationState,
        action: CouncilAdvisorAction,
    ) -> tuple[SimulationState, str]:
        result = await self.execute_tool(
            simulation_id,
            RealtimeRole.advisor,
            action.name,
            dict(action.arguments),
        )
        updated_payload = result.data.get("simulation")
        updated_state = state
        if isinstance(updated_payload, dict):
            try:
                updated_state = SimulationState.model_validate(updated_payload)
            except Exception:
                updated_state = await self.get_simulation(simulation_id)
        elif action.name in {"update_policy_board", "run_poll_now", "run_queued_polls", "move_room_focus", "focus_citizen_by_name"}:
            updated_state = await self.get_simulation(simulation_id)

        if not result.ok:
            message = str(result.data.get("message", "")).strip()
            return updated_state, message or "That move did not go through."

        if action.name == "run_poll_now":
            topline = str(result.data.get("topline", "")).strip()
            if topline:
                return updated_state, f"Quick poll read: {topline}."
        if action.name == "run_queued_polls":
            poll_summaries = result.data.get("poll_summaries") or []
            if isinstance(poll_summaries, list) and poll_summaries:
                first_summary = poll_summaries[0]
                if isinstance(first_summary, dict):
                    topline = self._poll_summary_topline(PollSummary.model_validate(first_summary))
                    if topline:
                        return updated_state, f"Fresh poll read: {topline}."
            return updated_state, "The poll battery is in."
        if action.name == "update_policy_board":
            policy_notes = result.data.get("policy_notes") or []
            action_name = str(action.arguments.get("action", "set")).strip().lower()
            if action_name == "clear":
                return updated_state, "I cleared the board."
            if isinstance(policy_notes, list) and policy_notes:
                preview = "; ".join(str(note).strip() for note in policy_notes[:2] if str(note).strip())
                if action_name == "replace":
                    return updated_state, f"I rewrote that line on the board: {preview or policy_notes[0]}"
                if action_name == "add":
                    return updated_state, f"I added it to the board: {preview or policy_notes[0]}"
                if action_name == "set":
                    return updated_state, f"I put it on the board: {preview or policy_notes[0]}"
                return updated_state, f"I updated the board: {preview or policy_notes[0]}"
            return updated_state, "I updated the board."
        if action.name == "move_room_focus":
            room = str(result.data.get("room", "")).strip()
            if room:
                return updated_state, f"Let's move to {room.replace('_', ' ')}."
        if action.name == "focus_citizen_by_name":
            citizen = result.data.get("citizen") or {}
            if isinstance(citizen, dict):
                name = str(citizen.get("display_name", "")).strip()
                if name:
                    return updated_state, f"Let's talk to {name}."

        message = str(result.data.get("message", "")).strip()
        return updated_state, message

    async def _maybe_execute_council_action(
        self,
        *,
        simulation_id: str,
        state: SimulationState,
        action: CouncilAdvisorAction | None,
        player_text: str,
    ) -> tuple[SimulationState, str]:
        if not self._should_execute_council_action(
            action,
            player_text=player_text,
        ):
            return state, ""
        return await self._execute_council_action(
            simulation_id=simulation_id,
            state=state,
            action=action,
        )

    async def _draft_town_hall_question(
        self,
        *,
        state: SimulationState,
        citizen: CitizenSnapshot,
        thread_turns: list[ConversationTurn],
        live_refresh: bool,
    ) -> tuple[str, str]:
        fallback_question = self._fallback_town_hall_question(state, citizen)
        stored_question_raw = " ".join(citizen.town_hall_question.split()).strip()
        stored_question = self._normalize_town_hall_question_text(
            stored_question_raw
        )
        fallback_question = self._normalize_town_hall_question_text(fallback_question) or fallback_question
        stored_cue = " ".join(citizen.town_hall_cue.split()).strip()
        fallback_cue = self._town_hall_pressure_cue(citizen)
        seed_question = stored_question or fallback_question
        seed_question_for_prompt = stored_question_raw or seed_question
        seed_cue = stored_cue or fallback_cue
        if stored_question and not live_refresh:
            return stored_question, self._normalize_town_hall_cue(stored_cue or seed_cue) or seed_cue
        try:
            instructions = self.realtime_prompts.town_hall_question_generation_instructions(state, citizen, thread_turns)
            if live_refresh:
                input_text = (
                    f"Generate the next audience question from {citizen.display_name}, "
                    f"{citizen.role} in {citizen.region}.\n"
                    f"Main pressure from this person's life: {seed_cue}\n"
                    f"If useful, here is a rough earlier phrasing from the citizen profile: {seed_question_for_prompt}\n"
                    "Keep the same person and stake, but let the current debate sharpen the exact wording at mic time."
                )
            else:
                input_text = (
                    f"Write one natural town hall question for {citizen.display_name}, "
                    f"{citizen.role} in {citizen.region}.\n"
                    f"Main pressure from this person's life: {seed_cue}\n"
                    f"If useful, here is a rough earlier phrasing from the citizen profile: {seed_question_for_prompt}\n"
                    "Keep it grounded in this person's own life. Do not make it sound like a moderator prompt or a debate memo."
                )
            parsed, _ = await self.gateway.parse(
                model=self.settings.debate_model,
                instructions=instructions,
                input_text=input_text,
                text_format=TownHallQuestionDraft,
                reasoning_effort="low",
                prompt_cache_key=f"{state.simulation_id}:townhall:{state.active_stage_index}:{citizen.citizen_id}",
                max_output_tokens=420,
                verbosity="low",
            )
        except Exception:
            parsed = TownHallQuestionDraft(
                question=seed_question,
                cue=seed_cue,
            )
        final_question = (
            self._normalize_town_hall_question_text(str(parsed.question or "").strip())
            or stored_question
            or fallback_question
        )
        final_cue = self._normalize_town_hall_cue(str(parsed.cue or "").strip() or seed_cue) or seed_cue
        return final_question, final_cue

    async def generate_town_hall_question(
        self,
        simulation_id: str,
        request: TownHallQuestionRequest,
    ) -> TownHallQuestionResponse:
        state = await self.get_simulation(simulation_id)
        self._ensure_stage_ready(state)
        self._ensure_citizens_ready(state)
        stage = state.stages[state.active_stage_index]
        citizen = self._resolve_town_hall_citizen(stage, request.citizen_id)
        thread_key = self._thread_key(
            state.active_stage_index,
            RealtimeRole.debate,
            None,
            AdvisorMode.solo,
            AuditoriumMode.town_hall,
        )
        thread_turns = self._merged_auditorium_turns(state)
        final_question, final_cue = await self._draft_town_hall_question(
            state=state,
            citizen=citizen,
            thread_turns=thread_turns,
            live_refresh=True,
        )
        citizen.town_hall_question = final_question
        citizen.town_hall_cue = final_cue
        question_turn = ConversationTurn(
            speaker="assistant",
            speaker_name=citizen.display_name,
            speaker_voice=citizen.voice,
            text=final_question,
            mode=request.mode,
        )
        self._append_turns(state, thread_key, [question_turn])
        state.updated_at = utc_now()
        await self.store.save(state)
        return TownHallQuestionResponse(
            simulation=state,
            thread_key=thread_key,
            cue=final_cue,
            question_turn=question_turn,
        )

    async def generate_town_hall_opponent_reply(
        self,
        simulation_id: str,
        request: TownHallOpponentReplyRequest,
    ) -> TownHallOpponentReplyResponse:
        state = await self.get_simulation(simulation_id)
        self._ensure_stage_ready(state)
        thread_key = self._thread_key(
            state.active_stage_index,
            RealtimeRole.debate,
            None,
            AdvisorMode.solo,
            AuditoriumMode.town_hall,
        )
        question_turn, player_turn = self._latest_town_hall_exchange(state)
        if player_turn is None:
            raise RuntimeError("The crowd is still waiting for the player's answer.")
        if question_turn is None:
            raise RuntimeError("No active town hall question is available yet.")
        thread_turns = self._merged_auditorium_turns(state)
        instructions = self.realtime_prompts.town_hall_opponent_reply_instructions(
            state,
            thread_turns,
            question_turn,
            player_turn,
        )
        input_text = (
            f"Latest audience question from {question_turn.speaker_name or 'the voter'}: {question_turn.text}\n"
            f"Latest player answer: {player_turn.text}\n"
            "Write one brief opposing-candidate reply for the live auditorium."
        )
        try:
            parsed, _ = await self.gateway.parse(
                model=self.settings.debate_model,
                instructions=instructions,
                input_text=input_text,
                text_format=TownHallOpponentReplyDraft,
                reasoning_effort="low",
                prompt_cache_key=f"{simulation_id}:townhall:{state.active_stage_index}:opponent-reply",
                max_output_tokens=220,
                verbosity="low",
            )
            reply_text = self._normalize_town_hall_reply_text(str(parsed.reply or "").strip())
        except Exception:
            reply_text = ""
        if not reply_text:
            reply_text = self._fallback_town_hall_opponent_reply(state, question_turn, player_turn)
        reply_turn = ConversationTurn(
            speaker="assistant",
            speaker_name=state.config.opponent_name,
            speaker_voice=state.config.opponent_voice,
            text=reply_text,
            mode=request.mode,
        )
        self._append_turns(state, thread_key, [reply_turn])
        state.updated_at = utc_now()
        await self.store.save(state)
        return TownHallOpponentReplyResponse(
            simulation=state,
            thread_key=thread_key,
            reply_turn=reply_turn,
        )

    async def execute_tool(
        self,
        simulation_id: str,
        role: RealtimeRole,
        tool_name: str,
        payload: dict,
    ) -> RealtimeToolResult:
        if role == RealtimeRole.citizen and tool_name != "move_room_focus":
            return RealtimeToolResult(ok=False, data={"message": "citizen sessions only expose room navigation"})
        if role == RealtimeRole.debate and tool_name != "move_room_focus":
            return RealtimeToolResult(ok=False, data={"message": "debate sessions only expose room navigation"})
        if tool_name == "get_world_briefing":
            state = await self.get_simulation(simulation_id)
            self._ensure_stage_ready(state)
            stage = state.stages[state.active_stage_index]
            return RealtimeToolResult(
                data={
                    "title": stage.title,
                    "year_label": stage.year_label,
                    "summary": stage.world_brief,
                    "macro_stats": {
                        key: stat.model_dump(mode="json")
                        for key, stat in stage.macro_stats.items()
                    },
                    "metrics": [metric.model_dump(mode="json") for metric in stage.tracking.as_list()],
                    "policy_notes": stage.policy_notes,
                    "poll_summaries": [summary.model_dump(mode="json") for summary in stage.poll_summaries[:4]],
                }
            )
        if tool_name == "queue_poll_question":
            question = str(payload.get("question", "")).strip()
            if not question:
                return RealtimeToolResult(ok=False, data={"message": "question is required"})
            updated = await self.queue_poll(simulation_id, QueuePollRequest(question=question, source="advisor"))
            return RealtimeToolResult(
                data={
                    "queued_questions": [item.question for item in updated.queued_poll_questions],
                    "simulation": updated.model_dump(mode="json"),
                }
            )
        if tool_name == "run_poll_now":
            question = str(payload.get("question", "")).strip()
            if not question:
                return RealtimeToolResult(ok=False, data={"message": "question is required"})
            state = await self.get_simulation(simulation_id)
            self._ensure_stage_ready(state)
            if not state.stages[state.active_stage_index].sample_citizens:
                return RealtimeToolResult(ok=False, data={"message": "citizens are still hydrating"})
            existing_question_keys = {
                self._poll_summary_identity(summary)
                for summary in state.stages[state.active_stage_index].poll_summaries
            }
            prepared_question = await self.gabriel_service.prepare_poll_question(question)
            await self.queue_poll(simulation_id, QueuePollRequest(question=prepared_question, source="advisor"))
            response = await self.run_polls(simulation_id)
            matched_summary = next(
                (
                    summary
                    for summary in reversed(response.poll_summaries)
                    if self._normalized_text_key(summary.question) == self._normalized_text_key(prepared_question)
                ),
                None,
            )
            if matched_summary is None:
                matched_summary = next(
                    (
                        summary
                        for summary in reversed(response.poll_summaries)
                        if self._poll_summary_identity(summary) not in existing_question_keys
                        and "election were held today" not in summary.question.lower()
                    ),
                    None,
                )
            if matched_summary is None:
                prepared_tokens = {
                    token
                    for token in re.findall(r"[a-z0-9]+", prepared_question.lower())
                    if len(token) > 3
                }
                matched_summary = next(
                    (
                        summary
                        for summary in reversed(response.poll_summaries)
                        if len(prepared_tokens.intersection(re.findall(r"[a-z0-9]+", summary.question.lower()))) >= 3
                    ),
                    None,
                )
            if matched_summary is None:
                matched_summary = next(
                    (
                        summary
                        for summary in reversed(response.poll_summaries)
                        if "election were held today" not in summary.question.lower()
                    ),
                    None,
                )
            return RealtimeToolResult(
                data={
                    "question": question,
                    "prepared_question": prepared_question,
                    "summary": matched_summary.model_dump(mode="json") if matched_summary else None,
                    "topline": self._poll_summary_topline(matched_summary),
                    "sample_reasons": matched_summary.sample_reasons[:2] if matched_summary else [],
                    "poll_summaries": [summary.model_dump(mode="json") for summary in response.poll_summaries],
                    "tracking": [
                        metric.model_dump(mode="json")
                        for metric in response.simulation.stages[response.simulation.active_stage_index].tracking.as_list()
                    ],
                    "simulation": response.simulation.model_dump(mode="json"),
                }
            )
        if tool_name == "run_queued_polls":
            state = await self.get_simulation(simulation_id)
            self._ensure_stage_ready(state)
            if not state.stages[state.active_stage_index].sample_citizens:
                return RealtimeToolResult(ok=False, data={"message": "citizens are still hydrating"})
            response = await self.run_polls(simulation_id)
            return RealtimeToolResult(
                data={
                    "poll_summaries": [summary.model_dump(mode="json") for summary in response.poll_summaries],
                    "tracking": [
                        metric.model_dump(mode="json")
                        for metric in response.simulation.stages[response.simulation.active_stage_index].tracking.as_list()
                    ],
                    "simulation": response.simulation.model_dump(mode="json"),
                }
            )
        if tool_name == "update_policy_board":
            state = await self.get_simulation(simulation_id)
            self._ensure_stage_ready(state)
            stage = state.stages[state.active_stage_index]
            action = str(payload.get("action", "add")).strip().lower()
            index = self._coerce_policy_note_index(payload.get("index"))
            raw_notes = payload.get("notes") or []
            if not isinstance(raw_notes, list):
                return RealtimeToolResult(ok=False, data={"message": "notes must be an array of strings"})
            notes = [self._normalize_policy_note(item) for item in raw_notes]
            notes = [note for note in notes if note]
            if action in {"add", "replace", "set"}:
                invalid_placeholder = next(
                    (note for note in notes if self._policy_board_extraction_is_placeholder(note)),
                    None,
                )
                if invalid_placeholder is not None:
                    return RealtimeToolResult(
                        ok=False,
                        data={"message": "policy board notes must be concrete lines, not placeholders"},
                    )
            if action == "clear":
                stage.policy_notes = []
            elif action == "add":
                combined = [*stage.policy_notes, *notes]
                stage.policy_notes = self._dedupe_policy_notes(combined)
            elif action == "remove":
                if index is not None and 0 <= index < len(stage.policy_notes):
                    stage.policy_notes = [note for position, note in enumerate(stage.policy_notes) if position != index]
                else:
                    removal_tokens = {" ".join(note.lower().split()) for note in notes}
                    stage.policy_notes = [
                        note for note in stage.policy_notes if " ".join(note.lower().split()) not in removal_tokens
                    ]
            elif action == "replace":
                if index is None or not (0 <= index < len(stage.policy_notes)):
                    return RealtimeToolResult(ok=False, data={"message": "replace requires a valid index"})
                if not notes:
                    return RealtimeToolResult(ok=False, data={"message": "replace requires at least one note"})
                stage.policy_notes = list(stage.policy_notes)
                stage.policy_notes[index] = notes[0]
                stage.policy_notes = self._dedupe_policy_notes(stage.policy_notes)
            elif action == "set":
                stage.policy_notes = self._dedupe_policy_notes(notes)
            else:
                return RealtimeToolResult(ok=False, data={"message": f"unknown policy board action '{action}'"})
            state.updated_at = utc_now()
            await self.store.save(state)
            return RealtimeToolResult(
                data={
                    "policy_notes": stage.policy_notes,
                    "message": "Policy board updated.",
                    "index": index,
                    "simulation": state.model_dump(mode="json"),
                }
            )
        if tool_name == "list_sample_citizens":
            state = await self.get_simulation(simulation_id)
            self._ensure_stage_ready(state)
            if not state.stages[state.active_stage_index].sample_citizens:
                return RealtimeToolResult(ok=False, data={"message": "citizens are still hydrating"})
            citizens = [citizen.model_dump(mode="json") for citizen in state.stages[state.active_stage_index].sample_citizens]
            return RealtimeToolResult(data={"citizens": citizens})
        if tool_name == "recommend_citizens_for_topic":
            state = await self.get_simulation(simulation_id)
            self._ensure_stage_ready(state)
            if not state.stages[state.active_stage_index].sample_citizens:
                return RealtimeToolResult(ok=False, data={"message": "citizens are still hydrating"})
            topic = str(payload.get("topic", "")).strip()
            if not topic:
                return RealtimeToolResult(ok=False, data={"message": "topic is required"})
            return RealtimeToolResult(
                data={
                    "topic": topic,
                    "recommendations": self._recommend_citizens(state.stages[state.active_stage_index], topic),
                }
            )
        if tool_name == "focus_citizen_by_name":
            state = await self.get_simulation(simulation_id)
            self._ensure_stage_ready(state)
            if not state.stages[state.active_stage_index].sample_citizens:
                return RealtimeToolResult(ok=False, data={"message": "citizens are still hydrating"})
            citizen_name = str(payload.get("citizen_name", "")).strip()
            if not citizen_name:
                return RealtimeToolResult(ok=False, data={"message": "citizen_name is required"})
            citizen = self._match_citizen_by_name(state.stages[state.active_stage_index], citizen_name)
            if citizen is None:
                return RealtimeToolResult(ok=False, data={"message": f"no citizen matched '{citizen_name}'"})
            state.current_room = RoomName.citizens
            state.focused_citizen_id = citizen.citizen_id
            state.updated_at = utc_now()
            await self.store.save(state)
            return RealtimeToolResult(
                data={
                    "message": f"Moved to {citizen.display_name} in the citizen room.",
                    "room": RoomName.citizens.value,
                    "citizen_id": citizen.citizen_id,
                    "citizen": citizen.model_dump(mode="json"),
                    "simulation": state.model_dump(mode="json"),
                }
            )
        if tool_name == "move_room_focus":
            state = await self.get_simulation(simulation_id)
            self._ensure_stage_ready(state)
            room_value = str(payload.get("room", "")).strip().lower()
            try:
                room = RoomName(room_value)
            except ValueError:
                return RealtimeToolResult(ok=False, data={"message": f"unknown room '{room_value}'"})
            citizen_id = str(payload.get("citizen_id", "")).strip() or None
            if room == RoomName.citizens:
                if not state.stages[state.active_stage_index].sample_citizens:
                    return RealtimeToolResult(ok=False, data={"message": "citizens are still hydrating"})
                if citizen_id is not None:
                    self._ensure_citizen_exists(state, citizen_id)
                elif state.stages[state.active_stage_index].sample_citizens:
                    citizen_id = state.stages[state.active_stage_index].sample_citizens[0].citizen_id
            else:
                citizen_id = None
            state.current_room = room
            state.focused_citizen_id = citizen_id
            state.updated_at = utc_now()
            await self.store.save(state)
            return RealtimeToolResult(
                data={
                    "message": f"Moved to {room.value}.",
                    "room": room.value,
                    "citizen_id": citizen_id,
                    "simulation": state.model_dump(mode="json"),
                }
            )
        return RealtimeToolResult(ok=False, data={"message": f"unknown tool '{tool_name}'"})

    def _create_request_from_setup_config(self, config: SimulationConfig) -> SimulationCreateRequest:
        return SimulationCreateRequest(**config.model_dump())

    def _apply_setup_patch(
        self,
        config: SimulationConfig,
        patch: SetupSessionPatchRequest | SetupSessionCreateRequest,
    ) -> SimulationConfig:
        updates: dict[str, object] = {}
        for field, value in patch.model_dump(exclude_none=True).items():
            if isinstance(value, str):
                normalized = " ".join(value.split()).strip()
                if not normalized:
                    continue
                updates[field] = normalized
            else:
                updates[field] = value
        next_config = config.model_copy(update=updates)
        if "country" in updates and ("player_role" not in updates or "opponent_role" not in updates):
            role_updates = self._auto_role_updates_for_country(config=config, next_config=next_config, updates=updates)
            if role_updates:
                next_config = next_config.model_copy(update=role_updates)
        focus_fields = {"country", "region_focus", "topic_lens"}
        if "population_description" not in updates and focus_fields.intersection(updates):
            current_population = " ".join(str(config.population_description or "").split()).strip()
            if self._looks_generated_population_frame(current_population):
                next_config = next_config.model_copy(
                    update={
                        "population_description": self._population_frame_for(
                            country=next_config.country,
                            region_focus=next_config.region_focus,
                            topic_lens=next_config.topic_lens,
                            premise=next_config.premise,
                            existing=current_population,
                        )
                    }
                )
        return next_config

    def _auto_role_updates_for_country(
        self,
        *,
        config: SimulationConfig,
        next_config: SimulationConfig,
        updates: dict[str, object],
    ) -> dict[str, str]:
        player_role, opponent_role = self._role_frame_for_country(str(next_config.country))
        role_updates: dict[str, str] = {}
        if "player_role" not in updates and self._role_looks_generated(config.player_role):
            role_updates["player_role"] = player_role
        if "opponent_role" not in updates and self._role_looks_generated(config.opponent_role):
            role_updates["opponent_role"] = opponent_role

        if "player_name" not in updates and self._name_has_known_title(config.player_name):
            role_updates["player_name"] = self._retitle_candidate_name(
                next_config.player_name,
                role_updates.get("player_role", next_config.player_role),
            )
        if "opponent_name" not in updates and self._name_has_known_title(config.opponent_name):
            role_updates["opponent_name"] = self._retitle_candidate_name(
                next_config.opponent_name,
                role_updates.get("opponent_role", next_config.opponent_role),
            )
        return role_updates

    def _role_frame_for_country(self, country: str) -> tuple[str, str]:
        normalized = country.strip().lower()
        if normalized in {"finland", "sweden", "norway", "denmark", "estonia", "canada", "australia", "new zealand"}:
            return ("incumbent prime minister", "opposition leader")
        if normalized in {"united kingdom", "uk", "britain"}:
            return ("incumbent prime minister", "opposition leader")
        if normalized in {"mexico", "brazil", "france", "poland", "south korea"}:
            return ("incumbent president", "opposition leader")
        if normalized in {"switzerland", "swiss"}:
            return ("incumbent federal councillor", "cantonal alliance leader")
        if normalized == "germany":
            return ("incumbent chancellor", "opposition leader")
        if normalized in {"texas", "california", "new york"}:
            return ("incumbent governor", "challenger attorney general")
        return ("incumbent president", "challenger governor")

    def _role_looks_generated(self, role: str) -> bool:
        normalized = " ".join(str(role or "").lower().split())
        return normalized in {
            "incumbent president",
            "challenger governor",
            "incumbent prime minister",
            "opposition leader",
            "incumbent chancellor",
            "incumbent federal councillor",
            "cantonal rival",
            "cantonal alliance leader",
            "incumbent governor",
            "challenger attorney general",
        }

    def _name_has_known_title(self, name: str) -> bool:
        return bool(
            re.match(
                r"^(President|Governor|Senator|Prime Minister|Premier|Chancellor|Mayor|Opposition Leader|Attorney General|Federal Councillor|Cantonal Rival|Cantonal Alliance Leader)\b",
                str(name or "").strip(),
            )
        )

    def _retitle_candidate_name(self, name: str, role: str) -> str:
        bare = re.sub(
            r"^(President|Governor|Senator|Prime Minister|Premier|Chancellor|Mayor|Opposition Leader|Attorney General|Federal Councillor|Cantonal Rival|Cantonal Alliance Leader)\s+",
            "",
            str(name or "").strip(),
        )
        prefix = self._display_title_for_role(role)
        return f"{prefix} {bare}".strip()

    def _display_title_for_role(self, role: str) -> str:
        normalized = " ".join(str(role or "").lower().split())
        if "president" in normalized:
            return "President"
        if "prime minister" in normalized:
            return "Prime Minister"
        if "opposition leader" in normalized:
            return "Opposition Leader"
        if "chancellor" in normalized:
            return "Chancellor"
        if "attorney general" in normalized:
            return "Attorney General"
        if "federal councillor" in normalized:
            return "Federal Councillor"
        if "cantonal alliance leader" in normalized:
            return "Cantonal Alliance Leader"
        if "cantonal rival" in normalized:
            return "Cantonal Rival"
        if "governor" in normalized:
            return "Governor"
        if "senator" in normalized:
            return "Senator"
        if "mayor" in normalized:
            return "Mayor"
        if "premier" in normalized:
            return "Premier"
        return "Leader"

    def _setup_status_for(self, config: SimulationConfig, *, started: bool = False) -> SetupSessionStatus:
        if started:
            return SetupSessionStatus.started
        return SetupSessionStatus.ready if self._setup_missing_fields(config) == [] else SetupSessionStatus.drafting

    def _setup_missing_fields(self, config: SimulationConfig) -> list[str]:
        required_fields = [
            "country",
            "player_name",
            "player_role",
            "opponent_name",
            "opponent_role",
            "population_description",
            "visual_style",
        ]
        missing: list[str] = []
        for field in required_fields:
            value = getattr(config, field, "")
            if not str(value).strip():
                missing.append(field)
        return missing

    def _setup_guidance_snapshot(
        self,
        config: SimulationConfig,
        *,
        chamber_reply: str,
        applied_updates: list[str] | None = None,
        open_questions: list[str] | None = None,
        next_actions: list[str] | None = None,
        config_updates: SetupSessionPatchRequest | None = None,
        launch_now: bool = False,
    ) -> SetupChamberGuidance:
        missing = self._setup_missing_fields(config)
        readiness = "ready" if not missing else "needs_input"
        merged_questions = list(open_questions or [])
        for field in missing:
            question = f"Set {field.replace('_', ' ')}."
            if question not in merged_questions:
                merged_questions.append(question)
        merged_actions = list(next_actions or [])
        if not merged_actions:
            if readiness == "ready":
                merged_actions = [
                    "Say start when you want me to launch the run.",
                    "Or tell me what world, institution, or future you want to examine first.",
                ]
            else:
                merged_actions = ["Fill the missing setup fields before launch."]
        return SetupChamberGuidance(
            chamber_reply=chamber_reply,
            readiness=readiness,
            launch_now=launch_now and readiness == "ready",
            applied_updates=applied_updates or [],
            open_questions=merged_questions[:3],
            next_actions=merged_actions[:3],
            config_updates=config_updates or SetupSessionPatchRequest(),
        )

    def _describe_config_delta(self, before: SimulationConfig, after: SimulationConfig) -> list[str]:
        labels = {
            "title": "title",
            "country": "country",
            "player_name": "player",
            "player_role": "player_role",
            "opponent_name": "opponent",
            "opponent_role": "opponent_role",
            "opponent_voice": "opponent_voice",
            "population_description": "population",
            "region_focus": "region_focus",
            "topic_lens": "topic_lens",
            "premise": "premise",
            "stakes": "stakes",
            "persona_count": "persona_count",
            "stage_count": "stage_count",
            "visual_style": "visual_style",
        }
        before_payload = before.model_dump()
        after_payload = after.model_dump()
        changes: list[str] = []
        for field, label in labels.items():
            if before_payload.get(field) != after_payload.get(field):
                changes.append(f"{label} -> {after_payload[field]}")
        return changes

    def _setup_patch_reply(self, config: SimulationConfig, applied_updates: list[str]) -> str:
        if applied_updates:
            lead = self._natural_setup_patch_lead(applied_updates)
        else:
            lead = "The broad default setup still holds."
        if self._setup_missing_fields(config):
            return lead + " Give me the next concrete nudge before launch."
        return lead + " Give me another nudge, or say start when you want to begin."

    def _natural_setup_patch_lead(self, applied_updates: list[str]) -> str:
        fields: dict[str, str] = {}
        for change in applied_updates:
            field, separator, value = change.partition(" -> ")
            if not separator:
                continue
            fields[field.strip()] = value.strip()
        if "country" in fields:
            return f"We will stage the run around {fields['country']}."
        if "premise" in fields:
            return "I folded that premise into the opening world."
        if "topic_lens" in fields:
            return "I set that as the main policy lens."
        if "population_description" in fields:
            return "I reshaped the population sample around that group."
        if "persona_count" in fields or "stage_count" in fields:
            parts = []
            if "persona_count" in fields:
                parts.append(f"{fields['persona_count']} personas")
            if "stage_count" in fields:
                parts.append(f"{fields['stage_count']} stages")
            return f"I adjusted the run to {' and '.join(parts)}."
        if "visual_style" in fields:
            return "I updated the visual treatment."
        if "council_roster" in fields:
            return "I reshaped the advisory table."
        return "I updated the setup."

    def _bounded_setup_turns(self, turns: list[ConversationTurn]) -> list[ConversationTurn]:
        return turns[-24:]

    def _looks_generated_population_frame(self, value: str) -> bool:
        text = " ".join(str(value or "").split()).strip()
        if not text:
            return True
        if text == self.settings.default_population_description:
            return True
        return text.startswith("A representative sample of people in ") or text.startswith("A representative sample of the current ")

    def _population_frame_for(
        self,
        *,
        country: str,
        region_focus: str | None,
        topic_lens: str | None,
        premise: str | None,
        existing: str | None = None,
    ) -> str:
        existing_text = " ".join(str(existing or "").split()).strip()
        focus = " ".join(part for part in [region_focus or "", topic_lens or ""] if part).lower()
        broad_national_frame = not region_focus or region_focus.lower() == "national field"
        if existing_text and not self._looks_generated_population_frame(existing_text):
            return existing_text
        if not broad_national_frame and any(keyword in focus for keyword in ("education", "school", "student", "teacher", "municipal", "classroom", "tutoring", "grading", "pupil")):
            return (
                f"A representative sample of people in {country}, with realistic variation across region, class, age, ideology, family structure, and AI exposure, "
                "weighted toward people whose lives are strongly touched by schools, local public administration, tutoring, credentials, and unequal access to AI-enabled learning."
            )
        if not broad_national_frame and any(keyword in focus for keyword in ("health", "care", "hospital", "clinic", "patient")):
            return (
                f"A representative sample of people in {country}, with realistic variation across region, class, age, ideology, family structure, and AI exposure, "
                "weighted toward people whose lives are strongly touched by care systems, hospitals, clinics, insurance, disability, aging, and family caregiving."
            )
        if not broad_national_frame and any(keyword in focus for keyword in ("factory", "manufactur", "industrial", "logistics", "port", "freight", "warehouse")):
            return (
                f"A representative sample of people in {country}, with realistic variation across region, class, age, ideology, family structure, and AI exposure, "
                "weighted toward people whose livelihoods touch factories, logistics, freight, ports, warehouses, local suppliers, and the nearby service economy."
            )
        region_clause = f" across {region_focus}" if region_focus and region_focus.lower() != "national field" else ""
        if not focus.strip():
            if country.strip().lower() == "united states":
                return self.settings.default_population_description
            return (
                f"A representative sample of the current adult population in {country}{region_clause}, "
                "with realistic variation across region, class, industry, education, ideology, age, family structure, and AI exposure."
            )
        weighted_focus = topic_lens or "the broad AGI transition"
        return (
            f"A representative sample of people in {country}{region_clause}, with realistic variation across class, education, industry, "
            f"family structure, ideology, ethnicity, age, and AI exposure, weighted toward people most directly touched by {weighted_focus}."
        )

    async def wait_for_pending(self, simulation_id: str) -> None:
        task = self._tasks.get(simulation_id)
        if task:
            await task
        tracking_tasks = [
            task
            for (candidate_simulation_id, _stage_index), task in self._tracking_tasks.items()
            if candidate_simulation_id == simulation_id
        ]
        if tracking_tasks:
            await asyncio.gather(*tracking_tasks)

    async def _prepare_stage(self, simulation_id: str) -> None:
        state = await self.get_simulation(simulation_id)
        background_tasks: list[asyncio.Task] = []
        try:
            roster_task: asyncio.Task[list[CouncilAdvisorProfile]] | None = None
            if self._should_generate_council_roster(state.config):
                roster_task = asyncio.create_task(self.orchestrator.build_council_roster(state.config))
                background_tasks.append(roster_task)
            await self._set_progress(
                state,
                phase=PreparationPhase.seeding,
                label="Preparing the chapter frame",
                detail="Loading the representative sample frame before the opening chapter is written.",
                percent=12,
            )
            personas = await self._load_personas(simulation_id)
            persona_task: asyncio.Task[pd.DataFrame] | None = None
            if personas is not None:
                state.persona_count_ready = len(personas)
                await self.store.save(state)
            else:
                persona_task = asyncio.create_task(self.gabriel_service.ensure_personas(
                    simulation_id=simulation_id,
                    population_description=state.config.population_description,
                    persona_count=state.config.persona_count,
                    save_dir=self.store.simulation_dir(simulation_id),
                ))
                background_tasks.append(persona_task)
                await self._set_progress(
                    state,
                    phase=PreparationPhase.seeding,
                    label="Starting world and sample in parallel",
                    detail="The representative sample is being seeded while the orchestrator writes the opening chapter.",
                    percent=18,
                )

            existing_current_stage = (
                state.stages[state.active_stage_index]
                if len(state.stages) > state.active_stage_index
                else None
            )
            previous_stage = (
                state.stages[state.active_stage_index - 1]
                if state.active_stage_index > 0 and len(state.stages) >= state.active_stage_index
                else None
            )
            prior_tracking = previous_stage.tracking if previous_stage else None
            prior_polls = previous_stage.poll_summaries if previous_stage else []
            if existing_current_stage is not None:
                stage = existing_current_stage
            else:
                await self._set_progress(
                    state,
                    phase=PreparationPhase.stagewriting,
                    label="Writing the next world state",
                    detail="Resolving technology diffusion, politics, prices, and public mood for the next stage.",
                    percent=34,
                )
                stage = await self.orchestrator.compose_stage(
                    state=state,
                    previous_stage=previous_stage,
                    tracking=prior_tracking,
                    poll_summaries=prior_polls,
                    queued_poll_questions=[item.question for item in state.queued_poll_questions],
                    progress_callback=lambda label, detail, percent: self._set_progress(
                        state,
                        phase=PreparationPhase.stagewriting,
                        label=label,
                        detail=detail,
                        percent=percent,
                    ),
                )
                if len(state.stages) > stage.index:
                    state.stages[stage.index] = stage
                else:
                    state.stages.append(stage)
                if not state.config.title.strip() and stage.title.strip():
                    state.config = state.config.model_copy(update={"title": stage.title.strip()})
                state.updated_at = utc_now()
                await self.store.save(state)
            await self._set_progress(
                state,
                phase=PreparationPhase.media,
                label="Rendering briefing while citizens prepare",
                detail="Generating the chapter reel while the representative sample catches up to the new world.",
                percent=56,
            )
            media_task = asyncio.create_task(self.orchestrator.materialize_stage_media(
                stage=stage,
                asset_dir=self.store.asset_dir(simulation_id, state.active_stage_index),
            ))
            background_tasks.append(media_task)
            if roster_task is not None:
                try:
                    generated_roster = await roster_task
                except Exception:
                    generated_roster = self._default_council_roster()
                if generated_roster:
                    state.config = state.config.model_copy(update={"council_roster": generated_roster})
                    state.updated_at = utc_now()
                    await self.store.save(state)
            await media_task
            stage.featurettes = []
            stage.featurettes_status = "queued"
            stage.featurettes_error = None
            stage.sample_citizens = list(stage.sample_citizens or [])
            stage.queued_poll_questions = [item.question for item in state.queued_poll_questions]
            self._decorate_asset_urls(stage)
            state.approval_rating = stage.tracking.approval.value
            state.standard_questions = self.gabriel_service.standard_questions(
                state.config.player_name,
                state.config.opponent_name,
                stage,
            )
            if len(state.stages) > stage.index:
                state.stages[stage.index] = stage
            else:
                state.stages.append(stage)
            state.status = SimulationStatus.stage_ready
            state.current_room = RoomName.briefing
            state.focused_citizen_id = None
            state.progress = StageProgress(
                phase=PreparationPhase.citizen_updates,
                label="Opening the chapter",
                detail="The reel and core rooms are live while citizens and polling catch up in the background.",
                percent=72,
            )
            state.updated_at = utc_now()
            await self.store.save(state)
            self._queue_stage_featurettes(simulation_id, stage.index)
            if persona_task is not None:
                personas = await persona_task
                await self._save_personas(simulation_id, personas)
                state.persona_count_ready = len(personas)
                state.updated_at = utc_now()
                await self.store.save(state)
            if personas is None:
                raise RuntimeError("personas not ready")
            await self._set_progress(
                state,
                phase=PreparationPhase.citizen_updates,
                label="Refreshing citizen lives",
                detail="The chapter is live. Representative households are still being updated into this world.",
                percent=80,
            )
            personas = await self.gabriel_service.update_personas_for_stage(
                personas=personas,
                stage=stage,
                incumbent_name=state.incumbent_name,
                player_name=state.config.player_name,
                opponent_name=state.config.opponent_name,
                save_dir=self.store.persona_update_dir(simulation_id, stage.index),
            )
            stage.sample_citizens = self.gabriel_service.pick_sample_citizens(personas, stage=stage)
            self._backfill_sample_citizen_town_hall_questions(state, stage)
            stage.queued_poll_questions = [item.question for item in state.queued_poll_questions]
            if len(state.stages) > stage.index:
                state.stages[stage.index] = stage
            else:
                state.stages.append(stage)
            if stage.sample_citizens:
                state.focused_citizen_id = stage.sample_citizens[0].citizen_id
            state.updated_at = utc_now()
            await self._save_personas(simulation_id, personas)
            await self.store.save(state)
            self._queue_stage_tracking_poll(simulation_id, stage.index)
            self._queue_stage_town_hall_questions(simulation_id, stage.index)
        except Exception as exc:  # pragma: no cover - surfaced through API
            unfinished = [task for task in background_tasks if not task.done()]
            for task in unfinished:
                task.cancel()
            if background_tasks:
                await asyncio.gather(*background_tasks, return_exceptions=True)
            if state.status == SimulationStatus.stage_ready:
                logger.exception(
                    "Stage %s for simulation %s stayed playable after late citizen hydration failed",
                    state.active_stage_index,
                    simulation_id,
                )
                state.progress = StageProgress(
                    phase=PreparationPhase.citizen_updates,
                    label="Citizens still arriving",
                    detail="The chapter is playable. Citizen interviews and poll extras are still catching up.",
                    percent=72,
                )
                state.updated_at = utc_now()
                await self.store.save(state)
                return
            state.status = SimulationStatus.error
            state.error = str(exc)
            state.progress = StageProgress(
                phase=PreparationPhase.error,
                label="Stage generation failed",
                detail=str(exc),
                percent=100,
            )
            state.updated_at = utc_now()
            await self.store.save(state)
            raise

    async def _set_progress(
        self,
        state: SimulationState,
        *,
        phase: PreparationPhase,
        label: str,
        detail: str,
        percent: int,
    ) -> None:
        state.progress = StageProgress(phase=phase, label=label, detail=detail, percent=percent)
        state.updated_at = utc_now()
        await self.store.save(state)

    def _ensure_stage_ready(self, state: SimulationState) -> None:
        if state.status != SimulationStatus.stage_ready or state.active_stage_index >= len(state.stages):
            raise RuntimeError("stage is not ready yet")

    def _ensure_citizens_ready(self, state: SimulationState) -> None:
        self._ensure_stage_ready(state)
        if not state.stages[state.active_stage_index].sample_citizens:
            raise RuntimeError("citizens are still hydrating")

    def _ensure_citizen_exists(self, state: SimulationState, citizen_id: str | None) -> None:
        self._ensure_citizens_ready(state)
        citizen_ids = {citizen.citizen_id for citizen in state.stages[state.active_stage_index].sample_citizens}
        if not citizen_id or citizen_id not in citizen_ids:
            raise KeyError(f"citizen '{citizen_id}' not found")

    def _thread_key(
        self,
        stage_index: int,
        role: RealtimeRole,
        citizen_id: str | None,
        advisor_mode: AdvisorMode = AdvisorMode.solo,
        auditorium_mode: AuditoriumMode = AuditoriumMode.debate,
    ) -> str:
        if role == RealtimeRole.advisor:
            if advisor_mode == AdvisorMode.council:
                return f"stage:{stage_index}:advisor:council"
            return f"stage:{stage_index}:advisor"
        if role == RealtimeRole.debate:
            if auditorium_mode == AuditoriumMode.town_hall:
                return f"stage:{stage_index}:debate:town_hall"
            return f"stage:{stage_index}:debate"
        return f"stage:{stage_index}:citizen:{citizen_id}"

    def _thread_turns(
        self,
        state: SimulationState,
        role: RealtimeRole,
        citizen_id: str | None,
        advisor_mode: AdvisorMode = AdvisorMode.solo,
        auditorium_mode: AuditoriumMode = AuditoriumMode.debate,
    ) -> list[ConversationTurn]:
        key = self._thread_key(state.active_stage_index, role, citizen_id, advisor_mode, auditorium_mode)
        return list(state.conversation_threads.get(key, []))

    def _merged_auditorium_turns(self, state: SimulationState) -> list[ConversationTurn]:
        stage_index = state.active_stage_index
        debate_key = self._thread_key(stage_index, RealtimeRole.debate, None, AdvisorMode.solo, AuditoriumMode.debate)
        town_hall_key = self._thread_key(stage_index, RealtimeRole.debate, None, AdvisorMode.solo, AuditoriumMode.town_hall)
        merged: dict[str, ConversationTurn] = {}
        for turn in [*state.conversation_threads.get(debate_key, []), *state.conversation_threads.get(town_hall_key, [])]:
            merged[turn.id] = turn
        return sorted(
            merged.values(),
            key=lambda turn: (
                turn.created_at,
                turn.id,
            ),
        )

    def _append_turns(self, state: SimulationState, thread_key: str, turns: list[ConversationTurn]) -> None:
        existing = list(state.conversation_threads.get(thread_key, []))
        existing.extend(turns)
        state.conversation_threads[thread_key] = existing[-48:]

    def _normalize_council_speech(self, text: str) -> str:
        cleaned = self._plain_language_cleanup(" ".join(str(text or "").split()).strip()).strip("\"'“”‘’").strip()
        if not cleaned:
            return ""
        cleaned = self._collapse_adjacent_word_repetitions(cleaned)
        cleaned = re.sub(
            r"(?<=[a-z0-9])\.\s+([a-z][a-z'-]*)",
            lambda match: f". {match.group(1)}"
            if match.group(1).lower() in {"the", "a", "an", "this", "that", "these", "those", "it", "we", "they", "you", "he", "she", "there"}
            else f", {match.group(1)}",
            cleaned,
        )
        cleaned = re.sub(r"\s*;\s*", ". ", cleaned)
        cleaned = re.sub(r"\s*[–—]\s*", ". ", cleaned)
        sentences = [part.strip() for part in re.split(r"(?<=[.!?])\s+", cleaned) if part.strip()]
        sentences = [
            f"{sentence[:1].upper()}{sentence[1:]}" if sentence and sentence[:1].islower() else sentence
            for sentence in sentences
        ]
        if (
            len(sentences) > 1
            and len(sentences[0].split()) >= 18
            and sentences[1].lower().startswith(("the win ", "the point ", "that means ", "the catch ", "the question "))
            and len(" ".join(sentences).split()) >= 26
        ):
            cleaned = sentences[0].strip()
        else:
            cleaned = " ".join(sentences[:3]).strip() if len(sentences) > 3 else " ".join(sentences).strip()
        words = cleaned.split()
        if len(words) > 110:
            trimmed_words = words[:110]
            while trimmed_words and trimmed_words[-1].lower() in {"a", "an", "the", "and", "or", "to", "of", "for", "with", "in", "on"}:
                trimmed_words.pop()
            cleaned = " ".join(trimmed_words).rstrip(",;:")
        cleaned = self._collapse_adjacent_word_repetitions(cleaned.strip())
        if cleaned and cleaned[-1] not in ".!?":
            cleaned = f"{cleaned}."
        return cleaned

    def _normalize_council_candidate_text(
        self,
        state: SimulationState,
        text: str,
        advisor_name: str,
    ) -> str:
        raw = " ".join(str(text or "").split()).strip()
        if not raw:
            return ""
        roster_names = {advisor.name.lower() for advisor in self._council_roster_for(state)}
        speaker_match = re.match(r"^([A-Za-z][A-Za-z' .-]{0,40})\s*:\s*(.+)$", raw)
        if speaker_match:
            claimed_speaker = speaker_match.group(1).strip()
            if claimed_speaker.lower() in roster_names:
                if claimed_speaker.lower() != advisor_name.lower():
                    return ""
                raw = speaker_match.group(2).strip()
        if raw.split(":", 1)[0].strip().lower() in roster_names and ":" in raw:
            return ""
        return self._normalize_council_speech(raw)

    def _collapse_adjacent_word_repetitions(self, text: str) -> str:
        cleaned = str(text or "")
        pattern = re.compile(r"\b([A-Za-z][A-Za-z'-]*)\b(\s+\1\b)+", flags=re.IGNORECASE)
        previous = None
        while cleaned != previous:
            previous = cleaned
            cleaned = pattern.sub(lambda match: match.group(1), cleaned)
        return cleaned

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
        )
        for pattern, replacement in replacements:
            cleaned = re.sub(pattern, replacement, cleaned, flags=re.IGNORECASE)
        return " ".join(cleaned.split()).strip()

    def _normalize_town_hall_cue(self, text: str, *, max_chars: int = 132) -> str:
        cleaned = self._plain_language_cleanup(" ".join(str(text or "").split()).strip()).strip("\"'“”‘’")
        if not cleaned:
            return ""
        cleaned = self._collapse_adjacent_word_repetitions(cleaned)
        cleaned = re.sub(r"\s*([,;:.!?])", r"\1", cleaned)
        cleaned = cleaned[:max_chars].rstrip(" ,.;:-")
        if cleaned and cleaned[-1] not in ".!?":
            cleaned = f"{cleaned}."
        return cleaned

    def _normalize_town_hall_question_text(self, text: str) -> str:
        cleaned = self._plain_language_cleanup(" ".join(str(text or "").split()).strip()).strip("\"'“”‘’")
        if not cleaned:
            return ""
        cleaned = self._collapse_adjacent_word_repetitions(cleaned)
        cleaned = re.sub(r"\s*([,;:.!?])", r"\1", cleaned)
        cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()
        words = cleaned.split()
        if len(words) > 42:
            first_sentence = re.split(r"(?<=[?!])\s+|(?<=\.)\s+", cleaned, maxsplit=1)[0].strip()
            cleaned = first_sentence if len(first_sentence.split()) <= 42 else " ".join(first_sentence.split()[:42]).rstrip(" ,;:.!?")
        if self._town_hall_question_has_dangling_ending(cleaned):
            return ""
        if cleaned and cleaned[-1] not in "?!":
            cleaned = f"{cleaned}?"
        return cleaned

    def _normalize_town_hall_reply_text(self, text: str) -> str:
        cleaned = self._normalize_council_speech(text)
        if not cleaned:
            return ""
        words = cleaned.split()
        if len(words) > 34:
            cleaned = " ".join(words[:34]).rstrip(" ,;:")
            if cleaned and cleaned[-1] not in ".!?":
                cleaned = f"{cleaned}."
        return cleaned

    def _town_hall_question_has_dangling_ending(self, text: str) -> bool:
        stripped = str(text or "").strip().rstrip(".!?")
        if not stripped:
            return True
        if re.search(r"\b(?:can't|cannot|can not|cant)\s+just(?:\s+[A-Za-z'-]+){1,2}$", stripped, flags=re.IGNORECASE):
            return True
        if re.search(
            r"\b(?:once|when|if|while|because)\s+(?:i'm|you're|we're|they're|he's|she's|it's|that's|there's|here's)\s+[A-Za-z'-]+$",
            stripped,
            flags=re.IGNORECASE,
        ):
            return True
        tail = stripped.split()[-1].lower()
        return tail in {
            "and",
            "or",
            "but",
            "because",
            "if",
            "when",
            "while",
            "that",
            "which",
            "just",
            "once",
            "i'm",
            "you're",
            "we're",
            "they're",
            "he's",
            "she's",
            "it's",
            "that's",
            "there's",
            "here's",
            "using",
            "stuck",
        }

    def _maybe_apply_council_board_notes(self, stage: StagePackage, board_notes: list[str]) -> list[str]:
        normalized = self._dedupe_policy_notes(
            [
                self._normalize_policy_note(note)
                for note in board_notes
                if str(note).strip() and not self._policy_board_extraction_is_placeholder(note)
            ]
        )
        if not normalized:
            return stage.policy_notes
        return normalized[:5]

    def _resolve_town_hall_citizen(self, stage: StagePackage, citizen_id: str | None) -> CitizenSnapshot:
        if citizen_id:
            for citizen in stage.sample_citizens:
                if citizen.citizen_id == citizen_id:
                    return citizen
        return stage.sample_citizens[0]

    def _latest_town_hall_exchange(
        self,
        state: SimulationState,
    ) -> tuple[ConversationTurn | None, ConversationTurn | None]:
        stage_index = state.active_stage_index
        town_hall_key = self._thread_key(stage_index, RealtimeRole.debate, None, AdvisorMode.solo, AuditoriumMode.town_hall)
        turns = list(state.conversation_threads.get(town_hall_key, []))
        latest_user_index: int | None = None
        for index in range(len(turns) - 1, -1, -1):
            if turns[index].speaker == "user" and turns[index].text.strip():
                latest_user_index = index
                break
        if latest_user_index is None:
            return None, None
        latest_question: ConversationTurn | None = None
        opponent_name = state.config.opponent_name.strip().lower()
        for index in range(latest_user_index - 1, -1, -1):
            turn = turns[index]
            speaker_name = (turn.speaker_name or "").strip().lower()
            if turn.speaker != "assistant" or not turn.text.strip() or speaker_name == opponent_name:
                continue
            latest_question = turn
            break
        return latest_question, turns[latest_user_index]

    def _town_hall_pressure_cue(self, citizen: CitizenSnapshot) -> str:
        source = (
            citizen.current_worries
            or citizen.current_update
            or citizen.current_hopes
            or citizen.recent_ai_moment
            or citizen.summary
            or citizen.support_label
        )
        cleaned = " ".join(str(source or "").split()).strip()
        if not cleaned:
            return "One live question from the crowd."
        return self._normalize_town_hall_cue(cleaned, max_chars=116) or "One live question from the crowd."

    def _fallback_town_hall_question(self, state: SimulationState, citizen: CitizenSnapshot) -> str:
        current_stage = state.stages[state.active_stage_index]
        source_options = [
            citizen.current_worries,
            citizen.current_update,
            citizen.recent_ai_moment,
            citizen.current_hopes,
            citizen.summary,
            self.realtime_prompts._stage_gain(current_stage, 140),
            self.realtime_prompts._stage_split(current_stage, 140),
            citizen.role,
        ]
        cleaned = ""
        backup = ""
        for source in source_options:
            candidate = " ".join(str(source or "").split()).strip().strip("\"'“”‘’")
            if not candidate:
                continue
            candidate = re.split(r"(?<=[.!?])\s+", candidate, maxsplit=1)[0].strip().rstrip(".!?")
            candidate = candidate.rstrip(".")
            if not backup:
                backup = candidate
            if candidate and not candidate.endswith("..") and "..." not in candidate:
                cleaned = candidate
                break
        if not cleaned:
            cleaned = backup
        if not cleaned:
            return "What does your plan actually do for people like me?"
        lead = cleaned[0].upper() + cleaned[1:] if len(cleaned) > 1 else cleaned.upper()
        stage_split = self.realtime_prompts._stage_split(current_stage, 120)
        stage_gain = self.realtime_prompts._stage_gain(current_stage, 120)
        access_channel = self.realtime_prompts._stage_access_channel(current_stage, 110)
        object_hint = access_channel or stage_gain or "the service or account my household depends on"
        if stage_split:
            return self._normalize_town_hall_question_text(
                f"{lead}. If {object_hint.lower()} gets cut off, repriced, or delayed, who can reverse it fast?"
            )
        if stage_gain:
            return self._normalize_town_hall_question_text(
                f"{lead}. Are you protecting that gain, or changing who gets access to it?"
            )
        if re.match(r"^(?:i|my|we|our)\b", cleaned, flags=re.IGNORECASE):
            return self._normalize_town_hall_question_text(f"{lead}. What would actually change in the account, queue, job, or bill next month?")
        return self._normalize_town_hall_question_text(f"{lead}. What power does your plan give us when the account, queue, or service goes wrong?")

    def _fallback_town_hall_opponent_reply(
        self,
        state: SimulationState,
        question_turn: ConversationTurn,
        player_turn: ConversationTurn,
    ) -> str:
        stage = state.stages[state.active_stage_index]
        question_hint = self._clip(question_turn.text, 120) or "That question matters"
        player_hint = self._clip(player_turn.text, 150) or "the player's answer"
        upside = self.realtime_prompts._stage_gain(stage, 96) or "the gains people already use"
        split = self.realtime_prompts._stage_split(stage, 108) or "who actually keeps the upside when the system speeds up"
        fallback = (
            f"{question_hint}. {player_hint} still ducks the real split: {split}. "
            f"My answer is to keep {upside} moving, but tie it to a protection people can actually feel."
        )
        return self._normalize_town_hall_reply_text(fallback) or (
            "That question is fair. I would keep the gains moving, but tie them to a protection people can actually feel."
        )

    def _backfill_sample_citizen_town_hall_questions(self, state: SimulationState, stage: StagePackage) -> None:
        for citizen in stage.sample_citizens:
            question = self._normalize_town_hall_question_text(" ".join(citizen.town_hall_question.split()).strip())
            citizen.town_hall_question = question or self._fallback_town_hall_question(state, citizen)
            cue = self._normalize_town_hall_cue(" ".join(citizen.town_hall_cue.split()).strip())
            citizen.town_hall_cue = cue or self._town_hall_pressure_cue(citizen)

    def _turn_signals_policy_commitment(self, text: str) -> bool:
        normalized = " ".join(text.lower().split())
        if not normalized:
            return False
        cues = (
            "put that on the board",
            "put this on the board",
            "add that to the board",
            "keep that on the board",
            "scratch that off",
            "take that off the board",
            "replace item",
            "replace that with",
            "let's do",
            "that is the plan",
            "that's the plan",
            "our platform is",
            "our agenda is",
            "i want to run on",
            "we should run on",
            "go with",
        )
        if any(cue in normalized for cue in cues):
            return True
        return normalized.startswith(("keep ", "drop ", "remove ", "replace ", "add ", "fund ", "speed ", "open ", "require ", "offer ", "share "))

    def _direct_policy_board_notes_from_turn(
        self,
        stage: StagePackage,
        text: str,
        thread_turns: list[ConversationTurn] | None = None,
    ) -> list[str]:
        raw = " ".join(str(text or "").split()).strip()
        if not raw:
            return []
        if not self._policy_board_change_was_requested(raw):
            return []
        if (
            re.search(
                r"\b(?:what|which|where|how|do\s+you\s+think)\b.{0,90}\b(?:put|add|write)\b.{0,40}\b(?:board|whiteboard|talking\s+points)\b",
                raw,
                flags=re.IGNORECASE,
            )
            and not re.search(r"\b(?:with|as|to\s+say)\b|[:;-]", raw, flags=re.IGNORECASE)
        ):
            return []
        extracted = ""
        raw_for_extraction = re.sub(
            r"^[A-Z][A-Za-z' .-]{1,40},?\s+(?:can|could|would|will)\s+you\s+",
            "",
            raw,
            flags=re.IGNORECASE,
        )
        direct_match = re.search(
            r"\b(?:put|add|write|place|keep|save|capture|note|pin)\s+(?P<note>.+?)\s+(?:on|onto|to|into)\s+(?:the\s+)?(?:policy\s+)?(?:ideas\s+)?(?:board|whiteboard|talking\s+points)\b",
            raw_for_extraction,
            flags=re.IGNORECASE,
        )
        if direct_match:
            extracted = direct_match.group("note")
            if extracted.strip().lower() in {"it", "this", "that", "this one", "that one"} and ":" in raw:
                extracted = ""
            if self._policy_board_extraction_is_placeholder(extracted):
                extracted = ""
        if not extracted and re.search(
            r"\b(?:best|those|these|your|their|the)\s+(?:ones|ideas|recommendations|proposals|planks)\b",
            raw,
            flags=re.IGNORECASE,
        ):
            extracted = next(
                (
                    " ".join(turn.text.split()).strip()
                    for turn in reversed(thread_turns or [])
                    if turn.speaker == "assistant" and turn.text.strip()
                ),
                "",
            )
            distilled = self._extract_policy_points_from_text(extracted)
            if distilled:
                extracted = "; ".join(distilled[: self._requested_policy_board_note_count(raw) or 3])
            else:
                extracted = ""
        if not extracted:
            trailing_match = re.search(
                r"\b(?:update|change|set|make|rewrite|replace|add\s+to|put\s+on|write\s+on)\b.{0,48}\b(?:board|whiteboard|talking\s+points)\b(?:\s*(?:to\s+say|with|as|that|this|:|-))\s*(?P<note>.+)$",
                raw,
                flags=re.IGNORECASE,
            )
            if trailing_match:
                extracted = trailing_match.group("note")
        if not extracted and ":" in raw:
            extracted = raw.split(":", 1)[1]
        extracted = re.sub(r"^(?:that|this|it|please|can you|could you|let'?s)\s+", "", extracted.strip(), flags=re.IGNORECASE)
        if self._policy_board_extraction_is_placeholder(extracted):
            extracted = ""
        if not extracted and re.search(
            r"\b(?:your|our|these|those|the)\s+(?:ideas|recommendations|proposal|proposals|plan|thoughts)\b",
            raw,
            flags=re.IGNORECASE,
        ):
            extracted = next(
                (
                    " ".join(turn.text.split()).strip()
                    for turn in reversed(thread_turns or [])
                    if turn.speaker == "assistant" and turn.text.strip()
                ),
                "",
            )
            distilled = self._extract_policy_points_from_text(extracted)
            extracted = "; ".join(distilled[: self._requested_policy_board_note_count(raw) or 3]) if distilled else extracted
        if not extracted and re.search(
            r"\b(?:put|add|write|place|keep|save|capture|note)\s+(?:that|this|it)\s+(?:(?:on|onto|to|into)\s+(?:the\s+)?(?:policy\s+)?(?:ideas\s+)?(?:board|whiteboard|talking\s+points)|down)\b",
            raw,
            flags=re.IGNORECASE,
        ):
            extracted = next(
                (
                    " ".join(turn.text.split()).strip()
                    for turn in reversed(thread_turns or [])
                    if turn.speaker == "assistant" and turn.text.strip()
                ),
                "",
            )
            distilled = self._extract_policy_points_from_text(extracted)
            extracted = "; ".join(distilled[: self._requested_policy_board_note_count(raw) or 3]) if distilled else extracted
        notes = self._extract_policy_points_from_text(extracted)
        if not notes and self._policy_board_change_was_requested(raw):
            latest_advisor_line = next(
                (
                    " ".join(turn.text.split()).strip()
                    for turn in reversed(thread_turns or [])
                    if turn.speaker == "assistant" and turn.text.strip()
                ),
                "",
            )
            if latest_advisor_line:
                notes = self._extract_policy_points_from_text(latest_advisor_line)
        if not notes:
            return []
        replace_board = bool(
            re.search(
                r"\b(?:set|replace|rewrite)\b.{0,24}\b(?:board|whiteboard|talking\s+points)\b|\b(?:make|change)\b.{0,24}\b(?:board|whiteboard|talking\s+points)\b.{0,16}\b(?:say|to)\b",
                raw,
                flags=re.IGNORECASE,
            )
        )
        if replace_board:
            return self._dedupe_policy_notes(notes)[:5]
        return self._dedupe_policy_notes([*stage.policy_notes, *notes])[:5]

    def _policy_board_change_was_requested(self, text: str) -> bool:
        raw = " ".join(str(text or "").split()).strip()
        if not raw:
            return False
        normalized = f" {raw.lower()} "
        implicit_board_request = bool(
            re.search(
                r"\b(?:put|write|add|keep|save|capture|note)\s+(?:that|this|it)\s+down\b",
                raw,
                flags=re.IGNORECASE,
            )
            or re.search(
                r"\b(?:put|write|add|keep|save|capture|note)\s+(?:that|this|it|one)\b",
                raw,
                flags=re.IGNORECASE,
            )
        )
        explicit_board_request = bool(re.search(r"\b(?:board|whiteboard|policy\s+ideas|talking\s+points)\b", normalized))
        if not explicit_board_request and not implicit_board_request:
            return False
        return any(
            cue in normalized
            for cue in (
                " add ",
                " put ",
                " write ",
                " place ",
                " update ",
                " change ",
                " set ",
                " make ",
                " replace ",
                " keep ",
                " save ",
                " capture ",
                " note ",
                " down ",
            )
        )

    def _requested_policy_board_note_count(self, text: str) -> int:
        normalized = " ".join(str(text or "").lower().split())
        if not normalized:
            return 0
        number_words = {
            "one": 1,
            "single": 1,
            "two": 2,
            "couple": 2,
            "pair": 2,
            "three": 3,
            "four": 4,
            "five": 5,
        }
        for word, count in number_words.items():
            if re.search(
                rf"\b{word}\b.{{0,32}}\b(?:planks?|ideas?|items?|lines?|polic(?:y|ies))\b",
                normalized,
            ):
                return count
        digit_match = re.search(r"\b([1-5])\b.{0,32}\b(?:planks?|ideas?|items?|lines?|polic(?:y|ies))\b", normalized)
        return int(digit_match.group(1)) if digit_match else 0

    def _policy_board_extraction_is_placeholder(self, text: str) -> bool:
        normalized = " ".join(str(text or "").lower().strip(" .!?;:-").split())
        if not normalized:
            return True
        placeholders = {
            "it",
            "this",
            "that",
            "this one",
            "that one",
            "the idea",
            "this idea",
            "that idea",
            "the policy idea",
            "the policy",
            "a policy",
            "the plan",
            "this plan",
            "the best idea",
            "best idea",
            "the best one",
            "best one",
            "the best ones",
            "best ones",
            "the best policy idea",
            "best policy idea",
            "the best concrete idea",
            "best concrete idea",
            "the best concrete policy idea",
            "best concrete policy idea",
            "idea",
            "ideas",
            "your idea",
            "your ideas",
            "our idea",
            "our ideas",
            "their idea",
            "their ideas",
            "these ideas",
            "those ideas",
            "the ideas",
            "your recommendation",
            "your recommendations",
            "our recommendation",
            "our recommendations",
            "the recommendation",
            "the recommendations",
            "the plank",
            "the planks",
            "the policy plank",
            "the policy planks",
            "the best plank",
            "best plank",
            "the best planks",
            "best planks",
            "the best policy plank",
            "best policy plank",
            "the best policy planks",
            "best policy planks",
            "the best concrete plank",
            "best concrete plank",
            "the best concrete planks",
            "best concrete planks",
            "the best concrete policy plank",
            "best concrete policy plank",
            "the best concrete policy planks",
            "best concrete policy planks",
            "your proposal",
            "your proposals",
            "the proposal",
            "the proposals",
            "your thoughts",
            "your plan",
            "our plan",
        }
        if normalized in placeholders:
            return True
        if self._policy_note_is_board_instruction(normalized):
            return True
        if re.search(r"\b(?:give|suggest|recommend|tell|show|write|put)\s+me\b", normalized) and re.search(
            r"\b(?:ideas?|planks?|polic(?:y|ies)|recommendations?|proposals?)\b",
            normalized,
        ):
            return True
        if re.search(r"\b(?:what|which|where|how)\b.{0,40}\b(?:board|whiteboard|policy ideas|talking points)\b", normalized):
            return True
        words = normalized.split()
        return len(words) <= 7 and (
            ("idea" in words or "ideas" in words or "plank" in words or "planks" in words)
            or (
                any(word in words for word in ("idea", "ideas", "plank", "planks", "proposal", "proposals", "recommendation", "recommendations"))
                and any(word in words for word in ("best", "concrete", "policy", "your", "our", "these", "those"))
            )
        )

    def _turn_requests_council_fight(self, text: str) -> bool:
        normalized = " ".join(text.lower().split())
        if not normalized:
            return False
        cues = (
            "argue it out",
            "fight it out",
            "disagree",
            "debate each other",
            "debate among yourselves",
            "have the room debate",
            "let the room debate",
            "let them argue",
            "full council",
            "where do you disagree",
            "i want the room to really disagree",
            "argue about whether",
        )
        return any(cue in normalized for cue in cues)

    def _turn_identity(self, turn: ConversationTurn) -> tuple[str, str, str, str, str]:
        return (
            turn.speaker,
            " ".join((turn.speaker_name or "").split()).strip(),
            " ".join((turn.speaker_voice or "").split()).strip(),
            " ".join((turn.text or "").split()).strip(),
            turn.mode,
        )

    def _merge_provisional_turns(
        self,
        persisted_turns: list[ConversationTurn],
        provisional_turns: list[ConversationTurn],
    ) -> list[ConversationTurn]:
        if not provisional_turns:
            return list(persisted_turns)
        max_overlap = min(len(persisted_turns), len(provisional_turns))
        overlap = 0
        for size in range(max_overlap, 0, -1):
            persisted_suffix = [self._turn_identity(turn) for turn in persisted_turns[-size:]]
            provisional_prefix = [self._turn_identity(turn) for turn in provisional_turns[:size]]
            if persisted_suffix == provisional_prefix:
                overlap = size
                break
        return [*persisted_turns, *provisional_turns[overlap:]]

    def _truncate_council_turns(self, turns: list[ConversationTurn]) -> list[ConversationTurn]:
        if len(turns) <= self._COUNCIL_CONTEXT_TURN_LIMIT:
            return list(turns)
        return list(turns[-self._COUNCIL_CONTEXT_TURN_LIMIT:])

    def _trailing_council_advisor_beats(
        self,
        turns: list[ConversationTurn],
        state: SimulationState | None = None,
    ) -> int:
        advisor_names = (
            {advisor.name for advisor in self._council_roster_for(state)}
            if state is not None
            else {advisor.name for advisor in COUNCIL_ADVISORS}
        )
        count = 0
        for turn in reversed(turns):
            if turn.speaker == "user":
                break
            if turn.speaker == "assistant" and turn.speaker_name in advisor_names:
                count += 1
        return count

    def _policy_clauses(self, text: str) -> list[str]:
        cleaned = " ".join(text.replace("\n", " ").split())
        if not cleaned:
            return []
        policy_heads = (
            "pay|finance|fund|speed|open|keep|offer|require|share|build|create|dedicate|cut|ban|restrict|"
            "expand|protect|guarantee|license|tax|subsidize|accelerate|fast-track|charge|deploy|make|skim|"
            "rebate|auction|establish|raise|audit|index|tie|target"
        )
        cleaned = re.sub(
            rf"(?:^|(?<=[.;:!?])\s+|\s+)(?:first|second|third|fourth|fifth|one|two|three|four|five|[1-5][.)])"
            rf"\s*,?\s*(?=(?:{policy_heads})\b)",
            "; ",
            cleaned,
            flags=re.IGNORECASE,
        )
        chunks = re.split(
            rf";|(?<=[.!?])\s+(?=(?:{policy_heads})\b)|(?:,\s+(?:but\s+)?(?=(?:{policy_heads})\b))|(?:\b(?:and|but)\b\s+(?=(?:{policy_heads})\b))",
            cleaned,
            flags=re.IGNORECASE,
        )
        return [
            chunk.strip(" -*.")
            for chunk in chunks
            if chunk.strip(" -*.")
            and not re.match(
                r"^(?:i|we)\s+would\s+do\s+(?:one|two|three|four|five|\d+)\s+things?$|^here\s+are\s+(?:one|two|three|four|five|\d+)\s+(?:ideas?|planks?|polic(?:y|ies))$",
                chunk.strip(" -*."),
                flags=re.IGNORECASE,
            )
        ]

    def _extract_policy_notes(self, turns: list[ConversationTurn]) -> list[str]:
        notes: list[str] = []
        seen: set[str] = set()
        for turn in reversed(turns):
            if turn.speaker != "user":
                continue
            for clause in self._policy_clauses(turn.text):
                note = self._normalize_policy_note(clause)
                normalized = " ".join(note.lower().split())
                if len(normalized) < 10:
                    continue
                if normalized in seen:
                    continue
                seen.add(normalized)
                notes.append(note)
                if len(notes) >= 6:
                    return notes
        return notes

    def _extract_policy_points_from_text(self, text: str) -> list[str]:
        candidate = " ".join(str(text or "").split())
        board_body_matches = [
            *re.finditer(
            r"\b(?:my\s+)?(?:two|three|four|five)?\s*(?:fundable\s+|specific\s+)?planks?\s+(?:are|would be|should be)\s*:\s*(?P<body>.+)$",
            candidate,
            flags=re.IGNORECASE,
            ),
            *re.finditer(
            r"\b(?:put|write|add).{0,80}\b(?:two\s+)?(?:up|on\s+the\s+board)\s*:\s*(?P<body>.+)$",
            candidate,
            flags=re.IGNORECASE,
            ),
        ]
        if board_body_matches:
            candidate = board_body_matches[-1].group("body")
        candidate = re.sub(
            r"\.\s+(?:And\s+)?(?=(?:audit|charge|fund|guarantee|keep|require|protect|build|create|dedicate|tax|offer|fast-track|index|auction|tie|make|target)\b)",
            "; ",
            candidate,
            flags=re.IGNORECASE,
        )
        pseudo_turn = ConversationTurn(speaker="user", text=candidate, mode="text")
        notes = self._extract_policy_notes([pseudo_turn])
        if notes:
            return notes
        raw_lines = [
            segment.strip().strip("-* ")
            for line in candidate.splitlines()
            for segment in line.split(";")
            if segment.strip()
        ]
        normalized = [self._normalize_policy_note(line) for line in raw_lines if line]
        return self._dedupe_policy_notes([note for note in normalized if note])

    def _draft_policy_notes(self, stage: StagePackage) -> list[str]:
        if stage.policy_notes:
            return stage.policy_notes[:5]
        return []

    def _resolve_agenda_points(
        self,
        stage: StagePackage,
        submitted_platform: str,
        player_rebuttal: str = "",
    ) -> list[str]:
        player_text = "\n".join(
            part.strip()
            for part in (submitted_platform, player_rebuttal)
            if str(part or "").strip()
        )
        extracted = self._extract_policy_points_from_text(player_text)
        if extracted:
            return extracted[:6]
        fallback = self._normalize_policy_note(player_text)
        if fallback and len(fallback.split()) >= 4:
            return [fallback]
        if stage.policy_notes:
            return stage.policy_notes[:6]
        return []

    def _axis_to_policy_note(self, axis: str) -> str:
        lower = axis.lower()
        if "public-facing administration" in lower or ("public" in lower and "administration" in lower):
            return "Open public services to AI."
        if "human-review" in lower or "human review" in lower or ("liability" in lower and "high-risk" in lower):
            return "Require review for high-risk AI."
        if "small firms" in lower or "local providers" in lower or "dominant vendors" in lower:
            return "Share AI tools with small firms."
        if "entry-level" in lower or "office slots" in lower or "step-up roles" in lower:
            return "Fund junior hiring credits."
        if "public ai infrastructure" in lower or ("smaller" in lower and "competitive" in lower):
            return "Share compute with small firms."
        if "tax" in lower and "training" in lower:
            return "Fund junior hiring credits."
        if "robotics" in lower or "industrial capacity" in lower:
            return "Fast-track power and chip build."
        return self._normalize_policy_note(axis)

    def _normalize_policy_note(self, value: object) -> str:
        text = " ".join(str(value or "").split()).strip()
        if not text:
            return ""
        filler_phrases = {
            "ok",
            "okay",
            "sure",
            "yes",
            "yeah",
            "yep",
            "mhm",
            "mmh",
            "mm",
            "uh huh",
            "uh-huh",
            "got it",
            "sounds good",
            "that sounds good",
            "fine",
            "好的",
            "嗯",
            "好",
        }
        lowered_original = text.lower().strip(" .!?")
        if lowered_original in filler_phrases:
            return ""
        if self._policy_note_is_board_instruction(text):
            return ""
        text = re.sub(
            r"^(whether to|how to|how aggressively to|how hard to|whether|should we|should the government|let'?s|we should|our plan is to|the plan is to)\s+",
            "",
            text,
            flags=re.IGNORECASE,
        )
        if ":" in text:
            left, right = text.split(":", 1)
            text = right if len(right.split()) >= 3 else left
        text = re.split(r"\b(?:because|so that|despite)\b", text, maxsplit=1, flags=re.IGNORECASE)[0].strip()
        if self._policy_note_has_dangling_ending(text):
            return ""
        lowered = text.lower().strip(" .!?")
        if lowered in filler_phrases:
            return ""
        if self._policy_note_is_board_instruction(text):
            return ""
        if 2 <= len(text.split()) <= 8 and len(text) <= 56:
            text = text.strip(" ,;:-")
            text = re.sub(r"\s+\b(?:and|or|with|for|to|of|the|a|an)\b$", "", text, flags=re.IGNORECASE).strip(" ,;:-")
            if self._policy_note_has_dangling_ending(text):
                return ""
            if len(text.split()) < 2:
                return ""
            if text and text[-1] not in ".!?":
                text += "."
            return text[0].upper() + text[1:]
        lower = text.lower()
        tokens = re.findall(r"[A-Za-z0-9'-]+", text)
        if not tokens:
            return ""
        directive_verbs = {
            "accelerate",
            "speed",
            "fast-track",
            "streamline",
            "pay",
            "fund",
            "finance",
            "dedicate",
            "subsidize",
            "give",
            "offer",
            "expand",
            "open",
            "keep",
            "protect",
            "require",
            "guarantee",
            "share",
            "build",
            "create",
            "cut",
            "cap",
            "tax",
            "charge",
            "license",
            "ban",
            "restrict",
            "deploy",
            "make",
            "skim",
            "rebate",
            "auction",
            "establish",
            "raise",
            "audit",
            "index",
            "tie",
            "target",
        }
        if (
            tokens[0].lower() in directive_verbs
            and len(tokens) <= 28
            and len(text) <= 220
            and not (tokens[0].lower() in {"accelerate", "speed", "fast-track", "streamline"} and " while " in f" {lower} ")
            and not (tokens[0].lower() == "require" and "appeal" in lower and re.search(r"\b(?:denial|denials|benefit|claim)\b", lower))
            and not (tokens[0].lower() == "keep" and re.search(r"\b(?:tools?|access|frontier|ai)\b", lower) and re.search(r"\b(?:schools?|small firms?|small businesses?|public agencies|agencies)\b", lower))
        ):
            sentence = text.strip(" ,;:-")
            if self._policy_note_has_dangling_ending(sentence):
                return ""
            if self._policy_note_is_board_instruction(sentence):
                return ""
            if sentence and sentence[-1] not in ".!?":
                sentence += "."
            return sentence[0].upper() + sentence[1:]
        if re.search(r"compute[- ]lease revenue.*household|household.*compute[- ]lease revenue", lower):
            return "Dedicate certified compute-lease revenue to monthly household dividends."
        if re.search(r"paid human advocate.*(?:benefits?|health|legal|compute)", lower):
            return "Fund paid human advocates for benefits, health, and legal compute disputes."
        if re.search(r"public warrants?.*(?:power|compute)|(?:power|compute).*public warrants?", lower):
            return "Take public warrants in fast-tracked power and compute campuses."
        pattern_labels = [
            (r"(public|national|federal).*(compute bank|compute reserve|compute utility|ai utility)", "Create a public compute bank."),
            (r"(premium|private).*(compute|frontier).*(market|tier|auction)|(?:market|auction).*(premium|private).*(compute|frontier)", "Auction premium compute above a public floor."),
            (r"(grid|power|interconnection|transmission|permitting|permit)", "Speed grid interconnection."),
            (r"(give|offer).*(every|each)\s+household.*(basic|public).*(ai|assistant|account)", "Give every household basic AI."),
            (r"(wage insurance|income insurance)", "Offer wage insurance."),
            (r"(apprentice|apprenticeship|entry[- ]level|first rung|junior hiring)", "Fund junior hiring credits."),
            (r"(appeal|human review).*(denial|claim|benefit|decision)", "Require appeals for AI denials."),
            (r"(consumer ai|everyday ai|household ai).*(open|access|available)|\bkeep\b.*(consumer ai|everyday ai|household ai)", "Keep consumer AI open."),
            (r"\bkeep\b.*(?:tools?|access|frontier|ai).*(?:cheap|open|affordable).*(?:schools?|small firms?|small businesses?|public agencies|agencies)|(?:schools?|small firms?|small businesses?|public agencies|agencies).*(?:tools?|access|frontier|ai).*(?:cheap|open|affordable)", "Keep access cheap for schools and small firms."),
            (r"(public|school|library|tutor|education).*(ai|assistant|copilot)|\bai tutors?\b", "Open public AI tutors."),
            (r"(small firm|small business|local firm).*(compute|gpu|cloud|access)", "Share compute with small firms."),
            (r"(power|chip|fab|data center).*(build|permit|approval|fast)", "Fast-track power and chip build."),
            (r"(benefit|claim|fraud).*(audit|review)", "Audit high-risk AI denials."),
        ]
        for pattern, label in pattern_labels:
            if re.search(pattern, lower):
                return label
        directive_verbs = {
            "accelerate",
            "speed",
            "fast-track",
            "streamline",
            "pay",
            "fund",
            "finance",
            "dedicate",
            "subsidize",
            "give",
            "offer",
            "expand",
            "open",
            "keep",
            "protect",
            "require",
            "guarantee",
            "share",
            "build",
            "create",
            "cut",
            "cap",
            "tax",
            "charge",
            "license",
            "ban",
            "restrict",
            "deploy",
            "make",
            "skim",
            "rebate",
            "auction",
            "establish",
            "raise",
            "audit",
            "index",
            "tie",
            "target",
        }
        if tokens[0].lower() in directive_verbs and len(tokens) <= 28 and len(text) <= 220:
            sentence = text.strip(" ,;:-")
            if self._policy_note_has_dangling_ending(sentence):
                return ""
            if self._policy_note_is_board_instruction(sentence):
                return ""
            if sentence and sentence[-1] not in ".!?":
                sentence += "."
            return sentence[0].upper() + sentence[1:]
        verb_map = {
            "accelerate": "Speed",
            "speed": "Speed",
            "fast-track": "Fast-track",
            "streamline": "Speed",
            "fund": "Fund",
            "finance": "Fund",
            "subsidize": "Fund",
            "offer": "Offer",
            "expand": "Expand",
            "open": "Open",
            "keep": "Keep",
            "protect": "Protect",
            "require": "Require",
            "guarantee": "Guarantee",
            "share": "Share",
            "build": "Build",
            "cut": "Cut",
            "cap": "Cap",
            "tax": "Tax",
            "charge": "Charge",
            "license": "License",
            "ban": "Ban",
            "restrict": "Restrict",
            "deploy": "Deploy",
            "make": "Make",
            "skim": "Skim",
            "rebate": "Rebate",
            "auction": "Auction",
            "establish": "Establish",
            "raise": "Raise",
            "audit": "Audit",
            "index": "Index",
            "tie": "Tie",
            "target": "Target",
        }
        stopwords = {
            "the",
            "a",
            "an",
            "to",
            "of",
            "with",
            "through",
            "while",
            "without",
            "broadly",
            "practical",
            "visible",
            "transition",
            "gains",
            "workers",
            "people",
        }
        start_index = 0
        verb = None
        for index, token in enumerate(tokens):
            mapped = verb_map.get(token.lower())
            if mapped:
                verb = mapped
                start_index = index + 1
                break
        if verb is None:
            verb = tokens[0].capitalize()
            start_index = 1
        remaining: list[str] = []
        for token in tokens[start_index:]:
            if token.lower() in stopwords:
                continue
            remaining.append(token)
            if len(remaining) >= 6:
                break
        phrase = " ".join([verb, *remaining]).strip()
        phrase = re.sub(r"\s+", " ", phrase).strip(" ,;:-")
        phrase = re.sub(r"\s+\b(?:and|or|with|for|to|of|the|a|an)\b$", "", phrase, flags=re.IGNORECASE).strip(" ,;:-")
        if len(phrase) > 72:
            words = phrase.split()
            phrase = " ".join(words[: min(len(words), 7)])
        if len(phrase.split()) < 2 and len(phrase.strip(" .!?")) < 8:
            return ""
        if self._policy_note_is_board_instruction(phrase):
            return ""
        if phrase and phrase[-1] not in ".!?":
            phrase += "."
        return phrase

    def _policy_note_is_board_instruction(self, value: object) -> bool:
        normalized = " ".join(str(value or "").lower().strip(" .!?;:-").split())
        if not normalized:
            return False
        if not re.search(r"\b(?:board|whiteboard|talking\s+points?|policy\s+ideas?|policy\s+board)\b", normalized):
            return False
        return bool(
            re.search(
                r"\b(?:please\s+)?(?:can|could|would|will)?\s*(?:you\s+)?(?:add|put|write|pin|place|keep|save|capture|note|update|change|rewrite|replace)\b",
                normalized,
            )
            or re.search(
                r"\b(?:this|that|it|one|idea|plank|policy)\b.{0,40}\b(?:on|to|onto|into)\s+(?:the\s+)?(?:policy\s+)?(?:ideas?\s+)?(?:board|whiteboard)\b",
                normalized,
            )
        )

    def _policy_note_has_dangling_ending(self, value: object) -> bool:
        text = " ".join(str(value or "").split()).strip().rstrip(" .!?;:")
        if not text:
            return True
        if re.search(
            r"\b(?:and|or|but|because|if|when|while|unless|where|with|for|to|of|the|a|an)\s*$",
            text,
            flags=re.IGNORECASE,
        ):
            return True
        tail = text.split()[-1].lower().strip("'\"")
        if tail in {
            "access",
            "capacity",
            "governance",
            "resilience",
            "alignment",
            "safeguards",
            "pathways",
            "stakeholders",
            "levers",
            "framework",
            "principles",
            "model-credit",
        } and len(text.split()) <= 7:
            return True
        conditional = re.search(r"\b(?:if|when|unless|where)\s+(?P<tail>[^.;!?]+)$", text, flags=re.IGNORECASE)
        if conditional:
            clause = conditional.group("tail").strip()
            clause_tokens = re.findall(r"[A-Za-z0-9'-]+", clause.lower())
            finite_verbs = {
                "is",
                "are",
                "was",
                "were",
                "gets",
                "get",
                "got",
                "has",
                "have",
                "had",
                "cuts",
                "cut",
                "caps",
                "cap",
                "charges",
                "charge",
                "prices",
                "price",
                "reprices",
                "reprice",
                "fails",
                "fail",
                "falls",
                "fall",
                "rises",
                "rise",
                "shrinks",
                "shrink",
                "expands",
                "expand",
                "denies",
                "deny",
                "delays",
                "delay",
                "serves",
                "serve",
                "works",
                "work",
                "pays",
                "pay",
            }
            if len(clause_tokens) <= 3 or not any(token in finite_verbs for token in clause_tokens):
                return True
        return False

    def _dedupe_policy_notes(self, notes: list[str]) -> list[str]:
        unique: list[str] = []
        seen: set[str] = set()
        for note in notes:
            normalized = " ".join(note.lower().split())
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            unique.append(note)
            if len(unique) >= 6:
                break
        return unique

    def _normalized_text_key(self, value: object) -> str:
        return " ".join(str(value or "").lower().split())

    def _poll_summary_identity(self, summary: PollSummary) -> str:
        return summary.key or self._normalized_text_key(summary.question)

    def _poll_summary_topline(self, summary: PollSummary | None) -> str:
        if summary is None:
            return ""
        top = sorted(summary.shares.items(), key=lambda item: item[1], reverse=True)
        if not top:
            return ""
        return "; ".join(f"{label}: {value * 100:.0f}%" for label, value in top[:2])

    def _coerce_policy_note_index(self, value: object) -> int | None:
        if value is None or value == "":
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    async def _load_personas(self, simulation_id: str) -> pd.DataFrame | None:
        path = self.store.persona_path(simulation_id)
        if not path.exists():
            return None
        return pd.read_csv(path)

    async def _save_personas(self, simulation_id: str, df: pd.DataFrame) -> None:
        df.to_csv(self.store.persona_path(simulation_id), index=False)

    def _decorate_asset_urls(self, stage: StagePackage) -> None:
        for beat in stage.narrative_beats:
            if beat.image_path:
                image_path = self._prefer_finished_image(Path(beat.image_path))
                beat.image_path = str(image_path)
                beat.image_url = self.store.asset_url(image_path)
            if beat.audio_path:
                beat.audio_url = self.store.asset_url(Path(beat.audio_path))
        for featurette in stage.featurettes:
            for beat in featurette.narrative_beats:
                if beat.image_path:
                    image_path = self._prefer_finished_image(Path(beat.image_path))
                    beat.image_path = str(image_path)
                    beat.image_url = self.store.asset_url(image_path)
                if beat.audio_path:
                    beat.audio_url = self.store.asset_url(Path(beat.audio_path))

    def _prefer_finished_image(self, path: Path) -> Path:
        if not path.name.endswith("-fallback.svg"):
            return path
        base_name = path.name.removesuffix("-fallback.svg")
        for suffix in ("jpg", "jpeg", "png", "webp"):
            candidate = path.with_name(f"{base_name}.{suffix}")
            if candidate.exists() and candidate.stat().st_size > 0:
                return candidate
        return path

    def _queue_stage_featurettes(self, simulation_id: str, stage_index: int) -> None:
        key = (simulation_id, stage_index)
        existing = self._featurette_tasks.get(key)
        if existing and not existing.done():
            return
        self._featurette_tasks[key] = asyncio.create_task(self._prepare_stage_featurettes(simulation_id, stage_index))

    def _queue_stage_tracking_poll(self, simulation_id: str, stage_index: int) -> None:
        key = (simulation_id, stage_index)
        existing = self._tracking_tasks.get(key)
        if existing and not existing.done():
            return
        self._tracking_tasks[key] = asyncio.create_task(self._prepare_stage_tracking_poll(simulation_id, stage_index))

    async def _prepare_stage_tracking_poll(self, simulation_id: str, stage_index: int) -> None:
        state = await self.get_simulation(simulation_id)
        if stage_index >= len(state.stages):
            return
        personas = await self._load_personas(simulation_id)
        if personas is None:
            return
        stage = state.stages[stage_index]
        extra_questions = list(state.queued_poll_questions)
        if stage.poll_summaries and not extra_questions:
            if state.active_stage_index == stage_index and state.progress.phase == PreparationPhase.polling:
                state.progress = StageProgress(
                    phase=PreparationPhase.ready,
                    label="Stage ready",
                    detail="Briefing, citizens, polls, and debate materials are now available.",
                    percent=100,
                )
                state.updated_at = utc_now()
                await self.store.save(state)
            return

        if state.active_stage_index == stage_index:
            state.progress = StageProgress(
                phase=PreparationPhase.polling,
                label="Polling the electorate",
                detail="Updating representative citizens and measuring how the public reads the moment.",
                percent=82,
            )
            state.updated_at = utc_now()
            await self.store.save(state)

        poll_task = asyncio.create_task(self.gabriel_service.run_tracking_polls(
            personas=personas,
            stage_index=stage_index,
            stage=stage,
            player_name=state.config.player_name,
            opponent_name=state.config.opponent_name,
            save_dir=self.store.poll_dir(simulation_id, stage_index),
            extra_questions=extra_questions,
        ))
        try:
            try:
                personas, poll_summaries, tracking = await asyncio.wait_for(
                    asyncio.shield(poll_task),
                    timeout=self.settings.tracking_poll_soft_timeout_seconds,
                )
            except TimeoutError:
                latest = await self.get_simulation(simulation_id)
                if latest.active_stage_index == stage_index and latest.progress.phase == PreparationPhase.polling:
                    latest.progress = StageProgress(
                        phase=PreparationPhase.ready,
                        label="Stage ready",
                        detail="Briefing, citizens, and rooms are ready. The electorate read will update the board when it lands.",
                        percent=100,
                    )
                    latest.updated_at = utc_now()
                    await self.store.save(latest)
                personas, poll_summaries, tracking = await poll_task
        except Exception:
            latest = await self.get_simulation(simulation_id)
            if latest.active_stage_index == stage_index and latest.progress.phase == PreparationPhase.polling:
                latest.progress = StageProgress(
                    phase=PreparationPhase.ready,
                    label="Stage ready",
                    detail="The chapter is live, but the fresh electorate read did not finish cleanly.",
                    percent=100,
                )
                latest.updated_at = utc_now()
                await self.store.save(latest)
            return

        latest = await self.get_simulation(simulation_id)
        if stage_index >= len(latest.stages):
            return
        latest_stage = latest.stages[stage_index]
        latest_stage.sample_citizens = self.gabriel_service.pick_sample_citizens(personas, stage=latest_stage)
        self._backfill_sample_citizen_town_hall_questions(latest, latest_stage)
        latest_stage.poll_summaries = poll_summaries
        latest_stage.tracking = tracking
        latest_stage.queued_poll_questions = [item.question for item in latest.queued_poll_questions]
        if len(latest.stages) > stage_index:
            latest.stages[stage_index] = latest_stage
        else:
            latest.stages.append(latest_stage)
        latest.approval_rating = tracking.approval.value
        latest.standard_questions = self.gabriel_service.standard_questions(
            latest.config.player_name,
            latest.config.opponent_name,
            latest_stage,
        )
        if latest.active_stage_index == stage_index:
            latest.current_polls = poll_summaries
            latest.progress = StageProgress(
                phase=PreparationPhase.ready,
                label="Stage ready",
                detail="Briefing, citizens, polls, and debate materials are now available.",
                percent=100,
            )
        latest.updated_at = utc_now()
        await self._save_personas(simulation_id, personas)
        await self.store.save(latest)

    async def _merge_stage_featurettes(
        self,
        simulation_id: str,
        stage_index: int,
        *,
        featurettes,
        featurettes_status: str,
        featurettes_error: str | None = None,
    ) -> SimulationState | None:
        latest = await self.get_simulation(simulation_id)
        if stage_index >= len(latest.stages):
            return None
        stage = latest.stages[stage_index]
        stage.featurettes = featurettes
        stage.featurettes_status = featurettes_status
        stage.featurettes_error = featurettes_error
        self._decorate_asset_urls(stage)
        latest.updated_at = utc_now()
        await self.store.save(latest)
        return latest

    async def _prepare_stage_featurettes(self, simulation_id: str, stage_index: int) -> None:
        state = await self.get_simulation(simulation_id)
        if stage_index >= len(state.stages):
            return
        stage = state.stages[stage_index]
        if stage.featurettes_status == "ready" and stage.featurettes:
            return
        await self._merge_stage_featurettes(
            simulation_id,
            stage_index,
            featurettes=[],
            featurettes_status="generating",
        )
        try:
            featurettes = await self.orchestrator.compose_stage_featurettes(state=state, stage=stage)
            if not featurettes:
                await self._merge_stage_featurettes(
                    simulation_id,
                    stage_index,
                    featurettes=[],
                    featurettes_status="ready",
                )
                return
            for featurette in featurettes:
                featurette.status = "generating"
                featurette.error = None
            await self._merge_stage_featurettes(
                simulation_id,
                stage_index,
                featurettes=featurettes,
                featurettes_status="generating",
            )
            async def render_featurette(slot: int, featurette):
                featurette_dir = self.store.asset_dir(simulation_id, stage_index) / "featurettes" / f"{slot + 1:02d}-{featurette.id}"
                await self.orchestrator.materialize_featurette_media(featurette=featurette, asset_dir=featurette_dir)
                featurette.status = "ready"
                await self._merge_stage_featurettes(
                    simulation_id,
                    stage_index,
                    featurettes=featurettes,
                    featurettes_status="generating",
                )

            await asyncio.gather(
                *(render_featurette(slot, featurette) for slot, featurette in enumerate(featurettes))
            )
            await self._merge_stage_featurettes(
                simulation_id,
                stage_index,
                featurettes=featurettes,
                featurettes_status="ready",
            )
        except Exception as exc:  # pragma: no cover - best effort background media
            latest = await self.get_simulation(simulation_id)
            if stage_index >= len(latest.stages):
                return
            stage = latest.stages[stage_index]
            featurettes = stage.featurettes
            for featurette in featurettes:
                if featurette.status != "ready":
                    featurette.status = "error"
                    featurette.error = str(exc)
            stage.featurettes_status = "error"
            stage.featurettes_error = str(exc)
            latest.updated_at = utc_now()
            await self.store.save(latest)

    def _queue_stage_town_hall_questions(self, simulation_id: str, stage_index: int) -> None:
        key = (simulation_id, stage_index)
        existing = self._town_hall_tasks.get(key)
        if existing and not existing.done():
            return
        self._town_hall_tasks[key] = asyncio.create_task(self._prepare_stage_town_hall_questions(simulation_id, stage_index))

    async def _prepare_stage_town_hall_questions(self, simulation_id: str, stage_index: int) -> None:
        state = await self.get_simulation(simulation_id)
        if stage_index >= len(state.stages):
            return
        stage = state.stages[stage_index]
        if not stage.sample_citizens:
            return

        semaphore = asyncio.Semaphore(2)

        async def enrich(citizen: CitizenSnapshot) -> None:
            async with semaphore:
                fallback_question = self._fallback_town_hall_question(state, citizen)
                stored_question = " ".join(citizen.town_hall_question.split()).strip()
                stored_key = self._normalized_text_key(stored_question)
                fallback_key = self._normalized_text_key(fallback_question)
                if stored_question and stored_key != fallback_key and citizen.town_hall_cue.strip():
                    return
                question, cue = await self._draft_town_hall_question(
                    state=state,
                    citizen=citizen,
                    thread_turns=[],
                    live_refresh=False,
                )
                citizen.town_hall_question = question
                citizen.town_hall_cue = cue

        try:
            await asyncio.gather(*(enrich(citizen) for citizen in stage.sample_citizens[:6]))
        except Exception:
            return

        latest = await self.get_simulation(simulation_id)
        if stage_index >= len(latest.stages):
            return
        latest_stage = latest.stages[stage_index]
        for source_citizen, target_citizen in zip(stage.sample_citizens[:6], latest_stage.sample_citizens[:6]):
            target_citizen.town_hall_question = source_citizen.town_hall_question
            target_citizen.town_hall_cue = source_citizen.town_hall_cue
        latest.updated_at = utc_now()
        await self.store.save(latest)

    def _recommend_citizens(self, stage: StagePackage, topic: str) -> list[dict]:
        topic_tokens = {token for token in re.findall(r"[a-z0-9']+", topic.lower()) if len(token) > 2}
        scored: list[tuple[int, int, dict]] = []
        for citizen in stage.sample_citizens:
            haystack = " ".join(
                [
                    citizen.role,
                    citizen.region,
                    citizen.mood,
                    citizen.ai_exposure,
                    citizen.household,
                    citizen.daily_routine,
                    citizen.current_worries,
                    citizen.current_hopes,
                    citizen.summary,
                    citizen.current_update,
                ]
            ).lower()
            overlap = sum(1 for token in topic_tokens if token in haystack)
            proximity = 100 - abs(citizen.support_score - 50)
            scored.append(
                (
                    overlap,
                    proximity,
                    {
                        "citizen_id": citizen.citizen_id,
                        "display_name": citizen.display_name,
                        "role": citizen.role,
                        "region": citizen.region,
                        "support_label": citizen.support_label,
                        "why_relevant": (citizen.current_update or citizen.summary)[:180],
                    },
                )
            )
        scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return [payload for _, _, payload in scored[:3]]

    def _match_citizen_by_name(self, stage: StagePackage, requested_name: str) -> CitizenSnapshot | None:
        normalized = " ".join(requested_name.lower().split())
        if not normalized:
            return None
        requested_tokens = {token for token in normalized.split() if token}
        best: tuple[int, CitizenSnapshot] | None = None
        for citizen in stage.sample_citizens:
            display = " ".join(citizen.display_name.lower().split())
            display_tokens = set(display.split())
            score = 0
            if normalized == display:
                score += 100
            if normalized in display:
                score += 60
            score += len(requested_tokens & display_tokens) * 20
            if score == 0:
                continue
            if best is None or score > best[0]:
                best = (score, citizen)
        if best:
            return best[1]
        if normalized in {
            "a person",
            "person",
            "someone",
            "someone nearby",
            "somebody",
            "anyone",
            "anybody",
            "nearest person",
            "closest person",
            "nearest citizen",
            "closest citizen",
        }:
            return stage.sample_citizens[0] if stage.sample_citizens else None
        descriptor_aliases = {
            "kid": {"kid", "child", "children", "student", "pupil", "school", "teen"},
            "child": {"kid", "child", "children", "student", "pupil", "school", "teen"},
            "student": {"student", "pupil", "school", "college", "university", "classroom"},
            "college": {"college", "university", "campus", "student", "graduate"},
            "worker": {"worker", "employee", "job", "shift", "staff", "wage"},
            "small business": {"small", "business", "store", "shop", "restaurant", "firm", "owner"},
            "owner": {"owner", "business", "store", "shop", "firm"},
            "teacher": {"teacher", "school", "classroom", "student"},
            "parent": {"parent", "child", "children", "family", "school", "care"},
            "retiree": {"retiree", "retired", "senior", "pension"},
            "doctor": {"doctor", "nurse", "clinic", "hospital", "patient", "health"},
            "farmer": {"farmer", "farm", "crop", "ranch"},
        }
        descriptor_tokens = {
            token
            for token in re.findall(r"[a-z0-9']+", normalized)
            if token not in {"a", "an", "the", "to", "with", "talk", "speak", "nearby", "nearest", "closest", "person", "citizen", "people", "someone", "somebody"}
        }
        expanded_tokens = set(descriptor_tokens)
        for phrase, aliases in descriptor_aliases.items():
            if phrase in normalized or descriptor_tokens.intersection(aliases):
                expanded_tokens.update(aliases)
        if not expanded_tokens:
            return None
        descriptor_best: tuple[int, int, CitizenSnapshot] | None = None
        for citizen in stage.sample_citizens:
            haystack = " ".join(
                [
                    citizen.display_name,
                    citizen.role,
                    citizen.region,
                    citizen.mood,
                    citizen.ai_exposure,
                    citizen.household,
                    citizen.daily_routine,
                    citizen.current_worries,
                    citizen.current_hopes,
                    citizen.recent_ai_moment,
                    citizen.summary,
                    citizen.current_update,
                ]
            ).lower()
            hay_tokens = set(re.findall(r"[a-z0-9']+", haystack))
            overlap = len(expanded_tokens & hay_tokens)
            phrase_bonus = 1 if any(token in haystack for token in descriptor_tokens if len(token) >= 4) else 0
            if overlap + phrase_bonus == 0:
                continue
            proximity = 100 - abs(citizen.support_score - 50)
            score = overlap * 10 + phrase_bonus * 4
            if descriptor_best is None or (score, proximity) > (descriptor_best[0], descriptor_best[1]):
                descriptor_best = (score, proximity, citizen)
        return descriptor_best[2] if descriptor_best else None
