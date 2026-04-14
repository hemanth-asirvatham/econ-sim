import type {
  AdvisorMode,
  AuditoriumMode,
  CouncilAdvisorProfile,
  CouncilTurnResponse,
  ConversationSyncResponse,
  RealtimeRole,
  RealtimeSessionResponse,
  RealtimeToolResult,
  ResolveStageRequest,
  RunPollsResponse,
  SetupDraft,
  SetupSessionState,
  SetupTranscriptTurn,
  SimulationCreateRequest,
  SimulationState,
  TownHallQuestionResponse,
  TownHallOpponentReplyResponse,
} from "../types";
import { makeDefaultSetupDraft, setupDraftToCreateRequest } from "../types";

function resolveDefaultApiBase() {
  if (typeof window === "undefined") {
    return "http://127.0.0.1:8000";
  }

  const { protocol, hostname, port, origin } = window.location;
  if (port === "8000") {
    return origin;
  }
  if (port === "5173" || port === "4173") {
    return `${protocol}//${hostname}:8000`;
  }
  return origin;
}

export const API_BASE = import.meta.env.VITE_API_BASE?.trim() || resolveDefaultApiBase();
const SETUP_FALLBACK_STATUSES = new Set([400, 404, 405, 422, 501]);

interface SetupCandidate {
  path: string;
  method?: "GET" | "POST" | "PATCH" | "PUT";
  body?: unknown;
  endpointBase: string;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers ?? {});
  const hasBody = init?.body !== undefined && init?.body !== null;
  if (
    hasBody &&
    !(typeof FormData !== "undefined" && init?.body instanceof FormData) &&
    !headers.has("Content-Type")
  ) {
    headers.set("Content-Type", "application/json");
  }
  const response = await fetch(`${API_BASE}${path}`, {
    headers,
    ...init,
  });
  if (!response.ok) {
    const body = await response.text();
    throw new Error(body || `request failed with ${response.status}`);
  }
  return (await response.json()) as T;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function asString(value: unknown): string | undefined {
  return typeof value === "string" && value.trim() ? value : undefined;
}

function asNumber(value: unknown): number | undefined {
  return typeof value === "number" && Number.isFinite(value) ? value : undefined;
}

function asReasoning(value: unknown): SetupDraft["orchestrator_reasoning_effort"] | undefined {
  if (value === "none" || value === "low" || value === "medium" || value === "high") {
    return value;
  }
  return undefined;
}

function normalizeCouncilRoster(value: unknown): CouncilAdvisorProfile[] {
  if (!Array.isArray(value)) {
    return [];
  }
  return value
    .flatMap((entry) => {
      if (!isRecord(entry)) {
        return [];
      }
      const key = asString(entry.key);
      const name = asString(entry.name);
      const room_role = asString(entry.room_role);
      const country_role = asString(entry.country_role);
      const remit = asString(entry.remit);
      if (!key || !name || !room_role || !country_role || !remit) {
        return [];
      }
      return [{
        key,
        name,
        room_role,
        country_role,
        remit,
        voice: asString(entry.voice) ?? "cedar",
        viewpoint: asString(entry.viewpoint) ?? "",
      } satisfies CouncilAdvisorProfile];
    });
}

function setupRoot(payload: unknown): Record<string, unknown> {
  if (!isRecord(payload)) {
    return {};
  }
  if (isRecord(payload.setup_session)) {
    return payload.setup_session;
  }
  if (isRecord(payload.session)) {
    return payload.session;
  }
  return payload;
}

