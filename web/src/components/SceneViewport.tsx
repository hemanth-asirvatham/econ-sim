import { Canvas, useFrame, useThree, type ThreeEvent } from "@react-three/fiber";
import { ContactShadows, Float, Html, PerspectiveCamera, RoundedBox, Sparkles } from "@react-three/drei";
import { type FormEvent, type MutableRefObject, useEffect, useMemo, useRef, useState } from "react";
import * as THREE from "three";
import { normalizeCouncilRoster, parseCouncilCaption, splitCouncilLines } from "../lib/council";
import { stageConstraint, stageGain, stageRoomBrief, stageSplit, stageWorldOpening } from "../lib/stageText";
import type { CountryThemeProfile } from "../lib/themeProfiles";
import type { AdvisorMode, AuditoriumMode, CitizenSnapshot, CouncilAdvisorProfile, RoomName, SceneHotspot, ScenePresence, StagePackage } from "../types";

interface SceneViewportProps {
  room: RoomName;
  advisorMode?: AdvisorMode;
  auditoriumMode?: AuditoriumMode;
  stage: StagePackage;
  playerName?: string;
  councilRoster?: CouncilAdvisorProfile[];
  themeMode?: "light" | "dark";
  themeProfile?: CountryThemeProfile;
  playerInPower?: boolean;
  citizens?: CitizenSnapshot[];
  activeCitizen?: CitizenSnapshot;
  previewCitizen?: CitizenSnapshot;
  advisorNotes?: string[];
  debateNotes?: string[];
  presence?: ScenePresence;
  councilFloorLead?: string;
  councilFloorOwner?: string;
  councilFloorContrast?: string[];
  townHallState?: {
    label: string;
    detail: string;
    speaker?: string;
    question?: string;
    active?: boolean;
    readyForNextQuestion?: boolean;
    phase?: "idle" | "generating" | "voter_speaking" | "player_turn" | "opponent_turn";
    error?: string | null;
  };
  resolvingStage?: boolean;
  hotspots?: SceneHotspot[];
  panelsOpen?: boolean;
  overlayActive?: boolean;
  captionSpeaker?: "user" | "assistant" | "system";
  captionText?: string;
  onHotspotSelect?: (hotspot: SceneHotspot) => void;
  onPrimaryInteract?: (citizenId?: string) => void;
  onStartVoice?: (citizenId?: string) => void;
  textComposerOpen?: boolean;
  textComposerDraft?: string;
  onTextComposerToggle?: () => void;
  onTextComposerChange?: (value: string) => void;
  onTextComposerSend?: () => void;
  onStageAdvance?: () => void;
  stageAdvanceLabel?: string;
  stageAdvanceHint?: string;
  stageAdvanceDisabled?: boolean;
  onTownHallQuestion?: () => void;
  townHallDisabled?: boolean;
  townHallDisabledReason?: string;
  onTogglePanels?: () => void;
  detailsOpen?: boolean;
  onOpenReels?: () => void;
  reelsLabel?: string;
  onToggleTheme?: () => void;
  themeLabel?: string;
  onToggleFullscreen?: () => void;
  fullscreenLabel?: string;
  onRestart?: () => void | Promise<void>;
  onStreetFocusChange?: (citizenId?: string) => void;
  onStreetPreviewChange?: (citizenId?: string) => void;
}

const DEFAULT_PRESENCE: ScenePresence = {
  status: "idle",
  liveMode: "text",
  muted: false,
  playerActivity: "idle",
  counterpartActivity: "idle",
  voicePhase: "idle",
};

interface StreetPlayerState {
  x: number;
  z: number;
  heading: number;
}

interface StreetPlacement {
  citizen: CitizenSnapshot;
  position: [number, number, number];
  appearance: ReturnType<typeof citizenAppearance>;
}

interface StreetAutoTarget {
  kind: "walk" | "approach";
  x: number;
  z: number;
  heading?: number;
  citizenId?: string;
  citizenPosition?: [number, number, number];
  stopRadius?: number;
  startVoiceOnArrival?: boolean;
}

interface StreetExtra {
  id: string;
  position: [number, number, number];
  appearance: ReturnType<typeof extraStreetAppearance>;
}

interface BoardPanel {
  kicker: string;
  variant?: "stats" | "policy" | "mood";
  headline?: string;
  footerLabel?: string;
  footerText?: string;
  stats?: Array<{
    label: string;
    value: string;
    note?: string;
    detail?: string;
  }>;
  chips?: string[];
  listNumbered?: boolean;
  columns?: Array<{
    id?: string;
    title: string;
    lines: Array<string | { label: string; answer: string; share?: string }>;
  }>;
  list?: string[];
}

const STREET_PLAYER_START: StreetPlayerState = {
  x: 0,
  z: 14.8,
  heading: 0,
};

const STREET_BOUNDS = {
  minX: -7.8,
  maxX: 7.8,
  minZ: -78,
  maxZ: 24.8,
};

const STREET_READY_DISTANCE = 5.8;
const STREET_HIGHLIGHT_DISTANCE = 8.2;
const STREET_FEATURE_DISTANCE = 22.5;
const STREET_EXTRA_MIN_DISTANCE = 9.6;
const STREET_POSE_PUBLISH_INTERVAL_MS = 90;
const STREET_POSE_PUBLISH_DELTA = 0.42;
const STREET_HEADING_PUBLISH_DELTA = 0.12;

const ROOM_CONFIGS: Record<
  RoomName,
  {
    camera: [number, number, number];
    focus: [number, number, number];
    background: string;
    fogNear: number;
    fogFar: number;
    accent: string;
    fill: string;
  }
> = {
  briefing: {
    camera: [0, 2.72, 10.35],
    focus: [0, 1.6, -1.2],
    background: "#17100d",
    fogNear: 9,
    fogFar: 23,
    accent: "#d1a15d",
    fill: "#6ea8b8",
  },
  advisor: {
    camera: [0.08, 2.58, 13.7],
    focus: [0.04, 1.72, -2.48],
    background: "#251c16",
    fogNear: 12,
    fogFar: 27,
    accent: "#c99052",
    fill: "#88b2c0",
  },
  citizens: {
    camera: [0, 2.02, 7.35],
    focus: [0, 1.05, 1.15],
    background: "#23313a",
    fogNear: 26,
    fogFar: 88,
    accent: "#b88458",
    fill: "#8fa9c6",
  },
  debate: {
    camera: [-0.08, 3.42, 15.4],
    focus: [0, 2.42, -3.4],
    background: "#1a1210",
    fogNear: 13,
    fogFar: 28,
    accent: "#d0a06a",
    fill: "#84a9c8",
  },
};

function mixHex(base: string, overlay: string, ratio: number) {
  const source = new THREE.Color(base);
  source.lerp(new THREE.Color(overlay), ratio);
  return `#${source.getHexString()}`;
}

function themedRoomConfig(room: RoomName, themeMode: "light" | "dark", themeProfile?: CountryThemeProfile, advisorMode: AdvisorMode = "solo") {
  const base = ROOM_CONFIGS[room];
  const advisorView =
    room === "advisor" && advisorMode === "council"
      ? {
          camera: [0, 2.92, 12.9] as [number, number, number],
          focus: [0, 1.62, -1.85] as [number, number, number],
          fogNear: 11,
          fogFar: 28,
        }
      : null;
  const accent = themeProfile?.accent ?? base.accent;
  const fill = themeProfile?.fill ?? base.fill;
  if (themeMode === "dark") {
    return {
      ...base,
      ...(advisorView ?? {}),
      accent: mixHex(base.accent, accent, 0.72),
      fill: mixHex(base.fill, fill, 0.68),
      background: themeProfile ? mixHex(base.background, themeProfile.loadingTone, 0.16) : base.background,
    };
  }
  if (room === "advisor") {
    return {
      ...base,
      ...(advisorView ?? {}),
      background: themeProfile ? mixHex("#e5d9cc", themeProfile.wallWarmth, 0.42) : "#e5d9cc",
      accent: mixHex("#bb8758", accent, 0.62),
      fill: mixHex("#87a7b6", fill, 0.44),
    };
  }
  if (room === "citizens") {
    return {
      ...base,
      background: themeProfile ? mixHex("#bdcad0", themeProfile.wallWarmth, 0.24) : "#bdcad0",
      accent: mixHex("#c99667", accent, 0.62),
      fill: mixHex("#86a9bf", fill, 0.62),
      fogNear: 28,
      fogFar: 86,
    };
  }
  if (room === "debate") {
    return {
      ...base,
      background: themeProfile ? mixHex("#e3d6c4", themeProfile.wallWarmth, 0.34) : "#e3d6c4",
      accent: mixHex("#c78b58", accent, 0.62),
      fill: mixHex("#789bb8", fill, 0.62),
    };
  }
  return {
    ...base,
    background: themeProfile ? mixHex("#d6c8b8", themeProfile.wallWarmth, 0.32) : "#d6c8b8",
    accent: mixHex("#cea164", accent, 0.58),
    fill: mixHex("#7aa3b6", fill, 0.58),
  };
}

function roomTitle(room: RoomName, playerInPower = true, advisorMode: AdvisorMode = "solo") {
  switch (room) {
    case "advisor":
      if (advisorMode === "council") {
        return playerInPower ? "Oval office advisory table" : "Campaign advisory table";
      }
      return playerInPower ? "Oval office briefing" : "Campaign war room";
    case "citizens":
      return "Neighborhood street";
    case "debate":
      return "National auditorium";
    default:
      return "Documentary montage";
  }
}

function roomNote(room: RoomName, stage: StagePackage, activeCitizen?: CitizenSnapshot, playerInPower = true, advisorMode: AdvisorMode = "solo") {
  switch (room) {
    case "advisor":
      if (advisorMode === "council") {
        return playerInPower
          ? "The broader advisory table is live, with several senior voices pressing the same decision from different angles in real time."
          : "The campaign advisory table turns strategy into a live argument among competing specialists instead of a single memo from one aide.";
      }
      return playerInPower
        ? "The advisor reads the room for opportunity, backlash, and strategic drift from inside the seat of power."
        : "Your strategist is gaming the next move from a campaign office that feels one step removed from the levers of state.";
    case "citizens":
      return activeCitizen
        ? `${activeCitizen.display_name} is the current interview focus. ${activeCitizen.support_label}.`
        : "Walk the street, stop by people, and hear how the transition lands in actual lives.";
    case "debate":
      return "Lights hot, crowd volatile, and every promise judged against a world changing faster than institutions.";
    default:
      return `${stage.phase_label} is now the governing texture of the world.`;
  }
}

function counterpartPalette(room: RoomName, activeCitizen?: CitizenSnapshot) {
  if (room === "citizens") {
    if (activeCitizen?.approval_band === "approve") {
      return { base: "#6f9d78", glow: "#d5e5c5", metallic: "#86a58a" };
    }
    if (activeCitizen?.approval_band === "disapprove") {
      return { base: "#9f6258", glow: "#f1ccc1", metallic: "#b37a71" };
    }
  }
  if (room === "debate") {
    return { base: "#6b748e", glow: "#d8dfef", metallic: "#8b96b2" };
  }
  if (room === "briefing") {
    return { base: "#85725b", glow: "#f2dfb9", metallic: "#a58d6d" };
  }
  return { base: "#677a8e", glow: "#d4e3ee", metallic: "#7d94aa" };
}

function primaryTargetLabel(room: RoomName, activeCitizen?: CitizenSnapshot, advisorMode: AdvisorMode = "solo") {
  switch (room) {
    case "advisor":
      return advisorMode === "council" ? "Multi-advisor chair" : "Advisor";
    case "citizens":
      return activeCitizen ? `${activeCitizen.display_name} · ${boardSnippet(activeCitizen.role, 28)}` : "Pick someone nearby";
    case "debate":
      return "At the podium";
    default:
      return "Open dossier";
  }
}

function compactSceneMarkerLabel(label: string) {
  const normalized = label.trim().toLowerCase();
  if (normalized.includes("single advisor")) {
    return "Advisor";
  }
  if (normalized.includes("multi-advisor") || normalized.includes("council")) {
    return "Council";
  }
  if (normalized.includes("town hall")) {
    return "Town hall";
  }
  if (normalized.includes("debate")) {
    return "Debate";
  }
  if (normalized.includes("call election") || normalized.includes("counting election")) {
    return "Vote";
  }
  if (normalized.includes("street") || normalized.includes("citizen")) {
    return "Street";
  }
  if (normalized.includes("briefing") || normalized.includes("dossier")) {
    return "Briefing";
  }
  return label;
}

function textPlaceholder(
  room: RoomName,
  activeCitizen?: CitizenSnapshot,
  advisorMode: AdvisorMode = "solo",
  auditoriumMode: AuditoriumMode = "debate",
  stage?: StagePackage,
) {
  const changedWorld = Boolean(
    stage &&
      /machine|model account|model credit|compute|agent|paid hours|income floor|public ai|automation|cheap expertise/i.test(
        `${stage.world_brief} ${stage.montage_logline}`,
      ),
  );
  switch (room) {
    case "advisor":
      return advisorMode === "council"
        ? "Ask where the room agrees, who wants the floor, or what belongs on the board..."
        : "Ask what is changing, who to poll, or what belongs on the board...";
    case "citizens":
      if (changedWorld) {
        return `Ask ${activeCitizen?.display_name?.split(" ")[0] ?? "them"} what pays the bills, which account or queue matters, or what old routine vanished...`;
      }
      return `Ask ${activeCitizen?.display_name?.split(" ")[0] ?? "them"} about work, bills, school, or what feels better or worse...`;
    case "debate":
      if (auditoriumMode === "town_hall") {
        return "Answer the crowd question, clarify one promise, or rebut the rival cleanly...";
      }
      return "Make your case, answer the attack, or sharpen one proposal...";
    default:
      return "Send a text turn...";
  }
}

function boardSnippet(text: string, max = 92) {
  const normalized = text.replace(/\s+/g, " ").trim();
  if (normalized.length <= max) {
    return normalized;
  }
  const clipped = normalized.slice(0, max);
  return clipped.slice(0, clipped.lastIndexOf(" ") > 12 ? clipped.lastIndexOf(" ") : max).trimEnd();
}

