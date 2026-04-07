import { lazy, startTransition, Suspense, useEffect, useMemo, useRef, useState, type CSSProperties } from "react";
import { BriefingTheater } from "./components/BriefingTheater";
import { CitizenGrid } from "./components/CitizenGrid";
import { DebateRoom, type DebateRoomHandle, type TownHallSceneState } from "./components/DebateRoom";
import { FeaturetteShelf } from "./components/FeaturetteShelf";
import { SetupRoomViewport } from "./components/SetupRoomViewport";
import { VoiceDock, type VoiceDockHandle } from "./components/VoiceDock";
import { useSetupRealtimeSession } from "./hooks/useSetupRealtimeSession";
import type { CouncilTurnContext } from "./lib/council";
import { featuretteQuestionLabel } from "./lib/featurettes";
import { stagePolicyAxes, stageRoomBrief } from "./lib/stageText";
import { countryThemeProfile } from "./lib/themeProfiles";
import {
  buildCompatibilitySetupSession,
  bootstrapSetupSession,
  callRealtimeTool,
  getSimulation,
  queuePoll,
  resolveStage,
  runPolls,
  startSimulationFromSetup,
} from "./lib/api";
import {
  type AdvisorMode,
  type AuditoriumMode,
  type ConversationTurn,
  makeDefaultSetupDraft,
  trackingList,
  type RoomName,
  type SceneHotspot,
  type ScenePresence,
  type SetupSessionState,
  type StagePackage,
  type SimulationState,
} from "./types";

const SceneViewport = lazy(() => import("./components/SceneViewport"));
type ThemeMode = "light" | "dark";
type StageGate = "loading" | "ready" | "intro" | "live";
const PREPARATION_PHASE_SEQUENCE = [
  "queued",
  "seeding",
  "stagewriting",
  "media",
  "citizen_updates",
  "polling",
  "ready",
  "resolving",
  "error",
] as const;

const ROOM_BUTTONS: Array<{ key: RoomName; label: string }> = [
  { key: "briefing", label: "Dossier" },
  { key: "advisor", label: "War Room" },
  { key: "citizens", label: "Street" },
  { key: "debate", label: "Auditorium" },
];

type DrawerTab = "room" | "intel" | "reels";
const EMPTY_PRESENCE: ScenePresence = {
  status: "idle",
  liveMode: "text",
  muted: false,
  playerActivity: "idle",
  counterpartActivity: "idle",
  voicePhase: "idle",
};