function normalizeSetupDraft(payload: unknown, fallbackDraft: SetupDraft): SetupDraft {
  const root = setupRoot(payload);
  const draftSource = isRecord(root.draft)
    ? root.draft
    : isRecord(root.defaults)
      ? root.defaults
      : isRecord(root.config)
        ? root.config
        : root;

  const council_roster = normalizeCouncilRoster(draftSource.council_roster);

  return {
    ...fallbackDraft,
    title: asString(draftSource.title) ?? fallbackDraft.title,
    country: asString(draftSource.country) ?? fallbackDraft.country,
    region_focus: asString(draftSource.region_focus) ?? fallbackDraft.region_focus,
    topic_lens: asString(draftSource.topic_lens) ?? fallbackDraft.topic_lens,
    population_description: asString(draftSource.population_description) ?? fallbackDraft.population_description,
    player_name: asString(draftSource.player_name) ?? fallbackDraft.player_name,
    player_role: asString(draftSource.player_role) ?? fallbackDraft.player_role,
    opponent_name: asString(draftSource.opponent_name) ?? fallbackDraft.opponent_name,
    opponent_role: asString(draftSource.opponent_role) ?? fallbackDraft.opponent_role,
    opponent_voice: asString(draftSource.opponent_voice) ?? fallbackDraft.opponent_voice,
    premise: asString(draftSource.premise) ?? fallbackDraft.premise,
    stakes: asString(draftSource.stakes) ?? fallbackDraft.stakes,
    persona_count: asNumber(draftSource.persona_count) ?? fallbackDraft.persona_count,
    stage_count: asNumber(draftSource.stage_count) ?? fallbackDraft.stage_count,
    visual_style: asString(draftSource.visual_style) ?? fallbackDraft.visual_style,
    orchestrator_reasoning_effort:
      asReasoning(draftSource.orchestrator_reasoning_effort) ?? fallbackDraft.orchestrator_reasoning_effort,
    realtime_model: asString(draftSource.realtime_model) ?? fallbackDraft.realtime_model,
    council_roster: council_roster.length > 0 ? council_roster : fallbackDraft.council_roster,
  };
}

function nowIso() {
  return new Date().toISOString();
}