function boardPolicyLabel(text: string) {
  const cleaned = text
    .trim()
    .replace(/[.]+$/, "")
    .replace(/^(?:i think we should|we should|let's|our plan is to|the plan is to|we need to)\s+/i, "");
  const primaryClause =
    cleaned.length > 98
      ? cleaned.replace(/\s+(?:while|so that|without|but)\s+.+$/i, "")
      : cleaned;
  return boardSnippet(primaryClause.length > 38 ? primaryClause : cleaned, 88);
}

function compactCitizenLabel(name: string, max = 22) {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (parts.length <= 2) {
    return boardSnippet(name, max);
  }
  return boardSnippet(`${parts[0]} ${parts.at(-1) ?? ""}`.trim(), max);
}

function citizenSuggestedQuestions(citizen: CitizenSnapshot) {
  const source = [
    citizen.role,
    citizen.summary,
    citizen.current_update,
    citizen.daily_routine,
    citizen.recent_ai_moment,
    citizen.current_worries,
    citizen.current_hopes,
    citizen.household,
    citizen.ai_exposure,
  ].join(" ").toLowerCase();
  const prompts: string[] = [];

  if (/\b(teacher|student|school|class|college|campus|parent)\b/.test(source)) {
    prompts.push("How has school or learning changed lately?");
  }
  if (/\b(nurse|doctor|clinic|hospital|care|caregiver|health)\b/.test(source)) {
    prompts.push("What changed around care this week?");
  }
  if (/\b(factory|warehouse|driver|truck|repair|construction|electrician|mechanic|machinist)\b/.test(source)) {
    prompts.push("What changed on the job lately?");
  }
  if (/\b(store|retail|shop|restaurant|waiter|server|cashier|hotel|customer)\b/.test(source)) {
    prompts.push("What changed for shifts or customers?");
  }
  if (/\b(rent|bill|price|prices|cost|debt|mortgage)\b/.test(source)) {
    prompts.push("Where do you feel the strain most right now?");
  }
  if (/\b(child|kid|family|parent|household|spouse|home)\b/.test(source)) {
    prompts.push("How is this landing at home or with family?");
  }

  if (prompts.length === 0) {
    prompts.push("What pays the bills or steadies life now?");
  }
  prompts.push(
    citizen.ai_exposure.toLowerCase().includes("low") || citizen.ai_exposure.toLowerCase().includes("minimal")
      ? "Has AI shown up in your life much, or not really?"
      : "Which account, service, or machine help mattered this week?",
  );
  prompts.push(
    /\b(rent|bill|price|prices|cost|debt|mortgage)\b/.test(source)
      ? "What feels easier now, and what still feels expensive or hard?"
      : "What old routine disappeared, and what still needs a person?",
  );

  return [...new Set(prompts)].slice(0, 3);
}

function citizenCardDisposition(citizen: CitizenSnapshot) {
  const hope = boardSnippet(citizen.current_hopes, 54);
  const worry = boardSnippet(citizen.current_worries, 54);
  if (hope && worry) {
    return `Hope: ${hope}  Pressure: ${worry}`;
  }
  if (hope) {
    return `Hope: ${hope}`;
  }
  if (worry) {
    return `Pressure: ${worry}`;
  }
  return boardSnippet(citizen.summary, 108);
}

function stripPollHonorifics(text: string) {
  return text
    .replace(/\b(?:President|Governor|Senator|Prime Minister|Chancellor|Opposition Leader|Mayor)\b/gi, "")
    .replace(/\s+/g, " ")
    .trim();
}

function boardPollLabel(answer: string, opts?: { candidate?: boolean; max?: number }) {
  const cleaned = stripPollHonorifics(answer).replace(/\s+/g, " ").trim();
  if (!cleaned) {
    return "";
  }
  if (opts?.candidate) {
    const surname = cleaned.split(" ").at(-1) ?? cleaned;
    return boardSnippet(surname, opts.max ?? 16);
  }
  return boardSnippet(cleaned, opts?.max ?? 34);
}

function boardPollQuestionLabel(question: string) {
  const normalized = question.toLowerCase().replace(/\?+$/, "").trim();
  if (normalized.includes("right now ai mostly feels able to handle")) {
    return "What AI can handle now";
  }
  if (normalized.includes("biggest national effect of ai right now")) {
    return "Biggest economic effect";
  }
  if (normalized.includes("still clearly needs a person") || normalized.includes("still would not trust ai")) {
    return "What still needs people";
  }
  if (normalized.includes("easier, cheaper, or better because of ai lately")) {
    return "What got better lately";
  }
  if (normalized.includes("hate to lose right now")) {
    return "What people want kept";
  }
  if (normalized.includes("what change from ai is most shaping your life right now")) {
    return "What most shapes life now";
  }
  if (normalized.includes("ai is touching your life most")) {
    return "Where it shows up";
  }
  if (normalized.includes("household feels much better off")) {
    return "Household effect";
  }
  if (normalized.includes("economy feels stronger and more capable")) {
    return "Economic mood";
  }
  if (normalized.includes("school or learning around you")) {
    return "Learning shift";
  }
  if (normalized.includes("everyday services now feel")) {
    return "Service quality";
  }
  if (normalized.includes("which issue most needs attention first") || normalized.includes("biggest worry about ai")) {
    return "Main pressure right now";
  }
  return boardSnippet(question.replace(/\?+$/, ""), 52);
}

function genericBoardAnswer(answer?: string | null) {
  const normalized = (answer ?? "").trim().toLowerCase();
  return !normalized || ["other", "none", "mixed", "unsure", "not sure"].includes(normalized);
}

function boardQuoteSnippet(text: string, max = 76) {
  const cleaned = text.trim().split(/\s+/).join(" ")
    .replace(/^[^:"“”]{1,42}:\s*["“]?/, "")
    .replace(/[”"]$/, "")
    .trim();
  return boardSnippet(cleaned || text, max);
}

function boardPollLaneLabel(id: string, question: string) {
  switch (id) {
    case "latest_custom":
      return boardSnippet(question.replace(/\?+$/, ""), 30);
    case "capability":
      return "best-fit public read";
    case "defended_gain":
      return "benefit voters would keep";
    case "main_pressure":
      return "main voter pressure";
    case "still_human":
      return "where people still want humans";
    case "economic_read":
      return "national mood";
    default:
      return boardPollQuestionLabel(question);
  }
}

function streetApproachPoint(position: [number, number, number]) {
  const directionToStreet = new THREE.Vector2(position[0] === 0 ? 0.01 : -position[0], position[2] >= 0 ? 0.75 : 1.05).normalize();
  const talkRadius = 2.2;
  const approachX = THREE.MathUtils.clamp(position[0] + directionToStreet.x * talkRadius, STREET_BOUNDS.minX + 1.2, STREET_BOUNDS.maxX - 1.2);
  const approachZ = THREE.MathUtils.clamp(position[2] + directionToStreet.y * talkRadius, STREET_BOUNDS.minZ + 2.2, STREET_BOUNDS.maxZ - 1.4);
  const heading = Math.atan2(position[0] - approachX, -(position[2] - approachZ));
  return {
    x: approachX,
    z: approachZ,
    heading,
    citizenPosition: position,
    stopRadius: 3.35,
  };
}

function citizenHash(citizen: CitizenSnapshot) {
  return Array.from(citizen.citizen_id).reduce((value, char, index) => value + char.charCodeAt(0) * (index + 3), 0);
}

function buildStreetPlacements(citizens: CitizenSnapshot[]): StreetPlacement[] {
  return citizens.map((citizen, index) => {
    const hash = citizenHash(citizen);
    const openingPocket: Array<[number, number, number]> = [
      [-3.25, 0, 16.6],
      [3.35, 0, 15.7],
      [-4.8, 0, 13.2],
      [4.9, 0, 11.4],
      [-6.2, 0, 8.8],
      [6.1, 0, 7.5],
      [-4.9, 0, 4.7],
      [4.8, 0, 3.6],
      [-3.1, 0, 1.5],
      [3.2, 0, 0.4],
      [-6.0, 0, -0.6],
      [6.1, 0, -2.1],
      [-4.9, 0, -6.8],
      [5.0, 0, -8.2],
    ];
    if (index < openingPocket.length) {
      const [x, y, z] = openingPocket[index];
      return {
        citizen,
        position: [x + (((hash >> 1) % 5) - 2) * 0.08, y, z + (((hash >> 2) % 5) - 2) * 0.12],
        appearance: citizenAppearance(citizen, index),
      };
    }
    const laneTemplate = [-6.35, -4.95, 4.95, 6.35];
    const spreadIndex = index - openingPocket.length;
    const lane = laneTemplate[spreadIndex % laneTemplate.length] + (((hash >> 1) % 5) - 2) * 0.12;
    const block = Math.floor(spreadIndex / laneTemplate.length);
    const rowBase = 14.8 - block * 8.2;
    const rowOffset = (spreadIndex % 2) * 0.42;
    const stagger = (((hash >> 2) % 7) * 0.24 - 0.72) * 0.82;
    const z = rowBase - rowOffset + stagger;
    return {
      citizen,
      position: [lane, 0, THREE.MathUtils.clamp(z, -76.8, 19.2)],
      appearance: citizenAppearance(citizen, index),
    };
  });
}

function buildStreetExtras(citizens: CitizenSnapshot[], count = 8): StreetExtra[] {
  return Array.from({ length: count }, (_, index) => {
    const reference = citizens[index % Math.max(citizens.length, 1)];
    const seed = reference ? citizenHash(reference) : 41 * (index + 2);
    if (index < 5) {
      const cluster: Array<[number, number, number]> = [
        [-15.8, 0, -16.8],
        [15.8, 0, -24.4],
        [15.6, 0, -40.6],
        [-15.6, 0, -51.2],
        [15.8, 0, -66.8],
      ];
      const [x, y, z] = cluster[index];
      return {
        id: `street-extra-${index}`,
        position: [x + ((seed % 3) - 1) * 0.18, y, z - (((seed >> 2) % 3) - 1) * 0.25],
        appearance: extraStreetAppearance(seed, index),
      };
    }
    const lane = index % 2 === 0 ? -10.8 + (seed % 4) * 0.38 : 10.8 - (seed % 4) * 0.38;
    const z = 23.6 - index * 9.1 - ((seed >> 2) % 5) * 0.66;
    return {
      id: `street-extra-${index}`,
      position: [lane, 0, THREE.MathUtils.clamp(z, -77.2, 24.2)],
      appearance: extraStreetAppearance(seed, index),
    };
  });
}

function topPollChoice(stage: StagePackage, needle: string) {
  const summary = stage.poll_summaries.find((item) => item.question.toLowerCase().includes(needle));
  if (!summary) {
    return null;
  }
  const [answer, share] =
    Object.entries(summary.shares).sort((left, right) => right[1] - left[1])[0] ?? ["n/a", 0];
  return {
    answer: boardSnippet(answer, 52),
    share: `${Math.round(share * 100)}%`,
  };
}

function topPollChoiceForNeedles(stage: StagePackage, needles: string[]) {
  const summary = stage.poll_summaries.find((item) => {
    const normalized = item.question.toLowerCase();
    return needles.some((needle) => normalized.includes(needle));
  });
  if (!summary) {
    return null;
  }
  const [answer, share] =
    Object.entries(summary.shares).sort((left, right) => right[1] - left[1])[0] ?? ["n/a", 0];
  return {
    question: summary.question,
    answer: boardSnippet(answer, 56),
    share: `${Math.round(share * 100)}%`,
  };
}

function latestPollSummaryByKey(stage: StagePackage, keys: string[]) {
  return [...stage.poll_summaries].reverse().find((summary) => {
    const summaryKey = (summary.key ?? "").trim();
    return summaryKey ? keys.includes(summaryKey) : false;
  });
}

function preferredPollSummaryByKey(stage: StagePackage, keys: string[]) {
  for (const key of keys) {
    const summary = latestPollSummaryByKey(stage, [key]);
    if (summary) {
      return summary;
    }
  }
  return null;
}

function latestPollSummaryBySlot(stage: StagePackage, slots: string[], fallbackKeys: string[] = []) {
  return [...stage.poll_summaries].reverse().find((summary) => {
    const slot = (summary.board_slot ?? "").trim();
    if (slot && slots.includes(slot)) {
      return true;
    }
    const summaryKey = (summary.key ?? "").trim();
    return summaryKey ? fallbackKeys.includes(summaryKey) : false;
  });
}

function latestCustomPollSummary(stage: StagePackage) {
  return [...stage.poll_summaries].reverse().find((summary) => {
    const source = summary.source ?? "standard";
    const slot = summary.board_slot ?? "";
    return slot === "custom" || source !== "standard";
  });
}

function boardSummaryChoice(summary?: StagePackage["poll_summaries"][number] | null) {
  if (!summary) {
    return null;
  }
  const [answer, share] =
    Object.entries(summary.shares).sort((left, right) => right[1] - left[1])[0] ?? ["n/a", 0];
  const hasMeaningfulShares = Object.keys(summary.shares ?? {}).length > 0 && Number(share) > 0;
  const quotedReason = Array.isArray(summary.sample_reasons)
    ? summary.sample_reasons.find((entry) => typeof entry === "string" && entry.trim())
    : null;
  if (quotedReason && !hasMeaningfulShares) {
    return {
      question: summary.question,
      answer: boardQuoteSnippet(quotedReason, 108),
      share: "",
    };
  }
  if (quotedReason && genericBoardAnswer(answer)) {
    return {
      question: summary.question,
      answer: boardQuoteSnippet(quotedReason, 76),
      share: "",
    };
  }
  return {
    question: summary.question,
    answer: boardSnippet(answer, 34),
    share: `${Math.round(share * 100)}%`,
  };
}

function boardSummaryTitle(summary?: StagePackage["poll_summaries"][number] | null, fallback = "Public read") {
  const slot = summary?.board_slot ?? "";
  const key = summary?.key ?? "";
  if (slot === "capability" || key === "capability_read") {
    return "Capability now";
  }
  if (slot === "national" || key === "national_effect") {
    return "National read";
  }
  if (slot === "gain" || key === "keep_change" || key === "ai_gain") {
    return "People keep";
  }
  if (key === "still_human") {
    return "Still human";
  }
  if (key === "newly_normal") {
    return "New normal";
  }
  if (slot === "pressure" || key === "biggest_worry" || key === "main_pressure") {
    return "Main pressure";
  }
  if (key === "household_security") {
    return "Household read";
  }
  if (key === "job_worry") {
    return "Job strain";
  }
  if (key === "ai_comfort") {
    return "AI comfort";
  }
  if (key === "daily_role") {
    return "Daily role";
  }
  if (key === "life_touchpoint") {
    return "Where it lands";
  }
  if (key === "public_stability") {
    return "Daily life";
  }
  if (key === "expertise_access") {
    return "Expertise access";
  }
  if (key === "service_reliability") {
    return "Service read";
  }
  if (key === "fairness") {
    return "Fairness";
  }
  if (slot === "custom" || (summary?.source && summary.source !== "standard")) {
    return "Your poll";
  }
  return fallback;
}

function boardSummaryLabel(summary?: StagePackage["poll_summaries"][number] | null) {
  if (!summary) {
    return "";
  }
  return boardSnippet(summary.board_label || boardPollQuestionLabel(summary.question), 54);
}

function stageMacroStats(stage: StagePackage) {
  const priorityFor = (key: string, label: string) => {
    const probe = `${key} ${label}`.toLowerCase();
    const worldProbe = `${stage.phase_label} ${stage.title} ${stage.world_brief}`.toLowerCase();
    const changedWorld =
      /\b(deep|later|machine|compute|robot|agent|old job|workweek|dividend|service guarantee|public account|post-work|screen work|model labor|automated labor)\b/.test(
        worldProbe,
      );
    if (/\bunemployment\b|\bjobless\b/.test(probe)) return 0;
    if (/\bhours|time|labor|labour|participation|work\b/.test(probe)) return 1;
    if (/\bgdp\b|\boutput\b|\bgrowth\b/.test(probe)) return 2;
    if (changedWorld) {
      if (/\b(machine|dividend|income floor|service guarantee|public account|universal service|automated income)\b/.test(probe)) {
        return 3;
      }
      if (/\bcompute|model|access|credit|platform|ownership|rent|toll|allocation\b/.test(probe)) {
        return 4;
      }
      if (/\bpower|grid|energy|chip|hardware|robot|depot|datacenter|data center\b/.test(probe)) {
        return 5;
      }
    }
    if (/\binflation\b|\bprices\b/.test(probe)) return 6;
    if (/\bcompute\b|\bpower\b|\bgrid\b/.test(probe)) return 7;
    if (/\bsecurity\b|\bstability\b|\btrust\b/.test(probe)) return 8;
    return 9;
  };
  return Object.entries(stage.macro_stats ?? {})
    .flatMap(([key, stat]) => {
      const label = String(stat?.label ?? "").replace(/\s+/g, " ").trim();
      const value = String(stat?.value ?? "").replace(/\s+/g, " ").trim();
      const detail = String(stat?.detail ?? "").replace(/\s+/g, " ").trim();
      if (!label || !value) {
        return [];
      }
      return [{ key, label, value, detail, priority: priorityFor(key, label) }];
    })
    .sort((left, right) => left.priority - right.priority || left.label.localeCompare(right.label));
}

function boardPublicMoodColumns(stage: StagePackage): BoardPanel["columns"] {
  const columns: BoardPanel["columns"] = [];
  const pushSummary = (summary?: StagePackage["poll_summaries"][number] | null) => {
    if (!summary) {
      return;
    }
    if (columns.some((column) => column.id === (summary.key ?? summary.question))) {
      return;
    }
    const choice = boardSummaryChoice(summary);
    if (choice?.answer) {
      columns.push({
        id: summary.key ?? summary.question,
        title: boardSummaryTitle(summary, "Public poll"),
        lines: [
          {
            label: boardSummaryLabel(summary) || "Top response",
            answer: boardPollLabel(choice.answer, { max: choice.share ? 32 : 66 }),
            share: choice.share,
          },
        ],
      });
    }
  };

  pushSummary(latestCustomPollSummary(stage));
  pushSummary(latestPollSummaryBySlot(stage, ["capability"], ["capability_read"]));
  pushSummary(latestPollSummaryBySlot(stage, ["gain"], ["ai_gain", "keep_change", "better_off"]));
  pushSummary(latestPollSummaryBySlot(stage, ["pressure"], ["main_pressure", "biggest_worry", "job_worry", "public_stability"]));
  pushSummary(latestPollSummaryBySlot(stage, ["national"], ["national_effect", "econ_read"]));

  if (columns.length === 0) {
    columns.push({
      id: "polling_pending",
      title: "Public poll",
      lines: [
        {
          label: "Status",
          answer: "Polling pending",
        },
      ],
    });
    const worldCue = stageWorldOpening(stage, 84);
    if (worldCue) {
      columns.push({
        id: "world_cue",
        title: "State cue",
        lines: [{ label: "Opening read", answer: boardSnippet(worldCue, 84) }],
      });
    }
  }

  return columns.slice(0, 3);
}

function boardMacroRead(stage: StagePackage) {
  const roomBrief = stage.room_briefing?.replace(/\s+/g, " ").trim();
  if (roomBrief) {
    return roomBrief;
  }
  const synthesized = [stageGain(stage, 140), stageSplit(stage, 140)].filter(Boolean);
  if (synthesized.length > 0) {
    return synthesized.join(" ");
  }
  return stageWorldOpening(stage, 180);
}

function boardMetricRows(stage: StagePackage): Array<{ label: string; value: string; note?: string }> {
  const pollingReady = stage.poll_summaries.length > 0;
  const macroRows = stageMacroStats(stage).slice(0, 4).map((entry) => ({
    label: entry.label,
    value: boardSnippet(entry.value, 18),
    note: entry.detail ? boardSnippet(entry.detail, 34) : undefined,
  }));
  const formatDelta = (metric: { delta: number }) => {
    if (!Number.isFinite(metric.delta) || metric.delta === 0) {
      return "";
    }
    const amount = Math.abs(metric.delta) >= 10 ? Math.round(Math.abs(metric.delta)) : Number(Math.abs(metric.delta).toFixed(1));
    return `${metric.delta > 0 ? "+" : "−"}${amount} vs last round`;
  };
  if (!pollingReady && macroRows.length > 0) {
    return macroRows.slice(0, 4);
  }

  const approvalRow = {
    label: "Approval",
    value: pollingReady ? stage.tracking.approval.display : "—",
    note: pollingReady ? (formatDelta(stage.tracking.approval) || "latest voter read") : "Polling in background",
  };
  if (macroRows.length > 0) {
    return pollingReady ? [...macroRows.slice(0, 3), approvalRow].slice(0, 4) : macroRows.slice(0, 4);
  }
  return [
    approvalRow,
    {
      label: "Better off",
      value: pollingReady ? stage.tracking.better_off.display : "—",
      note: pollingReady ? formatDelta(stage.tracking.better_off) : "Lived read still settling",
    },
    {
      label: "AI comfort",
      value: pollingReady ? stage.tracking.ai_comfort.display : "—",
      note: pollingReady ? formatDelta(stage.tracking.ai_comfort) : "Need public read first",
    },
  ];
}

function boardMetricsKey(stage: StagePackage) {
  return boardMetricRows(stage)
    .map((row) => `${row.label}:${row.value}:${row.note ?? ""}`)
    .join("|");
}

function boardPollsKey(stage: StagePackage) {
  return stage.poll_summaries
    .map((summary) => {
      const shareKey = Object.entries(summary.shares)
        .sort(([left], [right]) => left.localeCompare(right))
        .map(([label, share]) => `${label}:${Math.round(share * 1000)}`)
        .join(",");
      return [
        summary.key ?? "",
        summary.source ?? "",
        summary.board_slot ?? "",
        summary.board_label ?? "",
        shareKey,
        (summary.sample_reasons ?? []).slice(0, 2).join("|"),
      ].join("::");
    })
    .join("||");
}

function streetSurfaceTexture(themeMode: "light" | "dark") {
  const canvas = document.createElement("canvas");
  canvas.width = 1024;
  canvas.height = 2048;
  const ctx = canvas.getContext("2d");
  if (!ctx) {
    return new THREE.CanvasTexture(canvas);
  }

  const paving = themeMode === "light" ? "#87715d" : "#443933";
  const seam = themeMode === "light" ? "rgba(92, 74, 56, 0.08)" : "rgba(229, 210, 184, 0.035)";
  const laneTint = themeMode === "light" ? "rgba(239, 228, 212, 0.055)" : "rgba(214, 193, 165, 0.03)";
  const laneStripe = themeMode === "light" ? "rgba(247, 236, 220, 0.16)" : "rgba(226, 204, 171, 0.09)";
  const edgeShade = themeMode === "light" ? "rgba(248, 240, 228, 0.04)" : "rgba(255, 241, 222, 0.018)";

  ctx.fillStyle = paving;
  ctx.fillRect(0, 0, canvas.width, canvas.height);

  for (let i = 0; i < 58; i += 1) {
    const alpha = themeMode === "light" ? 0.012 : 0.009;
    ctx.fillStyle = `rgba(255,255,255,${alpha})`;
    ctx.fillRect((i * 131) % canvas.width, (i * 181) % canvas.height, 7, 7);
    ctx.fillStyle = `rgba(0,0,0,${alpha * 0.88})`;
    ctx.fillRect((i * 187) % canvas.width, (i * 109) % canvas.height, 8, 8);
  }

  const edgeGlow = ctx.createLinearGradient(0, 0, canvas.width, 0);
  edgeGlow.addColorStop(0, edgeShade);
  edgeGlow.addColorStop(0.12, "rgba(255,255,255,0)");
  edgeGlow.addColorStop(0.88, "rgba(255,255,255,0)");
  edgeGlow.addColorStop(1, edgeShade);
  ctx.fillStyle = edgeGlow;
  ctx.fillRect(0, 0, canvas.width, canvas.height);

  ctx.fillStyle = laneTint;
  ctx.fillRect(canvas.width / 2 - 128, 0, 256, canvas.height);

  ctx.strokeStyle = laneStripe;
  ctx.lineWidth = 9;
  for (let y = 180; y < canvas.height; y += 460) {
    ctx.beginPath();
    ctx.moveTo(canvas.width / 2, y);
    ctx.lineTo(canvas.width / 2, y + 148);
    ctx.stroke();
  }

  ctx.strokeStyle = seam;
  ctx.lineWidth = 1.2;
  for (let y = 240; y < canvas.height; y += 420) {
    ctx.beginPath();
    ctx.moveTo(0, y);
    ctx.lineTo(canvas.width, y);
    ctx.stroke();
  }
  for (let x = 208; x < canvas.width; x += 304) {
    ctx.beginPath();
    ctx.moveTo(x, 0);
    ctx.lineTo(x, canvas.height);
    ctx.stroke();
  }

  const texture = new THREE.CanvasTexture(canvas);
  texture.colorSpace = THREE.SRGBColorSpace;
  texture.wrapS = THREE.ClampToEdgeWrapping;
  texture.wrapT = THREE.ClampToEdgeWrapping;
  texture.repeat.set(1, 1);
  texture.magFilter = THREE.LinearFilter;
  texture.minFilter = THREE.LinearFilter;
  texture.anisotropy = 4;
  texture.generateMipmaps = false;
  texture.needsUpdate = true;
  return texture;
}

function counterpartPosition(room: RoomName, advisorMode: AdvisorMode = "solo"): [number, number, number] {
  if (room === "debate") {
    return [2.85, 0, 2.92];
  }
  if (room === "advisor") {
    return advisorMode === "council" ? [0, 0.02, -0.78] : [4.88, 0.02, -0.52];
  }
  return [1.9, 0, 0.2];
}

export default function SceneViewport({
  room,
  advisorMode = "solo",
  auditoriumMode = "debate",
  stage,
  playerName,
  councilRoster,
  themeMode = "light",
  themeProfile,
  playerInPower = true,
  citizens = [],
  activeCitizen,
  previewCitizen,
  advisorNotes = [],
  debateNotes = [],
  presence = DEFAULT_PRESENCE,
  councilFloorLead,
  councilFloorOwner,
  councilFloorContrast,
  townHallState,
  resolvingStage = false,
  hotspots = [],
  panelsOpen = false,
  overlayActive = false,
  captionSpeaker,
  captionText,
  onHotspotSelect,
  onPrimaryInteract,
  onStartVoice,
  textComposerOpen = false,
  textComposerDraft = "",
  onTextComposerToggle,
  onTextComposerChange,
  onTextComposerSend,
  onStageAdvance,
  stageAdvanceLabel,
  stageAdvanceHint,
  stageAdvanceDisabled = false,
  onTownHallQuestion,
  townHallDisabled = false,
  townHallDisabledReason = "Town hall is not ready yet.",
  onTogglePanels,
  detailsOpen = false,
  onOpenReels,
  reelsLabel = "Future reels",
  onToggleTheme,
  themeLabel = "Light",
  onToggleFullscreen,
  fullscreenLabel = "Fullscreen",
  onRestart,
  onStreetFocusChange,
  onStreetPreviewChange,
}: SceneViewportProps) {
  const normalizedCouncilRoster = useMemo(() => normalizeCouncilRoster(councilRoster), [councilRoster]);
  const config = themedRoomConfig(room, themeMode, themeProfile, advisorMode);
  const palette = counterpartPalette(room, activeCitizen ?? previewCitizen);
  const councilCaption = useMemo(
    () => (
      room === "advisor" && advisorMode === "council"
        ? parseCouncilCaption(captionText, normalizedCouncilRoster)
        : { speaker: undefined, text: captionText?.trim() ?? "" }
    ),
    [advisorMode, captionText, normalizedCouncilRoster, room],
  );
  const councilCaptionEntries = useMemo(
    () => (
      room === "advisor" && advisorMode === "council"
        ? splitCouncilLines(captionText, normalizedCouncilRoster).filter((line) => line.text)
        : []
    ),
    [advisorMode, captionText, normalizedCouncilRoster, room],
  );
  const councilCaptionLines = useMemo(
    () => councilCaptionEntries
      .map((line) => (line.speaker ? `${line.speaker}: ${line.text}` : line.text))
      .filter(Boolean),
    [councilCaptionEntries],
  );
  const trailingCouncilSpeaker = councilCaptionEntries
    .slice()
    .reverse()
    .find((line) => line.speaker)?.speaker;
  const activeCouncilLeadRaw =
    room === "advisor" && advisorMode === "council"
      ? councilFloorLead ?? (presence.counterpartActivity === "speaking" ? trailingCouncilSpeaker ?? councilCaption.speaker : undefined)
      : undefined;
  const activeCouncilOwner =
    room === "advisor" && advisorMode === "council"
      ? councilFloorOwner ?? activeCouncilLeadRaw
      : undefined;
  const playerHasCouncilFloor =
    room === "advisor" &&
    advisorMode === "council" &&
    (activeCouncilOwner === "player" || (
      Boolean(activeCouncilOwner) &&
      Boolean(playerName) &&
      activeCouncilOwner?.trim().toLowerCase() === playerName?.trim().toLowerCase()
    ));
  const councilCaptionVisible =
    room === "advisor" && advisorMode === "council"
      ? Boolean(activeCouncilLeadRaw) ||
        presence.counterpartActivity === "speaking" ||
        presence.voicePhase === "responding" ||
        (captionSpeaker === "user" && Boolean(captionText))
      : true;
  const activeCouncilLead = playerHasCouncilFloor ? "You" : activeCouncilLeadRaw;
  const showCouncilFloorCue =
    !overlayActive &&
    !panelsOpen &&
    room === "advisor" &&
    advisorMode === "council" &&
    Boolean(activeCouncilLead || presence.voicePhase === "waiting" || presence.voicePhase === "responding" || presence.status === "connecting");
  const councilFloorCueLabel =
    activeCouncilLead === "You"
      ? "Your floor"
      : activeCouncilLead
        ? `${activeCouncilLead} has the floor`
        : presence.status === "connecting"
          ? "Opening the table"
          : "Choosing the next voice";
  const visibleCaptionText = room === "advisor" && advisorMode === "council" ? councilCaption.text : captionText;
  const textComposerAvailable = Boolean(onTextComposerToggle && onTextComposerChange && onTextComposerSend);
  const showStageAdvance =
    !overlayActive &&
    !panelsOpen &&
    room === "debate" &&
    Boolean(onStageAdvance) &&
    Boolean(stageAdvanceLabel);
  const townHallQuestionBusy =
    townHallState?.phase === "generating" ||
    townHallState?.phase === "voter_speaking" ||
    townHallState?.phase === "opponent_turn";
  const showTownHallQuestionAction =
    !overlayActive &&
    !panelsOpen &&
    room === "debate" &&
    auditoriumMode === "town_hall" &&
    townHallState?.readyForNextQuestion !== false &&
    Boolean(onTownHallQuestion);
  const townHallQuestionDisabled =
    townHallDisabled || resolvingStage || townHallQuestionBusy || townHallState?.readyForNextQuestion === false;
  const townHallQuestionLabel =
    townHallDisabled
      ? "Citizens arriving"
      : townHallState?.phase === "generating"
      ? "Finding voter..."
      : townHallState?.phase === "voter_speaking"
      ? "Voter speaking..."
      : townHallState?.phase === "opponent_turn"
        ? "Opponent answering..."
        : townHallState?.question
            ? "Next audience question"
            : "Audience question";
  const showSceneUtilities =
    !overlayActive &&
    !panelsOpen &&
    Boolean(onTogglePanels || onOpenReels || onToggleTheme || onToggleFullscreen || onRestart);
  const playerStateRef = useRef<StreetPlayerState>({ ...STREET_PLAYER_START });
  const streetAutoTargetRef = useRef<StreetAutoTarget | null>(null);
  const streetBootedRef = useRef(false);
  const [playerPoseSnapshot, setPlayerPoseSnapshot] = useState<StreetPlayerState>({ ...STREET_PLAYER_START });
  const [hoveredCitizenId, setHoveredCitizenId] = useState<string | null>(null);
  const lastPoseCommitRef = useRef(0);
  const lastPublishedPoseRef = useRef<StreetPlayerState>({ ...STREET_PLAYER_START });
  const presenceStatusRef = useRef(presence.status);
  const streetCallbacksRef = useRef({
    onStartVoice,
    onStreetFocusChange,
    onStreetPreviewChange,
  });
  const streetGateRef = useRef({
    citizenConversationLocked: false,
    overlayActive,
    panelsOpen,
    textComposerOpen,
  });
  const streetPlacements = useMemo(() => buildStreetPlacements(citizens), [citizens]);
  const streetPlacementMap = useMemo(
    () => new Map(streetPlacements.map((entry) => [entry.citizen.citizen_id, entry])),
    [streetPlacements],
  );
  const streetExtras = useMemo(
    () => buildStreetExtras(citizens, 20),
    [citizens],
  );
  const citizenConversationLocked =
    room === "citizens" &&
    Boolean(activeCitizen?.citizen_id) &&
    (
      presence.status === "connecting" ||
      presence.playerActivity === "speaking" ||
      presence.counterpartActivity === "speaking"
    );

  useEffect(() => {
    streetGateRef.current = {
      citizenConversationLocked,
      overlayActive,
      panelsOpen,
      textComposerOpen,
    };
  }, [citizenConversationLocked, overlayActive, panelsOpen, textComposerOpen]);

  useEffect(() => {
    presenceStatusRef.current = presence.status;
  }, [presence.status]);

  useEffect(() => {
    streetCallbacksRef.current = {
      onStartVoice,
      onStreetFocusChange,
      onStreetPreviewChange,
    };
  }, [onStartVoice, onStreetFocusChange, onStreetPreviewChange]);

  useEffect(() => {
    if (room !== "citizens") {
      playerStateRef.current = { ...STREET_PLAYER_START };
      streetAutoTargetRef.current = null;
      streetBootedRef.current = false;
      lastPublishedPoseRef.current = { ...STREET_PLAYER_START };
      lastPoseCommitRef.current = 0;
      setPlayerPoseSnapshot((current) =>
        Math.abs(current.x - STREET_PLAYER_START.x) < 0.001 &&
        Math.abs(current.z - STREET_PLAYER_START.z) < 0.001 &&
        Math.abs(current.heading - STREET_PLAYER_START.heading) < 0.001
          ? current
          : { ...STREET_PLAYER_START },
      );
      setHoveredCitizenId((current) => (current === null ? current : null));
      return;
    }
    const pressed = new Set<string>();
    let animationFrame = 0;
    let lastTick = performance.now();
    let velocityX = 0;
    let velocityZ = 0;
    let headingVelocity = 0;
    const commitPlayerPose = (nextState: StreetPlayerState, now: number, force = false) => {
      const published = lastPublishedPoseRef.current;
      if (
        !force &&
        now - lastPoseCommitRef.current < STREET_POSE_PUBLISH_INTERVAL_MS &&
        Math.abs(published.x - nextState.x) < STREET_POSE_PUBLISH_DELTA &&
        Math.abs(published.z - nextState.z) < STREET_POSE_PUBLISH_DELTA &&
        Math.abs(published.heading - nextState.heading) < STREET_HEADING_PUBLISH_DELTA
      ) {
        return;
      }
      lastPublishedPoseRef.current = nextState;
      lastPoseCommitRef.current = now;
      setPlayerPoseSnapshot((current) => {
        if (
          Math.abs(current.x - nextState.x) < 0.001 &&
          Math.abs(current.z - nextState.z) < 0.001 &&
          Math.abs(current.heading - nextState.heading) < 0.001
        ) {
          return current;
        }
        return nextState;
      });
    };
    const handleKeyDown = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      const gate = streetGateRef.current;
      if (gate.overlayActive || gate.panelsOpen || gate.textComposerOpen) {
        pressed.clear();
        return;
      }
      if (
        target &&
        (
          target instanceof HTMLInputElement ||
          target instanceof HTMLTextAreaElement ||
          target instanceof HTMLSelectElement ||
          target.isContentEditable
        )
      ) {
        return;
      }
      if (["w", "a", "s", "d", "q", "e", "arrowup", "arrowdown", "arrowleft", "arrowright"].includes(event.key.toLowerCase())) {
        event.preventDefault();
        pressed.add(event.key.toLowerCase());
        streetAutoTargetRef.current = null;
      }
    };
    const handleKeyUp = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      if (
        target &&
        (
          target instanceof HTMLInputElement ||
          target instanceof HTMLTextAreaElement ||
          target instanceof HTMLSelectElement ||
          target.isContentEditable
        )
      ) {
        return;
      }
      if (["w", "a", "s", "d", "q", "e", "arrowup", "arrowdown", "arrowleft", "arrowright"].includes(event.key.toLowerCase())) {
        event.preventDefault();
      }
      pressed.delete(event.key.toLowerCase());
      if (pressed.size === 0) {
        commitPlayerPose(playerStateRef.current, performance.now(), true);
      }
    };
    const tick = (now: number) => {
      const elapsed = Math.min((now - lastTick) / 16.67, 2);
      lastTick = now;
      if (presenceStatusRef.current === "connecting") {
        animationFrame = window.requestAnimationFrame(tick);
        return;
      }
      const gate = streetGateRef.current;
      if (gate.overlayActive || gate.panelsOpen || gate.textComposerOpen) {
        pressed.clear();
        velocityX = THREE.MathUtils.lerp(velocityX, 0, Math.min(0.22 * elapsed, 1));
        velocityZ = THREE.MathUtils.lerp(velocityZ, 0, Math.min(0.22 * elapsed, 1));
        animationFrame = window.requestAnimationFrame(tick);
        return;
      }
      let inputForward = 0;
      let inputStrafe = 0;
      let inputTurn = 0;
      if (pressed.has("q") || pressed.has("arrowleft")) {
        inputTurn += 1;
      }
      if (pressed.has("e") || pressed.has("arrowright")) {
        inputTurn -= 1;
      }
      if (pressed.has("a")) {
        inputStrafe -= 1;
      }
      if (pressed.has("d")) {
        inputStrafe += 1;
      }
      if (pressed.has("w") || pressed.has("arrowup")) {
        inputForward += 1;
      }
      if (pressed.has("s") || pressed.has("arrowdown")) {
        inputForward -= 1;
      }
      let moveX = 0;
      let moveZ = 0;
      const manualMove = inputForward !== 0 || inputStrafe !== 0;
      let manualHeading = playerStateRef.current.heading;
      if (inputTurn !== 0) {
        headingVelocity = THREE.MathUtils.lerp(headingVelocity, inputTurn * 0.052, Math.min(0.16 * elapsed, 1));
      } else {
        headingVelocity = THREE.MathUtils.lerp(headingVelocity, 0, Math.min(0.12 * elapsed, 1));
      }
      manualHeading += headingVelocity * elapsed;
      if (manualMove) {
        const forwardX = Math.sin(manualHeading);
        const forwardZ = -Math.cos(manualHeading);
        const rightX = Math.cos(manualHeading);
        const rightZ = Math.sin(manualHeading);
        moveX = forwardX * inputForward + rightX * inputStrafe;
        moveZ = forwardZ * inputForward + rightZ * inputStrafe;
      }
      if (moveX !== 0 || moveZ !== 0) {
        const length = Math.hypot(moveX, moveZ) || 1;
        moveX /= length;
        moveZ /= length;
        if (inputForward !== 0) {
          manualHeading = Math.atan2(moveX, -moveZ);
        }
      } else if (streetAutoTargetRef.current) {
        const autoTarget = streetAutoTargetRef.current;
        const citizenDistance = autoTarget.citizenPosition
          ? Math.hypot(autoTarget.citizenPosition[0] - playerStateRef.current.x, autoTarget.citizenPosition[2] - playerStateRef.current.z)
          : Infinity;
        const deltaX = autoTarget.x - playerStateRef.current.x;
        const deltaZ = autoTarget.z - playerStateRef.current.z;
        const distance = Math.hypot(deltaX, deltaZ);
        if (citizenDistance <= (autoTarget.stopRadius ?? 4.1) || distance < 0.34) {
          streetAutoTargetRef.current = null;
          if (autoTarget.kind === "approach" && autoTarget.citizenId) {
            streetCallbacksRef.current.onStreetPreviewChange?.(autoTarget.citizenId);
            if (autoTarget.startVoiceOnArrival) {
              streetCallbacksRef.current.onStartVoice?.(autoTarget.citizenId);
            } else {
              streetCallbacksRef.current.onStreetFocusChange?.(autoTarget.citizenId);
            }
          }
        } else {
          moveX = deltaX / distance;
          moveZ = deltaZ / distance;
          manualHeading = autoTarget.heading ?? Math.atan2(moveX, -moveZ);
        }
      }
      if (gate.citizenConversationLocked) {
        velocityX = THREE.MathUtils.lerp(velocityX, 0, Math.min(0.34 * elapsed, 1));
        velocityZ = THREE.MathUtils.lerp(velocityZ, 0, Math.min(0.34 * elapsed, 1));
        if (Math.abs(velocityX) > 0.0005 || Math.abs(velocityZ) > 0.0005) {
          const nextState = {
            x: THREE.MathUtils.clamp(playerStateRef.current.x + velocityX * elapsed, STREET_BOUNDS.minX, STREET_BOUNDS.maxX),
            z: THREE.MathUtils.clamp(playerStateRef.current.z + velocityZ * elapsed, STREET_BOUNDS.minZ, STREET_BOUNDS.maxZ),
            heading: playerStateRef.current.heading,
          };
          playerStateRef.current = nextState;
          commitPlayerPose(nextState, now);
        }
        animationFrame = window.requestAnimationFrame(tick);
        return;
      }
      const walkSpeed = streetAutoTargetRef.current ? 0.134 : 0.118;
      const targetVelocityX = moveX * walkSpeed;
      const targetVelocityZ = moveZ * walkSpeed;
      const velocityBlend = Math.min(moveX !== 0 || moveZ !== 0 ? 0.046 * elapsed : 0.038 * elapsed, 1);
      velocityX = THREE.MathUtils.lerp(velocityX, targetVelocityX, velocityBlend);
      velocityZ = THREE.MathUtils.lerp(velocityZ, targetVelocityZ, velocityBlend);
      if (Math.abs(velocityX) < 0.001) {
        velocityX = 0;
      }
      if (Math.abs(velocityZ) < 0.001) {
        velocityZ = 0;
      }
      const headingTarget =
        streetAutoTargetRef.current?.heading != null
          ? streetAutoTargetRef.current.heading
          : manualHeading;
      const angleDelta = Math.atan2(
        Math.sin(headingTarget - playerStateRef.current.heading),
        Math.cos(headingTarget - playerStateRef.current.heading),
      );
      const nextHeading = playerStateRef.current.heading + angleDelta * Math.min((inputTurn !== 0 ? 0.066 : 0.038) * elapsed, 1);
      if (velocityX !== 0 || velocityZ !== 0 || Math.abs(nextHeading - playerStateRef.current.heading) > 0.001) {
        const nextState = {
          x: THREE.MathUtils.clamp(playerStateRef.current.x + velocityX * elapsed, STREET_BOUNDS.minX, STREET_BOUNDS.maxX),
          z: THREE.MathUtils.clamp(playerStateRef.current.z + velocityZ * elapsed, STREET_BOUNDS.minZ, STREET_BOUNDS.maxZ),
          heading: nextHeading,
        };
        playerStateRef.current = nextState;
        commitPlayerPose(nextState, now);
      }
      animationFrame = window.requestAnimationFrame(tick);
    };
    window.addEventListener("keydown", handleKeyDown);
    window.addEventListener("keyup", handleKeyUp);
    animationFrame = window.requestAnimationFrame(tick);
    return () => {
      window.removeEventListener("keydown", handleKeyDown);
      window.removeEventListener("keyup", handleKeyUp);
      window.cancelAnimationFrame(animationFrame);
    };
  }, [room]);

  useEffect(() => {
    if (room !== "citizens") {
      return;
    }
    if (!streetBootedRef.current) {
      streetBootedRef.current = true;
    }
    if (citizenConversationLocked) {
      streetAutoTargetRef.current = null;
    }
  }, [citizenConversationLocked, room]);

  const voiceChannelOpen = presence.liveMode === "voice" && presence.status === "connected";
  const conversationMicLive = voiceChannelOpen && !presence.muted;
  const stagePollingReady = stage.poll_summaries.length > 0;
  const streetSelectionLocked = room === "citizens" && citizenConversationLocked;
  const lockedStreetCitizen = streetSelectionLocked ? activeCitizen : undefined;
  const hoveredCitizen =
    room === "citizens" && hoveredCitizenId
      ? citizens.find((citizen) => citizen.citizen_id === hoveredCitizenId)
      : undefined;
  const focusedStreetCitizen = useMemo(() => {
    if (room !== "citizens") {
      return activeCitizen;
    }
    if (lockedStreetCitizen) {
      return lockedStreetCitizen;
    }
    return previewCitizen ?? activeCitizen;
  }, [activeCitizen, lockedStreetCitizen, previewCitizen, room]);
  const currentCitizen = room === "citizens" ? focusedStreetCitizen : activeCitizen;
  const liveCitizen = room === "citizens" ? activeCitizen : undefined;
  const citizenCardCitizen =
    room === "citizens"
      ? lockedStreetCitizen
      : undefined;
  const currentCitizenDistance =
    room === "citizens" && currentCitizen
      ? Math.hypot(
          (streetPlacementMap.get(currentCitizen.citizen_id)?.position[0] ?? playerPoseSnapshot.x) - playerPoseSnapshot.x,
          (streetPlacementMap.get(currentCitizen.citizen_id)?.position[2] ?? playerPoseSnapshot.z) - playerPoseSnapshot.z,
        )
      : Infinity;
  const citizenCardDistance =
    room === "citizens" && citizenCardCitizen
      ? Math.hypot(
          (streetPlacementMap.get(citizenCardCitizen.citizen_id)?.position[0] ?? playerPoseSnapshot.x) - playerPoseSnapshot.x,
          (streetPlacementMap.get(citizenCardCitizen.citizen_id)?.position[2] ?? playerPoseSnapshot.z) - playerPoseSnapshot.z,
        )
      : Infinity;
  const currentCitizenPlacement = useMemo(() => {
    if (room !== "citizens" || !currentCitizen) {
      return undefined;
    }
    return streetPlacementMap.get(currentCitizen.citizen_id);
  }, [currentCitizen, room, streetPlacementMap]);
  const citizenInteractionReady = room === "citizens" && currentCitizenDistance < STREET_READY_DISTANCE;
  const citizenCardReady = room === "citizens" && citizenCardDistance < STREET_READY_DISTANCE;
  const voiceChannelConnecting = presence.status === "connecting" && presence.liveMode === "voice";
  const textChannelPreparing = presence.status === "connecting" && presence.liveMode === "text";
  const approachingCitizenId =
    room === "citizens" && streetAutoTargetRef.current?.kind === "approach" ? streetAutoTargetRef.current.citizenId : undefined;
  const voiceButtonLabel =
    voiceChannelConnecting
      ? "Joining..."
      : presence.status === "error"
        ? "Retry"
      : voiceChannelOpen
        ? presence.muted
          ? "Resume mic"
          : "Pause mic"
        : room === "citizens"
          ? currentCitizen
            ? citizenInteractionReady
              ? "Speak"
              : approachingCitizenId === currentCitizen.citizen_id
                ? "Approaching…"
                : "Approach"
            : "Pick someone"
          : "Speak";
  const voiceButtonHint =
    voiceChannelConnecting
      ? "Opening"
      : presence.status === "error"
        ? "Retry connection"
        : "";
  const voiceTriggerCompact = false;
  const showSceneCaption =
    Boolean(captionText) &&
    councilCaptionVisible &&
    (
      room !== "citizens" ||
      citizenConversationLocked ||
      !currentCitizen ||
      currentCitizen.citizen_id === activeCitizen?.citizen_id
    );
  const townHallBannerActive =
    townHallState?.phase === "generating" ||
    townHallState?.phase === "voter_speaking" ||
    townHallState?.phase === "player_turn" ||
    townHallState?.phase === "opponent_turn" ||
    Boolean(townHallState?.error);
  const showTownHallBanner =
    room === "debate" &&
    auditoriumMode === "town_hall" &&
    Boolean(townHallState) &&
    townHallBannerActive;
  const denseBottomChrome =
    textComposerOpen ||
    showStageAdvance ||
    showTownHallQuestionAction ||
    showTownHallBanner;
  const textComposerInputRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    if (!textComposerAvailable || !textComposerOpen) {
      return;
    }
    const timer = window.setTimeout(() => {
      textComposerInputRef.current?.focus();
      textComposerInputRef.current?.select();
    }, 40);
    return () => window.clearTimeout(timer);
  }, [textComposerAvailable, textComposerOpen]);

  function handleVoiceTrigger() {
    if (room === "citizens") {
      if (!currentCitizen || !currentCitizenPlacement) {
        return;
      }
      if (!citizenInteractionReady) {
        onStreetPreviewChange?.(currentCitizen.citizen_id);
        streetAutoTargetRef.current = {
          kind: "approach",
          ...streetApproachPoint(currentCitizenPlacement.position),
          citizenId: currentCitizen.citizen_id,
          citizenPosition: currentCitizenPlacement.position,
          stopRadius: STREET_READY_DISTANCE - 0.6,
          startVoiceOnArrival: true,
        };
        return;
      }
    }
    onStartVoice?.(room === "citizens" ? currentCitizen?.citizen_id : undefined);
  }
  function handleComposerSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    onTextComposerSend?.();
  }
  const canvasDpr: [number, number] =
    room === "citizens" ? [0.66, 0.84] : room === "advisor" ? [1.04, 1.28] : [0.9, 1.08];
  const enableSceneShadows = room !== "citizens";

  return (
    <section
      className={`scene scene--${room} scene--theme-${themeMode} ${panelsOpen ? "scene--panels-open" : ""} ${
        denseBottomChrome ? "scene--dense-bottom" : ""
      }`}
    >
      <div className="scene__canvas">
        <Canvas
          dpr={canvasDpr}
          shadows={enableSceneShadows ? "percentage" : false}
          gl={{ antialias: true, powerPreference: "high-performance" }}
        >
          <SceneWorld
            key={`${room}:${advisorMode}:${themeMode}`}
            room={room}
            advisorMode={advisorMode}
            stage={stage}
            councilRoster={normalizedCouncilRoster}
            playerInPower={playerInPower}
            activeCitizen={activeCitizen}
            previewCitizen={previewCitizen}
            advisorNotes={advisorNotes}
            debateNotes={debateNotes}
            presence={presence}
            config={config}
            palette={palette}
            councilSpeaker={room === "advisor" && advisorMode === "council" ? activeCouncilLead : undefined}
            hotspots={hotspots}
            themeMode={themeMode}
            streetPlacements={streetPlacements}
            streetExtras={streetExtras}
            streetAutoTargetRef={streetAutoTargetRef}
            playerStateRef={playerStateRef}
            playerPose={playerPoseSnapshot}
            hoveredCitizenId={hoveredCitizenId}
            panelsOpen={panelsOpen}
            overlayActive={overlayActive}
            resolvingStage={resolvingStage}
            onHoveredCitizenChange={setHoveredCitizenId}
            onStreetFocusChange={onStreetFocusChange}
            onStreetPreviewChange={onStreetPreviewChange}
            onHotspotSelect={onHotspotSelect}
            onPrimaryInteract={onPrimaryInteract}
          />
        </Canvas>
      </div>

      {!overlayActive && panelsOpen ? (
        <div className="scene__badge">
          <span>{roomTitle(room, playerInPower, advisorMode)}</span>
          <strong>{stage.year_label}</strong>
        </div>
      ) : null}

      {panelsOpen && room === "briefing" ? (
        <div className="scene__hud">
          <span className="scene__eyebrow">{roomTitle(room, playerInPower, advisorMode)}</span>
          <h2>{stage.title}</h2>
          <p>{roomNote(room, stage, activeCitizen, playerInPower, advisorMode)}</p>
          <div className="scene__chips">
            <span>{stage.phase_label}</span>
            <span>{stage.year_label}</span>
            <span>{stagePollingReady ? `${stage.tracking.approval.display} approval` : "Approval pending"}</span>
            {presence.liveMode === "voice" && presence.status === "connected"
              ? <span>{presence.muted ? "mic paused" : "live mic"}</span>
              : null}
          </div>
        </div>
      ) : null}

      {showSceneUtilities ? (
        <div className="scene__controls">
          {onTogglePanels ? (
            <button className={`scene-control ${detailsOpen ? "scene-control--active" : ""}`} onClick={onTogglePanels}>
              {detailsOpen ? "Hide details" : "Details"}
            </button>
          ) : null}
          {onOpenReels ? (
            <button className="scene-control" onClick={onOpenReels}>
              {reelsLabel}
            </button>
          ) : null}
          {onToggleTheme ? (
            <button className="scene-control" onClick={onToggleTheme}>
              {themeLabel}
            </button>
          ) : null}
          {onToggleFullscreen ? (
            <button className="scene-control" onClick={onToggleFullscreen}>
              {fullscreenLabel}
            </button>
          ) : null}
        </div>
      ) : null}

      {!overlayActive ? (
        <>
          <div className="scene__channel-stack">
            {showTownHallQuestionAction || showStageAdvance ? (
              <div className="scene__action-row">
                {showTownHallQuestionAction ? (
                  <button
                    className={`scene__townhall-action ${townHallQuestionDisabled ? "scene__townhall-action--disabled" : ""}`}
                    onClick={() => onTownHallQuestion?.()}
                    disabled={townHallQuestionDisabled}
                    title={townHallDisabled ? townHallDisabledReason : "Open the floor for the next audience question."}
                  >
                    <span>{townHallQuestionLabel}</span>
                    <small>Let one voter test the room before the next exchange.</small>
                  </button>
                ) : null}
                {showStageAdvance ? (
                  <button
                    className={`scene__stage-action ${stageAdvanceDisabled ? "scene__stage-action--disabled" : ""}`}
                    onClick={() => onStageAdvance?.()}
                    disabled={stageAdvanceDisabled}
                    title={stageAdvanceHint}
                  >
                    <span>{stageAdvanceLabel}</span>
                    <small>{stageAdvanceHint}</small>
                  </button>
                ) : null}
              </div>
            ) : null}
            <div className={`scene__channel-bar ${textComposerOpen ? "scene__channel-bar--text-open" : ""}`}>
              <button
                className={`scene__voice-trigger scene__voice-trigger--scene ${ 
                  conversationMicLive
                    ? "scene__voice-trigger--live"
                    : voiceChannelOpen && (presence.voicePhase === "waiting" || presence.voicePhase === "responding" || presence.muted)
                      ? "scene__voice-trigger--muted"
                      : ""
                } ${voiceTriggerCompact ? "scene__voice-trigger--compact" : ""}`}
                disabled={
                  resolvingStage ||
                  (room === "citizens" && presence.status === "idle" && !currentCitizen)
                }
                onClick={handleVoiceTrigger}
                aria-label={voiceButtonLabel}
              >
                <span className="scene__voice-trigger-icon" aria-hidden="true">
                  {voiceChannelConnecting
                    ? "◌"
                    : conversationMicLive
                      ? "●"
                      : "○"}
                </span>
                <span className="scene__voice-trigger-copy">
                  <strong>{voiceButtonLabel}</strong>
                </span>
              </button>
              {textComposerAvailable && textComposerOpen ? (
                <form
                  className="scene__inline-composer"
                  data-testid="scene-inline-composer"
                  onSubmit={handleComposerSubmit}
                >
                  <input
                    ref={textComposerInputRef}
                    type="text"
                    value={textComposerDraft}
                    onChange={(event) => onTextComposerChange?.(event.target.value)}
                    placeholder={textPlaceholder(room, currentCitizen, advisorMode, auditoriumMode, stage)}
                    aria-label="Type a turn"
                    data-testid="scene-inline-input"
                  />
                  <button
                    type="submit"
                    aria-label="Send text turn"
                    disabled={!textComposerDraft.trim()}
                    data-testid="scene-inline-send"
                  >
                    Send
                  </button>
                </form>
              ) : null}
              {textComposerAvailable ? (
              <button
                type="button"
                className={`scene__text-trigger ${textComposerOpen ? "scene__text-trigger--active" : ""}`}
                onClick={onTextComposerToggle}
                aria-label={textComposerOpen ? "Hide text input" : "Open text input"}
                aria-pressed={textComposerOpen}
                title={textComposerOpen ? "Hide text input" : "Type instead"}
                data-testid="scene-text-trigger"
              >
                  <span aria-hidden="true">⌨</span>
                  <span className="sr-only">{textComposerOpen ? "Hide text input" : "Open text input"}</span>
                </button>
              ) : null}
            </div>
          </div>

          {showCouncilFloorCue ? (
            <div className={`scene-council-floor ${activeCouncilLead === "You" ? "scene-council-floor--player" : ""}`}>
              <span>{councilFloorCueLabel}</span>
            </div>
          ) : null}

          {!panelsOpen && showTownHallBanner ? (
            <div className={`scene-townhall-floor ${townHallState?.error ? "scene-townhall-floor--error" : ""}`}>
              <span>{townHallState?.label}</span>
              <strong>{townHallState?.speaker ? `${townHallState.speaker} has the mic` : "The crowd has the mic"}</strong>
              <p>{townHallState?.error ?? townHallState?.question ?? townHallState?.detail}</p>
            </div>
          ) : null}

          {!panelsOpen && showSceneCaption && !showTownHallBanner && !citizenCardCitizen ? (
            <>
              <div className={`scene__caption scene__caption--${captionSpeaker ?? "assistant"} scene__caption--centered`}>
                <span>
                  {captionSpeaker === "user"
                    ? "You"
                    : room === "advisor"
                      ? advisorMode === "council"
                        ? activeCouncilLead ?? "Advisor table"
                        : "Advisor"
                      : room === "debate"
                        ? auditoriumMode === "town_hall"
                          ? townHallState?.phase === "opponent_turn"
                            ? "Opponent"
                            : townHallState?.speaker ?? "Audience"
                          : "Opponent"
                        : compactCitizenLabel(activeCitizen?.display_name ?? "Citizen")}
                </span>
                {councilCaptionLines.length > 1 ? (
                  <p className="scene__caption-lines">
                    {councilCaptionLines.map((line, index) => (
                      <span key={`${index}-${line}`}>{line}</span>
                    ))}
                  </p>
                ) : (
                  <p>{visibleCaptionText}</p>
                )}
              </div>
            </>
          ) : null}

          {!panelsOpen && room === "citizens" && citizenCardCitizen ? (
            <div className={`scene__citizen-chip scene__citizen-chip--floating ${citizenCardReady ? "scene__citizen-chip--ready" : ""}`}>
              <strong>{citizenCardCitizen.display_name}</strong>
              <small className="scene__citizen-chip-meta">
                {boardSnippet(`${citizenCardCitizen.role} · ${citizenCardCitizen.region}`, 72)}
              </small>
              <small className="scene__citizen-chip-status">
                {citizenCardReady ? "ready to talk" : "move closer to talk"}
              </small>
            </div>
          ) : null}
        </>
      ) : null}

    </section>
  );
}

