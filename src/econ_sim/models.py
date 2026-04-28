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


class MacroStatistic(BaseModel):
    label: str
    value: str
    detail: str = ""


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
    world_brief: str
    room_briefing: str
    narrative_beats: list[NarrativeBeat]
    sample_citizens: list[CitizenSnapshot]
    tracking: StageTracking
    macro_stats: dict[str, MacroStatistic] = Field(default_factory=dict)
    poll_summaries: list[PollSummary]
    queued_poll_questions: list[str]
    policy_notes: list[str] = Field(default_factory=list)
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
    title: str = ""
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
    council_roster: list[CouncilAdvisorProfile] = Field(default_factory=list)
    orchestrator_reasoning_effort: ReasoningEffort = "medium"
    realtime_model: str = "gpt-realtime-alpha-dolphin-11"


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
    title: str | None = None
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
    council_roster: list["CouncilAdvisorProfile"] = Field(default_factory=list)
    orchestrator_reasoning_effort: ReasoningEffort = "medium"
    realtime_model: str = "gpt-realtime-alpha-dolphin-11"


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
    council_roster: list["CouncilAdvisorProfile"] | None = None


class SetupSessionCreateRequest(SetupSessionPatchRequest):
    pass


class SetupChamberGuidance(BaseModel):
    chamber_reply: str
    readiness: Literal["ready", "needs_input"] = "ready"
    launch_now: bool = False
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
    preferred_speaker: str = ""
    avoid_speaker: str = ""
    provisional_turns: list[ConversationTurnInput] = Field(default_factory=list)
    provisional_board_notes: list[str] = Field(default_factory=list)
    commit: bool = True


class CouncilAdvisorProfile(BaseModel):
    key: str
    name: str
    room_role: str
    country_role: str
    remit: str
    voice: str = "alloy"
    viewpoint: str = ""


class CouncilAdvisorAction(BaseModel):
    name: Literal[
        "run_poll_now",
        "run_queued_polls",
        "update_policy_board",
        "move_room_focus",
        "focus_citizen_by_name",
    ]
    arguments: dict[str, Any] = Field(default_factory=dict)


class CouncilAdvisorDraft(BaseModel):
    advisor_key: str
    advisor_name: str
    text: str = ""
    reason: str = ""
    board_notes: list[str] = Field(default_factory=list)
    action: CouncilAdvisorAction | None = None


class CouncilFloorPick(BaseModel):
    next_speaker: str


class CouncilSpeakerDecision(BaseModel):
    next_speaker: str
    reason: str
    yield_after_turn: bool = False
    board_notes: list[str] = Field(default_factory=list)
    contrast: list[str] = Field(default_factory=list)
    action: CouncilAdvisorAction | None = None


class CouncilTurnResponse(BaseModel):
    simulation: SimulationState
    thread_key: str
    lead: str
    next_speaker: str = "player"
    contrast: list[str] = Field(default_factory=list)
    reason: str | None = None
    yield_after_turn: bool = False
    board_notes: list[str] = Field(default_factory=list)
    turns: list[ConversationTurn] = Field(default_factory=list)
    audio_base64: str | None = None
    audio_format: str | None = None


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


class TownHallOpponentReplyRequest(BaseModel):
    citizen_id: str | None = None
    question_text: str = ""
    mode: Literal["text", "voice"] = "voice"


class TownHallOpponentReplyDraft(BaseModel):
    reply: str


class TownHallOpponentReplyResponse(BaseModel):
    simulation: SimulationState
    thread_key: str
    reply_turn: ConversationTurn
