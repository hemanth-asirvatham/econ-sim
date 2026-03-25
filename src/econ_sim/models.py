from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:12]}"


ReasoningEffort = Literal["none", "low", "medium", "high"]
ConversationSpeaker = Literal["user", "assistant", "system"]
ConversationMode = Literal["text", "voice", "system"]
FeaturetteStatus = Literal["idle", "queued", "generating", "ready", "error"]


class SimulationStatus(str, Enum):
    initializing = "initializing"
    stage_ready = "stage_ready"
    resolving = "resolving"
    completed = "completed"
    error = "error"


class SetupSessionStatus(str, Enum):
    drafting = "drafting"
    ready = "ready"
    started = "started"


class RoomName(str, Enum):
    briefing = "briefing"
    advisor = "advisor"
    citizens = "citizens"
    debate = "debate"


class ApprovalBand(str, Enum):
    approve = "approve"
    mixed = "mixed"
    disapprove = "disapprove"


class PreparationPhase(str, Enum):
    queued = "queued"
    seeding = "seeding"
    stagewriting = "stagewriting"
    media = "media"
    citizen_updates = "citizen_updates"
    polling = "polling"
    ready = "ready"
    resolving = "resolving"
    error = "error"


class TrackingMetric(BaseModel):
    key: str
    label: str
    value: float
    display: str
    delta: float = 0.0


class NarrativeBeat(BaseModel):
    id: str = Field(default_factory=lambda: new_id("beat"))
    line: str
    image_prompt: str
    image_path: str | None = None
    image_url: str | None = None
    audio_path: str | None = None
    audio_url: str | None = None


class CitizenSnapshot(BaseModel):
    citizen_id: str
    display_name: str
    role: str
    region: str
    voice: str = "alloy"
    support_label: str
    mood: str
    ai_exposure: str
    household: str = ""
    daily_routine: str = ""
    recent_ai_moment: str = ""
    current_worries: str = ""
    current_hopes: str = ""
    speech_habits: str = ""
    voice_notes: str = ""
    baseline_ai_instinct: str = ""
    baseline_priority: str = ""
    town_hall_question: str = ""
    town_hall_cue: str = ""
    summary: str
    current_update: str
    approval_band: ApprovalBand
    support_score: int = Field(ge=0, le=100)


class PollSummary(BaseModel):
    key: str | None = None
    source: Literal["standard", "advisor", "manual"] = "standard"
    board_label: str | None = None
    board_slot: Literal["capability", "national", "gain", "pressure", "custom"] | None = None
    question: str
    counts: dict[str, int]
    shares: dict[str, float]
    sample_reasons: list[str] = Field(default_factory=list)


class StageTracking(BaseModel):
    approval: TrackingMetric
    vote_share_player: TrackingMetric
    vote_share_opponent: TrackingMetric
    better_off: TrackingMetric
    ai_comfort: TrackingMetric
    unemployment_anxiety: TrackingMetric
    trust_in_government: TrackingMetric
    social_stability: TrackingMetric

    def as_list(self) -> list[TrackingMetric]:
        return [
            self.approval,
            self.vote_share_player,
            self.vote_share_opponent,
            self.better_off,
            self.ai_comfort,
            self.unemployment_anxiety,
            self.trust_in_government,
            self.social_stability,
        ]


class DebateReply(BaseModel):
    opponent_opening: str
    opponent_rebuttal: str
    analyst_take: str


class StageResolution(BaseModel):
    player_platform: str
    player_rebuttal: str | None = None
    player_agenda_points: list[str] = Field(default_factory=list)
    opponent_agenda_points: list[str] = Field(default_factory=list)
    winner: str
    enacted_agenda: str
    public_mandate: str
    election_takeaway: str | None = None
    pre_debate_vote_share_player: float | None = None
    pre_debate_vote_share_opponent: float | None = None
    post_debate_vote_share_player: float | None = None
    post_debate_vote_share_opponent: float | None = None


class ConversationTurn(BaseModel):
    id: str = Field(default_factory=lambda: new_id("turn"))
    speaker: ConversationSpeaker
    speaker_name: str | None = None
    speaker_voice: str | None = None
    text: str
    mode: ConversationMode = "text"
    created_at: datetime = Field(default_factory=utc_now)


class StageProgress(BaseModel):
    phase: PreparationPhase = PreparationPhase.queued
    label: str = "Queued"
    detail: str = "Waiting to initialize the next stage."
    percent: int = Field(default=0, ge=0, le=100)