interface SceneWorldProps {
  room: RoomName;
  advisorMode: AdvisorMode;
  stage: StagePackage;
  councilRoster: ReturnType<typeof normalizeCouncilRoster>;
  playerInPower: boolean;
  activeCitizen?: CitizenSnapshot;
  previewCitizen?: CitizenSnapshot;
  advisorNotes: string[];
  debateNotes: string[];
  presence: ScenePresence;
  config: (typeof ROOM_CONFIGS)[RoomName];
  themeMode: "light" | "dark";
  palette: { base: string; glow: string; metallic: string };
  councilSpeaker?: string;
  hotspots: SceneHotspot[];
  streetPlacements: StreetPlacement[];
  streetExtras: StreetExtra[];
  streetAutoTargetRef: MutableRefObject<StreetAutoTarget | null>;
  playerStateRef: MutableRefObject<StreetPlayerState>;
  playerPose: StreetPlayerState;
  hoveredCitizenId: string | null;
  panelsOpen: boolean;
  overlayActive: boolean;
  resolvingStage?: boolean;
  onHoveredCitizenChange?: (citizenId: string | null) => void;
  onStreetFocusChange?: (citizenId?: string) => void;
  onStreetPreviewChange?: (citizenId?: string) => void;
  onHotspotSelect?: (hotspot: SceneHotspot) => void;
  onPrimaryInteract?: (citizenId?: string) => void;
}

function SceneWorld({
  room,
  advisorMode,
  stage,
  councilRoster,
  playerInPower,
  activeCitizen,
  previewCitizen,
  advisorNotes,
  debateNotes,
  presence,
  config,
  themeMode,
  palette,
  councilSpeaker,
  hotspots,
  streetPlacements,
  streetExtras,
  streetAutoTargetRef,
  playerStateRef,
  playerPose,
  hoveredCitizenId,
  panelsOpen,
  overlayActive,
  resolvingStage = false,
  onHoveredCitizenChange,
  onStreetFocusChange,
  onStreetPreviewChange,
  onHotspotSelect,
  onPrimaryInteract,
}: SceneWorldProps) {
  const advisorDaylight = room === "advisor" && themeMode === "light";
  const citizenDaylight = room === "citizens" && themeMode === "light";
  const streetPlacementMap = useMemo(
    () => new Map(streetPlacements.map((entry) => [entry.citizen.citizen_id, entry])),
    [streetPlacements],
  );
  const citizenConversationLocked =
    room === "citizens" &&
    Boolean(activeCitizen?.citizen_id) &&
    (
      presence.status === "connecting" ||
      presence.playerActivity === "speaking" ||
      presence.counterpartActivity === "speaking"
    );
  const voiceChannelOpen = presence.liveMode === "voice" && presence.status === "connected";
  const advisorCouncilMode = room === "advisor" && advisorMode === "council";
  const playerHasCouncilFloor = advisorCouncilMode && councilSpeaker === "You";
  const streetSelectionLocked = room === "citizens" && citizenConversationLocked;
  const targetPosition =
    room === "citizens"
      ? streetPlacementMap.get(activeCitizen?.citizen_id ?? "")?.position ?? counterpartPosition(room, advisorMode)
      : counterpartPosition(room, advisorMode);
  const rankedStreetPlacements = useMemo(
    () =>
      room !== "citizens"
        ? []
        : [...streetPlacements]
            .map((entry) => ({
              entry,
              distance: Math.hypot(entry.position[0] - playerPose.x, entry.position[2] - playerPose.z),
            }))
            .filter(
              ({ entry, distance }) =>
                (Math.abs(entry.position[2] - playerPose.z) < 38 && distance < 24) ||
                entry.citizen.citizen_id === activeCitizen?.citizen_id ||
                entry.citizen.citizen_id === previewCitizen?.citizen_id ||
                entry.citizen.citizen_id === hoveredCitizenId,
            )
            .sort((left, right) => left.distance - right.distance),
    [activeCitizen?.citizen_id, hoveredCitizenId, playerPose.x, playerPose.z, previewCitizen?.citizen_id, room, streetPlacements],
  );
  const featuredStreetPlacements = useMemo(
    () =>
      rankedStreetPlacements
        .filter(
          ({ entry, distance }) =>
            distance < STREET_FEATURE_DISTANCE ||
            entry.citizen.citizen_id === activeCitizen?.citizen_id ||
            entry.citizen.citizen_id === previewCitizen?.citizen_id ||
            entry.citizen.citizen_id === hoveredCitizenId,
        )
        .slice(0, 26)
        .map(({ entry }) => entry),
    [activeCitizen?.citizen_id, hoveredCitizenId, previewCitizen?.citizen_id, rankedStreetPlacements],
  );
  const ambientStreetPlacements = useMemo(
    () =>
      rankedStreetPlacements
        .filter(({ entry }) => !featuredStreetPlacements.some((featured) => featured.citizen.citizen_id === entry.citizen.citizen_id))
        .slice(0, 18)
        .map(({ entry }) => entry),
    [featuredStreetPlacements, rankedStreetPlacements],
  );
  useEffect(() => {
    if (room !== "citizens" || streetSelectionLocked || hoveredCitizenId || streetAutoTargetRef.current?.kind === "approach") {
      return;
    }
	    const nearest = rankedStreetPlacements.find(({ distance }) => distance < STREET_READY_DISTANCE);
	    const previewId = previewCitizen?.citizen_id ?? activeCitizen?.citizen_id;
	    const floatingVoiceOpen = voiceChannelOpen && !presence.muted;
	    if (nearest) {
	      const nextId = nearest.entry.citizen.citizen_id;
	      if (nextId !== previewId) {
	        onStreetPreviewChange?.(nextId);
	      }
	      if (floatingVoiceOpen && nextId !== activeCitizen?.citizen_id) {
	        onStreetFocusChange?.(nextId);
	      }
	      return;
	    }
	    if (previewId) {
	      const previewDistance = rankedStreetPlacements.find(({ entry }) => entry.citizen.citizen_id === previewId)?.distance ?? Infinity;
	      if (previewDistance > STREET_HIGHLIGHT_DISTANCE) {
	        onStreetPreviewChange?.(undefined);
	        if (floatingVoiceOpen && activeCitizen?.citizen_id) {
	          onStreetFocusChange?.(undefined);
	        }
	      }
	    }
	  }, [
	    activeCitizen?.citizen_id,
	    hoveredCitizenId,
	    onStreetFocusChange,
	    onStreetPreviewChange,
	    presence.muted,
	    previewCitizen?.citizen_id,
	    rankedStreetPlacements,
	    room,
	    streetAutoTargetRef,
	    streetSelectionLocked,
	    voiceChannelOpen,
	  ]);
  const streetSelectionCitizenId =
    room !== "citizens"
      ? undefined
      : streetSelectionLocked
        ? activeCitizen?.citizen_id
        : previewCitizen?.citizen_id ?? activeCitizen?.citizen_id ?? hoveredCitizenId;
  const highlightedStreetPlacement = useMemo(
    () => {
      if (room !== "citizens") {
        return undefined;
      }
      const targetCitizenId = streetSelectionCitizenId ?? hoveredCitizenId ?? undefined;
      if (!targetCitizenId) {
        return undefined;
      }
      return (
        featuredStreetPlacements.find(({ citizen }) => citizen.citizen_id === targetCitizenId) ??
        rankedStreetPlacements.find(({ entry }) => entry.citizen.citizen_id === targetCitizenId)?.entry
      );
    },
    [featuredStreetPlacements, rankedStreetPlacements, room, streetSelectionCitizenId],
  );
  const cameraStreetPlacement = useMemo(
    () => {
      if (room !== "citizens") {
        return undefined;
      }
      const targetCitizenId =
        (streetAutoTargetRef.current?.kind === "approach" ? streetAutoTargetRef.current.citizenId : undefined) ??
        (streetSelectionLocked ? activeCitizen?.citizen_id : undefined);
      if (!targetCitizenId) {
        return undefined;
      }
      return (
        featuredStreetPlacements.find(({ citizen }) => citizen.citizen_id === targetCitizenId) ??
        rankedStreetPlacements.find(({ entry }) => entry.citizen.citizen_id === targetCitizenId)?.entry
      );
    },
    [activeCitizen?.citizen_id, featuredStreetPlacements, rankedStreetPlacements, room, streetSelectionLocked],
  );
  const highlightedStreetDistance = highlightedStreetPlacement
    ? Math.hypot(highlightedStreetPlacement.position[0] - playerPose.x, highlightedStreetPlacement.position[2] - playerPose.z)
    : Infinity;
  const highlightedStreetActive = highlightedStreetPlacement?.citizen.citizen_id === activeCitizen?.citizen_id;
  const approachingCitizenId =
    room === "citizens" && streetAutoTargetRef.current?.kind === "approach" ? streetAutoTargetRef.current.citizenId : undefined;
  const showStreetCitizenLabel =
    room === "citizens" &&
    Boolean(highlightedStreetPlacement) &&
    (
      highlightedStreetPlacement?.citizen.citizen_id === hoveredCitizenId ||
      highlightedStreetPlacement?.citizen.citizen_id === approachingCitizenId ||
      (
        streetSelectionLocked &&
        highlightedStreetPlacement?.citizen.citizen_id === activeCitizen?.citizen_id
      ) ||
      (
        highlightedStreetPlacement?.citizen.citizen_id === streetSelectionCitizenId &&
        highlightedStreetDistance < STREET_READY_DISTANCE
      )
    );
  function handleStreetCitizenSelect(entry: StreetPlacement) {
    if (streetSelectionLocked) {
      return;
    }
    onStreetPreviewChange?.(entry.citizen.citizen_id);
    const distance = Math.hypot(entry.position[0] - playerStateRef.current.x, entry.position[2] - playerStateRef.current.z);
    if (distance > STREET_READY_DISTANCE - 0.45) {
      streetAutoTargetRef.current = {
        kind: "approach",
        ...streetApproachPoint(entry.position),
        citizenId: entry.citizen.citizen_id,
        citizenPosition: entry.position,
        stopRadius: STREET_READY_DISTANCE - 0.6,
        startVoiceOnArrival: false,
      };
    } else {
      streetAutoTargetRef.current = null;
      onStreetFocusChange?.(entry.citizen.citizen_id);
    }
  }
  const playerPalette = playerInPower
    ? { base: "#4f667d", glow: "#e7d7b5", metallic: "#8aa1b8" }
    : { base: "#73504a", glow: "#f0decc", metallic: "#9d7a73" };
  const councilPalettes = [
    { base: "#6f7f8d", glow: "#dce8f0", metallic: "#8a99ab" },
    { base: "#7a6556", glow: "#f0d8c7", metallic: "#a38b78" },
    { base: "#6c7287", glow: "#dce1ef", metallic: "#8990ab" },
    { base: "#6d6d5b", glow: "#efe3bf", metallic: "#9e9a7d" },
    { base: "#72656f", glow: "#e8d6ea", metallic: "#9987a2" },
    { base: "#5f7469", glow: "#d8eadf", metallic: "#7f988b" },
  ] as const;
  const councilAdvisors = useMemo(() => {
    const roster = councilRoster.slice(0, 6);
    const span = roster.length <= 2 ? 3.7 : roster.length === 3 ? 3.1 : roster.length >= 6 ? 2.18 : 2.55;
    return roster.map((advisor, index) => {
      const centeredIndex = index - (roster.length - 1) / 2;
      const x = centeredIndex * span;
      const z = Math.abs(centeredIndex) < 0.6 ? -0.98 : roster.length >= 6 ? 0.1 + Math.abs(centeredIndex) * 0.08 : roster.length >= 4 ? -0.22 : -0.58;
      return {
        ...advisor,
        position: [x, Math.abs(centeredIndex) < 0.6 ? 0.02 : 0, z] as [number, number, number],
        facing: THREE.MathUtils.clamp(-x * 0.05, -0.22, 0.22),
        palette: councilPalettes[index % councilPalettes.length],
      };
    });
  }, [councilRoster]);

  return (
    <>
      <PerspectiveCamera
        makeDefault
        position={config.camera}
        fov={room === "citizens" ? 32 : room === "advisor" ? (advisorCouncilMode ? 42 : 37) : room === "debate" ? 34 : 34}
      />
      <color attach="background" args={[config.background]} />
      <fog attach="fog" args={[config.background, config.fogNear, config.fogFar]} />

      <ambientLight
        intensity={advisorDaylight ? 0.46 : citizenDaylight ? 0.74 : themeMode === "light" ? 1.02 : 0.96}
        color={advisorDaylight ? "#e9d8bf" : citizenDaylight ? "#eef2f3" : themeMode === "light" ? "#f4efe7" : "#f3e2c6"}
      />
      <hemisphereLight
        intensity={advisorDaylight ? 0.28 : citizenDaylight ? 0.56 : themeMode === "light" ? 0.84 : 0.72}
        groundColor={advisorDaylight ? "#7e6a57" : citizenDaylight ? "#695d53" : themeMode === "light" ? "#6f665c" : "#18110d"}
        color={advisorDaylight ? "#eadfcf" : citizenDaylight ? "#deebf1" : themeMode === "light" ? "#eef6fb" : "#ded1bd"}
      />
      <spotLight
        position={[0, 7.8, 4.8]}
        angle={0.46}
        penumbra={0.58}
        intensity={advisorDaylight ? 22 : themeMode === "light" ? 72 : 92}
        color={advisorDaylight ? "#efd3ab" : config.accent}
        castShadow
      />
      <spotLight
        position={advisorDaylight ? [-5.6, 6.2, 1.8] : [-4.8, 5.8, -3.2]}
        angle={advisorDaylight ? 0.62 : 0.52}
        penumbra={0.68}
        intensity={advisorDaylight ? 9.8 : themeMode === "light" ? 40 : 32}
        color={advisorDaylight ? "#f4e5c7" : config.fill}
      />
      <pointLight position={[4.8, 2.8, 3.6]} intensity={advisorDaylight ? 5.8 : themeMode === "light" ? 13 : 11} color={advisorDaylight ? "#dec39d" : config.accent} />
      {advisorDaylight ? <directionalLight position={[6.4, 5.6, 3.2]} intensity={0.62} color="#ede2d0" /> : null}
      {advisorDaylight ? <pointLight position={[-5.4, 3.8, 0.5]} intensity={3.2} color="#ead8bc" /> : null}
      {citizenDaylight ? <directionalLight position={[4.8, 5.8, 6.5]} intensity={0.58} color="#f0e8dc" /> : null}
      {citizenDaylight ? <pointLight position={[-3.8, 3.4, 2.8]} intensity={2.9} color="#e8d4bd" /> : null}

      <SceneRig
        target={config.camera}
        focus={config.focus}
        room={room}
        playerStateRef={playerStateRef}
        streetFocus={cameraStreetPlacement?.position}
      />
      <RoomShell room={room} advisorMode={advisorMode} accent={config.accent} fill={config.fill} themeMode={themeMode} />
      <RoomDecor room={room} advisorMode={advisorMode} accent={config.accent} fill={config.fill} playerInPower={playerInPower} themeMode={themeMode} />
      {room === "citizens" ? (
        <mesh
          position={[0, 0.42, -24]}
          rotation={[-Math.PI / 2, 0, 0]}
          onClick={(event) => {
            event.stopPropagation();
            if (streetSelectionLocked) {
              return;
            }
            streetAutoTargetRef.current = {
              kind: "walk",
              x: THREE.MathUtils.clamp(event.point.x, STREET_BOUNDS.minX, STREET_BOUNDS.maxX),
              z: THREE.MathUtils.clamp(event.point.z, STREET_BOUNDS.minZ, STREET_BOUNDS.maxZ),
            };
            onHoveredCitizenChange?.(null);
            onStreetPreviewChange?.(undefined);
          }}
        >
          <planeGeometry args={[24.6, 116]} />
          <meshBasicMaterial transparent opacity={0} depthWrite={false} />
        </mesh>
      ) : null}

      <CharacterFigure
        position={
          room === "debate"
            ? [-2.8, 0, 3.12]
            : room === "citizens"
                ? [STREET_PLAYER_START.x, 0, STREET_PLAYER_START.z]
              : room === "advisor"
                ? advisorCouncilMode
                  ? [0, 0, 2.95]
                  : [-4.92, 0, -0.52]
                : [-2.85, 0, 1.02]
        }
        facing={room === "citizens" ? 0 : advisorCouncilMode ? 0.04 : 0.36}
        activity={presence.playerActivity}
        scale={room === "debate" ? 0.78 : room === "citizens" ? 0.48 : room === "advisor" ? (advisorCouncilMode ? 0.72 : 0.66) : 0.9}
        palette={playerPalette}
        followRef={room === "citizens" ? playerStateRef : undefined}
        floorActive={room === "advisor" && advisorCouncilMode && playerHasCouncilFloor}
      />
      {room === "citizens"
        ? featuredStreetPlacements.map(({ citizen, position, appearance }) => (
            <CharacterFigure
              key={citizen.citizen_id}
              position={position}
              facing={appearance.facing}
              activity={citizen.citizen_id === activeCitizen?.citizen_id && citizenConversationLocked ? presence.counterpartActivity : "idle"}
              scale={appearance.scale}
              palette={appearance.palette}
              silhouette={appearance.silhouette}
              interactive={!overlayActive && !streetSelectionLocked}
              highlighted={
                citizen.citizen_id === streetSelectionCitizenId ||
                citizen.citizen_id === activeCitizen?.citizen_id
              }
              onHoverChange={(hovered) => {
                if (streetSelectionLocked) {
                  return;
                }
                onHoveredCitizenChange?.(hovered ? citizen.citizen_id : null);
              }}
              onSelect={() => handleStreetCitizenSelect({ citizen, position, appearance })}
            />
          ))
        : null}
      {room === "citizens"
        ? ambientStreetPlacements.map(({ citizen, position, appearance }) => (
            <CharacterFigure
              key={`ambient-${citizen.citizen_id}`}
              position={position}
              facing={appearance.facing}
              activity="idle"
              scale={appearance.scale * 0.94}
              palette={appearance.palette}
              silhouette={appearance.silhouette}
              animate={false}
              interactive={false}
              shadows={false}
              opacity={0.72}
            />
          ))
        : null}
      {room === "citizens"
        ? streetExtras.map(({ id, position, appearance }) => (
            <CharacterFigure
              key={id}
              position={position}
              facing={appearance.facing}
              activity="idle"
              scale={appearance.scale * 0.76}
              palette={appearance.palette}
              silhouette={appearance.silhouette}
              animate={false}
              interactive={false}
              shadows={false}
              opacity={0.3}
            />
          ))
        : null}
      {advisorCouncilMode
        ? councilAdvisors.map((advisor) => {
            const leadingSpeaker = councilSpeaker;
            const advisorOwnsFloor = leadingSpeaker === advisor.name;
            const advisorPendingFloor = advisorOwnsFloor && presence.counterpartActivity !== "speaking";
            const advisorActivity =
              advisorOwnsFloor
                ? presence.counterpartActivity === "speaking"
                  ? "speaking"
                  : "listening"
                : presence.counterpartActivity === "listening" || presence.playerActivity === "speaking"
                  ? "listening"
                  : "idle";
            return (
              <group key={advisor.name}>
                <CharacterFigure
                  position={advisor.position}
                  facing={advisor.facing}
                  activity={advisorActivity}
                  scale={councilAdvisors.length >= 6 ? (Math.abs(advisor.position[0]) < 0.9 ? 0.62 : 0.58) : (Math.abs(advisor.position[0]) < 0.9 ? 0.66 : 0.62)}
                  palette={advisor.palette}
                  interactive={false}
                  highlighted={advisorOwnsFloor}
                  floorActive={advisorOwnsFloor}
                  floorPending={advisorPendingFloor}
                />
                <Html position={[advisor.position[0], 1.58, advisor.position[2] + 0.18]} center distanceFactor={11.6}>
                  <div
                    className={`scene-council-label ${
                      leadingSpeaker === advisor.name ? "scene-council-label--active" : ""
                    } ${advisorPendingFloor ? "scene-council-label--pending" : ""}`}
                  >
                    <strong>{advisor.name}</strong>
                    <small>{advisorPendingFloor ? "next voice" : advisor.role}</small>
                  </div>
                </Html>
              </group>
            );
          })
        : null}
      {room === "citizens"
        ? null
        : !advisorCouncilMode ? (
            <CharacterFigure
              position={targetPosition}
              facing={advisorCouncilMode ? 0 : -0.42}
              activity={presence.counterpartActivity}
              scale={room === "debate" ? 0.8 : room === "advisor" ? (advisorCouncilMode ? 0.68 : 0.7) : 0.64}
              palette={palette}
              interactive={false}
            />
          ) : null}
      {room === "citizens" && highlightedStreetPlacement && highlightedStreetDistance < STREET_HIGHLIGHT_DISTANCE ? (
        <group position={highlightedStreetPlacement.position}>
          <mesh position={[0, 0.06, 0]} rotation={[-Math.PI / 2, 0, 0]}>
            <ringGeometry args={[0.42, 0.62, 44]} />
            <meshStandardMaterial
              color={highlightedStreetPlacement.citizen.approval_band === "approve" ? "#9ec28b" : highlightedStreetPlacement.citizen.approval_band === "disapprove" ? "#c88878" : "#92b1cf"}
              emissive={highlightedStreetPlacement.citizen.approval_band === "approve" ? "#9ec28b" : highlightedStreetPlacement.citizen.approval_band === "disapprove" ? "#c88878" : "#92b1cf"}
              emissiveIntensity={highlightedStreetActive ? 0.24 : 0.12}
              transparent
              opacity={highlightedStreetActive ? 0.94 : 0.52}
            />
          </mesh>
        </group>
      ) : null}
      {showStreetCitizenLabel && highlightedStreetPlacement ? (
        <SceneCitizenLabel
          citizen={highlightedStreetPlacement.citizen}
          position={[highlightedStreetPlacement.position[0], 2.1, highlightedStreetPlacement.position[2]]}
          active={highlightedStreetPlacement.citizen.citizen_id === activeCitizen?.citizen_id}
          ready={highlightedStreetDistance < STREET_READY_DISTANCE}
          onSelect={() => handleStreetCitizenSelect(highlightedStreetPlacement)}
        />
      ) : null}

      {!overlayActive ? hotspots.map((hotspot) => (
        <SceneMarker
          key={hotspot.id}
          position={hotspot.position}
          label={hotspot.label}
          hint={hotspot.hint}
          tone={hotspot.tone}
          active={hotspot.active}
          variant={hotspot.action === "room" || hotspot.action === "townhall" ? "door" : hotspot.action === "panel" || hotspot.action === "resolve" ? "note" : "person"}
          disabled={hotspot.disabled ?? (hotspot.action === "resolve" && resolvingStage)}
          onSelect={() => onHotspotSelect?.(hotspot)}
        />
      )) : null}
      {room === "advisor" ? <AdvisorBoards stage={stage} playerInPower={playerInPower} notes={advisorNotes} themeMode={themeMode} layout={advisorMode} /> : null}
      {room === "debate" ? <DebateBoards stage={stage} notes={debateNotes} themeMode={themeMode} /> : null}

      {room !== "citizens" ? (
        <ContactShadows
          position={[0, 0.02, 0]}
          opacity={room === "advisor" ? 0.38 : 0.28}
          blur={room === "advisor" ? 2.1 : 1.5}
          scale={room === "debate" ? 13 : 10}
          far={6.2}
        />
      ) : null}
      {room === "briefing" ? (
        <Sparkles
          count={16}
          scale={[10, 4, 10]}
          size={2.6}
          speed={0.16}
          color={config.accent}
          position={[0, 2.2, 0]}
        />
      ) : null}
    </>
  );
}

