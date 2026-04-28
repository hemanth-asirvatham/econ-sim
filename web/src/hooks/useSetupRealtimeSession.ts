import { useEffect, useEffectEvent, useRef, useState } from "react";
import { createSetupRealtimeSession, sendSetupPrompt } from "../lib/api";
import { REALTIME_TURN_DETECTION } from "../lib/realtimeConfig";
import type { ScenePresence, SessionStatus, SetupSessionState } from "../types";

interface UseSetupRealtimeSessionOptions {
  session: SetupSessionState | null;
  onSessionSync: (session: SetupSessionState) => void;
  onAutoLaunch?: (session: SetupSessionState, transcript: string) => void;
}

const EMPTY_PRESENCE: ScenePresence = {
  status: "idle",
  liveMode: "text",
  muted: false,
  playerActivity: "idle",
  counterpartActivity: "idle",
  voicePhase: "idle",
};

function setupLaunchIntent(prompt: string) {
  const normalized = prompt.trim().toLowerCase().replace(/[.!?]+$/g, "").replace(/\s+/g, " ");
  const directCommand =
    "(?:go|go ahead|go for it|do it|kick it off|get going|start|start it|start this|start from here|start the run|start the sim|start simulation|start the simulation|launch|launch it|launch this|launch the run|launch the sim|launch simulation|launch the simulation|run it|run this|begin|begin it|let's begin|lets begin)";
  if (
    new RegExp(
      `^(?:ok(?:ay)?|yeah|yes|yep|sure|alright|all right|cool|great|please|now|then|so)[, ]+${directCommand}(?:[, ]*(?:please|now))?$`,
    ).test(normalized) ||
    new RegExp(`^${directCommand}(?:[, ]*(?:please|now))?$`).test(normalized) ||
    new RegExp(`^(?:i['’]?m ready|im ready|ready to go|broad setup is fine|use the default(?: broad)?(?: u\\.?s\\.?)?(?: run| setup)?)(?:[, ]+${directCommand})?$`).test(
      normalized,
    ) ||
    new RegExp(`^(?:let's|lets|we should|i think we should|i guess we should)\\s+(?:launch|start|begin|run)\\s+(?:it|this|the run|the sim|the simulation)$`).test(normalized)
  ) {
    return true;
  }
  return new RegExp(`^(?:that(?:'| i)?s good|sounds good|looks good|default looks good|default is fine),?\\s+${directCommand}$`).test(normalized);
}

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

