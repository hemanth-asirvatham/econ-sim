import { lazy, startTransition, Suspense, useEffect, useMemo, useRef, useState, type CSSProperties } from "react";
import { BriefingTheater } from "./components/BriefingTheater";
import { CitizenGrid } from "./components/CitizenGrid";
import { DebateRoom } from "./components/DebateRoom";
import { SetupRoomViewport } from "./components/SetupRoomViewport";
import { VoiceDock, type VoiceDockHandle } from "./components/VoiceDock";
import { useSetupRealtimeSession } from "./hooks/useSetupRealtimeSession";
import type { CouncilTurnContext } from "./lib/council";
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

type DrawerTab = "room" | "intel";
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

const CHAMBER_ATTRIBUTION = "The chamber";

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
  if (advisorMode === "council") {
    params.set("advisor", "council");
  }
  if (auditoriumMode === "town_hall") {
    params.set("auditorium", "town_hall");
  }
  return `?${params.toString()}`;
}

function simulationUpdatedAtMs(simulation?: SimulationState | null) {
  if (!simulation?.updated_at) {
    return 0;
  }
  const parsed = Date.parse(simulation.updated_at);
  return Number.isFinite(parsed) ? parsed : 0;
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
    if (queryValue === "council" || queryValue === "solo") {
      return queryValue;
    }
    return window.localStorage.getItem("econ-sim-advisor-mode") === "council" ? "council" : "solo";
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
  const [manualPollQuestion, setManualPollQuestion] = useState("");
  const [activeCitizenId, setActiveCitizenId] = useState<string | undefined>(undefined);
  const [streetCandidateCitizenId, setStreetCandidateCitizenId] = useState<string | undefined>(undefined);
  const [streetPendingCitizenId, setStreetPendingCitizenId] = useState<string | undefined>(undefined);
  const [resolvingStage, setResolvingStage] = useState(false);
  const [setupDetailsOpen, setSetupDetailsOpen] = useState(false);
  const [panelsOpen, setPanelsOpen] = useState(false);
  const [drawerTab, setDrawerTab] = useState<DrawerTab>("room");
  const [showCinematicIntro, setShowCinematicIntro] = useState(false);
  const [sceneTextOpen, setSceneTextOpen] = useState(false);
  const [sceneTextDraft, setSceneTextDraft] = useState("");
  const [loadingQuoteIndex, setLoadingQuoteIndex] = useState(0);
  const [scenePresence, setScenePresence] = useState<Record<string, ScenePresence>>({
    advisor: EMPTY_PRESENCE,
    citizens: EMPTY_PRESENCE,
    debate: EMPTY_PRESENCE,
  });
  const advisorDockRef = useRef<VoiceDockHandle | null>(null);
  const citizenDockRef = useRef<VoiceDockHandle | null>(null);
  const debateDockRef = useRef<VoiceDockHandle | null>(null);
  const [councilFloor, setCouncilFloor] = useState<{
    lead: string;
    urgencies: Record<string, number>;
    contrast: string[];
    reason?: string;
  } | null>(null);
  const pendingCitizenActionRef = useRef<{ citizenId: string; action: (dock: VoiceDockHandle) => void } | null>(null);
  const dockActionGenerationRef = useRef(0);
  const refreshSnapshotGenerationRef = useRef(0);
  const simulationRef = useRef<SimulationState | null>(null);
  const setupLaunchInFlightRef = useRef<string | null>(null);
  const initialBootRef = useRef(false);
  const setupComposerRef = useRef<HTMLInputElement | null>(null);

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
  const setupVoiceLive = setupRealtime.liveMode === "voice" && setupRealtime.status === "connected";
  const setupVoiceConnected = setupRealtime.liveMode === "voice" && setupRealtime.status === "connected";
  const setupVoiceRecording = setupRealtime.presence.voicePhase === "recording";
  const setupVoiceConnecting = setupRealtime.status === "connecting" && setupRealtime.liveMode === "voice";
  const setupTextPreparing = setupRealtime.status === "connecting" && setupRealtime.liveMode === "text";
  const setupVoiceWaiting =
    setupVoiceConnecting ||
    (setupRealtime.liveMode === "voice" &&
      (setupRealtime.presence.voicePhase === "waiting" || setupRealtime.presence.voicePhase === "responding"));
  const citizens = stage?.sample_citizens ?? [];
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
  const debateThreadKey = simulation ? `stage:${simulation.active_stage_index}:debate` : "";
  const coreDebateTurns = useMemo(
    () => simulation?.conversation_threads[debateThreadKey] ?? [],
    [debateThreadKey, simulation?.conversation_threads],
  );
  const debateTurns = coreDebateTurns;
  const debatePlayerTurns = useMemo(
    () => coreDebateTurns.filter((turn) => turn.speaker === "user").map((turn) => turn.text.trim()).filter(Boolean),
    [coreDebateTurns],
  );
  const debatePlayerCase = useMemo(() => debatePlayerTurns.join("\n\n"), [debatePlayerTurns]);
  const debatePolicyLines = useMemo(() => extractDebatePolicyLines(debatePlayerTurns), [debatePlayerTurns]);
  const latestDebatePlayerTurn = debatePlayerTurns.at(-1) ?? "";
  const debatePlatform = useMemo(
    () =>
      debatePolicyLines.length > 0
        ? debatePolicyLines.join("\n")
        : debatePlayerCase.trim()
          ? debatePlayerCase
        : advisorPolicyNotes.length > 0
          ? advisorPolicyNotes.join("\n")
          : (stage?.suggested_policy_axes ?? []).slice(0, 4).join("\n"),
    [advisorPolicyNotes, debatePlayerCase, debatePolicyLines, stage?.suggested_policy_axes],
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
  const currentSceneTurns = room === "advisor" ? advisorTurns : room === "citizens" ? citizenTurns : room === "debate" ? debateTurns : [];
  const currentSceneCaption = useMemo(() => {
    const visibleTurns = currentSceneTurns.filter((turn) => turn.speaker !== "system");
    if (room === "advisor" && advisorMode === "council") {
      const tail: typeof visibleTurns = [];
      for (let index = visibleTurns.length - 1; index >= 0; index -= 1) {
        const turn = visibleTurns[index];
        if (turn.speaker !== "assistant") {
          break;
        }
        tail.unshift(turn);
        if (tail.length >= 3) {
          break;
        }
      }
      if (tail.length > 1 && tail.every((turn) => turn.speaker_name)) {
        return {
          ...tail[tail.length - 1],
          text: tail.map((turn) => `${turn.speaker_name}: ${turn.text}`).join("\n"),
        };
      }
    }
    return visibleTurns.at(-1);
  }, [advisorMode, currentSceneTurns, room]);
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
    const tutorialQuoteEntries: Array<{ kind: "quote" | "note"; label: string; text: string; attribution?: string }> =
      previousResolvedStage
        ? []
        : [
            {
              kind: "quote",
              label: "Opening frame",
              text: "Start with the frontier: what can AI now do across ordinary computer work, what still needs people, and what part of life just got easier?",
              attribution: CHAMBER_ATTRIBUTION,
            },
            {
              kind: "quote",
              label: "Room loop",
              text: "The loop is simple: hear the reel, shape a short agenda, pressure-test it on the street, then defend it in the debate.",
              attribution: CHAMBER_ATTRIBUTION,
            },
            {
              kind: "quote",
              label: "Street read",
              text: "Use the street for lived reality. Ask what changed in someone’s week before you ask what policy they want.",
              attribution: CHAMBER_ATTRIBUTION,
            },
            {
              kind: "quote",
              label: "Board discipline",
              text: "Keep only a few debate-worthy ideas. If a plank cannot be said plainly out loud, cut it.",
              attribution: CHAMBER_ATTRIBUTION,
            },
            {
              kind: "quote",
              label: "Vote test",
              text: "The debate is the vote test. People judge what you defend, what you protect, and whether you admit a real limit.",
              attribution: CHAMBER_ATTRIBUTION,
            },
            {
              kind: "quote",
              label: "Keep the gains",
              text: "Do not only ask what to slow down. Ask what benefit people already use and would hate to lose.",
              attribution: CHAMBER_ATTRIBUTION,
            },
            {
              kind: "quote",
              label: "Macro first",
              text: "Do not get trapped in office friction. Look for what got cheaper, broader, smarter, or newly possible across the economy and daily life.",
              attribution: CHAMBER_ATTRIBUTION,
            },
          ];
    const tutorialNoteEntries: Array<{ kind: "quote" | "note"; label: string; text: string; attribution?: string }> =
      previousResolvedStage
        ? []
        : [
            {
              kind: "note",
              label: "How play works",
              text: "Hear the reel, workshop a short agenda with advisors, test it against citizens, debate, then face the vote.",
            },
            {
              kind: "note",
              label: "Capability first",
              text: "Get clear on what AI can now do on a computer, what still needs people, and what still gets stuck in the physical world.",
            },
            {
              kind: "note",
              label: "What usually wins",
              text: "Voters mostly judge what you defend in the debate, whether it matches lived conditions, and whether you keep the useful gains while handling the strain.",
            },
            {
              kind: "note",
              label: "Good first question",
              text: "A strong opening question is simple: what can AI really do now, what still needs people, and who already wants more of it?",
            },
          ];
    const seenNotes = new Set<string>();
    const pushNote = (label: string, rawText?: string) => {
      const trimmed = sanitizeLoadingQuote(rawText ?? "", 132);
      if (!trimmed || seenNotes.has(`${label}:${trimmed}`)) {
        return;
      }
      seenNotes.add(`${label}:${trimmed}`);
      noteEntries.push({ kind: "note", label, text: trimmed });
    };
    pushNote("Capability now", sourceStage?.capability_frontier_now);
    for (const indicator of sourceStage?.economic_indicators.slice(0, 2) ?? []) {
      pushNote("Economic read", indicator);
    }
    pushNote("Visible gain", sourceStage?.dominant_upside);
    pushNote("Still hard", sourceStage?.still_hard_now);
    pushNote("Physical bottleneck", sourceStage?.physical_world_status);
    pushNote("Main split", sourceStage?.main_split);
    for (const quote of loadingVoiceStrips) {
      if (quote.text) {
        quoteEntries.push({
          kind: "quote",
          label: "Voice from the country",
          text: quote.text,
          attribution: quote.attribution,
        });
      }
    }
    const allQuoteEntries = quoteEntries.length > 0 ? [...quoteEntries, ...tutorialQuoteEntries] : [...tutorialQuoteEntries, ...quoteEntries];
    const allNoteEntries = [...tutorialNoteEntries, ...noteEntries];
    if (allQuoteEntries.length === 0 && allNoteEntries.length === 0) {
      quoteEntries.push(
        {
          kind: "quote",
          label: "Game guide",
          text: "Start broad: what can AI reliably do now, what still needs people, and which gains people would fight to keep.",
        },
        {
          kind: "quote",
          label: "Game guide",
          text: "Use the advisor room to shape only a few defendable ideas, then use the street to test them against lived reality.",
        },
        {
          kind: "quote",
          label: "Game guide",
          text: "The debate decides the election, so keep one gain, one strain, and one honest limit in view.",
        },
        {
          kind: "note",
          label: "Macro first",
          text: "Keep one eye on the whole country: what AI can now do, what stayed expensive or scarce, and why adoption is still spreading anyway.",
        },
        {
          kind: "quote",
          label: "Game guide",
          text: "Do not assume every pressure needs a brake. Sometimes the right move is to widen a gain, watch one signal, and wait.",
        },
      );
      noteEntries.push(
        {
          kind: "note",
          label: "How play works",
          text: "Hear the reel, workshop a short agenda with advisors, test it against citizens, return if needed, debate, then face the vote.",
        },
        {
          kind: "note",
          label: "What to ask",
          text: "Good first questions are simple: what can AI really do now, what still needs people, and who already wants more of it?",
        },
        {
          kind: "note",
          label: "Listen wide",
          text: "Some citizens will mainly notice relief, convenience, or not much yet. Others will feel strain first. The country is not one voice.",
        },
        {
          kind: "note",
          label: "Capability check",
          text: "Ask what the systems can now do reliably on a computer, what still needs judgment or trust, and what still gets stuck in the physical world.",
        },
        {
          kind: "note",
          label: "Use the board",
          text: "Use the board to keep a short agenda. If you cannot explain an idea plainly in the debate, it probably does not belong there.",
        },
        {
          kind: "note",
          label: "Hidden upside",
          text: "Ask what people are quietly glad about, not only what they fear. A strong policy often starts by protecting a benefit people do not want to lose.",
        },
        {
          kind: "note",
          label: "Debate test",
          text: "A good platform names one gain to protect, one strain to handle, and one thing you still would not overclaim.",
        },
        {
          kind: "note",
          label: "Poll for surprises",
          text: "Polls are for surprises, not decoration. Ask what people can newly do, what still feels unfair, and what they want kept open.",
        },
        {
          kind: "note",
          label: "Street strategy",
          text: "When you head to the street, ask people what AI is actually doing in their work, bills, school, care, or routines before asking what policy they want.",
        },
        {
          kind: "note",
          label: "How to win",
          text: "Voters mostly judge what you defend in the debate, whether it matches lived conditions, and whether your lane keeps the useful gains while handling the strain.",
        },
        {
          kind: "note",
          label: "Best street question",
          text: "Ask people what AI actually changed in their life this week. The strongest interviews start from one routine, bill, shortcut, or frustration.",
        },
        {
          kind: "note",
          label: "Good skepticism",
          text: "Do not treat every impressive demo as economy-wide change. Ask what is real, what scales, and what still depends on scarce people or infrastructure.",
        },
        {
          kind: "note",
          label: "Capability lens",
          text: "Before you argue about policy, get clear on the frontier: what the tools can actually do, what they still fail at, and who now depends on them.",
        },
      );
    }
    const entries: Array<{ kind: "quote" | "note"; label: string; text: string; attribution?: string }> = [];
    const maxLength = 12;
    const effectiveQuotes = allQuoteEntries.length > 0 ? allQuoteEntries : quoteEntries;
    const effectiveNotes = allNoteEntries.length > 0 ? allNoteEntries : noteEntries;
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
  }, [loadingVoiceStrips, previousResolvedStage, stage]);
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
  const showLiveTopbar = Boolean(simulation && !showCinematicIntro && !showLoadingStage && room === "briefing");
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
          setRoom(loaded.current_room);
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
    setShowCinematicIntro(false);
    setStageGate("ready");
    setPanelsOpen(false);
    setDrawerTab("room");
    setSceneTextOpen(false);
    setSceneTextDraft("");
    setRoom(simulation.current_room);
  }, [simulation, stage, stageKey]);

  useEffect(() => {
    document.documentElement.dataset.theme = themeMode;
    window.localStorage.setItem("econ-sim-theme", themeMode);
  }, [themeMode]);

  useEffect(() => {
    window.localStorage.setItem("econ-sim-advisor-mode", advisorMode);
    window.localStorage.setItem("econ-sim-auditorium-mode", auditoriumMode);
    const url = new URL(window.location.href);
    if (advisorMode === "council") {
      url.searchParams.set("advisor", "council");
    } else {
      url.searchParams.delete("advisor");
    }
    if (auditoriumMode === "town_hall") {
      url.searchParams.set("auditorium", "town_hall");
    } else {
      url.searchParams.delete("auditorium");
    }
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
    if (!simulation || (simulation.status !== "initializing" && simulation.status !== "resolving")) {
      return;
    }
    setStageGate("loading");
    const timer = window.setInterval(async () => {
      try {
        const latest = await getSimulation(simulation.simulation_id);
        startTransition(() => {
          setSimulation(latest);
          setRoom(latest.current_room);
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
    withMountedDock(pending.action, 0, dockActionGenerationRef.current);
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
    if (
      currentSimulation &&
      currentSimulation.simulation_id === updated.simulation_id &&
      simulationUpdatedAtMs(updated) < simulationUpdatedAtMs(currentSimulation)
    ) {
      return;
    }
    if (updated.current_room !== (currentSimulation?.current_room ?? room)) {
      disconnectLiveChannels();
    }
    startTransition(() => {
      setSimulation(updated);
      setRoom(updated.current_room);
      setShowCinematicIntro(false);
      setStageGate((current) => {
        if (updated.status !== "stage_ready") {
          return "loading";
        }
        if (current === "intro" && updated.current_room === "briefing") {
          return "intro";
        }
        if (updated.current_room === "briefing" && current !== "live") {
          return "ready";
        }
        return "live";
      });
      if (updated.current_room !== "briefing") {
        setSceneTextOpen(false);
      }
      if (updated.focused_citizen_id) {
        setActiveCitizenId(updated.focused_citizen_id);
        setStreetCandidateCitizenId(updated.current_room === "citizens" ? updated.focused_citizen_id : undefined);
      }
      if (updated.current_room !== "citizens") {
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

  async function handleRoomFocus(nextRoom: RoomName, citizenId?: string) {
    if (!simulation) {
      return;
    }
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

  function toggleAdvisorMode() {
    advisorDockRef.current?.disconnect();
    handlePresenceChange("advisor", EMPTY_PRESENCE);
    setAdvisorMode((current) => (current === "solo" ? "council" : "solo"));
  }

  function toggleAuditoriumMode() {
    debateDockRef.current?.disconnect();
    handlePresenceChange("debate", EMPTY_PRESENCE);
    setAuditoriumMode((current) => (current === "debate" ? "town_hall" : "debate"));
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

  function currentDockRef() {
    if (room === "advisor") {
      return advisorDockRef.current;
    }
    if (room === "citizens") {
      return citizenDockRef.current;
    }
    if (room === "debate") {
      return debateDockRef.current;
    }
    return null;
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
            withMountedDock(action, 0, generation);
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

  function withMountedDock(action: (dock: VoiceDockHandle) => void, attempt = 0, generation = dockActionGenerationRef.current) {
    queueAfterPaint(() => {
      if (generation !== dockActionGenerationRef.current) {
        return;
      }
      const dock = currentDockRef();
      if (dock) {
        action(dock);
        return;
      }
      if (attempt < 3) {
        window.setTimeout(() => {
          withMountedDock(action, attempt + 1, generation);
        }, 120);
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

  function focusCurrentChannel() {
    setPanelsOpen(true);
    setDrawerTab("room");
    queueAfterPaint(() => {
      currentDockRef()?.focusComposer();
    });
  }

  function toggleCurrentRoomVoice() {
    const dock = currentDockRef();
    if (!dock) {
      return;
    }
    void dock.toggleVoiceCapture();
  }

  async function handleEnterWarRoom(options?: { startVoice?: boolean }) {
    if (!simulation) {
      return;
    }
    setShowCinematicIntro(false);
    setStageGate("live");
    setSceneTextOpen(false);
    if (room !== "advisor" || simulation.current_room === "briefing") {
      await handleRoomFocus("advisor");
    }
    if (options?.startVoice) {
      withMountedDock((dock) => {
        void dock.enableVoice();
      });
    }
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
      await handleEnterWarRoom();
      withMountedDock((dock) => {
        void dock.sendText(next);
      });
      refreshSimulationSnapshot(1400);
      refreshSimulationSnapshot(4200);
      refreshSimulationSnapshot(9500);
      return;
    }
    if (room === "citizens" && candidateCitizen?.citizen_id && candidateCitizen.citizen_id !== activeCitizen?.citizen_id) {
      await handOffCitizenAction(candidateCitizen.citizen_id, (dock) => {
        void dock.sendText(next);
      });
      refreshSimulationSnapshot(1400);
      refreshSimulationSnapshot(4200);
      refreshSimulationSnapshot(9500);
      return;
    }
    withMountedDock((dock) => {
      void dock.sendText(next);
    });
    refreshSimulationSnapshot(1400);
    refreshSimulationSnapshot(4200);
    refreshSimulationSnapshot(9500);
  }

  async function handleSceneVoiceStart(citizenId?: string) {
    if (!simulation || simulation.status !== "stage_ready") {
      return;
    }
    setSceneTextOpen(false);
    if (showCinematicIntro || room === "briefing") {
      disconnectLiveChannels();
      await handleEnterWarRoom({ startVoice: true });
      return;
    }
    if (room === "citizens") {
      const nextCitizenId = citizenId ?? candidateCitizen?.citizen_id ?? activeCitizen?.citizen_id;
      if (!nextCitizenId) {
        return;
      }
      const reusingCurrentCitizen = nextCitizenId === activeCitizen?.citizen_id;
      await handOffCitizenAction(nextCitizenId, (dock) => {
        if (reusingCurrentCitizen && scenePresence.citizens.status === "connected" && scenePresence.citizens.liveMode === "voice") {
          void dock.toggleVoiceCapture();
          return;
        }
        void dock.toggleVoiceCapture();
      });
      return;
    }
    withMountedDock(() => {
      toggleCurrentRoomVoice();
    });
  }

  async function handleScenePrimaryInteract(citizenId?: string) {
    setSceneTextOpen(false);
    if (showCinematicIntro || room === "briefing") {
      await handleEnterWarRoom();
      return;
    }
    if (room === "citizens") {
      const nextCitizenId = citizenId ?? candidateCitizen?.citizen_id ?? activeCitizen?.citizen_id;
      if (!nextCitizenId) {
        return;
      }
      await handOffCitizenAction(nextCitizenId, (dock) => {
        dock.focusComposer();
      });
      return;
    }
    withMountedDock(() => {
      toggleCurrentRoomVoice();
    });
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
      if (!debatePlatform.trim()) {
        setError("Give the room at least one clear proposal before you call the vote.");
        setPanelsOpen(true);
        setDrawerTab("room");
        return;
      }
      await handleResolveStage(debatePlatform, latestDebatePlayerTurn);
      return;
    }
    if (hotspot.action === "townhall") {
      setDrawerTab("room");
      setPanelsOpen(true);
      toggleAuditoriumMode();
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
  const advisorModeToggleLabel = advisorMode === "solo" ? "Council room" : "Solo room";
  const auditoriumModeToggleLabel = auditoriumMode === "town_hall" ? "Main debate" : "Town hall";

  const setupVoiceButtonLabel =
    setupVoiceConnecting
      ? "Joining…"
      : setupVoiceConnected
        ? "Stop"
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
            ? "Stop the live chamber"
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
          label: advisorMode === "council" ? "Solo advisor" : "Council table",
          hint: advisorMode === "council" ? "Return to one-on-one live counsel" : "Open the wider advisory room",
          position: [0, 1.88, 2.12],
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
        label: auditoriumMode === "town_hall" ? "Main debate" : "Town hall questions",
        hint: auditoriumMode === "town_hall" ? "Return to candidate exchange" : "Open the audience floor",
        position: [0, 1.96, 2.26],
        tone: "sage",
        action: "townhall",
        active: auditoriumMode === "town_hall",
      },
        {
          id: "debate-resolve",
          label: resolvingStage ? "Counting election" : "Call election",
          hint: resolvingStage ? "Election is being counted" : "Lock the election and advance",
          position: [0, 0.42, 6.15],
          tone: "sage",
          action: "resolve",
          disabled: resolvingStage,
        },
    ];
  }, [activeCitizen?.citizen_id, advisorMode, auditoriumMode, candidateCitizen?.citizen_id, citizens, resolvingStage, room, stage]);

  const roomDrawer = !stage ? null : (
    <section className="immersive-drawer__room">
      {room === "briefing" ? <BriefingTheater stage={stage} variant="drawer" /> : null}

      {room === "advisor" ? (
        <section className="room-grid immersive-room-grid">
          <VoiceDock
            ref={advisorDockRef}
            simulationId={simulation?.simulation_id}
            role="advisor"
            advisorMode={advisorMode}
            councilContext={advisorMode === "council" ? councilContext : undefined}
            onCouncilFloorChange={advisorMode === "council" ? setCouncilFloor : undefined}
            title={advisorMode === "council" ? "Advisory council chair" : "Chief economic advisor"}
            blurb={
              advisorMode === "council"
                ? "A broader strategy table. The live chair now speaks from a four-advisor council visual, synthesizing capability, households, coalition, and state capacity into one conversation."
                : "Interrogate the transition, queue polling, ask who to interview, and use voice or text to move through the stage."
            }
            turns={advisorTurns}
            metaChips={[stage.phase_label, `${simulation?.approval_rating.toFixed(0)} approval`, advisorMode === "council" ? "council voice" : "advisor voice"]}
            onSimulationSync={handleSimulationSync}
            onPresenceChange={(presence) => handlePresenceChange("advisor", presence)}
          />
          <section className="side-panel side-panel--desk">
            <div className="side-panel__block">
              <span>Advisor mode</span>
              <p>
                {advisorMode === "council"
                  ? "Council room uses the multi-advisor panel. Switch back for the single live advisor."
                  : "Solo room keeps the single live advisor. Switch over for the internal council table."}
              </p>
              <div className="side-panel__actions">
                <button className="btn btn--ghost" onClick={toggleAdvisorMode}>
                  {advisorMode === "council" ? "Go to solo advisor" : "Go to council room"}
                </button>
              </div>
            </div>
            <div className="side-panel__block">
              <span>Working agenda</span>
              {advisorPolicyNotes.length > 0 ? (
                advisorPolicyNotes.map((note, index) => <p key={note}>{index + 1}. {note}</p>)
              ) : (
                <p>Talk through options with your advisor and the working slate will settle here.</p>
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
                placeholder="What benefits do people most want kept? Which frictions feel most unfair? What would they never forgive you for throttling?"
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
              simulationId={simulation?.simulation_id}
              role="citizen"
              citizenId={activeCitizen.citizen_id}
              title={activeCitizen.display_name}
              blurb={activeCitizen.summary}
              turns={citizenTurns}
              metaChips={[activeCitizen.role, activeCitizen.region, activeCitizen.support_label, activeCitizen.voice]}
              onSimulationSync={handleSimulationSync}
              onPresenceChange={(presence) => handlePresenceChange("citizens", presence)}
            />
          ) : null}
        </section>
      ) : null}

      {room === "debate" ? (
        <DebateRoom
          voiceDockRef={debateDockRef}
          simulationId={simulation?.simulation_id}
          stage={stage}
          debateTurns={debateTurns}
          auditoriumMode={auditoriumMode}
          resolvedPlatform={debatePlatform}
          pending={resolvingStage}
          onResolve={handleResolveStage}
          onToggleTownHall={toggleAuditoriumMode}
          onSimulationSync={handleSimulationSync}
          onPresenceChange={(presence) => handlePresenceChange("debate", presence)}
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

  return (
    <div className={`app-shell ${simulation ? "app-shell--live" : "app-shell--setup"} app-shell--theme-${themeMode}`}>
      <div className="app-shell__glow app-shell__glow--left" />
      <div className="app-shell__glow app-shell__glow--right" />

      {showLiveTopbar ? (
        <header className="topbar topbar--live">
          <div className="topbar__actions">
            {room === "advisor" && !showLoadingStage && !showCinematicIntro ? (
              <button className="btn btn--ghost" onClick={toggleAdvisorMode}>
                {advisorModeToggleLabel}
              </button>
            ) : null}
            {room === "debate" && !showLoadingStage && !showCinematicIntro ? (
              <button className="btn btn--ghost" onClick={toggleAuditoriumMode}>
                {auditoriumModeToggleLabel}
              </button>
            ) : null}
            <button className="btn btn--ghost" onClick={() => setThemeMode((current) => (current === "light" ? "dark" : "light"))}>
              {themeMode === "light" ? "Dark mode" : "Light mode"}
            </button>
            <button className="btn btn--primary" onClick={() => void handleRestart()} disabled={setupBooting || launchingSetup}>
              New setup
            </button>
          </div>
        </header>
      ) : null}

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
                  disabled={setupBooting || launchingSetup}
                  placeholder="Talk to the orchestrator. Change country, scale, lens, style, or just say go."
                />
                <button
                  type="submit"
                  disabled={!setupPromptDraft.trim() || setupBooting || launchingSetup}
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
                    stage={stage}
                    themeProfile={themeProfile}
                    playerInPower={simulation.player_in_power}
                    citizens={citizens}
                    activeCitizen={activeCitizen}
                    previewCitizen={candidateCitizen}
                    advisorNotes={advisorPolicyNotes}
                    debateNotes={debateBoardNotes}
                    presence={activePresence}
                    councilFloorLead={advisorMode === "council" ? councilFloor?.lead : undefined}
                    councilUrgencies={advisorMode === "council" ? councilFloor?.urgencies : undefined}
                    resolvingStage={resolvingStage}
                    hotspots={sceneHotspots}
                    panelsOpen={panelsOpen}
                    overlayActive={showCinematicIntro}
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
                    onStreetFocusChange={handleStreetFocusChange}
                    onTogglePanels={undefined}
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
                </section>

              {!showCinematicIntro ? (
                <section className={`immersive-drawer ${panelsOpen ? "immersive-drawer--open" : ""}`}>
                  <div className="immersive-drawer__rail">
                    {panelsOpen ? (
                      <>
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
                        </div>
                        <button className="btn btn--ghost immersive-drawer__toggle" onClick={() => setPanelsOpen(false)}>
                          Close
                        </button>
                      </>
                    ) : (
                      <button
                        className="btn btn--ghost immersive-drawer__toggle"
                        onClick={() => {
                          setDrawerTab("intel");
                          setPanelsOpen(true);
                        }}
                      >
                        Intel
                      </button>
                    )}
                  </div>

                  <div className={`immersive-drawer__body ${panelsOpen ? "immersive-drawer__body--open" : ""}`}>
                    <div className={`immersive-drawer__pane ${drawerTab === "room" ? "immersive-drawer__pane--active" : ""}`}>
                      {roomDrawer}
                    </div>
                    <div className={`immersive-drawer__pane ${drawerTab === "intel" ? "immersive-drawer__pane--active" : ""}`}>
                      {intelDrawer}
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
