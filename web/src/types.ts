export type SimulationStatus = "initializing" | "stage_ready" | "resolving" | "completed" | "error";
export type RoomName = "briefing" | "advisor" | "citizens" | "debate";
export type RealtimeRole = "advisor" | "citizen" | "debate";
export type ReasoningEffort = "none" | "low" | "medium" | "high";
export type AdvisorMode = "solo" | "council";
export type AuditoriumMode = "debate" | "town_hall";
export type SessionStatus = "idle" | "connecting" | "connected" | "error";
export type PresenceActivity = "idle" | "listening" | "speaking";
export type SetupSessionStatus = "drafting" | "ready" | "started";
export type FeaturetteStatus = "idle" | "queued" | "generating" | "ready" | "error";

export interface ScenePresence {
  status: SessionStatus;
  liveMode: "text" | "voice";
  muted: boolean;
  playerActivity: PresenceActivity;
  counterpartActivity: PresenceActivity;
  voicePhase: "idle" | "recording" | "waiting" | "responding";
}

export type SceneHotspotTone = "amber" | "steel" | "sage" | "rose";

export interface SceneHotspot {
  id: string;
  label: string;
  hint?: string;
  position: [number, number, number];
  tone?: SceneHotspotTone;
  active?: boolean;
  disabled?: boolean;
  action: "room" | "citizen" | "panel" | "resolve" | "townhall" | "advisor_mode";
  room?: RoomName;
  citizenId?: string;
}

export interface TrackingMetric {
  key: string;
  label: string;
  value: number;
  display: string;
  delta: number;
}

export interface NarrativeBeat {
  id: string;
  line: string;
  image_prompt: string;
  image_path?: string | null;
  image_url?: string | null;
  audio_path?: string | null;
  audio_url?: string | null;
}

export interface CitizenSnapshot {
  citizen_id: string;
  display_name: string;
  role: string;
  region: string;
  voice: string;
  support_label: string;
  mood: string;
  ai_exposure: string;
  household: string;
  daily_routine: string;
  recent_ai_moment: string;
  current_worries: string;
  current_hopes: string;
  speech_habits: string;
  voice_notes: string;
  town_hall_question: string;
  town_hall_cue: string;
  summary: string;
  current_update: string;
  approval_band: "approve" | "mixed" | "disapprove";
  support_score: number;
}

export interface PollSummary {
  key?: string | null;
  source?: "standard" | "advisor" | "manual";
  board_label?: string | null;
  board_slot?: "capability" | "national" | "gain" | "pressure" | "custom" | null;
  question: string;
  counts: Record<string, number>;
  shares: Record<string, number>;
  sample_reasons?: string[];
}

export interface StageTracking {
  approval: TrackingMetric;
  vote_share_player: TrackingMetric;
  vote_share_opponent: TrackingMetric;
  better_off: TrackingMetric;
  ai_comfort: TrackingMetric;
  unemployment_anxiety: TrackingMetric;
  trust_in_government: TrackingMetric;
  social_stability: TrackingMetric;
}

export interface DebateReply {
  opponent_opening: string;
  opponent_rebuttal: string;
  analyst_take: string;
}

export interface StageResolution {
  player_platform: string;
  player_rebuttal?: string | null;
  player_agenda_points: string[];
  opponent_agenda_points: string[];
  winner: string;
  enacted_agenda: string;
  public_mandate: string;
  election_takeaway?: string | null;
  pre_debate_vote_share_player?: number | null;
  pre_debate_vote_share_opponent?: number | null;
  post_debate_vote_share_player?: number | null;
  post_debate_vote_share_opponent?: number | null;
}

export interface ConversationTurn {
  id: string;
  speaker: "user" | "assistant" | "system";
  speaker_name?: string;
  speaker_voice?: string;
  text: string;
  mode: "text" | "voice" | "system";
  created_at: string;
}

