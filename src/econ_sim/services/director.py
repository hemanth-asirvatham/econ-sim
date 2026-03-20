from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pandas as pd

from ..config import AppSettings
from ..models import (
    AuditoriumMode,
    AdvisorMode,
    CitizenSnapshot,
    CouncilTurnPlan,
    CouncilTurnRequest,
    CouncilTurnResponse,
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


class SimulationDirector:
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

    def _build_default_config(self) -> SimulationConfig:
        ticket = self.settings.random_candidate_ticket()
        player_role = "incumbent president"
        opponent_role = "challenger governor"
        return SimulationConfig(
            title="AGI Transition Command",
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
            starting_world_mode="default",
            persona_count=min(self.settings.default_persona_count, 48),
            stage_count=self.settings.max_stage_count,
            visual_style=self.settings.default_visual_style,
            orchestrator_reasoning_effort=self.settings.orchestrator_reasoning_effort,
            realtime_model=self.settings.realtime_model,
        )

    def build_create_defaults(self) -> SimulationCreateRequest:
        return SimulationCreateRequest(**self._build_default_config().model_dump())

    def build_setup_defaults(self) -> SetupSessionDefaults:
        config = self._build_default_config()
        return SetupSessionDefaults(
            config=config,
            chamber_intro=(
                "Give me a country, scale, lens, or art-direction nudge if you want one. "
                "If you want the broad default run, just say go and I will launch it."
            ),
            suggested_prompts=[
                "Use the default broad U.S. run.",
                "Keep it national and representative, but raise the sample to 120 people.",
                "Make this Finland, focus on education policy, and weight the electorate toward students, teachers, parents, and municipal administrators.",
                "Make this the Swiss education system, with students, parents, teachers, and cantonal administrators as the core electorate.",
                "Make this a Texas governor run and focus on grid power, logistics, and local manufacturing.",
                "Keep the national setup, but make the argument more about AI in schools and public services.",
                "visual_style: Naturalistic campaign documentary with warm civic interiors and industrial wide shots.",
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
            starting_world_mode=request.starting_world_mode or defaults.starting_world_mode,
            persona_count=request.persona_count,
            stage_count=request.stage_count,
            visual_style=request.visual_style or defaults.visual_style or self.settings.default_visual_style,
            orchestrator_reasoning_effort=request.orchestrator_reasoning_effort,
            realtime_model=request.realtime_model,
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
        return await self.store.load(simulation_id)

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
                    "The hall is set. Give me a country, scale, lens, or style if you want one, or say start to launch the broad default run."
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
        player_agenda_points = self._resolve_agenda_points(current_stage, request.player_platform)
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
            else:
                instructions = self.realtime_prompts.advisor_instructions(
                    state,
                    current_stage.state_of_world,
                    thread_turns,
                    advisor_mode=request.advisor_mode,
                )
                tools = self.realtime_prompts.tools_for(request.role, request.advisor_mode)
                create_response = True
        elif request.role == RealtimeRole.debate:
            thread_turns = self._thread_turns(
                state,
                request.role,
                None,
                AdvisorMode.solo,
                AuditoriumMode.debate,
            )
            instructions = self.realtime_prompts.debate_instructions(state, thread_turns)
            tools = self.realtime_prompts.tools_for(request.role)
            selected_voice = state.config.opponent_voice
            create_response = True
        else:
            citizens = {citizen.citizen_id: citizen for citizen in current_stage.sample_citizens}
            citizen = citizens.get(request.citizen_id or "")
            if citizen is None:
                raise KeyError(f"citizen '{request.citizen_id}' not found")
            thread_turns = self._thread_turns(state, request.role, citizen.citizen_id)
            instructions = self.realtime_prompts.citizen_instructions(state, citizen, thread_turns)
            tools = self.realtime_prompts.tools_for(request.role)
            selected_voice = citizen.voice
            create_response = True
        client_secret, model = await self.gateway.create_realtime_session(
            instructions=instructions,
            tools=tools,
            model=state.config.realtime_model,
            voice=selected_voice,
            create_response=create_response,
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
        working_turns = prior_turns
        input_text = "Continue the council exchange from the latest spoken line."
        if request.continue_dialogue:
            if not prior_turns:
                raise RuntimeError("council context is required before continuing dialogue")
            input_text = (
                "Council continuation turn. Everyone already heard the last spoken advisor line. "
                "React directly to that last spoken line instead of restarting from the president's original prompt. "
                "Pick the single best next voice unless a second interruption is essential. "
                "If the room should now wait for the president, set yield_after_turn true and leave advisor text empty. "
                "If the room is still productively arguing among itself, keep yield_after_turn false and let the strongest reply take the next beat."
            )
        else:
            user_turn = ConversationTurn(speaker="user", text=text, mode=request.mode)
            working_turns = [*prior_turns, user_turn]
            input_text = (
                f"Latest player turn: {text}\n"
                "Start the council response from that player turn. "
                "If the player asked the room to argue it out, let the first advisor beat land and then rely on continuation beats for the rest of the exchange."
            )
        instructions = self.realtime_prompts.council_turn_generation_instructions(state, working_turns)
        parsed, _ = await self.gateway.parse(
            model=self.settings.debate_model,
            instructions=instructions,
            input_text=input_text,
            text_format=CouncilTurnPlan,
            reasoning_effort="none",
            max_output_tokens=1400,
            verbosity="low",
        )

        assistant_turns = self._assistant_turns_from_council_plan(parsed, request.mode)
        if parsed.yield_after_turn and parsed.player_proxy_urgency < 6:
            parsed.player_proxy_urgency = 6
        board_notes = self._dedupe_policy_notes(
            [self._normalize_policy_note(note) for note in parsed.board_notes if str(note).strip()]
        )[:4]

        urgencies = {
            advisor.name: 0 for advisor in COUNCIL_ADVISORS
        }
        for beat in parsed.advisors:
            urgencies[beat.name] = max(0, min(10, int(beat.urgency)))
        contrast = [
            turn.speaker_name
            for turn in assistant_turns[1:]
            if turn.speaker_name
        ]
        return CouncilTurnResponse(
            simulation=state,
            thread_key=thread_key,
            lead=parsed.lead,
            urgencies=urgencies,
            contrast=contrast,
            reason=parsed.reason,
            yield_after_turn=parsed.yield_after_turn,
            player_proxy_urgency=parsed.player_proxy_urgency,
            board_notes=board_notes,
            turns=assistant_turns,
        )

    async def generate_town_hall_question(
        self,
        simulation_id: str,
        request: TownHallQuestionRequest,
    ) -> TownHallQuestionResponse:
        state = await self.get_simulation(simulation_id)
        self._ensure_stage_ready(state)
        stage = state.stages[state.active_stage_index]
        citizen = self._resolve_town_hall_citizen(stage, request.citizen_id)
        thread_key = self._thread_key(state.active_stage_index, RealtimeRole.debate, None, AdvisorMode.solo, AuditoriumMode.debate)
        thread_turns = list(state.conversation_threads.get(thread_key, []))
        instructions = self.realtime_prompts.town_hall_question_generation_instructions(state, citizen, thread_turns)
        parsed, _ = await self.gateway.parse(
            model=self.settings.debate_model,
            instructions=instructions,
            input_text=(
                f"Generate the next audience question from {citizen.display_name}, "
                f"{citizen.role} in {citizen.region}."
            ),
            text_format=TownHallQuestionDraft,
            reasoning_effort="none",
            max_output_tokens=700,
            verbosity="low",
        )
        question_turn = ConversationTurn(
            speaker="assistant",
            speaker_name=citizen.display_name,
            speaker_voice=citizen.voice,
            text=parsed.question.strip(),
            mode=request.mode,
        )
        self._append_turns(state, thread_key, [question_turn])
        state.updated_at = utc_now()
        await self.store.save(state)
        return TownHallQuestionResponse(
            simulation=state,
            thread_key=thread_key,
            cue=parsed.cue.strip(),
            question_turn=question_turn,
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
                    "summary": stage.detailed_summary,
                    "room_briefing": stage.room_briefing,
                    "metrics": [metric.model_dump(mode="json") for metric in stage.tracking.as_list()],
                    "tension_points": stage.tension_points,
                    "policy_axes": stage.suggested_policy_axes,
                    "policy_notes": stage.policy_notes,
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
            action = str(payload.get("action", "set")).strip().lower()
            index = self._coerce_policy_note_index(payload.get("index"))
            raw_notes = payload.get("notes") or []
            if not isinstance(raw_notes, list):
                return RealtimeToolResult(ok=False, data={"message": "notes must be an array of strings"})
            notes = [self._normalize_policy_note(item) for item in raw_notes]
            notes = [note for note in notes if note]
            if action == "clear":
                stage.policy_notes = []
                stage.policy_board_manual = False
            elif action == "add":
                combined = [*stage.policy_notes, *notes]
                stage.policy_notes = self._dedupe_policy_notes(combined)
                stage.policy_board_manual = True
            elif action == "remove":
                if index is not None and 0 <= index < len(stage.policy_notes):
                    stage.policy_notes = [note for position, note in enumerate(stage.policy_notes) if position != index]
                else:
                    removal_tokens = {" ".join(note.lower().split()) for note in notes}
                    stage.policy_notes = [
                        note for note in stage.policy_notes if " ".join(note.lower().split()) not in removal_tokens
                    ]
                stage.policy_board_manual = True
            elif action == "replace":
                if index is None or not (0 <= index < len(stage.policy_notes)):
                    return RealtimeToolResult(ok=False, data={"message": "replace requires a valid index"})
                if not notes:
                    return RealtimeToolResult(ok=False, data={"message": "replace requires at least one note"})
                stage.policy_notes = list(stage.policy_notes)
                stage.policy_notes[index] = notes[0]
                stage.policy_notes = self._dedupe_policy_notes(stage.policy_notes)
                stage.policy_board_manual = True
            elif action == "set":
                stage.policy_notes = self._dedupe_policy_notes(notes)
                stage.policy_board_manual = True
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
            citizens = [citizen.model_dump(mode="json") for citizen in state.stages[state.active_stage_index].sample_citizens]
            return RealtimeToolResult(data={"citizens": citizens})
        if tool_name == "recommend_citizens_for_topic":
            state = await self.get_simulation(simulation_id)
            self._ensure_stage_ready(state)
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
        focus_fields = {"country", "region_focus", "topic_lens", "premise"}
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
            "title",
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
                    "Or give me a country, scale, lens, or style nudge first.",
                ]
            else:
                merged_actions = ["Fill the missing setup fields before launch."]
        return SetupChamberGuidance(
            chamber_reply=chamber_reply,
            readiness=readiness,
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
            "starting_world_mode": "starting_world_mode",
            "persona_count": "persona_count",
            "stage_count": "stage_count",
            "visual_style": "visual_style",
            "orchestrator_reasoning_effort": "orchestrator_reasoning_effort",
            "realtime_model": "realtime_model",
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
            lead = "Applied " + "; ".join(applied_updates[:4]) + "."
        else:
            lead = "The broad default setup still holds."
        if self._setup_missing_fields(config):
            return lead + " Give me the next concrete nudge before launch."
        return lead + " Give me another nudge, or say start when you want to begin."

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
        focus = " ".join(part for part in [region_focus or "", topic_lens or "", premise or ""] if part).lower()
        if existing_text and not self._looks_generated_population_frame(existing_text):
            return existing_text
        if any(keyword in focus for keyword in ("education", "school", "student", "teacher", "municipal", "classroom", "tutoring", "grading", "pupil")):
            return (
                f"A representative sample of people in {country} whose lives are shaped by schools, local public administration, and unequal access to AI-enabled learning, "
                "with pupils, students, parents, teachers, principals, municipal officials, tutors, and education employers represented across region, class, age, ideology, and AI exposure."
            )
        if any(keyword in focus for keyword in ("health", "care", "hospital", "clinic", "patient")):
            return (
                f"A representative sample of people in {country} whose lives are shaped by health and care systems, "
                "with patients, nurses, physicians, aides, administrators, insurers, and family caregivers represented across region, class, age, ideology, and AI exposure."
            )
        if any(keyword in focus for keyword in ("factory", "manufactur", "industrial", "logistics", "port", "freight", "warehouse")):
            return (
                f"A representative sample of people in {country} whose livelihoods touch factories, logistics, and nearby service economies, "
                "with line workers, technicians, dispatchers, managers, suppliers, and surrounding households represented across region, class, age, ideology, and AI exposure."
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

    async def _prepare_stage(self, simulation_id: str) -> None:
        state = await self.get_simulation(simulation_id)
        try:
            await self._set_progress(
                state,
                phase=PreparationPhase.seeding,
                label="Seeding representative citizens",
                detail="Generating a compact but realistic sample of citizens who will experience this stage.",
                percent=12,
            )
            personas = await self._load_personas(simulation_id)
            if personas is None:
                personas = await self.gabriel_service.ensure_personas(
                    simulation_id=simulation_id,
                    population_description=state.config.population_description,
                    persona_count=state.config.persona_count,
                    save_dir=self.store.simulation_dir(simulation_id),
                )
                await self._save_personas(simulation_id, personas)
                state.persona_count_ready = len(personas)
                await self.store.save(state)

            previous_stage = state.stages[-1] if state.stages else None
            prior_tracking = previous_stage.tracking if previous_stage else None
            prior_polls = previous_stage.poll_summaries if previous_stage else []
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
            )
            await self._set_progress(
                state,
                phase=PreparationPhase.media,
                label="Rendering briefing media",
                detail="Generating impressionist images and narration for the opening stage briefing.",
                percent=56,
            )
            await self.orchestrator.materialize_stage_media(
                stage=stage,
                asset_dir=self.store.asset_dir(simulation_id, state.active_stage_index),
            )
            await self._set_progress(
                state,
                phase=PreparationPhase.citizen_updates,
                label="Refreshing citizen lives",
                detail="Updating how different households and workers experience the new stage before measuring sentiment.",
                percent=70,
            )
            personas = await self.gabriel_service.update_personas_for_stage(
                personas=personas,
                stage=stage,
                incumbent_name=state.incumbent_name,
                player_name=state.config.player_name,
                opponent_name=state.config.opponent_name,
                save_dir=self.store.persona_update_dir(simulation_id, stage.index),
            )
            await self._set_progress(
                state,
                phase=PreparationPhase.polling,
                label="Polling the electorate",
                detail="Updating representative citizens and measuring how the public reads the moment.",
                percent=82,
            )
            _, poll_summaries, tracking = await self.gabriel_service.run_tracking_polls(
                personas=personas,
                stage_index=stage.index,
                stage=stage,
                player_name=state.config.player_name,
                opponent_name=state.config.opponent_name,
                save_dir=self.store.poll_dir(simulation_id, stage.index),
                extra_questions=list(state.queued_poll_questions),
            )
            stage.sample_citizens = self.gabriel_service.pick_sample_citizens(personas, stage=stage)
            stage.poll_summaries = poll_summaries
            stage.tracking = tracking
            stage.queued_poll_questions = [item.question for item in state.queued_poll_questions]
            stage = await self.orchestrator.polish_stage_after_poll(
                stage=stage,
                tracking=tracking,
                poll_summaries=poll_summaries,
                sample_citizens=stage.sample_citizens,
            )
            self._decorate_asset_urls(stage)
            state.approval_rating = tracking.approval.value
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
            state.focused_citizen_id = stage.sample_citizens[0].citizen_id if stage.sample_citizens else None
            state.current_polls = poll_summaries
            state.progress = StageProgress(
                phase=PreparationPhase.ready,
                label="Stage ready",
                detail="Briefing, citizens, polls, and debate materials are now available.",
                percent=100,
            )
            state.updated_at = utc_now()
            await self._save_personas(simulation_id, personas)
            await self.store.save(state)
        except Exception as exc:  # pragma: no cover - surfaced through API
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

    def _ensure_citizen_exists(self, state: SimulationState, citizen_id: str | None) -> None:
        self._ensure_stage_ready(state)
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

    def _append_turns(self, state: SimulationState, thread_key: str, turns: list[ConversationTurn]) -> None:
        existing = list(state.conversation_threads.get(thread_key, []))
        existing.extend(turns)
        state.conversation_threads[thread_key] = existing[-48:]

    def _assistant_turns_from_council_plan(self, plan: CouncilTurnPlan, mode: str) -> list[ConversationTurn]:
        selected = [beat for beat in plan.advisors if beat.speak and " ".join(beat.text.split()).strip()]
        if not selected:
            lead_entry = next((beat for beat in plan.advisors if beat.name == plan.lead and beat.text.strip()), None)
            if lead_entry is not None:
                selected = [lead_entry]
        turns: list[ConversationTurn] = []
        for beat in selected[:4]:
            cleaned_text = " ".join(beat.text.split()).strip().strip("\"'“”‘’").strip()
            if not cleaned_text:
                continue
            turns.append(
                ConversationTurn(
                    speaker="assistant",
                    speaker_name=beat.name,
                    speaker_voice=COUNCIL_VOICES.get(beat.name),
                    text=cleaned_text,
                    mode="voice" if mode == "voice" else "text",
                )
            )
        return turns

    def _maybe_apply_council_board_notes(self, stage: StagePackage, board_notes: list[str]) -> list[str]:
        normalized = self._dedupe_policy_notes(
            [self._normalize_policy_note(note) for note in board_notes if str(note).strip()]
        )
        if not normalized:
            return stage.policy_notes
        stage.policy_board_manual = True
        return normalized[:4]

    def _resolve_town_hall_citizen(self, stage: StagePackage, citizen_id: str | None) -> CitizenSnapshot:
        if citizen_id:
            for citizen in stage.sample_citizens:
                if citizen.citizen_id == citizen_id:
                    return citizen
        return stage.sample_citizens[0]

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

    def _policy_clauses(self, text: str) -> list[str]:
        cleaned = " ".join(text.replace("\n", " ").split())
        if not cleaned:
            return []
        chunks = re.split(
            r";|(?:,\s+(?=(?:fund|speed|open|keep|offer|require|share|build|cut|ban|restrict|expand|protect|guarantee|license|tax|subsidize|accelerate)\b))|(?:\band\b\s+(?=(?:fund|speed|open|keep|offer|require|share|build|cut|ban|restrict|expand|protect|guarantee|license|tax|subsidize|accelerate)\b))",
            cleaned,
            flags=re.IGNORECASE,
        )
        return [chunk.strip(" -*.") for chunk in chunks if chunk.strip(" -*.")]

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
        pseudo_turn = ConversationTurn(speaker="user", text=text, mode="text")
        notes = self._extract_policy_notes([pseudo_turn])
        if notes:
            return notes
        raw_lines = [
            segment.strip().strip("-* ")
            for line in text.splitlines()
            for segment in line.split(";")
            if segment.strip()
        ]
        normalized = [self._normalize_policy_note(line) for line in raw_lines if line]
        return self._dedupe_policy_notes([note for note in normalized if note])

    def _draft_policy_notes(self, stage: StagePackage) -> list[str]:
        seed_notes = [self._axis_to_policy_note(axis) for axis in stage.suggested_policy_axes[:4]]
        return self._dedupe_policy_notes([note for note in seed_notes if note])[:4]

    def _resolve_agenda_points(self, stage: StagePackage, submitted_platform: str) -> list[str]:
        extracted = self._extract_policy_points_from_text(submitted_platform)
        if extracted:
            return extracted[:6]
        if stage.policy_notes:
            return stage.policy_notes[:6]
        drafted = self._draft_policy_notes(stage)
        if drafted:
            return drafted[:6]
        return ["Keep consumer AI open.", "Speed grid hookups.", "Cushion visible job loss."]

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
        text = re.sub(
            r"^(whether to|how to|how aggressively to|how hard to|whether|should we|should the government|let'?s|we should|our plan is to|the plan is to)\s+",
            "",
            text,
            flags=re.IGNORECASE,
        )
        if ":" in text:
            left, right = text.split(":", 1)
            text = right if len(right.split()) >= 3 else left
        text = re.split(r"\b(?:while|without|because|so that|despite|if)\b", text, maxsplit=1, flags=re.IGNORECASE)[0].strip()
        lowered = text.lower().strip(" .!?")
        if lowered in filler_phrases:
            return ""
        if 2 <= len(text.split()) <= 8 and len(text) <= 56:
            text = text.strip(" ,;:-")
            if text and text[-1] not in ".!?":
                text += "."
            return text[0].upper() + text[1:]
        lower = text.lower()
        pattern_labels = [
            (r"(grid|power|interconnection|transmission|permitting|permit)", "Speed grid hookups."),
            (r"(wage insurance|income insurance)", "Offer wage insurance."),
            (r"(apprentice|apprenticeship|entry[- ]level|first rung|junior hiring)", "Fund junior hiring credits."),
            (r"(appeal|human review).*(denial|claim|benefit|decision)", "Require appeals for AI denials."),
            (r"(consumer ai|everyday ai|household ai).*(open|access)|\bkeep\b.*(consumer ai|everyday ai|household ai)", "Keep consumer AI open."),
            (r"(public|school|library|tutor|education).*(ai|assistant|copilot)|\bai tutors?\b", "Open public AI tutors."),
            (r"(small firm|small business|local firm).*(compute|gpu|cloud|access)", "Share compute with small firms."),
            (r"(power|chip|fab|data center).*(build|permit|approval|fast)", "Fast-track power and chip build."),
            (r"(benefit|claim|fraud).*(audit|review)", "Audit high-risk AI denials."),
        ]
        for pattern, label in pattern_labels:
            if re.search(pattern, lower):
                return label
        tokens = re.findall(r"[A-Za-z0-9'-]+", text)
        if not tokens:
            return ""
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
            "license": "License",
            "ban": "Ban",
            "restrict": "Restrict",
            "deploy": "Deploy",
        }
        stopwords = {
            "the",
            "a",
            "an",
            "and",
            "for",
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
            if len(remaining) >= 4:
                break
        phrase = " ".join([verb, *remaining]).strip()
        phrase = re.sub(r"\s+", " ", phrase).strip(" ,;:-")
        if len(phrase) > 40:
            words = phrase.split()
            phrase = " ".join(words[: min(len(words), 5)])
        if len(phrase.split()) < 2 and len(phrase.strip(" .!?")) < 8:
            return ""
        if phrase and phrase[-1] not in ".!?":
            phrase += "."
        return phrase

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
                beat.image_url = self.store.asset_url(Path(beat.image_path))
            if beat.audio_path:
                beat.audio_url = self.store.asset_url(Path(beat.audio_path))

    def _recommend_citizens(self, stage: StagePackage, topic: str) -> list[dict]:
        topic_tokens = {token for token in topic.lower().split() if len(token) > 2}
        scored: list[tuple[int, int, dict]] = []
        for citizen in stage.sample_citizens:
            haystack = " ".join(
                [
                    citizen.role,
                    citizen.region,
                    citizen.mood,
                    citizen.ai_exposure,
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
        return best[1] if best else None
