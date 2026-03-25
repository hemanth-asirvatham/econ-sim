import { useEffect, useEffectEvent, useRef, useState } from "react";
import { callRealtimeTool, createRealtimeSession, generateCouncilTurn, syncConversation, synthesizeSpeech } from "../lib/api";
import { COUNCIL_ADVISORS, splitCouncilLines, type CouncilTurnContext } from "../lib/council";
import { makeRealtimeTurnDetection, REALTIME_TURN_DETECTION } from "../lib/realtimeConfig";
import type { AdvisorMode, AuditoriumMode, ConversationTurn, RealtimeRole, RoomName, ScenePresence, SessionStatus, SimulationState } from "../types";

interface UseRealtimeSessionOptions {
  simulationId?: string;
  role: RealtimeRole;
  citizenId?: string;
  advisorMode?: AdvisorMode;
  auditoriumMode?: AuditoriumMode;
  autoResponse?: boolean;
  councilContext?: CouncilTurnContext;
  initialTurns: ConversationTurn[];
  onSimulationSync?: (simulation: SimulationState) => void;
  onCouncilFloorChange?: (floor: {
    lead: string;
    urgencies: Record<string, number>;
    contrast: string[];
    reason?: string;
  } | null) => void;
  onModeCommand?: (command: {
    room?: RoomName;
    advisorMode?: AdvisorMode;
    auditoriumMode?: AuditoriumMode;
    citizenName?: string;
  }) => Promise<boolean> | boolean;
}

const EMPTY_PRESENCE: ScenePresence = {
  status: "idle",
  liveMode: "text",
  muted: false,
  playerActivity: "idle",
  counterpartActivity: "idle",
  voicePhase: "idle",
};

function makeId(prefix: string) {
  return `${prefix}-${Math.random().toString(36).slice(2, 10)}`;
}

function nowIso() {
  return new Date().toISOString();
}

function extractRealtimeText(output: unknown): string {
  if (!Array.isArray(output)) {
    return "";
  }
  return sanitizeRealtimeText(output
    .flatMap((item) => {
      const record = item as Record<string, unknown>;
      if (record.type === "function_call") {
        return [];
      }
      const content = Array.isArray(record.content) ? record.content : [];
      return content.map((entry) => {
        const part = entry as Record<string, unknown>;
        return String(part.transcript ?? part.text ?? "");
      });
    })
    .join(" ")
    .trim());
}

