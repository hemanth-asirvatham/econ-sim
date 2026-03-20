import { useEffect, useMemo, useRef, useState } from "react";
import type { RefObject } from "react";
import { VoiceDock, type VoiceDockHandle } from "./VoiceDock";
import { generateTownHallQuestion, synthesizeSpeech } from "../lib/api";
import { buildTownHallQuestions, type TownHallQuestion } from "../lib/townHall";
import type { AuditoriumMode, ConversationTurn, ScenePresence, SimulationState, StagePackage } from "../types";

interface DebateRoomProps {
  simulationId?: string;
  stage: StagePackage;
  debateTurns: ConversationTurn[];
  auditoriumMode: AuditoriumMode;
  resolvedPlatform: string;
  pending: boolean;
  onResolve: (playerPlatform: string, playerRebuttal: string) => Promise<void>;
  onToggleTownHall: () => void;
  onSimulationSync: (simulation: SimulationState) => void;
  onPresenceChange?: (presence: ScenePresence) => void;
  voiceDockRef?: RefObject<VoiceDockHandle | null>;
}

function formatBallotLine(stage: StagePackage) {
  const voteQuestion = stage.poll_summaries.find((summary) => summary.question.includes("election were held today"));
  if (!voteQuestion) {
    return "Polling has not been refreshed yet.";
  }
  return Object.entries(voteQuestion.shares)
    .map(([label, value]) => `${label}: ${(value * 100).toFixed(0)}%`)
    .join(" · ");
}