const BOARD_HAND_FONT = "'Segoe Print', 'Bradley Hand', 'Chalkboard SE', 'Marker Felt', 'Noteworthy', cursive";
const BOARD_SANS_FONT = "'Avenir Next Condensed', 'DIN Condensed', 'IBM Plex Sans', 'Avenir Next', 'Segoe UI', sans-serif";
const BOARD_TEXTURE_CACHE_LIMIT = 18;
const VENUE_TEXTURE_CACHE_LIMIT = 10;
const BOARD_TEXTURE_CACHE = new Map<string, THREE.CanvasTexture>();
const VENUE_TEXTURE_CACHE = new Map<string, THREE.CanvasTexture>();

function rememberTexture(cache: Map<string, THREE.CanvasTexture>, key: string, texture: THREE.CanvasTexture, limit: number) {
  if (cache.has(key)) {
    return cache.get(key)!;
  }
  if (cache.size >= limit) {
    const oldestKey = cache.keys().next().value as string | undefined;
    if (oldestKey) {
      const staleTexture = cache.get(oldestKey);
      staleTexture?.dispose();
      cache.delete(oldestKey);
    }
  }
  cache.set(key, texture);
  return texture;
}

function boardRoundRect(ctx: CanvasRenderingContext2D, x: number, y: number, width: number, height: number, radius: number) {
  const insetRadius = Math.min(radius, width / 2, height / 2);
  ctx.beginPath();
  ctx.moveTo(x + insetRadius, y);
  ctx.arcTo(x + width, y, x + width, y + height, insetRadius);
  ctx.arcTo(x + width, y + height, x, y + height, insetRadius);
  ctx.arcTo(x, y + height, x, y, insetRadius);
  ctx.arcTo(x, y, x + width, y, insetRadius);
  ctx.closePath();
}

function boardWrapLines(ctx: CanvasRenderingContext2D, text: string, maxWidth: number, maxLines = 2) {
  const roughWords = text.trim().split(/\s+/).filter(Boolean);
  if (!roughWords.length) {
    return [];
  }
  const words = roughWords.flatMap((word) => {
    if (ctx.measureText(word).width <= maxWidth) {
      return [word];
    }
    const chunks: string[] = [];
    let chunk = "";
    for (const char of word) {
      const probe = `${chunk}${char}`;
      if (!chunk || ctx.measureText(probe).width <= maxWidth) {
        chunk = probe;
        continue;
      }
      chunks.push(chunk);
      chunk = char;
    }
    if (chunk) {
      chunks.push(chunk);
    }
    return chunks;
  });
  const lines: string[] = [];
  let current = words[0];
  for (let index = 1; index < words.length; index += 1) {
    const probe = `${current} ${words[index]}`;
    if (ctx.measureText(probe).width <= maxWidth) {
      current = probe;
      continue;
    }
    lines.push(current);
    current = words[index];
    if (lines.length === maxLines - 1) {
      break;
    }
  }
  if (lines.length < maxLines) {
    lines.push(current);
  }
  if (lines.length === maxLines) {
    const consumed = lines.join(" ").split(/\s+/).filter(Boolean).length;
    if (consumed < words.length) {
      lines[maxLines - 1] = `${lines[maxLines - 1].replace(/[.,;:!?-]+$/, "")}…`;
    }
  }
  return lines.slice(0, maxLines);
}

function boardTexture(panel: BoardPanel, themeMode: "light" | "dark", signature: string) {
  const cachedTexture = BOARD_TEXTURE_CACHE.get(signature);
  if (cachedTexture) {
    return cachedTexture;
  }
  const canvas = document.createElement("canvas");
  canvas.width = 3072;
  canvas.height = 1728;
  const ctx = canvas.getContext("2d");
  if (!ctx) {
    return new THREE.CanvasTexture(canvas);
  }

  const darkBoard = themeMode === "dark";
  const paper = darkBoard ? "#2b241c" : "#e7d8c3";
  const paperEdge = darkBoard ? "#15110d" : "#c89d71";
  const border = darkBoard ? "#f1dfbd" : "#21130a";
  const line = darkBoard ? "rgba(244, 225, 192, 0.2)" : "rgba(50, 31, 16, 0.28)";
  const marker = darkBoard ? "#fff1cf" : "#140b05";
  const markerSoft = darkBoard ? "#d8b979" : "#573726";
  const accent = darkBoard ? "#f0a95f" : "#7b4820";
  const paperGradient = ctx.createLinearGradient(0, 0, canvas.width, canvas.height);
  paperGradient.addColorStop(0, paper);
  paperGradient.addColorStop(1, paperEdge);
  ctx.fillStyle = paperGradient;
  ctx.fillRect(0, 0, canvas.width, canvas.height);

  const vignette = ctx.createRadialGradient(canvas.width / 2, canvas.height / 2, 640, canvas.width / 2, canvas.height / 2, canvas.width * 0.68);
  vignette.addColorStop(0, "rgba(255,255,255,0)");
  vignette.addColorStop(1, darkBoard ? "rgba(0, 0, 0, 0.18)" : "rgba(78, 54, 32, 0.09)");
  ctx.fillStyle = vignette;
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  ctx.strokeStyle = border;
  ctx.lineWidth = 16;
  ctx.strokeRect(42, 42, canvas.width - 84, canvas.height - 84);

  ctx.fillStyle = darkBoard ? "rgba(244, 225, 192, 0.026)" : "rgba(94, 68, 45, 0.024)";
  for (let index = 0; index < 48; index += 1) {
    const width = 52 + (index % 7) * 18;
    const height = 6 + (index % 5) * 2;
    const x = 120 + ((index * 197) % (canvas.width - 280));
    const y = 180 + ((index * 131) % (canvas.height - 360));
    ctx.fillRect(x, y, width, height);
  }

  ctx.fillStyle = accent;
  ctx.font = `700 120px ${BOARD_SANS_FONT}`;
  ctx.fillText(panel.kicker.toUpperCase(), 214, 212);

  if (panel.variant === "policy") {
    const policyItems = (panel.list?.slice(0, 4) ?? ["", "", "", ""]);
    const rowGap = 246;
    const rowStartY = 368;
    const itemFontSize = 76;
    const numberFontSize = 90;
    const lineStep = 68;
    const maxPolicyLines = 3;
    ctx.strokeStyle = line;
    ctx.lineWidth = 2.5;
    for (let y = 246; y < canvas.height - 152; y += 116) {
      ctx.beginPath();
      ctx.moveTo(138, y);
      ctx.lineTo(canvas.width - 138, y);
      ctx.stroke();
    }
    ctx.fillStyle = marker;
    policyItems.forEach((item, index) => {
      const rowY = rowStartY + index * rowGap;
      ctx.strokeStyle = line;
      ctx.lineWidth = 5;
      ctx.beginPath();
      ctx.moveTo(228, rowY + lineStep - 10);
      ctx.lineTo(canvas.width - 228, rowY + lineStep - 10);
      ctx.stroke();
      ctx.fillStyle = markerSoft;
      ctx.font = `700 ${numberFontSize}px ${BOARD_SANS_FONT}`;
      ctx.fillText(`${index + 1}.`, 332, rowY);
      if (item) {
        ctx.fillStyle = marker;
        ctx.font = `600 ${itemFontSize}px ${BOARD_HAND_FONT}`;
        const lines = boardWrapLines(ctx, item, canvas.width - 820, maxPolicyLines);
        lines.forEach((lineItem, lineIndex) => {
          ctx.fillText(lineItem, 700, rowY + lineIndex * lineStep);
        });
      } else {
        ctx.strokeStyle = "rgba(108, 88, 67, 0.18)";
        ctx.lineWidth = 4;
        ctx.beginPath();
        ctx.moveTo(740, rowY + 4);
        ctx.lineTo(canvas.width - 248, rowY + 2);
        ctx.stroke();
      }
    });
  } else {
    const statsX = 240;
    let cursorY = panel.headline ? 366 : 286;
    if (panel.headline) {
      const headerGradient = ctx.createLinearGradient(statsX, cursorY - 112, canvas.width - statsX, cursorY + 148);
      headerGradient.addColorStop(0, themeMode === "light" ? "rgba(255, 250, 243, 0.78)" : "rgba(66, 55, 44, 0.92)");
      headerGradient.addColorStop(1, themeMode === "light" ? "rgba(246, 236, 219, 0.5)" : "rgba(33, 27, 21, 0.9)");
      ctx.fillStyle = headerGradient;
      boardRoundRect(ctx, statsX - 20, cursorY - 126, canvas.width - statsX * 2 + 40, 248, 42);
      ctx.fill();
      ctx.strokeStyle = themeMode === "light" ? "rgba(72, 50, 32, 0.1)" : "rgba(244, 236, 223, 0.16)";
      ctx.lineWidth = 4;
      ctx.stroke();
      ctx.fillStyle = marker;
      ctx.font = `700 150px ${BOARD_SANS_FONT}`;
      const lines = boardWrapLines(ctx, panel.headline, 4480, 2);
      lines.forEach((lineItem, index) => {
        ctx.fillText(lineItem, statsX + 26, cursorY + index * 112);
      });
      cursorY += lines.length * 112 + 94;
    }

    const isMoodVariant = panel.variant === "mood";
    const isStatsVariant = panel.variant === "stats";
      const statRows = (panel.stats ?? []).slice(0, 4);
    const signalRows = (panel.columns ?? []).slice(0, 3);
    const statBandTop = cursorY + 34;
    const statBandWidth = canvas.width - statsX * 2;

    if (isMoodVariant) {
      const primaryStatRows = statRows.slice(0, 4);
      const visibleSignals = signalRows.slice(0, panel.footerText ? 2 : 3);
      ctx.fillStyle = accent;
      ctx.fillRect(statsX, statBandTop - 12, 286, 12);

      const metricBaseY = statBandTop + 86;
      const metricColumns = primaryStatRows.length > 1 ? 2 : 1;
      const metricGapX = 86;
      const metricGapY = 44;
      const metricCardWidth = (statBandWidth - metricGapX * (metricColumns - 1)) / metricColumns;
      const metricRowHeight = 236;
      primaryStatRows.forEach((row, index) => {
        const columnIndex = index % metricColumns;
        const rowIndex = Math.floor(index / metricColumns);
        const x = statsX + columnIndex * (metricCardWidth + metricGapX);
        const y = metricBaseY + rowIndex * (metricRowHeight + metricGapY);
        ctx.strokeStyle = line;
        ctx.lineWidth = 4;
        ctx.beginPath();
        ctx.moveTo(x, y + 190);
        ctx.lineTo(x + metricCardWidth, y + 190);
        ctx.stroke();
        ctx.fillStyle = markerSoft;
        ctx.font = `700 48px ${BOARD_SANS_FONT}`;
        ctx.fillText(row.label.toUpperCase(), x, y);
        ctx.fillStyle = marker;
        ctx.font = `700 112px ${BOARD_HAND_FONT}`;
        const valueLines = boardWrapLines(ctx, row.value, metricCardWidth - 28, 2);
        valueLines.forEach((lineText, lineIndex) => {
          ctx.fillText(lineText, x, y + 92 + lineIndex * 72);
        });
        if (row.note) {
          ctx.fillStyle = markerSoft;
          ctx.font = `600 34px ${BOARD_SANS_FONT}`;
          ctx.fillText(boardSnippet(row.note, 38), x, y + 224);
        }
      });

      const metricRows = Math.max(1, Math.ceil(primaryStatRows.length / metricColumns));
      const signalTop = metricBaseY + metricRows * (metricRowHeight + metricGapY) + 34;
      if (visibleSignals.length > 0) {
        ctx.fillStyle = markerSoft;
        ctx.font = `700 58px ${BOARD_SANS_FONT}`;
        ctx.fillText("PUBLIC SIGNALS", statsX, signalTop);
        ctx.fillStyle = accent;
        ctx.fillRect(statsX, signalTop + 18, 250, 8);
      }

      visibleSignals.forEach((column, index) => {
        const lineItem = column.lines[0];
        if (!lineItem) {
          return;
        }
        const label = typeof lineItem === "string" ? column.title : lineItem.label;
        const value = typeof lineItem === "string" ? lineItem : lineItem.answer;
        const detail = typeof lineItem === "string" ? "" : lineItem.share ?? "";
        const y = signalTop + 80 + index * 202;
        const detailColumnWidth = detail ? 560 : 0;
        const textColumnWidth = statBandWidth - detailColumnWidth - (detail ? 90 : 0);
        ctx.strokeStyle = line;
        ctx.lineWidth = 4;
        ctx.beginPath();
        ctx.moveTo(statsX, y + 164);
        ctx.lineTo(canvas.width - statsX, y + 164);
        ctx.stroke();

        ctx.fillStyle = markerSoft;
        ctx.font = `700 40px ${BOARD_SANS_FONT}`;
        ctx.fillText(column.title.toUpperCase(), statsX, y);
        if (detail) {
          ctx.textAlign = "right";
          ctx.fillStyle = markerSoft;
          ctx.font = `700 30px ${BOARD_SANS_FONT}`;
          ctx.fillText("TOP SHARE", canvas.width - statsX, y + 2);
          ctx.fillStyle = marker;
          ctx.font = `700 112px ${BOARD_HAND_FONT}`;
          ctx.fillText(detail, canvas.width - statsX, y + 96);
          ctx.textAlign = "left";
        }
        if (label && label.toLowerCase() !== column.title.toLowerCase()) {
          ctx.fillStyle = markerSoft;
          ctx.font = `600 32px ${BOARD_SANS_FONT}`;
          const labelLines = boardWrapLines(ctx, boardSnippet(label, 86), textColumnWidth, 1);
          labelLines.forEach((lineText, lineIndex) => {
            ctx.fillText(lineText, statsX, y + 42 + lineIndex * 30);
          });
        }
        ctx.fillStyle = marker;
        ctx.font = `700 70px ${BOARD_HAND_FONT}`;
        const answerWidth = Math.max(1120, textColumnWidth);
        const answerLines = boardWrapLines(ctx, value, answerWidth, 2);
        answerLines.forEach((lineText, lineIndex) => {
          ctx.fillText(lineText, statsX, y + 108 + lineIndex * 52);
        });
      });

      if (panel.footerText) {
        const footerTop = signalTop + 96 + visibleSignals.length * 202 + 8;
        ctx.fillStyle = markerSoft;
        ctx.font = `700 48px ${BOARD_SANS_FONT}`;
        ctx.fillText((panel.footerLabel ?? "Macro read").toUpperCase(), statsX, footerTop);
        ctx.fillStyle = accent;
        ctx.fillRect(statsX, footerTop + 17, 152, 7);
        ctx.strokeStyle = line;
        ctx.lineWidth = 4;
        boardRoundRect(ctx, statsX - 8, footerTop + 42, statBandWidth + 16, 162, 24);
        ctx.stroke();
        ctx.fillStyle = marker;
        ctx.font = `600 48px ${BOARD_SANS_FONT}`;
        const footerLines = boardWrapLines(ctx, panel.footerText, statBandWidth - 82, 2);
        footerLines.forEach((lineText, lineIndex) => {
          ctx.fillText(lineText, statsX + 24, footerTop + 100 + lineIndex * 48);
        });
      }
    } else if (isStatsVariant) {
      const primaryStatRows = statRows.slice(0, 4);
      const visibleSignals = signalRows.slice(0, 2);
      ctx.fillStyle = markerSoft;
      ctx.font = `700 82px ${BOARD_SANS_FONT}`;
      ctx.fillText("PUBLIC READ", statsX, statBandTop - 6);
      ctx.fillStyle = accent;
      ctx.fillRect(statsX, statBandTop + 14, 236, 8);

      let readoutY = statBandTop + 86;
      primaryStatRows.forEach((row, index) => {
        const lineY = readoutY + index * 182;
        ctx.strokeStyle = line;
        ctx.lineWidth = 4;
        ctx.beginPath();
        ctx.moveTo(statsX, lineY + 92);
        ctx.lineTo(canvas.width - statsX, lineY + 92);
        ctx.stroke();

        ctx.fillStyle = markerSoft;
        ctx.font = `700 60px ${BOARD_SANS_FONT}`;
        ctx.fillText(row.label.toUpperCase(), statsX, lineY + 12);
        ctx.fillStyle = marker;
        ctx.textAlign = "right";
        ctx.font = `700 154px ${BOARD_HAND_FONT}`;
        ctx.fillText(row.value, canvas.width - statsX, lineY + 78);
        ctx.textAlign = "left";
        if (row.note) {
          ctx.fillStyle = accent;
          ctx.font = `700 42px ${BOARD_SANS_FONT}`;
          ctx.fillText(boardSnippet(row.note, 38).toUpperCase(), statsX, lineY + 154);
        }
      });

      const signalTop = readoutY + primaryStatRows.length * 182 + 24;
      if (visibleSignals.length > 0) {
        ctx.fillStyle = markerSoft;
        ctx.font = `700 84px ${BOARD_SANS_FONT}`;
        ctx.fillText("LATEST POLLS", statsX, signalTop);
        ctx.fillStyle = accent;
        ctx.fillRect(statsX, signalTop + 18, 192, 8);
      }

      visibleSignals.forEach((column, index) => {
        const lineItem = column.lines[0];
        if (!lineItem) {
          return;
        }
        const label = typeof lineItem === "string" ? lineItem : lineItem.label;
        const value = typeof lineItem === "string" ? "" : lineItem.answer;
        const detail = typeof lineItem === "string" ? "" : lineItem.share ?? "";
        const y = signalTop + 82 + index * 210;
        ctx.strokeStyle = line;
        ctx.lineWidth = 4;
        ctx.beginPath();
        ctx.moveTo(statsX, y + 156);
        ctx.lineTo(canvas.width - statsX, y + 156);
        ctx.stroke();

        ctx.fillStyle = markerSoft;
        ctx.font = `700 56px ${BOARD_SANS_FONT}`;
        ctx.fillText(column.title.toUpperCase(), statsX, y);
        ctx.fillStyle = accent;
        ctx.fillRect(statsX, y + 16, 124, 8);
        ctx.fillStyle = markerSoft;
        ctx.font = `700 48px ${BOARD_SANS_FONT}`;
        const labelLines = boardWrapLines(ctx, boardSnippet(label, 64), statBandWidth - (detail ? 980 : 220), 2);
        labelLines.forEach((lineText, lineIndex) => {
          ctx.fillText(lineText, statsX, y + 60 + lineIndex * 36);
        });

        if (value) {
          ctx.fillStyle = marker;
        ctx.font = `700 96px ${BOARD_HAND_FONT}`;
          const valueLines = boardWrapLines(ctx, value, statBandWidth - (detail ? 860 : 180), 2);
          valueLines.forEach((lineText, lineIndex) => {
            ctx.fillText(lineText, statsX, y + 118 + lineIndex * 60);
          });
        }

        if (detail) {
          ctx.textAlign = "right";
          ctx.fillStyle = markerSoft;
          ctx.font = `700 38px ${BOARD_SANS_FONT}`;
          ctx.fillText("TOP SHARE", canvas.width - statsX, y + 2);
          ctx.fillStyle = marker;
          ctx.font = `700 104px ${BOARD_HAND_FONT}`;
          ctx.fillText(detail, canvas.width - statsX, y + 96);
          ctx.textAlign = "left";
        }
      });

      if (panel.footerText) {
        const footerTop = signalTop + 72 + visibleSignals.length * 210 + 24;
        ctx.fillStyle = markerSoft;
        ctx.font = `700 42px ${BOARD_SANS_FONT}`;
        ctx.fillText((panel.footerLabel ?? "Current read").toUpperCase(), statsX, footerTop);
        ctx.fillStyle = accent;
        ctx.fillRect(statsX, footerTop + 18, 184, 8);
        ctx.strokeStyle = line;
        ctx.lineWidth = 4;
        boardRoundRect(ctx, statsX - 8, footerTop + 42, statBandWidth + 16, 196, 28);
        ctx.stroke();
        ctx.fillStyle = marker;
        ctx.font = `600 44px ${BOARD_SANS_FONT}`;
        const footerLines = boardWrapLines(ctx, panel.footerText, statBandWidth - 72, 3);
        footerLines.forEach((lineText, lineIndex) => {
          ctx.fillText(lineText, statsX + 24, footerTop + 108 + lineIndex * 48);
        });
      }
    } else {
      const statColumns = statRows.length > 1 ? 2 : 1;
      const statRowsNeeded = Math.max(1, Math.ceil(statRows.length / statColumns));
      const statGapX = 56;
      const statGapY = 90;
      const statCellWidth = (statBandWidth - statGapX * (statColumns - 1)) / statColumns;
      const statBlockHeight = 252;
      const statLineY = statBandTop + statRowsNeeded * statBlockHeight + (statRowsNeeded - 1) * statGapY - 28;

      statRows.forEach((row, index) => {
        const columnIndex = index % statColumns;
        const rowIndex = Math.floor(index / statColumns);
        const x = statsX + columnIndex * (statCellWidth + statGapX);
        const statY = statBandTop + rowIndex * (statBlockHeight + statGapY);
        const labelY = statY + 18;
        const accentY = statY + 44;
        const valueY = statY + 148;
        const noteY = statY + 192;
        ctx.fillStyle = markerSoft;
        ctx.font = `700 44px ${BOARD_SANS_FONT}`;
        ctx.fillText(row.label.toUpperCase(), x, labelY);
        ctx.fillStyle = accent;
        ctx.fillRect(x, accentY, 148, 8);
        ctx.fillStyle = marker;
        ctx.font = `700 140px ${BOARD_HAND_FONT}`;
        const valueLines = boardWrapLines(ctx, row.value, statCellWidth - 12, 1);
        valueLines.forEach((lineItem, lineIndex) => {
          ctx.fillText(lineItem, x, valueY + lineIndex * 78);
        });
        if (row.note) {
          ctx.fillStyle = markerSoft;
          ctx.font = `600 32px ${BOARD_SANS_FONT}`;
          ctx.fillText(row.note, x, noteY);
        }
        ctx.strokeStyle = line;
        ctx.lineWidth = 4;
        ctx.beginPath();
        ctx.moveTo(x, statLineY);
        ctx.lineTo(x + statCellWidth - 12, statLineY);
        ctx.stroke();
      });

      const signalTop = statLineY + 108;
      if (signalRows.length > 0) {
        ctx.fillStyle = markerSoft;
        ctx.font = `700 44px ${BOARD_SANS_FONT}`;
        ctx.fillText("POLL PULSE", statsX, signalTop - 20);
      }

      signalRows.forEach((column, index) => {
        const lineItem = column.lines[0];
        if (!lineItem) {
          return;
        }
        const label = typeof lineItem === "string" ? lineItem : lineItem.label;
        const value = typeof lineItem === "string" ? "" : lineItem.answer;
        const detail = typeof lineItem === "string" ? undefined : lineItem.share;
        const signalWidth = (statBandWidth - 28) / signalRows.length;
        const x = statsX + index * (signalWidth + 28);
        const y = signalTop + index * 320;
        const rowHeight = 246;

        ctx.strokeStyle = line;
        ctx.lineWidth = 5;
        ctx.beginPath();
        ctx.moveTo(x, y + rowHeight - 12);
        ctx.lineTo(x + signalWidth, y + rowHeight - 12);
        ctx.stroke();

        ctx.fillStyle = markerSoft;
        ctx.font = `700 44px ${BOARD_SANS_FONT}`;
        ctx.fillText(column.title.toUpperCase(), x, y + 28);
        ctx.fillStyle = accent;
        ctx.fillRect(x, y + 50, 132, 8);

        ctx.fillStyle = markerSoft;
        ctx.font = `700 36px ${BOARD_SANS_FONT}`;
        ctx.fillText(boardSnippet(label, 28), x, y + 114);

        if (value) {
          ctx.fillStyle = marker;
          ctx.font = `700 92px ${BOARD_HAND_FONT}`;
          const valueLines = boardWrapLines(ctx, boardSnippet(value, 32), signalWidth - 420, 1);
          valueLines.forEach((lineText) => {
            ctx.fillText(lineText, x, y + 184);
          });
        }

        if (detail) {
          ctx.textAlign = "right";
          ctx.fillStyle = marker;
          ctx.font = `700 112px ${BOARD_HAND_FONT}`;
          ctx.fillText(detail, x + signalWidth, y + 146);
          ctx.fillStyle = markerSoft;
          ctx.font = `700 28px ${BOARD_SANS_FONT}`;
          ctx.fillText("TOP SHARE", x + signalWidth, y + 40);
          ctx.textAlign = "left";
        }
      });
    }
  }

  const texture = new THREE.CanvasTexture(canvas);
  texture.colorSpace = THREE.SRGBColorSpace;
  texture.magFilter = THREE.LinearFilter;
  texture.minFilter = THREE.LinearFilter;
  texture.anisotropy = 4;
  texture.generateMipmaps = false;
  texture.needsUpdate = true;
  return rememberTexture(BOARD_TEXTURE_CACHE, signature, texture, BOARD_TEXTURE_CACHE_LIMIT);
}