export interface StageProgress {
  phase:
    | "queued"
    | "seeding"
    | "stagewriting"
    | "media"
    | "citizen_updates"
    | "polling"
    | "ready"
    | "resolving"
    | "error";
  label: string;
  detail: string;
  percent: number;
}

export interface DocumentaryFeaturette {
  id: string;
  subject: string;
  question: string;
  title: string;
  logline: string;
  status: FeaturetteStatus;
  narrative_beats: NarrativeBeat[];
  error?: string | null;
  generated_at: string;
}

export interface StagePackage {
  index: number;
  phase_label: string;
  year_label: string;
  title: string;
  montage_logline: string;
  capability_frontier_now: string;
  still_hard_now: string;
  physical_world_status: string;
  dominant_mechanism: string;
  dominant_upside: string;
  main_split: string;
  household_income_system: string;
  capability_access_norm: string;
  firm_structure_norm: string;
  ownership_regime: string;
  public_service_norm: string;
  state_of_world: string;
  detailed_summary: string;
  room_briefing: string;
  authored_room_briefing: string;
  economic_indicators: string[];
  tension_points: string[];
  suggested_policy_axes: string[];
  authored_policy_axes: string[];
  narrative_beats: NarrativeBeat[];
  sample_citizens: CitizenSnapshot[];
  tracking: StageTracking;
  poll_summaries: PollSummary[];
  queued_poll_questions: string[];
  policy_notes: string[];
  featurettes: DocumentaryFeaturette[];
  featurettes_status: FeaturetteStatus;
  featurettes_error?: string | null;
  debate_reply?: DebateReply | null;
  resolution?: StageResolution | null;
  generated_at: string;
}

export interface SimulationConfig {
  title: string;
  country: string;
  player_role: string;
  player_name: string;
  opponent_role: string;
  opponent_name: string;
  opponent_voice: string;
  population_description: string;
  region_focus: string;
  topic_lens: string;
  premise: string;
  stakes: string;
  persona_count: number;
  stage_count: number;
  visual_style: string;
  orchestrator_reasoning_effort: ReasoningEffort;
  realtime_model: string;
}

export interface SimulationState {
  simulation_id: string;
  created_at: string;
  updated_at: string;
  status: SimulationStatus;
  current_room: RoomName;
  focused_citizen_id?: string | null;
  active_stage_index: number;
  incumbent_name: string;
  player_in_power: boolean;
  approval_rating: number;
  error?: string | null;
  config: SimulationConfig;
  stages: StagePackage[];
  queued_poll_questions: Array<{ question: string; source: "advisor" | "manual"; created_at: string }>;
  standard_questions: string[];
  persona_count_ready: number;
  current_polls: PollSummary[];
  progress: StageProgress;
  conversation_threads: Record<string, ConversationTurn[]>;
}

export interface RealtimeSessionResponse {
  client_secret: string;
  model: string;
  voice: string;
  session_type: string;
  session_variant?: string | null;
}

export interface RealtimeToolResult {
  ok: boolean;
  data: Record<string, unknown>;
}

export interface RunPollsResponse {
  simulation: SimulationState;
  poll_summaries: PollSummary[];
}

export interface SetupDraft {
  title: string;
  country: string;
  region_focus?: string | null;
  topic_lens?: string | null;
  population_description?: string | null;
  player_name?: string | null;
  player_role?: string | null;
  opponent_name?: string | null;
  opponent_role?: string | null;
  opponent_voice?: string | null;
  premise?: string | null;
  stakes?: string | null;
  persona_count: number;
  stage_count: number;
  visual_style?: string | null;
  orchestrator_reasoning_effort: ReasoningEffort;
  realtime_model: string;
}

export interface SetupTranscriptTurn {
  id: string;
  speaker: "user" | "assistant" | "system";
  text: string;
  created_at: string;
}

export interface SetupGuidance {
  chamber_reply: string;
  readiness: "ready" | "needs_input";
  applied_updates: string[];
  open_questions: string[];
  next_actions: string[];
}