function sanitizeRealtimeArtifactText(text: string) {
  return text
    .replace(/<\|[^|>]+?\|>/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

function makeSetupTurn(speaker: SetupTranscriptTurn["speaker"], text: string): SetupTranscriptTurn {
  return {
    id: `setup-${typeof crypto !== "undefined" && "randomUUID" in crypto ? crypto.randomUUID() : Math.random().toString(36).slice(2)}`,
    speaker,
    text,
    created_at: nowIso(),
  };
}

function focusLabel(draft: SetupDraft) {
  const country = draft.country.trim() || "the default country frame";
  const region = draft.region_focus?.trim() || "the national field";
  const lens = draft.topic_lens?.trim() || "the broad AGI transition";
  return `${country} · ${region} · ${lens}`;
}

function castLabel(draft: SetupDraft) {
  const player = draft.player_name?.trim() || draft.player_role?.trim() || "the player";
  const opponent = draft.opponent_name?.trim() || draft.opponent_role?.trim() || "the opponent";
  return `${player} versus ${opponent}`;
}

function composeSetupSummary(draft: SetupDraft) {
  return `Current draft: ${focusLabel(draft)}. Cast: ${castLabel(draft)}. Scale: ${draft.persona_count} citizens across ${draft.stage_count} chapters.`;
}

function composeDraftUpdate(draft: SetupDraft) {
  const premise = draft.premise?.trim();
  return premise
    ? `Draft tightened. ${composeSetupSummary(draft)} Working premise: ${premise}`
    : `Draft tightened. ${composeSetupSummary(draft)}`;
}

function composePromptReply(prompt: string, draft: SetupDraft) {
  const cleaned = prompt.trim().replace(/\s+/g, " ");
  const clipped = cleaned.length > 170 ? `${cleaned.slice(0, 167)}...` : cleaned;
  const stakes = draft.stakes?.trim();
  return stakes
    ? `Noted. I am weighting the run toward ${focusLabel(draft)} with ${castLabel(draft)} in frame. Your note is now part of the chamber brief: ${clipped} Stakes held: ${stakes}`
    : `Noted. I am weighting the run toward ${focusLabel(draft)} with ${castLabel(draft)} in frame. Your note is now part of the chamber brief: ${clipped}`;
}

function seedTranscript(draft: SetupDraft, note?: string): SetupTranscriptTurn[] {
  const transcript = [
    makeSetupTurn(
      "assistant",
      "I am the setup orchestrator. Tell me what world, institution, or future you want to examine if you want to steer it. If not, say go and I will launch the broad default run.",
    ),
  ];
  if (note) {
    transcript.push(makeSetupTurn("system", note));
  }
  return transcript;
}

function normalizeSetupGuidance(payload: unknown) {
  const root = setupRoot(payload);
  const source = isRecord(root.guidance) ? root.guidance : undefined;
  if (!source) {
    return undefined;
  }
  return {
    chamber_reply: asString(source.chamber_reply) ?? "The chamber is ready.",
    readiness: source.readiness === "needs_input" ? "needs_input" : "ready",
    applied_updates: Array.isArray(source.applied_updates)
      ? source.applied_updates.flatMap((entry) => (typeof entry === "string" ? [entry] : []))
      : [],
    open_questions: Array.isArray(source.open_questions)
      ? source.open_questions.flatMap((entry) => (typeof entry === "string" ? [entry] : []))
      : [],
    next_actions: Array.isArray(source.next_actions)
      ? source.next_actions.flatMap((entry) => (typeof entry === "string" ? [entry] : []))
      : [],
  } satisfies NonNullable<SetupSessionState["guidance"]>;
}

function normalizeSetupTranscript(payload: unknown, draft: SetupDraft, note?: string): SetupTranscriptTurn[] {
  const root = setupRoot(payload);
  const source = Array.isArray(root.transcript)
    ? root.transcript
    : Array.isArray(root.turns)
      ? root.turns
      : Array.isArray(root.messages)
        ? root.messages
        : Array.isArray(root.conversation)
          ? root.conversation
          : [];

  const transcript = source
    .flatMap((entry, index) => {
      if (!isRecord(entry)) {
        return [];
      }
      const text = asString(entry.text) ?? asString(entry.content) ?? asString(entry.message);
      if (!text) {
        return [];
      }
      const speaker = entry.speaker === "user" || entry.speaker === "assistant" || entry.speaker === "system" ? entry.speaker : "assistant";
      return [
        {
          id: asString(entry.id) ?? `setup-turn-${index}`,
          speaker,
          text,
          created_at: asString(entry.created_at) ?? asString(entry.timestamp) ?? nowIso(),
        } satisfies SetupTranscriptTurn,
      ];
    })
    .filter((turn) => turn.text.trim().length > 0);

  if (transcript.length > 0) {
    return transcript;
  }

  const guidance = normalizeSetupGuidance(payload);
  const seeded = seedTranscript(draft, note);
  if (guidance?.chamber_reply) {
    seeded.unshift(makeSetupTurn("assistant", guidance.chamber_reply));
  } else {
    seeded.push(makeSetupTurn("assistant", composeSetupSummary(draft)));
  }
  return seeded;
}

function normalizeSetupSession(
  payload: unknown,
  candidate: SetupCandidate | undefined,
  fallbackDraft: SetupDraft,
  note?: string,
): SetupSessionState {
  const root = setupRoot(payload);
  const draft = normalizeSetupDraft(payload, fallbackDraft);
  const sessionId =
    asString(root.session_id) ??
    asString(root.setup_session_id) ??
    asString(root.id) ??
    `setup-${typeof crypto !== "undefined" && "randomUUID" in crypto ? crypto.randomUUID() : Math.random().toString(36).slice(2)}`;
  const status = root.status === "started" ? "started" : root.status === "drafting" ? "drafting" : "ready";
  const transcript = normalizeSetupTranscript(payload, draft, note);
  const guidance = normalizeSetupGuidance(payload);

  return {
    session_id: sessionId,
    mode: candidate ? "live" : "fallback",
    status,
    endpoint_base: candidate?.endpointBase,
    draft,
    transcript,
    guidance,
    chamber_intro: asString(root.chamber_intro),
    suggested_prompts: Array.isArray(root.suggested_prompts)
      ? root.suggested_prompts.flatMap((entry) => (typeof entry === "string" ? [entry] : []))
      : undefined,
    updated_at: asString(root.updated_at) ?? nowIso(),
  };
}

function normalizeSimulation(payload: unknown): SimulationState | null {
  function sanitizeSimulation(state: SimulationState): SimulationState {
    const cleanedThreads = Object.fromEntries(
      Object.entries(state.conversation_threads ?? {}).map(([threadKey, turns]) => [
        threadKey,
        Array.isArray(turns)
          ? turns
              .map((turn) => ({
                ...turn,
                text: sanitizeRealtimeArtifactText(String(turn.text ?? "")),
              }))
              .filter((turn) => turn.text)
          : [],
      ]),
    );
    return {
      ...state,
      conversation_threads: cleanedThreads,
    };
  }

  if (!isRecord(payload)) {
    return null;
  }
  if (typeof payload.simulation_id === "string") {
    return sanitizeSimulation(payload as unknown as SimulationState);
  }
  if (isRecord(payload.simulation) && typeof payload.simulation.simulation_id === "string") {
    return sanitizeSimulation(payload.simulation as unknown as SimulationState);
  }
  if (isRecord(payload.state) && typeof payload.state.simulation_id === "string") {
    return sanitizeSimulation(payload.state as unknown as SimulationState);
  }
  return null;
}

async function requestSetupCandidates(candidates: SetupCandidate[]) {
  let lastError: Error | null = null;

  for (const candidate of candidates) {
    try {
      const response = await fetch(`${API_BASE}${candidate.path}`, {
        method: candidate.method ?? "GET",
        headers: {
          "Content-Type": "application/json",
        },
        body: candidate.body === undefined ? undefined : JSON.stringify(candidate.body),
      });
      if (response.ok) {
        return {
          candidate,
          payload: (await response.json()) as unknown,
        };
      }
      const body = await response.text();
      if (!SETUP_FALLBACK_STATUSES.has(response.status)) {
        lastError = new Error(body || `request failed with ${response.status}`);
      }
    } catch (caught) {
      lastError = caught instanceof Error ? caught : new Error("request failed");
    }
  }

  if (lastError) {
    throw lastError;
  }
  return null;
}

function setupBases(primary?: string) {
  return Array.from(new Set([primary, "/api/setup-sessions", "/api/setup-session"].filter(Boolean))) as string[];
}

function setupUpdateCandidates(session: SetupSessionState, draft: SetupDraft): SetupCandidate[] {
  return setupBases(session.endpoint_base).flatMap((base) => [
    { method: "PATCH", path: `${base}/${session.session_id}`, endpointBase: base, body: draft },
    { method: "PUT", path: `${base}/${session.session_id}`, endpointBase: base, body: draft },
    { method: "POST", path: `${base}/${session.session_id}`, endpointBase: base, body: draft },
    { method: "PATCH", path: `${base}/${session.session_id}/draft`, endpointBase: base, body: { draft } },
    { method: "POST", path: `${base}/${session.session_id}/draft`, endpointBase: base, body: { draft } },
  ]);
}

function setupMessageCandidates(session: SetupSessionState, prompt: string, draft: SetupDraft): SetupCandidate[] {
  return setupBases(session.endpoint_base).flatMap((base) => [
    { method: "POST", path: `${base}/${session.session_id}/turn`, endpointBase: base, body: { text: prompt } },
    { method: "POST", path: `${base}/${session.session_id}/message`, endpointBase: base, body: { message: prompt, draft } },
    { method: "POST", path: `${base}/${session.session_id}/prompt`, endpointBase: base, body: { prompt, draft } },
    { method: "POST", path: `${base}/${session.session_id}/conversation`, endpointBase: base, body: { message: prompt, draft } },
    { method: "POST", path: `${base}/${session.session_id}/turns`, endpointBase: base, body: { prompt, draft } },
  ]);
}

function setupStartCandidates(session: SetupSessionState): SetupCandidate[] {
  return setupBases(session.endpoint_base).flatMap((base) => [
    { method: "POST", path: `${base}/${session.session_id}/start`, endpointBase: base, body: session.draft },
    { method: "POST", path: `${base}/${session.session_id}/launch`, endpointBase: base, body: session.draft },
    { method: "POST", path: `${base}/${session.session_id}/simulation`, endpointBase: base, body: session.draft },
  ]);
}

export function createSimulation(payload: SimulationCreateRequest) {
  return request<SimulationState>("/api/simulations", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function getSimulationDefaults() {
  return request<SimulationCreateRequest>("/api/simulations/defaults");
}

export function buildCompatibilitySetupSession(draft = makeDefaultSetupDraft(), note?: string): SetupSessionState {
  return normalizeSetupSession(
    {
      draft,
      transcript: seedTranscript(draft, note),
      status: "ready",
    },
    undefined,
    draft,
    note,
  );
}

export async function bootstrapSetupSession() {
  const fallbackDefaults = await getSimulationDefaults().catch(() => setupDraftToCreateRequest(makeDefaultSetupDraft()));
  const fallbackDraft = normalizeSetupDraft(fallbackDefaults, makeDefaultSetupDraft());
  const result = await requestSetupCandidates([
    { method: "POST", path: "/api/setup-sessions", endpointBase: "/api/setup-sessions", body: {} },
    { method: "POST", path: "/api/setup-session", endpointBase: "/api/setup-session", body: {} },
    { method: "GET", path: "/api/setup-sessions/defaults", endpointBase: "/api/setup-sessions" },
    { method: "GET", path: "/api/setup-session/defaults", endpointBase: "/api/setup-session" },
  ]).catch(() => null);

  if (!result) {
    return buildCompatibilitySetupSession(
      fallbackDraft,
      "This local checkout is still exposing the direct simulation-create API, so the chamber is running in compatibility mode until setup-session routes are available.",
    );
  }

  const session = normalizeSetupSession(result.payload, result.candidate, fallbackDraft);
  const setupRootPayload = setupRoot(result.payload);
  if (
    !session.endpoint_base ||
    (!asString(setupRootPayload.session_id) && !asString(setupRootPayload.setup_session_id))
  ) {
    return buildCompatibilitySetupSession(
      session.draft,
      "The backend provided setup defaults but not a durable setup session id, so the chamber is keeping a local draft and will translate it on launch.",
    );
  }
  return session;
}

export async function persistSetupDraft(session: SetupSessionState, draft: SetupDraft) {
  if (session.mode !== "live" || !session.endpoint_base) {
    return {
      ...session,
      draft,
      status: "ready" as const,
      transcript: [...session.transcript, makeSetupTurn("assistant", composeDraftUpdate(draft))],
      guidance: session.guidance
        ? {
            ...session.guidance,
            chamber_reply: composeDraftUpdate(draft),
            applied_updates: [],
          }
        : session.guidance,
      updated_at: nowIso(),
    };
  }

  const result = await requestSetupCandidates(setupUpdateCandidates(session, draft));
  if (!result) {
    return {
      ...session,
      mode: "fallback" as const,
      endpoint_base: undefined,
      draft,
      transcript: [
        ...session.transcript,
        makeSetupTurn("system", "Setup-session update endpoints were unavailable, so the chamber stayed in compatibility mode."),
        makeSetupTurn("assistant", composeDraftUpdate(draft)),
      ],
      guidance: session.guidance
        ? {
            ...session.guidance,
            chamber_reply: composeDraftUpdate(draft),
          }
        : session.guidance,
      updated_at: nowIso(),
    };
  }
  return normalizeSetupSession(result.payload, result.candidate, draft);
}

export async function sendSetupPrompt(session: SetupSessionState, prompt: string) {
  const userTurn = makeSetupTurn("user", prompt);

  if (session.mode !== "live" || !session.endpoint_base) {
    return {
      ...session,
      transcript: [...session.transcript, userTurn, makeSetupTurn("assistant", composePromptReply(prompt, session.draft))],
      guidance: session.guidance
        ? {
            ...session.guidance,
            chamber_reply: composePromptReply(prompt, session.draft),
          }
        : session.guidance,
      updated_at: nowIso(),
    };
  }

  const result = await requestSetupCandidates(setupMessageCandidates(session, prompt, session.draft));
  if (!result) {
    return {
      ...session,
      mode: "fallback" as const,
      endpoint_base: undefined,
      transcript: [
        ...session.transcript,
        userTurn,
        makeSetupTurn("system", "The setup-session prompt route was unavailable, so the chamber switched to compatibility mode."),
        makeSetupTurn("assistant", composePromptReply(prompt, session.draft)),
      ],
      guidance: session.guidance
        ? {
            ...session.guidance,
            chamber_reply: composePromptReply(prompt, session.draft),
          }
        : session.guidance,
      updated_at: nowIso(),
    };
  }
  return normalizeSetupSession(result.payload, result.candidate, session.draft);
}

export async function sendSetupAudio(session: SetupSessionState, audio: Blob, mimeType?: string) {
  if (session.mode !== "live" || !session.endpoint_base) {
    throw new Error("Voice setup needs the live backend. Use text if the chamber is in compatibility mode.");
  }
  const filename = mimeType?.includes("mp4")
    ? "setup-turn.m4a"
    : mimeType?.includes("ogg")
      ? "setup-turn.ogg"
      : "setup-turn.webm";
  const form = new FormData();
  form.append("audio", audio, filename);

  const response = await fetch(`${API_BASE}${session.endpoint_base}/${session.session_id}/voice-turn`, {
    method: "POST",
    body: form,
  });
  if (!response.ok) {
    const body = await response.text();
    throw new Error(body || `request failed with ${response.status}`);
  }
  const payload = (await response.json()) as unknown;
  const root = setupRoot(payload);
  const transcript =
    (isRecord(payload)
      ? asString(payload.transcript_text) ?? asString(payload.transcript) ?? asString(payload.text)
      : undefined) ??
    asString((root.transcript_text ?? root.transcript ?? root.text) as unknown) ??
    "";
  return {
    session: normalizeSetupSession(payload, { endpointBase: session.endpoint_base, path: "", method: "POST" }, session.draft),
    transcript,
  };
}

export async function startSimulationFromSetup(session: SetupSessionState) {
  if (session.mode === "live" && session.endpoint_base) {
    const result = await requestSetupCandidates(setupStartCandidates(session)).catch(() => null);
    if (result) {
      const simulation = normalizeSimulation(result.payload);
      if (simulation) {
        return simulation;
      }
    }
  }
  return createSimulation(setupDraftToCreateRequest(session.draft));
}

export function createSetupRealtimeSession(setupSessionId: string) {
  return request<RealtimeSessionResponse>(`/api/setup-sessions/${setupSessionId}/realtime/session`, {
    method: "POST",
  });
}

export function getSimulation(simulationId: string) {
  return request<unknown>(`/api/simulations/${simulationId}`).then((payload) => {
    const simulation = normalizeSimulation(payload);
    if (!simulation) {
      throw new Error("invalid simulation payload");
    }
    return simulation;
  });
}

export function queuePoll(simulationId: string, question: string, source: "advisor" | "manual" = "manual") {
  return request<unknown>(`/api/simulations/${simulationId}/polls/queue`, {
    method: "POST",
    body: JSON.stringify({ question, source }),
  }).then((payload) => {
    const simulation = normalizeSimulation(payload);
    if (!simulation) {
      throw new Error("invalid simulation payload");
    }
    return simulation;
  });
}

export function runPolls(simulationId: string) {
  return request<RunPollsResponse>(`/api/simulations/${simulationId}/polls/run`, {
    method: "POST",
  });
}

export function resolveStage(simulationId: string, payload: ResolveStageRequest) {
  return request<unknown>(`/api/simulations/${simulationId}/stage/resolve`, {
    method: "POST",
    body: JSON.stringify(payload),
  }).then((responsePayload) => {
    const simulation = normalizeSimulation(responsePayload);
    if (!simulation) {
      throw new Error("invalid simulation payload");
    }
    return simulation;
  });
}

export function createRealtimeSession(
  simulationId: string,
  role: RealtimeRole,
  citizenId?: string,
  advisorMode: AdvisorMode = "solo",
  auditoriumMode: AuditoriumMode = "debate",
  autoResponse?: boolean,
) {
  return request<RealtimeSessionResponse>(`/api/simulations/${simulationId}/realtime/session`, {
    method: "POST",
    body: JSON.stringify({
      role,
      citizen_id: citizenId,
      advisor_mode: role === "advisor" ? advisorMode : "solo",
      auditorium_mode: role === "debate" ? auditoriumMode : "debate",
      auto_response: autoResponse ?? null,
    }),
  });
}

export function callRealtimeTool(
  simulationId: string,
  role: RealtimeRole,
  toolName: string,
  payload: Record<string, unknown>,
) {
  return request<RealtimeToolResult>(`/api/simulations/${simulationId}/realtime/${role}/tools/${toolName}`, {
    method: "POST",
    body: JSON.stringify(payload),
  }).then((result) => {
    const simulation = normalizeSimulation(result.data?.simulation);
    if (!simulation) {
      return result;
    }
    return {
      ...result,
      data: {
        ...result.data,
        simulation,
      },
    };
  });
}

export function syncConversation(
  simulationId: string,
  role: RealtimeRole,
  turns: Array<{
    speaker: "user" | "assistant" | "system";
    speaker_name?: string;
    speaker_voice?: string;
    text: string;
    mode: "text" | "voice" | "system";
  }>,
  citizenId?: string,
  advisorMode: AdvisorMode = "solo",
  auditoriumMode: AuditoriumMode = "debate",
  boardNotes?: string[],
) {
  return request<ConversationSyncResponse>(`/api/simulations/${simulationId}/conversation/sync`, {
    method: "POST",
    body: JSON.stringify({
      role,
      citizen_id: citizenId,
      advisor_mode: role === "advisor" ? advisorMode : "solo",
      auditorium_mode: role === "debate" ? auditoriumMode : "debate",
      turns,
      board_notes: role === "advisor" && advisorMode === "council" ? boardNotes ?? null : null,
    }),
  }).then((response) => {
    const simulation = normalizeSimulation(response.simulation);
    if (!simulation) {
      return response;
    }
    return {
      ...response,
      simulation,
    };
  });
}

export function generateCouncilTurn(
  simulationId: string,
  text: string,
  mode: "text" | "voice",
  continueDialogue = false,
  preferredSpeaker = "",
  avoidSpeaker = "",
  provisionalTurns: Array<{
    speaker: "user" | "assistant" | "system";
    speaker_name?: string;
    speaker_voice?: string;
    text: string;
    mode: "text" | "voice" | "system";
  }> = [],
  boardNotes: string[] = [],
  signal?: AbortSignal,
) {
  return request<CouncilTurnResponse>(`/api/simulations/${simulationId}/advisor/council-turn`, {
    method: "POST",
    body: JSON.stringify({
      text,
      mode,
      continue_dialogue: continueDialogue,
      preferred_speaker: preferredSpeaker,
      avoid_speaker: avoidSpeaker,
      provisional_turns: provisionalTurns,
      provisional_board_notes: boardNotes,
    }),
    signal,
  }).then((response) => {
    const simulation = normalizeSimulation(response.simulation);
    if (!simulation) {
      return response;
    }
    return {
      ...response,
      simulation,
    };
  });
}

export function generateTownHallQuestion(
  simulationId: string,
  citizenId?: string,
  mode: "text" | "voice" = "voice",
) {
  return request<TownHallQuestionResponse>(`/api/simulations/${simulationId}/debate/town-hall-question`, {
    method: "POST",
    body: JSON.stringify({ citizen_id: citizenId, mode }),
  }).then((response) => {
    const simulation = normalizeSimulation(response.simulation);
    if (!simulation) {
      return response;
    }
    return {
      ...response,
      simulation,
    };
  });
}

export function generateTownHallOpponentReply(
  simulationId: string,
  citizenId?: string,
  questionText = "",
  mode: "text" | "voice" = "voice",
) {
  return request<TownHallOpponentReplyResponse>(`/api/simulations/${simulationId}/debate/town-hall-opponent-reply`, {
    method: "POST",
    body: JSON.stringify({
      citizen_id: citizenId,
      question_text: questionText,
      mode,
    }),
  }).then((response) => {
    const simulation = normalizeSimulation(response.simulation);
    if (!simulation) {
      return response;
    }
    return {
      ...response,
      simulation,
    };
  });
}

export async function synthesizeSpeech(text: string, voice: string, signal?: AbortSignal): Promise<Blob> {
  const response = await fetch(`${API_BASE}/api/audio/speech`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ text, voice }),
    signal,
  });
  if (!response.ok) {
    const body = await response.text();
    throw new Error(body || `speech request failed with ${response.status}`);
  }
  return await response.blob();
}

export function speechStreamUrl(text: string, voice: string) {
  const url = new URL(`${API_BASE}/api/audio/speech`);
  url.searchParams.set("text", text);
  url.searchParams.set("voice", voice);
  return url.toString();
}

export function toAbsoluteAssetUrl(assetUrl?: string | null): string | undefined {
  if (!assetUrl) {
    return undefined;
  }
  if (assetUrl.startsWith("http")) {
    return assetUrl;
  }
  return `${API_BASE}${assetUrl}`;
}