function boardPanelSignature(panel: BoardPanel, themeMode: "light" | "dark") {
  return JSON.stringify({
    themeMode,
    kicker: panel.kicker,
    variant: panel.variant ?? "",
    headline: panel.headline ?? "",
    footerLabel: panel.footerLabel ?? "",
    footerText: panel.footerText ?? "",
    stats: (panel.stats ?? []).map((row) => [row.label, row.value, row.note ?? "", row.detail ?? ""]),
    chips: panel.chips ?? [],
    listNumbered: Boolean(panel.listNumbered),
    columns: (panel.columns ?? []).map((column) => ({
      id: column.id ?? "",
      title: column.title,
      lines: column.lines.map((line) => (typeof line === "string" ? line : [line.label, line.answer, line.share ?? ""])),
    })),
    list: panel.list ?? [],
  });
}

function venueScreenSignature(stage: StagePackage, themeMode: "light" | "dark") {
  return JSON.stringify({
    themeMode,
    title: stage.title,
    year: stage.year_label,
    phase: stage.phase_label,
    metrics: boardMetricsKey(stage),
    polls: boardPollsKey(stage),
    roomBrief: stageRoomBrief(stage),
    macroRead: boardMacroRead(stage),
    policyNotes: stage.policy_notes.slice(0, 5),
  });
}

function venueScreenTexture(stage: StagePackage, themeMode: "light" | "dark", signature: string) {
  const cachedTexture = VENUE_TEXTURE_CACHE.get(signature);
  if (cachedTexture) {
    return cachedTexture;
  }
  const canvas = document.createElement("canvas");
  canvas.width = 2560;
  canvas.height = 1728;
  const ctx = canvas.getContext("2d");
  if (!ctx) {
    return new THREE.CanvasTexture(canvas);
  }

  const base = themeMode === "light" ? "#d8cdc0" : "#111318";
  const inner = themeMode === "light" ? "#f5eee2" : "#171b22";
  const glow = themeMode === "light" ? "rgba(211, 154, 95, 0.16)" : "rgba(126, 168, 214, 0.12)";
  const frame = themeMode === "light" ? "#7e6249" : "#3b414c";
  const text = themeMode === "light" ? "#1f130c" : "#e7edf4";
  const soft = themeMode === "light" ? "#4f3b2a" : "#a8b6c5";
  const accent = themeMode === "light" ? "#6f421f" : "#d4a66d";

  const votePoll = topPollChoice(stage, "election were held today");
  const pressure = topPollChoice(stage, "biggest worry about ai");
  const service = topPollChoiceForNeedles(stage, ["everyday services now feel", "everyday services more reliable"]);
  const visibleGain = topPollChoiceForNeedles(stage, ["hate to lose right now", "easier, cheaper, or better because of ai lately"]);
  const latestCustom = [...stage.poll_summaries].reverse().find((summary) => {
    const normalized = summary.question.toLowerCase();
    return ![
      "trust ai to handle",
      "still would not trust ai",
      "easier, cheaper, or better",
      "hate to lose right now",
      "which issue most needs attention first",
      "biggest worry about ai",
      "everyday services now feel",
      "election were held today",
    ].some((fragment) => normalized.includes(fragment));
  });
  const latestCustomChoice = latestCustom
    ? (() => {
        const [answer, share] =
          Object.entries(latestCustom.shares).sort((left, right) => right[1] - left[1])[0] ?? ["n/a", 0];
        return {
          answer: boardSnippet(answer, 52),
          share: `${Math.round(share * 100)}%`,
        };
      })()
    : null;
  const platformNotes = stage.policy_notes
    .slice(0, 5)
    .map((note) => boardPolicyLabel(note));

  ctx.fillStyle = base;
  ctx.fillRect(0, 0, canvas.width, canvas.height);

  const glowFill = ctx.createLinearGradient(0, 0, canvas.width, canvas.height);
  glowFill.addColorStop(0, glow);
  glowFill.addColorStop(1, "rgba(0,0,0,0)");
  ctx.fillStyle = glowFill;
  ctx.fillRect(0, 0, canvas.width, canvas.height);

  ctx.fillStyle = inner;
  ctx.fillRect(46, 46, canvas.width - 92, canvas.height - 92);
  ctx.strokeStyle = frame;
  ctx.lineWidth = 18;
  ctx.strokeRect(46, 46, canvas.width - 92, canvas.height - 92);

  ctx.strokeStyle = themeMode === "light" ? "rgba(109, 92, 75, 0.18)" : "rgba(205, 219, 236, 0.1)";
  ctx.lineWidth = 5;
  ctx.beginPath();
  ctx.moveTo(canvas.width / 2, 160);
  ctx.lineTo(canvas.width / 2, canvas.height - 160);
  ctx.stroke();

  ctx.fillStyle = soft;
  ctx.font = `700 58px ${BOARD_SANS_FONT}`;
  ctx.fillText("PUBLIC READ", 170, 164);
  ctx.fillText("PLATFORM", 1720, 164);

  ctx.fillStyle = text;
  ctx.font = `700 108px ${BOARD_SANS_FONT}`;
  ctx.fillText(votePoll ? boardPollLabel(votePoll.answer, { candidate: true, max: 18 }) : "Open race", 170, 280);
  if (votePoll?.share) {
    ctx.fillStyle = accent;
    ctx.font = `700 66px ${BOARD_SANS_FONT}`;
    ctx.fillText(votePoll.share, 170, 350);
  }

  const statRows = boardMetricRows(stage).slice(0, 4);

  let statY = 438;
  statRows.forEach((row) => {
    ctx.fillStyle = soft;
    ctx.font = `700 54px ${BOARD_SANS_FONT}`;
    ctx.fillText(row.label.toUpperCase(), 170, statY);
    ctx.fillStyle = text;
    ctx.font = `600 104px ${BOARD_HAND_FONT}`;
    const lines = boardWrapLines(ctx, row.value, 1120, 2);
    lines.forEach((lineItem, index) => {
      ctx.fillText(lineItem, 170, statY + 86 + index * 64);
    });
    if (row.note) {
      ctx.fillStyle = accent;
      ctx.font = `600 50px ${BOARD_SANS_FONT}`;
      ctx.fillText(row.note, 1200, statY + 22);
    }
    statY += 206;
  });

  ctx.strokeStyle = themeMode === "light" ? "rgba(116, 98, 80, 0.16)" : "rgba(210, 223, 238, 0.1)";
  ctx.lineWidth = 4;
  ctx.beginPath();
  ctx.moveTo(170, 1124);
  ctx.lineTo(1480, 1124);
  ctx.stroke();

  const moodCallouts = [
    {
      label: "Main pressure",
      value: pressure?.answer ? boardPollLabel(pressure.answer, { max: 22 }) : "mixed",
      note: pressure?.share,
    },
    {
      label: "Clear upside",
      value: visibleGain?.answer ? boardPollLabel(visibleGain.answer, { max: 22 }) : "not clear yet",
      note: visibleGain?.share,
    },
    {
      label: "Service read",
      value: service?.answer ? boardPollLabel(service.answer, { max: 22 }) : "mixed",
      note: service?.share,
    },
  ];

  moodCallouts.forEach((item, index) => {
    const x = 170 + index * 432;
    ctx.fillStyle = soft;
    ctx.font = `700 46px ${BOARD_SANS_FONT}`;
    ctx.fillText(item.label.toUpperCase(), x, 1196);
    ctx.fillStyle = text;
    ctx.font = `600 72px ${BOARD_HAND_FONT}`;
    const lines = boardWrapLines(ctx, item.value, 360, 2);
    lines.forEach((lineItem, lineIndex) => {
      ctx.fillText(lineItem, x, 1266 + lineIndex * 52);
    });
    if (item.note) {
      ctx.fillStyle = accent;
      ctx.font = `700 44px ${BOARD_SANS_FONT}`;
      ctx.fillText(item.note, x, 1380);
    }
  });

  ctx.fillStyle = text;
  ctx.font = `600 84px ${BOARD_HAND_FONT}`;
  (platformNotes.length > 0 ? platformNotes : ["No final platform locked", "", "", ""]).forEach((note, index) => {
    const y = 280 + index * 180;
    ctx.fillText(`${index + 1}.`, 1720, y);
    if (note) {
      const lines = boardWrapLines(ctx, note, 1220, 3);
      lines.forEach((lineItem, lineIndex) => {
        ctx.fillText(lineItem, 1875, y + lineIndex * 56);
      });
    } else {
      ctx.strokeStyle = themeMode === "light" ? "rgba(116, 98, 80, 0.22)" : "rgba(210, 223, 238, 0.12)";
      ctx.lineWidth = 4;
      ctx.beginPath();
      ctx.moveTo(1875, y - 18);
      ctx.lineTo(canvas.width - 160, y - 18);
      ctx.stroke();
    }
  });

  ctx.strokeStyle = themeMode === "light" ? "rgba(116, 98, 80, 0.22)" : "rgba(210, 223, 238, 0.12)";
  ctx.lineWidth = 5;
  ctx.beginPath();
  ctx.moveTo(1720, 1040);
  ctx.lineTo(canvas.width - 160, 1040);
  ctx.stroke();
  ctx.fillStyle = soft;
  ctx.font = `700 46px ${BOARD_SANS_FONT}`;
  ctx.fillText("LIVE NOTE", 1720, 1118);
  ctx.fillStyle = text;
  ctx.font = `500 62px ${BOARD_HAND_FONT}`;
  const closingLines = boardWrapLines(
    ctx,
    boardSnippet(stageRoomBrief(stage) || "Make the case in plain language, then call the election when the room feels settled.", 122),
    1240,
    2,
  );
  closingLines.forEach((lineItem, index) => {
    ctx.fillText(lineItem, 1720, 1192 + index * 60);
  });

  ctx.strokeStyle = themeMode === "light" ? "rgba(116, 98, 80, 0.22)" : "rgba(210, 223, 238, 0.12)";
  ctx.lineWidth = 5;
  ctx.beginPath();
  ctx.moveTo(1720, 1380);
  ctx.lineTo(canvas.width - 160, 1380);
  ctx.stroke();
  ctx.fillStyle = soft;
  ctx.font = `700 46px ${BOARD_SANS_FONT}`;
  ctx.fillText(latestCustom ? "LATEST POLL" : "LIVE READ", 1720, 1458);
  ctx.fillStyle = text;
  ctx.font = `500 54px ${BOARD_HAND_FONT}`;
  const lowerRead = latestCustom && latestCustomChoice
    ? `${boardSnippet(latestCustom.question.replace(/\?+$/, ""), 64)} ${latestCustomChoice.share} · ${latestCustomChoice.answer}`
    : boardSnippet(stageRoomBrief(stage) || "Keep the platform short enough to defend from the podium.", 110);
  boardWrapLines(ctx, lowerRead, 1240, 2).forEach((lineItem, index) => {
    ctx.fillText(lineItem, 1720, 1534 + index * 56);
  });

  const texture = new THREE.CanvasTexture(canvas);
  texture.colorSpace = THREE.SRGBColorSpace;
  texture.magFilter = THREE.LinearFilter;
  texture.minFilter = THREE.LinearFilter;
  texture.anisotropy = 16;
  texture.generateMipmaps = false;
  texture.needsUpdate = true;
  return rememberTexture(VENUE_TEXTURE_CACHE, signature, texture, VENUE_TEXTURE_CACHE_LIMIT);
}

function VenueScreen({
  position,
  width,
  height,
  stage,
  themeMode,
}: {
  position: [number, number, number];
  width: number;
  height: number;
  stage: StagePackage;
  themeMode: "light" | "dark";
}) {
  const textureSignature = useMemo(() => venueScreenSignature(stage, themeMode), [stage, themeMode]);
  const texture = useMemo(() => venueScreenTexture(stage, themeMode, textureSignature), [textureSignature]);

  return (
    <group position={position}>
      <RoundedBox args={[width + 0.56, height + 0.38, 0.18]} radius={0.08} castShadow receiveShadow>
        <meshStandardMaterial color={themeMode === "light" ? "#8a6d57" : "#2d2f38"} roughness={0.92} />
      </RoundedBox>
      <RoundedBox args={[width + 0.26, height + 0.12, 0.1]} radius={0.06} position={[0, 0, 0.04]} castShadow receiveShadow>
        <meshStandardMaterial color={themeMode === "light" ? "#a6896f" : "#161b24"} roughness={0.84} metalness={0.08} />
      </RoundedBox>
      <mesh position={[0, 0.01, 0.12]} receiveShadow castShadow>
        <boxGeometry args={[width - 0.14, height - 0.14, 0.05]} />
        <meshBasicMaterial
          map={texture}
          toneMapped={false}
        />
      </mesh>
    </group>
  );
}

function MountedBoard({
  position,
  width = 6.42,
  height = 4.34,
  yaw = 0,
  pitch = -0.03,
  panel,
  themeMode,
}: {
  position: [number, number, number];
  width?: number;
  height?: number;
  yaw?: number;
  pitch?: number;
  panel: BoardPanel;
  themeMode: "light" | "dark";
}) {
  const { gl } = useThree();
  const panelSignature = boardPanelSignature(panel, themeMode);
  const texture = useMemo(() => boardTexture(panel, themeMode, panelSignature), [panelSignature, themeMode]);
  useEffect(() => {
    texture.anisotropy = Math.min(16, gl.capabilities.getMaxAnisotropy());
    texture.needsUpdate = true;
  }, [gl, texture]);

  return (
    <group position={position} rotation={[pitch, yaw, 0]}>
      <mesh position={[0, 0, -0.18]}>
        <planeGeometry args={[width + 0.42, height + 0.34]} />
        <meshBasicMaterial color={themeMode === "light" ? "#d6b289" : "#d7c1a5"} transparent opacity={themeMode === "light" ? 0.05 : 0.02} />
      </mesh>
      <RoundedBox args={[width + 0.44, height + 0.32, 0.18]} radius={0.06} position={[0, 0, -0.08]} castShadow receiveShadow>
        <meshStandardMaterial color={themeMode === "light" ? "#523b2c" : "#2b3441"} roughness={0.97} />
      </RoundedBox>
      <RoundedBox args={[width + 0.08, height + 0.08, 0.08]} radius={0.04} position={[0, 0, -0.02]} castShadow receiveShadow>
        <meshStandardMaterial color={themeMode === "light" ? "#241912" : "#121822"} roughness={0.99} />
      </RoundedBox>
      <RoundedBox args={[width + 0.04, height + 0.04, 0.06]} radius={0.03} position={[0, 0, -0.005]} castShadow receiveShadow>
        <meshStandardMaterial color={themeMode === "light" ? "#f4eadb" : "#222b36"} roughness={0.99} />
      </RoundedBox>
      <mesh position={[0, -height * 0.53, 0.04]} castShadow receiveShadow>
        <boxGeometry args={[width * 0.94, 0.08, 0.08]} />
        <meshStandardMaterial color={themeMode === "light" ? "#735845" : "#3a2b22"} roughness={0.94} />
      </mesh>
      <mesh position={[0, 0.008, 0.16]} renderOrder={8}>
        <planeGeometry args={[width - 0.06, height - 0.06]} />
        <meshBasicMaterial
          map={texture}
          color={themeMode === "light" ? "#f3e7d6" : "#ffffff"}
          toneMapped={false}
          depthWrite={false}
          polygonOffset
          polygonOffsetFactor={-2}
          polygonOffsetUnits={-2}
        />
      </mesh>
    </group>
  );
}

function WallBoardBay({
  position,
  width,
  height,
  themeMode,
}: {
  position: [number, number, number];
  width: number;
  height: number;
  themeMode: "light" | "dark";
}) {
  return (
    <group position={position}>
      <RoundedBox args={[width + 0.04, height + 0.04, 0.1]} radius={0.04} castShadow receiveShadow>
        <meshStandardMaterial color={themeMode === "light" ? "#5a4131" : "#32404f"} roughness={0.96} />
      </RoundedBox>
      <RoundedBox args={[width - 0.08, height - 0.08, 0.05]} radius={0.03} position={[0, 0, -0.02]} castShadow receiveShadow>
        <meshStandardMaterial color={themeMode === "light" ? "#34251c" : "#18202a"} roughness={0.99} />
      </RoundedBox>
      <RoundedBox args={[width - 0.16, height - 0.16, 0.03]} radius={0.03} position={[0, 0, -0.06]} castShadow receiveShadow>
        <meshStandardMaterial color={themeMode === "light" ? "#201712" : "#101720"} roughness={0.99} />
      </RoundedBox>
      <mesh position={[0, 0, -0.13]} receiveShadow>
        <planeGeometry args={[width - 0.2, height - 0.2]} />
        <meshStandardMaterial color={themeMode === "light" ? "#2d221a" : "#0b1118"} roughness={1} />
      </mesh>
    </group>
  );
}

function AdvisorBoards({
  stage,
  playerInPower,
  notes,
  themeMode,
  layout = "solo",
}: {
  stage: StagePackage;
  playerInPower: boolean;
  notes: string[];
  themeMode: "light" | "dark";
  layout?: AdvisorMode;
}) {
  const notesKey = notes.join("|");
  const fallbackAgendaSource = useMemo(
    () => (
      notes.length > 0
        ? notes
        : stage.policy_notes
    ),
    [notes, stage],
  );
  const agendaNotes = useMemo(
    () => fallbackAgendaSource.slice(0, 4).map((item) => boardPolicyLabel(item)),
    [fallbackAgendaSource, notesKey],
  );
  const statsKey = boardMetricsKey(stage);
  const statsRows = useMemo(() => boardMetricRows(stage), [statsKey]);
  const agendaKey = agendaNotes.join("|");
  const pollsKey = boardPollsKey(stage);
  const moodColumns = useMemo(() => boardPublicMoodColumns(stage) ?? [], [stage, pollsKey]);
  const moodKey = moodColumns
    .map((column) => `${column.id ?? column.title}:${column.title}:${column.lines.map((line) => typeof line === "string" ? line : `${line.label}|${line.answer}|${line.share ?? ""}`).join("~")}`)
    .join("||");
  const statsPanel = useMemo<BoardPanel>(() => ({
    variant: "mood",
    kicker: playerInPower ? "Public mood" : "Campaign mood",
    stats: statsRows.slice(0, 4),
    columns: moodColumns.slice(0, 2),
  }), [
    moodKey,
    playerInPower,
    stage,
    statsKey,
  ]);
  const policyPanel = useMemo<BoardPanel>(() => ({
    variant: "policy",
    kicker: playerInPower ? "Policy ideas" : "Campaign ideas",
    list: (agendaNotes.length > 0 ? agendaNotes : ["", "", "", ""]).slice(0, 4),
  }), [agendaKey, playerInPower]);
  const statsBoardLayout =
    layout === "council"
      ? {
          bayWidth: 6.78,
          bayHeight: 4.82,
          x: 3.42,
          y: 3.18,
          z: -3.82,
          boardWidth: 6.42,
          boardHeight: 4.5,
          boardZ: -3.56,
          pitch: -0.012,
        }
      : {
          bayWidth: 7.34,
          bayHeight: 4.96,
          x: 3.86,
          y: 3.22,
          z: -3.9,
          boardWidth: 6.96,
          boardHeight: 4.56,
          boardZ: -3.62,
          pitch: -0.012,
        };
  const policyBoardLayout =
    layout === "council"
      ? {
          bayWidth: 6.54,
          bayHeight: 4.72,
          x: 3.42,
          y: 3.18,
          z: -3.82,
          boardWidth: 6.18,
          boardHeight: 4.38,
          boardZ: -3.58,
          pitch: -0.012,
        }
      : {
          bayWidth: 6.72,
          bayHeight: 4.82,
          x: 3.86,
          y: 3.22,
          z: -3.9,
          boardWidth: 6.28,
          boardHeight: 4.42,
          boardZ: -3.66,
          pitch: -0.012,
        };
  return (
    <>
      <WallBoardBay position={[-statsBoardLayout.x, statsBoardLayout.y, statsBoardLayout.z]} width={statsBoardLayout.bayWidth} height={statsBoardLayout.bayHeight} themeMode={themeMode} />
      <WallBoardBay position={[policyBoardLayout.x, policyBoardLayout.y, policyBoardLayout.z]} width={policyBoardLayout.bayWidth} height={policyBoardLayout.bayHeight} themeMode={themeMode} />
      <MountedBoard
        position={[-statsBoardLayout.x + 0.02, statsBoardLayout.y + 0.06, statsBoardLayout.boardZ]}
        width={statsBoardLayout.boardWidth}
        height={statsBoardLayout.boardHeight}
        yaw={0.001}
        pitch={statsBoardLayout.pitch}
        panel={statsPanel}
        themeMode={themeMode}
      />
      <MountedBoard
        position={[policyBoardLayout.x - 0.04, policyBoardLayout.y + 0.04, policyBoardLayout.boardZ]}
        width={policyBoardLayout.boardWidth}
        height={policyBoardLayout.boardHeight}
        yaw={-0.001}
        pitch={policyBoardLayout.pitch}
        panel={policyPanel}
        themeMode={themeMode}
      />
    </>
  );
}

function DebateBoards({ stage, notes, themeMode }: { stage: StagePackage; notes: string[]; themeMode: "light" | "dark" }) {
  const pollsKey = boardPollsKey(stage);
  const moodColumns = useMemo(() => boardPublicMoodColumns(stage) ?? [], [stage, pollsKey]);
  const statsKey = boardMetricsKey(stage);
  const statsRows = useMemo(() => boardMetricRows(stage), [statsKey]);
  const moodKey = moodColumns
    .map((column) => `${column.id ?? column.title}:${column.title}:${column.lines.map((line) => typeof line === "string" ? line : `${line.label}|${line.answer}|${line.share ?? ""}`).join("~")}`)
    .join("||");
  const publicReadPanel = useMemo<BoardPanel>(() => ({
    variant: "mood",
    kicker: "Public mood",
    stats: statsRows.slice(0, 4),
    columns: moodColumns,
    footerLabel: "World read",
    footerText: boardSnippet(boardMacroRead(stage), 156),
  }), [moodKey, stage, statsKey]);
  const platformNotes = useMemo(
    () =>
      (
        notes.length > 0
          ? notes
          : stage.policy_notes
      )
        .slice(0, 4)
        .map((note) => boardPolicyLabel(note)),
    [notes, stage],
  );
  return (
    <>
      <WallBoardBay position={[-4.02, 4.16, -5.08]} width={8.12} height={5.94} themeMode={themeMode} />
      <WallBoardBay position={[4.02, 4.16, -5.08]} width={8.12} height={5.94} themeMode={themeMode} />
      <MountedBoard position={[-4.02, 4.2, -4.92]} width={7.72} height={5.4} yaw={0.002} pitch={-0.004} panel={publicReadPanel} themeMode={themeMode} />
      <MountedBoard
        position={[4.02, 4.2, -4.92]}
        width={7.72}
        height={5.4}
        yaw={-0.002}
        pitch={-0.004}
        panel={{
          variant: "policy",
          kicker: "Platform today",
          list: (platformNotes.length > 0 ? platformNotes : ["", "", "", ""]).slice(0, 4),
        }}
        themeMode={themeMode}
      />
    </>
  );
}

function SceneMarker({
  position,
  label,
  hint,
  tone = "amber",
  active = false,
  variant = "door",
  disabled = false,
  onSelect,
  onHoverChange,
}: {
  position: [number, number, number];
  label: string;
  hint?: string;
  tone?: SceneHotspot["tone"];
  active?: boolean;
  variant?: "door" | "person" | "note";
  disabled?: boolean;
  onSelect?: () => void;
  onHoverChange?: (hovered: boolean) => void;
}) {
  const distanceFactor = variant === "door" ? 14.4 : variant === "note" ? 10.8 : 11.2;
  const [hovered, setHovered] = useState(false);
  const compactLabel = variant === "door" ? compactSceneMarkerLabel(label) : label;
  const accessibleLabel = hint ? `${label}. ${hint}` : label;
  return (
    <Html position={position} center distanceFactor={distanceFactor}>
      <button
        aria-label={accessibleLabel}
        title={label}
        className={`scene-hotspot scene-hotspot--${tone} scene-hotspot--${variant} ${active ? "scene-hotspot--active" : ""} ${disabled ? "scene-hotspot--disabled" : ""} ${hovered ? "scene-hotspot--hovered" : ""}`}
        disabled={disabled}
        onClick={(event) => {
          event.stopPropagation();
          if (disabled) {
            return;
          }
          onSelect?.();
        }}
        onMouseEnter={() => {
          setHovered(true);
          onHoverChange?.(true);
        }}
        onMouseLeave={() => {
          setHovered(false);
          onHoverChange?.(false);
        }}
        onFocus={() => setHovered(true)}
        onBlur={() => setHovered(false)}
      >
        <span>{compactLabel}</span>
        {hint ? <small>{hint}</small> : null}
      </button>
    </Html>
  );
}

function SceneCitizenLabel({
  citizen,
  position,
  active,
  ready,
  onSelect,
}: {
  citizen: CitizenSnapshot;
  position: [number, number, number];
  active: boolean;
  ready: boolean;
  onSelect?: () => void;
}) {
  const tone =
    citizen.approval_band === "approve"
      ? "sage"
      : citizen.approval_band === "disapprove"
        ? "rose"
        : "steel";
  return (
    <Html position={position} center distanceFactor={7.6} style={{ pointerEvents: "auto" }}>
      <button
        type="button"
        className={`scene-hotspot scene-hotspot--${tone} scene-hotspot--person scene-citizen-label ${active ? "scene-hotspot--active scene-citizen-label--active" : ""} ${ready ? "scene-citizen-label--ready" : ""}`}
        onClick={(event) => {
          event.stopPropagation();
          onSelect?.();
        }}
      >
        <span>{compactCitizenLabel(citizen.display_name)}</span>
        <small className="scene-citizen-label__role">{boardSnippet(citizen.role, 24)}</small>
        <small className={`scene-citizen-label__status scene-citizen-label__status--${citizen.approval_band}`}>
          {ready ? "ready to talk" : "move closer"}
        </small>
      </button>
    </Html>
  );
}