class DocumentaryFeaturette(BaseModel):
    id: str = Field(default_factory=lambda: new_id("reel"))
    subject: str
    question: str = ""
    title: str
    logline: str
    status: FeaturetteStatus = "queued"
    narrative_beats: list[NarrativeBeat] = Field(default_factory=list)
    error: str | None = None
    generated_at: datetime = Field(default_factory=utc_now)


class StagePackage(BaseModel):
    index: int
    phase_label: str = "Transition stage"
    year_label: str
    title: str
    montage_logline: str = ""
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
    state_of_world: str
    detailed_summary: str
    room_briefing: str
    authored_room_briefing: str = ""
    economic_indicators: list[str]
    tension_points: list[str]
    suggested_policy_axes: list[str]
    authored_policy_axes: list[str] = Field(default_factory=list)
    narrative_beats: list[NarrativeBeat]
    sample_citizens: list[CitizenSnapshot]
    tracking: StageTracking
    poll_summaries: list[PollSummary]
    queued_poll_questions: list[str]
    policy_notes: list[str] = Field(default_factory=list)
    policy_board_manual: bool = False
    featurettes: list[DocumentaryFeaturette] = Field(default_factory=list)
    featurettes_status: FeaturetteStatus = "idle"
    featurettes_error: str | None = None
    debate_reply: DebateReply | None = None
    resolution: StageResolution | None = None
    orchestrator_response_id: str | None = None
    generated_at: datetime = Field(default_factory=utc_now)


class QueuedPollQuestion(BaseModel):
    question: str
    source: Literal["advisor", "manual"] = "manual"
    created_at: datetime = Field(default_factory=utc_now)


class SimulationConfig(BaseModel):
    title: str = "AGI Transition Command"
    country: str = "United States"
    player_name: str
    player_role: str = "incumbent president"
    opponent_name: str
    opponent_role: str = "challenger governor"
    opponent_voice: str = "ash"
    population_description: str
    region_focus: str = ""
    topic_lens: str = ""
    premise: str = ""
    stakes: str = ""
    persona_count: int = Field(default=64, ge=8, le=256)
    stage_count: int = Field(default=5, ge=3, le=8)
    visual_style: str
    orchestrator_reasoning_effort: ReasoningEffort = "low"
    realtime_model: str = "gpt-realtime-1.5"


class SimulationState(BaseModel):
    simulation_id: str
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    status: SimulationStatus = SimulationStatus.initializing
    current_room: RoomName = RoomName.briefing
    focused_citizen_id: str | None = None
    active_stage_index: int = 0
    incumbent_name: str
    player_in_power: bool = True
    approval_rating: float = 50.0
    error: str | None = None
    config: SimulationConfig
    stages: list[StagePackage] = Field(default_factory=list)
    queued_poll_questions: list[QueuedPollQuestion] = Field(default_factory=list)
    standard_questions: list[str] = Field(default_factory=list)
    persona_count_ready: int = 0
    current_polls: list[PollSummary] = Field(default_factory=list)
    progress: StageProgress = Field(default_factory=StageProgress)
    conversation_threads: dict[str, list[ConversationTurn]] = Field(default_factory=dict)


class SimulationCreateRequest(BaseModel):
    title: str = "AGI Transition Command"
    country: str = "United States"
    player_name: str | None = None
    player_role: str | None = None
    opponent_name: str | None = None
    opponent_role: str | None = None
    opponent_voice: str | None = None
    population_description: str | None = None
    region_focus: str | None = None
    topic_lens: str | None = None
    premise: str | None = None
    stakes: str | None = None
    persona_count: int = Field(default=48, ge=8, le=256)
    stage_count: int = Field(default=5, ge=3, le=8)
    visual_style: str | None = None
    orchestrator_reasoning_effort: ReasoningEffort = "low"
    realtime_model: str = "gpt-realtime-1.5"


class SetupSessionPatchRequest(BaseModel):
    title: str | None = None
    country: str | None = None
    player_name: str | None = None
    player_role: str | None = None
    opponent_name: str | None = None
    opponent_role: str | None = None
    opponent_voice: str | None = None
    population_description: str | None = None
    region_focus: str | None = None
    topic_lens: str | None = None
    premise: str | None = None
    stakes: str | None = None
    persona_count: int | None = Field(default=None, ge=8, le=256)
    stage_count: int | None = Field(default=None, ge=3, le=8)
    visual_style: str | None = None
    orchestrator_reasoning_effort: ReasoningEffort | None = None
    realtime_model: str | None = None


