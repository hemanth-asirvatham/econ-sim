import { forwardRef, useEffect, useEffectEvent, useImperativeHandle, useMemo, useRef, useState } from "react";
import { useRealtimeSession } from "../hooks/useRealtimeSession";
import { normalizeCouncilRoster, parseCouncilCaption, splitCouncilLines, type CouncilTurnContext } from "../lib/council";
import type { AdvisorMode, AuditoriumMode, ConversationTurn, CouncilAdvisorProfile, RealtimeRole, RoomName, ScenePresence, SimulationState } from "../types";

interface VoiceDockProps {
  scopeKey?: string;
  simulationId?: string;
  role: RealtimeRole;
  themeMode?: "light" | "dark";
  presentation?: "full" | "drawer";
  citizenId?: string;
  advisorMode?: AdvisorMode;
  auditoriumMode?: AuditoriumMode;
  sessionAuditoriumMode?: AuditoriumMode;
  autoResponse?: boolean;
  externalPlaybackActive?: boolean;
  councilContext?: CouncilTurnContext;
  councilRoster?: CouncilAdvisorProfile[];
  title: string;
  blurb: string;
  draftPlaceholder?: string;
  emptyStateText?: string;
  turns: ConversationTurn[];
  metaChips?: string[];
  onSimulationSync: (simulation: SimulationState) => void;
  onPresenceChange?: (presence: ScenePresence) => void;
  onLiveCaptionChange?: (turn: ConversationTurn | null) => void;
  onCouncilFloorChange?: (floor: {
    lead: string;
    owner: string;
    contrast: string[];
    reason?: string;
  } | null) => void;
  onHybridSpeechStart?: () => void;
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

function speakerLabel(
  role: RealtimeRole,
  turn: ConversationTurn,
  advisorMode: AdvisorMode,
  auditoriumMode: AuditoriumMode,
  councilRoster?: ReturnType<typeof normalizeCouncilRoster>,
) {
  if (turn.speaker === "user") {
    return turn.mode === "voice" ? "You · live mic" : "You";
  }
  if (turn.speaker === "system") {
    return "Channel";
  }
  if (turn.speaker_name?.trim()) {
    return turn.speaker_name.trim();
  }
  if (role === "advisor") {
    if (advisorMode === "council") {
      const parsed = parseCouncilCaption(turn.text, councilRoster);
      if (parsed.speaker) {
        return parsed.speaker;
      }
      return "Advisor table";
    }
    return "Advisor";
  }
  if (role === "debate") {
    return auditoriumMode === "town_hall" ? "Town hall" : "Opponent";
  }
  return "Citizen";
}

export interface VoiceDockHandle {
  addTurn: (turn: Pick<ConversationTurn, "speaker" | "speaker_name" | "speaker_voice" | "text" | "mode">) => Promise<void>;
  enableVoice: () => Promise<void>;
  disconnect: () => void;
  focusComposer: () => void;
  getScopeKey: () => string;
  injectContextTurn: (text: string) => Promise<boolean>;
  requestAssistantReply: (instructions?: string) => Promise<boolean>;
  sendText: (text: string) => Promise<void>;
  startOrToggleVoice: () => Promise<void>;
  toggleMute: () => void;
  toggleVoiceCapture: () => Promise<void>;
}

export const VoiceDock = forwardRef<VoiceDockHandle, VoiceDockProps>(function VoiceDock({
  scopeKey,
  simulationId,
  role,
  themeMode = "dark",
  presentation = "full",
  citizenId,
  advisorMode = "solo",
  auditoriumMode = "debate",
  sessionAuditoriumMode,
  autoResponse = true,
  externalPlaybackActive = false,
  councilContext,
  councilRoster,
  title,
  blurb,
  draftPlaceholder,
  emptyStateText,
  turns,
  metaChips = [],
  onSimulationSync,
  onPresenceChange,
  onLiveCaptionChange,
  onCouncilFloorChange,
  onHybridSpeechStart,
  onModeCommand,
}: VoiceDockProps, ref) {
  const [draft, setDraft] = useState("");
  const composerRef = useRef<HTMLTextAreaElement | null>(null);
  const lastPresenceSignatureRef = useRef("");
  const lastSceneCaptionRef = useRef<ConversationTurn | null>(null);
  const liveAuditoriumMode = sessionAuditoriumMode ?? auditoriumMode;
  const normalizedCouncilRoster = useMemo(() => normalizeCouncilRoster(councilRoster), [councilRoster]);
  const liveScopeKey = useMemo(
    () => scopeKey ?? `${simulationId ?? "local"}:${role}:${citizenId ?? "shared"}:${advisorMode}:${liveAuditoriumMode}`,
    [advisorMode, citizenId, liveAuditoriumMode, role, scopeKey, simulationId],
  );
  const session = useRealtimeSession({
    simulationId,
    role,
    citizenId,
    advisorMode,
    auditoriumMode: liveAuditoriumMode,
    autoResponse,
    externalPlaybackActive,
    councilContext,
    councilRoster,
    initialTurns: turns,
    onSimulationSync,
    onCouncilFloorChange,
    onHybridSpeechStart,
    onModeCommand,
  });

  const renderedEvents = useMemo(
    () =>
      session.events.flatMap((event) => {
        if (!(advisorMode === "council" && role === "advisor" && event.speaker === "assistant")) {
          return [{ ...event, displaySpeaker: undefined, displayText: event.text }];
        }
        const councilLines = splitCouncilLines(event.text, normalizedCouncilRoster);
        if (councilLines.length <= 1) {
          const parsed = parseCouncilCaption(event.text, normalizedCouncilRoster);
          return [{ ...event, displaySpeaker: parsed.speaker, displayText: parsed.text || event.text }];
        }
        return councilLines.map((line, index) => ({
          ...event,
          id: `${event.id}:${index}`,
          displaySpeaker: line.speaker,
          displayText: line.text,
        }));
      }),
    [advisorMode, normalizedCouncilRoster, role, session.events],
  );
  const liveCaptionTurn = useMemo<ConversationTurn | null>(() => {
    const latestEvent = [...renderedEvents]
      .reverse()
      .find((event) => event.speaker !== "system" && event.displayText.trim());
    if (!latestEvent) {
      return null;
    }
    return {
      ...latestEvent,
      speaker_name: latestEvent.displaySpeaker ?? latestEvent.speaker_name,
      text: latestEvent.displayText,
    };
  }, [renderedEvents]);

  const conversationLabel =
    session.status === "connecting"
      ? session.liveMode === "voice"
        ? "connecting mic"
        : "opening text channel"
      : session.status === "error"
        ? "channel lost"
      : session.status === "connected"
        ? session.liveMode === "voice"
          ? session.assistantSpeaking
            ? "counterpart speaking"
            : session.awaitingVoiceReply
              ? "waiting for reply"
              : session.muted
                ? "mic paused"
                : "live voice"
          : "text live"
        : "ready";

  const portraitState = useMemo(() => {
    if (session.presence.counterpartActivity === "speaking") {
      return "speaking";
    }
    if (session.presence.playerActivity === "speaking" || session.presence.counterpartActivity === "listening") {
      return "listening";
    }
    return "idle";
  }, [session.presence.counterpartActivity, session.presence.playerActivity]);
  const emitPresenceChange = useEffectEvent((nextPresence: ScenePresence) => {
    onPresenceChange?.(nextPresence);
  });
  const emitLiveCaptionChange = useEffectEvent((turn: ConversationTurn | null) => {
    onLiveCaptionChange?.(turn);
  });
  const presenceSignature =
    `${session.presence.status}:${session.presence.liveMode}:${session.presence.muted ? "muted" : "open"}:` +
    `${session.presence.playerActivity}:${session.presence.counterpartActivity}:${session.presence.voicePhase}`;

  useEffect(() => {
    if (lastPresenceSignatureRef.current === presenceSignature) {
      return;
    }
    lastPresenceSignatureRef.current = presenceSignature;
    emitPresenceChange({
      status: session.presence.status,
      liveMode: session.presence.liveMode,
      muted: session.presence.muted,
      playerActivity: session.presence.playerActivity,
      counterpartActivity: session.presence.counterpartActivity,
      voicePhase: session.presence.voicePhase,
    });
  }, [
    emitPresenceChange,
    presenceSignature,
    session.presence.counterpartActivity,
    session.presence.liveMode,
    session.presence.muted,
    session.presence.playerActivity,
    session.presence.status,
    session.presence.voicePhase,
  ]);

  useEffect(() => {
    const liveSpeaking =
      session.status === "connected" &&
      ((session.assistantSpeaking || session.presence.counterpartActivity === "speaking")
        ? liveCaptionTurn?.speaker === "assistant"
        : session.recordingVoiceTurn || session.presence.playerActivity === "speaking"
          ? liveCaptionTurn?.speaker === "user"
          : false);
    if (liveSpeaking && liveCaptionTurn) {
      lastSceneCaptionRef.current = liveCaptionTurn;
      emitLiveCaptionChange(liveCaptionTurn);
      return;
    }
    const holdCouncilCaption =
      role === "advisor" &&
      advisorMode === "council" &&
      session.awaitingVoiceReply &&
      lastSceneCaptionRef.current?.speaker === "assistant";
    if (holdCouncilCaption) {
      emitLiveCaptionChange(lastSceneCaptionRef.current);
      return;
    }
    lastSceneCaptionRef.current = null;
    emitLiveCaptionChange(null);
  }, [
    emitLiveCaptionChange,
    advisorMode,
    liveCaptionTurn,
    role,
    session.assistantSpeaking,
    session.awaitingVoiceReply,
    session.presence.counterpartActivity,
    session.presence.playerActivity,
    session.recordingVoiceTurn,
    session.status,
  ]);

  useImperativeHandle(ref, () => ({
    addTurn: session.addTurn,
    disconnect: session.disconnect,
    enableVoice: session.enableVoice,
    focusComposer: () => composerRef.current?.focus(),
    getScopeKey: () => liveScopeKey,
    injectContextTurn: session.injectContextTurn,
    requestAssistantReply: session.requestAssistantReply,
    sendText: session.sendText,
    startOrToggleVoice: session.startOrToggleVoice,
    toggleMute: session.toggleMute,
    toggleVoiceCapture: session.toggleVoiceCapture,
  }), [liveScopeKey, session.addTurn, session.disconnect, session.enableVoice, session.injectContextTurn, session.requestAssistantReply, session.sendText, session.startOrToggleVoice, session.toggleMute, session.toggleVoiceCapture]);

  async function handleSend() {
    if (!draft.trim()) {
      return;
    }
    const next = draft;
    setDraft("");
    await session.sendText(next);
  }

  async function handleVoiceButton() {
    await session.startOrToggleVoice();
  }

  const voiceActionLabel =
    session.status === "connecting"
      ? session.liveMode === "voice"
        ? "Joining..."
        : "Opening..."
      : session.liveMode === "voice" && session.status === "connected"
        ? session.muted
          ? "Resume mic"
          : "Pause mic"
        : "Speak";

  return (
    <section className={`voice-dock voice-dock--${role} voice-dock--theme-${themeMode} voice-dock--${presentation}`}>
      <div className="voice-dock__hero">
        <div className={`voice-dock__portrait voice-dock__portrait--${portraitState}`}>
          <div className="voice-dock__portrait-halo" />
          <div className="voice-dock__portrait-figure" />
        </div>
        <div className="voice-dock__hero-copy">
          <div className="voice-dock__heading">
            <span className="composer__eyebrow">
              {role === "advisor"
                ? advisorMode === "council"
                  ? "Multi-advisor room"
                  : "War room channel"
                : role === "debate"
                  ? auditoriumMode === "town_hall"
                    ? "Town hall floor"
                    : "Debate channel"
                  : "Interview channel"}
            </span>
            <h3>{title}</h3>
            <span className={`voice-dock__mode voice-dock__mode--${session.liveMode}`}>{conversationLabel}</span>
          </div>
          <p>{blurb}</p>
          {metaChips.length > 0 ? (
            <div className="voice-dock__chips">
              {metaChips.map((chip) => (
                <span key={chip}>{chip}</span>
              ))}
            </div>
          ) : null}
        </div>
      </div>

      <div className="voice-log">
        {session.events.length === 0 ? (
          <p className="voice-log__placeholder">
            {emptyStateText ?? "Start by typing, or turn the mic on to stay in a live back-and-forth."}
          </p>
        ) : null}
        {renderedEvents.map((event) => (
          <article key={event.id} className={`voice-log__entry voice-log__entry--${event.speaker}`}>
            <span>{event.displaySpeaker ?? speakerLabel(role, event, advisorMode, liveAuditoriumMode, normalizedCouncilRoster)}</span>
            <p>{event.displayText}</p>
          </article>
        ))}
      </div>

      <div className="composer">
        <label className="composer__field">
          <span className="composer__eyebrow">
            {role === "advisor"
              ? advisorMode === "council"
                ? "Ask the table"
                : "Ask the room"
              : role === "debate"
                ? auditoriumMode === "town_hall"
                  ? "Town hall answer"
                  : "Debate turn"
                : "Interview prompt"}
          </span>
          <textarea
            ref={composerRef}
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            disabled={session.status === "connecting" && session.liveMode === "text"}
            placeholder={
              draftPlaceholder ??
              (role === "advisor"
                ? advisorMode === "council"
                  ? "Ask where the advisors split, who should answer, what belongs on the board, or what not to break."
                  : "Ask what is changing, what the public loves or fears, who to talk to, or what tradeoff matters most."
                : role === "debate"
                  ? auditoriumMode === "town_hall"
                    ? "Answer the voter plainly, then invite a rebuttal only if you want one."
                    : "State your case, answer the opponent, or challenge their assumptions before you call the election."
                  : "Ask about work, dignity, hope, worry, what AI has improved, or what now feels more fragile.")
            }
            rows={4}
          />
        </label>
        <div className="composer__actions">
          <button className="btn btn--primary" onClick={handleSend} disabled={!simulationId || !draft.trim() || (session.status === "connecting" && session.liveMode === "text")}>
            Send
          </button>
          <button className="btn btn--secondary composer__voice-action" onClick={handleVoiceButton} disabled={!simulationId}>
            {voiceActionLabel}
          </button>
          <button className="btn btn--ghost composer__end-action" onClick={session.disconnect} disabled={session.status === "idle"}>
            End
          </button>
        </div>
      </div>

      {session.error ? <p className="voice-dock__error">{session.error}</p> : null}
    </section>
  );
});