function SceneRig({
  target,
  focus,
  room,
  playerStateRef,
  streetFocus,
}: {
  target: [number, number, number];
  focus: [number, number, number];
  room: RoomName;
  playerStateRef: MutableRefObject<StreetPlayerState>;
  streetFocus?: [number, number, number];
}) {
  const smoothedFocusRef = useRef<[number, number] | null>(null);
  useFrame(({ camera, mouse }, delta) => {
    const player = playerStateRef.current;
    const follow = 1 - Math.exp(-Math.max(1.9, room === "citizens" ? 1.18 : 3.4) * delta);
    const mouseDriftX = room === "debate" ? 0.045 : room === "advisor" ? 0.025 : 0.018;
    const mouseDriftY = room === "debate" ? 0.028 : room === "advisor" ? 0.018 : 0.012;
    const zoomDrift = room === "debate" ? 0.018 : room === "advisor" ? 0.012 : 0.022;
    if (room === "citizens") {
      const focusDistance = streetFocus ? Math.hypot(streetFocus[0] - player.x, streetFocus[2] - player.z) : Infinity;
      const cameraHeading = player.heading;
      const forwardX = Math.sin(cameraHeading);
      const forwardZ = -Math.cos(cameraHeading);
      const encounterBlend = streetFocus ? THREE.MathUtils.clamp(1 - Math.min(1, focusDistance / 8.8), 0, 1) : 0;
      const shoulderDistance = THREE.MathUtils.lerp(0.42, 0.28, encounterBlend);
      const behindDistance = THREE.MathUtils.lerp(6.25, 4.72, encounterBlend);
      const cameraXMargin = 1.8;
      const lookXMargin = 1.05;
      const shoulderX = Math.cos(cameraHeading) * shoulderDistance;
      const shoulderZ = Math.sin(cameraHeading) * shoulderDistance;
      const behindX = -forwardX * behindDistance;
      const behindZ = -forwardZ * behindDistance;
      const desiredX = player.x + behindX + shoulderX * 0.3;
      const targetX = THREE.MathUtils.clamp(desiredX, STREET_BOUNDS.minX + cameraXMargin, STREET_BOUNDS.maxX - cameraXMargin);
      const targetY = THREE.MathUtils.lerp(3.48, 2.72, encounterBlend);
      const targetZ = THREE.MathUtils.clamp(player.z + behindZ + shoulderZ * 0.32 + 0.04, STREET_BOUNDS.minZ + 4.4, STREET_BOUNDS.maxZ - 1.05);
      const baseLookX = THREE.MathUtils.clamp(player.x + forwardX * 4.2, STREET_BOUNDS.minX + lookXMargin, STREET_BOUNDS.maxX - lookXMargin);
      const baseLookZ = player.z + forwardZ * 6.8;
      const focusBlend =
        streetFocus
          ? THREE.MathUtils.clamp(
              1 - Math.min(1, focusDistance / 8.8),
              0,
              0.08,
            )
          : 0;
      const desiredLookX = streetFocus ? THREE.MathUtils.lerp(baseLookX, streetFocus[0], focusBlend) : baseLookX;
      const desiredLookZ = streetFocus ? THREE.MathUtils.lerp(baseLookZ, streetFocus[2], focusBlend) : baseLookZ;
      if (!smoothedFocusRef.current) {
        smoothedFocusRef.current = [desiredLookX, desiredLookZ];
      }
      smoothedFocusRef.current[0] = THREE.MathUtils.lerp(smoothedFocusRef.current[0], desiredLookX, follow);
      smoothedFocusRef.current[1] = THREE.MathUtils.lerp(smoothedFocusRef.current[1], desiredLookZ, follow);
      camera.position.x = THREE.MathUtils.lerp(camera.position.x, targetX, follow);
      camera.position.y = THREE.MathUtils.lerp(camera.position.y, targetY, follow);
      camera.position.z = THREE.MathUtils.lerp(camera.position.z, targetZ, follow);
      camera.lookAt(smoothedFocusRef.current[0], 1.24, smoothedFocusRef.current[1]);
      return;
    }
    const driftedTarget = target;
    const driftedFocus = focus;
    camera.position.x = THREE.MathUtils.lerp(camera.position.x, driftedTarget[0] + mouse.x * mouseDriftX, follow);
    camera.position.y = THREE.MathUtils.lerp(camera.position.y, driftedTarget[1] + mouse.y * mouseDriftY, follow);
    camera.position.z = THREE.MathUtils.lerp(camera.position.z, driftedTarget[2] - Math.abs(mouse.x) * zoomDrift, follow);
    camera.lookAt(driftedFocus[0], driftedFocus[1], driftedFocus[2]);
  });
  return null;
}

function RoomShell({
  room,
  advisorMode,
  accent,
  fill,
  themeMode,
}: {
  room: RoomName;
  advisorMode: AdvisorMode;
  accent: string;
  fill: string;
  themeMode: "light" | "dark";
}) {
  if (room === "advisor") {
    return advisorMode === "council"
      ? <AdvisorCouncilShell accent={accent} fill={fill} themeMode={themeMode} />
      : <AdvisorShell accent={accent} fill={fill} themeMode={themeMode} />;
  }
  if (room === "citizens") {
    return <CitizenShell accent={accent} fill={fill} themeMode={themeMode} />;
  }
  if (room === "debate") {
    return <DebateShell accent={accent} fill={fill} themeMode={themeMode} />;
  }
  return <BriefingShell accent={accent} fill={fill} themeMode={themeMode} />;
}

function AdvisorShell({ accent, fill, themeMode }: { accent: string; fill: string; themeMode: "light" | "dark" }) {
  return (
    <group>
      <mesh rotation={[-Math.PI / 2, 0, 0]} receiveShadow>
        <planeGeometry args={[27.8, 27.8]} />
        <meshStandardMaterial color={themeMode === "light" ? "#dcd1c2" : "#5a4232"} roughness={0.95} metalness={0.04} />
      </mesh>
      <mesh position={[0, 6.86, -0.72]} rotation={[Math.PI / 2, 0, 0]} receiveShadow>
        <planeGeometry args={[25.8, 24.8]} />
        <meshStandardMaterial color={themeMode === "light" ? "#fbf5eb" : "#221713"} roughness={0.98} />
      </mesh>
      <RoundedBox args={[25.4, 7.62, 0.34]} radius={0.08} position={[0, 3.42, -5.54]} castShadow receiveShadow>
        <meshStandardMaterial color={themeMode === "light" ? "#f4e9da" : "#403024"} roughness={0.95} />
      </RoundedBox>
      <RoundedBox args={[24.18, 6.08, 0.16]} radius={0.06} position={[0, 3.16, -5.28]} castShadow receiveShadow>
        <meshStandardMaterial color={themeMode === "light" ? "#e2d4c2" : "#4d392d"} roughness={0.98} />
      </RoundedBox>
      <RoundedBox args={[0.36, 7.12, 14.2]} radius={0.12} position={[-12.96, 3.32, 0.14]} receiveShadow castShadow>
        <meshStandardMaterial color={themeMode === "light" ? "#ebdece" : "#352923"} roughness={0.98} />
      </RoundedBox>
      <RoundedBox args={[0.36, 7.12, 14.2]} radius={0.12} position={[12.96, 3.32, 0.14]} receiveShadow castShadow>
        <meshStandardMaterial color={themeMode === "light" ? "#ebdece" : "#352923"} roughness={0.98} />
      </RoundedBox>
      {[-10.2, 10.2].map((x) => (
        <RoundedBox key={`advisor-fore-column-${x}`} args={[1.02, 6.02, 1.06]} radius={0.08} position={[x, 3.0, 6.42]} receiveShadow castShadow>
          <meshStandardMaterial color={themeMode === "light" ? "#ddcfbe" : "#31241f"} roughness={0.98} />
        </RoundedBox>
      ))}
      <mesh position={[0, 0.02, 0.12]} rotation={[-Math.PI / 2, 0, 0]} receiveShadow>
        <circleGeometry args={[4.6, 88]} />
        <meshStandardMaterial color={themeMode === "light" ? "#87acc4" : "#28445d"} roughness={0.82} />
      </mesh>
      <mesh position={[0, 0.03, 0.12]} rotation={[-Math.PI / 2, 0, 0]} receiveShadow>
        <ringGeometry args={[3.52, 4.4, 88]} />
        <meshStandardMaterial color={themeMode === "light" ? "#dfccb0" : "#c7ad88"} roughness={0.52} metalness={0.18} />
      </mesh>
      <RoundedBox args={[9.3, 5.42, 0.18]} radius={0.08} position={[0, 3.2, -5.16]} castShadow receiveShadow>
        <meshStandardMaterial color={themeMode === "light" ? "#e8dbc8" : "#453328"} roughness={0.98} />
      </RoundedBox>
      <RoundedBox args={[5.18, 3.48, 0.08]} radius={0.06} position={[0, 3.0, -5.02]} receiveShadow>
        <meshStandardMaterial color={themeMode === "light" ? "#c7b29c" : "#2a201b"} emissive={themeMode === "light" ? "#bcd0db" : "#1f3342"} emissiveIntensity={themeMode === "light" ? 0.08 : 0.16} roughness={0.74} />
      </RoundedBox>
      <RoundedBox args={[4.84, 0.2, 0.5]} radius={0.05} position={[0, 1.12, -4.94]} castShadow receiveShadow>
        <meshStandardMaterial color={themeMode === "light" ? "#b38b69" : "#5a4234"} roughness={0.82} />
      </RoundedBox>
      {[-7.9, 7.9].map((x, index) => (
        <group key={`advisor-window-${x}`} position={[x, 3.34, -5.1]}>
          <RoundedBox args={[3.52, 5.14, 0.14]} radius={0.08} castShadow receiveShadow>
            <meshStandardMaterial color={themeMode === "light" ? "#e4d4c1" : "#46362c"} roughness={0.98} />
          </RoundedBox>
          <RoundedBox args={[2.82, 4.34, 0.06]} radius={0.05} position={[0, 0.02, 0.08]} receiveShadow>
            <meshStandardMaterial color={themeMode === "light" ? "#adc0ca" : "#223743"} emissive={index === 0 ? accent : fill} emissiveIntensity={0.08} roughness={0.3} metalness={0.08} />
          </RoundedBox>
          <RoundedBox args={[0.12, 4.02, 0.08]} radius={0.04} position={[0, 0.02, 0.12]} receiveShadow>
            <meshStandardMaterial color={themeMode === "light" ? "#d5c2aa" : "#5b4437"} roughness={0.86} />
          </RoundedBox>
          <RoundedBox args={[2.6, 0.12, 0.08]} radius={0.04} position={[0, 0.04, 0.12]} receiveShadow>
            <meshStandardMaterial color={themeMode === "light" ? "#d5c2aa" : "#5b4437"} roughness={0.86} />
          </RoundedBox>
        </group>
      ))}
      <RoundedBox args={[24.1, 0.24, 0.22]} radius={0.04} position={[0, 6.76, -4.92]} castShadow receiveShadow>
        <meshStandardMaterial color={themeMode === "light" ? "#b89271" : "#5c4537"} roughness={0.9} />
      </RoundedBox>
      <RoundedBox args={[22.8, 0.42, 0.24]} radius={0.05} position={[0, 0.24, -4.82]} castShadow receiveShadow>
        <meshStandardMaterial color={themeMode === "light" ? "#d1c0ae" : "#6d5039"} roughness={0.72} />
      </RoundedBox>
      {[-6.6, 0, 6.6].map((x, index) => (
        <mesh key={`advisor-ring-light-${x}`} position={[x, 6.2, -0.64]} castShadow>
          <sphereGeometry args={[0.12, 20, 20]} />
          <meshStandardMaterial color={index === 1 ? accent : fill} emissive={index === 1 ? accent : fill} emissiveIntensity={0.42} />
        </mesh>
      ))}
    </group>
  );
}

function AdvisorCouncilShell({ accent, fill, themeMode }: { accent: string; fill: string; themeMode: "light" | "dark" }) {
  return (
    <group>
      <mesh rotation={[-Math.PI / 2, 0, 0]} receiveShadow>
        <planeGeometry args={[27.4, 27.4]} />
        <meshStandardMaterial color={themeMode === "light" ? "#d7cab8" : "#5b4332"} roughness={0.95} metalness={0.04} />
      </mesh>
      <mesh position={[0, 3.94, -5.56]} receiveShadow>
        <boxGeometry args={[24.8, 5.8, 0.34]} />
        <meshStandardMaterial color={themeMode === "light" ? "#efe3d4" : "#342823"} roughness={0.98} />
      </mesh>
      <mesh position={[0, 6.84, -0.4]} rotation={[Math.PI / 2, 0, 0]} receiveShadow>
        <planeGeometry args={[24.8, 24.8]} />
        <meshStandardMaterial color={themeMode === "light" ? "#f7efe2" : "#221713"} roughness={0.98} />
      </mesh>
      <RoundedBox args={[24.2, 7.52, 0.34]} radius={0.08} position={[0, 3.42, -5.42]} castShadow receiveShadow>
        <meshStandardMaterial color={themeMode === "light" ? "#f0e3d3" : "#403025"} roughness={0.95} />
      </RoundedBox>
      <RoundedBox args={[22.48, 6.02, 0.16]} radius={0.06} position={[0, 3.22, -5.24]} castShadow receiveShadow>
        <meshStandardMaterial color={themeMode === "light" ? "#e2d2be" : "#4a382d"} roughness={0.98} />
      </RoundedBox>
      <RoundedBox args={[0.28, 7.14, 12.96]} radius={0.12} position={[-12.98, 3.34, -0.28]} receiveShadow castShadow>
        <meshStandardMaterial color={themeMode === "light" ? "#e8dac9" : "#342823"} roughness={0.98} />
      </RoundedBox>
      <RoundedBox args={[0.28, 7.14, 12.96]} radius={0.12} position={[12.98, 3.34, -0.28]} receiveShadow castShadow>
        <meshStandardMaterial color={themeMode === "light" ? "#e8dac9" : "#342823"} roughness={0.98} />
      </RoundedBox>
      <mesh position={[0, 0.02, 0.4]} rotation={[-Math.PI / 2, 0, 0]} scale={[1.4, 1, 0.96]} receiveShadow>
        <circleGeometry args={[4.8, 84]} />
        <meshStandardMaterial color={themeMode === "light" ? "#88aec7" : "#27445e"} roughness={0.82} />
      </mesh>
      <mesh position={[0, 0.04, 0.4]} rotation={[-Math.PI / 2, 0, 0]} scale={[1.26, 1, 0.88]} receiveShadow>
        <ringGeometry args={[3.6, 4.6, 84]} />
        <meshStandardMaterial color={themeMode === "light" ? "#e7d5bc" : "#d0b38c"} roughness={0.54} metalness={0.18} />
      </mesh>
      <RoundedBox args={[10.12, 0.14, 0.2]} radius={0.05} position={[0, 6.44, -4.92]} castShadow receiveShadow>
        <meshStandardMaterial color={themeMode === "light" ? "#ad8461" : "#5c4537"} roughness={0.9} />
      </RoundedBox>
      {[-8.4, -4.2, 0, 4.2, 8.4].map((x, index) => (
        <mesh key={`advisor-council-orb-${x}`} position={[x, 6.16, -0.34]} castShadow>
          <sphereGeometry args={[0.11, 20, 20]} />
          <meshStandardMaterial color={index % 2 === 0 ? accent : fill} emissive={index % 2 === 0 ? accent : fill} emissiveIntensity={0.38} />
        </mesh>
      ))}
    </group>
  );
}

function CitizenShell({ accent, fill, themeMode }: { accent: string; fill: string; themeMode: "light" | "dark" }) {
  const roadTexture = useMemo(() => streetSurfaceTexture(themeMode), [themeMode]);
  useEffect(() => () => roadTexture.dispose(), [roadTexture]);
  const rearHorizon = [
    { position: [-25.4, 6.2, -47.2] as [number, number, number], size: [3.2, 8.8, 1.4] as [number, number, number], tone: "#6c5543" },
    { position: [-21.3, 5.6, -48.2] as [number, number, number], size: [2.8, 7.4, 1.3] as [number, number, number], tone: "#4f6a7e" },
    { position: [-15.4, 6.4, -47.6] as [number, number, number], size: [3.4, 9.2, 1.4] as [number, number, number], tone: "#7b604b" },
    { position: [-9.1, 5.3, -48.4] as [number, number, number], size: [2.9, 7.2, 1.2] as [number, number, number], tone: "#5a7284" },
    { position: [-2.2, 6.8, -47.9] as [number, number, number], size: [3.6, 10.2, 1.6] as [number, number, number], tone: "#7b644f" },
    { position: [4.9, 5.7, -48.1] as [number, number, number], size: [3.0, 7.8, 1.3] as [number, number, number], tone: "#5e7988" },
    { position: [11.7, 6.5, -47.4] as [number, number, number], size: [3.5, 9.4, 1.5] as [number, number, number], tone: "#856a53" },
    { position: [18.2, 5.5, -48.3] as [number, number, number], size: [2.9, 7.1, 1.2] as [number, number, number], tone: "#597180" },
    { position: [24.7, 6.1, -47.7] as [number, number, number], size: [3.2, 8.6, 1.4] as [number, number, number], tone: "#725a47" },
  ];
  const frontHorizon = [
    { position: [-23.2, 5.1, 33.6] as [number, number, number], size: [3.0, 7.4, 1.1] as [number, number, number], tone: "#6f553f" },
    { position: [-17.2, 4.6, 34.1] as [number, number, number], size: [2.7, 6.2, 1.0] as [number, number, number], tone: "#577082" },
    { position: [-10.3, 5.3, 33.8] as [number, number, number], size: [3.2, 7.8, 1.1] as [number, number, number], tone: "#7a5f4b" },
    { position: [-3.4, 4.8, 34.0] as [number, number, number], size: [2.8, 6.4, 1.0] as [number, number, number], tone: "#5c7586" },
    { position: [3.8, 5.4, 33.5] as [number, number, number], size: [3.3, 8.0, 1.1] as [number, number, number], tone: "#83664f" },
    { position: [10.6, 4.7, 34.2] as [number, number, number], size: [2.9, 6.6, 1.0] as [number, number, number], tone: "#5b7283" },
    { position: [17.4, 5.2, 33.7] as [number, number, number], size: [3.1, 7.6, 1.1] as [number, number, number], tone: "#6d5541" },
    { position: [24.1, 4.8, 34.3] as [number, number, number], size: [2.7, 6.3, 1.0] as [number, number, number], tone: "#557083" },
  ];
  return (
    <group>
      <mesh position={[0, 0.02, -25.2]} rotation={[-Math.PI / 2, 0, 0]} receiveShadow>
        <planeGeometry args={[17.2, 103]} />
        <meshStandardMaterial map={roadTexture} color="#ffffff" roughness={0.98} />
      </mesh>
      {[-12.2, 12.2].map((x) => (
        <mesh key={`street-sidewalk-${x}`} position={[x, 0.018, -25.2]} rotation={[-Math.PI / 2, 0, 0]} receiveShadow>
          <planeGeometry args={[6.2, 103]} />
          <meshStandardMaterial color={themeMode === "light" ? "#a99176" : "#2e2925"} roughness={0.98} />
        </mesh>
      ))}
      {[-8.65, 8.65].map((x) => (
        <mesh key={`street-curb-${x}`} position={[x, 0.075, -25.2]} receiveShadow>
          <boxGeometry args={[0.28, 0.12, 103]} />
          <meshStandardMaterial color={themeMode === "light" ? "#d0bda4" : "#4b4037"} roughness={0.94} />
        </mesh>
      ))}
      <mesh position={[0, 8.4, -32]}>
        <planeGeometry args={[48, 24]} />
        <meshStandardMaterial
          color={themeMode === "light" ? "#cad4dc" : "#506575"}
          emissive={themeMode === "light" ? "#b5c3cb" : "#2d3d49"}
          emissiveIntensity={themeMode === "light" ? 0.08 : 0.14}
        />
      </mesh>
      <mesh position={[0, 11.2, -31.8]}>
        <planeGeometry args={[48, 18]} />
        <meshStandardMaterial
          color={themeMode === "light" ? "#dde5e9" : "#42545f"}
          emissive={themeMode === "light" ? "#d8e0e5" : "#27333b"}
          emissiveIntensity={themeMode === "light" ? 0.1 : 0.16}
        />
      </mesh>
      <mesh position={[0, 4.8, -42.2]}>
        <boxGeometry args={[18.8, 4.8, 1.2]} />
        <meshStandardMaterial color={themeMode === "light" ? "#cbb8a4" : "#2f2722"} roughness={0.96} />
      </mesh>
      {rearHorizon.map((building, index) => (
        <group key={`street-rear-ridge-${index}`} position={building.position}>
          <mesh position={[0, building.size[1] / 2 - 0.05, 0]}>
            <boxGeometry args={building.size} />
            <meshStandardMaterial color={building.tone} roughness={0.95} />
          </mesh>
          <mesh position={[0, building.size[1] + 0.18, 0.02]}>
            <boxGeometry args={[building.size[0] * 0.74, 0.12, building.size[2] * 0.92]} />
            <meshStandardMaterial color={themeMode === "light" ? "#d8c9b7" : "#2d2520"} roughness={0.9} />
          </mesh>
        </group>
      ))}
      {[-18.4, -15.2, -11.8, -7.8, -3.8, 0, 3.8, 7.8, 11.8, 15.2, 18.4].map((x, index) => (
        <mesh key={`street-far-skyline-${x}`} position={[x, 5.8 + (index % 3) * 0.34, -39.8]}>
          <boxGeometry args={[2.8, 6.2 + (index % 4) * 0.72, 1.2]} />
          <meshStandardMaterial color={index % 2 === 0 ? "#8b755f" : "#688092"} roughness={0.96} />
        </mesh>
      ))}
      {[-5.8, -1.9, 1.9, 5.8].map((x, index) => (
        <mesh key={`street-far-midrise-${x}`} position={[x, 3.84 + (index % 2) * 0.44, -34.6]}>
          <boxGeometry args={[3.2, 7.1 + (index % 2) * 0.9, 1.6]} />
          <meshStandardMaterial color={index % 2 === 0 ? "#7c6652" : "#60798c"} roughness={0.95} />
        </mesh>
      ))}
      {frontHorizon.map((building, index) => (
        <group key={`street-front-ridge-${index}`} position={building.position}>
          <mesh position={[0, building.size[1] / 2 - 0.05, 0]}>
            <boxGeometry args={building.size} />
            <meshStandardMaterial color={building.tone} roughness={0.95} />
          </mesh>
          <mesh position={[0, building.size[1] + 0.18, 0.02]}>
            <boxGeometry args={[building.size[0] * 0.72, 0.12, building.size[2] * 0.92]} />
            <meshStandardMaterial color={themeMode === "light" ? "#f0e7db" : "#2a211d"} roughness={0.9} />
          </mesh>
        </group>
      ))}
      <mesh position={[0, 6.4, 31.6]}>
        <boxGeometry args={[22.8, 9.8, 1.6]} />
        <meshStandardMaterial color={themeMode === "light" ? "#a5917a" : "#312924"} roughness={0.96} />
      </mesh>
      <mesh position={[0, 1.12, 30.3]}>
        <boxGeometry args={[22.6, 2.3, 0.46]} />
        <meshStandardMaterial color={themeMode === "light" ? "#8f7864" : "#352c27"} roughness={0.94} />
      </mesh>
      <mesh position={[0, 0.78, -40]}>
        <boxGeometry args={[46.6, 1.8, 0.5]} />
        <meshStandardMaterial color={themeMode === "light" ? "#cdb9a7" : "#2f2823"} roughness={0.95} />
      </mesh>
      {[-13.2, -9.2, -5.2, 5.2, 9.2, 13.2].map((x, index) => (
        <mesh key={`street-skyline-${x}`} position={[x, 3.2 + (index % 3) * 0.45, -34.5]}>
          <boxGeometry args={[2.8, 5.8 + (index % 3) * 0.8, 1.4]} />
          <meshStandardMaterial color={index % 2 === 0 ? "#8f765f" : "#6d8292"} roughness={0.96} />
        </mesh>
      ))}
      {[-9.4, -4.4, 0.6, 5.8, 10.2].map((x, index) => (
        <mesh key={`street-endcap-${x}`} position={[x, 4.7 + (index % 2) * 0.65, 29.1]}>
          <boxGeometry args={[3.8, 9.1 + (index % 2) * 1.4, 1.3]} />
          <meshStandardMaterial color={index % 2 === 0 ? "#8f765f" : "#687b89"} roughness={0.96} />
        </mesh>
      ))}
      <mesh position={[0, 1.38, 28.35]}>
        <boxGeometry args={[18.8, 2.8, 0.34]} />
        <meshStandardMaterial color={themeMode === "light" ? "#8b765f" : "#372d28"} roughness={0.94} />
      </mesh>
      <mesh position={[0, 0.24, -28]} rotation={[-Math.PI / 2, 0, 0]} receiveShadow>
        <planeGeometry args={[14.8, 18]} />
        <meshStandardMaterial color={themeMode === "light" ? "#87735f" : "#3c3530"} roughness={0.98} />
      </mesh>
      {[-2.4, 2.4].map((x) => (
        <mesh key={`crosswalk-${x}`} position={[x, 0.136, 22.6]} rotation={[-Math.PI / 2, 0, 0]} receiveShadow>
          <planeGeometry args={[1.6, 5.8]} />
          <meshStandardMaterial
            color={themeMode === "light" ? "#ded0bc" : "#7f6e5a"}
            transparent
            opacity={themeMode === "light" ? 0.92 : 0.34}
            roughness={0.92}
          />
        </mesh>
      ))}
      {[-4.4, 4.4].map((x, index) => (
        <Float key={`street-orb-${x}`} speed={1 + index * 0.12} rotationIntensity={0.05} floatIntensity={0.05}>
          <mesh position={[x, 4.6, -4.6]}>
            <sphereGeometry args={[0.12, 22, 22]} />
            <meshStandardMaterial color={index === 0 ? accent : fill} emissive={index === 0 ? accent : fill} emissiveIntensity={0.4} />
          </mesh>
        </Float>
      ))}
      {[-10.6, 10.6].map((x) => (
        <group key={`street-inner-shop-${x}`} position={[x, 1.28, -12.6]} scale={0.84}>
          <Storefront position={[0, 0, 0]} accent={x < 0 ? "#8a624f" : "#557086"} rotationY={x < 0 ? 0.08 : -0.08} />
        </group>
      ))}
      {[-7.8, 7.8].map((x) => (
        <group key={`street-mid-shop-${x}`} position={[x, 1.24, x < 0 ? -3.8 : 4.8]} scale={0.76}>
          <Storefront position={[0, 0, 0]} accent={x < 0 ? "#7a5d48" : "#587086"} rotationY={x < 0 ? 0.14 : -0.14} />
        </group>
      ))}
      {[-8.8, 8.8].map((x) => (
        <group key={`street-inner-townhouse-${x}`} position={[x, 1.26, 6.8]} scale={0.78}>
          <Townhouse position={[0, 0, 0]} tone={x < 0 ? "#7b604b" : "#557085"} />
        </group>
      ))}
      {[-7.2, 7.2].map((x) => (
        <group key={`street-mid-townhouse-${x}`} position={[x, 1.14, x < 0 ? 18.2 : -18.2]} scale={0.72}>
          <Townhouse position={[0, 0, 0]} tone={x < 0 ? "#7c5e49" : "#587186"} />
        </group>
      ))}
      {[-11.8, 11.8].map((x, index) => (
        <StreetLamp key={`street-inner-lamp-${x}`} position={[x, 0.2, index === 0 ? -16.2 : 14.8]} glow={index === 0 ? accent : fill} />
      ))}
      {[-10.6, 10.6].map((x) => (
        <Plant key={`street-inner-plant-${x}`} position={[x, 0.18, -1.8]} tone={x < 0 ? "#67815d" : "#5f7962"} />
      ))}
      {[-8.8, 8.8].map((x) => (
        <Bench key={`street-mid-bench-${x}`} position={[x, 0.28, x < 0 ? 11.8 : -11.8]} tone={x < 0 ? "#5a4331" : "#4e3a2e"} />
      ))}
    </group>
  );
}

function DebateShell({ accent, fill, themeMode }: { accent: string; fill: string; themeMode: "light" | "dark" }) {
  return (
    <group>
      <mesh rotation={[-Math.PI / 2, 0, 0]} receiveShadow>
        <planeGeometry args={[25.8, 33.4]} />
        <meshStandardMaterial color={themeMode === "light" ? "#b5957c" : "#281d19"} roughness={0.98} />
      </mesh>
      <mesh position={[0, 7.15, -1.6]} rotation={[Math.PI / 2, 0, 0]} receiveShadow>
        <planeGeometry args={[25.8, 27.8]} />
        <meshStandardMaterial color={themeMode === "light" ? "#f1e6d8" : "#1d1412"} roughness={0.98} />
      </mesh>
      <mesh position={[0, 3.96, -8.6]} receiveShadow>
        <boxGeometry args={[24.8, 8.2, 0.62]} />
        <meshStandardMaterial color={themeMode === "light" ? "#e1d3c4" : "#342724"} roughness={0.98} />
      </mesh>
      <mesh position={[-12.42, 3.5, -1.1]} receiveShadow>
        <boxGeometry args={[0.64, 7.4, 17.8]} />
        <meshStandardMaterial color={themeMode === "light" ? "#d4c0a8" : "#211917"} roughness={0.98} />
      </mesh>
      <mesh position={[12.42, 3.5, -1.1]} receiveShadow>
        <boxGeometry args={[0.64, 7.4, 17.8]} />
        <meshStandardMaterial color={themeMode === "light" ? "#d4c0a8" : "#211917"} roughness={0.98} />
      </mesh>
      <RoundedBox args={[13.1, 0.26, 0.34]} radius={0.08} position={[0, 6.88, -8.28]} castShadow receiveShadow>
        <meshStandardMaterial color={themeMode === "light" ? "#86644c" : "#261c19"} roughness={0.88} />
      </RoundedBox>
      {[-7.35, 7.35].map((x) => (
        <RoundedBox key={`debate-proscenium-${x}`} args={[1.2, 6.4, 0.72]} radius={0.08} position={[x, 3.1, -7.7]} castShadow receiveShadow>
          <meshStandardMaterial color={themeMode === "light" ? "#b68f72" : "#31231e"} roughness={0.9} />
        </RoundedBox>
      ))}
      <RoundedBox args={[15.2, 0.42, 0.72]} radius={0.08} position={[0, 6.1, -7.78]} castShadow receiveShadow>
        <meshStandardMaterial color={themeMode === "light" ? "#b58d71" : "#2b201c"} roughness={0.92} />
      </RoundedBox>
      {[-9.3, 9.3].map((x) => (
        <RoundedBox key={`debate-balcony-${x}`} args={[2.25, 1.18, 4.9]} radius={0.08} position={[x, 4.45, -2.7]} castShadow receiveShadow>
          <meshStandardMaterial color={themeMode === "light" ? "#bca490" : "#2b201c"} roughness={0.95} />
        </RoundedBox>
      ))}
      {[-10.55, -9.7, 9.7, 10.55].map((x) => (
        <Drape key={`debate-drape-${x}`} position={[x, 2.92, -7.04]} color={x < 0 ? "#7d342b" : "#31465f"} scale={[0.92, 1.92, 1]} />
      ))}
      <mesh position={[0, 0.56, 0.94]} receiveShadow>
        <boxGeometry args={[12.8, 0.86, 7.9]} />
        <meshStandardMaterial color={themeMode === "light" ? "#53392c" : "#241915"} roughness={0.9} />
      </mesh>
      <RoundedBox args={[12.1, 0.18, 0.38]} radius={0.08} position={[0, 0.94, -0.66]} castShadow receiveShadow>
        <meshStandardMaterial color={themeMode === "light" ? "#a68165" : "#382822"} roughness={0.82} />
      </RoundedBox>
      {[-2.6, 2.6].map((x, index) => (
        <LightCone key={`auditorium-cone-${x}`} position={[x, 5.85, 2.6]} color={index === 0 ? accent : fill} />
      ))}
      {[-5.4, -2.7, 0, 2.7, 5.4].map((x) => (
        <mesh key={`auditorium-lamp-${x}`} position={[x, 6.14, 1.24]} castShadow>
          <sphereGeometry args={[0.12, 20, 20]} />
          <meshStandardMaterial color={accent} emissive={accent} emissiveIntensity={0.44} />
        </mesh>
      ))}
      <RoundedBox args={[9.6, 0.2, 0.44]} radius={0.06} position={[0, 2.16, -6.12]} castShadow receiveShadow>
        <meshStandardMaterial color={themeMode === "light" ? "#b89d86" : "#302622"} roughness={0.9} />
      </RoundedBox>
    </group>
  );
}