function setupLaunchIntent(prompt: string) {
  const normalized = prompt.trim().toLowerCase().replace(/[.!?]+$/g, "");
  if (/^(?:go|i['’]?m ready|im ready|ready to go|get going|go ahead|go for it|use the default(?: broad)?(?: u\.?s\.?)?(?: run| setup)?|start it|start the run|start the sim|launch it|let's begin|lets begin)$/.test(normalized)) {
    return true;
  }
  if (
    /\b(?:i['’]?m ready|im ready|ready to go|get going|go ahead|go for it|start it|start the run|start the sim|launch it|let's begin|lets begin)\b/.test(
      normalized,
    )
  ) {
    return true;
  }
  return /^(?:that(?:'| i)?s good|sounds good|looks good),?\s+(?:go|start(?: it| the run| the sim)?|launch it)$/.test(
    normalized,
  );
}

function queueAfterPaint(callback: () => void) {
  window.requestAnimationFrame(() => {
    window.setTimeout(callback, 80);
  });
}

function focusableElements(container: HTMLElement | null) {
  if (!container) {
    return [] as HTMLElement[];
  }
  return Array.from(
    container.querySelectorAll<HTMLElement>(
      'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
    ),
  ).filter((element) => !element.hasAttribute("aria-hidden") && element.offsetParent !== null);
}

function sanitizeLoadingQuote(text: string, maxChars = 132) {
  const cleaned = text
    .replace(/^[\s"'“”‘’]+|[\s"'“”‘’]+$/g, "")
    .replace(/^[A-Z][A-Za-z .'-]{0,36}:\s*/, "")
    .replace(/^["“](.*)["”]$/g, "$1")
    .replace(/\s+/g, " ")
    .trim();
  if (cleaned.length <= maxChars) {
    return cleaned;
  }
  const sentences = cleaned.split(/(?<=[.!?])\s+/).filter(Boolean);
  if (sentences[0]) {
    const firstSentence = sentences[0].trim();
    if (firstSentence.length <= maxChars) {
      return firstSentence;
    }
  }
  let assembled = "";
  for (const sentence of sentences) {
    const probe = assembled ? `${assembled} ${sentence}` : sentence;
    if (probe.length > maxChars) {
      break;
    }
    assembled = probe;
    if (assembled.length >= 106) {
      break;
    }
  }
  if (assembled) {
    return assembled.trim();
  }
  const clipped = cleaned.slice(0, maxChars);
  return `${clipped.slice(0, clipped.lastIndexOf(" ") > 64 ? clipped.lastIndexOf(" ") : maxChars).trimEnd()}…`;
}

function formatLoadingAttribution(...parts: Array<string | undefined>) {
  return parts
    .map((part) => part?.replace(/\s+/g, " ").trim())
    .filter((part): part is string => Boolean(part))
    .join(" · ");
}

function liveRoomHeading(room: RoomName, advisorMode: AdvisorMode, auditoriumMode: AuditoriumMode) {
  if (room === "advisor") {
    return advisorMode === "council" ? "Multi-advisor table" : "Chief advisor room";
  }
  if (room === "citizens") {
    return "Street interviews";
  }
  if (room === "debate") {
    return auditoriumMode === "town_hall" ? "Town hall floor" : "Debate stage";
  }
  return "Chapter briefing";
}

function parseRequestedRoom(value: string | null): RoomName | null {
  return value === "briefing" || value === "advisor" || value === "citizens" || value === "debate" ? value : null;
}

function requestedRoomFromModeQuery(params: URLSearchParams): RoomName | null {
  const explicitRoom = parseRequestedRoom(params.get("room"));
  if (explicitRoom) {
    return explicitRoom;
  }
  if (params.get("auditorium")) {
    return "debate";
  }
  if (params.get("advisor")) {
    return "advisor";
  }
  return null;
}

function summarizePollTakeaway(summary: StagePackage["poll_summaries"][number]) {
  const top = Object.entries(summary.shares ?? {}).sort((left, right) => right[1] - left[1])[0];
  if (!top) {
    return summary.question;
  }
  const [answer, share] = top;
  return `${summary.question.replace(/\?+$/, "")}: ${answer} (${Math.round(share * 100)}%)`;
}

function splitNamedLoadingQuote(
  raw: string,
  fallbackAttribution?: string,
): { text: string; attribution?: string; speakerName?: string } {
  const match = raw.match(/^([A-Z][A-Za-z .'-]{0,36}):\s*["“]?(.+?)["”]?\s*$/);
  if (!match) {
    return {
      text: sanitizeLoadingQuote(raw, 92),
      attribution: fallbackAttribution,
    };
  }
  const name = match[1].trim();
  const text = sanitizeLoadingQuote(match[2], 92);
  return {
    text,
    attribution: formatLoadingAttribution(name, fallbackAttribution),
    speakerName: name,
  };
}

const POLICY_LINE_FILLER = /^(?:yes|yeah|yep|no|nope|okay|ok|alright|all right|right|sure|thanks|thank you|mhm|mm-?hmm|hmm|uh|um|let me think|i think so)$/i;
const POLICY_ACTION_WORDS = [
  "keep",
  "open",
  "build",
  "speed",
  "expand",
  "protect",
  "fund",
  "allow",
  "license",
  "tax",
  "cut",
  "ban",
  "cap",
  "require",
  "subsidize",
  "train",
  "invest",
  "enforce",
  "break up",
  "mandate",
  "support",
  "delay",
  "pause",
];
const POLICY_OBJECT_HINTS = [
  "ai",
  "tax",
  "taxes",
  "license",
  "licensing",
  "permit",
  "permits",
  "competition",
  "antitrust",
  "grid",
  "power",
  "chips",
  "school",
  "schools",
  "care",
  "worker",
  "workers",
  "wage",
  "wages",
  "union",
  "bargaining",
  "appeals",
  "safety",
  "audits",
  "compute",
  "interoperability",
  "insurance",
  "benefits",
  "subsidy",
  "subsidies",
  "training",
  "standards",
  "review",
  "guardrails",
];
const POLICY_DIRECTIONAL_PREFIXES = [
  "we will",
  "i will",
  "my plan is",
  "our plan is",
  "the plan is",
  "i want",
  "we want",
  "we need",
  "i'd",
  "id",
  "we'd",
  "wed",
  "let us",
  "let's",
  "lets",
];
const ROOM_DOCK_RETRY_LIMIT = 60;

function clipInlineCopy(text: string, maxChars = 44) {
  const cleaned = text.replace(/\s+/g, " ").trim();
  if (cleaned.length <= maxChars) {
    return cleaned;
  }
  const clipped = cleaned.slice(0, maxChars);
  const boundary = clipped.lastIndexOf(" ");
  return `${clipped.slice(0, boundary > 18 ? boundary : maxChars).trimEnd()}…`;
}

function normalizePolicyBoardLine(text: string) {
  const cleaned = text
    .replace(/\s+/g, " ")
    .trim()
    .replace(/^[\d\-*.)\s]+/, "")
    .replace(/^["“”'‘’]+|["“”'‘’]+$/g, "")
    .replace(/[.?!]+$/g, "")
    .replace(/^(?:i think we should|i think|we should|let's|lets|our plan is to|the plan is to|i want to|we need to)\s+/i, "")
    .trim();
  return clipInlineCopy(cleaned, 44);
}

function extractDebatePolicyLines(turns: string[]) {
  const fragments = turns.flatMap((turn) =>
    turn
      .split(/\n+|(?<=[.!?])\s+/)
      .map((fragment) => normalizePolicyBoardLine(fragment))
      .filter(Boolean),
  );
  const filtered = fragments.filter((fragment) => {
    const normalized = fragment.toLowerCase();
    if (POLICY_LINE_FILLER.test(normalized)) {
      return false;
    }
    if (fragment.includes("?")) {
      return false;
    }
    const words = fragment.split(/\s+/).filter(Boolean);
    if (words.length < 3) {
      return false;
    }
    const hasAction = POLICY_ACTION_WORDS.some((word) => normalized.includes(word));
    const hasObject = POLICY_OBJECT_HINTS.some((word) => normalized.includes(word));
    if (hasAction && hasObject) {
      return true;
    }
    if (POLICY_DIRECTIONAL_PREFIXES.some((prefix) => normalized.startsWith(prefix))) {
      return words.length >= 4 && hasObject;
    }
    return false;
  });
  return filtered.filter((item, index) => filtered.findIndex((other) => other.toLowerCase() === item.toLowerCase()) === index).slice(0, 4);
}

function isCitizenFocusLocked(presence: ScenePresence) {
  return (
    presence.status === "connecting" ||
    presence.playerActivity === "speaking" ||
    presence.counterpartActivity === "speaking" ||
    presence.voicePhase === "waiting"
  );
}

function chooseSetupTurns(
  liveTurns: Array<{ id: string; speaker: "user" | "assistant" | "system"; text: string; mode?: string; created_at: string }>,
  sessionTurns: Array<{ id: string; speaker: "user" | "assistant" | "system"; text: string; created_at: string }>,
) {
  if (liveTurns.length === 0) {
    return sessionTurns;
  }
  if (sessionTurns.length > liveTurns.length) {
    return sessionTurns;
  }
  return liveTurns;
}

function loadingPhaseMeta(progress: SimulationState["progress"]) {
  return {
    phaseStep: progress.phase === "ready" ? "Ready" : progress.label,
    statusLine:
      progress.phase === "ready"
        ? "The next chapter is assembled."
        : `${progress.percent}% · ${progress.detail || progress.label}`,
  };
}

function simulationUrl(simulationId: string, advisorMode: AdvisorMode, auditoriumMode: AuditoriumMode) {
  const params = new URLSearchParams();
  params.set("sim", simulationId);
  params.set("advisor", advisorModeSlug(advisorMode));
  params.set("auditorium", auditoriumMode);
  return `?${params.toString()}`;
}

function parseStoredAdvisorMode(value: string | null): AdvisorMode {
  return value === "council" || value === "multi" ? "council" : "solo";
}

function advisorModeSlug(mode: AdvisorMode) {
  return mode === "council" ? "multi" : "solo";
}

function simulationUpdatedAtMs(simulation?: SimulationState | null) {
  if (!simulation?.updated_at) {
    return 0;
  }
  const parsed = Date.parse(simulation.updated_at);
  return Number.isFinite(parsed) ? parsed : 0;
}

function mergeThreadTurns(...groups: Array<readonly ConversationTurn[]>) {
  const merged = new Map<string, ConversationTurn>();
  for (const group of groups) {
    for (const turn of group) {
      if (!turn) {
        continue;
      }
      const existing = merged.get(turn.id);
      merged.set(turn.id, existing ? { ...existing, ...turn } : turn);
    }
  }
  return [...merged.values()].sort((left, right) => Date.parse(left.created_at) - Date.parse(right.created_at));
}

export default function App() {
  const [simulation, setSimulation] = useState<SimulationState | null>(null);
  const [setupSession, setSetupSession] = useState<SetupSessionState | null>(null);
  const [launchingSetup, setLaunchingSetup] = useState(false);
  const [setupBooting, setSetupBooting] = useState(false);
  const [stageGate, setStageGate] = useState<StageGate>("loading");
  const [setupPromptDraft, setSetupPromptDraft] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [themeMode, setThemeMode] = useState<ThemeMode>(() => {
    if (typeof window === "undefined") {
      return "light";
    }
    return window.localStorage.getItem("econ-sim-theme") === "dark" ? "dark" : "light";
  });
  const [advisorMode, setAdvisorMode] = useState<AdvisorMode>(() => {
    if (typeof window === "undefined") {
      return "solo";
    }
    const queryValue = new URLSearchParams(window.location.search).get("advisor");
    if (queryValue) {
      return parseStoredAdvisorMode(queryValue);
    }
    return parseStoredAdvisorMode(window.localStorage.getItem("econ-sim-advisor-mode"));
  });
  const [auditoriumMode, setAuditoriumMode] = useState<AuditoriumMode>(() => {
    if (typeof window === "undefined") {
      return "debate";
    }
    const queryValue = new URLSearchParams(window.location.search).get("auditorium");
    if (queryValue === "town_hall" || queryValue === "debate") {
      return queryValue;
    }
    return window.localStorage.getItem("econ-sim-auditorium-mode") === "town_hall" ? "town_hall" : "debate";
  });
  const [room, setRoom] = useState<RoomName>("briefing");
  const roomRef = useRef<RoomName>("briefing");
  const [manualPollQuestion, setManualPollQuestion] = useState("");
  const [activeCitizenId, setActiveCitizenId] = useState<string | undefined>(undefined);
  const [streetCandidateCitizenId, setStreetCandidateCitizenId] = useState<string | undefined>(undefined);
  const [streetPendingCitizenId, setStreetPendingCitizenId] = useState<string | undefined>(undefined);
  const [resolvingStage, setResolvingStage] = useState(false);
  const [setupDetailsOpen, setSetupDetailsOpen] = useState(false);
  const [panelsOpen, setPanelsOpen] = useState(false);
  const [drawerTab, setDrawerTab] = useState<DrawerTab>("room");
  const [reelsOpen, setReelsOpen] = useState(false);
  const [reelsRequestedFeaturetteId, setReelsRequestedFeaturetteId] = useState<string | null>(null);
  const queryRequestedRoomRef = useRef<RoomName | null>(
    typeof window === "undefined" ? null : requestedRoomFromModeQuery(new URLSearchParams(window.location.search)),
  );
  const honorQueryRequestedRoomRef = useRef(Boolean(queryRequestedRoomRef.current));
  const [showCinematicIntro, setShowCinematicIntro] = useState(false);
  const [sceneTextOpen, setSceneTextOpen] = useState(false);
  const [sceneTextDraft, setSceneTextDraft] = useState("");
  const [loadingQuoteIndex, setLoadingQuoteIndex] = useState(0);
  const [isFullscreen, setIsFullscreen] = useState(false);
  const [scenePresence, setScenePresence] = useState<Record<string, ScenePresence>>({
    advisor: EMPTY_PRESENCE,
    citizens: EMPTY_PRESENCE,
    debate: EMPTY_PRESENCE,
  });
  const [liveSceneCaptions, setLiveSceneCaptions] = useState<Record<RoomName, ConversationTurn | null>>({
    briefing: null,
    advisor: null,
    citizens: null,
    debate: null,
  });
  const advisorDockRef = useRef<VoiceDockHandle | null>(null);
  const citizenDockRef = useRef<VoiceDockHandle | null>(null);
  const debateDockRef = useRef<VoiceDockHandle | null>(null);
  const debateRoomRef = useRef<DebateRoomHandle | null>(null);
  const [councilFloor, setCouncilFloor] = useState<{
    lead: string;
    owner: string;
    contrast: string[];
    reason?: string;
  } | null>(null);
  const [townHallSceneState, setTownHallSceneState] = useState<TownHallSceneState | null>(null);
  const [townHallLaunchNonce, setTownHallLaunchNonce] = useState(0);
  const pendingCitizenActionRef = useRef<{ citizenId: string; action: (dock: VoiceDockHandle) => void } | null>(null);
  const dockActionGenerationRef = useRef(0);
  const refreshSnapshotGenerationRef = useRef(0);
  const simulationRef = useRef<SimulationState | null>(null);
  const setupLaunchInFlightRef = useRef<string | null>(null);
  const initialBootRef = useRef(false);
  const setupComposerRef = useRef<HTMLInputElement | null>(null);
  const reelsOverlayRef = useRef<HTMLDivElement | null>(null);
  const reelsCloseButtonRef = useRef<HTMLButtonElement | null>(null);
  const reelsLastFocusRef = useRef<HTMLElement | null>(null);

  const stage = simulation?.stages[simulation.active_stage_index];
  const setupRealtime = useSetupRealtimeSession({
    session: setupSession,
    onSessionSync: (nextSession) => {
      startTransition(() => {
        setSetupSession(nextSession);
      });
    },
    onAutoLaunch: async (nextSession) => {
      await handleStartFromSetup(nextSession);
    },
  });
  const setupTurns = chooseSetupTurns(setupRealtime.events, setupSession?.transcript ?? []);
  const setupCaption = useMemo(
    () => [...setupTurns].reverse().find((turn) => turn.speaker !== "system"),
    [setupTurns],
  );
  const countryFrame = simulation?.config.country ?? setupSession?.draft.country ?? "United States";
  const themeProfile = useMemo(() => countryThemeProfile(countryFrame), [countryFrame]);
  const setupComposerValue = setupPromptDraft;
  const setupRecentTurns = useMemo(() => [...setupTurns].reverse().slice(0, 4), [setupTurns]);
  const setupFocusSummary = useMemo(() => {
    if (!setupSession) {
      return "Broad default run. Add a concrete nudge only if you want one.";
    }
    const draft = setupSession.draft;
    const nudges = [draft.region_focus, draft.topic_lens, draft.premise, draft.stakes]
      .map((entry) => entry?.trim())
      .filter(Boolean);
    return nudges.length > 0
      ? nudges.slice(0, 2).join(" · ")
      : "Broad representative run. No special region or lens is locked.";
  }, [setupSession]);
  const setupReady = setupSession?.status === "ready";
  const setupVoiceLive =
    setupRealtime.liveMode === "voice" &&
    setupRealtime.status === "connected" &&
    !setupRealtime.muted;
  const setupVoiceConnected = setupRealtime.liveMode === "voice" && setupRealtime.status === "connected";
  const setupVoiceRecording = setupRealtime.presence.voicePhase === "recording";
  const setupVoiceConnecting = setupRealtime.status === "connecting" && setupRealtime.liveMode === "voice";
  const setupTextPreparing = setupRealtime.status === "connecting" && setupRealtime.liveMode === "text";
  const setupVoiceWaiting =
    setupVoiceConnecting ||
    (setupRealtime.liveMode === "voice" &&
      (setupRealtime.presence.voicePhase === "waiting" || setupRealtime.presence.voicePhase === "responding"));
  const citizens = stage?.sample_citizens ?? [];
  const currentStageRoomBrief = stageRoomBrief(stage);
  const currentStageAxes = stagePolicyAxes(stage, 4);
  const readyFeaturetteCount = stage?.featurettes.filter((featurette) => featurette.status === "ready").length ?? 0;
  const featurettesPending = stage
    ? Boolean(simulation && simulation.status === "stage_ready" && stage.featurettes_status !== "ready" && stage.featurettes_status !== "error")
    : false;
  const displayedFeaturettes = stage?.featurettes ?? [];
  const playableFeaturettes = useMemo(
    () =>
      displayedFeaturettes.filter(
        (featurette) => featurette.status === "ready" && Boolean(featurette.narrative_beats?.length),
      ),
    [displayedFeaturettes],
  );
  const hasPlayableFeaturettes = playableFeaturettes.length > 0;
  const activeCitizen = citizens.find((citizen) => citizen.citizen_id === activeCitizenId);
  const candidateCitizen = citizens.find((citizen) => citizen.citizen_id === streetCandidateCitizenId);
  const citizenFocusLocked = isCitizenFocusLocked(scenePresence.citizens);
  const metrics = stage ? trackingList(stage.tracking) : [];
  const advisorThreadKey = simulation
    ? `stage:${simulation.active_stage_index}:advisor${advisorMode === "council" ? ":council" : ""}`
    : "";
  const advisorTurns = useMemo(
    () => simulation?.conversation_threads[advisorThreadKey] ?? [],
    [advisorThreadKey, simulation?.conversation_threads],
  );
  const advisorPolicyNotes = useMemo(() => stage?.policy_notes ?? [], [stage?.policy_notes]);
  const councilContext = useMemo<CouncilTurnContext | undefined>(
    () =>
      stage
        ? {
            dominantMechanism: stage.dominant_mechanism,
            dominantUpside: stage.dominant_upside,
            mainSplit: stage.main_split,
            policyNotes: advisorPolicyNotes,
            pollTakeaways: stage.poll_summaries.slice(0, 4).map((summary) => summarizePollTakeaway(summary)),
          }
        : undefined,
    [advisorPolicyNotes, stage],
  );
  const debateStageThreadKey = simulation ? `stage:${simulation.active_stage_index}:debate` : "";
  const townHallThreadKey = simulation ? `stage:${simulation.active_stage_index}:debate:town_hall` : "";
  const debateStageTurns = useMemo(
    () => simulation?.conversation_threads[debateStageThreadKey] ?? [],
    [debateStageThreadKey, simulation?.conversation_threads],
  );
  const townHallTurns = useMemo(
    () => simulation?.conversation_threads[townHallThreadKey] ?? [],
    [simulation?.conversation_threads, townHallThreadKey],
  );
  const combinedAuditoriumTurns = useMemo(
    () => mergeThreadTurns(debateStageTurns, townHallTurns),
    [debateStageTurns, townHallTurns],
  );
  const debateTurns = auditoriumMode === "town_hall" ? townHallTurns : debateStageTurns;
  const debatePlayerTurns = useMemo(
    () => combinedAuditoriumTurns.filter((turn) => turn.speaker === "user").map((turn) => turn.text.trim()).filter(Boolean),
    [combinedAuditoriumTurns],
  );
  const debatePlayerCase = useMemo(() => debatePlayerTurns.join("\n\n"), [debatePlayerTurns]);
  const debatePolicyLines = useMemo(() => extractDebatePolicyLines(debatePlayerTurns), [debatePlayerTurns]);
  const latestDebatePlayerTurn = useMemo(
    () =>
      [...debateTurns]
        .reverse()
        .find((turn) => turn.speaker === "user" && turn.text.trim())
        ?.text.trim() ?? debatePlayerTurns.at(-1) ?? "",
    [debatePlayerTurns, debateTurns],
  );
  const debatePlatform = useMemo(
    () =>
      debatePolicyLines.length > 0
        ? debatePolicyLines.join("\n")
        : debatePlayerCase.trim()
        ? debatePlayerCase
        : advisorPolicyNotes.length > 0
          ? advisorPolicyNotes.join("\n")
          : currentStageAxes.join("\n"),
    [advisorPolicyNotes, currentStageAxes, debatePlayerCase, debatePolicyLines],
  );
  const debateBoardNotes = useMemo(
    () =>
      (debatePolicyLines.length > 0 ? debatePolicyLines : advisorPolicyNotes.length > 0 ? advisorPolicyNotes : stage?.policy_notes ?? [])
        .map((line) => line.trim().replace(/^[-*]\s*/, ""))
        .filter(Boolean)
        .slice(0, 4),
    [advisorPolicyNotes, debatePolicyLines, stage?.policy_notes],
  );
  const citizenThreadKey =
    simulation && activeCitizen ? `stage:${simulation.active_stage_index}:citizen:${activeCitizen.citizen_id}` : "";
  const citizenTurns = useMemo(
    () => (citizenThreadKey ? simulation?.conversation_threads[citizenThreadKey] ?? [] : []),
    [citizenThreadKey, simulation?.conversation_threads],
  );
  const liveSceneCaption = liveSceneCaptions[room];
  const currentSceneTurns = room === "advisor" ? advisorTurns : room === "citizens" ? citizenTurns : room === "debate" ? debateTurns : [];
  const currentSceneCaption = useMemo(() => {
    if (liveSceneCaption && liveSceneCaption.speaker !== "system" && liveSceneCaption.text.trim()) {
      return liveSceneCaption;
    }
    if (
      room === "debate" &&
      auditoriumMode === "town_hall" &&
      townHallSceneState?.question?.question &&
      (townHallSceneState.phase === "generating" ||
        townHallSceneState.phase === "voter_speaking" ||
        (townHallSceneState.phase === "player_turn" && !townHallSceneState.playerAnswered))
    ) {
      return {
        id: townHallSceneState.activeTurnId ?? "town-hall-preview",
        speaker: "assistant" as const,
        speaker_name: townHallSceneState.question.displayName,
        text: townHallSceneState.question.question,
        mode: "voice" as const,
        created_at: new Date().toISOString(),
      };
    }
    const visibleTurns = currentSceneTurns.filter((turn) => turn.speaker !== "system");
    return visibleTurns.at(-1);
  }, [auditoriumMode, currentSceneTurns, liveSceneCaption, room, townHallSceneState]);
  const isBusy = simulation?.status === "initializing" || simulation?.status === "resolving";
  const previousResolvedStage = useMemo(() => {
    if (!simulation || simulation.active_stage_index === 0) {
      return undefined;
    }
    return simulation.stages[simulation.active_stage_index - 1];
  }, [simulation]);
  const loadingHighlights = useMemo(() => {
    if (!simulation) {
      return [];
    }
    const latestPoll = simulation.current_polls[0];
    const [topAnswer, topShare] = latestPoll
      ? Object.entries(latestPoll.shares).sort((left, right) => right[1] - left[1])[0] ?? ["Polling pending", 0]
      : ["Polling pending", 0];
    const normalizedTopAnswer = String(topAnswer).trim().toLowerCase();
    const pollDetail = latestPoll
      ? ["other", "mixed", "none", "unsure", "not sure"].includes(normalizedTopAnswer)
        ? sanitizeLoadingQuote(latestPoll.question.replace(/\?+$/, ""), 88)
        : `${topAnswer} · ${Math.round(topShare * 100)}%`
      : "Fresh polling will appear once the stage locks in.";
    return [
      {
        label: "Chapter",
        value: `Chapter ${simulation.active_stage_index + 1}`,
        detail: stage?.phase_label ?? (simulation.player_in_power ? "You are still in office." : "You are trying to win power back."),
      },
      {
        label: "Public mood",
        value: `${simulation.approval_rating.toFixed(0)}% approval`,
        detail: pollDetail,
      },
      {
        label: "Electorate",
        value: `${simulation.persona_count_ready || simulation.config.persona_count} citizens ready`,
        detail: "Representative citizens are being updated for the next chapter.",
      },
      {
        label: previousResolvedStage?.title ? "Carryover" : "In motion",
        value: previousResolvedStage?.title ?? simulation.progress.label,
        detail:
          previousResolvedStage?.resolution
            ? "The vote is being absorbed into the next chapter; the opening montage will reveal who took office and why."
            : "The orchestrator is resolving how capability, policy, and public reaction carry forward.",
      },
    ];
  }, [previousResolvedStage, simulation, stage]);
  const loadingVoiceStrips = useMemo<Array<{ text: string; attribution?: string }>>(() => {
    const sourceStage = previousResolvedStage ?? stage;
    if (!sourceStage) {
      return [];
    }
    const citizenByName = new Map(
      (sourceStage.sample_citizens ?? [])
        .map((citizen) => [citizen.display_name.toLowerCase(), citizen] as const),
    );
    const citizenQuotes = (sourceStage.sample_citizens ?? [])
      .map((citizen) => {
        const text = sanitizeLoadingQuote(citizen.current_update || citizen.summary, 104);
        if (!text) {
          return null;
        }
        return {
          text,
          attribution: formatLoadingAttribution(citizen.display_name, citizen.role, citizen.region),
        };
      })
      .filter(Boolean) as Array<{ text: string; attribution?: string }>;
    const prioritized = [...sourceStage.poll_summaries].sort((left, right) => {
      const priority = (question: string) => {
        const lower = question.toLowerCase();
        if (lower.includes("biggest national effect of ai")) return 0;
        if (lower.includes("right now ai mostly feels able to handle")) return 1;
        if (lower.includes("still clearly needs a person")) return 2;
        if (lower.includes("easier, cheaper, or better")) return 3;
        if (lower.includes("hate to lose")) return 4;
        if (lower.includes("most shaping your life")) return 5;
        if (lower.includes("useful expertise now feels")) return 6;
        if (lower.includes("school or learning around you")) return 7;
        if (lower.includes("why would you vote")) return 8;
        return 10;
      };
      return priority(left.question) - priority(right.question);
    });
    const pollQuotes = prioritized
      .flatMap((summary) =>
        (summary.sample_reasons ?? []).map((reason) => {
          const parsed = splitNamedLoadingQuote(reason, "National poll");
          if (!parsed.speakerName) {
            return parsed;
          }
          const citizen = citizenByName.get(parsed.speakerName.toLowerCase());
          if (!citizen) {
            return parsed;
          }
          return {
            ...parsed,
            attribution: formatLoadingAttribution(citizen.display_name, citizen.role, citizen.region),
          };
        }),
      )
      .filter((entry) => entry.text);
    return [...citizenQuotes.slice(0, 3), ...pollQuotes].slice(0, 6);
  }, [previousResolvedStage, stage]);
  const loadingDeck = useMemo<Array<{ kind: "quote" | "note"; label: string; text: string; attribution?: string }>>(() => {
    const sourceStage = previousResolvedStage ?? stage;
    const quoteEntries: Array<{ kind: "quote" | "note"; label: string; text: string; attribution?: string }> = [];
    const noteEntries: Array<{ kind: "quote" | "note"; label: string; text: string; attribution?: string }> = [];
    const seenQuotes = new Set<string>();
    const seenNotes = new Set<string>();
    const pushQuote = (label: string, rawText?: string, attribution?: string) => {
      const trimmed = sanitizeLoadingQuote(rawText ?? "", 132);
      if (!trimmed || seenQuotes.has(`${label}:${trimmed}:${attribution ?? ""}`)) {
        return;
      }
      seenQuotes.add(`${label}:${trimmed}:${attribution ?? ""}`);
      quoteEntries.push({ kind: "quote", label, text: trimmed, attribution });
    };
    const pushNote = (label: string, rawText?: string) => {
      const trimmed = sanitizeLoadingQuote(rawText ?? "", 132);
      if (!trimmed || seenNotes.has(`${label}:${trimmed}`)) {
        return;
      }
      seenNotes.add(`${label}:${trimmed}`);
      noteEntries.push({ kind: "note", label, text: trimmed });
    };
    pushNote("Documentary reel", sourceStage?.montage_logline);
    pushNote("War room brief", stageRoomBrief(sourceStage));
    pushNote("State of world", sourceStage?.state_of_world);
    pushNote("Capability now", sourceStage?.capability_frontier_now);
    for (const indicator of sourceStage?.economic_indicators.slice(0, 2) ?? []) {
      pushNote("Economic read", indicator);
    }
    pushNote("Visible gain", sourceStage?.dominant_upside);
    pushNote("Still hard", sourceStage?.still_hard_now);
    pushNote("Physical bottleneck", sourceStage?.physical_world_status);
    pushNote("Main split", sourceStage?.main_split);
    for (const beat of sourceStage?.narrative_beats.slice(0, 3) ?? []) {
      pushNote("Documentary line", beat.line);
    }
    for (const quote of loadingVoiceStrips) {
      if (quote.text) {
        pushQuote("Voice from the country", quote.text, quote.attribution);
      }
    }
    if (quoteEntries.length === 0 && noteEntries.length === 0 && simulation) {
      pushNote("Country", simulation.config.country);
      pushNote(
        "Lens",
        [simulation.config.region_focus, simulation.config.topic_lens].filter((value) => value && value.trim()).join(" · "),
      );
      pushNote("Premise", simulation.config.premise);
      pushNote("Stakes", simulation.config.stakes);
      pushNote("Transition", simulation.progress.label);
    }
    if (quoteEntries.length === 0 && noteEntries.length === 0) {
      pushNote("Transition", "The next chapter is being assembled from the current run.");
      pushNote("Status", simulation?.progress.label ?? "Writing the world.");
    }
    const entries: Array<{ kind: "quote" | "note"; label: string; text: string; attribution?: string }> = [];
    const maxLength = 12;
    const effectiveQuotes = quoteEntries;
    const effectiveNotes = noteEntries;
    if (effectiveQuotes.length > 0) {
      entries.push(...effectiveQuotes.slice(0, maxLength));
    } else {
      entries.push(...effectiveNotes.slice(0, maxLength));
    }
    if (entries.length < maxLength) {
      const remainingNotes = effectiveNotes.filter((entry) => !entries.includes(entry));
      entries.push(...remainingNotes.slice(0, maxLength - entries.length));
    }
    return entries.slice(0, maxLength);
  }, [loadingVoiceStrips, previousResolvedStage, simulation, stage]);
  const stageKey = simulation && stage ? `${simulation.simulation_id}:${stage.index}:${stage.generated_at}` : null;
  const showLoadingStage = Boolean(simulation) && (resolvingStage || !stage || simulation.status !== "stage_ready" || stageGate === "ready");
  const readyForNextEra = Boolean(simulation && stage && simulation.status === "stage_ready" && stageGate === "ready");
  const loadingEyebrow =
    resolvingStage
      ? "Election night"
      : readyForNextEra
        ? (stage?.phase_label ?? "Next chapter")
        : simulation?.progress.phase === "seeding"
          ? "Building the electorate"
          : simulation?.progress.phase === "stagewriting"
            ? "Writing the next era"
            : simulation?.progress.phase === "media"
              ? "Scoring the documentary"
              : simulation?.progress.phase === "citizen_updates"
                ? "Refreshing daily life"
                : simulation?.progress.phase === "polling"
                  ? "Reading the country"
                  : (simulation?.progress.label ?? "Transition");
  const debateAdvancePayload =
    debatePlatform.trim()
    || advisorPolicyNotes.join("\n").trim()
    || currentStageAxes.join("\n").trim()
    || stage?.state_of_world.trim()
    || "Carry the current platform into the vote.";
  const debateAdvanceDisabled = resolvingStage;
  const debateAdvanceLabel = resolvingStage ? "Counting vote..." : "Call election and advance";
  const debateAdvanceHint = debateAdvanceDisabled
    ? "The election is being counted and folded into the next stage."
    : "Lock the vote and move the simulation into the next chapter.";
  const visibleLoadingHighlights = useMemo(
    () => loadingHighlights.slice(0, 2),
    [loadingHighlights],
  );
  const introducedStageRef = useRef<string | null>(null);

  async function bootSetupChamber() {
    setSetupBooting(true);
    setError(null);
    try {
      const nextSession = await bootstrapSetupSession();
      startTransition(() => {
        setSetupSession(nextSession);
        setSetupPromptDraft("");
        setSetupDetailsOpen(false);
      });
    } catch (caught) {
      setSetupSession(
        buildCompatibilitySetupSession(
          makeDefaultSetupDraft(),
          "The backend was unreachable while opening the chamber, so the UI is holding a local draft until the API comes back.",
        ),
      );
      setError(caught instanceof Error ? caught.message : "failed to open setup chamber");
    } finally {
      setSetupBooting(false);
    }
  }

  useEffect(() => {
    if (initialBootRef.current) {
      return;
    }
    initialBootRef.current = true;
    const params = new URLSearchParams(window.location.search);
    const simulationId = params.get("sim");
    void (async () => {
      try {
        if (simulationId) {
          const loaded = await getSimulation(simulationId);
          setSimulation(loaded);
          const requestedRoom =
            honorQueryRequestedRoomRef.current
              ? requestedRoomFromModeQuery(params) ?? queryRequestedRoomRef.current
              : null;
          setRoom(requestedRoom ?? loaded.current_room);
          if (loaded.focused_citizen_id) {
            setActiveCitizenId(loaded.focused_citizen_id);
          }
          return;
        }
        await bootSetupChamber();
      } catch (caught) {
        setError(caught instanceof Error ? caught.message : "failed to load simulation");
      }
    })();
  }, []);

  useEffect(() => {
    if (!simulation || simulation.status !== "stage_ready" || !stage || !stageKey) {
      return;
    }
    if (introducedStageRef.current === stageKey) {
      return;
    }
    introducedStageRef.current = stageKey;
    const params = new URLSearchParams(window.location.search);
    const directLive = params.get("view") === "live";
    const requestedRoom = honorQueryRequestedRoomRef.current
      ? requestedRoomFromModeQuery(params) ?? queryRequestedRoomRef.current
      : null;
    const defaultLiveRoom = directLive
      ? (simulation.current_room === "briefing" ? "advisor" : simulation.current_room)
      : simulation.current_room;
    setShowCinematicIntro(false);
    setStageGate(directLive ? "live" : "ready");
    setPanelsOpen(false);
    setDrawerTab("room");
    setSceneTextOpen(false);
    setSceneTextDraft("");
    setRoom(requestedRoom ?? defaultLiveRoom);
  }, [simulation, stage, stageKey]);

  useEffect(() => {
    document.documentElement.dataset.theme = themeMode;
    window.localStorage.setItem("econ-sim-theme", themeMode);
  }, [themeMode]);

  useEffect(() => {
    const syncFullscreen = () => {
      setIsFullscreen(Boolean(document.fullscreenElement));
    };
    syncFullscreen();
    document.addEventListener("fullscreenchange", syncFullscreen);
    return () => document.removeEventListener("fullscreenchange", syncFullscreen);
  }, []);

  useEffect(() => {
    window.localStorage.setItem("econ-sim-advisor-mode", advisorModeSlug(advisorMode));
    window.localStorage.setItem("econ-sim-auditorium-mode", auditoriumMode);
    const url = new URL(window.location.href);
    url.searchParams.set("advisor", advisorModeSlug(advisorMode));
    url.searchParams.set("auditorium", auditoriumMode);
    window.history.replaceState({}, "", url);
  }, [advisorMode, auditoriumMode]);

  useEffect(() => {
    simulationRef.current = simulation;
  }, [simulation]);

  useEffect(() => {
    if (!simulation) {
      return;
    }
    setupRealtime.disconnect();
    setSetupPromptDraft("");
  }, [simulation]);

  useEffect(() => {
    if (!setupRealtime.error) {
      return;
    }
    setError(setupRealtime.error);
  }, [setupRealtime.error]);

  useEffect(() => {
    roomRef.current = room;
  }, [room]);

  useEffect(() => {
    if (!simulation || (simulation.status !== "initializing" && simulation.status !== "resolving")) {
      return;
    }
    setStageGate("loading");
    const timer = window.setInterval(async () => {
      try {
        const latest = await getSimulation(simulation.simulation_id);
        const requestedRoom = honorQueryRequestedRoomRef.current
          ? requestedRoomFromModeQuery(new URLSearchParams(window.location.search)) ?? queryRequestedRoomRef.current
          : null;
        startTransition(() => {
          setSimulation(latest);
          setRoom(requestedRoom ?? latest.current_room);
          if (latest.focused_citizen_id) {
            setActiveCitizenId(latest.focused_citizen_id);
          }
        });
      } catch (caught) {
        setError(caught instanceof Error ? caught.message : "failed to refresh simulation");
      }
    }, 2000);
    return () => window.clearInterval(timer);
  }, [simulation]);

  useEffect(() => {
    if (!simulation || !stage || simulation.status !== "stage_ready" || !featurettesPending) {
      return;
    }
    const timer = window.setInterval(() => {
      refreshSimulationSnapshot(0);
    }, 4000);
    return () => window.clearInterval(timer);
  }, [featurettesPending, simulation, stage]);

  useEffect(() => {
    if (!stage || showLoadingStage || showCinematicIntro) {
      setReelsOpen(false);
      setReelsRequestedFeaturetteId(null);
    }
  }, [showCinematicIntro, showLoadingStage, stage]);

  useEffect(() => {
    if (!reelsOpen) {
      return;
    }
    const previousOverflow = document.body.style.overflow;
    reelsLastFocusRef.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setReelsOpen(false);
        setReelsRequestedFeaturetteId(null);
        return;
      }
      if (event.key !== "Tab") {
        return;
      }
      const focusables = focusableElements(reelsOverlayRef.current);
      if (focusables.length === 0) {
        event.preventDefault();
        reelsOverlayRef.current?.focus();
        return;
      }
      const activeElement = document.activeElement as HTMLElement | null;
      const currentIndex = activeElement ? focusables.indexOf(activeElement) : -1;
      const nextIndex = event.shiftKey
        ? currentIndex <= 0
          ? focusables.length - 1
          : currentIndex - 1
        : currentIndex === -1 || currentIndex >= focusables.length - 1
          ? 0
          : currentIndex + 1;
      event.preventDefault();
      focusables[nextIndex]?.focus();
    };
    const focusTimer = window.setTimeout(() => {
      (reelsCloseButtonRef.current ?? focusableElements(reelsOverlayRef.current)[0] ?? reelsOverlayRef.current)?.focus();
    }, 0);
    document.body.style.overflow = "hidden";
    window.addEventListener("keydown", handleKeyDown);
    return () => {
      window.clearTimeout(focusTimer);
      document.body.style.overflow = previousOverflow;
      window.removeEventListener("keydown", handleKeyDown);
      reelsLastFocusRef.current?.focus();
      reelsLastFocusRef.current = null;
    };
  }, [reelsOpen]);

  useEffect(() => {
    if (!reelsOpen) {
      return;
    }
    const focusables = focusableElements(reelsOverlayRef.current);
    if (focusables.length === 0) {
      reelsOverlayRef.current?.focus();
      return;
    }
    if (!focusables.includes(document.activeElement as HTMLElement)) {
      (reelsCloseButtonRef.current ?? focusables[0])?.focus();
    }
  }, [reelsOpen, reelsRequestedFeaturetteId, stage?.featurettes, stage?.index]);

  useEffect(() => {
    if (!showLoadingStage || loadingDeck.length <= 1) {
      setLoadingQuoteIndex(0);
      return;
    }
    const timer = window.setInterval(() => {
      setLoadingQuoteIndex((current) => (current + 1) % loadingDeck.length);
    }, readyForNextEra ? 12000 : 10000);
    return () => window.clearInterval(timer);
  }, [loadingDeck, readyForNextEra, showLoadingStage]);

  useEffect(() => {
    if (simulation?.focused_citizen_id && citizens.some((citizen) => citizen.citizen_id === simulation.focused_citizen_id)) {
      setActiveCitizenId(simulation.focused_citizen_id);
      setStreetPendingCitizenId(undefined);
      return;
    }
    setActiveCitizenId(undefined);
    setStreetPendingCitizenId(undefined);
  }, [citizens, simulation?.focused_citizen_id, simulation?.active_stage_index]);

  useEffect(() => {
    if (room !== "citizens" || !activeCitizen?.citizen_id) {
      return;
    }
    const pending = pendingCitizenActionRef.current;
    if (!pending || pending.citizenId !== activeCitizen.citizen_id) {
      return;
    }
    pendingCitizenActionRef.current = null;
    withMountedDock(
      pending.action,
      0,
      dockActionGenerationRef.current,
      "citizens",
      dockScopeKeyForRoom("citizens", { citizenId: activeCitizen.citizen_id }),
    );
  }, [activeCitizen?.citizen_id, room]);

  async function handleRestart() {
    disconnectLiveChannels();
    setupRealtime.disconnect();
    setupLaunchInFlightRef.current = null;
    setSimulation(null);
    setSetupSession(null);
    setSetupPromptDraft("");
    setStageGate("loading");
    setSetupDetailsOpen(false);
    setPanelsOpen(false);
    setDrawerTab("room");
    setShowCinematicIntro(false);
    setSceneTextOpen(false);
    setSceneTextDraft("");
    window.history.replaceState(null, "", window.location.pathname);
    setError(null);
    await bootSetupChamber();
  }

  async function handleStartFromSetup(sessionOverride?: SetupSessionState) {
    const activeSession = sessionOverride ?? (await setupRealtime.awaitPendingSync()) ?? setupSession;
    if (!activeSession) {
      return;
    }
    if (setupLaunchInFlightRef.current) {
      return;
    }
    setupLaunchInFlightRef.current = activeSession.session_id;
    setupRealtime.disconnect();
    setSetupPromptDraft("");
    setLaunchingSetup(true);
    setError(null);
    try {
      const created = await startSimulationFromSetup(activeSession);
      setSimulation(created);
      setRoom("briefing");
      setStageGate("loading");
      setSetupDetailsOpen(false);
      setPanelsOpen(false);
      setDrawerTab("room");
      setShowCinematicIntro(false);
      setSceneTextOpen(false);
      setSceneTextDraft("");
      window.history.replaceState(null, "", simulationUrl(created.simulation_id, advisorMode, auditoriumMode));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "failed to start simulation from setup");
    } finally {
      if (setupLaunchInFlightRef.current === activeSession.session_id) {
        setupLaunchInFlightRef.current = null;
      }
      setLaunchingSetup(false);
    }
  }

  async function handleSetupComposerSend(override?: string) {
    const next = (override ?? setupPromptDraft).trim();
    if (!next || !setupSession || launchingSetup) {
      return;
    }
    setSetupPromptDraft("");
    await setupRealtime.sendText(next);
  }

  async function handleSetupVoiceToggle() {
    if (setupBooting || launchingSetup) {
      return;
    }
    try {
      setError(null);
      await setupRealtime.toggleVoiceCapture();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Microphone capture failed. Try again, or use the text box below.");
      queueAfterPaint(() => {
        setupComposerRef.current?.focus();
      });
    }
  }

  async function handleQueuePoll() {
    if (!simulation || !manualPollQuestion.trim()) {
      return;
    }
    setError(null);
    try {
      const updated = await queuePoll(simulation.simulation_id, manualPollQuestion);
      setSimulation(updated);
      setManualPollQuestion("");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "failed to queue poll");
    }
  }

  async function handleRunPolls() {
    if (!simulation) {
      return;
    }
    setError(null);
    try {
      const response = await runPolls(simulation.simulation_id);
      setSimulation(response.simulation);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "failed to run polls");
    }
  }

  async function handleResolveStage(playerPlatform: string, playerRebuttal: string) {
    if (!simulation || resolvingStage) {
      return;
    }
    const previousRoom = room;
    const previousStageGate = stageGate;
    const previousPanelsOpen = panelsOpen;
    const previousIntroState = showCinematicIntro;
    setResolvingStage(true);
    setError(null);
    disconnectLiveChannels();
    setStageGate("loading");
    setRoom("briefing");
    setPanelsOpen(false);
    setSceneTextOpen(false);
    setShowCinematicIntro(false);
    setSimulation((current) =>
      current
        ? {
            ...current,
            current_room: "briefing",
            status: "resolving",
            progress: {
              phase: "resolving",
              label: "Resolving election",
              detail: "Counting the vote and folding the result into the next chapter.",
              percent: 8,
            },
          }
        : current,
    );
    try {
      const updated = await resolveStage(simulation.simulation_id, {
        player_platform: playerPlatform,
        player_rebuttal: playerRebuttal || undefined,
      });
      setSimulation(updated);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "failed to resolve stage");
      setStageGate(previousStageGate);
      setRoom(previousRoom);
      setPanelsOpen(previousPanelsOpen);
      setShowCinematicIntro(previousIntroState);
      setSimulation((current) =>
        current
          ? {
              ...current,
              current_room: previousRoom,
              status: "stage_ready",
            }
          : current,
      );
    } finally {
      setResolvingStage(false);
    }
  }

  function handleSimulationSync(updated: SimulationState) {
    const currentSimulation = simulationRef.current;
    const requestedRoom = honorQueryRequestedRoomRef.current
      ? requestedRoomFromModeQuery(new URLSearchParams(window.location.search)) ?? queryRequestedRoomRef.current
      : null;
    const nextRoom = requestedRoom ?? updated.current_room;
    if (
      currentSimulation &&
      currentSimulation.simulation_id === updated.simulation_id &&
      simulationUpdatedAtMs(updated) < simulationUpdatedAtMs(currentSimulation)
    ) {
      return;
    }
    if (nextRoom !== roomRef.current) {
      disconnectLiveChannels();
    }
    startTransition(() => {
      setSimulation(updated);
      setRoom(nextRoom);
      setShowCinematicIntro(false);
      setStageGate((current) => {
        if (updated.status !== "stage_ready") {
          return "loading";
        }
        if (current === "intro" && nextRoom === "briefing") {
          return "intro";
        }
        if (nextRoom === "briefing" && current !== "live") {
          return "ready";
        }
        return "live";
      });
      if (nextRoom !== "briefing") {
        setSceneTextOpen(false);
      }
      if (updated.focused_citizen_id) {
        setActiveCitizenId(updated.focused_citizen_id);
        setStreetCandidateCitizenId(nextRoom === "citizens" ? updated.focused_citizen_id : undefined);
      }
      if (nextRoom !== "citizens") {
        setStreetCandidateCitizenId(undefined);
      }
    });
    window.history.replaceState(null, "", simulationUrl(updated.simulation_id, advisorMode, auditoriumMode));
  }

  function refreshSimulationSnapshot(delayMs = 1200) {
    const currentSimulation = simulationRef.current;
    if (!currentSimulation) {
      return;
    }
    const snapshotGeneration = refreshSnapshotGenerationRef.current;
    const simulationId = currentSimulation.simulation_id;
    window.setTimeout(() => {
      const latestSimulation = simulationRef.current;
      if (
        snapshotGeneration !== refreshSnapshotGenerationRef.current ||
        !latestSimulation ||
        latestSimulation.simulation_id !== simulationId
      ) {
        return;
      }
      void (async () => {
        try {
          const latest = await getSimulation(simulationId);
          if (
            snapshotGeneration !== refreshSnapshotGenerationRef.current ||
            latest.simulation_id !== simulationId
          ) {
            return;
          }
          handleSimulationSync(latest);
        } catch {
          // Scene-side refresh is best-effort; the live session still owns the conversation.
        }
      })();
    }, delayMs);
  }

  function queuePostTurnRefreshes() {
    refreshSimulationSnapshot(1200);
    refreshSimulationSnapshot(3600);
    if (featurettesPending) {
      refreshSimulationSnapshot(7800);
    }
  }

  function openReelsSurface(requestedFeaturetteId: string | null = null) {
    if (!stage) {
      return;
    }
    if (hasPlayableFeaturettes) {
      const defaultFeaturetteId = stage.featurettes.find((item) => item.status === "ready")?.id ?? null;
      setReelsRequestedFeaturetteId(requestedFeaturetteId ?? defaultFeaturetteId);
      setReelsOpen(true);
      setPanelsOpen(false);
      return;
    }
    setReelsRequestedFeaturetteId(null);
    setReelsOpen(false);
    setDrawerTab("reels");
    setPanelsOpen(true);
  }

  async function toggleFullscreen() {
    if (document.fullscreenElement) {
      await document.exitFullscreen().catch(() => undefined);
      return;
    }
    await document.documentElement.requestFullscreen?.().catch(() => undefined);
  }

  async function handleRoomFocus(
    nextRoom: RoomName,
    citizenId?: string,
    options?: {
      nextAuditoriumMode?: AuditoriumMode;
    },
  ) {
    if (!simulation) {
      return;
    }
    honorQueryRequestedRoomRef.current = false;
    const effectiveAuditoriumMode = options?.nextAuditoriumMode ?? auditoriumMode;
    const focusedCitizenId =
      nextRoom === "citizens"
        ? citizenId ?? candidateCitizen?.citizen_id ?? activeCitizen?.citizen_id ?? simulation.focused_citizen_id ?? citizens[0]?.citizen_id
        : undefined;
    disconnectLiveChannels();
    setShowCinematicIntro(false);
    setStageGate("live");
    setSceneTextOpen(false);
    setSceneTextDraft("");
    if (panelsOpen) {
      setDrawerTab("room");
    }
    if (nextRoom !== "citizens") {
      setStreetCandidateCitizenId(undefined);
    } else if (focusedCitizenId) {
      setStreetCandidateCitizenId(focusedCitizenId);
    }
    try {
      const result = await callRealtimeTool(simulation.simulation_id, "advisor", "move_room_focus", {
        room: nextRoom,
        citizen_id: focusedCitizenId,
      });
      const maybeSimulation = result.data?.simulation as SimulationState | undefined;
      if (maybeSimulation?.simulation_id) {
        handleSimulationSync(maybeSimulation);
      }
      const committedRoom = maybeSimulation?.current_room ?? nextRoom;
      setRoom(committedRoom);
      if (committedRoom === "citizens") {
        const committedCitizenId = maybeSimulation?.focused_citizen_id ?? focusedCitizenId;
        if (committedCitizenId) {
          setActiveCitizenId(committedCitizenId);
          setStreetCandidateCitizenId(committedCitizenId);
        }
      } else {
        setStreetCandidateCitizenId(undefined);
      }
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "failed to move room");
    }
  }

  function handlePresenceChange(key: "advisor" | "citizens" | "debate", presence: ScenePresence) {
    setScenePresence((current) => {
      const previous = current[key];
      if (
        previous.status === presence.status &&
        previous.liveMode === presence.liveMode &&
        previous.muted === presence.muted &&
        previous.playerActivity === presence.playerActivity &&
        previous.counterpartActivity === presence.counterpartActivity &&
        previous.voicePhase === presence.voicePhase
      ) {
        return current;
      }
      return { ...current, [key]: presence };
    });
  }

  function handleLiveCaptionChange(key: "advisor" | "citizens" | "debate", turn: ConversationTurn | null) {
    setLiveSceneCaptions((current) => {
      const previous = current[key];
      if (!previous && !turn) {
        return current;
      }
      if (
        previous &&
        turn &&
        previous.id === turn.id &&
        previous.speaker === turn.speaker &&
        previous.text === turn.text &&
        previous.mode === turn.mode
      ) {
        return current;
      }
      return { ...current, [key]: turn };
    });
  }

  function toggleAdvisorMode() {
    setAdvisorRoomMode(advisorMode === "solo" ? "council" : "solo");
  }

  function setAdvisorRoomMode(nextMode: AdvisorMode) {
    if (nextMode === advisorMode) {
      return;
    }
    honorQueryRequestedRoomRef.current = false;
    advisorDockRef.current?.disconnect();
    handlePresenceChange("advisor", EMPTY_PRESENCE);
    setAdvisorMode(nextMode);
  }

  function toggleAuditoriumMode() {
    setAuditoriumRoomMode(auditoriumMode === "debate" ? "town_hall" : "debate", {
      launchTownHall: auditoriumMode === "debate",
    });
  }

  function setAuditoriumRoomMode(nextMode: AuditoriumMode, options?: { launchTownHall?: boolean }) {
    if (nextMode === auditoriumMode) {
      if (nextMode === "town_hall" && options?.launchTownHall) {
        setTownHallLaunchNonce((current) => current + 1);
      }
      return;
    }
    honorQueryRequestedRoomRef.current = false;
    debateDockRef.current?.disconnect();
    handlePresenceChange("debate", EMPTY_PRESENCE);
    setAuditoriumMode(nextMode);
    if (nextMode === "town_hall" && options?.launchTownHall) {
      setTownHallLaunchNonce((current) => current + 1);
    }
  }

  async function handleModeCommand(command: {
    room?: RoomName;
    advisorMode?: AdvisorMode;
    auditoriumMode?: AuditoriumMode;
    citizenName?: string;
  }) {
    if (!simulation) {
      return false;
    }
    if (command.advisorMode) {
      setAdvisorRoomMode(command.advisorMode);
    }
    if (command.auditoriumMode) {
      setAuditoriumRoomMode(command.auditoriumMode, {
        launchTownHall: command.auditoriumMode === "town_hall",
      });
    }
    if (command.citizenName) {
      try {
        disconnectLiveChannels();
        const result = await callRealtimeTool(simulation.simulation_id, "advisor", "focus_citizen_by_name", {
          citizen_name: command.citizenName,
        });
        const maybeSimulation = result.data?.simulation as SimulationState | undefined;
        if (maybeSimulation?.simulation_id) {
          handleSimulationSync(maybeSimulation);
        }
        return true;
      } catch (caught) {
        setError(caught instanceof Error ? caught.message : "failed to focus citizen");
        return false;
      }
    }
    const targetRoom = command.room ?? (command.advisorMode ? "advisor" : command.auditoriumMode ? "debate" : undefined);
    if (targetRoom) {
      await handleRoomFocus(targetRoom, undefined, { nextAuditoriumMode: command.auditoriumMode });
      return true;
    }
    return Boolean(command.advisorMode || command.auditoriumMode);
  }

  function handleStreetFocusChange(citizenId?: string) {
    if (room !== "citizens" || citizenFocusLocked || streetPendingCitizenId) {
      return;
    }
    setStreetCandidateCitizenId((current) => (current === citizenId ? current : citizenId));
    if (!citizenId || !simulation) {
      return;
    }
    if (simulation.focused_citizen_id === citizenId) {
      setActiveCitizenId((current) => (current === citizenId ? current : citizenId));
    }
  }

  async function persistCitizenFocus(nextCitizenId: string) {
    if (!simulation) {
      throw new Error("simulation is not ready");
    }
    try {
      const result = await callRealtimeTool(simulation.simulation_id, "advisor", "move_room_focus", {
        room: "citizens",
        citizen_id: nextCitizenId,
      });
      const maybeSimulation = result.data?.simulation as SimulationState | undefined;
      if (maybeSimulation?.simulation_id) {
        handleSimulationSync(maybeSimulation);
      }
      return maybeSimulation;
    } catch (caught) {
      const message = caught instanceof Error ? caught.message : "failed to focus citizen";
      setError(message);
      throw new Error(message);
    }
  }

  function dockRefForRoom(targetRoom: RoomName) {
    if (targetRoom === "advisor") {
      return advisorDockRef.current;
    }
    if (targetRoom === "citizens") {
      return citizenDockRef.current;
    }
    if (targetRoom === "debate") {
      return debateDockRef.current;
    }
    return null;
  }

  function dockScopeKeyForRoom(
    targetRoom: RoomName,
    options?: {
      citizenId?: string;
      advisorMode?: AdvisorMode;
      auditoriumMode?: AuditoriumMode;
    },
  ) {
    const simulationKey = simulation?.simulation_id ?? "local";
    if (targetRoom === "advisor") {
      return `${simulationKey}:advisor:${options?.advisorMode ?? advisorMode}`;
    }
    if (targetRoom === "citizens") {
      return `${simulationKey}:citizen:${options?.citizenId ?? activeCitizen?.citizen_id ?? streetCandidateCitizenId ?? "none"}`;
    }
    if (targetRoom === "debate") {
      return `${simulationKey}:debate:${options?.auditoriumMode ?? auditoriumMode}`;
    }
    return `${simulationKey}:${targetRoom}`;
  }

  function disconnectNonCurrentRoomChannels(targetRoom: RoomName) {
    if (targetRoom !== "advisor") {
      advisorDockRef.current?.disconnect();
      handlePresenceChange("advisor", EMPTY_PRESENCE);
    }
    if (targetRoom !== "citizens") {
      citizenDockRef.current?.disconnect();
      handlePresenceChange("citizens", EMPTY_PRESENCE);
    }
    if (targetRoom !== "debate") {
      debateDockRef.current?.disconnect();
      handlePresenceChange("debate", EMPTY_PRESENCE);
    }
  }

  async function handOffCitizenAction(nextCitizenId: string, action?: (dock: VoiceDockHandle) => void) {
    const previousActiveCitizenId = activeCitizen?.citizen_id;
    const previousCandidateCitizenId = candidateCitizen?.citizen_id;
    const previousPendingCitizenId = streetPendingCitizenId;
    setStreetPendingCitizenId(nextCitizenId);
    setStreetCandidateCitizenId(nextCitizenId);
    const needsCitizenSwap = nextCitizenId !== activeCitizen?.citizen_id;
    try {
      if (needsCitizenSwap) {
        citizenDockRef.current?.disconnect();
      }
      const updated = await persistCitizenFocus(nextCitizenId);
      const committedCitizenId = updated?.focused_citizen_id ?? nextCitizenId;
      setActiveCitizenId(committedCitizenId);
      setStreetCandidateCitizenId(committedCitizenId);
      setStreetPendingCitizenId(undefined);
      if (action) {
        if (!needsCitizenSwap && committedCitizenId === activeCitizen?.citizen_id) {
          const generation = dockActionGenerationRef.current;
          queueAfterPaint(() => {
            withMountedDock(
              action,
              0,
              generation,
              "citizens",
              dockScopeKeyForRoom("citizens", { citizenId: committedCitizenId }),
            );
          });
        } else {
          pendingCitizenActionRef.current = { citizenId: committedCitizenId, action };
        }
      }
    } catch {
      setActiveCitizenId(previousActiveCitizenId);
      setStreetCandidateCitizenId(previousCandidateCitizenId);
      setStreetPendingCitizenId(previousPendingCitizenId);
      pendingCitizenActionRef.current = null;
    }
  }

  function withMountedDock(
    action: (dock: VoiceDockHandle) => void,
    attempt = 0,
    generation = dockActionGenerationRef.current,
    targetRoom: RoomName = room,
    expectedScopeKey = dockScopeKeyForRoom(targetRoom),
  ) {
    if (attempt === 0) {
      const dock = dockRefForRoom(targetRoom);
      if (dock && dock.getScopeKey() === expectedScopeKey) {
        action(dock);
        return;
      }
    }
    queueAfterPaint(() => {
      if (generation !== dockActionGenerationRef.current) {
        return;
      }
      const dock = dockRefForRoom(targetRoom);
      if (dock && dock.getScopeKey() === expectedScopeKey) {
        action(dock);
        return;
      }
      if (attempt < ROOM_DOCK_RETRY_LIMIT) {
        window.setTimeout(() => {
          withMountedDock(action, attempt + 1, generation, targetRoom, expectedScopeKey);
        }, 120);
      } else {
        setError("The live room did not finish mounting. Try the mic again.");
      }
    });
  }

  function disconnectLiveChannels() {
    dockActionGenerationRef.current += 1;
    refreshSnapshotGenerationRef.current += 1;
    pendingCitizenActionRef.current = null;
    setStreetPendingCitizenId(undefined);
    advisorDockRef.current?.disconnect();
    citizenDockRef.current?.disconnect();
    debateDockRef.current?.disconnect();
    setCouncilFloor(null);
    setScenePresence({
      advisor: EMPTY_PRESENCE,
      citizens: EMPTY_PRESENCE,
      debate: EMPTY_PRESENCE,
    });
  }

  function revealRoomChannel() {
    setPanelsOpen(true);
    setDrawerTab("room");
  }

  function focusCurrentChannel() {
    revealRoomChannel();
    queueAfterPaint(() => {
      dockRefForRoom(room)?.focusComposer();
    });
  }

  async function handOffRoomAction(
    targetRoom: RoomName,
    action: (dock: VoiceDockHandle) => void,
    options?: {
      citizenId?: string;
      advisorMode?: AdvisorMode;
      auditoriumMode?: AuditoriumMode;
    },
  ) {
    if (!simulation) {
      return;
    }
    dockActionGenerationRef.current += 1;
    let generation = dockActionGenerationRef.current;
    const expectedScopeKey = dockScopeKeyForRoom(targetRoom, options);
    if (room !== targetRoom || simulation.current_room !== targetRoom) {
      await handleRoomFocus(targetRoom, options?.citizenId, {
        nextAuditoriumMode: options?.auditoriumMode,
      });
      // Room focus intentionally disconnects old channels, which invalidates
      // pending dock actions. Refresh the token after navigation so the
      // follow-up mic/text action still reaches the newly mounted dock.
      dockActionGenerationRef.current += 1;
      generation = dockActionGenerationRef.current;
    } else {
      disconnectNonCurrentRoomChannels(targetRoom);
      setSceneTextOpen(false);
    }
    withMountedDock(
      action,
      0,
      generation,
      targetRoom,
      expectedScopeKey,
    );
  }

  async function handleEnterWarRoom(options?: { startVoice?: boolean; openChannel?: boolean }) {
    if (!simulation) {
      return room;
    }
    setShowCinematicIntro(false);
    setStageGate("live");
    setSceneTextOpen(false);
    const targetRoom = room !== "briefing" ? room : simulation.current_room !== "briefing" ? simulation.current_room : "advisor";
    if (room !== targetRoom || simulation.current_room === "briefing") {
      await handleRoomFocus(targetRoom);
    }
    if (options?.openChannel) {
      revealRoomChannel();
    }
    if (options?.startVoice) {
      await handOffRoomAction(targetRoom, (dock) => {
        void dock.startOrToggleVoice();
      });
    }
    return targetRoom;
  }

  function handleLaunchStageIntro() {
    if (!simulation || simulation.status !== "stage_ready") {
      return;
    }
    setRoom("briefing");
    setPanelsOpen(false);
    setDrawerTab("room");
    setSceneTextOpen(false);
    setStageGate("intro");
    setShowCinematicIntro(true);
  }

  async function handleSceneTextSend() {
    const next = sceneTextDraft.trim();
    if (!next) {
      return;
    }
    setSceneTextDraft("");
    setSceneTextOpen(false);
    if (showCinematicIntro || room === "briefing") {
      const targetRoom = await handleEnterWarRoom({ openChannel: false });
      withMountedDock((dock) => {
        void dock.sendText(next);
      }, 0, dockActionGenerationRef.current, targetRoom, dockScopeKeyForRoom(targetRoom));
      queuePostTurnRefreshes();
      return;
    }
    if (room === "citizens" && candidateCitizen?.citizen_id && candidateCitizen.citizen_id !== activeCitizen?.citizen_id) {
      await handOffCitizenAction(candidateCitizen.citizen_id, (dock) => {
        void dock.sendText(next);
      });
      queuePostTurnRefreshes();
      return;
    }
    withMountedDock((dock) => {
      void dock.sendText(next);
    });
    queuePostTurnRefreshes();
  }

  async function handleSceneVoiceStart(citizenId?: string) {
    if (!simulation || simulation.status !== "stage_ready") {
      return;
    }
    setSceneTextOpen(false);
    if (showCinematicIntro || room === "briefing") {
      disconnectLiveChannels();
      await handleEnterWarRoom({ openChannel: false, startVoice: true });
      return;
    }
    if (room === "citizens") {
      const nextCitizenId = citizenId ?? candidateCitizen?.citizen_id ?? activeCitizen?.citizen_id;
      if (!nextCitizenId) {
        return;
      }
      await handOffCitizenAction(nextCitizenId, (dock) => {
        void dock.startOrToggleVoice();
      });
      return;
    }
    if (
      room === "debate" &&
      auditoriumMode === "town_hall" &&
      (townHallSceneState?.readyForNextQuestion ?? true) &&
      townHallSceneState?.phase !== "generating"
    ) {
      await debateRoomRef.current?.askTownHallQuestion();
      return;
    }
    await handOffRoomAction(room, (dock) => {
      void dock.startOrToggleVoice();
    });
  }

  async function handleScenePrimaryInteract(citizenId?: string) {
    setSceneTextDraft("");
    if (showCinematicIntro || room === "briefing") {
      await handleEnterWarRoom({ openChannel: false });
      setSceneTextOpen(true);
      return;
    }
    if (room === "citizens") {
      const nextCitizenId = citizenId ?? candidateCitizen?.citizen_id ?? activeCitizen?.citizen_id;
      if (!nextCitizenId) {
        return;
      }
      if (nextCitizenId !== activeCitizen?.citizen_id) {
        await handOffCitizenAction(nextCitizenId);
      }
      setSceneTextOpen(true);
      return;
    }
    setSceneTextOpen(true);
  }

  async function handleSceneHotspotSelect(hotspot: SceneHotspot) {
    if (hotspot.disabled) {
      return;
    }
    if (hotspot.action === "panel") {
      setDrawerTab("room");
      setPanelsOpen((current) => !current);
      return;
    }
    if (hotspot.action === "resolve") {
      await handleResolveStage(debateAdvancePayload, latestDebatePlayerTurn);
      return;
    }
    if (hotspot.action === "townhall") {
      setPanelsOpen(false);
      if (auditoriumMode === "town_hall") {
        setAuditoriumRoomMode("debate");
        return;
      }
      void debateRoomRef.current?.primeTownHallAudio();
      setAuditoriumRoomMode("town_hall", { launchTownHall: true });
      return;
    }
    if (hotspot.action === "advisor_mode") {
      setPanelsOpen(false);
      toggleAdvisorMode();
      return;
    }
    if (hotspot.action === "citizen") {
      setPanelsOpen(false);
      if (room === "citizens" && hotspot.citizenId) {
        await handleStreetFocusChange(hotspot.citizenId);
        return;
      }
      await handleRoomFocus("citizens", hotspot.citizenId);
      return;
    }
    if (hotspot.room) {
      setPanelsOpen(false);
      await handleRoomFocus(hotspot.room, hotspot.citizenId);
    }
  }

  const activePresence =
    room === "advisor" ? scenePresence.advisor : room === "citizens" ? scenePresence.citizens : room === "debate" ? scenePresence.debate : EMPTY_PRESENCE;

  const setupVoiceButtonLabel =
    setupVoiceConnecting
      ? "Cancel joining"
      : setupVoiceConnected
        ? setupRealtime.muted
          ? "Resume"
          : "Pause"
        : "Speak";
  const setupVoiceButtonHint =
    setupVoiceConnecting
      ? "Opening the live chamber"
      : setupTextPreparing
        ? "Preparing the chamber"
      : setupRealtime.presence.voicePhase === "responding" && setupVoiceConnected
        ? "Orchestrator answering"
      : setupVoiceRecording
        ? "Listening"
      : setupVoiceConnected
            ? setupRealtime.muted
              ? "Resume the live chamber"
              : "Pause the live chamber"
            : "Talk to the orchestrator";

  const sceneHotspots = useMemo<SceneHotspot[]>(() => {
    if (!stage) {
      return [];
    }
    if (room === "briefing") {
      return [
        {
          id: "briefing-advisor",
          label: "War Room",
          hint: "Meet your advisor",
          position: [-2.5, 2.08, -3.58],
          tone: "amber",
          action: "room",
          room: "advisor",
        },
        {
          id: "briefing-voices",
          label: "Street",
          hint: "Hear the country",
          position: [2.7, 2.26, -3.55],
          tone: "steel",
          action: "room",
          room: "citizens",
          citizenId: activeCitizen?.citizen_id ?? candidateCitizen?.citizen_id,
        },
        {
          id: "briefing-debate",
          label: "Auditorium",
          hint: "Preview the debate hall",
          position: [0, 1.02, 0.95],
          tone: "rose",
          action: "room",
          room: "debate",
        },
      ];
    }
    if (room === "advisor") {
      return [
        {
          id: "advisor-voices",
          label: "Street",
          hint: "Interview voters directly",
          position: [-5.55, 1.06, -1.12],
          tone: "steel",
          action: "room",
          room: "citizens",
          citizenId: activeCitizen?.citizen_id ?? candidateCitizen?.citizen_id,
        },
        {
          id: "advisor-debate",
          label: "Auditorium",
          hint: "Test your case in public",
          position: [5.55, 1.06, -1.12],
          tone: "rose",
          action: "room",
          room: "debate",
        },
        {
          id: "advisor-mode",
          label: advisorMode === "council" ? "Solo room" : "Council",
          hint: advisorMode === "council" ? "Return to the one-on-one room" : "Open the wider advisory table",
          position: [3.05, 1.46, 0.9],
          tone: "sage",
          action: "advisor_mode",
          active: advisorMode === "council",
        },
      ];
    }
    if (room === "citizens") {
      return [
        {
          id: "citizens-war-room",
          label: "War Room",
          hint: "Return to the advisor",
          position: [-6.85, 1.56, -9.4],
          tone: "amber",
          action: "room",
          room: "advisor",
        },
        {
          id: "citizens-auditorium",
          label: "Auditorium",
          hint: "Go to the debate hall",
          position: [6.85, 1.56, -9.4],
          tone: "rose",
          action: "room",
          room: "debate",
        },
      ];
    }
    return [
      {
        id: "debate-war-room",
        label: "War Room",
        hint: "Go back to strategy",
        position: [-4.45, 1.34, 2.82],
        tone: "amber",
        action: "room",
        room: "advisor",
      },
        {
          id: "debate-voices",
          label: "Street",
          hint: "Return to voters",
          position: [4.45, 1.34, 2.82],
        tone: "steel",
        action: "room",
        room: "citizens",
        citizenId: activeCitizen?.citizen_id ?? candidateCitizen?.citizen_id,
      },
      {
        id: "debate-townhall",
        label: auditoriumMode === "town_hall" ? "Debate stage" : "Town hall",
        hint: auditoriumMode === "town_hall" ? "Return to the candidate exchange" : "Open the live audience floor",
        position: [2.58, 1.28, 1.5],
        tone: "sage",
        action: "townhall",
        active: auditoriumMode === "town_hall",
      },
    ];
  }, [activeCitizen?.citizen_id, advisorMode, auditoriumMode, candidateCitizen?.citizen_id, citizens, resolvingStage, room, stage]);

  const roomDrawer = !stage ? null : (
    <section className="immersive-drawer__room">
      {room === "briefing" ? <BriefingTheater stage={stage} variant="drawer" /> : null}

      {room === "advisor" ? (
        <section className="room-grid immersive-room-grid">
          <VoiceDock
            key={`${simulation?.simulation_id ?? "sim"}:${simulation?.active_stage_index ?? 0}:advisor:${advisorMode}`}
            ref={advisorDockRef}
            scopeKey={`${simulation?.simulation_id ?? "local"}:advisor:${advisorMode}`}
            simulationId={simulation?.simulation_id}
            role="advisor"
            themeMode={themeMode}
            presentation="drawer"
            advisorMode={advisorMode}
            autoResponse={advisorMode !== "council"}
            councilContext={advisorMode === "council" ? councilContext : undefined}
            councilRoster={simulation?.config.council_roster}
            onCouncilFloorChange={advisorMode === "council" ? setCouncilFloor : undefined}
            title={advisorMode === "council" ? `Multi-advisor table · ${stage.phase_label}` : `Advisor desk · ${stage.phase_label}`}
            blurb={
              advisorMode === "council"
                ? (stage.main_split || currentStageRoomBrief)
                : (currentStageRoomBrief || stage.state_of_world)
            }
            draftPlaceholder={
              advisorMode === "council"
                ? `Ask the table where they split on ${stage.main_split || "the live question"}, who should lead, or what belongs on the board.`
                : `Ask what ${stage.capability_frontier_now || "the frontier"} changes, what voters want kept, or which tradeoff binds this stage.`
            }
            emptyStateText={
              advisorMode === "council"
                ? "The table is waiting for the next strategic question."
                : "The advisor room is quiet until you open the next strategic thread."
            }
            turns={advisorTurns}
            metaChips={[stage.phase_label, `${simulation?.approval_rating.toFixed(0)} approval`, advisorMode === "council" ? "multi-advisor voice" : "single-advisor voice"]}
            onSimulationSync={handleSimulationSync}
            onPresenceChange={(presence) => handlePresenceChange("advisor", presence)}
            onLiveCaptionChange={(turn) => handleLiveCaptionChange("advisor", turn)}
            onModeCommand={handleModeCommand}
          />
          <section className={`side-panel side-panel--desk side-panel--theme-${themeMode}`}>
            <div className="side-panel__block">
              <span>Working agenda</span>
              {advisorPolicyNotes.length > 0 ? (
                advisorPolicyNotes.map((note, index) => <p key={note}>{index + 1}. {note}</p>)
              ) : (
                <p>{currentStageRoomBrief || "The room will hold the few planks that survive the argument."}</p>
              )}
            </div>
            <div className="side-panel__block">
              <span>Current pressures</span>
              {stage.tension_points.map((item) => (
                <p key={item}>{item}</p>
              ))}
            </div>
            <div className="side-panel__block">
              <span>Queue manual poll</span>
              <textarea
                rows={3}
                value={manualPollQuestion}
                onChange={(event) => setManualPollQuestion(event.target.value)}
                placeholder={
                  stage.main_split
                    ? `Ask the country where it lands on this split: ${stage.main_split}`
                    : "Ask what benefit people most want kept, what feels unfair, or what they would not forgive you for slowing."
                }
              />
              <div className="side-panel__actions">
                <button className="btn btn--secondary" onClick={handleQueuePoll}>
                  Queue question
                </button>
                <button className="btn btn--ghost" onClick={handleRunPolls}>
                  Run polls now
                </button>
              </div>
              <div className="side-panel__tags">
                {simulation?.queued_poll_questions.map((item) => (
                  <span key={`${item.question}-${item.created_at}`}>{item.question}</span>
                ))}
              </div>
            </div>
          </section>
        </section>
      ) : null}

      {room === "citizens" ? (
        <section className="room-grid room-grid--citizens immersive-room-grid">
          <CitizenGrid citizens={citizens} activeCitizenId={activeCitizen?.citizen_id ?? candidateCitizen?.citizen_id} onSelect={(citizenId) => void handleRoomFocus("citizens", citizenId)} />
          {activeCitizen ? (
            <VoiceDock
              key={activeCitizen.citizen_id}
              ref={citizenDockRef}
              scopeKey={`${simulation?.simulation_id ?? "local"}:citizen:${activeCitizen.citizen_id}`}
              simulationId={simulation?.simulation_id}
              role="citizen"
              themeMode={themeMode}
              presentation="drawer"
              citizenId={activeCitizen.citizen_id}
              title={activeCitizen.display_name}
              blurb={activeCitizen.summary}
              draftPlaceholder={
                activeCitizen.current_update || activeCitizen.current_worries || activeCitizen.recent_ai_moment
                  ? `Ask about ${activeCitizen.current_update || activeCitizen.current_worries || activeCitizen.recent_ai_moment}.`
                  : "Ask about one recent routine, frustration, relief, or hope."
              }
              emptyStateText="Pick up one thread from this person's week and let them answer in their own words."
              turns={citizenTurns}
              metaChips={[activeCitizen.role, activeCitizen.region, activeCitizen.support_label, activeCitizen.voice]}
              onSimulationSync={handleSimulationSync}
              onPresenceChange={(presence) => handlePresenceChange("citizens", presence)}
              onLiveCaptionChange={(turn) => handleLiveCaptionChange("citizens", turn)}
              onModeCommand={handleModeCommand}
            />
          ) : null}
        </section>
      ) : null}

      {room === "debate" ? (
        <DebateRoom
          ref={debateRoomRef}
          key={`${simulation?.simulation_id ?? "sim"}:${simulation?.active_stage_index ?? 0}:debate:${auditoriumMode}`}
          voiceDockRef={debateDockRef}
          simulationId={simulation?.simulation_id}
          themeMode={themeMode}
          stage={stage}
          debateTurns={debateTurns}
          auditoriumTurns={combinedAuditoriumTurns}
          auditoriumMode={auditoriumMode}
          resolvedPlatform={debatePlatform}
          pending={resolvingStage}
          onResolve={handleResolveStage}
          onToggleTownHall={toggleAuditoriumMode}
          onSimulationSync={handleSimulationSync}
          onPresenceChange={(presence) => handlePresenceChange("debate", presence)}
          onModeCommand={handleModeCommand}
          onTownHallStateChange={setTownHallSceneState}
          townHallLaunchNonce={townHallLaunchNonce}
        />
      ) : null}
    </section>
  );

  const intelDrawer = !stage ? null : (
    <>
      <section className="immersive-drawer__intel">
        <div className="immersive-stat">
          <span>{simulation?.progress.label}</span>
          <strong>{simulation?.progress.percent}%</strong>
          <p>{simulation?.progress.detail}</p>
        </div>
        <div className="immersive-stat">
          <span>Incumbent</span>
          <strong>{simulation?.incumbent_name}</strong>
          <p>{simulation?.player_in_power ? "You currently govern." : "You are campaigning from the outside."}</p>
        </div>
        <div className="immersive-stat">
          <span>Approval</span>
          <strong>{simulation?.approval_rating.toFixed(0)}%</strong>
          <p>{stage.tracking.approval.display}</p>
        </div>
        <div className="immersive-stat">
          <span>Population ready</span>
          <strong>{simulation?.persona_count_ready || simulation?.config.persona_count}</strong>
          <p>{stage.phase_label}</p>
        </div>
      </section>

      <section className="immersive-drawer__timeline">
        {Array.from({ length: simulation?.config.stage_count ?? 0 }).map((_, index) => {
          const summary = simulation?.stages[index];
          const active = index === simulation?.active_stage_index;
          return (
            <article key={index} className={`immersive-timeline-card ${active ? "immersive-timeline-card--active" : ""}`}>
              <span>Stage {index + 1}</span>
              <strong>{summary?.title ?? "Pending"}</strong>
              <p>{summary ? `${summary.phase_label} · ${summary.year_label}` : "Queued"}</p>
            </article>
          );
        })}
      </section>

      <section className="immersive-drawer__metrics">
        {metrics.map((metric) => (
          <article key={metric.key}>
            <span>{metric.label}</span>
            <strong>{metric.display}</strong>
          </article>
        ))}
      </section>
    </>
  );

  const reelsDrawer = !stage ? null : (
    <section className="immersive-drawer__reels">
      <header className="immersive-drawer__reels-header">
        <div>
          <span>Future reels</span>
          <strong>Choose what you want to learn about this future.</strong>
          <p>
            {featurettesPending
              ? "The main chapter is live while the side reels keep rendering in the background."
              : readyFeaturetteCount > 0
                ? "Each reel explains one part of the same future in clearer, more concrete detail."
                : "The shelf is still rendering, but the main chapter is already playable."}
          </p>
        </div>
        <div className="immersive-drawer__reels-meta">
          <span>{readyFeaturetteCount} ready</span>
          <span>{featurettesPending ? "rendering more" : "shelf live"}</span>
        </div>
      </header>
      <div className="immersive-drawer__reels-list">
        {displayedFeaturettes.map((featurette) => {
          const ready = featurette.status === "ready" && Boolean(featurette.narrative_beats?.length);
          const questionLabel = featurette.question.trim() || featuretteQuestionLabel(featurette);
          return (
            <button
              key={featurette.id}
              className={`immersive-drawer__reel-card ${
                ready ? "immersive-drawer__reel-card--ready" : "immersive-drawer__reel-card--pending"
              }`}
              onClick={() => {
                if (!ready) {
                  return;
                }
                setReelsRequestedFeaturetteId(featurette.id);
                setReelsOpen(true);
                setPanelsOpen(false);
              }}
              disabled={!ready}
            >
              <span>{featurette.subject}</span>
              <strong>{featurette.title}</strong>
              <p>{questionLabel}</p>
              <small>{ready ? "Open this reel" : "Still rendering"}</small>
            </button>
          );
        })}
        {featurettesPending ? (
          <article className="immersive-drawer__reel-card immersive-drawer__reel-card--pending">
            <span>More on the way</span>
            <strong>Still cutting another reel</strong>
            <p>The main chapter is already live while another side documentary finishes in the background.</p>
            <small>Background render still running</small>
          </article>
        ) : null}
      </div>
      <button
        className="btn btn--secondary"
        onClick={() => {
          openReelsSurface(null);
        }}
      >
        {hasPlayableFeaturettes ? "Open future reels" : "View reel status"}
      </button>
    </section>
  );
  const reelsDrawerOpen = panelsOpen && drawerTab === "reels";

  useEffect(() => {
    setLiveSceneCaptions({
      briefing: null,
      advisor: null,
      citizens: null,
      debate: null,
    });
  }, [simulation?.simulation_id]);

  return (
    <div className={`app-shell ${simulation ? "app-shell--live" : "app-shell--setup"} app-shell--theme-${themeMode}`}>
      <div className="app-shell__glow app-shell__glow--left" />
      <div className="app-shell__glow app-shell__glow--right" />

      {!simulation ? (
        <main className="immersive-stage immersive-stage--setup">
          <section className="immersive-stage__world" style={{ position: "relative" }}>
            <SetupRoomViewport
              session={setupSession}
              themeMode={themeMode}
              themeProfile={themeProfile}
              loading={setupBooting}
              launching={launchingSetup}
              caption={setupCaption}
              detailsOpen={setupDetailsOpen}
            />
            {setupDetailsOpen ? (
              <aside
                className="setup-notes"
                style={{
                  position: "absolute",
                  left: "1.25rem",
                  right: "auto",
                  bottom: "7.25rem",
                  zIndex: 25,
                  width: "min(32rem, calc(100% - 2.5rem))",
                  pointerEvents: "auto",
                  padding: "1rem 1.1rem",
                  borderRadius: "1.2rem",
                  backdropFilter: "blur(18px)",
                  background:
                    themeMode === "light"
                      ? "linear-gradient(180deg, rgba(248, 243, 235, 0.94), rgba(231, 221, 204, 0.9))"
                      : "linear-gradient(180deg, rgba(20, 14, 11, 0.84), rgba(13, 10, 8, 0.9))",
                  border: themeMode === "light" ? "1px solid rgba(145, 114, 82, 0.18)" : "1px solid rgba(230, 197, 148, 0.12)",
                  boxShadow: themeMode === "light" ? "0 18px 42px rgba(118, 88, 56, 0.14)" : "0 22px 60px rgba(0, 0, 0, 0.3)",
                }}
              >
                <div className="setup-notes__grid">
                  <article className="setup-notes__card">
                    <span>Frame</span>
                    <strong>{setupSession?.draft.country || "United States"}</strong>
                    <p>{setupFocusSummary}</p>
                  </article>
                  <article className="setup-notes__card">
                    <span>Launch status</span>
                    <strong>
                      {setupReady ? "Ready to go" : "Still sketching"}
                    </strong>
                    <p>{setupSession?.guidance?.chamber_reply ?? "Talk to the orchestrator, or say go to launch the broad run."}</p>
                  </article>
                </div>

                <div className="setup-notes__grid">
                  <section className="setup-notes__block">
                    <span>Recent adjustments</span>
                    {setupSession?.guidance?.applied_updates?.length ? (
                      setupSession.guidance.applied_updates.slice(0, 4).map((item) => <p key={item}>{item}</p>)
                    ) : (
                      <p>No special steering is locked. The broad representative run is still in frame.</p>
                    )}
                  </section>
                  <section className="setup-notes__block">
                    <span>Open questions</span>
                    {setupSession?.guidance?.open_questions?.length ? (
                      setupSession.guidance.open_questions.slice(0, 4).map((item) => <p key={item}>{item}</p>)
                    ) : (
                      <p>Nothing critical is missing. If you like the broad U.S. setup, just say go.</p>
                    )}
                  </section>
                </div>

                <section className="setup-notes__block">
                  <span>Recent chamber turns</span>
                  {setupRecentTurns.length > 0 ? (
                    setupRecentTurns.map((turn) => (
                      <p key={turn.id}>
                        <strong>{turn.speaker === "user" ? "You" : turn.speaker === "system" ? "System" : "Orchestrator"}:</strong>{" "}
                        {turn.text}
                      </p>
                    ))
                  ) : (
                    <p>The chamber is ready. Ask for a nudge, or tell the orchestrator to start.</p>
                  )}
                </section>
              </aside>
            ) : null}

            <div className="setup-room__controls">
              <button
                className={`scene__utility-button ${setupDetailsOpen ? "scene__utility-button--active" : ""}`}
                onClick={() => setSetupDetailsOpen((current) => !current)}
                disabled={setupBooting || launchingSetup}
                aria-label={setupDetailsOpen ? "Hide chamber notes" : "Show chamber notes"}
                title={setupDetailsOpen ? "Hide chamber notes" : "Show chamber notes"}
              >
                ⋯
              </button>
              <button
                className="scene__utility-button"
                onClick={() => setThemeMode((current) => (current === "light" ? "dark" : "light"))}
                disabled={setupBooting || launchingSetup}
                aria-label={themeMode === "light" ? "Switch to dark mode" : "Switch to light mode"}
                title={themeMode === "light" ? "Switch to dark mode" : "Switch to light mode"}
              >
                {themeMode === "light" ? "◐" : "◑"}
              </button>
              <button
                className="scene__utility-button scene__utility-button--launch"
                onClick={() => void handleRestart()}
                disabled={setupBooting || launchingSetup}
                aria-label="Restart setup"
                title="Restart setup"
              >
                ↺
              </button>
            </div>

            <div className="scene__channel-bar scene__channel-bar--setup setup-room__channel-bar">
              <button
                className={`scene__voice-trigger ${
                  setupVoiceLive ? "scene__voice-trigger--live" : ""
                } ${
                  setupVoiceConnected && setupRealtime.muted ? "scene__voice-trigger--muted" : ""
                }`}
                onClick={handleSetupVoiceToggle}
                disabled={setupBooting || launchingSetup}
              >
                <span className="scene__voice-trigger-icon" aria-hidden="true">
                  {setupVoiceLive ? "●" : setupVoiceConnecting ? "◌" : "○"}
                </span>
                <span className="scene__voice-trigger-copy">
                  <strong>{setupVoiceButtonLabel}</strong>
                  <small>{setupVoiceButtonHint}</small>
                </span>
              </button>

              <form
                className="scene__inline-composer"
                onSubmit={(event) => {
                  event.preventDefault();
                  void handleSetupComposerSend();
                }}
              >
                <input
                  ref={setupComposerRef}
                  type="text"
                  value={setupComposerValue}
                  onChange={(event) => setSetupPromptDraft(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter") {
                      event.preventDefault();
                      void handleSetupComposerSend();
                    }
                  }}
                  disabled={setupBooting || launchingSetup || setupTextPreparing}
                  placeholder="Tell the orchestrator what world, institution, or future to examine, or just say go."
                />
                <button
                  type="submit"
                  disabled={!setupPromptDraft.trim() || setupBooting || launchingSetup || setupTextPreparing}
                >
                  {setupRealtime.status === "connecting" && setupRealtime.liveMode === "text" ? "Sending…" : "Send"}
                </button>
              </form>
              <button
                className={`scene__launch-button scene__launch-button--inline ${setupReady ? "scene__launch-button--ready" : ""}`}
                onClick={() => void handleStartFromSetup()}
                disabled={!setupReady || setupBooting || launchingSetup}
              >
                {launchingSetup ? "Launching…" : "Launch"}
              </button>
            </div>
            {error ? (
              <p
                className="setup-room__error"
                style={{
                  position: "absolute",
                  left: "50%",
                  bottom: "5.2rem",
                  transform: "translateX(-50%)",
                  zIndex: 29,
                  pointerEvents: "none",
                }}
              >
                {error}
              </p>
            ) : null}
          </section>
        </main>
      ) : (
        <main className="immersive-stage">
          {showLoadingStage ? (
            <section
              className={`loading-stage loading-stage--immersive ${
                readyForNextEra ? "loading-stage--ready" : resolvingStage ? "loading-stage--resolving" : ""
              }`}
              style={
                {
                  ["--loading-accent" as string]: themeProfile.loadingTone,
                  ["--loading-fill" as string]: themeProfile.fill,
                  ["--loading-halo" as string]: themeProfile.halo,
                } as CSSProperties
              }
            >
              <div className="loading-stage__hero">
                <div className={`loading-stage__spinner ${readyForNextEra ? "loading-stage__spinner--ready" : ""}`} />
                <div className="loading-stage__heading">
                  <span className="loading-stage__eyebrow">
                    {loadingEyebrow}
                  </span>
                  <h2>
                    {resolvingStage ? "The country is choosing" : readyForNextEra ? (stage?.title ?? "A new chapter is waiting") : "The next chapter is taking shape"}
                  </h2>
                  <p>
                    {resolvingStage
                      ? "The public choice is being counted and folded into the next chapter."
                      : readyForNextEra
                      ? "The next documentary is ready. Launch it when your group is ready to step forward."
                      : simulation.progress.detail}
                  </p>
                </div>
              </div>
              {!readyForNextEra && !resolvingStage ? (
                <div className="loading-stage__bar">
                  <div style={{ width: `${simulation.progress.percent}%` }} />
                </div>
              ) : null}
              <div className="loading-stage__spotlight">
                <div
                  key={`${loadingQuoteIndex}-${loadingDeck[loadingQuoteIndex]?.kind ?? "note"}`}
                  className={`loading-stage__flash ${readyForNextEra ? "loading-stage__flash--ready" : ""} ${
                    loadingDeck[loadingQuoteIndex]?.kind === "quote" ? "loading-stage__flash--quote" : "loading-stage__flash--note"
                  }`}
                >
                  {loadingDeck[loadingQuoteIndex]?.kind === "quote" ? null : (
                    <span className="loading-stage__flash-label">{loadingDeck[loadingQuoteIndex]?.label ?? "Transition"}</span>
                  )}
                  <p>{loadingDeck[loadingQuoteIndex]?.text ?? "The next chapter is being assembled."}</p>
                  {loadingDeck[loadingQuoteIndex]?.kind === "quote" && loadingDeck[loadingQuoteIndex]?.attribution ? (
                    <small className="loading-stage__flash-attribution">— {loadingDeck[loadingQuoteIndex]?.attribution}</small>
                  ) : null}
                </div>
                {readyForNextEra ? (
                  <div className="loading-stage__ready loading-stage__ready--hero">
                    <button className="btn btn--primary loading-stage__launch" onClick={handleLaunchStageIntro}>
                      Begin the chapter reel
                    </button>
                    <p>The reel waits on your cue.</p>
                  </div>
                ) : null}
              </div>
              <div className="loading-stage__footer">
                <div className="loading-stage__cards">
                  {visibleLoadingHighlights.map((item) => (
                    <article key={item.label} className="loading-stage__card">
                      <span>{item.label}</span>
                      <strong>{item.value}</strong>
                      <p>{item.detail}</p>
                    </article>
                  ))}
                </div>
                {error ? <p className="stage-error">{error}</p> : null}
              </div>
            </section>
          ) : stage ? (
            <>
              <section className="immersive-stage__world">
                <Suspense fallback={<section className="scene scene--loading immersive-stage__scene" />}>
                  <SceneViewport
                    key={`${simulation.simulation_id}:${stage.index}`}
                    room={room}
                    advisorMode={advisorMode}
                    auditoriumMode={auditoriumMode}
                    stage={stage}
                    playerName={simulation.config.player_name}
                    councilRoster={simulation.config.council_roster}
                    themeProfile={themeProfile}
                    playerInPower={simulation.player_in_power}
                    citizens={citizens}
                    activeCitizen={activeCitizen}
                    previewCitizen={candidateCitizen}
                    advisorNotes={advisorPolicyNotes}
                    debateNotes={debateBoardNotes}
                    presence={activePresence}
                    councilFloorLead={advisorMode === "council" ? councilFloor?.lead : undefined}
                    councilFloorOwner={advisorMode === "council" ? councilFloor?.owner : undefined}
                    councilFloorContrast={advisorMode === "council" ? councilFloor?.contrast : undefined}
                    townHallState={
                      room === "debate" && auditoriumMode === "town_hall"
                        ? {
                            label: townHallSceneState?.label ?? "Town hall floor",
                            detail: townHallSceneState?.detail ?? "A voter is about to step up.",
                            speaker: townHallSceneState?.question?.displayName,
                            question: townHallSceneState?.question?.question,
                            active: townHallSceneState?.awaitingPlayer ?? false,
                            readyForNextQuestion: townHallSceneState?.readyForNextQuestion ?? true,
                            phase: townHallSceneState?.phase,
                            error: townHallSceneState?.error ?? null,
                          }
                        : undefined
                    }
                    resolvingStage={resolvingStage}
                    hotspots={sceneHotspots}
                    panelsOpen={panelsOpen}
                    overlayActive={showCinematicIntro || reelsOpen}
                    themeMode={themeMode}
                    captionSpeaker={currentSceneCaption?.speaker}
                    captionText={currentSceneCaption?.text}
                    textComposerOpen={sceneTextOpen}
                    textComposerDraft={sceneTextDraft}
                    onStreetPreviewChange={(citizenId?: string) => {
                      if (room !== "citizens" || citizenFocusLocked || streetPendingCitizenId) {
                        return;
                      }
                      setStreetCandidateCitizenId(citizenId);
                    }}
                    onHotspotSelect={(hotspot) => void handleSceneHotspotSelect(hotspot)}
                    onPrimaryInteract={(citizenId) => void handleScenePrimaryInteract(citizenId)}
                    onStartVoice={(citizenId) => void handleSceneVoiceStart(citizenId)}
                    onTextComposerToggle={() => setSceneTextOpen((current) => !current)}
                    onTextComposerChange={setSceneTextDraft}
                    onTextComposerSend={() => void handleSceneTextSend()}
                    onStageAdvance={() => void handleResolveStage(debateAdvancePayload, latestDebatePlayerTurn)}
                    stageAdvanceLabel={debateAdvanceLabel}
                    stageAdvanceHint={debateAdvanceHint}
                    stageAdvanceDisabled={debateAdvanceDisabled}
                    onStreetFocusChange={handleStreetFocusChange}
                    onTogglePanels={() => {
                      setDrawerTab("room");
                      setPanelsOpen((current) => !current || drawerTab !== "room");
                    }}
                    detailsOpen={panelsOpen && drawerTab === "room"}
                    onOpenReels={() => openReelsSurface(null)}
                    reelsLabel={
                      featurettesPending
                        ? hasPlayableFeaturettes
                          ? `Reels ${readyFeaturetteCount}`
                          : "Reels soon"
                        : readyFeaturetteCount > 0
                          ? `Reels ${readyFeaturetteCount}`
                          : "Reels"
                    }
                    onToggleTheme={() => setThemeMode((current) => (current === "light" ? "dark" : "light"))}
                    themeLabel={themeMode === "light" ? "Dark" : "Light"}
                    onToggleFullscreen={() => void toggleFullscreen()}
                    fullscreenLabel={isFullscreen ? "Window" : "Fullscreen"}
                    onRestart={() => void handleRestart()}
                  />
                </Suspense>
                {showCinematicIntro ? (
                  <BriefingTheater
                    stage={stage}
                    variant="cinematic"
                    themeProfile={themeProfile}
                    hidden={panelsOpen}
                    onEnterWarRoom={() => void handleEnterWarRoom()}
                  />
                ) : null}
                {simulation.status === "completed" && stage.resolution ? (
                  <section className="final-stage-banner">
                    <span className="final-stage-banner__eyebrow">Final chapter complete</span>
                    <h2>{stage.resolution.winner} carried the last election</h2>
                    <p>{stage.resolution.election_takeaway ?? stage.resolution.public_mandate}</p>
                    <div className="final-stage-banner__meta">
                      <span>{stage.resolution.public_mandate}</span>
                      <span>{stage.phase_label}</span>
                    </div>
                  </section>
                ) : null}
                {reelsOpen && stage ? (
                  <section className="featurette-overlay" role="dialog" aria-modal="true" aria-label="Future reels">
                    <button
                      className="featurette-overlay__backdrop"
                      aria-label="Dismiss future reels"
                      onClick={() => {
                        setReelsOpen(false);
                        setReelsRequestedFeaturetteId(null);
                      }}
                    />
                    <div className="featurette-overlay__panel" ref={reelsOverlayRef} tabIndex={-1}>
                      <button
                        className="btn btn--ghost featurette-overlay__close"
                        data-testid="featurette-overlay-close"
                        ref={reelsCloseButtonRef}
                        onClick={() => {
                          setReelsOpen(false);
                          setReelsRequestedFeaturetteId(null);
                        }}
                      >
                        Close
                      </button>
                      <FeaturetteShelf
                        stage={stage}
                        variant="overlay"
                        requestedFeaturetteId={reelsRequestedFeaturetteId}
                        onRequestedFeaturetteClear={() => setReelsRequestedFeaturetteId(null)}
                        onClose={() => {
                          setReelsOpen(false);
                          setReelsRequestedFeaturetteId(null);
                        }}
                      />
                    </div>
                  </section>
                ) : null}
                </section>

              {!showCinematicIntro ? (
                <section
                  className={`immersive-drawer ${panelsOpen ? "immersive-drawer--open" : ""} ${
                    reelsDrawerOpen ? "immersive-drawer--reels" : ""
                  }`}
                >
                  {panelsOpen ? (
                    <div className="immersive-drawer__rail">
                      <div className="immersive-drawer__handle" />
                      <nav className="room-nav room-nav--immersive">
                        {ROOM_BUTTONS.map((entry) => (
                          <button
                            key={entry.key}
                            className={`room-nav__button ${room === entry.key ? "room-nav__button--active" : ""}`}
                            onClick={() => void handleRoomFocus(entry.key)}
                            disabled={simulation.status !== "stage_ready" && entry.key !== "briefing"}
                          >
                            {entry.label}
                          </button>
                        ))}
                      </nav>
                      <div className="immersive-drawer__tabs">
                        <button
                          className={`immersive-drawer__tab ${drawerTab === "room" ? "immersive-drawer__tab--active" : ""}`}
                          onClick={() => setDrawerTab("room")}
                        >
                          Room
                        </button>
                        <button
                          className={`immersive-drawer__tab ${drawerTab === "intel" ? "immersive-drawer__tab--active" : ""}`}
                          onClick={() => setDrawerTab("intel")}
                        >
                          Intel
                        </button>
                        <button
                          className={`immersive-drawer__tab ${drawerTab === "reels" ? "immersive-drawer__tab--active" : ""}`}
                          onClick={() => setDrawerTab("reels")}
                        >
                          Reels
                        </button>
                      </div>
                      <button className="btn btn--ghost immersive-drawer__toggle" onClick={() => setPanelsOpen(false)}>
                        Close
                      </button>
                    </div>
                  ) : null}

                  <div className={`immersive-drawer__body ${panelsOpen ? "immersive-drawer__body--open" : ""}`}>
                    <div className={`immersive-drawer__pane ${drawerTab === "room" ? "immersive-drawer__pane--active" : ""}`}>
                      {roomDrawer}
                    </div>
                    <div className={`immersive-drawer__pane ${drawerTab === "intel" ? "immersive-drawer__pane--active" : ""}`}>
                      {intelDrawer}
                    </div>
                    <div className={`immersive-drawer__pane ${drawerTab === "reels" ? "immersive-drawer__pane--active" : ""}`}>
                      {!reelsOpen && reelsDrawerOpen ? reelsDrawer : null}
                    </div>
                    {error ? <p className="stage-error">{error}</p> : null}
                  </div>
                </section>
              ) : null}
            </>
          ) : null}
          
        </main>
      )}
    </div>
  );
}
