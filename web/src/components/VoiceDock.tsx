import { forwardRef, useEffect, useEffectEvent, useImperativeHandle, useMemo, useRef, useState } from "react";
import { useRealtimeSession } from "../hooks/useRealtimeSession";
import { parseCouncilCaption, splitCouncilLines, type CouncilTurnContext } from "../lib/council";
import type { AdvisorMode, AuditoriumMode, ConversationTurn, RealtimeRole, ScenePresence, SimulationState } from "../types";

interface VoiceDockProps {
  simulationId?: string;
  role: RealtimeRole;
  citizenId?: string;
  advisorMode?: AdvisorMode;
  auditoriumMode?: AuditoriumMode;
  sessionAuditoriumMode?: AuditoriumMode;
  councilContext?: CouncilTurnContext;
  title: string;
  blurb: string;
  turns: ConversationTurn[];
  metaChips?: string[];
  onSimulationSync: (simulation: SimulationState) => void;
  onPresenceChange?: (presence: ScenePresence) => void;
  onCouncilFloorChange?: (floor: {
    lead: string;
    urgencies: Record<string, number>;
    contrast: string[];
    reason?: string;
  } | null) => void;
}

function speakerLabel(role: RealtimeRole, turn: ConversationTurn, advisorMode: AdvisorMode, auditoriumMode: AuditoriumMode) {
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
      const parsed = parseCouncilCaption(turn.text);
      if (parsed.speaker) {
        return parsed.speaker;
      }
      return "Council";
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
  injectContextTurn: (text: string) => Promise<boolean>;
  requestAssistantReply: (instructions?: string) => Promise<boolean>;
  sendText: (text: string) => Promise<void>;
  toggleMute: () => void;
  toggleVoiceCapture: () => Promise<void>;
}

export const VoiceDock = forwardRef<VoiceDockHandle, VoiceDockProps>(function VoiceDock({
  simulationId,
  role,
  citizenId,
  advisorMode = "solo",
  auditoriumMode = "debate",
  sessionAuditoriumMode,
  councilContext,
  title,
  blurb,
  turns,
  metaChips = [],
  onSimulationSync,
  onPresenceChange,
  onCouncilFloorChange,
}: VoiceDockProps, ref) {
  const [draft, setDraft] = useState("");
  const composerRef = useRef<HTMLTextAreaElement | null>(null);
  const liveAuditoriumMode = sessionAuditoriumMode ?? auditoriumMode;
  const session = useRealtimeSession({
    simulationId,
    role,
    citizenId,
    advisorMode,
    auditoriumMode: liveAuditoriumMode,
    councilContext,
    initialTurns: turns,
    onSimulationSync,
    onCouncilFloorChange,
  });

  const renderedEvents = useMemo(
    () =>
      session.events.flatMap((event) => {
        if (!(advisorMode === "council" && role === "advisor" && event.speaker === "assistant")) {
          return [{ ...event, displaySpeaker: undefined, displayText: event.text }];
        }
        const councilLines = splitCouncilLines(event.text);
        if (councilLines.length <= 1) {
          const parsed = parseCouncilCaption(event.text);
          return [{ ...event, displaySpeaker: parsed.speaker, displayText: parsed.text || event.text }];
        }
        return councilLines.map((line, index) => ({
          ...event,
          id: `${event.id}:${index}`,
          displaySpeaker: line.speaker,
          displayText: line.text,
        }));
      }),
    [advisorMode, role, session.events],
  );

  const conversationLabel =
    session.status === "connecting"
      ? session.liveMode === "voice"
        ? "connecting mic"
        : "opening text channel"
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

  useEffect(() => {
    emitPresenceChange(session.presence);
  }, [emitPresenceChange, session.presence]);

  useImperativeHandle(ref, () => ({
    addTurn: session.addTurn,
    disconnect: session.disconnect,
    enableVoice: session.enableVoice,
    focusComposer: () => composerRef.current?.focus(),
    injectContextTurn: session.injectContextTurn,
    requestAssistantReply: session.requestAssistantReply,
    sendText: session.sendText,
    toggleMute: session.toggleMute,
    toggleVoiceCapture: session.toggleVoiceCapture,
  }), [session.addTurn, session.disconnect, session.enableVoice, session.injectContextTurn, session.requestAssistantReply, session.sendText, session.toggleMute, session.toggleVoiceCapture]);

  async function handleSend() {
    if (!draft.trim()) {
      return;
    }
    const next = draft;
    setDraft("");
    await session.sendText(next);
  }

  async function handleVoiceButton() {
    await session.toggleVoiceCapture();
  }

  return (
    <section className={`voice-dock voice-dock--${role}`}>
      <div className="voice-dock__hero">
        <div className={`voice-dock__portrait voice-dock__portrait--${portraitState}`}>
          <div className="voice-dock__portrait-halo" />
          <div className="voice-dock__portrait-figure" />
        </div>
        <div className="voice-dock__hero-copy">
          <div className="voice-dock__heading">
            <span className="composer__eyebrow">
              {role === "advisor" ? "War room channel" : role === "debate" ? auditoriumMode === "town_hall" ? "Town hall channel" : "Debate channel" : "Interview channel"}
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
            Start by typing, or turn the mic on to stay in a live back-and-forth.
          </p>
        ) : null}
        {renderedEvents.map((event) => (
          <article key={event.id} className={`voice-log__entry voice-log__entry--${event.speaker}`}>
            <span>{event.displaySpeaker ?? speakerLabel(role, event, advisorMode, liveAuditoriumMode)}</span>
            <p>{event.displayText}</p>
          </article>
        ))}
      </div>

      <div className="composer">
        <label className="composer__field">
          <span className="composer__eyebrow">
            {role === "advisor" ? "Ask the room" : role === "debate" ? auditoriumMode === "town_hall" ? "Town hall turn" : "Debate turn" : "Interview prompt"}
          </span>
          <textarea
            ref={composerRef}
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            placeholder={
              role === "advisor"
                ? "Ask what is changing, what the public loves or fears, who to talk to, or what tradeoff matters most."
                : role === "debate"
                  ? auditoriumMode === "town_hall"
                    ? "Take the audience question head on, answer plainly, or challenge the room's premise."
                    : "State your case, answer the opponent, or challenge their assumptions before you call the election."
                  : "Ask about work, dignity, hope, worry, what AI has improved, or what now feels more fragile."
            }
            rows={4}
          />
        </label>
        <div className="composer__actions">
          <button className="btn btn--primary" onClick={handleSend} disabled={!simulationId || !draft.trim()}>
            Send
          </button>
          <button className="btn btn--secondary" onClick={handleVoiceButton} disabled={!simulationId}>
            {session.liveMode === "voice" && session.status === "connected"
              ? "Stop"
              : "Speak"}
          </button>
          <button className="btn btn--ghost" onClick={session.disconnect} disabled={session.status === "idle"}>
            End
          </button>
        </div>
      </div>

      {session.error ? <p className="voice-dock__error">{session.error}</p> : null}
    </section>
  );
});