function BriefingShell({ accent, fill, themeMode }: { accent: string; fill: string; themeMode: "light" | "dark" }) {
  return (
    <group>
      <mesh rotation={[-Math.PI / 2, 0, 0]} receiveShadow>
        <planeGeometry args={[23.5, 23.5]} />
        <meshStandardMaterial color={themeMode === "light" ? "#cfc0b1" : "#2d221d"} roughness={0.95} metalness={0.04} />
      </mesh>
      <mesh position={[0, 6.94, -0.4]} rotation={[Math.PI / 2, 0, 0]} receiveShadow>
        <planeGeometry args={[23.2, 21.2]} />
        <meshStandardMaterial color={themeMode === "light" ? "#f1e8da" : "#120d0a"} roughness={0.98} />
      </mesh>
      <RoundedBox args={[19.4, 8.4, 0.36]} radius={0.08} position={[0, 4.14, -5.12]} castShadow receiveShadow>
        <meshStandardMaterial color={themeMode === "light" ? "#e8dacb" : "#241a14"} roughness={0.98} />
      </RoundedBox>
      <RoundedBox args={[16.4, 5.96, 0.14]} radius={0.08} position={[0, 3.74, -4.94]} receiveShadow>
        <meshStandardMaterial color={themeMode === "light" ? "#2d2623" : "#15100d"} roughness={0.96} />
      </RoundedBox>
      <RoundedBox args={[12.3, 4.46, 0.06]} radius={0.06} position={[0, 3.62, -4.72]} castShadow>
        <meshStandardMaterial color={fill} emissive={fill} emissiveIntensity={0.2} roughness={0.24} metalness={0.16} />
      </RoundedBox>
      {[-8.2, 8.2].map((x) => (
        <mesh key={`briefing-side-wall-${x}`} position={[x, 3.3, -0.2]} receiveShadow>
          <boxGeometry args={[0.52, 6.8, 11.6]} />
          <meshStandardMaterial color={themeMode === "light" ? "#ddd1c2" : "#1d1511"} roughness={0.98} />
        </mesh>
      ))}
      {[-6.4, -2.1, 2.1, 6.4].map((x) => (
        <RoundedBox key={`briefing-rib-${x}`} args={[0.22, 5.5, 0.34]} radius={0.05} position={[x, 4.1, -4.7]} castShadow receiveShadow>
          <meshStandardMaterial color={x < 0 ? accent : fill} emissive={x < 0 ? accent : fill} emissiveIntensity={0.14} roughness={0.3} metalness={0.16} />
        </RoundedBox>
      ))}
      <RoundedBox args={[17.8, 0.26, 0.46]} radius={0.08} position={[0, 0.34, -4.76]} receiveShadow>
        <meshStandardMaterial color={themeMode === "light" ? "#8d6649" : "#5e402c"} roughness={0.7} />
      </RoundedBox>
      <RoundedBox args={[9.2, 0.26, 6.8]} radius={0.08} position={[0, 0.14, 1.12]} receiveShadow>
        <meshStandardMaterial color={themeMode === "light" ? "#4c382d" : "#211915"} roughness={0.92} />
      </RoundedBox>
      <RoundedBox args={[11.2, 0.16, 8.6]} radius={0.08} position={[0, 0.3, 0.36]} receiveShadow>
        <meshStandardMaterial color={themeMode === "light" ? "#7d5e47" : "#382821"} roughness={0.84} />
      </RoundedBox>
      <RoundedBox args={[14.8, 0.16, 10.2]} radius={0.08} position={[0, 0.08, -0.4]} receiveShadow>
        <meshStandardMaterial color={themeMode === "light" ? "#5a4438" : "#271d18"} roughness={0.94} />
      </RoundedBox>
      {[-4.4, 0, 4.4].map((x) => (
        <mesh key={`ceiling-lamp-${x}`} position={[x, 6.24, -1.1]} castShadow>
          <sphereGeometry args={[0.11, 20, 20]} />
          <meshStandardMaterial color={x === 0 ? accent : fill} emissive={x === 0 ? accent : fill} emissiveIntensity={0.5} />
        </mesh>
      ))}
      <RoundedBox args={[2.4, 0.14, 0.24]} radius={0.05} position={[-6.05, 5.66, -4.72]} receiveShadow>
        <meshStandardMaterial color={themeMode === "light" ? "#ceb89d" : "#4d392f"} roughness={0.88} />
      </RoundedBox>
      <RoundedBox args={[2.4, 0.14, 0.24]} radius={0.05} position={[6.05, 5.66, -4.72]} receiveShadow>
        <meshStandardMaterial color={themeMode === "light" ? "#ceb89d" : "#4d392f"} roughness={0.88} />
      </RoundedBox>
    </group>
  );
}

function RoomDecor({
  room,
  advisorMode,
  accent,
  fill,
  playerInPower,
  themeMode,
}: {
  room: RoomName;
  advisorMode: AdvisorMode;
  accent: string;
  fill: string;
  playerInPower: boolean;
  themeMode: "light" | "dark";
}) {
  switch (room) {
    case "advisor":
      return advisorMode === "council"
        ? <AdvisorCouncilDecor accent={accent} fill={fill} playerInPower={playerInPower} themeMode={themeMode} />
        : <AdvisorDecor accent={accent} fill={fill} playerInPower={playerInPower} themeMode={themeMode} />;
    case "citizens":
      return <CitizenDecor accent={accent} fill={fill} themeMode={themeMode} />;
    case "debate":
      return <DebateDecor accent={accent} fill={fill} themeMode={themeMode} />;
    default:
      return <BriefingDecor accent={accent} fill={fill} themeMode={themeMode} />;
  }
}

function AdvisorDecor({ accent, fill, playerInPower, themeMode }: { accent: string; fill: string; playerInPower: boolean; themeMode: "light" | "dark" }) {
  return (
    <group>
      <mesh position={[0, 0.05, 0.1]} rotation={[-Math.PI / 2, 0, 0]} receiveShadow>
        <circleGeometry args={[3.55, 64]} />
        <meshStandardMaterial color={themeMode === "light" ? "#7dabc8" : "#1f3a54"} roughness={0.84} />
      </mesh>
      <mesh position={[0, 0.06, 0.1]} rotation={[-Math.PI / 2, 0, 0]} receiveShadow>
        <ringGeometry args={[3.1, 3.45, 64]} />
        <meshStandardMaterial color={themeMode === "light" ? "#e6d4b8" : "#d7bf9a"} roughness={0.52} metalness={0.18} />
      </mesh>

      <RoundedBox args={[1.18, 0.34, 0.58]} radius={0.08} position={[0, 0.21, 0.52]} castShadow receiveShadow>
        <meshStandardMaterial color={themeMode === "light" ? "#a58265" : "#6c4b31"} roughness={0.62} metalness={0.08} />
      </RoundedBox>

      <RoundedBox args={[0.46, 0.07, 0.24]} radius={0.05} position={[0, 0.43, 0.54]} castShadow>
        <meshStandardMaterial color={fill} emissive={fill} emissiveIntensity={themeMode === "light" ? 0.26 : 0.2} metalness={0.3} roughness={0.3} />
      </RoundedBox>

      <RoundedBox args={[0.98, 0.74, 0.76]} radius={0.08} position={[-4.7, 0.52, 1.18]} castShadow receiveShadow>
        <meshStandardMaterial color="#4a3426" roughness={0.78} />
      </RoundedBox>
      <RoundedBox args={[0.98, 0.74, 0.76]} radius={0.08} position={[4.7, 0.52, 1.18]} castShadow receiveShadow>
        <meshStandardMaterial color="#4a3426" roughness={0.78} />
      </RoundedBox>
      <RoundedBox args={[0.64, 0.14, 1.05]} radius={0.05} position={[-4.7, 0.38, 1.18]} castShadow receiveShadow>
        <meshStandardMaterial color="#69462c" roughness={0.62} />
      </RoundedBox>
      <RoundedBox args={[0.64, 0.14, 1.05]} radius={0.05} position={[4.7, 0.38, 1.18]} castShadow receiveShadow>
        <meshStandardMaterial color="#69462c" roughness={0.62} />
      </RoundedBox>

      {[-11.8, -11.05, 11.05, 11.8].map((x) => (
        <Drape key={`advisor-drape-${x}`} position={[x, 2.48, -4.34]} color={x < 0 ? "#8e5e43" : "#4b5e73"} scale={[0.66, 1.02, 1]} />
      ))}

      <Sofa position={[-10.85, 0.44, -0.62]} tone="#3e2b22" />
      <Sofa position={[10.85, 0.44, -0.62]} tone="#42312a" />
      <RoundedBox args={[0.9, 0.12, 0.68]} radius={0.05} position={[-10.72, 0.26, 0.24]} castShadow receiveShadow>
        <meshStandardMaterial color="#6a4930" roughness={0.58} />
      </RoundedBox>
      <RoundedBox args={[0.9, 0.12, 0.68]} radius={0.05} position={[10.72, 0.26, 0.24]} castShadow receiveShadow>
        <meshStandardMaterial color="#6a4930" roughness={0.58} />
      </RoundedBox>

      <Flag position={[-15.7, 1.82, -4.34]} accent={accent} />
      <Flag position={[15.7, 1.82, -4.34]} accent={fill} />
      <Bookcase position={[-14.35, 1.34, -1.88]} accent={themeMode === "light" ? "#8b6a51" : "#4f392b"} />
      <Bookcase position={[14.35, 1.34, -1.88]} accent={themeMode === "light" ? "#7a5d49" : "#433126"} />
      <PortraitFrame position={[-11.9, 3.34, -4.6]} tint={themeMode === "light" ? "#c5af93" : "#7d634f"} />
      <PortraitFrame position={[11.9, 3.34, -4.6]} tint={themeMode === "light" ? "#c5af93" : "#7d634f"} />
      <Lamp position={[-10.25, 1.48, -2.26]} color={accent} />
      <Lamp position={[10.25, 1.48, -2.26]} color={fill} />
      <RoundedBox args={[5.72, 0.28, 0.66]} radius={0.08} position={[0, 0.46, -3.34]} castShadow receiveShadow>
        <meshStandardMaterial color={themeMode === "light" ? "#a78a71" : "#553f32"} roughness={0.84} />
      </RoundedBox>
      <RoundedBox args={[5.1, 0.08, 0.54]} radius={0.04} position={[0, 0.62, -3.16]} castShadow receiveShadow>
        <meshStandardMaterial color={themeMode === "light" ? "#d7c6b3" : "#6a5446"} roughness={0.72} />
      </RoundedBox>
      {[-10.25, 10.25].map((x) => (
        <group key={`advisor-side-cred-${x}`} position={[x, 0, -4.08]}>
          <RoundedBox args={[1.16, 0.44, 0.58]} radius={0.06} position={[0, 0.38, 0]} castShadow receiveShadow>
            <meshStandardMaterial color={themeMode === "light" ? "#9c7d63" : "#523c30"} roughness={0.82} />
          </RoundedBox>
          <RoundedBox args={[0.92, 0.07, 0.42]} radius={0.04} position={[0, 0.67, 0.02]} castShadow receiveShadow>
            <meshStandardMaterial color={themeMode === "light" ? "#d8cab7" : "#6d5749"} roughness={0.72} />
          </RoundedBox>
          <RoundedBox args={[0.18, 0.05, 0.15]} radius={0.03} position={[-0.2, 0.74, 0.08]} castShadow>
            <meshStandardMaterial color={x < 0 ? accent : fill} emissive={x < 0 ? accent : fill} emissiveIntensity={themeMode === "light" ? 0.18 : 0.12} />
          </RoundedBox>
          <RoundedBox args={[0.26, 0.06, 0.16]} radius={0.03} position={[0.16, 0.75, -0.06]} castShadow receiveShadow>
            <meshStandardMaterial color={themeMode === "light" ? "#f0e5d8" : "#7a6556"} roughness={0.7} />
          </RoundedBox>
        </group>
      ))}
      <RoundedBox args={[0.92, 0.07, 0.22]} radius={0.03} position={[0, 0.7, 0.22]} castShadow receiveShadow>
        <meshStandardMaterial color={themeMode === "light" ? "#d8ccb8" : "#6b5648"} roughness={0.7} />
      </RoundedBox>
      <Plant position={[-10.85, 0.18, -1.12]} tone="#5d7651" />
      <Plant position={[10.85, 0.18, -1.12]} tone="#6a845f" />
    </group>
  );
}

function CouncilChair({
  position,
  rotationY = 0,
  tone,
}: {
  position: [number, number, number];
  rotationY?: number;
  tone: string;
}) {
  return (
    <group position={position} rotation={[0, rotationY, 0]}>
      <RoundedBox args={[1.26, 0.16, 1.02]} radius={0.05} castShadow receiveShadow>
        <meshStandardMaterial color={tone} roughness={0.78} />
      </RoundedBox>
      <RoundedBox args={[1.16, 0.9, 0.14]} radius={0.05} position={[0, 0.44, -0.36]} castShadow receiveShadow>
        <meshStandardMaterial color={tone} roughness={0.78} />
      </RoundedBox>
      <mesh position={[-0.38, -0.22, 0.28]} castShadow>
        <cylinderGeometry args={[0.04, 0.04, 0.46, 12]} />
        <meshStandardMaterial color="#4d3a2d" roughness={0.72} />
      </mesh>
      <mesh position={[0.38, -0.22, 0.28]} castShadow>
        <cylinderGeometry args={[0.04, 0.04, 0.46, 12]} />
        <meshStandardMaterial color="#4d3a2d" roughness={0.72} />
      </mesh>
      <mesh position={[-0.38, -0.22, -0.28]} castShadow>
        <cylinderGeometry args={[0.04, 0.04, 0.46, 12]} />
        <meshStandardMaterial color="#4d3a2d" roughness={0.72} />
      </mesh>
      <mesh position={[0.38, -0.22, -0.28]} castShadow>
        <cylinderGeometry args={[0.04, 0.04, 0.46, 12]} />
        <meshStandardMaterial color="#4d3a2d" roughness={0.72} />
      </mesh>
    </group>
  );
}

function AdvisorCouncilDecor({ accent, fill, playerInPower, themeMode }: { accent: string; fill: string; playerInPower: boolean; themeMode: "light" | "dark" }) {
  return (
    <group>
      <RoundedBox args={[5.82, 0.3, 2.42]} radius={0.18} position={[0, 0.28, 0.74]} castShadow receiveShadow>
        <meshStandardMaterial color={themeMode === "light" ? "#855d43" : "#5a3d2c"} roughness={0.62} metalness={0.08} />
      </RoundedBox>
      <RoundedBox args={[5.16, 0.08, 1.86]} radius={0.14} position={[0, 0.42, 0.78]} castShadow receiveShadow>
        <meshStandardMaterial color={themeMode === "light" ? "#b99778" : "#70523f"} roughness={0.48} metalness={0.12} />
      </RoundedBox>
      <RoundedBox args={[1.26, 0.07, 0.3]} radius={0.03} position={[0, 0.5, 0.74]} castShadow receiveShadow>
        <meshStandardMaterial color={themeMode === "light" ? "#d9ccb8" : "#6b5648"} roughness={0.7} />
      </RoundedBox>

      <CouncilChair position={[-3.95, 0.46, -0.18]} rotationY={0.2} tone="#4d3526" />
      <CouncilChair position={[-1.3, 0.46, -0.9]} rotationY={0.08} tone={playerInPower ? "#4c5c6c" : "#5d4535"} />
      <CouncilChair position={[1.3, 0.46, -0.9]} rotationY={-0.08} tone="#4a3a30" />
      <CouncilChair position={[3.95, 0.46, -0.18]} rotationY={-0.2} tone="#4b4432" />
      <CouncilChair position={[0, 0.46, 2.46]} rotationY={Math.PI} tone="#6b4b33" />

      {[-10.8, 10.8].map((x) => (
        <Sofa key={`advisor-council-sofa-${x}`} position={[x, 0.44, -0.92]} tone={x < 0 ? "#3d2b22" : "#42312a"} />
      ))}
      {[-10.64, 10.64].map((x) => (
        <RoundedBox key={`advisor-council-table-${x}`} args={[0.94, 0.12, 0.7]} radius={0.05} position={[x, 0.26, 0.06]} castShadow receiveShadow>
          <meshStandardMaterial color="#6a4930" roughness={0.58} />
        </RoundedBox>
      ))}

      {[-11.6, -10.85, 10.85, 11.6].map((x) => (
        <Drape key={`advisor-council-drape-${x}`} position={[x, 2.58, -4.32]} color={x < 0 ? "#8e5e43" : "#4b5e73"} scale={[0.68, 1.08, 1]} />
      ))}

      <Flag position={[-15.15, 1.82, -4.26]} accent={accent} />
      <Flag position={[15.15, 1.82, -4.26]} accent={fill} />
      <Bookcase position={[-13.4, 1.34, -1.72]} accent={themeMode === "light" ? "#8b6a51" : "#4f392b"} />
      <Bookcase position={[13.4, 1.34, -1.72]} accent={themeMode === "light" ? "#7a5d49" : "#433126"} />
      <Lamp position={[-9.35, 1.56, -2.12]} color={accent} />
      <Lamp position={[9.35, 1.56, -2.12]} color={fill} />
      <Plant position={[-10.8, 0.18, -1.16]} tone="#5d7651" />
      <Plant position={[10.8, 0.18, -1.16]} tone="#6a845f" />
      <Plant position={[-6.8, 0.18, 1.74]} tone="#5f7962" />
      <Plant position={[6.8, 0.18, 1.74]} tone="#6b845c" />
    </group>
  );
}

function CitizenDecor({ accent, fill, themeMode }: { accent: string; fill: string; themeMode: "light" | "dark" }) {
  return (
    <group>
      <RoundedBox args={[8.3, 0.08, 98]} radius={0.03} position={[0, 0, -24]} receiveShadow>
        <meshStandardMaterial color={themeMode === "light" ? "#78695a" : "#3c3530"} roughness={0.97} />
      </RoundedBox>
      <mesh position={[0, 0.126, -24]} rotation={[-Math.PI / 2, 0, 0]} receiveShadow>
        <planeGeometry args={[7.6, 96]} />
        <meshStandardMaterial color={themeMode === "light" ? "#6c5f53" : "#312c28"} roughness={0.98} />
      </mesh>
      {[-32, -14, 4, 22].map((z) => (
        <mesh key={`center-line-${z}`} position={[0, 0.136, z]} rotation={[-Math.PI / 2, 0, 0]} receiveShadow>
          <planeGeometry args={[0.18, 5.8]} />
          <meshStandardMaterial color={themeMode === "light" ? "#d6c5ac" : "#8e795f"} roughness={0.88} />
        </mesh>
      ))}
      {[-12.0, 12.0].map((x) => (
        <RoundedBox key={`sidewalk-${x}`} args={[11.2, 0.1, 96]} radius={0.02} position={[x, 0.1, -24]} receiveShadow>
          <meshStandardMaterial color={themeMode === "light" ? "#b8a793" : "#685b4f"} roughness={0.9} />
        </RoundedBox>
      ))}
      {[-4.25, 4.25].map((x) => (
        <mesh key={`curb-${x}`} position={[x, 0.1, -24]} receiveShadow>
          <boxGeometry args={[0.18, 0.16, 96]} />
          <meshStandardMaterial color={themeMode === "light" ? "#8e7a66" : "#7c6957"} roughness={0.84} />
        </mesh>
      ))}
      <mesh position={[0, 0.165, -24]} rotation={[-Math.PI / 2, 0, 0]} receiveShadow>
        <planeGeometry args={[1.1, 96]} />
        <meshStandardMaterial color={themeMode === "light" ? "#847769" : "#433c37"} roughness={0.98} />
      </mesh>
      {[
        [-10.1, -20.5],
        [-9.6, -4.4],
        [9.6, 1.6],
        [10.1, 18.2],
      ].map(([x, z], index) => (
        <StreetLamp key={`citizen-lamp-${x}-${z}`} position={[x, 0.2, z]} glow={index < 2 ? accent : fill} />
      ))}
      {[-35.2, -21.2, -7.2, 6.8, 20.8].flatMap((zOffset) =>
        [-13.3, -10.1, 10.1, 13.3].map((x, index) => (
          <Townhouse
            key={`townhouse-${zOffset}-${x}`}
            position={[x, 1.65, zOffset - 4.65]}
            tone={(index + Math.round(zOffset)) % 2 === 0 ? "#7d5d48" : "#49606f"}
          />
        )),
      )}
      {[
        { position: [-12.9, 1.28, -24.2] as [number, number, number], rotationY: Math.PI / 2, accent: "#8a624f" },
        { position: [-12.9, 1.28, -8.8] as [number, number, number], rotationY: Math.PI / 2, accent: "#557086" },
        { position: [-12.9, 1.28, 7.1] as [number, number, number], rotationY: Math.PI / 2, accent: "#8a624f" },
        { position: [12.9, 1.28, -23.1] as [number, number, number], rotationY: -Math.PI / 2, accent: "#557086" },
        { position: [12.9, 1.28, -7.4] as [number, number, number], rotationY: -Math.PI / 2, accent: "#8a624f" },
        { position: [12.9, 1.28, 8.6] as [number, number, number], rotationY: -Math.PI / 2, accent: "#557086" },
      ].map((storefront, index) => (
        <Storefront
          key={`storefront-${index}`}
          position={storefront.position}
          rotationY={storefront.rotationY}
          accent={storefront.accent}
        />
      ))}
      <ParkedCar position={[-6.3, 0.26, -11.2]} color="#556574" />
      <ParkedCar position={[6.3, 0.26, 8.6]} color="#8a624f" rotationY={Math.PI} />
      <Newsstand position={[-12.2, 0.58, 13.2]} tone="#5c4636" />
      <FireHydrant position={[12.2, 0.2, -18.2]} color="#af5a3f" />
      <Bench position={[-11.8, 0.28, 6.5]} tone="#5a4331" />
      <Bench position={[11.8, 0.28, -7.6]} tone="#4e3a2e" />
      <Bench position={[-11.7, 0.28, -22.5]} tone="#5a4331" />
      <Bench position={[11.7, 0.28, 21.2]} tone="#4e3a2e" />
      <Plant position={[-12.1, 0.18, 3.8]} tone="#65805b" />
      <Plant position={[12.1, 0.18, -5.1]} tone="#5f7962" />
      <Plant position={[-12.0, 0.18, -18.1]} tone="#6b845c" />
      <Plant position={[12.1, 0.18, 15.6]} tone="#6a845f" />
      {[-24.5, -8.5, 8.5].flatMap((z) =>
        [-12.9, 12.9].map((x) => (
          <mesh key={`street-marquee-${x}-${z}`} position={[x, 3.32, z]}>
            <boxGeometry args={[1.48, 0.12, 0.34]} />
            <meshStandardMaterial color={x < 0 ? "#795943" : "#587185"} roughness={0.9} />
          </mesh>
        )),
      )}
      {[-25.5, -11.5, 2.5, 16.5].flatMap((zOffset) =>
        [-12.1, 12.1].map((x) => (
          <Plant key={`street-planter-${x}-${zOffset}`} position={[x, 0.18, zOffset]} tone={x < 0 ? "#6c845f" : "#63805e"} />
        )),
      )}
      {[-35.2, -21.2, -7.2, 6.8, 20.8].flatMap((zOffset) =>
        [-13.3, -10.1, 10.1, 13.3].map((x) => (
          <mesh key={`stoop-${zOffset}-${x}`} position={[x, 0.16, zOffset - 3.65]} receiveShadow>
            <boxGeometry args={[1.2, 0.16, 0.65]} />
            <meshStandardMaterial color="#6d5847" roughness={0.82} />
          </mesh>
        )),
      )}
    </group>
  );
}

function DebateDecor({ accent, fill, themeMode }: { accent: string; fill: string; themeMode: "light" | "dark" }) {
  const seats = useMemo(() => {
    const output: Array<[number, number, number]> = [];
    for (let row = 0; row < 6; row += 1) {
      for (let seat = -5; seat <= 5; seat += 1) {
        const fan = row * 0.09;
        output.push([seat * (0.92 + fan), 0.18 + row * 0.32, -0.9 - row * 1.62]);
      }
    }
    return output;
  }, []);
  const audience = useMemo(() => {
    const output: Array<[number, number, number]> = [];
    for (let row = 0; row < 6; row += 1) {
      for (let seat = -5; seat <= 5; seat += 1) {
        if ((row + seat) % 2 === 0) {
          const fan = row * 0.09;
          output.push([seat * (0.92 + fan), 0.36 + row * 0.32, -0.74 - row * 1.62]);
        }
      }
    }
    return output;
  }, []);
  const balconyAudience = useMemo(() => {
    const output: Array<[number, number, number]> = [];
    for (let row = 0; row < 2; row += 1) {
      for (let seat = -4; seat <= 4; seat += 1) {
        if ((seat + row) % 2 === 0) {
          output.push([seat * 0.92, 2.12 + row * 0.3, -7.0 - row * 0.9]);
        }
      }
    }
    return output;
  }, []);
  const tiers = useMemo(
    () => Array.from({ length: 6 }, (_, row) => ({
      width: 13.6 + row * 1.05,
      depth: 1.24,
      y: 0.05 + row * 0.28,
      z: -0.92 - row * 1.62,
    })),
    [],
  );

  return (
    <group>
      <mesh position={[0, 0.04, 1.25]} rotation={[-Math.PI / 2, 0, 0]} receiveShadow>
        <circleGeometry args={[6.6, 72]} />
        <meshStandardMaterial color={themeMode === "light" ? "#8f715d" : "#5a3d2b"} roughness={0.85} />
      </mesh>
      <RoundedBox args={[12.4, 0.24, 8.6]} radius={0.08} position={[0, 0.14, 1.18]} receiveShadow>
        <meshStandardMaterial color="#473127" roughness={0.92} />
      </RoundedBox>
      {tiers.map((tier, index) => (
        <RoundedBox key={`auditorium-tier-${index}`} args={[tier.width, 0.22, tier.depth]} radius={0.06} position={[0, tier.y, tier.z]} receiveShadow>
          <meshStandardMaterial color={index % 2 === 0 ? "#3b2a22" : "#31231d"} roughness={0.94} />
        </RoundedBox>
      ))}

      <RoundedBox args={[0.92, 1.05, 0.72]} radius={0.06} position={[-2.8, 0.52, 3.12]} castShadow receiveShadow>
        <meshStandardMaterial color="#69462c" roughness={0.54} metalness={0.07} />
      </RoundedBox>
      <RoundedBox args={[0.92, 1.05, 0.72]} radius={0.06} position={[2.85, 0.52, 2.92]} castShadow receiveShadow>
        <meshStandardMaterial color="#4a5366" roughness={0.54} metalness={0.07} />
      </RoundedBox>

      {seats.map(([x, y, z], index) => (
        <group key={`${x}-${y}-${z}-${index}`} position={[x, y, z]}>
          <RoundedBox args={[0.65, 0.3, 0.48]} radius={0.03} receiveShadow>
            <meshStandardMaterial color="#2b201d" roughness={0.95} />
          </RoundedBox>
          <RoundedBox args={[0.65, 0.72, 0.12]} radius={0.03} position={[0, 0.28, -0.16]} receiveShadow>
            <meshStandardMaterial color="#261c19" roughness={0.95} />
          </RoundedBox>
        </group>
      ))}
      {audience.map(([x, y, z], index) => (
        <AudienceFigure key={`audience-${x}-${y}-${z}-${index}`} position={[x, y, z]} tone={index % 3 === 0 ? "#7d6b57" : index % 2 === 0 ? "#677587" : "#5f4a3e"} />
      ))}
      {balconyAudience.map(([x, y, z], index) => (
        <AudienceFigure key={`balcony-audience-${x}-${y}-${z}-${index}`} position={[x, y, z]} tone={index % 2 === 0 ? "#6d7886" : "#735646"} />
      ))}
      {[-4.8, -2.4, 0, 2.4, 4.8].map((x) => (
        <Lamp key={`aisle-${x}`} position={[x, 0.42, 5.0]} color={accent} />
      ))}

      <RoundedBox args={[2.1, 0.42, 1.55]} radius={0.06} position={[0, 0.28, 5.35]} castShadow receiveShadow>
        <meshStandardMaterial color="#2a1f1a" roughness={0.9} />
      </RoundedBox>
      <RoundedBox args={[12.4, 0.36, 0.58]} radius={0.08} position={[0, 0.16, -0.98]} receiveShadow>
        <meshStandardMaterial color="#2b1f1b" roughness={0.92} />
      </RoundedBox>
      <RoundedBox args={[10.1, 0.16, 0.32]} radius={0.05} position={[0, 2.08, -6.12]} receiveShadow>
        <meshStandardMaterial color={themeMode === "light" ? "#d2bca6" : "#45352e"} roughness={0.84} />
      </RoundedBox>
      {[-8.8, 8.8].map((x) => (
        <RoundedBox key={`debate-box-rail-${x}`} args={[2.26, 0.14, 0.26]} radius={0.04} position={[x, 4.98, -1.1]} receiveShadow>
          <meshStandardMaterial color={themeMode === "light" ? "#d4c0aa" : "#46352d"} roughness={0.84} />
        </RoundedBox>
      ))}

      <LightCone position={[-2.2, 5.3, 2.9]} color={accent} />
      <LightCone position={[2.2, 5.1, 2.7]} color={fill} />
    </group>
  );
}