export interface SetupSessionState {
  session_id: string;
  mode: "live" | "fallback";
  status: SetupSessionStatus;
  endpoint_base?: string;
  draft: SetupDraft;
  transcript: SetupTranscriptTurn[];
  guidance?: SetupGuidance;
  chamber_intro?: string;
  suggested_prompts?: string[];
  updated_at: string;
}

export interface SimulationCreateRequest {
  title: string;
  country: string;
  region_focus?: string | null;
  topic_lens?: string | null;
  player_name?: string | null;
  player_role?: string | null;
  opponent_name?: string | null;
  opponent_role?: string | null;
  opponent_voice?: string;
  persona_count: number;
  stage_count: number;
  population_description?: string;
  premise?: string | null;
  stakes?: string | null;
  visual_style?: string;
  orchestrator_reasoning_effort: ReasoningEffort;
  realtime_model: string;
}

export interface CouncilTurnResponse {
  simulation: SimulationState;
  thread_key: string;
  lead: string;
  urgencies: Record<string, number>;
  contrast: string[];
  reason?: string | null;
  yield_after_turn: boolean;
  player_proxy_urgency: number;
  board_notes: string[];
  turns: ConversationTurn[];
}

export interface TownHallQuestionResponse {
  simulation: SimulationState;
  thread_key: string;
  cue: string;
  question_turn: ConversationTurn;
}

export interface ResolveStageRequest {
  player_platform: string;
  player_rebuttal?: string;
}

export interface ConversationSyncResponse {
  simulation: SimulationState;
  thread_key: string;
}

export function trackingList(tracking: StageTracking): TrackingMetric[] {
  return [
    tracking.approval,
    tracking.vote_share_player,
    tracking.vote_share_opponent,
    tracking.better_off,
    tracking.ai_comfort,
    tracking.unemployment_anxiety,
    tracking.trust_in_government,
    tracking.social_stability,
  ];
}

export function makeDefaultSetupDraft(): SetupDraft {
  return {
    title: "AGI Transition Command",
    country: "United States",
    region_focus: "",
    topic_lens: "",
    population_description:
      "A representative sample of the current United States adult population, with realistic variation across region, class, education, industry, family structure, ideology, ethnicity, age, and AI exposure.",
    player_name: "President Lena Park",
    player_role: "incumbent president",
    opponent_name: "Governor Malcolm Pryce",
    opponent_role: "challenger governor",
    opponent_voice: "ash",
    premise: "",
    stakes: "",
    persona_count: 48,
    stage_count: 5,
    visual_style:
      "Painterly civic documentary in a Cezanne, Monet, and Matisse register: bold color planes, softened edges, lived-in institutions and neighborhoods, atmospheric light, selective abstraction, and never glossy CGI, stock-photo realism, or cartoon exaggeration.",
    orchestrator_reasoning_effort: "low",
    realtime_model: "gpt-realtime-1.5",
  };
}

export function setupDraftToCreateRequest(draft: SetupDraft): SimulationCreateRequest {
  return {
    title: draft.title.trim() || "AGI Transition Command",
    country: draft.country.trim() || "United States",
    region_focus: draft.region_focus?.trim() || undefined,
    topic_lens: draft.topic_lens?.trim() || undefined,
    player_name: draft.player_name?.trim() || draft.player_role?.trim() || undefined,
    player_role: draft.player_role?.trim() || undefined,
    opponent_name: draft.opponent_name?.trim() || draft.opponent_role?.trim() || undefined,
    opponent_role: draft.opponent_role?.trim() || undefined,
    opponent_voice: draft.opponent_voice?.trim() || undefined,
    persona_count: draft.persona_count,
    stage_count: draft.stage_count,
    population_description: draft.population_description?.trim() || undefined,
    premise: draft.premise?.trim() || undefined,
    stakes: draft.stakes?.trim() || undefined,
    visual_style: draft.visual_style?.trim() || undefined,
    orchestrator_reasoning_effort: draft.orchestrator_reasoning_effort,
    realtime_model: draft.realtime_model.trim() || "gpt-realtime-1.5",
  };
}