class SetupSessionCreateRequest(SetupSessionPatchRequest):
    pass


class SetupChamberGuidance(BaseModel):
    chamber_reply: str
    readiness: Literal["ready", "needs_input"] = "ready"
    applied_updates: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    next_actions: list[str] = Field(default_factory=list)
    config_updates: SetupSessionPatchRequest = Field(default_factory=SetupSessionPatchRequest)


class SetupSessionDefaults(BaseModel):
    config: SimulationConfig
    chamber_intro: str
    suggested_prompts: list[str] = Field(default_factory=list)


class SetupSessionTurnRequest(BaseModel):
    text: str


class SetupSessionState(BaseModel):
    setup_session_id: str
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    status: SetupSessionStatus = SetupSessionStatus.ready
    config: SimulationConfig
    turns: list[ConversationTurn] = Field(default_factory=list)
    guidance: SetupChamberGuidance | None = None
    started_simulation_id: str | None = None


class SetupSessionStartResponse(BaseModel):
    setup_session: SetupSessionState
    simulation: SimulationState


class QueuePollRequest(BaseModel):
    question: str
    source: Literal["advisor", "manual"] = "manual"


class RunPollsResponse(BaseModel):
    simulation: SimulationState
    poll_summaries: list[PollSummary]


class ResolveStageRequest(BaseModel):
    player_platform: str
    player_rebuttal: str | None = None


class RealtimeRole(str, Enum):
    advisor = "advisor"
    citizen = "citizen"
    debate = "debate"


class AdvisorMode(str, Enum):
    solo = "solo"
    council = "council"


class AuditoriumMode(str, Enum):
    debate = "debate"
    town_hall = "town_hall"


class RealtimeSessionRequest(BaseModel):
    role: RealtimeRole
    citizen_id: str | None = None
    advisor_mode: AdvisorMode = AdvisorMode.solo
    auditorium_mode: AuditoriumMode = AuditoriumMode.debate
    auto_response: bool | None = None


class RealtimeToolResult(BaseModel):
    ok: bool = True
    data: dict[str, Any] = Field(default_factory=dict)


class SpeechSynthesisRequest(BaseModel):
    text: str
    voice: str


class RealtimeSessionResponse(BaseModel):
    client_secret: str
    model: str
    voice: str
    session_type: str
    session_variant: str | None = None


class ConversationTurnInput(BaseModel):
    speaker: ConversationSpeaker
    speaker_name: str | None = None
    speaker_voice: str | None = None
    text: str
    mode: ConversationMode = "text"


class ConversationSyncRequest(BaseModel):
    role: RealtimeRole
    citizen_id: str | None = None
    advisor_mode: AdvisorMode = AdvisorMode.solo
    auditorium_mode: AuditoriumMode = AuditoriumMode.debate
    turns: list[ConversationTurnInput]
    board_notes: list[str] | None = None


class ConversationSyncResponse(BaseModel):
    simulation: SimulationState
    thread_key: str


class CouncilTurnRequest(BaseModel):
    text: str = ""
    mode: Literal["text", "voice"] = "text"
    continue_dialogue: bool = False


class CouncilAdvisorBeat(BaseModel):
    name: Literal["Rowan", "Leila", "Mateo", "Amina"]
    urgency: int = Field(ge=0, le=10)
    speak: bool = False
    text: str = ""


class CouncilTurnPlan(BaseModel):
    lead: Literal["Rowan", "Leila", "Mateo", "Amina"]
    reason: str
    yield_after_turn: bool = False
    player_proxy_urgency: int = Field(default=0, ge=0, le=10)
    board_notes: list[str] = Field(default_factory=list)
    advisors: list[CouncilAdvisorBeat]


class CouncilTurnResponse(BaseModel):
    simulation: SimulationState
    thread_key: str
    lead: str
    urgencies: dict[str, int]
    speaker_order: list[str] = Field(default_factory=list)
    contrast: list[str] = Field(default_factory=list)
    reason: str | None = None
    yield_after_turn: bool = False
    player_proxy_urgency: int = 0
    board_notes: list[str] = Field(default_factory=list)
    turns: list[ConversationTurn] = Field(default_factory=list)


class TownHallQuestionRequest(BaseModel):
    citizen_id: str | None = None
    mode: Literal["text", "voice"] = "voice"


class TownHallQuestionDraft(BaseModel):
    question: str
    cue: str = ""


class TownHallQuestionResponse(BaseModel):
    simulation: SimulationState
    thread_key: str
    cue: str = ""
    question_turn: ConversationTurn