function BriefingDecor({ accent, fill, themeMode }: { accent: string; fill: string; themeMode: "light" | "dark" }) {
  return (
    <group>
      <RoundedBox args={[2.4, 1.44, 0.08]} radius={0.05} position={[-5.6, 2.42, -4.2]} castShadow>
        <meshStandardMaterial color={themeMode === "light" ? "#90a5b1" : "#44515b"} emissive={themeMode === "light" ? "#90a5b1" : "#44515b"} emissiveIntensity={0.18} roughness={0.26} />
      </RoundedBox>
      <RoundedBox args={[2.4, 1.44, 0.08]} radius={0.05} position={[5.6, 2.42, -4.2]} castShadow>
        <meshStandardMaterial color={themeMode === "light" ? "#b29885" : "#5b4d44"} emissive={themeMode === "light" ? "#b29885" : "#5b4d44"} emissiveIntensity={0.16} roughness={0.26} />
      </RoundedBox>
      <RoundedBox args={[6.6, 0.38, 2.65]} radius={0.12} position={[0, 0.34, 0.78]} castShadow receiveShadow>
        <meshStandardMaterial color={themeMode === "light" ? "#5c4130" : "#2a1e18"} roughness={0.82} />
      </RoundedBox>
      <RoundedBox args={[5.76, 0.14, 1.92]} radius={0.1} position={[0, 0.58, 0.82]} castShadow receiveShadow>
        <meshStandardMaterial color={themeMode === "light" ? "#b79172" : "#6b4d3b"} roughness={0.52} metalness={0.1} />
      </RoundedBox>
      <RoundedBox args={[1.42, 0.26, 1.28]} radius={0.1} position={[0, 0.16, 1.48]} receiveShadow>
        <meshStandardMaterial color="#513a2a" roughness={0.82} />
      </RoundedBox>
      <RoundedBox args={[6.2, 0.28, 2.65]} radius={0.12} position={[0, 0.22, -1.78]} receiveShadow>
        <meshStandardMaterial color="#2d221c" roughness={0.9} />
      </RoundedBox>
      {[-2.8, -0.95, 0.95, 2.8].map((x, index) => (
        <RoundedBox key={`console-${x}`} args={[1.02, 0.22, 0.78]} radius={0.04} position={[x, 0.44, -0.72]} castShadow receiveShadow>
          <meshStandardMaterial color={index % 2 === 0 ? "#5c4331" : "#4f382b"} roughness={0.6} />
        </RoundedBox>
      ))}
      <RoundedBox args={[1.3, 0.74, 0.06]} radius={0.04} position={[-5.6, 2.46, -3.9]} castShadow>
        <meshStandardMaterial color={accent} emissive={accent} emissiveIntensity={0.18} roughness={0.35} />
      </RoundedBox>
      <RoundedBox args={[1.42, 0.84, 0.06]} radius={0.04} position={[5.7, 2.3, -3.86]} castShadow>
        <meshStandardMaterial color={fill} emissive={fill} emissiveIntensity={0.18} roughness={0.35} />
      </RoundedBox>
      <Lamp position={[-4.1, 1.52, -1.18]} color={accent} />
      <Lamp position={[4.1, 1.52, -1.18]} color={fill} />
      <Drape position={[-6.95, 2.54, -4.2]} color="#5d4132" scale={[1.2, 1.24, 1]} />
      <Drape position={[6.95, 2.54, -4.2]} color="#425566" scale={[1.2, 1.24, 1]} />
      {[-5.8, 5.8].map((x) => (
        <Plant key={`briefing-plant-${x}`} position={[x, 0.16, -1.82]} tone={x < 0 ? "#5f7962" : "#6b845c"} />
      ))}
    </group>
  );
}

function CharacterFigure({
  position,
  facing,
  activity,
  scale,
  palette,
  silhouette,
  interactive = false,
  highlighted = false,
  floorActive = false,
  floorPending = false,
  followRef,
  animate = true,
  shadows = true,
  opacity = 1,
  onHoverChange,
  onSelect,
}: {
  position: [number, number, number];
  facing: number;
  activity: ScenePresence["playerActivity"];
  scale: number;
  palette: { base: string; glow: string; metallic: string };
  silhouette?: {
    head: number;
    torsoWidth: number;
    torsoHeight: number;
    torsoDepth: number;
    armLength: number;
    legLength: number;
  };
  interactive?: boolean;
  highlighted?: boolean;
  floorActive?: boolean;
  floorPending?: boolean;
  followRef?: MutableRefObject<StreetPlayerState>;
  animate?: boolean;
  shadows?: boolean;
  opacity?: number;
  onHoverChange?: (hovered: boolean) => void;
  onSelect?: () => void;
}) {
  const groupRef = useRef<THREE.Group>(null);
  const headRef = useRef<THREE.Mesh>(null);
  const armRef = useRef<THREE.Group>(null);
  const floorGlowRef = useRef(0);
  const floorLightRef = useRef<THREE.PointLight>(null);
  const floorOrbMaterialRef = useRef<THREE.MeshBasicMaterial>(null);
  const floorHaloMaterialRef = useRef<THREE.MeshBasicMaterial>(null);
  const floorRingMaterialRef = useRef<THREE.MeshBasicMaterial>(null);
  const [hovered, setHovered] = useState(false);
  const [floorCueMounted, setFloorCueMounted] = useState(floorActive || floorPending);
  const form = silhouette ?? {
    head: 0.34,
    torsoWidth: 0.95,
    torsoHeight: 1.45,
    torsoDepth: 0.62,
    armLength: 0.62,
    legLength: 0.82,
  };
  const emphasis = hovered || highlighted || floorActive || floorPending;
  useEffect(() => {
    if (floorActive || floorPending) {
      setFloorCueMounted(true);
      return undefined;
    }
    const timer = window.setTimeout(() => setFloorCueMounted(false), 760);
    return () => window.clearTimeout(timer);
  }, [floorActive, floorPending]);
  const handleHoverStart = interactive
    ? (event: ThreeEvent<PointerEvent>) => {
        event.stopPropagation();
        setHovered(true);
        document.body.style.cursor = "pointer";
        onHoverChange?.(true);
      }
    : undefined;
  const handleHoverEnd = interactive
    ? (event: ThreeEvent<PointerEvent>) => {
        event.stopPropagation();
        setHovered(false);
        document.body.style.cursor = "";
        onHoverChange?.(false);
      }
    : undefined;
  const handleSelect = interactive
    ? (event: ThreeEvent<MouseEvent>) => {
        event.stopPropagation();
        onSelect?.();
      }
    : undefined;

  useFrame((state, delta) => {
    if (!animate) {
      return;
    }
    const group = groupRef.current;
    const head = headRef.current;
    const arms = armRef.current;
    if (!group || !head || !arms) {
      return;
    }
    const time = state.clock.elapsedTime;
    const amplitude = activity === "speaking" ? 0.14 : floorActive ? 0.1 : activity === "listening" ? 0.05 : 0.02;
    const speed = activity === "speaking" ? 5.5 : floorActive ? 4.3 : activity === "listening" ? 2.4 : 1.4;
    const anchor = followRef?.current;
    const translatedX = anchor ? anchor.x : position[0];
    const translatedZ = anchor ? anchor.z : position[2];
    group.position.set(translatedX, position[1] + Math.sin(time * speed) * amplitude, translatedZ);
    group.rotation.y = THREE.MathUtils.lerp(group.rotation.y, anchor ? anchor.heading : facing, 0.08);
    head.rotation.z = Math.sin(time * speed * 0.7) * (activity === "speaking" ? 0.08 : 0.03);
    head.rotation.x = Math.sin(time * speed * 0.9) * (activity === "speaking" ? 0.05 : 0.02);
    arms.rotation.z = Math.sin(time * speed) * (activity === "speaking" ? 0.08 : 0.02);

    const floorTarget = floorActive ? (floorPending ? 0.64 : 1) : 0;
    floorGlowRef.current = THREE.MathUtils.damp(floorGlowRef.current, floorTarget, 6.2, delta);
    const glow = floorGlowRef.current;
    if (floorLightRef.current) {
      floorLightRef.current.intensity = glow * (floorPending ? 0.95 : 1.7);
    }
    if (floorOrbMaterialRef.current) {
      floorOrbMaterialRef.current.opacity = glow * (floorPending ? 0.18 : 0.3) * opacity;
    }
    if (floorHaloMaterialRef.current) {
      floorHaloMaterialRef.current.opacity = glow * (floorPending ? 0.18 : 0.34) * opacity;
    }
    if (floorRingMaterialRef.current) {
      floorRingMaterialRef.current.opacity = glow * (floorPending ? 0.22 : 0.36) * opacity;
    }
  });

  return (
    <group ref={groupRef} position={position} scale={scale}>
      {interactive ? (
        <mesh
          position={[0, 1.18, 0]}
          onClick={handleSelect}
          onPointerOver={handleHoverStart}
          onPointerOut={handleHoverEnd}
        >
          <capsuleGeometry args={[0.56, 1.55, 6, 10]} />
          <meshBasicMaterial transparent opacity={0} depthWrite={false} />
        </mesh>
      ) : null}
      <mesh position={[0, 0.02, 0]} rotation={[-Math.PI / 2, 0, 0]} receiveShadow>
        <circleGeometry args={[0.6, 18]} />
        <meshBasicMaterial color="#000000" transparent opacity={0.16 * opacity} />
      </mesh>
      {(interactive || emphasis) ? (
        <mesh position={[0, 0.04, 0]} rotation={[-Math.PI / 2, 0, 0]}>
          <ringGeometry args={[0.68, 0.9, 28]} />
          <meshBasicMaterial color={palette.glow} transparent opacity={(floorActive ? 0.58 : emphasis ? 0.42 : 0.18) * opacity} />
        </mesh>
      ) : null}
      {floorCueMounted ? (
        <>
          <pointLight ref={floorLightRef} position={[0, 2.25, 0.55]} color={palette.glow} intensity={0} distance={4.2} />
          <mesh position={[0, 2.52, 0]}>
            <sphereGeometry args={[0.2, 16, 16]} />
            <meshBasicMaterial ref={floorOrbMaterialRef} color={palette.glow} transparent opacity={0} />
          </mesh>
          <mesh position={[0, 0.08, 0]} rotation={[-Math.PI / 2, 0, 0]}>
            <ringGeometry args={[0.76, 0.9, 36]} />
            <meshBasicMaterial ref={floorHaloMaterialRef} color={palette.glow} transparent opacity={0} />
          </mesh>
          <mesh position={[0, 0.085, 0]} rotation={[-Math.PI / 2, 0, 0]}>
            <ringGeometry args={[0.94, 1.16, 36]} />
            <meshBasicMaterial ref={floorRingMaterialRef} color={palette.glow} transparent opacity={0} />
          </mesh>
        </>
      ) : null}

      <mesh ref={headRef} position={[0, 1.82, 0]} castShadow={shadows}>
        <sphereGeometry args={[form.head, 18, 18]} />
        <meshStandardMaterial transparent opacity={opacity} color={palette.glow} emissive={palette.glow} emissiveIntensity={emphasis ? 0.18 : 0.06} roughness={0.32} metalness={0.08} />
      </mesh>

      <RoundedBox
        args={[form.torsoWidth, form.torsoHeight, form.torsoDepth]}
        radius={0.16}
        position={[0, 0.98, 0]}
        castShadow={shadows}
        receiveShadow={shadows}
      >
        <meshStandardMaterial transparent opacity={opacity} color={palette.base} roughness={0.48} metalness={0.12} />
      </RoundedBox>

      <group ref={armRef} position={[0, 1.05, 0]}>
        <mesh position={[-0.68, 0.08, 0]} castShadow={shadows}>
          <capsuleGeometry args={[0.11, form.armLength, 4, 8]} />
          <meshStandardMaterial transparent opacity={opacity} color={palette.base} roughness={0.5} metalness={0.12} />
        </mesh>
        <mesh position={[0.68, 0.08, 0]} castShadow={shadows}>
          <capsuleGeometry args={[0.11, form.armLength, 4, 8]} />
          <meshStandardMaterial transparent opacity={opacity} color={palette.base} roughness={0.5} metalness={0.12} />
        </mesh>
      </group>

      <mesh position={[-0.24, 0.08, 0]} castShadow={shadows}>
        <capsuleGeometry args={[0.11, form.legLength, 4, 8]} />
        <meshStandardMaterial transparent opacity={opacity} color={palette.metallic} roughness={0.56} metalness={0.08} />
      </mesh>
      <mesh position={[0.24, 0.08, 0]} castShadow={shadows}>
        <capsuleGeometry args={[0.11, form.legLength, 4, 8]} />
        <meshStandardMaterial transparent opacity={opacity} color={palette.metallic} roughness={0.56} metalness={0.08} />
      </mesh>

      <mesh position={[0, 2.5, 0]} castShadow={shadows}>
        <sphereGeometry args={[0.11, 12, 12]} />
        <meshStandardMaterial transparent opacity={opacity} color={palette.glow} emissive={palette.glow} emissiveIntensity={activity === "speaking" ? 1.2 : emphasis ? 0.65 : 0.35} />
      </mesh>
    </group>
  );
}

function citizenAppearance(citizen: CitizenSnapshot, index: number) {
  const hash = Array.from(citizen.citizen_id).reduce((value, char, charIndex) => value + char.charCodeAt(0) * (charIndex + 3), 0);
  const palettes = [
    { base: "#6f8aa2", glow: "#dbe8f3", metallic: "#879cb0" },
    { base: "#8a624f", glow: "#f0d7c6", metallic: "#ab8370" },
    { base: "#5f7c67", glow: "#d8e8d3", metallic: "#7b9a84" },
    { base: "#7a6a8f", glow: "#e7deef", metallic: "#9887ad" },
    { base: "#92684c", glow: "#efd7c2", metallic: "#ac8368" },
  ];
  return {
    palette: palettes[hash % palettes.length],
    scale: 0.62 + (hash % 5) * 0.035,
    facing: index % 2 === 0 ? 0.28 : -0.28,
    silhouette: {
      head: 0.27 + (hash % 4) * 0.015,
      torsoWidth: 0.76 + (hash % 5) * 0.04,
      torsoHeight: 1.08 + (hash % 4) * 0.09,
      torsoDepth: 0.5 + (hash % 3) * 0.035,
      armLength: 0.48 + (hash % 4) * 0.04,
      legLength: 0.64 + (hash % 5) * 0.045,
    },
  };
}

function extraStreetAppearance(seed: number, index: number) {
  const palettes = [
    { base: "#4f5e4f", glow: "#9da89a", metallic: "#6d7a6e" },
    { base: "#614c40", glow: "#ab9a8d", metallic: "#766257" },
    { base: "#4f6171", glow: "#98a8b6", metallic: "#6c7f91" },
    { base: "#5f5769", glow: "#a9a0b5", metallic: "#766e84" },
  ];
  return {
    palette: palettes[seed % palettes.length],
    scale: 0.4 + (seed % 4) * 0.03,
    facing: index % 2 === 0 ? 0.18 : -0.18,
    silhouette: {
      head: 0.23 + (seed % 4) * 0.01,
      torsoWidth: 0.68 + (seed % 4) * 0.035,
      torsoHeight: 0.98 + (seed % 5) * 0.06,
      torsoDepth: 0.45 + (seed % 3) * 0.025,
      armLength: 0.4 + (seed % 4) * 0.035,
      legLength: 0.56 + (seed % 4) * 0.04,
    },
  };
}

function Lamp({ position, color }: { position: [number, number, number]; color: string }) {
  return (
    <group position={position}>
      <mesh castShadow>
        <cylinderGeometry args={[0.08, 0.08, 0.5, 20]} />
        <meshStandardMaterial color="#7c5a43" roughness={0.45} metalness={0.45} />
      </mesh>
      <mesh position={[0, 0.33, 0]} castShadow>
        <sphereGeometry args={[0.16, 20, 20]} />
        <meshStandardMaterial color={color} emissive={color} emissiveIntensity={0.7} />
      </mesh>
    </group>
  );
}

function StreetLamp({ position, glow }: { position: [number, number, number]; glow: string }) {
  return (
    <group position={position}>
      <mesh castShadow receiveShadow>
        <cylinderGeometry args={[0.06, 0.08, 2.7, 18]} />
        <meshStandardMaterial color="#674a36" roughness={0.45} metalness={0.36} />
      </mesh>
      <mesh position={[0, 1.48, 0]} castShadow>
        <sphereGeometry args={[0.2, 20, 20]} />
        <meshStandardMaterial color={glow} emissive={glow} emissiveIntensity={0.6} />
      </mesh>
    </group>
  );
}

function PortraitFrame({ position, tint }: { position: [number, number, number]; tint: string }) {
  return (
    <group position={position}>
      <RoundedBox args={[1.56, 2.12, 0.12]} radius={0.06} castShadow receiveShadow>
        <meshStandardMaterial color={tint} roughness={0.82} metalness={0.1} />
      </RoundedBox>
      <RoundedBox args={[1.24, 1.8, 0.06]} radius={0.05} position={[0, 0, 0.06]} castShadow receiveShadow>
        <meshStandardMaterial color="#3a2b22" roughness={0.98} />
      </RoundedBox>
      <mesh position={[0, 0, 0.1]}>
        <planeGeometry args={[1.1, 1.64]} />
        <meshStandardMaterial color="#7e684f" emissive="#4e3f32" emissiveIntensity={0.08} roughness={0.96} />
      </mesh>
      <mesh position={[0, 0.14, 0.12]} castShadow>
        <circleGeometry args={[0.28, 24]} />
        <meshStandardMaterial color="#bfa88d" roughness={0.84} />
      </mesh>
      <mesh position={[0, -0.34, 0.12]} castShadow>
        <boxGeometry args={[0.44, 0.5, 0.04]} />
        <meshStandardMaterial color="#947a61" roughness={0.9} />
      </mesh>
    </group>
  );
}

function Townhouse({ position, tone }: { position: [number, number, number]; tone: string }) {
  return (
    <group position={position}>
      <RoundedBox args={[2.08, 4.08, 0.82]} radius={0.08} castShadow receiveShadow>
        <meshStandardMaterial color={tone} roughness={0.84} />
      </RoundedBox>
      <mesh position={[0, 2.02, 0.42]}>
        <boxGeometry args={[1.62, 0.14, 0.1]} />
        <meshStandardMaterial color="#d6c8b2" roughness={0.68} />
      </mesh>
      <mesh position={[0, -1.1, 0.42]}>
        <planeGeometry args={[0.6, 1.28]} />
        <meshStandardMaterial color="#33251c" roughness={0.88} />
      </mesh>
      {[-0.45, 0.45].map((x) => (
        <mesh key={x} position={[x, 0.34, 0.42]}>
          <planeGeometry args={[0.38, 0.78]} />
          <meshStandardMaterial color="#86a2b2" emissive="#5d7482" emissiveIntensity={0.18} transparent opacity={0.82} />
        </mesh>
      ))}
      <mesh position={[0, 1.48, 0.42]}>
        <planeGeometry args={[1.18, 0.52]} />
        <meshStandardMaterial color="#86a2b2" emissive="#5d7482" emissiveIntensity={0.14} transparent opacity={0.8} />
      </mesh>
    </group>
  );
}

function Storefront({
  position,
  accent,
  rotationY = 0,
}: {
  position: [number, number, number];
  accent: string;
  rotationY?: number;
}) {
  return (
    <group position={position} rotation={[0, rotationY, 0]}>
      <RoundedBox args={[2.35, 3.08, 0.78]} radius={0.08} castShadow receiveShadow>
        <meshStandardMaterial color="#4c3a30" roughness={0.86} />
      </RoundedBox>
      <mesh position={[0, 1.66, 0.42]}>
        <boxGeometry args={[2.14, 0.14, 0.1]} />
        <meshStandardMaterial color="#d9ccb7" roughness={0.7} />
      </mesh>
      <mesh position={[0, 0.64, 0.42]}>
        <boxGeometry args={[2.0, 0.18, 0.14]} />
        <meshStandardMaterial color={accent} roughness={0.6} />
      </mesh>
      <mesh position={[0, 1.04, 0.42]}>
        <planeGeometry args={[1.98, 0.54]} />
        <meshStandardMaterial color={accent} roughness={0.58} />
      </mesh>
      <mesh position={[0, -0.32, 0.42]}>
        <planeGeometry args={[1.82, 1.44]} />
        <meshStandardMaterial color="#89a3b0" emissive="#607989" emissiveIntensity={0.16} transparent opacity={0.76} />
      </mesh>
    </group>
  );
}

function Bench({ position, tone }: { position: [number, number, number]; tone: string }) {
  return (
    <group position={position}>
      <RoundedBox args={[0.9, 0.12, 0.3]} radius={0.03} castShadow receiveShadow>
        <meshStandardMaterial color={tone} roughness={0.74} />
      </RoundedBox>
      <RoundedBox args={[0.9, 0.42, 0.08]} radius={0.03} position={[0, 0.22, -0.12]} castShadow receiveShadow>
        <meshStandardMaterial color={tone} roughness={0.74} />
      </RoundedBox>
      <mesh position={[-0.3, -0.16, 0]} castShadow>
        <cylinderGeometry args={[0.03, 0.03, 0.34, 12]} />
        <meshStandardMaterial color="#4a392d" roughness={0.68} />
      </mesh>
      <mesh position={[0.3, -0.16, 0]} castShadow>
        <cylinderGeometry args={[0.03, 0.03, 0.34, 12]} />
        <meshStandardMaterial color="#4a392d" roughness={0.68} />
      </mesh>
    </group>
  );
}

function ParkedCar({
  position,
  color,
  rotationY = 0,
}: {
  position: [number, number, number];
  color: string;
  rotationY?: number;
}) {
  return (
    <group position={position} rotation={[0, rotationY, 0]}>
      <RoundedBox args={[1.55, 0.48, 0.82]} radius={0.12} castShadow receiveShadow>
        <meshStandardMaterial color={color} roughness={0.5} metalness={0.22} />
      </RoundedBox>
      <RoundedBox args={[0.86, 0.32, 0.68]} radius={0.1} position={[0.12, 0.28, 0]} castShadow receiveShadow>
        <meshStandardMaterial color="#90a4b5" roughness={0.32} metalness={0.18} />
      </RoundedBox>
      {[-0.48, 0.48].flatMap((x) => [-0.32, 0.32].map((z) => [x, z] as const)).map(([x, z]) => (
        <mesh key={`${x}-${z}`} position={[x, -0.22, z]} castShadow receiveShadow>
          <cylinderGeometry args={[0.14, 0.14, 0.12, 18]} />
          <meshStandardMaterial color="#1d1b1a" roughness={0.86} metalness={0.18} />
        </mesh>
      ))}
    </group>
  );
}

function Newsstand({ position, tone }: { position: [number, number, number]; tone: string }) {
  return (
    <group position={position}>
      <RoundedBox args={[0.82, 1.18, 0.62]} radius={0.05} castShadow receiveShadow>
        <meshStandardMaterial color={tone} roughness={0.78} />
      </RoundedBox>
      <mesh position={[0, 0.18, 0.33]}>
        <planeGeometry args={[0.58, 0.5]} />
        <meshStandardMaterial color="#d8ccb0" roughness={0.62} />
      </mesh>
      <mesh position={[0, 0.64, 0]} rotation={[0.35, 0, 0]} castShadow receiveShadow>
        <boxGeometry args={[0.92, 0.1, 0.72]} />
        <meshStandardMaterial color="#6f4e39" roughness={0.68} />
      </mesh>
    </group>
  );
}

function FireHydrant({ position, color }: { position: [number, number, number]; color: string }) {
  return (
    <group position={position}>
      <mesh castShadow receiveShadow>
        <cylinderGeometry args={[0.12, 0.15, 0.42, 16]} />
        <meshStandardMaterial color={color} roughness={0.62} metalness={0.12} />
      </mesh>
      <mesh position={[0, 0.28, 0]} castShadow>
        <sphereGeometry args={[0.14, 18, 18]} />
        <meshStandardMaterial color={color} roughness={0.56} metalness={0.12} />
      </mesh>
      <mesh position={[0.18, 0.12, 0]} rotation={[0, 0, Math.PI / 2]} castShadow>
        <cylinderGeometry args={[0.05, 0.05, 0.18, 12]} />
        <meshStandardMaterial color={color} roughness={0.56} metalness={0.12} />
      </mesh>
      <mesh position={[-0.18, 0.12, 0]} rotation={[0, 0, Math.PI / 2]} castShadow>
        <cylinderGeometry args={[0.05, 0.05, 0.18, 12]} />
        <meshStandardMaterial color={color} roughness={0.56} metalness={0.12} />
      </mesh>
    </group>
  );
}

function Drape({
  position,
  color,
  scale = [1, 1, 1],
}: {
  position: [number, number, number];
  color: string;
  scale?: [number, number, number];
}) {
  return (
    <group position={position} scale={scale}>
      <mesh castShadow receiveShadow>
        <boxGeometry args={[0.36, 3.1, 0.18]} />
        <meshStandardMaterial color={color} roughness={0.95} />
      </mesh>
    </group>
  );
}

function Bookcase({ position, accent }: { position: [number, number, number]; accent: string }) {
  return (
    <group position={position}>
      <RoundedBox args={[0.95, 2.8, 0.6]} radius={0.05} castShadow receiveShadow>
        <meshStandardMaterial color={accent} roughness={0.84} />
      </RoundedBox>
      {[-0.75, -0.1, 0.55].map((y) => (
        <mesh key={y} position={[0, y, 0]} castShadow receiveShadow>
          <boxGeometry args={[0.82, 0.06, 0.5]} />
          <meshStandardMaterial color="#6d4e39" roughness={0.76} />
        </mesh>
      ))}
      {[-0.24, 0, 0.24].map((x, index) => (
        <mesh key={`${x}-${index}`} position={[x, 0.78, 0.04]} castShadow receiveShadow>
          <boxGeometry args={[0.16, 0.58, 0.22]} />
          <meshStandardMaterial color={index % 2 === 0 ? "#536b7f" : "#8c6446"} roughness={0.64} />
        </mesh>
      ))}
    </group>
  );
}

function Sofa({ position, tone }: { position: [number, number, number]; tone: string }) {
  return (
    <group position={position}>
      <RoundedBox args={[1.8, 0.62, 0.78]} radius={0.08} castShadow receiveShadow>
        <meshStandardMaterial color={tone} roughness={0.88} />
      </RoundedBox>
      <RoundedBox args={[1.8, 0.74, 0.22]} radius={0.06} position={[0, 0.32, -0.24]} castShadow receiveShadow>
        <meshStandardMaterial color={tone} roughness={0.88} />
      </RoundedBox>
    </group>
  );
}

function AudienceFigure({ position, tone }: { position: [number, number, number]; tone: string }) {
  return (
    <group position={position}>
      <mesh position={[0, 0.44, 0]}>
        <sphereGeometry args={[0.16, 12, 12]} />
        <meshStandardMaterial color="#e2ccb1" roughness={0.34} metalness={0.04} />
      </mesh>
      <RoundedBox args={[0.42, 0.58, 0.24]} radius={0.05} position={[0, 0.05, 0]} receiveShadow>
        <meshStandardMaterial color={tone} roughness={0.78} />
      </RoundedBox>
    </group>
  );
}

function Plant({ position, tone }: { position: [number, number, number]; tone: string }) {
  return (
    <group position={position}>
      <mesh castShadow receiveShadow>
        <cylinderGeometry args={[0.22, 0.28, 0.34, 18]} />
        <meshStandardMaterial color="#634331" roughness={0.75} />
      </mesh>
      <mesh position={[0, 0.44, 0]} castShadow>
        <sphereGeometry args={[0.42, 18, 18]} />
        <meshStandardMaterial color={tone} roughness={0.92} />
      </mesh>
      <mesh position={[-0.16, 0.75, 0.08]} castShadow>
        <sphereGeometry args={[0.26, 18, 18]} />
        <meshStandardMaterial color={tone} roughness={0.92} />
      </mesh>
      <mesh position={[0.18, 0.72, -0.06]} castShadow>
        <sphereGeometry args={[0.24, 18, 18]} />
        <meshStandardMaterial color={tone} roughness={0.92} />
      </mesh>
    </group>
  );
}

function Flag({ position, accent }: { position: [number, number, number]; accent: string }) {
  return (
    <group position={position}>
      <mesh position={[0, 1.15, 0]} castShadow>
        <cylinderGeometry args={[0.04, 0.04, 2.4, 16]} />
        <meshStandardMaterial color="#8a6b51" roughness={0.42} metalness={0.5} />
      </mesh>
      <mesh position={[0.42, 1.6, 0.04]} castShadow>
        <boxGeometry args={[0.82, 0.58, 0.04]} />
        <meshStandardMaterial color={accent} roughness={0.45} metalness={0.08} />
      </mesh>
    </group>
  );
}

function LightCone({ position, color }: { position: [number, number, number]; color: string }) {
  return (
    <group position={position}>
      <mesh rotation={[Math.PI, 0, 0]}>
        <coneGeometry args={[0.95, 3.4, 24, 1, true]} />
        <meshBasicMaterial color={color} transparent opacity={0.08} depthWrite={false} />
      </mesh>
    </group>
  );
}