export function useSetupRealtimeSession({
  session,
  onSessionSync,
  onAutoLaunch,
}: UseSetupRealtimeSessionOptions) {
  const [status, setStatus] = useState<SessionStatus>("idle");
  const [error, setError] = useState<string | null>(null);
  const [muted, setMuted] = useState(false);
  const [liveMode, setLiveMode] = useState<"text" | "voice">("text");
  const [presence, setPresence] = useState<ScenePresence>(EMPTY_PRESENCE);
  const [assistantSpeaking, setAssistantSpeaking] = useState(false);
  const [recordingVoiceTurn, setRecordingVoiceTurn] = useState(false);
  const [awaitingVoiceReply, setAwaitingVoiceReply] = useState(false);
  const statusRef = useRef<SessionStatus>("idle");
  statusRef.current = status;
  const liveModeRef = useRef<"text" | "voice">("text");
  liveModeRef.current = liveMode;
  const [events, setEvents] = useState<Array<{ id: string; speaker: "user" | "assistant" | "system"; text: string; mode: "text" | "voice" | "system"; created_at: string }>>([]);
  const eventsRef = useRef(events);
  const sessionRef = useRef<SetupSessionState | null>(session);
  sessionRef.current = session;
  const sessionIdRef = useRef<string | null>(session?.session_id ?? null);
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
  const responseTextRef = useRef<Record<string, string>>({});
  const responseAudioTranscriptRef = useRef<Record<string, string>>({});
  const responseEpochRef = useRef<Record<string, number>>({});
  const completedTextResponseIdsRef = useRef<Set<string>>(new Set());
  const ignoredResponseIdsRef = useRef<Set<string>>(new Set());
  const dropPendingVoiceResponsesRef = useRef(false);
  const voiceEpochRef = useRef(0);
  const activeInputEpochRef = useRef<number | null>(null);
  const awaitFreshInputRef = useRef(false);
  const mutedRef = useRef(false);
  const pendingSyncPromiseRef = useRef<Promise<SetupSessionState> | null>(null);
  const voiceToggleInFlightRef = useRef(false);
  const connectionRequestedRef = useRef(false);
  const controlChannelFailureRef = useRef(false);

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

  const appendLocalTurn = useEffectEvent((speaker: "user" | "assistant" | "system", text: string, mode: "text" | "voice" | "system") => {
    const trimmed = sanitizeRealtimeText(text);
    if (!trimmed) {
      return;
    }
    setEvents((current) => [...current.slice(-15), { id: makeId("setup"), speaker, text: trimmed, mode, created_at: nowIso() }]);
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
      setError(`${context}. ${detail}. Click Speak again to resume audio.`);
      releaseAssistantSpeaking();
      setAwaitingVoiceReply(false);
      return false;
    }
  });

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
      audioElement.muted = nextMuted;
      if (pause) {
        audioElement.pause();
        continue;
      }
      if (!nextMuted) {
        void attemptRemoteAudioPlay(audioElement, "Orchestrator audio could not start");
      }
    }
  });

  const sendEvent = useEffectEvent((payload: Record<string, unknown>, options?: { tolerateClosed?: boolean }) => {
    const channel = dataChannelRef.current;
    if (channel?.readyState === "open") {
      channel.send(JSON.stringify(payload));
      return true;
    }
    if (!options?.tolerateClosed && (statusRef.current === "connected" || statusRef.current === "connecting")) {
      flagControlChannelFailure("The setup voice control channel dropped");
    }
    return false;
  });

  const updateVoiceTurnDetection = useEffectEvent((_paused: boolean) => {
    // Keep setup pause/resume local so the chamber mic toggle does not depend
    // on schema-sensitive session.update payloads.
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

  const syncPromptTurn = useEffectEvent(async (text: string) => {
    const activeSession = sessionRef.current;
    if (!activeSession) {
      return;
    }
    const nextSession = await sendSetupPrompt(activeSession, text);
    sessionRef.current = nextSession;
    onSessionSync(nextSession);
    if (onAutoLaunch && nextSession.guidance?.launch_now === true && nextSession.status === "ready") {
      await onAutoLaunch(nextSession, text);
    }
  });

  const queueSetupSync = useEffectEvent((text: string) => {
    const previous = pendingSyncPromiseRef.current ?? Promise.resolve(sessionRef.current as SetupSessionState);
    const next = previous
      .catch(() => sessionRef.current as SetupSessionState)
      .then(async () => {
        await syncPromptTurn(text);
        return sessionRef.current as SetupSessionState;
      });
    pendingSyncPromiseRef.current = next;
    void next.catch((caught) => {
      const message = caught instanceof Error ? caught.message : "Failed to update setup chamber";
      setError(message);
    }).finally(() => {
      if (pendingSyncPromiseRef.current === next) {
        pendingSyncPromiseRef.current = null;
      }
    });
    return next;
  });

  const hardStopRealtime = useEffectEvent(() => {
    nextVoiceEpoch();
    activeInputEpochRef.current = null;
    awaitFreshInputRef.current = true;
    dropPendingVoiceResponsesRef.current = true;
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
    setRecordingVoiceTurn(false);
    setAwaitingVoiceReply(false);
  });

  const pauseRealtime = useEffectEvent(() => {
    nextVoiceEpoch();
    activeInputEpochRef.current = null;
    awaitFreshInputRef.current = true;
    dropPendingVoiceResponsesRef.current = true;
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
    setRecordingVoiceTurn(false);
    setAwaitingVoiceReply(false);
  });

  const resumeRealtimeAfterPause = useEffectEvent(() => {
    nextVoiceEpoch();
    activeInputEpochRef.current = null;
    awaitFreshInputRef.current = true;
    dropPendingVoiceResponsesRef.current = true;
    ignoredResponseIdsRef.current.clear();
    mutedRef.current = false;
    setMuted(false);
    setRecordingVoiceTurn(false);
    setAwaitingVoiceReply(false);
    releaseAssistantSpeaking();
    syncInputTrackState(false);
    updateVoiceTurnDetection(false);
    syncRemoteAudioState(false, false);
    const audio = remoteAudioRef.current;
    if (audio) {
      void attemptRemoteAudioPlay(audio, "Orchestrator audio could not resume");
    }
  });

  const awaitPendingSync = useEffectEvent(async () => {
    const pending = pendingSyncPromiseRef.current;
    if (pending) {
      await pending;
    }
    return sessionRef.current;
  });

  const handleRealtimeEvent = useEffectEvent(async (payload: Record<string, unknown>) => {
    const eventType = String(payload.type ?? "");
    const payloadResponseId = String(payload.response_id ?? "");
    if (payloadResponseId) {
      const responseEpoch = responseEpochRef.current[payloadResponseId];
      if (responseEpoch !== undefined && responseEpoch !== voiceEpochRef.current) {
        if (eventType === "response.done") {
          delete responseTextRef.current[payloadResponseId];
          delete responseAudioTranscriptRef.current[payloadResponseId];
          delete responseEpochRef.current[payloadResponseId];
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
      }
      return;
    }
    if (eventType === "response.created") {
      if (liveModeRef.current === "voice") {
        const response = (payload.response as Record<string, unknown> | undefined) ?? {};
        const responseId = String(response.id ?? payloadResponseId ?? "");
        if (responseId) {
          responseEpochRef.current[responseId] = voiceEpochRef.current;
        }
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
      setAwaitingVoiceReply(true);
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
      appendLocalTurn("user", transcript, "voice");
      if (setupLaunchIntent(transcript)) {
        await awaitPendingSync();
        await syncPromptTurn(transcript);
        return;
      }
      queueSetupSync(transcript);
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
      const voiceLikeResponse = liveModeRef.current === "voice" || Boolean(responseAudioTranscriptRef.current[responseId]);
      if (voiceLikeResponse && (mutedRef.current || dropPendingVoiceResponsesRef.current)) {
        if (responseId) {
          delete responseTextRef.current[responseId];
          delete responseAudioTranscriptRef.current[responseId];
          delete responseEpochRef.current[responseId];
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
      }
      if (!finalText) {
        return;
      }
      appendLocalTurn("assistant", finalText, "text");
      return;
    }
    if (eventType === "response.done") {
      const response = (payload.response as Record<string, unknown> | undefined) ?? {};
      const responseId = String(response.id ?? "");
      if (responseId && completedTextResponseIdsRef.current.has(responseId)) {
        completedTextResponseIdsRef.current.delete(responseId);
        delete responseTextRef.current[responseId];
        delete responseAudioTranscriptRef.current[responseId];
        delete responseEpochRef.current[responseId];
        return;
      }
      const responseStatus = String(response.status ?? "completed");
      const wasVoiceResponse = liveModeRef.current === "voice" || Boolean(responseAudioTranscriptRef.current[responseId]);
      const text = sanitizeRealtimeText(
        extractRealtimeText(response.output) ||
        String(responseTextRef.current[responseId] ?? responseAudioTranscriptRef.current[responseId] ?? ""),
      );
      if (responseId) {
        delete responseTextRef.current[responseId];
        delete responseAudioTranscriptRef.current[responseId];
        delete responseEpochRef.current[responseId];
      }
      if (wasVoiceResponse && !audioOutputPlayingRef.current) {
        releaseAssistantSpeaking();
      }
      if (wasVoiceResponse && (mutedRef.current || dropPendingVoiceResponsesRef.current)) {
        return;
      }
      if (!text || (responseStatus && responseStatus !== "completed")) {
        return;
      }
      appendLocalTurn("assistant", text, wasVoiceResponse ? "voice" : "text");
      return;
    }
    if (eventType === "error") {
      const message = String((payload.error as Record<string, unknown> | undefined)?.message ?? "Realtime session failed");
      setAwaitingVoiceReply(false);
      setRecordingVoiceTurn(false);
      setError(message);
      setStatus("error");
    }
  });

  const disconnect = useEffectEvent(() => {
    controlChannelFailureRef.current = false;
    voiceToggleInFlightRef.current = false;
    connectionRequestedRef.current = false;
    connectGenerationRef.current += 1;
    clearDisconnectGraceTimer();
    hardStopRealtime();
    disposeAllRealtimeTransport();
    audioOutputPlayingRef.current = false;
    assistantSpeakingRef.current = false;
    setAssistantSpeaking(false);
    setRecordingVoiceTurn(false);
    setAwaitingVoiceReply(false);
    mutedRef.current = false;
    setMuted(false);
    setLiveMode("text");
    setStatus("idle");
    setPresence(EMPTY_PRESENCE);
    responseTextRef.current = {};
    responseAudioTranscriptRef.current = {};
    completedTextResponseIdsRef.current.clear();
    dropPendingVoiceResponsesRef.current = true;
    activeInputEpochRef.current = null;
    awaitFreshInputRef.current = true;
    pendingSyncPromiseRef.current = null;
  });

  const connectInternal = useEffectEvent(async (withAudio: boolean, options?: { silentOpen?: boolean }) => {
    const activeSession = session ?? sessionRef.current;
    if (!activeSession) {
      throw new Error("setup chamber is not ready");
    }
    if (statusRef.current === "connecting") {
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
      disposeAllRealtimeTransport();
    }
    const generation = connectGenerationRef.current + 1;
    connectGenerationRef.current = generation;
    controlChannelFailureRef.current = false;
    connectionRequestedRef.current = true;
    const generationMatches = () => connectGenerationRef.current === generation;
    responseTextRef.current = {};
    responseAudioTranscriptRef.current = {};
    responseEpochRef.current = {};
    completedTextResponseIdsRef.current.clear();
    ignoredResponseIdsRef.current.clear();
    setStatus("connecting");
    setLiveMode(withAudio ? "voice" : "text");
    setError(null);
    audioOutputPlayingRef.current = false;
    assistantSpeakingRef.current = false;
    nextVoiceEpoch();
    activeInputEpochRef.current = null;
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
      const realtimeSession = await createSetupRealtimeSession(activeSession.session_id);
      if (!generationMatches()) {
        clearPendingRealtimeTransportRefs();
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
      audioElement.muted = !withAudio;
      audioElement.style.display = "none";
      document.body.appendChild(audioElement);
      peerConnection.ontrack = (event) => {
        if (!generationMatches()) {
          return;
        }
        audioElement.srcObject = event.streams[0];
        void attemptRemoteAudioPlay(audioElement, "Orchestrator audio could not start");
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
          appendLocalTurn("system", withAudio ? "Orchestrator channel live." : "Text channel live.", "system");
        }
      });
      dataChannel.addEventListener("close", () => {
        if (!generationMatches()) {
          return;
        }
        flagControlChannelFailure("The setup voice control channel closed");
      });
      dataChannel.addEventListener("error", () => {
        if (!generationMatches()) {
          return;
        }
        flagControlChannelFailure("The setup voice control channel failed");
      });
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
          Authorization: `Bearer ${realtimeSession.client_secret}`,
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
          dataChannel.addEventListener(
            "open",
            () => {
              window.clearTimeout(timeout);
              resolve();
            },
            { once: true },
          );
          dataChannel.addEventListener(
            "error",
            () => {
              window.clearTimeout(timeout);
              reject(new Error("Realtime data channel failed"));
            },
            { once: true },
          );
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
      setLiveMode("text");
      mutedRef.current = false;
      setMuted(false);
      setRecordingVoiceTurn(false);
      setAwaitingVoiceReply(false);
      releaseAssistantSpeaking();
      setStatus("error");
      throw caught;
    }
  });

  async function sendText(text: string) {
    const trimmed = text.trim();
    if (!trimmed) {
      return;
    }
    try {
      if (setupLaunchIntent(trimmed)) {
        appendLocalTurn("user", trimmed, "text");
        await awaitPendingSync();
        await syncPromptTurn(trimmed);
        return;
      }
      const wantsVoiceReply =
        liveModeRef.current === "voice" ||
        connectionRequestedRef.current ||
        statusRef.current === "connecting";
      if (statusRef.current !== "connected") {
        try {
          await connectInternal(wantsVoiceReply);
          if (wantsVoiceReply) {
            await waitForConnectedStatus();
          }
        } catch {
          appendLocalTurn("user", trimmed, "text");
          await awaitPendingSync();
          return;
        }
      }
      const useVoiceReply = statusRef.current === "connected" && liveModeRef.current === "voice";
      appendLocalTurn("user", trimmed, "text");
      queueSetupSync(trimmed);
      sendEvent({
        type: "conversation.item.create",
        item: {
          type: "message",
          role: "user",
          content: [{ type: "input_text", text: trimmed }],
        },
      });
      if (useVoiceReply) {
        activeInputEpochRef.current = voiceEpochRef.current;
        awaitFreshInputRef.current = false;
        dropPendingVoiceResponsesRef.current = false;
        setAwaitingVoiceReply(true);
        sendEvent({
          type: "response.create",
          response: { output_modalities: ["audio"] },
        });
      } else {
        sendEvent({
          type: "response.create",
          response: { output_modalities: ["text"] },
        });
      }
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

  useEffect(() => {
    eventsRef.current = events;
  }, [events]);

  useEffect(() => {
    sessionRef.current = session;
    const nextSessionId = session?.session_id ?? null;
    const transcriptEvents = session?.transcript.map((turn) => ({ ...turn, mode: "text" as const })) ?? [];
    if (nextSessionId !== sessionIdRef.current) {
      sessionIdRef.current = nextSessionId;
      setEvents(transcriptEvents);
      return;
    }
    if (transcriptEvents.length > eventsRef.current.length && status !== "connected") {
      setEvents(transcriptEvents);
    }
  }, [session, status]);

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
    setError(null);
  }, [session?.session_id]);

  useEffect(() => () => disconnect(), []);

  useEffect(() => {
    syncInputTrackState(muted);
  }, [muted, syncInputTrackState]);

  useEffect(() => {
    if (!remoteAudioRef.current) {
      return;
    }
    remoteAudioRef.current.muted = liveMode !== "voice" || muted;
  }, [liveMode, muted]);

  return {
    assistantSpeaking,
    awaitingVoiceReply,
    disconnect,
    enableVoice,
    error,
    events,
    liveMode,
    muted,
    presence,
    recordingVoiceTurn,
    awaitPendingSync,
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
