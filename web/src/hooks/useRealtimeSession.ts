import { useEffect, useEffectEvent, useRef, useState } from "react";
import { callRealtimeTool, createRealtimeSession, generateCouncilTurn, speechStreamUrl, syncConversation } from "../lib/api";
import { councilVoiceForSpeaker, normalizeCouncilRoster, splitCouncilLines, type CouncilTurnContext } from "../lib/council";
import { makeRealtimeTurnDetection, REALTIME_TURN_DETECTION } from "../lib/realtimeConfig";
import type { AdvisorMode, AuditoriumMode, ConversationTurn, CouncilAdvisorProfile, RealtimeRole, RoomName, ScenePresence, SessionStatus, SimulationState } from "../types";

interface UseRealtimeSessionOptions {
  simulationId?: string;
  role: RealtimeRole;
  citizenId?: string;
  advisorMode?: AdvisorMode;
  auditoriumMode?: AuditoriumMode;
  autoResponse?: boolean;
  externalPlaybackActive?: boolean;
  councilContext?: CouncilTurnContext;
  councilRoster?: CouncilAdvisorProfile[];
  initialTurns: ConversationTurn[];
  onSimulationSync?: (simulation: SimulationState) => void;
  onCouncilFloorChange?: (floor: {
    lead: string;
    owner: string;
    contrast: string[];
    reason?: string;
  } | null) => void;
  onModeCommand?: (command: {
    room?: RoomName;
    advisorMode?: AdvisorMode;
    auditoriumMode?: AuditoriumMode;
    action?: "townhall_question" | "call_election" | "open_reels" | "close_reels" | "open_text" | "open_details" | "open_intel" | "close_panels" | "begin_reel" | "enter_war_room" | "toggle_theme" | "toggle_fullscreen" | "run_poll_now" | "run_queued_polls" | "update_policy_board";
    pollQuestion?: string;
    policyBoard?: {
      action: "set" | "add" | "clear" | "remove" | "replace";
      notes?: string[];
      index?: number;
    };
    citizenName?: string;
    streetCommand?: {
      kind: "nearest" | "query";
      query?: string;
    };
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

async function settleAudioStart(audioElement: HTMLMediaElement, playPromise: Promise<unknown>, timeoutMs = 1200) {
  let started = !audioElement.paused && !audioElement.ended;
  if (started) {
    return true;
  }
  await Promise.race([
    playPromise
      .then(() => {
        started = true;
      })
      .catch(() => undefined),
    new Promise<void>((resolve) => {
      const cleanup = () => {
        audioElement.removeEventListener("play", handleStarted);
        audioElement.removeEventListener("playing", handleStarted);
        audioElement.removeEventListener("timeupdate", handleStarted);
        window.clearTimeout(timer);
      };
      const handleStarted = () => {
        started = true;
        cleanup();
        resolve();
      };
      const timer = window.setTimeout(() => {
        cleanup();
        resolve();
      }, timeoutMs);
      audioElement.addEventListener("play", handleStarted, { once: true });
      audioElement.addEventListener("playing", handleStarted, { once: true });
      audioElement.addEventListener("timeupdate", handleStarted, { once: true });
    }),
  ]);
  return started || (!audioElement.paused && !audioElement.ended);
}

function waitForAudioCompletion(audioElement: HTMLAudioElement, fallbackMs = 16000) {
  return new Promise<"ended" | "error">((resolve) => {
    const cleanup = () => {
      audioElement.removeEventListener("ended", handleEnded);
      audioElement.removeEventListener("error", handleError);
      window.clearInterval(pollTimer);
      window.clearTimeout(timeoutTimer);
    };
    const handleEnded = () => {
      cleanup();
      resolve("ended");
    };
    const handleError = () => {
      cleanup();
      resolve("error");
    };
    const pollTimer = window.setInterval(() => {
      if (audioElement.error) {
        handleError();
        return;
      }
      if (audioElement.ended || (Number.isFinite(audioElement.duration) && audioElement.duration > 0 && audioElement.currentTime >= audioElement.duration - 0.06)) {
        handleEnded();
      }
    }, 240);
    const timeoutTimer = window.setTimeout(() => {
      cleanup();
      resolve("ended");
    }, fallbackMs);
    audioElement.addEventListener("ended", handleEnded, { once: true });
    audioElement.addEventListener("error", handleError, { once: true });
  });
}

function estimatedSpeechMs(text: string) {
  const words = sanitizeRealtimeText(text).split(/\s+/).filter(Boolean).length;
  return Math.max(1400, Math.min(9000, 1000 + words * 260));
}

async function getUserMediaWithTimeout(constraints: MediaStreamConstraints, timeoutMs = 12000) {
  const streamPromise = navigator.mediaDevices.getUserMedia(constraints);
  let timeoutHandle = 0;
  let timedOut = false;
  try {
    const timeoutPromise = new Promise<MediaStream>((_, reject) => {
      timeoutHandle = window.setTimeout(() => {
        timedOut = true;
        reject(new Error("Microphone permission timed out. Click Speak again to retry."));
      }, timeoutMs);
    });
    return await Promise.race([streamPromise, timeoutPromise]);
  } catch (caught) {
    if (timedOut) {
      void streamPromise
        .then((lateStream) => {
          lateStream.getTracks().forEach((track) => track.stop());
        })
        .catch(() => undefined);
    }
    throw caught;
  } finally {
    if (timeoutHandle) {
      window.clearTimeout(timeoutHandle);
    }
  }
}

async function fetchWithTimeout(input: RequestInfo | URL, init: RequestInit, timeoutMs: number) {
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await fetch(input, {
      ...init,
      signal: controller.signal,
    });
  } catch (caught) {
    if (caught instanceof DOMException && caught.name === "AbortError") {
      throw new Error("Realtime negotiation timed out");
    }
    throw caught;
  } finally {
    window.clearTimeout(timeout);
  }
}

function isMeaningfulHybridTranscript(text: string) {
  const normalized = sanitizeRealtimeText(text)
    .toLowerCase()
    .replace(/\b(?:uh|um|mm|hmm|ah|er|like|you know)\b/g, " ")
    .replace(/[^a-z0-9\s']/g, " ")
    .replace(/\s+/g, " ")
    .trim();
  if (!normalized) {
    return false;
  }
  const shortCommandPhrases = new Set([
    "street",
    "debate",
    "town hall",
    "single advisor",
    "solo advisor",
    "multi advisor",
    "council",
    "pause",
    "resume",
    "stop",
    "skip",
    "next stage",
    "next question",
  ]);
  if (shortCommandPhrases.has(normalized)) {
    return true;
  }
  const words = normalized.split(/\s+/).filter(Boolean);
  return words.length >= 3 || normalized.length >= 12;
}

function normalizedSpeechFingerprint(text: string) {
  return sanitizeRealtimeText(text)
    .toLowerCase()
    .replace(/[^a-z0-9\s']/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

function looksLikeHybridPlaybackEcho(text: string, events: ConversationTurn[]) {
  const transcript = normalizedSpeechFingerprint(text);
  if (!transcript || transcript.length < 10) {
    return false;
  }
  const latestAssistant = [...events]
    .reverse()
    .find((event) => event.speaker === "assistant" && sanitizeRealtimeText(event.text));
  if (!latestAssistant) {
    return false;
  }
  const assistantText = normalizedSpeechFingerprint(latestAssistant.text);
  if (!assistantText) {
    return false;
  }
  return assistantText.includes(transcript) || transcript.includes(assistantText);
}

function isExpectedRealtimeTeardownMessage(message: string) {
  const normalized = message.toLowerCase();
  return (
    normalized.includes("cancellation failed") ||
    normalized.includes("no active response") ||
    normalized.includes("response.cancel") ||
    normalized.includes("output_audio_buffer.clear")
  );
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
const COUNCIL_MAX_CONTINUATION_MS = 36000;
const COUNCIL_MAX_CONTINUATION_BEATS = 4;
const COUNCIL_CONTEXT_TURN_LIMIT = 12;
const COUNCIL_BARGE_IN_CONFIRM_MS = 140;
const COUNCIL_IDLE_SPEECH_START_CONFIRM_MS = 520;
const SILENT_AUDIO_DATA_URI = "data:audio/wav;base64,UklGRiQAAABXQVZFZm10IBAAAAABAAEAESsAACJWAAACABAAZGF0YQAAAAA=";

function councilTurnSignature(turns: LocalTurnInput[]) {
  return turns
    .map((turn) => `${(turn.speaker_name ?? turn.speaker ?? "").trim().toLowerCase()}:${sanitizeRealtimeText(turn.text).toLowerCase()}`)
    .filter(Boolean)
    .join(" | ");
}

function councilProvisionalTurns(turns: ConversationTurn[]) {
  const deduped: Array<{
    speaker: "user" | "assistant" | "system";
    speaker_name?: string;
    speaker_voice?: string;
    text: string;
    mode: "text" | "voice" | "system";
  }> = [];
  for (const turn of turns
    .filter((turn) => turn.speaker !== "system")
    .map((turn) => ({
      speaker: turn.speaker,
      speaker_name: turn.speaker_name,
      speaker_voice: turn.speaker_voice,
      text: sanitizeRealtimeText(turn.text),
      mode: turn.mode,
    }))
    .filter((turn) => turn.text)) {
    const prior = deduped.at(-1);
    if (
      prior &&
      prior.speaker === turn.speaker &&
      normalizedSpeechFingerprint(prior.text) === normalizedSpeechFingerprint(turn.text)
    ) {
      continue;
    }
    deduped.push(turn);
  }
  return deduped.slice(-COUNCIL_CONTEXT_TURN_LIMIT);
}

function titleCaseCommandValue(text: string) {
  return text
    .split(/\s+/)
    .filter(Boolean)
    .map((part) => part.slice(0, 1).toUpperCase() + part.slice(1))
    .join(" ");
}

function cleanVoiceCommandPayload(text: string) {
  return text
    .replace(/^(?:please\s+|can you\s+|could you\s+|would you\s+|let's\s+|lets\s+|i want to\s+|i'd like to\s+)/i, "")
    .replace(/\s+please$/i, "")
    .trim()
    .replace(/[.?!]+$/, "")
    .trim();
}

function isPolicyBoardPlaceholderNote(text: string) {
  const normalized = cleanVoiceCommandPayload(text)
    .toLowerCase()
    .replace(/[.?!;:-]+$/g, "")
    .replace(/\s+/g, " ")
    .trim();
  if (!normalized) {
    return true;
  }
  const placeholders = new Set([
    "this",
    "that",
    "it",
    "this one",
    "that one",
    "the idea",
    "our idea",
    "this idea",
    "that idea",
    "the policy idea",
    "the policy",
    "a policy",
    "the plan",
    "this plan",
    "the best idea",
    "best idea",
    "the best policy idea",
    "best policy idea",
    "the best concrete idea",
    "best concrete idea",
    "the best concrete policy idea",
    "best concrete policy idea",
  ]);
  if (placeholders.has(normalized)) {
    return true;
  }
  const words = normalized.split(/\s+/).filter(Boolean);
  return words.length <= 7 && words.includes("idea") && words.some((word) => ["best", "concrete", "policy"].includes(word));
}

function policyBoardCommandNotes(text: string) {
  return cleanVoiceCommandPayload(text)
    .split(/\s+(?:and then|plus)\s+|[;\n]+|\s+\/\s+/i)
    .map((item) => cleanVoiceCommandPayload(item))
    .filter((item) => item.length >= 4 && !isPolicyBoardPlaceholderNote(item))
    .slice(0, 6);
}

function policyBoardItemIndex(text?: string) {
  const value = (text ?? "").trim().toLowerCase();
  if (!value) {
    return undefined;
  }
  const wordToNumber: Record<string, number> = {
    one: 1,
    first: 1,
    two: 2,
    second: 2,
    three: 3,
    third: 3,
    four: 4,
    fourth: 4,
    five: 5,
    fifth: 5,
    six: 6,
    sixth: 6,
  };
  const parsed = /^\d+$/.test(value) ? Number(value) : wordToNumber[value];
  return Number.isFinite(parsed) && parsed > 0 ? parsed - 1 : undefined;
}

function interpretPollVoiceCommand(raw: string, normalized: string): {
  action: "run_poll_now" | "run_queued_polls";
  pollQuestion?: string;
} | null {
  if (/\b(?:run|take|conduct|launch|finish)\s+(?:the\s+)?(?:queued|pending|those|these|all)\s+polls?\b/.test(normalized)) {
    return { action: "run_queued_polls" };
  }
  if (!/\b(?:run|take|conduct|launch|do)\s+(?:a\s+|the\s+)?poll\b|\bpoll\s+(?:people|voters|citizens|the public)\b/.test(normalized)) {
    return null;
  }
  const question = cleanVoiceCommandPayload(
    raw.replace(
      /^.*?\bpoll(?:\s+(?:people|voters|citizens|the public))?(?:\s+(?:on|about|whether|if|asking|to ask))?\s*/i,
      "",
    ),
  );
  if (!question || /^(?:a poll|the poll|people|voters|citizens|the public)$/i.test(question)) {
    return { action: "run_queued_polls" };
  }
  return { action: "run_poll_now", pollQuestion: question };
}

function interpretPolicyBoardVoiceCommand(raw: string, normalized: string): {
  action: "update_policy_board";
  policyBoard: {
    action: "set" | "add" | "clear" | "remove" | "replace";
    notes?: string[];
    index?: number;
  };
} | null {
  if (/\b(?:clear|erase|wipe|blank)\s+(?:the\s+)?(?:policy\s+)?board\b/.test(normalized)) {
    return { action: "update_policy_board", policyBoard: { action: "clear" } };
  }
  const indexedReplaceMatch = raw.match(
    /\b(?:replace|change|update|rewrite)\s+(?:policy\s+|idea\s+|item\s+|number\s+|#)?(\d+|one|first|two|second|three|third|four|fourth|five|fifth|six|sixth)\s+(?:with|to|as)\s+(.{4,220})$/i,
  );
  if (indexedReplaceMatch) {
    const index = policyBoardItemIndex(indexedReplaceMatch[1]);
    const notes = policyBoardCommandNotes(indexedReplaceMatch[2]);
    if (index !== undefined && notes.length > 0) {
      return { action: "update_policy_board", policyBoard: { action: "replace", index, notes: [notes[0]] } };
    }
  }
  const indexedRemoveMatch = raw.match(
    /\b(?:remove|delete|erase|cross off|strike)\s+(?:policy\s+|idea\s+|item\s+|number\s+|#)?(\d+|one|first|two|second|three|third|four|fourth|five|fifth|six|sixth)(?:\s+(?:from|off)\s+(?:the\s+)?(?:policy\s+|ideas?\s+)?board)?\b/i,
  );
  if (indexedRemoveMatch) {
    const index = policyBoardItemIndex(indexedRemoveMatch[1]);
    if (index !== undefined) {
      return { action: "update_policy_board", policyBoard: { action: "remove", index } };
    }
  }
  const setMatch = raw.match(
    /\b(?:set|rewrite|replace|make)\s+(?:the\s+)?(?:policy\s+|ideas?\s+)?board\s+(?:to|as|be)\s+(.{6,320})$/i,
  );
  if (setMatch) {
    const notes = policyBoardCommandNotes(setMatch[1]);
    if (notes.length > 0) {
      return { action: "update_policy_board", policyBoard: { action: "set", notes } };
    }
  }
  const addMatch = raw.match(
    /\b(?:add|put|write|pin)\s+(.{4,160}?)\s+(?:on|to)\s+(?:the\s+)?(?:policy\s+|ideas?\s+)?board\b/i,
  );
  const removeMatch = raw.match(
    /\b(?:remove|delete|erase|cross off|strike)\s+(.{4,160}?)\s+(?:from|off)\s+(?:the\s+)?(?:policy\s+|ideas?\s+)?board\b/i,
  );
  const removeNotes = policyBoardCommandNotes(removeMatch?.[1] ?? "");
  if (removeNotes.length > 0) {
    return { action: "update_policy_board", policyBoard: { action: "remove", notes: removeNotes } };
  }
  const trailingMatch = raw.match(
    /\b(?:on|to)\s+(?:the\s+)?(?:policy\s+|ideas?\s+)?board\s*[:,-]?\s*(.{4,160})$/i,
  );
  const notes = policyBoardCommandNotes(addMatch?.[1] ?? trailingMatch?.[1] ?? "");
  if (notes.length === 0) {
    return null;
  }
  return { action: "update_policy_board", policyBoard: { action: "add", notes } };
}

function interpretBareModeCommand(normalized: string): {
  room?: RoomName;
  advisorMode?: AdvisorMode;
  auditoriumMode?: AuditoriumMode;
  action?: "townhall_question" | "call_election" | "open_reels" | "close_reels" | "open_text" | "open_details" | "open_intel" | "close_panels" | "begin_reel" | "enter_war_room" | "toggle_theme" | "toggle_fullscreen" | "run_queued_polls" | "update_policy_board";
  policyBoard?: {
    action: "clear";
  };
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
  if (/^(?:audience question|ask the audience|call on voter|next voter|next question|voter question)$/.test(trimmed)) {
    return { room: "debate", auditoriumMode: "town_hall", action: "townhall_question" };
  }
  if (/^(?:call election|call the election|run election|hold the election|go to election|count the vote|count votes|next stage|go to the next stage|advance stage|advance to the next stage)$/.test(trimmed)) {
    return { room: "debate", action: "call_election" };
  }
  if (/^(?:future reels|open reels|show reels|featurettes|show featurettes)$/.test(trimmed)) {
    return { action: "open_reels" };
  }
  if (/^(?:close reels|hide reels|exit reels|back from reels|close movie|close featurette)$/.test(trimmed)) {
    return { action: "close_reels" };
  }
  if (/^(?:begin reel|begin the reel|begin chapter reel|begin the chapter reel|start reel|start the reel|start documentary|play documentary|play intro|launch chapter reel)$/.test(trimmed)) {
    return { action: "begin_reel" };
  }
  if (/^(?:skip reel|skip the reel|skip documentary|enter room|enter the room|enter war room|go live|return to room|back to room)$/.test(trimmed)) {
    return { action: "enter_war_room" };
  }
  if (/^(?:type|type instead|keyboard|open keyboard|open text|text box|show text box|write instead)$/.test(trimmed)) {
    return { action: "open_text" };
  }
  if (/^(?:details|open details|show details|open panel|show panel|show room panel|room notes)$/.test(trimmed)) {
    return { action: "open_details" };
  }
  if (/^(?:intel|open intel|show intel|show numbers|show public mood|show polls|show statistics)$/.test(trimmed)) {
    return { action: "open_intel" };
  }
  if (/^(?:close panel|close panels|hide panel|hide panels|close details|hide details)$/.test(trimmed)) {
    return { action: "close_panels" };
  }
  if (/^(?:toggle theme|switch theme|light mode|dark mode|switch lights)$/.test(trimmed)) {
    return { action: "toggle_theme" };
  }
  if (/^(?:fullscreen|full screen|go fullscreen|go full screen|exit fullscreen|exit full screen|toggle fullscreen)$/.test(trimmed)) {
    return { action: "toggle_fullscreen" };
  }
  if (/^(?:run polls|run the polls|run queued polls|run pending polls|finish polls|finish the polls)$/.test(trimmed)) {
    return { action: "run_queued_polls" };
  }
  if (/^(?:clear board|clear the board|erase board|erase the board|wipe board|wipe the board)$/.test(trimmed)) {
    return { action: "update_policy_board", policyBoard: { action: "clear" } };
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

function interpretGlobalSceneAction(normalized: string): ReturnType<typeof interpretBareModeCommand> {
  if (/\b(?:close reels|hide reels|exit reels|close featurette)\b/.test(normalized)) {
    return { action: "close_reels" };
  }
  if (/\b(?:begin|start|play|launch)\s+(?:the\s+)?(?:chapter\s+)?(?:reel|documentary|intro)\b/.test(normalized)) {
    return { action: "begin_reel" };
  }
  if (/\b(?:skip|stop)\s+(?:the\s+)?(?:reel|documentary|intro)\b|\b(?:enter|return to)\s+(?:the\s+)?(?:war room|room)\b/.test(normalized)) {
    return { action: "enter_war_room" };
  }
  if (/\b(?:open|show)\s+(?:the\s+)?(?:details|panel|room panel|room notes)\b/.test(normalized)) {
    return { action: "open_details" };
  }
  if (/\b(?:open|show)\s+(?:the\s+)?(?:intel|numbers|polls|statistics|public mood)\b/.test(normalized)) {
    return { action: "open_intel" };
  }
  if (/\b(?:close|hide)\s+(?:the\s+)?(?:panel|panels|details|drawer)\b/.test(normalized)) {
    return { action: "close_panels" };
  }
  if (/\b(?:toggle|switch)\s+(?:the\s+)?theme\b|\b(?:light mode|dark mode)\b/.test(normalized)) {
    return { action: "toggle_theme" };
  }
  if (/\b(?:go\s+)?full\s*screen\b|\b(?:exit|toggle)\s+full\s*screen\b/.test(normalized)) {
    return { action: "toggle_fullscreen" };
  }
  return null;
}

function interpretModeCommand(text: string): {
  room?: RoomName;
  advisorMode?: AdvisorMode;
  auditoriumMode?: AuditoriumMode;
  action?: "townhall_question" | "call_election" | "open_reels" | "close_reels" | "open_text" | "open_details" | "open_intel" | "close_panels" | "begin_reel" | "enter_war_room" | "toggle_theme" | "toggle_fullscreen" | "run_poll_now" | "run_queued_polls" | "update_policy_board";
  pollQuestion?: string;
  policyBoard?: {
    action: "set" | "add" | "clear" | "remove" | "replace";
    notes?: string[];
    index?: number;
  };
  citizenName?: string;
  streetCommand?: {
    kind: "nearest" | "query";
    query?: string;
  };
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
  const pollCommand = interpretPollVoiceCommand(raw, normalized);
  if (pollCommand) {
    return pollCommand;
  }
  const policyBoardCommand = interpretPolicyBoardVoiceCommand(raw, normalized);
  if (policyBoardCommand) {
    return policyBoardCommand;
  }
  const globalSceneAction = interpretGlobalSceneAction(normalized);
  if (globalSceneAction) {
    return globalSceneAction;
  }
  if (!/\b(go to|take me to|bring me to|move me to|return to|back to|head to|switch to|let's go to|i want to go to|i'm ready to go to|i am ready to go to|talk to|speak to|speak with)\b/.test(normalized)) {
    if (/\b(?:audience question|ask the audience|call on voter|next voter|voter question)\b/.test(normalized)) {
      return { room: "debate", auditoriumMode: "town_hall", action: "townhall_question" };
    }
    if (/\b(?:call election|call the election|run election|hold the election|go to election|count the vote|count votes|next stage|go to the next stage|advance stage|advance to the next stage)\b/.test(normalized)) {
      return { room: "debate", action: "call_election" };
    }
    if (/\b(?:future reels|open reels|show reels|featurettes|show featurettes)\b/.test(normalized)) {
      return { action: "open_reels" };
    }
    if (/\b(?:type instead|open keyboard|keyboard|open text|show text box|write instead)\b/.test(normalized)) {
      return { action: "open_text" };
    }
    return null;
  }
  if (/\b(?:audience question|ask the audience|call on voter|next voter|voter question)\b/.test(normalized)) {
    return { room: "debate", auditoriumMode: "town_hall", action: "townhall_question" };
  }
  if (/\b(?:call election|call the election|run election|hold the election|go to election|count the vote|count votes|next stage|go to the next stage|advance stage|advance to the next stage)\b/.test(normalized)) {
    return { room: "debate", action: "call_election" };
  }
  if (/\b(?:future reels|open reels|show reels|featurettes|show featurettes)\b/.test(normalized)) {
    return { action: "open_reels" };
  }
  if (/\b(?:type instead|open keyboard|keyboard|open text|show text box|write instead)\b/.test(normalized)) {
    return { action: "open_text" };
  }
  if (/\b(?:talk to|speak to|speak with|bring me to|take me to|go to)\b.{0,24}\b(?:nearest|closest|nearby|someone nearby|person nearby|voter nearby)\b/.test(normalized)) {
    return { room: "citizens", streetCommand: { kind: "nearest" } };
  }
  const queryCitizenMatch = normalized.match(
    /\b(?:talk to|speak to|speak with|bring me to|take me to|go to)\s+(?:a|an|the)?\s*(kid|child|student|teacher|parent|older person|retiree|worker|small business owner|business owner|doctor|nurse|farmer|engineer|artist|someone optimistic|someone worried|someone skeptical|someone positive|supporter|opponent)\b/,
  );
  if (queryCitizenMatch) {
    return {
      room: "citizens",
      streetCommand: { kind: "query", query: queryCitizenMatch[1] },
    };
  }
  const citizenTargetMatch = raw.match(
    /(?:talk to|speak to|speak with|go to|take me to|bring me to|move me to|head to)\s+(?:the\s+)?([A-Za-z][A-Za-z' -]{1,56})(?:\s+(?:on|in)\s+the\s+street)?/i,
  );
  if (citizenTargetMatch) {
    const citizenName = citizenTargetMatch[1]
      .replace(/\b(?:out there|on the street|in the street|nearby)\b/gi, "")
      .trim();
    const citizenTargetOnly = citizenName.toLowerCase();
    if (
      citizenName &&
      !/^(?:street|citizens?|people|advisor|advisors|debate|town hall|briefing|documentary|intro)$/i.test(citizenName) &&
      !/\b(?:advisor|advisors|debate|town hall|briefing|documentary|intro|auditorium|podium|war room|council room|council table)\b/i.test(citizenTargetOnly)
    ) {
      return {
        room: "citizens",
        citizenName: titleCaseCommandValue(citizenName),
      };
    }
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
  const mergedById = new Map<string, ConversationTurn>();
  for (const turn of [...current, ...incoming]) {
    const existing = mergedById.get(turn.id);
    mergedById.set(turn.id, existing ? { ...existing, ...turn } : turn);
  }
  const deduped: ConversationTurn[] = [];
  const mergeFingerprint = (turn: ConversationTurn) =>
    [
      turn.speaker,
      turn.speaker_name ?? "",
      turn.speaker_voice ?? "",
      turn.mode,
      sanitizeRealtimeText(turn.text).toLowerCase(),
    ].join("|");
  const shouldCoalesce = (left: ConversationTurn, right: ConversationTurn) => {
    if (left.id === right.id) {
      return true;
    }
    if (mergeFingerprint(left) !== mergeFingerprint(right)) {
      return false;
    }
    const leftTime = Date.parse(left.created_at);
    const rightTime = Date.parse(right.created_at);
    if (!Number.isFinite(leftTime) || !Number.isFinite(rightTime)) {
      return true;
    }
    return Math.abs(leftTime - rightTime) <= 15_000;
  };

  for (const turn of [...mergedById.values()].sort((left, right) => Date.parse(left.created_at) - Date.parse(right.created_at))) {
    const prior = deduped.at(-1);
    if (prior && shouldCoalesce(prior, turn)) {
      deduped[deduped.length - 1] = { ...prior, ...turn };
      continue;
    }
    deduped.push(turn);
  }

  return deduped.slice(-48);
}

export function useRealtimeSession({
  simulationId,
  role,
  citizenId,
  advisorMode = "solo",
  auditoriumMode = "debate",
  autoResponse = true,
  externalPlaybackActive = false,
  councilContext,
  councilRoster,
  initialTurns,
  onSimulationSync,
  onCouncilFloorChange,
  onModeCommand,
}: UseRealtimeSessionOptions) {
  const [status, setStatus] = useState<SessionStatus>("idle");
  const [error, setError] = useState<string | null>(null);
  const [events, setEvents] = useState<ConversationTurn[]>(initialTurns);
  const eventsRef = useRef<ConversationTurn[]>(initialTurns);
  eventsRef.current = events;
  const [muted, setMuted] = useState(false);
  const [liveMode, setLiveMode] = useState<"text" | "voice">("text");
  const [presence, setPresence] = useState<ScenePresence>(EMPTY_PRESENCE);
  const [assistantSpeaking, setAssistantSpeaking] = useState(false);
  const [recordingVoiceTurn, setRecordingVoiceTurn] = useState(false);
  const [awaitingVoiceReply, setAwaitingVoiceReply] = useState(false);
  const recordingVoiceTurnRef = useRef(false);
  recordingVoiceTurnRef.current = recordingVoiceTurn;
  const councilMode = role === "advisor" && advisorMode === "council";
  const townHallHybridMode = role === "debate" && auditoriumMode === "town_hall";
  const hybridListenerMode = councilMode || townHallHybridMode;
  const councilRosterRef = useRef<ReturnType<typeof normalizeCouncilRoster>>(normalizeCouncilRoster(councilRoster));
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
  const intentionalDisconnectRef = useRef(false);
  const controlChannelFailureRef = useRef(false);
  const responseInFlightRef = useRef(false);
  const pendingToolFollowupsRef = useRef<Array<{ instructions?: string; textOnly: boolean }>>([]);
  const remoteAudioPrimedRef = useRef(false);
  const councilLeadRef = useRef<string | null>(null);
  const councilLoopGenerationRef = useRef(0);
  const councilSpeechEpochRef = useRef(0);
  const councilSpeechAbortRef = useRef<AbortController | null>(null);
  const councilSpeechAudioRef = useRef<HTMLAudioElement | null>(null);
  const councilSpeechUnlockedRef = useRef(false);
  const councilSpeechUrlsRef = useRef<string[]>([]);
  const councilSpeechStartTimerRef = useRef<number | null>(null);
  const councilSpeechBargeRef = useRef(false);
  const councilPlaybackActiveRef = useRef(false);
  const externalPlaybackActiveRef = useRef(false);
  const externalPlaybackSuppressUntilRef = useRef(0);
  const councilTurnAbortRef = useRef<AbortController | null>(null);
  const councilPrefetchAbortRef = useRef<AbortController | null>(null);
  const councilPlaybackQueueRef = useRef<Promise<void>>(Promise.resolve());
  const liveModeRef = useRef<"text" | "voice">("text");
  const pendingVoiceUpgradeRef = useRef(false);

  liveModeRef.current = liveMode;

  useEffect(() => {
    councilRosterRef.current = normalizeCouncilRoster(councilRoster);
  }, [councilRoster]);

  useEffect(() => {
    const wasActive = externalPlaybackActiveRef.current;
    externalPlaybackActiveRef.current = externalPlaybackActive;
    if (externalPlaybackActive) {
      externalPlaybackSuppressUntilRef.current = Math.max(externalPlaybackSuppressUntilRef.current, Date.now() + 700);
      return;
    }
    if (wasActive) {
      externalPlaybackSuppressUntilRef.current = Math.max(externalPlaybackSuppressUntilRef.current, Date.now() + 900);
    }
  }, [externalPlaybackActive]);

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

  async function waitForConnectedStatus(timeoutMs = 12000) {
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
      audioElement.muted = hybridListenerMode || nextMuted;
      if (pause) {
        audioElement.pause();
        continue;
      }
      if (!hybridListenerMode && !nextMuted) {
        void attemptRemoteAudioPlay(audioElement, "Remote audio could not start");
      }
    }
  });

  const stopCouncilSpeechPlayback = useEffectEvent(() => {
    councilSpeechEpochRef.current += 1;
    councilSpeechBargeRef.current = false;
    councilPlaybackActiveRef.current = false;
    councilSpeechAbortRef.current?.abort();
    councilSpeechAbortRef.current = null;
    const audioElement = councilSpeechAudioRef.current;
    if (audioElement) {
      audioElement.volume = 0.94;
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

  const primeCouncilSpeechAudio = useEffectEvent(async () => {
    if (!councilMode || councilSpeechUnlockedRef.current) {
      return;
    }
    const audioElement = councilSpeechAudioRef.current ?? document.createElement("audio");
    if (!councilSpeechAudioRef.current) {
      audioElement.autoplay = false;
      audioElement.setAttribute("playsinline", "true");
      audioElement.volume = 0.94;
      audioElement.style.display = "none";
      document.body.appendChild(audioElement);
      councilSpeechAudioRef.current = audioElement;
    }
    try {
      audioElement.src = SILENT_AUDIO_DATA_URI;
      audioElement.muted = false;
      audioElement.currentTime = 0;
      await settleAudioStart(audioElement, audioElement.play(), 900);
      audioElement.pause();
      audioElement.currentTime = 0;
      audioElement.removeAttribute("src");
      audioElement.load();
      councilSpeechUnlockedRef.current = true;
    } catch {
      audioElement.removeAttribute("src");
      audioElement.load();
    }
  });

  const abortCouncilTurnRequests = useEffectEvent(() => {
    councilTurnAbortRef.current?.abort();
    councilTurnAbortRef.current = null;
    councilPrefetchAbortRef.current?.abort();
    councilPrefetchAbortRef.current = null;
  });

  const emitPlaytestProbe = useEffectEvent((type: string, payload?: Record<string, unknown>) => {
    if (typeof window === "undefined") {
      return;
    }
    try {
      const probe = (window as Window & {
        __econSimPlaytestProbe?: {
          note?: (entry: Record<string, unknown>) => void;
        };
      }).__econSimPlaytestProbe;
      probe?.note?.({
        type,
        ...payload,
      });
    } catch {
      // Ignore probe-only diagnostics.
    }
  });

  const clearCouncilSpeechStartTimer = useEffectEvent(() => {
    if (councilSpeechStartTimerRef.current) {
      window.clearTimeout(councilSpeechStartTimerRef.current);
      councilSpeechStartTimerRef.current = null;
    }
  });

  const invalidateCouncilLoop = useEffectEvent((nextFloor?: {
    lead: string;
    owner: string;
    contrast: string[];
    reason?: string;
  } | null) => {
    nextCouncilLoopGeneration();
    responseInFlightRef.current = false;
    setAwaitingVoiceReply(false);
    councilLeadRef.current = null;
    onCouncilFloorChange?.(nextFloor ?? null);
    clearCouncilSpeechStartTimer();
    abortCouncilTurnRequests();
    stopCouncilSpeechPlayback();
    councilPlaybackQueueRef.current = Promise.resolve();
  });

  const playCouncilSpeechTurns = useEffectEvent(async (
    turns: LocalTurnInput[],
    onTurnStarted?: (turn: LocalTurnInput) => Promise<void> | void,
    onTurnSpoken?: (turn: LocalTurnInput) => Promise<void> | void,
    preparedAudioByIndex: Array<{ base64: string; format?: string | null } | null> = [],
  ): Promise<LocalTurnInput[]> => {
    if (!councilMode || mutedRef.current) {
      return [] as LocalTurnInput[];
    }
    const lines = turns
      .map((turn) => ({
        turn,
        speaker: turn.speaker_name ?? councilLeadRef.current ?? councilRosterRef.current[0]?.name ?? "Advisor",
        voice: turn.speaker_voice ?? councilVoiceForSpeaker(turn.speaker_name ?? councilLeadRef.current, councilRosterRef.current),
        text: sanitizeRealtimeText(turn.text),
      }))
      .filter((line) => line.text);
    if (lines.length === 0) {
      return [] as LocalTurnInput[];
    }

    stopCouncilSpeechPlayback();
    const epoch = councilSpeechEpochRef.current;
    councilSpeechBargeRef.current = false;
    councilPlaybackActiveRef.current = true;
    councilSpeechAbortRef.current = new AbortController();
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
      const spokenTurns: LocalTurnInput[] = [];
      let speakingMarked = false;
      for (let index = 0; index < lines.length; index += 1) {
        const currentLine = lines[index];
        if (
          mutedRef.current ||
          councilSpeechEpochRef.current !== epoch
        ) {
          return spokenTurns;
        }
        const preparedAudio = preparedAudioByIndex[index];
        if (preparedAudio?.base64) {
          const mimeSubtype = (preparedAudio.format || "mp3").toLowerCase() === "wav" ? "wav" : "mpeg";
          const binary = window.atob(preparedAudio.base64);
          const bytes = new Uint8Array(binary.length);
          for (let byteIndex = 0; byteIndex < binary.length; byteIndex += 1) {
            bytes[byteIndex] = binary.charCodeAt(byteIndex);
          }
          const blobUrl = URL.createObjectURL(new Blob([bytes], { type: `audio/${mimeSubtype}` }));
          councilSpeechUrlsRef.current.push(blobUrl);
          audioElement.src = blobUrl;
          emitPlaytestProbe("council_audio_ready", {
            source: "prepared",
            speaker: currentLine.speaker,
          });
        } else {
          const voice = currentLine.voice || councilVoiceForSpeaker(currentLine.speaker, councilRosterRef.current);
          audioElement.src = speechStreamUrl(currentLine.text, voice);
          audioElement.load();
          emitPlaytestProbe("council_audio_ready", {
            source: "speech_stream",
            speaker: currentLine.speaker,
          });
        }
        if (!speakingMarked) {
          markAssistantSpeaking();
          speakingMarked = true;
        }
        await onTurnStarted?.(currentLine.turn);
        const started = await settleAudioStart(audioElement, audioElement.play(), 1600);
        if (
          mutedRef.current ||
          councilSpeechEpochRef.current !== epoch
        ) {
          return spokenTurns;
        }
        if (!started) {
          const message = "Council speech audio did not start.";
          emitPlaytestProbe("council_audio_error", {
            source: "line_by_line",
            speaker: currentLine.speaker,
            message,
          });
          setError(message);
          return spokenTurns;
        }
        if (
          mutedRef.current ||
          councilSpeechEpochRef.current !== epoch
        ) {
          return spokenTurns;
        }
        const playbackResult = await waitForAudioCompletion(audioElement, estimatedSpeechMs(currentLine.text) + 5200);
        if (
          mutedRef.current ||
          councilSpeechEpochRef.current !== epoch
        ) {
          return spokenTurns;
        }
        if (playbackResult === "error") {
          const message = "Council speech playback failed.";
          emitPlaytestProbe("council_audio_error", {
            source: "line_by_line",
            speaker: currentLine.speaker,
            message,
          });
          setError(message);
          return spokenTurns;
        }
        spokenTurns.push(currentLine.turn);
        await onTurnSpoken?.(currentLine.turn);
      }
      return spokenTurns;
    } catch (caught) {
      const message = caught instanceof Error ? caught.message : "Council speech playback failed";
      emitPlaytestProbe("council_audio_error", {
        source: "line_by_line",
        message,
      });
      setError(message);
      return [] as LocalTurnInput[];
    } finally {
      councilSpeechBargeRef.current = false;
      councilSpeechAbortRef.current = null;
      if (councilSpeechEpochRef.current === epoch) {
        councilPlaybackActiveRef.current = false;
        if (audioElement) {
          audioElement.volume = 0.94;
        }
        releaseAssistantSpeaking();
      }
      for (const url of councilSpeechUrlsRef.current) {
        URL.revokeObjectURL(url);
      }
      councilSpeechUrlsRef.current = [];
    }
  });

  const playPreparedCouncilTurnAudio = useEffectEvent(async (
    turns: LocalTurnInput[],
    preparedAudio: { base64: string; format?: string | null },
    onTurnStarted?: (turn: LocalTurnInput) => Promise<void> | void,
    onTurnSpoken?: (turn: LocalTurnInput) => Promise<void> | void,
  ): Promise<LocalTurnInput[]> => {
    if (!councilMode || mutedRef.current || !preparedAudio.base64) {
      return [];
    }
    const spokenTurns = turns
      .map((turn) => ({
        ...turn,
        text: sanitizeRealtimeText(turn.text),
      }))
      .filter((turn) => turn.text);
    if (spokenTurns.length === 0) {
      return [];
    }

    stopCouncilSpeechPlayback();
    const epoch = councilSpeechEpochRef.current;
    councilSpeechBargeRef.current = false;
    councilPlaybackActiveRef.current = true;
    councilSpeechAbortRef.current = new AbortController();
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
      const mimeSubtype = (preparedAudio.format || "mp3").toLowerCase() === "wav" ? "wav" : "mpeg";
      const binary = window.atob(preparedAudio.base64);
      const bytes = new Uint8Array(binary.length);
      for (let byteIndex = 0; byteIndex < binary.length; byteIndex += 1) {
        bytes[byteIndex] = binary.charCodeAt(byteIndex);
      }
      const blobUrl = URL.createObjectURL(new Blob([bytes], { type: `audio/${mimeSubtype}` }));
      councilSpeechUrlsRef.current.push(blobUrl);
      audioElement.src = blobUrl;
      emitPlaytestProbe("council_audio_ready", {
        source: "prepared_full_turn",
        speaker: spokenTurns[0]?.speaker_name ?? councilLeadRef.current ?? "Advisor",
        turn_count: spokenTurns.length,
      });
      markAssistantSpeaking();
      for (const turn of spokenTurns) {
        if (mutedRef.current || councilSpeechEpochRef.current !== epoch) {
          return [];
        }
        await onTurnStarted?.(turn);
      }
      const started = await settleAudioStart(audioElement, audioElement.play(), 1400);
      if (mutedRef.current || councilSpeechEpochRef.current !== epoch) {
        return [];
      }
      if (!started) {
        const message = "Council prepared speech audio did not start.";
        emitPlaytestProbe("council_audio_error", {
          source: "prepared_full_turn",
          message,
        });
        setError(message);
        return [];
      }
      const playbackResult = await waitForAudioCompletion(
        audioElement,
        spokenTurns.reduce((total, turn) => total + estimatedSpeechMs(turn.text), 0) + 6500,
      );
      if (mutedRef.current || councilSpeechEpochRef.current !== epoch) {
        return [];
      }
      if (playbackResult === "error") {
        const message = "Council prepared speech playback failed.";
        emitPlaytestProbe("council_audio_error", {
          source: "prepared_full_turn",
          message,
        });
        setError(message);
        return [];
      }
      for (const turn of spokenTurns) {
        if (mutedRef.current || councilSpeechEpochRef.current !== epoch) {
          return [];
        }
        await onTurnSpoken?.(turn);
      }
      return spokenTurns;
    } catch (caught) {
      const message = caught instanceof Error ? caught.message : "Council full-turn playback failed";
      emitPlaytestProbe("council_audio_error", {
        source: "prepared_full_turn",
        message,
      });
      setError(message);
      return [] as LocalTurnInput[];
    } finally {
      councilSpeechBargeRef.current = false;
      councilSpeechAbortRef.current = null;
      if (councilSpeechEpochRef.current === epoch) {
        councilPlaybackActiveRef.current = false;
        if (audioElement) {
          audioElement.volume = 0.94;
        }
        releaseAssistantSpeaking();
      }
      for (const url of councilSpeechUrlsRef.current) {
        URL.revokeObjectURL(url);
      }
      councilSpeechUrlsRef.current = [];
    }
  });

  const enqueueCouncilSpeechPlayback = useEffectEvent((
    turns: LocalTurnInput[],
    loopGeneration: number,
    preparedAudio?: { base64: string; format?: string | null } | null,
    onTurnStarted?: (turn: LocalTurnInput) => Promise<void> | void,
    onTurnSpoken?: (turn: LocalTurnInput) => Promise<void> | void,
  ) => {
    const queueStart = councilPlaybackQueueRef.current.catch(() => undefined);
    const nextPlayback = queueStart.then(async (): Promise<LocalTurnInput[]> => {
      if (
        councilLoopGenerationRef.current !== loopGeneration ||
        mutedRef.current ||
        !councilMode
      ) {
        return [];
      }
      emitPlaytestProbe("council_playback_started", {
        turn_count: turns.length,
        prepared: Boolean(preparedAudio?.base64),
      });
      if (preparedAudio?.base64 && turns.length === 1) {
        return await playPreparedCouncilTurnAudio(turns, preparedAudio, onTurnStarted, onTurnSpoken);
      }
      return await playCouncilSpeechTurns(
        turns,
        onTurnStarted,
        onTurnSpoken,
        [],
      );
    }).catch((caught) => {
      const message = caught instanceof Error ? caught.message : "Queued council playback failed";
      emitPlaytestProbe("council_audio_error", {
        source: "queued_playback",
        message,
      });
      return [] as LocalTurnInput[];
    });
    councilPlaybackQueueRef.current = nextPlayback.then(() => undefined).catch(() => undefined);
    return nextPlayback;
  });

  const awaitCouncilPlaybackSettled = useEffectEvent(async (
    playbackPromise: Promise<LocalTurnInput[]> | null,
    turns: LocalTurnInput[],
  ) => {
    if (!playbackPromise) {
      return [] as LocalTurnInput[];
    }
    const estimatedMs = Math.max(
      6500,
      Math.min(
        30000,
        turns.reduce((total, turn) => total + estimatedSpeechMs(turn.text), 0) + 8000,
      ),
    );
    let timedOut = false;
    const spokenTurns = await Promise.race([
      playbackPromise.then((result) => result),
      new Promise<void>((resolve) => {
        window.setTimeout(() => {
          timedOut = true;
          resolve();
        }, estimatedMs);
      }),
    ]);
    if (!timedOut) {
      return Array.isArray(spokenTurns) ? spokenTurns : [] as LocalTurnInput[];
    }
    emitPlaytestProbe("council_audio_timeout", {
      turn_count: turns.length,
      estimated_ms: estimatedMs,
    });
    stopCouncilSpeechPlayback();
    emitPlaytestProbe("council_audio_assumed_complete", {
      turn_count: turns.length,
      estimated_ms: estimatedMs,
    });
    return turns;
  });

  const appendLocalTurn = useEffectEvent((turn: LocalTurnInput) => {
    const trimmed = sanitizeRealtimeText(turn.text);
    if (!trimmed) {
      return;
    }
    setEvents((current) => {
      const next = [
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
      ];
      eventsRef.current = next;
      return next;
    });
  });

  const clearPendingRealtimeTransportRefs = useEffectEvent(() => {
    pendingConnectionRef.current = null;
    pendingDataChannelRef.current = null;
    pendingStreamRef.current = null;
    pendingRemoteAudioRef.current = null;
    pendingSyntheticCleanupRef.current = null;
  });

  const flagControlChannelFailure = useEffectEvent((message: string) => {
    if (controlChannelFailureRef.current) {
      return;
    }
    controlChannelFailureRef.current = true;
    setError(message);
    disconnect();
  });

  const attemptRemoteAudioPlay = useEffectEvent(async (audioElement: HTMLAudioElement, context: string) => {
    try {
      await audioElement.play();
      return true;
    } catch (caught) {
      const detail = caught instanceof Error ? caught.message : "Audio playback was blocked";
      mutedRef.current = true;
      setMuted(true);
      syncInputTrackState(true);
      updateVoiceTurnDetection(true);
      syncRemoteAudioState(true, true);
      setError(`${context}. ${detail}. Click Speak again to resume audio.`);
      releaseAssistantSpeaking();
      setAwaitingVoiceReply(false);
      return false;
    }
  });

  const primeRemoteAudioPlayback = useEffectEvent(async () => {
    if (hybridListenerMode || remoteAudioPrimedRef.current) {
      return;
    }
    const audioElement = document.createElement("audio");
    audioElement.autoplay = false;
    audioElement.setAttribute("playsinline", "true");
    audioElement.style.display = "none";
    document.body.appendChild(audioElement);
    try {
      audioElement.src = SILENT_AUDIO_DATA_URI;
      audioElement.muted = false;
      audioElement.currentTime = 0;
      await settleAudioStart(audioElement, audioElement.play(), 900);
      audioElement.pause();
      audioElement.currentTime = 0;
      audioElement.removeAttribute("src");
      audioElement.load();
      remoteAudioPrimedRef.current = true;
    } catch {
      audioElement.removeAttribute("src");
      audioElement.load();
    } finally {
      audioElement.remove();
    }
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
    const councilLines = splitCouncilLines(transcriptText, councilRosterRef.current)
      .map((line) => sanitizeRealtimeText(line.text))
      .filter(Boolean);
    const chosenSpeaker = councilLeadRef.current ?? councilRosterRef.current[0]?.name ?? "Advisor";
    const spokenText = councilLines.length > 0 ? councilLines.join(" ") : transcriptText;
    return [{
      speaker: "assistant",
      speaker_name: chosenSpeaker,
      speaker_voice: councilVoiceForSpeaker(chosenSpeaker, councilRosterRef.current),
      text: spokenText,
      mode,
    }];
  });

  const sendEvent = useEffectEvent((payload: Record<string, unknown>, options?: { tolerateClosed?: boolean }) => {
    const channel = dataChannelRef.current;
    if (channel?.readyState === "open") {
      channel.send(JSON.stringify(payload));
      return true;
    }
    if (!options?.tolerateClosed && (statusRef.current === "connected" || statusRef.current === "connecting")) {
      flagControlChannelFailure("The live voice control channel dropped");
    }
    return false;
  });

  const injectContextTurn = useEffectEvent(async (text: string) => {
    if (hybridListenerMode) {
      return false;
    }
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
    if (hybridListenerMode) {
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
    const wantsVoiceResponse = requestedMode === "voice";
    if (!trimmed) {
      emitPlaytestProbe("council_turn_skipped", { reason: "empty" });
      return;
    }
    if (!simulationId) {
      emitPlaytestProbe("council_turn_skipped", { reason: "no_simulation" });
      return;
    }
    if (responseInFlightRef.current) {
      emitPlaytestProbe("council_turn_skipped", { reason: "response_in_flight" });
      return;
    }
    emitPlaytestProbe("council_turn_started", { mode: requestedMode, text: trimmed });
    const loopGeneration = nextCouncilLoopGeneration();
    const userTurn: LocalTurnInput = { speaker: "user", text: trimmed, mode: requestedMode };
    let workingLocalTurns = [...councilProvisionalTurns(eventsRef.current), userTurn];
    let workingBoardNotes = [...(councilContext?.policyNotes ?? [])];
    appendLocalTurn(userTurn);
    onCouncilFloorChange?.({
      lead: "player",
      owner: "player",
      contrast: [],
    });
    setError(null);
    responseInFlightRef.current = true;
    if (wantsVoiceResponse) {
      setAwaitingVoiceReply(true);
      recordingVoiceTurnRef.current = false;
      setRecordingVoiceTurn(false);
    }
    try {
      const requestCouncilTurn = (
        requestText: string,
        continueDialogue: boolean,
        kind: "live" | "prefetch",
        preferredSpeaker: string,
        avoidSpeaker: string,
        localTurns: LocalTurnInput[],
        boardNotes: string[],
      ) => {
        const normalizedRequestText = sanitizeRealtimeText(requestText);
        const provisionalTurns = continueDialogue
          ? localTurns
          : localTurns.filter((turn, index) => !(
            index === localTurns.length - 1 &&
            turn.speaker === "user" &&
            sanitizeRealtimeText(turn.text) === normalizedRequestText
          ));
        emitPlaytestProbe("council_turn_request", {
          kind,
          continue_dialogue: continueDialogue,
          text: requestText,
          provisional_turn_count: provisionalTurns.length,
          board_note_count: boardNotes.length,
        });
        const controller = new AbortController();
        controller.signal.addEventListener("abort", () => {
          emitPlaytestProbe("council_turn_request_aborted", {
            kind,
            continue_dialogue: continueDialogue,
          });
        }, { once: true });
        if (kind === "live") {
          councilTurnAbortRef.current?.abort();
          councilTurnAbortRef.current = controller;
        } else {
          councilPrefetchAbortRef.current?.abort();
          councilPrefetchAbortRef.current = controller;
        }
        const compactTurns = continueDialogue
          ? provisionalTurns.slice(-2)
          : provisionalTurns.filter((turn) => turn.speaker === "user").slice(-1);
        const responseMode = kind === "prefetch" ? "text" : requestedMode;
        return generateCouncilTurn(
          simulationId,
          requestText,
          responseMode,
          continueDialogue,
          preferredSpeaker,
          avoidSpeaker,
          provisionalTurns,
          boardNotes,
          controller.signal,
        ).catch(async (caught) => {
          const message = caught instanceof Error ? caught.message : String(caught);
          const aborted = controller.signal.aborted || (caught instanceof DOMException && caught.name === "AbortError");
          if (
            aborted ||
            compactTurns.length === 0 ||
            compactTurns.length >= localTurns.length ||
            !/failed to fetch|networkerror|load failed/i.test(message.toLowerCase())
          ) {
            throw caught;
          }
          emitPlaytestProbe("council_turn_retry_compact", {
            kind,
            continue_dialogue: continueDialogue,
            original_turn_count: provisionalTurns.length,
            compact_turn_count: compactTurns.length,
          });
          return await generateCouncilTurn(
            simulationId,
            requestText,
            responseMode,
            continueDialogue,
            preferredSpeaker,
            avoidSpeaker,
            compactTurns,
            [],
            controller.signal,
          );
        }).finally(() => {
          if (kind === "live" && councilTurnAbortRef.current === controller) {
            councilTurnAbortRef.current = null;
          }
          if (kind === "prefetch" && councilPrefetchAbortRef.current === controller) {
            councilPrefetchAbortRef.current = null;
          }
        });
      };
      let continueDialogue = false;
      let nextText = trimmed;
      let preferredSpeaker = "";
      let avoidSpeaker = "";
      const loopStartedAt = Date.now();
      let lastAssistantSignature = "";
      let repeatedAssistantSignatureCount = 0;
      let continuationBeatCount = 0;
      while (true) {
        if (councilLoopGenerationRef.current !== loopGeneration) {
          return;
        }
        responseInFlightRef.current = true;
        const response = await requestCouncilTurn(
          nextText,
          continueDialogue,
          "live",
          preferredSpeaker,
          avoidSpeaker,
          workingLocalTurns,
          workingBoardNotes,
        );
        if (councilLoopGenerationRef.current !== loopGeneration) {
          emitPlaytestProbe("council_turn_cancelled", { stage: "after_response" });
          return;
        }
        if (response.simulation?.simulation_id) {
          onSimulationSync?.(response.simulation);
        }
        const shouldYieldToPlayer =
          response.next_speaker === "player" ||
          response.next_speaker === "President" ||
          response.yield_after_turn;
        onCouncilFloorChange?.({
          lead: response.lead,
          owner: shouldYieldToPlayer ? "player" : (response.next_speaker ?? response.lead),
          contrast: response.contrast,
          reason: response.reason ?? undefined,
        });
        councilLeadRef.current = response.lead;
        const rawAssistantTurns = response.turns
          .map((turn) => ({
            speaker: turn.speaker,
            speaker_name: turn.speaker_name,
            speaker_voice: turn.speaker_voice,
            text: turn.text,
            mode: turn.mode,
          }))
          .filter((turn) => sanitizeRealtimeText(turn.text));
        const assistantTurns = rawAssistantTurns.length > 0
          ? rawAssistantTurns.map((turn) => ({
              speaker: "assistant" as const,
              speaker_name: turn.speaker_name ?? response.lead ?? response.next_speaker ?? "Advisor",
              speaker_voice: turn.speaker_voice ?? councilVoiceForSpeaker(turn.speaker_name ?? response.lead ?? response.next_speaker ?? "Advisor", councilRosterRef.current),
              text: turn.text,
              mode: turn.mode ?? "text",
            }))
          : [];
        emitPlaytestProbe("council_turn_response", {
          next_speaker: response.next_speaker,
          lead: response.lead,
          yield_after_turn: response.yield_after_turn,
          assistant_turn_count: assistantTurns.length,
          board_note_count: response.board_notes.length,
        });
        const assistantSignature = councilTurnSignature(assistantTurns);
        if (assistantSignature && assistantSignature === lastAssistantSignature) {
          repeatedAssistantSignatureCount += 1;
        } else {
          repeatedAssistantSignatureCount = 0;
          lastAssistantSignature = assistantSignature;
        }
        if (response.board_notes.length > 0) {
          workingBoardNotes = [...response.board_notes];
        }
        const nextWorkingLocalTurns = [...workingLocalTurns, ...assistantTurns].slice(-COUNCIL_CONTEXT_TURN_LIMIT);
        workingLocalTurns = nextWorkingLocalTurns;
        if (assistantTurns.length === 0) {
          responseInFlightRef.current = false;
          setAwaitingVoiceReply(false);
          return;
        }
        let playbackPromise: Promise<LocalTurnInput[]> | null = null;
        if (wantsVoiceResponse && !mutedRef.current) {
          playbackPromise = enqueueCouncilSpeechPlayback(
            assistantTurns,
            loopGeneration,
            response.audio_base64
              ? { base64: response.audio_base64, format: response.audio_format }
              : null,
            async (turn) => {
              appendLocalTurn(turn);
            },
          );
        } else {
          assistantTurns.forEach((turn) => appendLocalTurn(turn));
        }
        responseInFlightRef.current = false;
        let spokenTurns: LocalTurnInput[] = assistantTurns;
        if (playbackPromise) {
          spokenTurns = await awaitCouncilPlaybackSettled(playbackPromise, assistantTurns);
          if (councilLoopGenerationRef.current !== loopGeneration) {
            emitPlaytestProbe("council_turn_cancelled", { stage: "after_playback" });
            return;
          }
          if (spokenTurns.length < assistantTurns.length) {
            emitPlaytestProbe("council_turn_stopped_after_audio_failure", {
              spoken_turn_count: spokenTurns.length,
              assistant_turn_count: assistantTurns.length,
            });
            return;
          }
        }
        setAwaitingVoiceReply(false);
        const canContinueDialogue =
          !shouldYieldToPlayer &&
          assistantTurns.length > 0 &&
          Date.now() - loopStartedAt < COUNCIL_MAX_CONTINUATION_MS &&
          repeatedAssistantSignatureCount < 2 &&
          continuationBeatCount < COUNCIL_MAX_CONTINUATION_BEATS;
        if (shouldYieldToPlayer) {
          onCouncilFloorChange?.({
            lead: response.lead,
            owner: "player",
            contrast: response.contrast,
            reason: response.reason ?? undefined,
          });
        }

        if (shouldYieldToPlayer) {
          emitPlaytestProbe("council_continuation_stopped", {
            reason: "yield_to_player",
            lead: response.lead,
          });
          return;
        }
        if (Date.now() - loopStartedAt >= COUNCIL_MAX_CONTINUATION_MS) {
          emitPlaytestProbe("council_continuation_stopped", {
            reason: "max_duration",
            elapsed_ms: Date.now() - loopStartedAt,
          });
          return;
        }
        if (wantsVoiceResponse && (mutedRef.current || liveModeRef.current !== "voice" || statusRef.current !== "connected")) {
          emitPlaytestProbe("council_continuation_stopped", {
            reason: "voice_unavailable",
            muted: mutedRef.current,
            live_mode: liveModeRef.current,
            status: statusRef.current,
          });
          return;
        }
        if (repeatedAssistantSignatureCount >= 2) {
          emitPlaytestProbe("council_continuation_stopped", {
            reason: "repeated_signature",
            repeatedAssistantSignatureCount,
          });
          return;
        }
        if (continuationBeatCount >= COUNCIL_MAX_CONTINUATION_BEATS) {
          emitPlaytestProbe("council_continuation_stopped", {
            reason: "max_beats",
            continuationBeatCount,
          });
          return;
        }
        if (!canContinueDialogue) {
          emitPlaytestProbe("council_continuation_stopped", {
            reason: "not_continuable",
          });
          return;
        }
        continuationBeatCount += 1;
        continueDialogue = true;
        nextText = "";
        preferredSpeaker = shouldYieldToPlayer ? "" : (response.next_speaker ?? "");
        avoidSpeaker = "";
        emitPlaytestProbe("council_continuation_next", {
          beat: continuationBeatCount,
          preferred_speaker: preferredSpeaker,
          avoid_speaker: avoidSpeaker,
        });
        if (wantsVoiceResponse) {
          setAwaitingVoiceReply(true);
        }
      }
    } catch (caught) {
      if ((caught instanceof DOMException && caught.name === "AbortError") || (caught instanceof Error && caught.name === "AbortError")) {
        emitPlaytestProbe("council_turn_aborted");
        return;
      }
      const message = caught instanceof Error ? caught.message : "Council turn failed";
      emitPlaytestProbe("council_turn_error", { message });
      setError(message);
    } finally {
      if (councilLoopGenerationRef.current === loopGeneration) {
        responseInFlightRef.current = false;
        setAwaitingVoiceReply(false);
      }
    }
  });

  const updateVoiceTurnDetection = useEffectEvent((_paused: boolean) => {
    // Keep voice pause/resume local. Toggling the microphone track and pausing
    // playback gives us the UX we want here without relying on brittle
    // session.update turn-detection mutations across Realtime API revisions.
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
      dataChannel.onopen = null;
      dataChannel.onclose = null;
      dataChannel.onerror = null;
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
    recordingVoiceTurnRef.current = false;
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
    sendEvent({ type: "input_audio_buffer.clear" }, { tolerateClosed: true });
    sendEvent({ type: "response.cancel" }, { tolerateClosed: true });
    sendEvent({ type: "output_audio_buffer.clear" }, { tolerateClosed: true });
    releaseAssistantSpeaking();
    recordingVoiceTurnRef.current = false;
    setRecordingVoiceTurn(false);
    setAwaitingVoiceReply(false);
  });

  const pauseRealtime = useEffectEvent(() => {
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
    sendEvent({ type: "input_audio_buffer.clear" }, { tolerateClosed: true });
    sendEvent({ type: "response.cancel" }, { tolerateClosed: true });
    sendEvent({ type: "output_audio_buffer.clear" }, { tolerateClosed: true });
    releaseAssistantSpeaking();
    recordingVoiceTurnRef.current = false;
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
    recordingVoiceTurnRef.current = false;
    setRecordingVoiceTurn(false);
    setAwaitingVoiceReply(false);
    releaseAssistantSpeaking();
    syncInputTrackState(false);
    updateVoiceTurnDetection(false);
    syncRemoteAudioState(false, false);
    const audio = remoteAudioRef.current;
    if (audio) {
      void attemptRemoteAudioPlay(audio, "Remote audio could not resume");
    }
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

  const canAcceptCompletedTranscript = useEffectEvent(() => {
    if (liveModeRef.current !== "voice" || mutedRef.current || statusRef.current !== "connected") {
      return false;
    }
    activeInputEpochRef.current = voiceEpochRef.current;
    awaitFreshInputRef.current = false;
    return true;
  });

  const handleRealtimeEvent = useEffectEvent(async (payload: Record<string, unknown>) => {
    const eventType = String(payload.type ?? "");
    const payloadResponseId = String(payload.response_id ?? "");
    if (hybridListenerMode) {
      if (eventType === "input_audio_buffer.speech_started") {
        if (liveModeRef.current !== "voice" || mutedRef.current) {
          return;
        }
        clearCouncilSpeechStartTimer();
        const initialBusyPlayback =
          assistantSpeakingRef.current || audioOutputPlayingRef.current || responseInFlightRef.current || councilPlaybackActiveRef.current;
        const confirmMs = initialBusyPlayback
          ? COUNCIL_BARGE_IN_CONFIRM_MS
          : COUNCIL_IDLE_SPEECH_START_CONFIRM_MS;
        councilSpeechStartTimerRef.current = window.setTimeout(() => {
          councilSpeechStartTimerRef.current = null;
          if (liveModeRef.current !== "voice" || mutedRef.current) {
            return;
          }
          const busyPlayback =
            assistantSpeakingRef.current || audioOutputPlayingRef.current || responseInFlightRef.current || councilPlaybackActiveRef.current;
          if (busyPlayback) {
            councilSpeechBargeRef.current = true;
            if (councilMode) {
              invalidateCouncilLoop({
                lead: "player",
                owner: "player",
                contrast: [],
              });
            } else {
              const audio = councilSpeechAudioRef.current;
              if (audio) {
                audio.volume = 0.18;
              }
            }
          }
          recordingVoiceTurnRef.current = true;
          setRecordingVoiceTurn(true);
          setAwaitingVoiceReply(false);
        }, confirmMs);
        return;
      }
      if (eventType === "input_audio_buffer.speech_stopped") {
        clearCouncilSpeechStartTimer();
        if (liveModeRef.current !== "voice" || mutedRef.current) {
          return;
        }
        if (councilMode) {
          const audio = councilSpeechAudioRef.current;
          if (audio) {
            audio.volume = 0.94;
          }
        }
        if (!recordingVoiceTurnRef.current) {
          return;
        }
        recordingVoiceTurnRef.current = false;
        setRecordingVoiceTurn(false);
        setAwaitingVoiceReply(false);
        return;
      }
      if (eventType === "conversation.interrupted") {
        pendingToolFollowupsRef.current = [];
        clearToolFollowupFlushTimer();
        clearCouncilSpeechStartTimer();
        releaseAssistantSpeaking();
        return;
      }
      if (eventType === "conversation.item.input_audio_transcription.completed") {
        clearCouncilSpeechStartTimer();
        if (!canAcceptCompletedTranscript()) {
          return;
        }
        const transcript = String(payload.transcript ?? "").trim();
        if (!transcript) {
          councilSpeechBargeRef.current = false;
          recordingVoiceTurnRef.current = false;
          setRecordingVoiceTurn(false);
          return;
        }
        if (!isMeaningfulHybridTranscript(transcript)) {
          councilSpeechBargeRef.current = false;
          recordingVoiceTurnRef.current = false;
          setRecordingVoiceTurn(false);
          return;
        }
        if (
          (assistantSpeakingRef.current ||
            audioOutputPlayingRef.current ||
            councilSpeechBargeRef.current ||
            externalPlaybackActiveRef.current ||
            Date.now() < externalPlaybackSuppressUntilRef.current) &&
          looksLikeHybridPlaybackEcho(transcript, eventsRef.current)
        ) {
          councilSpeechBargeRef.current = false;
          recordingVoiceTurnRef.current = false;
          setRecordingVoiceTurn(false);
          return;
        }
        activeInputEpochRef.current = voiceEpochRef.current;
        awaitFreshInputRef.current = false;
        dropPendingVoiceResponsesRef.current = false;
        if (councilMode && (assistantSpeakingRef.current || audioOutputPlayingRef.current || councilSpeechBargeRef.current)) {
          stopCouncilSpeechPlayback();
        }
        councilSpeechBargeRef.current = false;
        if (councilMode) {
          invalidateCouncilLoop({
            lead: "player",
            owner: "player",
            contrast: [],
          });
        }
        dropPendingVoiceResponsesRef.current = false;
        if (await handleModeCommand(transcript)) {
          emitPlaytestProbe("hybrid_transcript_routed", { route: "mode_command", transcript });
          return;
        }
        if (councilMode) {
          emitPlaytestProbe("hybrid_transcript_routed", { route: "council_turn", transcript });
          void runCouncilTurn(transcript, "voice");
          return;
        }
        appendLocalTurn({ speaker: "user", text: transcript, mode: "voice" });
        void persistTurns([{ speaker: "user", text: transcript, mode: "voice" }]).catch((caught) => {
          const message = caught instanceof Error ? caught.message : "Failed to sync voice turn";
          setError(message);
        });
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
      recordingVoiceTurnRef.current = true;
      setRecordingVoiceTurn(true);
      setAwaitingVoiceReply(false);
      return;
    }
    if (eventType === "input_audio_buffer.speech_stopped") {
      if (liveModeRef.current !== "voice" || mutedRef.current) {
        return;
      }
      recordingVoiceTurnRef.current = false;
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
      if (!canAcceptCompletedTranscript()) {
        return;
      }
      const transcript = String(payload.transcript ?? "").trim();
      if (!transcript) {
        return;
      }
      if (await handleModeCommand(transcript)) {
        emitPlaytestProbe("voice_transcript_routed", { route: "mode_command", transcript });
        return;
      }
      if (councilMode) {
        dropPendingVoiceResponsesRef.current = false;
        emitPlaytestProbe("voice_transcript_routed", { route: "council_turn", transcript });
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
      const message = String((payload.error as Record<string, unknown> | undefined)?.message ?? "Realtime session failed");
      if (isExpectedRealtimeTeardownMessage(message)) {
        emitPlaytestProbe("realtime_expected_teardown", { message });
        setAwaitingVoiceReply(false);
        recordingVoiceTurnRef.current = false;
        setRecordingVoiceTurn(false);
        releaseAssistantSpeaking();
        return;
      }
      invalidateCouncilLoop();
      setAwaitingVoiceReply(false);
      recordingVoiceTurnRef.current = false;
      setRecordingVoiceTurn(false);
      setError(message);
      setStatus("error");
    }
  });

  const disconnect = useEffectEvent(() => {
    intentionalDisconnectRef.current = true;
    controlChannelFailureRef.current = false;
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
    recordingVoiceTurnRef.current = false;
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
    pendingVoiceUpgradeRef.current = false;
  });

  const connectInternal = useEffectEvent(async (withAudio: boolean, options?: { silentOpen?: boolean }) => {
    if (!simulationId) {
      throw new Error("simulation is not ready");
    }
    if (statusRef.current === "connecting") {
      if (withAudio) {
        pendingVoiceUpgradeRef.current = true;
      }
      return;
    }
    if (
      statusRef.current === "connected" &&
      ((withAudio && liveModeRef.current === "voice") || (!withAudio && liveModeRef.current === "text"))
    ) {
      return;
    }
    if (statusRef.current === "connected") {
      disconnect();
    }
    if (connectionRef.current || pendingConnectionRef.current || dataChannelRef.current || pendingDataChannelRef.current || remoteAudioRef.current || pendingRemoteAudioRef.current) {
      intentionalDisconnectRef.current = true;
      disposeAllRealtimeTransport();
    }
    const generation = connectGenerationRef.current + 1;
    connectGenerationRef.current = generation;
    controlChannelFailureRef.current = false;
    intentionalDisconnectRef.current = false;
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
    pendingVoiceUpgradeRef.current = false;
    setStatus("connecting");
    liveModeRef.current = withAudio ? "voice" : "text";
    setLiveMode(withAudio ? "voice" : "text");
    setError(null);
    audioOutputPlayingRef.current = false;
    assistantSpeakingRef.current = false;
    nextVoiceEpoch();
    awaitFreshInputRef.current = withAudio;
    setAssistantSpeaking(false);
    recordingVoiceTurnRef.current = false;
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
      audioElement.muted = hybridListenerMode || !withAudio;
      audioElement.style.display = "none";
      document.body.appendChild(audioElement);
      peerConnection.ontrack = (event) => {
        if (!generationMatches()) {
          return;
        }
        audioElement.srcObject = event.streams[0];
        void attemptRemoteAudioPlay(audioElement, "Remote audio could not start");
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
        localStream = await getUserMediaWithTimeout({
          audio: {
            echoCancellation: true,
            noiseSuppression: true,
            autoGainControl: false,
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
      dataChannel.onopen = () => {
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
      };
      dataChannel.onclose = () => {
        if (!generationMatches() || intentionalDisconnectRef.current) {
          return;
        }
        flagControlChannelFailure("The live voice control channel closed");
      };
      dataChannel.onerror = () => {
        if (!generationMatches() || intentionalDisconnectRef.current) {
          return;
        }
        flagControlChannelFailure("The live voice control channel failed");
      };
      dataChannel.onmessage = (event) => {
        if (!generationMatches()) {
          return;
        }
        void handleRealtimeEvent(JSON.parse(event.data) as Record<string, unknown>);
      };

      const offer = await peerConnection.createOffer();
      await peerConnection.setLocalDescription(offer);

      const response = await fetchWithTimeout("https://api.openai.com/v1/realtime/calls", {
        method: "POST",
        body: offer.sdp,
        headers: {
          Authorization: `Bearer ${session.client_secret}`,
          "Content-Type": "application/sdp",
        },
      }, 15000);
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
          const timeout = window.setTimeout(() => reject(new Error("Realtime data channel did not open in time")), 12000);
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
      const needsFullyConnectedPeer = !hybridListenerMode;
      if (needsFullyConnectedPeer && peerConnection.connectionState !== "connected") {
        await new Promise<void>((resolve, reject) => {
          const timeout = window.setTimeout(() => reject(new Error("Realtime peer connection did not finish connecting in time")), 12000);
          const handleStateChange = () => {
            if (peerConnection.connectionState === "connected") {
              window.clearTimeout(timeout);
              peerConnection.removeEventListener("connectionstatechange", handleStateChange);
              resolve();
              return;
            }
            if (peerConnection.connectionState === "failed" || peerConnection.connectionState === "closed") {
              window.clearTimeout(timeout);
              peerConnection.removeEventListener("connectionstatechange", handleStateChange);
              reject(new Error("Realtime peer connection failed"));
            }
          };
          peerConnection.addEventListener("connectionstatechange", handleStateChange);
          handleStateChange();
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
      recordingVoiceTurnRef.current = false;
      setRecordingVoiceTurn(false);
      setAwaitingVoiceReply(false);
      syncInputTrackState(false);
      if (!withAudio && pendingVoiceUpgradeRef.current && generationMatches()) {
        pendingVoiceUpgradeRef.current = false;
        window.setTimeout(() => {
          void connectInternal(true).catch(() => undefined);
        }, 0);
      }
    } catch (caught) {
      connectionRequestedRef.current = false;
      disposeRealtimeTransport(localPeerConnection, localDataChannel, localStream, localAudioElement, localSyntheticCleanup);
      clearPendingRealtimeTransportRefs();
      const message = caught instanceof Error ? caught.message : "Failed to connect realtime session";
      setError(message);
      liveModeRef.current = "text";
      setLiveMode("text");
      mutedRef.current = false;
      setMuted(false);
      recordingVoiceTurnRef.current = false;
      setRecordingVoiceTurn(false);
      setAwaitingVoiceReply(false);
      releaseAssistantSpeaking();
      setStatus("error");
      pendingVoiceUpgradeRef.current = false;
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
    if (townHallHybridMode) {
      if (await handleModeCommand(trimmed)) {
        return;
      }
      const requestedMode =
        statusRef.current === "connected" && liveModeRef.current === "voice"
          ? "voice"
          : "text";
      appendLocalTurn({ speaker: "user", text: trimmed, mode: requestedMode });
      void persistTurns([{ speaker: "user", text: trimmed, mode: requestedMode }]).catch((caught) => {
        const message = caught instanceof Error ? caught.message : "Failed to sync text turn";
        setError(message);
      });
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
      void primeCouncilSpeechAudio();
      void primeRemoteAudioPlayback();
      if (statusRef.current === "error") {
        disconnect();
      }
      if (statusRef.current === "connected" && liveModeRef.current === "voice") {
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
    if (statusRef.current !== "connected" || liveModeRef.current !== "voice") {
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
      pauseRealtime();
    } catch (caught) {
      const message = caught instanceof Error ? caught.message : "Voice connection failed";
      setError(message);
    } finally {
      voiceToggleInFlightRef.current = false;
    }
  }

  async function toggleVoiceCapture() {
    if (
      statusRef.current === "connecting" ||
      (connectionRequestedRef.current && statusRef.current !== "connected")
    ) {
      disconnect();
      return;
    }
    if (voiceToggleInFlightRef.current) {
      return;
    }
    if (statusRef.current === "connected" && liveModeRef.current === "voice") {
      await toggleMute();
      return;
    }
    if (statusRef.current === "error") {
      disconnect();
    }
    await enableVoice();
  }

  const startOrToggleVoice = useEffectEvent(async () => {
    if (
      statusRef.current === "connecting" ||
      (connectionRequestedRef.current && statusRef.current !== "connected")
    ) {
      disconnect();
      return;
    }
    if (statusRef.current === "connected" && liveModeRef.current === "voice") {
      await toggleMute();
      return;
    }
    await enableVoice();
  });

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
    eventsRef.current = initialTurns;
    setEvents(initialTurns);
    setError(null);
  }, [sessionScopeKey]);

  useEffect(() => {
    setEvents((current) => {
      const next = mergeConversationTurns(current, initialTurns);
      eventsRef.current = next;
      return next;
    });
  }, [initialTurns]);

  useEffect(() => () => disconnect(), []);

  useEffect(() => {
    syncInputTrackState(muted);
  }, [muted, syncInputTrackState]);

  useEffect(() => {
    if (!remoteAudioRef.current) {
      return;
    }
    remoteAudioRef.current.muted = hybridListenerMode || liveMode !== "voice" || muted;
  }, [hybridListenerMode, liveMode, muted]);

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
    startOrToggleVoice,
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