function sanitizeRealtimeText(text: string): string {
  return text
    .replace(/<\|[^|>]+?\|>/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

function compactPollSummaryForRealtime(summary: unknown) {
  const record = summary as Record<string, unknown>;
  const shares = record?.shares && typeof record.shares === "object" ? record.shares as Record<string, number> : {};
  const topAnswers = Object.entries(shares)
    .sort((left, right) => right[1] - left[1])
    .slice(0, 2)
    .map(([label, share]) => `${label} (${Math.round(Number(share) * 100)}%)`);
  return {
    question: String(record.question ?? ""),
    topline: topAnswers.join("; "),
    sample_reasons: Array.isArray(record.sample_reasons)
      ? record.sample_reasons.map((entry) => String(entry)).slice(0, 2)
      : [],
  };
}

function compactRealtimeToolOutput(payload: unknown) {
  if (!payload || typeof payload !== "object") {
    return payload;
  }
  const record = payload as Record<string, unknown>;
  const { simulation: _simulation, ...rest } = record;
  const compact: Record<string, unknown> = { ...rest };

  if (Array.isArray(rest.poll_summaries)) {
    compact.poll_summaries = rest.poll_summaries.slice(0, 3).map((summary) => compactPollSummaryForRealtime(summary));
  }
  if (rest.summary && typeof rest.summary === "object") {
    compact.summary = compactPollSummaryForRealtime(rest.summary);
  }
  if (Array.isArray(rest.policy_notes)) {
    compact.policy_notes = rest.policy_notes.map((entry) => String(entry)).slice(0, 4);
  }
  if (Array.isArray(rest.queued_questions)) {
    compact.queued_questions = rest.queued_questions.map((entry) => String(entry)).slice(0, 4);
  }
  if (Array.isArray(rest.tracking)) {
    compact.tracking = rest.tracking
      .map((entry) => {
        const item = entry as Record<string, unknown>;
        return {
          label: String(item.label ?? ""),
          display: String(item.display ?? ""),
        };
      })
      .slice(0, 6);
  }
  if (Array.isArray(rest.recommendations)) {
    compact.recommendations = rest.recommendations
      .map((entry) => {
        const item = entry as Record<string, unknown>;
        return {
          display_name: String(item.display_name ?? ""),
          role: String(item.role ?? ""),
          reason: String(item.reason ?? ""),
        };
      })
      .slice(0, 4);
  }
  if (Array.isArray(rest.citizens)) {
    compact.citizens = rest.citizens
      .map((entry) => {
        const item = entry as Record<string, unknown>;
        return {
          display_name: String(item.display_name ?? ""),
          role: String(item.role ?? ""),
          region: String(item.region ?? ""),
        };
      })
      .slice(0, 6);
  }
  return compact;
}

type ResponseDeliveryMode = "audio" | "text";
type LocalTurnInput = Pick<ConversationTurn, "speaker" | "speaker_name" | "speaker_voice" | "text" | "mode">;
const COUNCIL_MAX_CONTINUATION_MS = 16000;
const COUNCIL_PLAYER_URGENCY_YIELD = 8;
const COUNCIL_MAX_AUTO_CONTINUATION_BEATS = 1;
const COUNCIL_MAX_FIGHT_CONTINUATION_BEATS = 2;
const COUNCIL_REPEAT_REPLAN_PROMPT =
  "The council is starting to repeat itself. Let the next advisor add one genuinely new mechanism, objection, or direct question, or yield clearly to the president now.";

function councilVoiceForSpeaker(speaker?: string | null) {
  const normalized = (speaker ?? "").trim().toLowerCase();
  const advisor = COUNCIL_ADVISORS.find((entry) => entry.name.toLowerCase() === normalized);
  return advisor?.voice ?? "cedar";
}

function councilTurnSignature(turns: LocalTurnInput[]) {
  return turns
    .map((turn) => `${(turn.speaker_name ?? turn.speaker ?? "").trim().toLowerCase()}:${sanitizeRealtimeText(turn.text).toLowerCase()}`)
    .filter(Boolean)
    .join(" | ");
}

function titleCaseCommandValue(text: string) {
  return text
    .split(/\s+/)
    .filter(Boolean)
    .map((part) => part.slice(0, 1).toUpperCase() + part.slice(1))
    .join(" ");
}

function interpretBareModeCommand(normalized: string): {
  room?: RoomName;
  advisorMode?: AdvisorMode;
  auditoriumMode?: AuditoriumMode;
} | null {
  const trimmed = normalized
    .replace(/^(?:please\s+|let's\s+|lets\s+)/, "")
    .replace(/\s+please$/, "")
    .trim();
  if (!trimmed) {
    return null;
  }
  if (/^(?:town hall|town hall floor)$/.test(trimmed)) {
    return { room: "debate", auditoriumMode: "town_hall" };
  }
  if (/^(?:debate|debate stage|auditorium|podium)$/.test(trimmed)) {
    return { room: "debate", auditoriumMode: "debate" };
  }
  if (/^(?:multi-advisor|advisor table|council table|council room)$/.test(trimmed)) {
    return { room: "advisor", advisorMode: "council" };
  }
  if (/^(?:single advisor|chief advisor|chief of staff)$/.test(trimmed)) {
    return { room: "advisor", advisorMode: "solo" };
  }
  if (/^(?:street|citizens|people)$/.test(trimmed)) {
    return { room: "citizens" };
  }
  if (/^(?:briefing|documentary|intro)$/.test(trimmed)) {
    return { room: "briefing" };
  }
  if (/^(?:advisor|war room)$/.test(trimmed)) {
    return { room: "advisor" };
  }
  return null;
}

function interpretModeCommand(text: string): {
  room?: RoomName;
  advisorMode?: AdvisorMode;
  auditoriumMode?: AuditoriumMode;
  citizenName?: string;
} | null {
  const raw = text.replace(/\s+/g, " ").trim();
  if (!raw) {
    return null;
  }
  const normalized = raw.toLowerCase();
  const bareCommand = interpretBareModeCommand(normalized);
  if (bareCommand) {
    return bareCommand;
  }
  if (!/\b(go to|take me to|bring me to|move me to|return to|back to|head to|switch to|let's go to|i want to go to|i'm ready to go to|i am ready to go to|talk to|speak to|speak with)\b/.test(normalized)) {
    return null;
  }
  if (normalized.includes("town hall")) {
    return { room: "debate", auditoriumMode: "town_hall" };
  }
  if (normalized.includes("debate") || normalized.includes("auditorium") || normalized.includes("podium")) {
    return { room: "debate", auditoriumMode: "debate" };
  }
  if (normalized.includes("multi-advisor") || normalized.includes("council table") || normalized.includes("council room") || normalized.includes("advisor table")) {
    return { room: "advisor", advisorMode: "council" };
  }
  if (normalized.includes("single advisor") || normalized.includes("chief advisor") || normalized.includes("chief of staff")) {
    return { room: "advisor", advisorMode: "solo" };
  }
  if (normalized.includes("street") || normalized.includes("citizens") || normalized.includes("people")) {
    return { room: "citizens" };
  }
  if (normalized.includes("briefing") || normalized.includes("documentary") || normalized.includes("intro")) {
    return { room: "briefing" };
  }
  if (normalized.includes("advisor") || normalized.includes("war room")) {
    return { room: "advisor" };
  }
  const citizenMatch = raw.match(/(?:talk to|speak to|speak with)\s+([A-Za-z][A-Za-z' -]{1,40})/i);
  if (!citizenMatch) {
    return null;
  }
  const citizenName = citizenMatch[1]
    .replace(/\b(?:out there|on the street|in the street|nearby)\b/gi, "")
    .trim();
  if (!citizenName || /\b(?:street|citizen|people|advisor|debate|town hall|briefing)\b/i.test(citizenName)) {
    return null;
  }
  return {
    room: "citizens",
    citizenName: titleCaseCommandValue(citizenName),
  };
}

function mergeConversationTurns(current: ConversationTurn[], incoming: ConversationTurn[]) {
  const merged = new Map<string, ConversationTurn>();
  for (const turn of [...current, ...incoming]) {
    const existing = merged.get(turn.id);
    merged.set(turn.id, existing ? { ...existing, ...turn } : turn);
  }
  return [...merged.values()]
    .sort((left, right) => Date.parse(left.created_at) - Date.parse(right.created_at))
    .slice(-48);
}

function requestsCouncilFight(text: string) {
  const normalized = text.toLowerCase();
  return [
    "let the room argue it out",
    "let them argue it out",
    "let the room debate",
    "let them debate",
    "i want the room to disagree",
    "i want the room to really disagree",
    "argue about",
    "fight it out",
    "debate among yourselves",
    "full council",
  ].some((phrase) => normalized.includes(phrase));
}

export function useRealtimeSession({
  simulationId,
  role,
  citizenId,
  advisorMode = "solo",
  auditoriumMode = "debate",
  autoResponse = true,
  councilContext,
  initialTurns,
  onSimulationSync,
  onCouncilFloorChange,
  onModeCommand,
}: UseRealtimeSessionOptions) {
  const [status, setStatus] = useState<SessionStatus>("idle");
  const [error, setError] = useState<string | null>(null);
  const [events, setEvents] = useState<ConversationTurn[]>(initialTurns);
  const [muted, setMuted] = useState(false);
  const [liveMode, setLiveMode] = useState<"text" | "voice">("text");
  const [presence, setPresence] = useState<ScenePresence>(EMPTY_PRESENCE);
  const [assistantSpeaking, setAssistantSpeaking] = useState(false);
  const [recordingVoiceTurn, setRecordingVoiceTurn] = useState(false);
  const [awaitingVoiceReply, setAwaitingVoiceReply] = useState(false);
  const councilMode = role === "advisor" && advisorMode === "council";
  const sessionScopeKey = [
    simulationId ?? "",
    role,
    citizenId ?? "",
    role === "advisor" ? advisorMode : "-",
    role === "debate" ? auditoriumMode : "-",
    role === "debate" ? String(Boolean(autoResponse)) : "-",
  ].join("|");
  const statusRef = useRef<SessionStatus>("idle");
  statusRef.current = status;
  const connectionRef = useRef<RTCPeerConnection | null>(null);
  const dataChannelRef = useRef<RTCDataChannel | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const inputSenderRef = useRef<RTCRtpSender | null>(null);
  const remoteAudioRef = useRef<HTMLAudioElement | null>(null);
  const syntheticCleanupRef = useRef<(() => void) | null>(null);
  const pendingConnectionRef = useRef<RTCPeerConnection | null>(null);
  const pendingDataChannelRef = useRef<RTCDataChannel | null>(null);
  const pendingStreamRef = useRef<MediaStream | null>(null);
  const pendingRemoteAudioRef = useRef<HTMLAudioElement | null>(null);
  const pendingSyntheticCleanupRef = useRef<(() => void) | null>(null);
  const connectGenerationRef = useRef(0);
  const assistantSpeakingRef = useRef(false);
  const audioOutputPlayingRef = useRef(false);
  const disconnectGraceTimerRef = useRef<number | null>(null);
  const toolFollowupFlushTimerRef = useRef<number | null>(null);
  const responseTextRef = useRef<Record<string, string>>({});
  const responseAudioTranscriptRef = useRef<Record<string, string>>({});
  const responseEpochRef = useRef<Record<string, number>>({});
  const responseDeliveryModeRef = useRef<Record<string, ResponseDeliveryMode>>({});
  const pendingResponseDeliveryModeRef = useRef<ResponseDeliveryMode>("text");
  const completedTextResponseIdsRef = useRef<Set<string>>(new Set());
  const ignoredResponseIdsRef = useRef<Set<string>>(new Set());
  const handledToolCallIdsRef = useRef<Set<string>>(new Set());
  const dropPendingVoiceResponsesRef = useRef(false);
  const voiceEpochRef = useRef(0);
  const activeInputEpochRef = useRef<number | null>(null);
  const awaitFreshInputRef = useRef(false);
  const mutedRef = useRef(false);
  const voiceToggleInFlightRef = useRef(false);
  const connectionRequestedRef = useRef(false);
  const responseInFlightRef = useRef(false);
  const pendingToolFollowupsRef = useRef<Array<{ instructions?: string; textOnly: boolean }>>([]);
  const councilLeadRef = useRef<string | null>(null);
  const councilLoopGenerationRef = useRef(0);
  const councilSpeechEpochRef = useRef(0);
  const councilSpeechAbortRef = useRef<AbortController | null>(null);
  const councilSpeechAudioRef = useRef<HTMLAudioElement | null>(null);
  const councilSpeechUrlsRef = useRef<string[]>([]);
  const liveModeRef = useRef<"text" | "voice">("text");

  liveModeRef.current = liveMode;

  function nextVoiceEpoch() {
    voiceEpochRef.current += 1;
    return voiceEpochRef.current;
  }

  function clearDisconnectGraceTimer() {
    if (disconnectGraceTimerRef.current) {
      window.clearTimeout(disconnectGraceTimerRef.current);
      disconnectGraceTimerRef.current = null;
    }
  }

  function clearToolFollowupFlushTimer() {
    if (toolFollowupFlushTimerRef.current) {
      window.clearTimeout(toolFollowupFlushTimerRef.current);
      toolFollowupFlushTimerRef.current = null;
    }
  }

  function nextCouncilLoopGeneration() {
    councilLoopGenerationRef.current += 1;
    return councilLoopGenerationRef.current;
  }

  async function waitForConnectedStatus(timeoutMs = 6000) {
    const started = Date.now();
    while (Date.now() - started < timeoutMs) {
      if (statusRef.current === "connected") {
        return true;
      }
      await new Promise((resolve) => window.setTimeout(resolve, 50));
    }
    return statusRef.current === "connected";
  }

  const syncInputTrackState = useEffectEvent((nextMuted = mutedRef.current) => {
    const enabled = !nextMuted;
    for (const stream of [streamRef.current, pendingStreamRef.current]) {
      stream?.getAudioTracks().forEach((track) => {
        track.enabled = enabled;
      });
    }
  });

  const syncRemoteAudioState = useEffectEvent((nextMuted: boolean, pause = false) => {
    for (const audioElement of [remoteAudioRef.current, pendingRemoteAudioRef.current]) {
      if (!audioElement) {
        continue;
      }
      audioElement.muted = councilMode || nextMuted;
      if (pause) {
        audioElement.pause();
      }
    }
  });

  const stopCouncilSpeechPlayback = useEffectEvent(() => {
    councilSpeechEpochRef.current += 1;
    councilSpeechAbortRef.current?.abort();
    councilSpeechAbortRef.current = null;
    const audioElement = councilSpeechAudioRef.current;
    if (audioElement) {
      audioElement.pause();
      audioElement.src = "";
      audioElement.removeAttribute("src");
      audioElement.load();
    }
    for (const url of councilSpeechUrlsRef.current) {
      URL.revokeObjectURL(url);
    }
    councilSpeechUrlsRef.current = [];
    releaseAssistantSpeaking();
  });

  const invalidateCouncilLoop = useEffectEvent(() => {
    nextCouncilLoopGeneration();
    responseInFlightRef.current = false;
    setAwaitingVoiceReply(false);
    councilLeadRef.current = null;
    onCouncilFloorChange?.(null);
    stopCouncilSpeechPlayback();
  });

  const playCouncilSpeechTurns = useEffectEvent(async (
    turns: LocalTurnInput[],
    onTurnSpoken?: (turn: LocalTurnInput) => Promise<void> | void,
  ): Promise<LocalTurnInput[]> => {
    if (!councilMode || mutedRef.current) {
      return [] as LocalTurnInput[];
    }
    const lines = turns
      .map((turn) => ({
        turn,
        speaker: turn.speaker_name ?? councilLeadRef.current ?? "Rowan",
        text: sanitizeRealtimeText(turn.text),
      }))
      .filter((line) => line.text);
    if (lines.length === 0) {
      return [] as LocalTurnInput[];
    }

    stopCouncilSpeechPlayback();
    const epoch = councilSpeechEpochRef.current;
    const abortController = new AbortController();
    councilSpeechAbortRef.current = abortController;
    const audioElement = councilSpeechAudioRef.current ?? document.createElement("audio");
    if (!councilSpeechAudioRef.current) {
      audioElement.autoplay = true;
      audioElement.setAttribute("playsinline", "true");
      audioElement.volume = 0.94;
      audioElement.style.display = "none";
      document.body.appendChild(audioElement);
      councilSpeechAudioRef.current = audioElement;
    }

    try {
      markAssistantSpeaking();
      const synthesizeLine = async (lineText: string, speaker?: string | null) => {
        try {
          return await synthesizeSpeech(lineText, councilVoiceForSpeaker(speaker), abortController.signal);
        } catch (caught) {
          if (caught instanceof DOMException && caught.name === "AbortError") {
            return null;
          }
          throw caught;
        }
      };
      const spokenTurns: LocalTurnInput[] = [];
      const [firstLine, ...remainingLines] = lines;
      const remainingPromises = remainingLines.map((line) => synthesizeLine(line.text, line.speaker));
      const firstBlob = await synthesizeLine(firstLine.text, firstLine.speaker);
      if (!firstBlob) {
        return spokenTurns;
      }
      const blobs = [firstBlob, ...remainingPromises];
      for (let index = 0; index < blobs.length; index += 1) {
        const blobOrPromise = blobs[index];
        const currentLine = lines[index];
        if (
          abortController.signal.aborted ||
          mutedRef.current ||
          councilSpeechEpochRef.current !== epoch
        ) {
          return spokenTurns;
        }
        const blob = await blobOrPromise;
        if (!blob) {
          continue;
        }
        const url = URL.createObjectURL(blob);
        councilSpeechUrlsRef.current.push(url);
        audioElement.src = url;
        await audioElement.play();
        await new Promise<void>((resolve) => {
          const done = () => {
            audioElement.removeEventListener("ended", done);
            audioElement.removeEventListener("error", done);
            resolve();
          };
          audioElement.addEventListener("ended", done, { once: true });
          audioElement.addEventListener("error", done, { once: true });
        });
        spokenTurns.push(currentLine.turn);
        await onTurnSpoken?.(currentLine.turn);
      }
      return spokenTurns;
    } catch (caught) {
      if (!(caught instanceof DOMException && caught.name === "AbortError")) {
        const message = caught instanceof Error ? caught.message : "Council speech playback failed";
        setError(message);
      }
      return [];
    } finally {
      if (councilSpeechAbortRef.current === abortController) {
        councilSpeechAbortRef.current = null;
      }
      if (councilSpeechEpochRef.current === epoch) {
        releaseAssistantSpeaking();
      }
      for (const url of councilSpeechUrlsRef.current) {
        URL.revokeObjectURL(url);
      }
      councilSpeechUrlsRef.current = [];
    }
  });

  const appendLocalTurn = useEffectEvent((turn: LocalTurnInput) => {
    const trimmed = sanitizeRealtimeText(turn.text);
    if (!trimmed) {
      return;
    }
    setEvents((current) => [
      ...current.slice(-47),
      {
        id: makeId("evt"),
        speaker: turn.speaker,
        speaker_name: turn.speaker_name,
        speaker_voice: turn.speaker_voice,
        text: trimmed,
        mode: turn.mode,
        created_at: nowIso(),
      },
    ]);
  });

  const clearPendingRealtimeTransportRefs = useEffectEvent(() => {
    pendingConnectionRef.current = null;
    pendingDataChannelRef.current = null;
    pendingStreamRef.current = null;
    pendingRemoteAudioRef.current = null;
    pendingSyntheticCleanupRef.current = null;
  });

  const persistTurns = useEffectEvent(async (
    turns: LocalTurnInput[],
    options?: { boardNotes?: string[] | null },
  ) => {
    const cleanedTurns = turns
      .map((turn) => ({ ...turn, text: sanitizeRealtimeText(turn.text) }))
      .filter((turn) => turn.text);
    const boardNotes = options?.boardNotes?.filter((entry) => entry.trim()) ?? [];
    if (!simulationId || (cleanedTurns.length === 0 && boardNotes.length === 0)) {
      return;
    }
    const response = await syncConversation(
      simulationId,
      role,
      cleanedTurns,
      citizenId,
      advisorMode,
      auditoriumMode,
      boardNotes,
    );
    if (response.simulation) {
      onSimulationSync?.(response.simulation);
    }
  });

  const buildAssistantTurns = useEffectEvent((text: string, mode: ConversationTurn["mode"], deliveryMode: ResponseDeliveryMode): LocalTurnInput[] => {
    const transcriptText = sanitizeRealtimeText(text);
    if (!councilMode) {
      return [{ speaker: "assistant", text: transcriptText, mode }];
    }
    const councilLines = splitCouncilLines(transcriptText)
      .map((line) => ({
        speaker: "assistant" as const,
        speaker_name: line.speaker ?? councilLeadRef.current ?? undefined,
        speaker_voice: councilVoiceForSpeaker(line.speaker ?? councilLeadRef.current),
        text: line.text,
        mode,
      }))
      .filter((line) => sanitizeRealtimeText(line.text));
    return councilLines.length > 0 ? councilLines : [{ speaker: "assistant", text: transcriptText, mode }];
  });

  const sendEvent = useEffectEvent((payload: Record<string, unknown>) => {
    const channel = dataChannelRef.current;
    if (channel?.readyState === "open") {
      channel.send(JSON.stringify(payload));
    }
  });

  const injectContextTurn = useEffectEvent(async (text: string) => {
    const trimmed = sanitizeRealtimeText(text);
    if (!trimmed) {
      return false;
    }
    if (statusRef.current !== "connected") {
      try {
        await connectInternal(false);
        const connected = await waitForConnectedStatus();
        if (!connected) {
          return false;
        }
      } catch {
        return false;
      }
    }
    sendEvent({
      type: "conversation.item.create",
      item: {
        type: "message",
        role: "user",
        content: [{ type: "input_text", text: trimmed }],
      },
    });
    return true;
  });

  const requestAssistantReply = useEffectEvent(async (instructions?: string) => {
    if (councilMode) {
      return false;
    }
    if (statusRef.current !== "connected") {
      try {
        await connectInternal(false);
        const connected = await waitForConnectedStatus();
        if (!connected) {
          return false;
        }
      } catch {
        return false;
      }
    }
    const deliveryMode: ResponseDeliveryMode = liveModeRef.current === "voice" ? "audio" : "text";
    responseInFlightRef.current = true;
    pendingResponseDeliveryModeRef.current = deliveryMode;
    if (deliveryMode !== "text") {
      activeInputEpochRef.current = voiceEpochRef.current;
      awaitFreshInputRef.current = false;
      dropPendingVoiceResponsesRef.current = false;
      setAwaitingVoiceReply(true);
    }
    sendEvent({
      type: "response.create",
      response: {
        output_modalities: deliveryMode === "audio" ? ["audio"] : ["text"],
        ...(instructions ? { instructions } : {}),
      },
    });
    return true;
  });

  const addTurn = useEffectEvent(async (turn: LocalTurnInput) => {
    appendLocalTurn(turn);
    await persistTurns([turn]);
  });

  const handleModeCommand = useEffectEvent(async (text: string) => {
    const command = interpretModeCommand(text);
    if (!command) {
      return false;
    }
    if (onModeCommand) {
      return Boolean(await onModeCommand(command));
    }
    if (!simulationId) {
      return false;
    }
    const result = command.citizenName
      ? await callRealtimeTool(simulationId, role, "focus_citizen_by_name", { citizen_name: command.citizenName })
      : command.room
        ? await callRealtimeTool(simulationId, role, "move_room_focus", { room: command.room })
        : null;
    const maybeSimulation = result?.data?.simulation as SimulationState | undefined;
    if (maybeSimulation?.simulation_id) {
      onSimulationSync?.(maybeSimulation);
    }
    return Boolean(result);
  });

  const runCouncilTurn = useEffectEvent(async (text: string, requestedMode: "text" | "voice") => {
    const trimmed = sanitizeRealtimeText(text);
    if (!trimmed || !simulationId || responseInFlightRef.current) {
      return;
    }
    const explicitFightRequest = requestsCouncilFight(trimmed);
    const loopGeneration = nextCouncilLoopGeneration();
    const userTurn: LocalTurnInput = { speaker: "user", text: trimmed, mode: requestedMode };
    appendLocalTurn(userTurn);
    void persistTurns([userTurn]).catch((caught) => {
      const message = caught instanceof Error ? caught.message : "Failed to sync council turn";
      setError(message);
    });
    setError(null);
    responseInFlightRef.current = true;
    if (requestedMode === "voice") {
      setAwaitingVoiceReply(true);
      setRecordingVoiceTurn(false);
    }
    try {
      let continueDialogue = false;
      let nextText = trimmed;
      const loopStartedAt = Date.now();
      let lastAssistantSignature = "";
      let repeatedAssistantSignatureCount = 0;
      let continuationBeatCount = 0;
      const maxContinuationBeats = explicitFightRequest
        ? COUNCIL_MAX_FIGHT_CONTINUATION_BEATS
        : COUNCIL_MAX_AUTO_CONTINUATION_BEATS;
      while (true) {
        if (councilLoopGenerationRef.current !== loopGeneration) {
          return;
        }
        responseInFlightRef.current = true;
        const response = await generateCouncilTurn(simulationId, nextText, requestedMode, continueDialogue);
        if (councilLoopGenerationRef.current !== loopGeneration) {
          return;
        }
        onCouncilFloorChange?.({
          lead: response.lead,
          urgencies: response.urgencies,
          contrast: response.contrast,
          reason: response.reason ?? undefined,
        });
        councilLeadRef.current = response.lead;
        const shouldYieldToPlayer =
          response.yield_after_turn || response.player_proxy_urgency >= COUNCIL_PLAYER_URGENCY_YIELD;
        const assistantTurns = response.turns
          .map((turn) => ({
            speaker: turn.speaker,
            speaker_name: turn.speaker_name,
            speaker_voice: turn.speaker_voice,
            text: turn.text,
            mode: turn.mode,
          }))
          .filter((turn) => sanitizeRealtimeText(turn.text));
        const assistantSignature = councilTurnSignature(assistantTurns);
        if (assistantSignature && assistantSignature === lastAssistantSignature) {
          repeatedAssistantSignatureCount += 1;
        } else {
          repeatedAssistantSignatureCount = 0;
          lastAssistantSignature = assistantSignature;
        }
        responseInFlightRef.current = false;
        setAwaitingVoiceReply(false);

        if (assistantTurns.length === 0) {
          if (response.board_notes.length > 0) {
            await persistTurns([], { boardNotes: response.board_notes });
          }
          return;
        }

        if (requestedMode === "voice" && !mutedRef.current) {
          assistantTurns.forEach((turn) => appendLocalTurn(turn));
          const persistPromise = persistTurns(
            assistantTurns,
            response.board_notes.length > 0 ? { boardNotes: response.board_notes } : undefined,
          );
          await playCouncilSpeechTurns(assistantTurns);
          if (councilLoopGenerationRef.current !== loopGeneration) {
            return;
          }
          await persistPromise;
        } else {
          assistantTurns.forEach((turn) => appendLocalTurn(turn));
          await persistTurns(assistantTurns, { boardNotes: response.board_notes });
        }

        const directQuestionToPlayer = assistantTurns.some((turn) => /\?\s*$/.test(sanitizeRealtimeText(turn.text)));
        const sharpContrast = response.contrast.some((name) => {
          const leadUrgency = response.urgencies[response.lead] ?? 0;
          const contrastUrgency = response.urgencies[name] ?? 0;
          return contrastUrgency >= Math.max(6, leadUrgency - 1);
        });
        const keepAutoDebating =
          explicitFightRequest ||
          sharpContrast ||
          assistantTurns.length > 1;
        const shouldForceFollowupBeat = explicitFightRequest && continuationBeatCount === 0 && !directQuestionToPlayer;
        if ((shouldYieldToPlayer || !keepAutoDebating) && !shouldForceFollowupBeat) {
          return;
        }
        if (Date.now() - loopStartedAt >= COUNCIL_MAX_CONTINUATION_MS) {
          return;
        }
        if (requestedMode === "voice" && (mutedRef.current || liveModeRef.current !== "voice" || statusRef.current !== "connected")) {
          return;
        }
        if (!shouldForceFollowupBeat && continuationBeatCount >= maxContinuationBeats) {
          return;
        }
        continuationBeatCount += 1;
        continueDialogue = true;
        if (shouldForceFollowupBeat) {
          nextText = "Keep the council argument going from the strongest unresolved objection, mechanism, or political consequence. Do not yield to the president yet unless there is no real second beat.";
        } else if (repeatedAssistantSignatureCount >= 2) {
          if (repeatedAssistantSignatureCount >= 4) {
            return;
          }
          nextText = COUNCIL_REPEAT_REPLAN_PROMPT;
        } else {
          nextText = "";
        }
        if (requestedMode === "voice") {
          setAwaitingVoiceReply(true);
          await new Promise((resolve) => window.setTimeout(resolve, repeatedAssistantSignatureCount >= 2 ? 60 : 10));
        }
      }
    } catch (caught) {
      const message = caught instanceof Error ? caught.message : "Council turn failed";
      setError(message);
    } finally {
      if (councilLoopGenerationRef.current === loopGeneration) {
        responseInFlightRef.current = false;
        setAwaitingVoiceReply(false);
      }
    }
  });

  const updateVoiceTurnDetection = useEffectEvent((paused: boolean) => {
    sendEvent({
      type: "session.update",
      session: {
        audio: {
          input: {
            turn_detection: paused
              ? null
              : councilMode
                ? makeRealtimeTurnDetection(false)
                : REALTIME_TURN_DETECTION,
          },
        },
      },
    });
  });

  const scheduleToolFollowupRescue = useEffectEvent((delayMs = 4800) => {
    clearToolFollowupFlushTimer();
    toolFollowupFlushTimerRef.current = window.setTimeout(() => {
      toolFollowupFlushTimerRef.current = null;
      if (!responseInFlightRef.current || pendingToolFollowupsRef.current.length === 0) {
        return;
      }
      if (assistantSpeakingRef.current || audioOutputPlayingRef.current) {
        scheduleToolFollowupRescue(Math.max(2400, delayMs));
        return;
      }
      responseInFlightRef.current = false;
      flushPendingToolFollowup();
    }, delayMs);
  });

  const disposeRealtimeTransport = useEffectEvent((
    peerConnection: RTCPeerConnection | null,
    dataChannel: RTCDataChannel | null,
    stream: MediaStream | null,
    remoteAudio: HTMLAudioElement | null,
    syntheticCleanup: (() => void) | null,
  ) => {
    if (dataChannel) {
      dataChannel.onmessage = null;
      try {
        dataChannel.close();
      } catch {
        // Ignore double-close cleanup noise.
      }
    }
    if (peerConnection) {
      peerConnection.ontrack = null;
      peerConnection.onconnectionstatechange = null;
      peerConnection.getSenders().forEach((sender) => {
        sender.track?.stop();
        void sender.replaceTrack(null).catch(() => undefined);
      });
      peerConnection.getReceivers().forEach((receiver) => {
        receiver.track?.stop();
      });
      peerConnection.getTransceivers().forEach((transceiver) => {
        try {
          transceiver.stop();
        } catch {
          // Some browsers surface stop errors during shutdown; ignore them.
        }
      });
      try {
        peerConnection.close();
      } catch {
        // Ignore double-close cleanup noise.
      }
    }
    stream?.getTracks().forEach((track) => track.stop());
    syntheticCleanup?.();
    if (remoteAudio) {
      remoteAudio.pause();
      remoteAudio.srcObject = null;
      remoteAudio.removeAttribute("src");
      remoteAudio.load();
      remoteAudio.remove();
    }
  });

  const disposeAllRealtimeTransport = useEffectEvent(() => {
    disposeRealtimeTransport(
      connectionRef.current,
      dataChannelRef.current,
      streamRef.current,
      remoteAudioRef.current,
      syntheticCleanupRef.current,
    );
    if (pendingConnectionRef.current || pendingDataChannelRef.current || pendingStreamRef.current || pendingRemoteAudioRef.current) {
      disposeRealtimeTransport(
        pendingConnectionRef.current,
        pendingDataChannelRef.current,
        pendingStreamRef.current,
        pendingRemoteAudioRef.current,
        pendingSyntheticCleanupRef.current,
      );
    }
    clearPendingRealtimeTransportRefs();
    syntheticCleanupRef.current = null;
    connectionRef.current = null;
    dataChannelRef.current = null;
    streamRef.current = null;
    inputSenderRef.current = null;
    remoteAudioRef.current = null;
  });

  const markAssistantSpeaking = useEffectEvent(() => {
    audioOutputPlayingRef.current = true;
    assistantSpeakingRef.current = true;
    setAssistantSpeaking(true);
    setAwaitingVoiceReply(false);
    setRecordingVoiceTurn(false);
  });

  const releaseAssistantSpeaking = useEffectEvent(() => {
    audioOutputPlayingRef.current = false;
    assistantSpeakingRef.current = false;
    setAssistantSpeaking(false);
  });

  const hardStopRealtime = useEffectEvent(() => {
    nextVoiceEpoch();
    activeInputEpochRef.current = null;
    awaitFreshInputRef.current = true;
    dropPendingVoiceResponsesRef.current = true;
    responseInFlightRef.current = false;
    pendingToolFollowupsRef.current = [];
    clearToolFollowupFlushTimer();
    invalidateCouncilLoop();
    Object.keys(responseEpochRef.current).forEach((responseId) => {
      ignoredResponseIdsRef.current.add(responseId);
    });
    syncInputTrackState(true);
    updateVoiceTurnDetection(true);
    syncRemoteAudioState(true, true);
    sendEvent({ type: "input_audio_buffer.clear" });
    sendEvent({ type: "response.cancel" });
    sendEvent({ type: "output_audio_buffer.clear" });
    releaseAssistantSpeaking();
    setRecordingVoiceTurn(false);
    setAwaitingVoiceReply(false);
  });

  const resumeRealtimeAfterPause = useEffectEvent(() => {
    nextVoiceEpoch();
    activeInputEpochRef.current = null;
    awaitFreshInputRef.current = true;
    dropPendingVoiceResponsesRef.current = true;
    responseInFlightRef.current = false;
    pendingToolFollowupsRef.current = [];
    clearToolFollowupFlushTimer();
    ignoredResponseIdsRef.current.clear();
    invalidateCouncilLoop();
    mutedRef.current = false;
    setMuted(false);
    setRecordingVoiceTurn(false);
    setAwaitingVoiceReply(false);
    releaseAssistantSpeaking();
    syncInputTrackState(false);
    updateVoiceTurnDetection(false);
    syncRemoteAudioState(false, false);
  });

  function toolFollowupInstructions(toolName: string) {
    if (toolName === "update_policy_board") {
      return "Say exactly what changed on the board first, then finish the wider answer.";
    }
    if (toolName === "run_poll_now" || toolName === "run_queued_polls") {
      return "Say the topline that came back from the poll first, then finish the wider answer.";
    }
    if (toolName === "queue_poll_question") {
      return "Confirm what poll question you just queued and whether you want another before running them.";
    }
    if (toolName === "get_world_briefing") {
      return "Keep speaking after the tool result. Give one short takeaway, not a memo.";
    }
    return null;
  }

  const flushPendingToolFollowup = useEffectEvent(() => {
    if (responseInFlightRef.current) {
      return;
    }
    const next = pendingToolFollowupsRef.current.shift();
    if (!next) {
      return;
    }
    responseInFlightRef.current = true;
    if (!next.textOnly) {
      activeInputEpochRef.current = voiceEpochRef.current;
      awaitFreshInputRef.current = false;
      dropPendingVoiceResponsesRef.current = false;
      setAwaitingVoiceReply(true);
    }
    const deliveryMode: ResponseDeliveryMode = next.textOnly ? "text" : "audio";
    pendingResponseDeliveryModeRef.current = deliveryMode;
    sendEvent({
      type: "response.create",
      response: {
        output_modalities: deliveryMode === "audio" ? ["audio"] : ["text"],
        ...(next.instructions ? { instructions: next.instructions } : {}),
      },
    });
  });

  const handleToolCall = useEffectEvent(async (item: Record<string, unknown>) => {
    const generation = connectGenerationRef.current;
    const name = String(item.name ?? "");
    const navigationTool = name === "move_room_focus" || name === "focus_citizen_by_name";
    const callId = String(item.call_id ?? "");
    if (callId && handledToolCallIdsRef.current.has(callId)) {
      return;
    }
    if (callId) {
      handledToolCallIdsRef.current.add(callId);
    }
    const rawArguments = String(item.arguments ?? "{}");
    let parsedArguments: Record<string, unknown> = {};
    try {
      parsedArguments = JSON.parse(rawArguments);
    } catch {
      parsedArguments = {};
    }
    if (!simulationId) {
      return;
    }
    if (navigationTool) {
      disconnect();
    }
    const result = await callRealtimeTool(simulationId, role, name, parsedArguments);
    if (!navigationTool && connectGenerationRef.current !== generation) {
      return;
    }
    const maybeSimulation = result.data?.simulation as SimulationState | undefined;
    if (maybeSimulation?.simulation_id) {
      onSimulationSync?.(maybeSimulation);
    }
    if (navigationTool) {
      return;
    }
    if (connectGenerationRef.current !== generation) {
      return;
    }
    sendEvent({
      type: "conversation.item.create",
      item: {
        type: "function_call_output",
        call_id: callId,
        output: JSON.stringify(compactRealtimeToolOutput(result.data)),
      },
    });
    const followupInstructions = toolFollowupInstructions(name);
    pendingToolFollowupsRef.current.push({
      textOnly: liveModeRef.current === "text",
      instructions: followupInstructions ?? undefined,
    });
    flushPendingToolFollowup();
    if (responseInFlightRef.current && pendingToolFollowupsRef.current.length > 0) {
      scheduleToolFollowupRescue(5200);
    }
  });

  const handleRealtimeEvent = useEffectEvent(async (payload: Record<string, unknown>) => {
    const eventType = String(payload.type ?? "");
    const payloadResponseId = String(payload.response_id ?? "");
    if (councilMode) {
      if (eventType === "input_audio_buffer.speech_started") {
        if (liveModeRef.current !== "voice" || mutedRef.current) {
          return;
        }
        invalidateCouncilLoop();
        activeInputEpochRef.current = voiceEpochRef.current;
        awaitFreshInputRef.current = false;
        dropPendingVoiceResponsesRef.current = false;
        setRecordingVoiceTurn(true);
        setAwaitingVoiceReply(false);
        return;
      }
      if (eventType === "input_audio_buffer.speech_stopped") {
        if (liveModeRef.current !== "voice" || mutedRef.current) {
          return;
        }
        setRecordingVoiceTurn(false);
        setAwaitingVoiceReply(autoResponse);
        return;
      }
      if (eventType === "conversation.interrupted") {
        pendingToolFollowupsRef.current = [];
        clearToolFollowupFlushTimer();
        invalidateCouncilLoop();
        releaseAssistantSpeaking();
        return;
      }
      if (eventType === "conversation.item.input_audio_transcription.completed") {
        if (liveModeRef.current === "voice" && (mutedRef.current || activeInputEpochRef.current !== voiceEpochRef.current)) {
          return;
        }
        const transcript = String(payload.transcript ?? "").trim();
        if (!transcript) {
          return;
        }
        dropPendingVoiceResponsesRef.current = false;
        if (await handleModeCommand(transcript)) {
          return;
        }
        void runCouncilTurn(transcript, "voice");
        return;
      }
      if (eventType.startsWith("response.") || eventType.startsWith("output_audio_buffer.")) {
        return;
      }
    }
    if (payloadResponseId) {
      const responseEpoch = responseEpochRef.current[payloadResponseId];
      if (responseEpoch !== undefined && responseEpoch !== voiceEpochRef.current) {
        if (eventType === "response.done") {
          delete responseTextRef.current[payloadResponseId];
          delete responseAudioTranscriptRef.current[payloadResponseId];
          delete responseEpochRef.current[payloadResponseId];
          delete responseDeliveryModeRef.current[payloadResponseId];
          ignoredResponseIdsRef.current.delete(payloadResponseId);
        }
        return;
      }
    }
    if (payloadResponseId && ignoredResponseIdsRef.current.has(payloadResponseId)) {
      if (eventType === "response.done") {
        ignoredResponseIdsRef.current.delete(payloadResponseId);
        delete responseTextRef.current[payloadResponseId];
        delete responseAudioTranscriptRef.current[payloadResponseId];
        delete responseEpochRef.current[payloadResponseId];
        delete responseDeliveryModeRef.current[payloadResponseId];
      }
      return;
    }
    if (eventType === "response.created") {
      responseInFlightRef.current = true;
      const response = (payload.response as Record<string, unknown> | undefined) ?? {};
      const responseId = String(response.id ?? payloadResponseId ?? "");
      const deliveryMode = pendingResponseDeliveryModeRef.current;
      if (responseId) {
        responseEpochRef.current[responseId] = voiceEpochRef.current;
        responseDeliveryModeRef.current[responseId] = deliveryMode;
      }
      if (deliveryMode === "audio" && liveModeRef.current === "voice") {
        if (
          mutedRef.current ||
          dropPendingVoiceResponsesRef.current ||
          awaitFreshInputRef.current ||
          activeInputEpochRef.current !== voiceEpochRef.current
        ) {
          if (responseId) {
            ignoredResponseIdsRef.current.add(responseId);
          }
          return;
        }
        setAwaitingVoiceReply(true);
      }
      return;
    }
    if (eventType === "input_audio_buffer.speech_started") {
      if (liveModeRef.current !== "voice" || mutedRef.current) {
        return;
      }
      invalidateCouncilLoop();
      activeInputEpochRef.current = voiceEpochRef.current;
      awaitFreshInputRef.current = false;
      dropPendingVoiceResponsesRef.current = false;
      setRecordingVoiceTurn(true);
      setAwaitingVoiceReply(false);
      return;
    }
    if (eventType === "input_audio_buffer.speech_stopped") {
      if (liveModeRef.current !== "voice" || mutedRef.current) {
        return;
      }
      setRecordingVoiceTurn(false);
      setAwaitingVoiceReply(autoResponse);
      return;
    }
    if (eventType === "output_audio_buffer.started" || eventType === "response.output_audio.delta") {
      if (liveModeRef.current === "voice") {
        if (mutedRef.current || dropPendingVoiceResponsesRef.current) {
          return;
        }
        markAssistantSpeaking();
      }
      return;
    }
    if (eventType === "output_audio_buffer.stopped" || eventType === "output_audio_buffer.cleared") {
      if (liveModeRef.current === "voice") {
        releaseAssistantSpeaking();
      }
      return;
    }
    if (eventType === "conversation.interrupted") {
      responseInFlightRef.current = false;
      pendingToolFollowupsRef.current = [];
      clearToolFollowupFlushTimer();
      invalidateCouncilLoop();
      releaseAssistantSpeaking();
      return;
    }
    if (eventType === "conversation.item.input_audio_transcription.completed") {
      if (liveModeRef.current === "voice" && (mutedRef.current || activeInputEpochRef.current !== voiceEpochRef.current)) {
        return;
      }
      const transcript = String(payload.transcript ?? "").trim();
      if (!transcript) {
        return;
      }
      if (await handleModeCommand(transcript)) {
        return;
      }
      if (councilMode) {
        dropPendingVoiceResponsesRef.current = false;
        void runCouncilTurn(transcript, "voice");
        return;
      }
      dropPendingVoiceResponsesRef.current = false;
      appendLocalTurn({ speaker: "user", text: transcript, mode: "voice" });
      void persistTurns([{ speaker: "user", text: transcript, mode: "voice" }]).catch((caught) => {
        const message = caught instanceof Error ? caught.message : "Failed to sync voice turn";
        setError(message);
      });
      return;
    }
    if (eventType === "response.output_text.delta") {
      const responseId = String(payload.response_id ?? "");
      const delta = String(payload.delta ?? "");
      if (!responseId || !delta) {
        return;
      }
      responseTextRef.current[responseId] = `${responseTextRef.current[responseId] ?? ""}${delta}`;
      return;
    }
    if (eventType === "response.output_audio_transcript.delta" || eventType === "response.audio_transcript.delta") {
      const responseId = String(payload.response_id ?? "");
      const delta = String(payload.delta ?? "");
      if (!responseId || !delta || mutedRef.current || dropPendingVoiceResponsesRef.current) {
        return;
      }
      markAssistantSpeaking();
      responseAudioTranscriptRef.current[responseId] = `${responseAudioTranscriptRef.current[responseId] ?? ""}${delta}`;
      return;
    }
    if (eventType === "response.output_audio_transcript.done" || eventType === "response.audio_transcript.done") {
      const responseId = String(payload.response_id ?? "");
      if (!responseId || mutedRef.current || dropPendingVoiceResponsesRef.current) {
        return;
      }
      const finalText = sanitizeRealtimeText(String(payload.transcript ?? responseAudioTranscriptRef.current[responseId] ?? ""));
      if (finalText) {
        responseAudioTranscriptRef.current[responseId] = finalText;
      }
      return;
    }
    if (eventType === "response.output_text.done") {
      const responseId = String(payload.response_id ?? "");
      const finalText = sanitizeRealtimeText(String(payload.text ?? responseTextRef.current[responseId] ?? ""));
      const deliveryMode = responseDeliveryModeRef.current[responseId]
        ?? (liveModeRef.current === "voice" ? "audio" : "text");
      const voiceLikeResponse = deliveryMode !== "text";
      if (voiceLikeResponse && (mutedRef.current || dropPendingVoiceResponsesRef.current)) {
        if (responseId) {
          delete responseTextRef.current[responseId];
          delete responseAudioTranscriptRef.current[responseId];
          delete responseEpochRef.current[responseId];
          delete responseDeliveryModeRef.current[responseId];
        }
        return;
      }
      if (voiceLikeResponse) {
        if (finalText) {
          responseTextRef.current[responseId] = finalText;
        }
        return;
      }
      releaseAssistantSpeaking();
      if (responseId) {
        completedTextResponseIdsRef.current.add(responseId);
        delete responseTextRef.current[responseId];
        delete responseAudioTranscriptRef.current[responseId];
        delete responseEpochRef.current[responseId];
        delete responseDeliveryModeRef.current[responseId];
      }
      if (!finalText) {
        return;
      }
      const assistantTurns = buildAssistantTurns(finalText, "text", deliveryMode);
      assistantTurns.forEach((turn) => appendLocalTurn(turn));
      void persistTurns(assistantTurns).catch((caught) => {
        const message = caught instanceof Error ? caught.message : "Failed to sync assistant text";
        setError(message);
      });
      return;
    }
    if (eventType === "response.output_item.done") {
      const item = (payload.item as Record<string, unknown> | undefined) ?? {};
      const itemType = String(item.type ?? "");
      if (itemType === "function_call") {
        await handleToolCall(item);
      }
      return;
    }
    if (eventType === "response.done") {
      responseInFlightRef.current = false;
      const response = (payload.response as Record<string, unknown> | undefined) ?? {};
      const responseId = String(response.id ?? payloadResponseId ?? "");
      const deliveryMode = responseDeliveryModeRef.current[responseId]
        ?? ((liveModeRef.current === "voice" || Boolean(responseAudioTranscriptRef.current[responseId])) ? "audio" : "text");
      const functionCalls = Array.isArray(response.output)
        ? response.output.filter((entry) => {
          const item = entry as Record<string, unknown>;
          const callId = String(item.call_id ?? "");
          return item.type === "function_call" && (!callId || !handledToolCallIdsRef.current.has(callId));
        })
        : [];
      if (responseId && completedTextResponseIdsRef.current.has(responseId)) {
        completedTextResponseIdsRef.current.delete(responseId);
        delete responseTextRef.current[responseId];
        delete responseAudioTranscriptRef.current[responseId];
        delete responseEpochRef.current[responseId];
        delete responseDeliveryModeRef.current[responseId];
        flushPendingToolFollowup();
        return;
      }
      const responseStatus = String(response.status ?? "completed");
      const wasVoiceResponse = deliveryMode !== "text";
      const text = sanitizeRealtimeText(
        extractRealtimeText(response.output) ||
        String(responseTextRef.current[responseId] ?? responseAudioTranscriptRef.current[responseId] ?? ""),
      );
      if (responseId) {
        delete responseTextRef.current[responseId];
        delete responseAudioTranscriptRef.current[responseId];
        delete responseEpochRef.current[responseId];
        delete responseDeliveryModeRef.current[responseId];
      }
      if (deliveryMode === "audio" && wasVoiceResponse && !audioOutputPlayingRef.current) {
        releaseAssistantSpeaking();
      }
      if (wasVoiceResponse && (mutedRef.current || dropPendingVoiceResponsesRef.current)) {
        flushPendingToolFollowup();
        return;
      }
      if (!text || (responseStatus && responseStatus !== "completed")) {
        for (const entry of functionCalls) {
          await handleToolCall(entry as Record<string, unknown>);
        }
        flushPendingToolFollowup();
        return;
      }
      const assistantTurns = buildAssistantTurns(text, wasVoiceResponse ? "voice" : "text", deliveryMode);
      assistantTurns.forEach((turn) => appendLocalTurn(turn));
      void persistTurns(assistantTurns).catch((caught) => {
        const message = caught instanceof Error ? caught.message : "Failed to sync assistant turn";
        setError(message);
      });
      for (const entry of functionCalls) {
        await handleToolCall(entry as Record<string, unknown>);
      }
      flushPendingToolFollowup();
      return;
    }
    if (eventType === "error") {
      responseInFlightRef.current = false;
      pendingToolFollowupsRef.current = [];
      invalidateCouncilLoop();
      const message = String((payload.error as Record<string, unknown> | undefined)?.message ?? "Realtime session failed");
      setAwaitingVoiceReply(false);
      setRecordingVoiceTurn(false);
      setError(message);
      setStatus("error");
    }
  });

  const disconnect = useEffectEvent(() => {
    voiceToggleInFlightRef.current = false;
    connectionRequestedRef.current = false;
    connectGenerationRef.current += 1;
    clearDisconnectGraceTimer();
    clearToolFollowupFlushTimer();
    hardStopRealtime();
    disposeAllRealtimeTransport();
    audioOutputPlayingRef.current = false;
    assistantSpeakingRef.current = false;
    setAssistantSpeaking(false);
    setRecordingVoiceTurn(false);
    setAwaitingVoiceReply(false);
    mutedRef.current = false;
    setMuted(false);
    liveModeRef.current = "text";
    setLiveMode("text");
    setStatus("idle");
    setPresence(EMPTY_PRESENCE);
    responseTextRef.current = {};
    responseAudioTranscriptRef.current = {};
    responseDeliveryModeRef.current = {};
    pendingResponseDeliveryModeRef.current = "text";
    completedTextResponseIdsRef.current.clear();
    handledToolCallIdsRef.current.clear();
    dropPendingVoiceResponsesRef.current = true;
    activeInputEpochRef.current = null;
    awaitFreshInputRef.current = true;
    responseInFlightRef.current = false;
    pendingToolFollowupsRef.current = [];
    invalidateCouncilLoop();
  });

  const connectInternal = useEffectEvent(async (withAudio: boolean, options?: { silentOpen?: boolean }) => {
    if (!simulationId) {
      throw new Error("simulation is not ready");
    }
    if (status === "connecting") {
      return;
    }
    if (status === "connected" && ((withAudio && liveModeRef.current === "voice") || (!withAudio && liveModeRef.current === "text"))) {
      return;
    }
    if (status === "connected") {
      disconnect();
    }
    if (connectionRef.current || pendingConnectionRef.current || dataChannelRef.current || pendingDataChannelRef.current || remoteAudioRef.current || pendingRemoteAudioRef.current) {
      disposeAllRealtimeTransport();
    }
    const generation = connectGenerationRef.current + 1;
    connectGenerationRef.current = generation;
    connectionRequestedRef.current = true;
    const generationMatches = () => connectGenerationRef.current === generation;
    responseTextRef.current = {};
    responseAudioTranscriptRef.current = {};
    responseEpochRef.current = {};
    responseDeliveryModeRef.current = {};
    pendingResponseDeliveryModeRef.current = "text";
    completedTextResponseIdsRef.current.clear();
    handledToolCallIdsRef.current.clear();
    ignoredResponseIdsRef.current.clear();
    setStatus("connecting");
    liveModeRef.current = withAudio ? "voice" : "text";
    setLiveMode(withAudio ? "voice" : "text");
    setError(null);
    audioOutputPlayingRef.current = false;
    assistantSpeakingRef.current = false;
    nextVoiceEpoch();
    awaitFreshInputRef.current = withAudio;
    setAssistantSpeaking(false);
    setRecordingVoiceTurn(false);
    setAwaitingVoiceReply(false);
    mutedRef.current = false;
    setMuted(false);
    dropPendingVoiceResponsesRef.current = withAudio;
    let localPeerConnection: RTCPeerConnection | null = null;
    let localDataChannel: RTCDataChannel | null = null;
    let localStream: MediaStream | null = null;
    let localAudioElement: HTMLAudioElement | null = null;
    let localSyntheticCleanup: (() => void) | null = null;
    try {
      const session = await createRealtimeSession(simulationId, role, citizenId, advisorMode, auditoriumMode, autoResponse);
      if (!generationMatches()) {
        clearPendingRealtimeTransportRefs();
        return;
      }
      if (session.client_secret === "dummy-client-secret") {
        setStatus("connected");
        liveModeRef.current = withAudio ? "voice" : "text";
        setLiveMode(withAudio ? "voice" : "text");
        if (withAudio) {
          mutedRef.current = false;
          setMuted(false);
        }
        appendLocalTurn({
          speaker: "system",
          text: "Dummy mode is active. Live audio is disabled, but the interaction surface still works.",
          mode: "system",
        });
        return;
      }

      const peerConnection = new RTCPeerConnection();
      localPeerConnection = peerConnection;
      pendingConnectionRef.current = peerConnection;
      const audioElement = document.createElement("audio");
      localAudioElement = audioElement;
      pendingRemoteAudioRef.current = audioElement;
      audioElement.autoplay = true;
      audioElement.setAttribute("playsinline", "true");
      audioElement.volume = 0.92;
      audioElement.muted = councilMode || !withAudio;
      audioElement.style.display = "none";
      document.body.appendChild(audioElement);
      peerConnection.ontrack = (event) => {
        if (!generationMatches()) {
          return;
        }
        audioElement.srcObject = event.streams[0];
        void audioElement.play().catch(() => {
          // Browser autoplay policy may block this until the user interacts again.
        });
      };
      peerConnection.onconnectionstatechange = () => {
        if (!generationMatches()) {
          return;
        }
        if (peerConnection.connectionState === "connected") {
          clearDisconnectGraceTimer();
          return;
        }
        if (peerConnection.connectionState === "failed") {
          setError("Realtime connection failed");
          disconnect();
          return;
        }
        if (peerConnection.connectionState === "disconnected") {
          clearDisconnectGraceTimer();
          disconnectGraceTimerRef.current = window.setTimeout(() => {
            disconnectGraceTimerRef.current = null;
            if (peerConnection.connectionState === "disconnected") {
              setError("Realtime connection dropped");
              disconnect();
            }
          }, 7000);
          return;
        }
        if (peerConnection.connectionState === "closed") {
          setError("Realtime connection closed");
          disconnect();
        }
      };

      if (withAudio) {
        localStream = await navigator.mediaDevices.getUserMedia({
          audio: {
            echoCancellation: true,
            noiseSuppression: true,
            autoGainControl: true,
            channelCount: 1,
            sampleRate: 24000,
          },
        });
        if (!generationMatches()) {
          localStream.getTracks().forEach((track) => track.stop());
          peerConnection.close();
          audioElement.remove();
          clearPendingRealtimeTransportRefs();
          return;
        }
        const audioTrack = localStream.getAudioTracks()[0] ?? null;
        pendingStreamRef.current = localStream;
        if (audioTrack) {
          inputSenderRef.current = peerConnection.addTrack(audioTrack, localStream as MediaStream);
        }
      } else {
        const syntheticStream = createSyntheticAudioStream();
        localStream = syntheticStream;
        localSyntheticCleanup = syntheticStream.__cleanup ?? null;
        pendingStreamRef.current = syntheticStream;
        pendingSyntheticCleanupRef.current = localSyntheticCleanup;
        const syntheticTrack = syntheticStream.getAudioTracks()[0] ?? null;
        if (syntheticTrack) {
          inputSenderRef.current = peerConnection.addTrack(syntheticTrack, syntheticStream);
        } else {
          syntheticStream.getTracks().forEach((track) => peerConnection.addTrack(track, syntheticStream));
          inputSenderRef.current = null;
        }
      }

      const dataChannel = peerConnection.createDataChannel("oai-events");
      localDataChannel = dataChannel;
      pendingDataChannelRef.current = dataChannel;
      dataChannel.addEventListener("open", () => {
        if (!generationMatches()) {
          return;
        }
        if (!options?.silentOpen) {
          appendLocalTurn({
            speaker: "system",
            text: withAudio ? "Voice channel live." : "Text channel live.",
            mode: "system",
          });
        }
      });
      dataChannel.onmessage = (event) => {
        if (!generationMatches()) {
          return;
        }
        void handleRealtimeEvent(JSON.parse(event.data) as Record<string, unknown>);
      };

      const offer = await peerConnection.createOffer();
      await peerConnection.setLocalDescription(offer);

      const response = await fetch("https://api.openai.com/v1/realtime/calls", {
        method: "POST",
        body: offer.sdp,
        headers: {
          Authorization: `Bearer ${session.client_secret}`,
          "Content-Type": "application/sdp",
        },
      });
      if (!response.ok) {
        throw new Error(await response.text());
      }
      const answer = {
        type: "answer" as const,
        sdp: await response.text(),
      };
      if (!generationMatches()) {
        dataChannel.close();
        peerConnection.close();
        localStream?.getTracks().forEach((track) => track.stop());
        localSyntheticCleanup?.();
        audioElement.pause();
        audioElement.srcObject = null;
        audioElement.remove();
        clearPendingRealtimeTransportRefs();
        return;
      }
      await peerConnection.setRemoteDescription(answer);
      if (dataChannel.readyState !== "open") {
        await new Promise<void>((resolve, reject) => {
          const timeout = window.setTimeout(() => reject(new Error("Realtime data channel did not open in time")), 5000);
          dataChannel.addEventListener("open", () => {
            window.clearTimeout(timeout);
            resolve();
          }, { once: true });
          dataChannel.addEventListener("error", () => {
            window.clearTimeout(timeout);
            reject(new Error("Realtime data channel failed"));
          }, { once: true });
        });
      }
      if (!generationMatches()) {
        dataChannel.close();
        peerConnection.close();
        localStream?.getTracks().forEach((track) => track.stop());
        localSyntheticCleanup?.();
        audioElement.pause();
        audioElement.srcObject = null;
        audioElement.remove();
        clearPendingRealtimeTransportRefs();
        return;
      }
      connectionRef.current = peerConnection;
      dataChannelRef.current = dataChannel;
      streamRef.current = localStream;
      remoteAudioRef.current = audioElement;
      syntheticCleanupRef.current = localSyntheticCleanup;
      clearPendingRealtimeTransportRefs();
      connectionRequestedRef.current = false;
      liveModeRef.current = withAudio ? "voice" : "text";
      setLiveMode(withAudio ? "voice" : "text");
      setStatus("connected");
      mutedRef.current = false;
      setMuted(false);
      setRecordingVoiceTurn(false);
      setAwaitingVoiceReply(false);
      syncInputTrackState(false);
    } catch (caught) {
      connectionRequestedRef.current = false;
      disposeRealtimeTransport(localPeerConnection, localDataChannel, localStream, localAudioElement, localSyntheticCleanup);
      clearPendingRealtimeTransportRefs();
      const message = caught instanceof Error ? caught.message : "Failed to connect realtime session";
      setError(message);
      setStatus("error");
      disconnect();
      throw caught;
    }
  });

  async function sendText(text: string) {
    const trimmed = text.trim();
    if (!trimmed) {
      return;
    }
    if (councilMode) {
      if (await handleModeCommand(trimmed)) {
        return;
      }
      if (responseInFlightRef.current || assistantSpeakingRef.current || audioOutputPlayingRef.current) {
        invalidateCouncilLoop();
      }
      const requestedMode =
        statusRef.current === "connected" && liveModeRef.current === "voice"
          ? "voice"
          : "text";
      await runCouncilTurn(trimmed, requestedMode);
      return;
    }
    try {
      const wantsVoiceReply =
        liveModeRef.current === "voice" ||
        connectionRequestedRef.current ||
        statusRef.current === "connecting";
      if (statusRef.current !== "connected") {
        await connectInternal(wantsVoiceReply);
        if (wantsVoiceReply) {
          await waitForConnectedStatus();
        }
      }
      const useVoiceReply = statusRef.current === "connected" && liveModeRef.current === "voice";
      appendLocalTurn({ speaker: "user", text: trimmed, mode: "text" });
      sendEvent({
        type: "conversation.item.create",
        item: {
          type: "message",
          role: "user",
          content: [{ type: "input_text", text: trimmed }],
        },
      });
      if (useVoiceReply && autoResponse) {
        activeInputEpochRef.current = voiceEpochRef.current;
        awaitFreshInputRef.current = false;
        dropPendingVoiceResponsesRef.current = false;
        setAwaitingVoiceReply(true);
        pendingResponseDeliveryModeRef.current = "audio";
        sendEvent({
          type: "response.create",
          response: {
            output_modalities: ["audio"],
          },
        });
      } else if (autoResponse) {
        pendingResponseDeliveryModeRef.current = "text";
        sendEvent({
          type: "response.create",
          response: {
            output_modalities: ["text"],
          },
        });
      } else {
        setAwaitingVoiceReply(false);
      }
      void persistTurns([{ speaker: "user", text: trimmed, mode: "text" }]).catch((caught) => {
        const message = caught instanceof Error ? caught.message : "Failed to sync text turn";
        setError(message);
      });
    } catch (caught) {
      const message = caught instanceof Error ? caught.message : "Failed to send text";
      setError(message);
    }
  }

  async function enableVoice() {
    if (voiceToggleInFlightRef.current) {
      return;
    }
    voiceToggleInFlightRef.current = true;
    try {
      if (status === "connected" && liveModeRef.current === "voice") {
        return;
      }
      await connectInternal(true);
    } catch (caught) {
      const message = caught instanceof Error ? caught.message : "Voice connection failed";
      setError(message);
      return;
    } finally {
      voiceToggleInFlightRef.current = false;
    }
  }

  async function toggleMute() {
    if (voiceToggleInFlightRef.current) {
      return;
    }
    if (status !== "connected" || liveModeRef.current !== "voice") {
      return;
    }
    voiceToggleInFlightRef.current = true;
    try {
      if (mutedRef.current) {
        resumeRealtimeAfterPause();
        return;
      }
      mutedRef.current = true;
      setMuted(true);
      hardStopRealtime();
    } catch (caught) {
      const message = caught instanceof Error ? caught.message : "Voice connection failed";
      setError(message);
    } finally {
      voiceToggleInFlightRef.current = false;
    }
  }

  async function toggleVoiceCapture() {
    if (connectionRequestedRef.current && status !== "connected") {
      return;
    }
    if (voiceToggleInFlightRef.current) {
      return;
    }
    if (status === "connecting") {
      return;
    }
    if (status === "connected" && liveModeRef.current === "voice") {
      await toggleMute();
      return;
    }
    await enableVoice();
  }

  useEffect(() => {
    const basePresence: ScenePresence = {
      status,
      liveMode,
      muted,
      playerActivity: "idle",
      counterpartActivity: "idle",
      voicePhase: "idle",
    };
    if (status === "connecting") {
      basePresence.playerActivity = liveMode === "voice" ? "listening" : "idle";
      setPresence(basePresence);
      return;
    }
    if (status === "connected" && liveMode === "voice") {
      if (assistantSpeaking) {
        basePresence.voicePhase = "responding";
        basePresence.playerActivity = muted ? "idle" : "listening";
        basePresence.counterpartActivity = "speaking";
      } else if (awaitingVoiceReply) {
        basePresence.voicePhase = "waiting";
        basePresence.playerActivity = muted ? "idle" : "listening";
        basePresence.counterpartActivity = "listening";
      } else if (!muted && recordingVoiceTurn) {
        basePresence.voicePhase = "recording";
        basePresence.playerActivity = "speaking";
      }
      setPresence(basePresence);
      return;
    }
    const latest = [...events].reverse().find((entry) => entry.speaker !== "system");
    if (!latest) {
      setPresence(basePresence);
      return;
    }
    const speakingPresence: ScenePresence = {
      ...basePresence,
      playerActivity: latest.speaker === "user" ? "speaking" : basePresence.playerActivity,
      counterpartActivity: latest.speaker === "assistant" ? "speaking" : basePresence.counterpartActivity,
    };
    setPresence(speakingPresence);
    const duration = latest.speaker === "assistant" ? 3200 : 1400;
    const timeout = window.setTimeout(() => {
      setPresence(basePresence);
    }, duration);
    return () => window.clearTimeout(timeout);
  }, [assistantSpeaking, awaitingVoiceReply, events, liveMode, muted, recordingVoiceTurn, status]);

  useEffect(() => {
    disconnect();
    setEvents(initialTurns);
    setError(null);
  }, [sessionScopeKey]);

  useEffect(() => {
    setEvents((current) => mergeConversationTurns(current, initialTurns));
  }, [initialTurns]);

  useEffect(() => () => disconnect(), []);

  useEffect(() => {
    syncInputTrackState(muted);
  }, [muted, syncInputTrackState]);

  useEffect(() => {
    if (!remoteAudioRef.current) {
      return;
    }
    remoteAudioRef.current.muted = councilMode || liveMode !== "voice" || muted;
  }, [liveMode, muted]);

  return {
    addTurn,
    assistantSpeaking,
    awaitingVoiceReply,
    disconnect,
    enableVoice,
    error,
    events,
    injectContextTurn,
    liveMode,
    muted,
    presence,
    requestAssistantReply,
    recordingVoiceTurn,
    sendText,
    status,
    toggleMute,
    toggleVoiceCapture,
  };
}

function createSyntheticAudioStream(): MediaStream & { __cleanup?: () => void } {
  const audioContext = new AudioContext();
  const destination = audioContext.createMediaStreamDestination();
  const oscillator = audioContext.createOscillator();
  const gain = audioContext.createGain();
  gain.gain.value = 0.00001;
  oscillator.connect(gain);
  gain.connect(destination);
  oscillator.start();
  const stream = destination.stream as MediaStream & { __cleanup?: () => void };
  stream.__cleanup = () => {
    oscillator.stop();
    oscillator.disconnect();
    gain.disconnect();
    void audioContext.close();
  };
  return stream;
}