export function DebateRoom({
  simulationId,
  stage,
  debateTurns,
  auditoriumMode,
  resolvedPlatform,
  pending,
  onResolve,
  onToggleTownHall,
  onSimulationSync,
  onPresenceChange,
  voiceDockRef,
}: DebateRoomProps) {
  const ballotLine = useMemo(() => formatBallotLine(stage), [stage]);
  const playerTurns = useMemo(
    () => debateTurns.filter((turn) => turn.speaker === "user").map((turn) => turn.text.trim()).filter(Boolean),
    [debateTurns],
  );
  const playerCase = useMemo(() => playerTurns.join("\n\n"), [playerTurns]);
  const latestPlayerTurn = playerTurns.at(-1);
  const platformNotes = stage.policy_notes.length > 0 ? stage.policy_notes : stage.suggested_policy_axes.slice(0, 4);
  const townHallQuestions = useMemo(() => buildTownHallQuestions(stage, debateTurns), [debateTurns, stage]);
  const [townHallIndex, setTownHallIndex] = useState(0);
  const [townHallPlaying, setTownHallPlaying] = useState(false);
  const [townHallError, setTownHallError] = useState<string | null>(null);
  const [liveTownHallQuestion, setLiveTownHallQuestion] = useState<TownHallQuestion | null>(null);
  const townHallAudioRef = useRef<HTMLAudioElement | null>(null);
  const townHallAbortRef = useRef<AbortController | null>(null);
  const townHallQuestion = townHallQuestions[townHallIndex] ?? townHallQuestions[0];
  const visibleTownHallQuestion = liveTownHallQuestion ?? townHallQuestion;

  useEffect(() => {
    if (townHallIndex < townHallQuestions.length) {
      return;
    }
    setTownHallIndex(0);
  }, [townHallIndex, townHallQuestions.length]);

  useEffect(() => {
    setLiveTownHallQuestion(null);
  }, [townHallIndex]);

  useEffect(() => () => {
    townHallAbortRef.current?.abort();
    if (townHallAudioRef.current) {
      townHallAudioRef.current.pause();
      townHallAudioRef.current.src = "";
      townHallAudioRef.current.remove();
      townHallAudioRef.current = null;
    }
  }, []);

  async function handlePlayTownHallQuestion(question = visibleTownHallQuestion) {
    if (!question) {
      return;
    }
    townHallAbortRef.current?.abort();
    const controller = new AbortController();
    townHallAbortRef.current = controller;
    setTownHallPlaying(true);
    setTownHallError(null);
    try {
      const blob = await synthesizeSpeech(question.question, question.voice, controller.signal);
      const audio = townHallAudioRef.current ?? document.createElement("audio");
      if (!townHallAudioRef.current) {
        audio.autoplay = true;
        audio.setAttribute("playsinline", "true");
        audio.style.display = "none";
        document.body.appendChild(audio);
        townHallAudioRef.current = audio;
      }
      const url = URL.createObjectURL(blob);
      audio.src = url;
      await audio.play();
      await new Promise<void>((resolve) => {
        const done = () => {
          audio.removeEventListener("ended", done);
          audio.removeEventListener("error", done);
          URL.revokeObjectURL(url);
          resolve();
        };
        audio.addEventListener("ended", done, { once: true });
        audio.addEventListener("error", done, { once: true });
      });
    } catch (caught) {
      if (!(caught instanceof DOMException && caught.name === "AbortError")) {
        setTownHallError(caught instanceof Error ? caught.message : "Town hall question playback failed");
      }
    } finally {
      if (townHallAbortRef.current === controller) {
        townHallAbortRef.current = null;
      }
      setTownHallPlaying(false);
    }
  }

  async function handleAskTownHallQuestion() {
    if (!townHallQuestion || !simulationId) {
      return;
    }
    setTownHallError(null);
    try {
      const response = await generateTownHallQuestion(simulationId, townHallQuestion.citizenId, "voice");
      const generatedQuestion = {
        ...townHallQuestion,
        question: response.question_turn.text,
        cue: response.cue || townHallQuestion.cue,
      } satisfies TownHallQuestion;
      setLiveTownHallQuestion(generatedQuestion);
      onSimulationSync(response.simulation);
      await handlePlayTownHallQuestion(generatedQuestion);
      await voiceDockRef?.current?.injectContextTurn(
        `Audience question from ${generatedQuestion.displayName}, ${generatedQuestion.role} in ${generatedQuestion.region}: ${generatedQuestion.question}`,
      );
      await voiceDockRef?.current?.requestAssistantReply(
        "An audience member just asked that question in the auditorium. Answer directly as the opposing candidate in one or two brief sentences, then stop so the player can answer too.",
      );
    } catch (caught) {
      setTownHallError(caught instanceof Error ? caught.message : "Town hall question failed");
    }
  }

  return (
    <section className="debate-room">
      <header className="debate-room__stage">
        <div className="debate-room__plate">
          <span>{auditoriumMode === "town_hall" ? "National auditorium · town hall" : "National auditorium"}</span>
          <strong>{stage.phase_label}</strong>
        </div>
        <p>{ballotLine}</p>
      </header>

      <div className="debate-room__body">
        <VoiceDock
          ref={voiceDockRef}
          simulationId={simulationId}
          role="debate"
          auditoriumMode={auditoriumMode}
          sessionAuditoriumMode="debate"
          title="Live debate channel"
          blurb={
            auditoriumMode === "town_hall"
              ? "Call on a voter, let their question land in the room, then keep the same debate moving."
              : "Speak your case and the opposing candidate answers in the same room thread. Use the main mic in the scene or type here; when you are satisfied with the exchange, call the election."
          }
          turns={debateTurns}
          metaChips={[
            stage.phase_label,
            auditoriumMode === "town_hall" ? "audience question live" : "opponent live",
            "shared debate thread",
          ]}
          onSimulationSync={onSimulationSync}
          onPresenceChange={onPresenceChange}
        />

        <div className="debate-room__compose">
          <div className="debate-room__context">
            <article>
              <span>Crowd line</span>
              <p>{ballotLine}</p>
            </article>
            <article>
              <span>Your platform</span>
              {platformNotes.map((note) => (
                <p key={note}>{note}</p>
              ))}
            </article>
            <article>
              <span>Latest line</span>
              <p>{latestPlayerTurn ?? "Use the scene mic or type in the debate channel to start making your case."}</p>
            </article>
          </div>

          <div className="debate-room__actions">
            <button className={`btn ${auditoriumMode === "town_hall" ? "btn--secondary" : "btn--ghost"}`} onClick={onToggleTownHall}>
              {auditoriumMode === "town_hall" ? "Return to debate" : "Open town hall Q&A"}
            </button>
            <button
              className="btn btn--primary"
              onClick={() => onResolve(resolvedPlatform, latestPlayerTurn ?? "")}
              disabled={pending || !resolvedPlatform.trim()}
            >
              {pending ? "Resolving..." : "Call election and advance"}
            </button>
            <p className="debate-room__callout">
              {auditoriumMode === "town_hall"
                ? "Use the audience questions to pressure-test your case, then return to debate or call the election when the room feels settled."
                : "Keep debating as long as you want. Call the election only when you are ready to lock the public choice and move to the next stage."}
            </p>
          </div>

          {auditoriumMode === "town_hall" && visibleTownHallQuestion ? (
            <section className="debate-room__townhall">
              <header className="debate-room__townhall-head">
                <div>
                  <span>Current audience question</span>
                  <strong>{visibleTownHallQuestion.displayName} · {visibleTownHallQuestion.role}</strong>
                  <p>{visibleTownHallQuestion.region} · {visibleTownHallQuestion.supportLabel} · {visibleTownHallQuestion.aiExposure}</p>
                </div>
                <div className="debate-room__townhall-actions">
                  <button className="btn btn--secondary" onClick={() => setTownHallIndex((current) => (current + 1) % townHallQuestions.length)} disabled={townHallQuestions.length <= 1}>
                    Next voter
                  </button>
                  <button className="btn btn--ghost" onClick={() => void handlePlayTownHallQuestion()} disabled={townHallPlaying}>
                    {townHallPlaying ? "Playing..." : "Replay audio"}
                  </button>
                  <button className="btn btn--primary" onClick={() => void handleAskTownHallQuestion()} disabled={!simulationId}>
                    Call on voter
                  </button>
                </div>
              </header>
              <article className="debate-room__townhall-card">
                {liveTownHallQuestion ? (
                  <>
                    <h4>{liveTownHallQuestion.question}</h4>
                    <p>{liveTownHallQuestion.cue}</p>
                  </>
                ) : (
                  <>
                    <h4>Call on this voter to hear their live question.</h4>
                    <p>{visibleTownHallQuestion.cue}</p>
                  </>
                )}
              </article>
              <div className="debate-room__townhall-strip">
                {townHallQuestions.slice(0, 4).map((item, index) => (
                  <button
                    key={item.id}
                    className={`debate-room__townhall-chip ${item.id === visibleTownHallQuestion.id ? "debate-room__townhall-chip--active" : ""}`}
                    onClick={() => setTownHallIndex(index)}
                  >
                    <strong>{item.displayName}</strong>
                    <span>{item.role}</span>
                  </button>
                ))}
              </div>
              {townHallError ? <p className="voice-dock__error">{townHallError}</p> : null}
            </section>
          ) : null}

          {stage.debate_reply ? (
            <div className="debate-room__reply">
              <article>
                <span>Opponent opening</span>
                <p>{stage.debate_reply.opponent_opening}</p>
              </article>
              <article>
                <span>Opponent rebuttal</span>
                <p>{stage.debate_reply.opponent_rebuttal}</p>
              </article>
              <article>
                <span>Analyst take</span>
                <p>{stage.debate_reply.analyst_take}</p>
              </article>
            </div>
          ) : null}
        </div>
      </div>
    </section>
  );
}
