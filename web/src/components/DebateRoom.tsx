import { useEffect, useMemo, useRef, useState } from "react";
import type { RefObject } from "react";
import { VoiceDock, type VoiceDockHandle } from "./VoiceDock";
import { generateTownHallQuestion, synthesizeSpeech } from "../lib/api";
import { stagePolicyAxes, stageRoomBrief } from "../lib/stageText";
import { buildTownHallQuestions, type TownHallQuestion } from "../lib/townHall";
import type { AdvisorMode, AuditoriumMode, ConversationTurn, RoomName, ScenePresence, SimulationState, StagePackage } from "../types";

interface DebateRoomProps {
  simulationId?: string;
  stage: StagePackage;
  debateTurns: ConversationTurn[];
  auditoriumTurns?: ConversationTurn[];
  auditoriumMode: AuditoriumMode;
  resolvedPlatform: string;
  pending: boolean;
  onResolve: (playerPlatform: string, playerRebuttal: string) => Promise<void>;
  onToggleTownHall: () => void;
  onSimulationSync: (simulation: SimulationState) => void;
  onPresenceChange?: (presence: ScenePresence) => void;
  onModeCommand?: (command: {
    room?: RoomName;
    advisorMode?: AdvisorMode;
    auditoriumMode?: AuditoriumMode;
    citizenName?: string;
  }) => Promise<boolean> | boolean;
  voiceDockRef?: RefObject<VoiceDockHandle | null>;
}

function formatBallotLine(stage: StagePackage) {
  const voteQuestion = stage.poll_summaries.find((summary) => {
    const question = summary.question.toLowerCase();
    return question.includes("election") && question.includes("today");
  });
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
  auditoriumTurns,
  auditoriumMode,
  resolvedPlatform,
  pending,
  onResolve,
  onToggleTownHall,
  onSimulationSync,
  onPresenceChange,
  onModeCommand,
  voiceDockRef,
}: DebateRoomProps) {
  type TownHallPhase = "idle" | "generating" | "voter_speaking" | "player_turn" | "opponent_turn";
  const ballotLine = useMemo(() => formatBallotLine(stage), [stage]);
  const contextTurns = auditoriumTurns && auditoriumTurns.length > 0 ? auditoriumTurns : debateTurns;
  const playerTurns = useMemo(
    () => contextTurns.filter((turn) => turn.speaker === "user").map((turn) => turn.text.trim()).filter(Boolean),
    [contextTurns],
  );
  const playerCase = useMemo(() => playerTurns.join("\n\n"), [playerTurns]);
  const latestPlayerTurn = playerTurns.at(-1);
  const platformNotes = stagePolicyAxes(stage, 4);
  const roomBrief = stageRoomBrief(stage);
  const townHallQuestions = useMemo(() => buildTownHallQuestions(stage, contextTurns), [contextTurns, stage]);
  const [townHallIndex, setTownHallIndex] = useState(0);
  const [townHallPlaying, setTownHallPlaying] = useState(false);
  const [townHallError, setTownHallError] = useState<string | null>(null);
  const [liveTownHallQuestion, setLiveTownHallQuestion] = useState<TownHallQuestion | null>(null);
  const [townHallPhase, setTownHallPhase] = useState<TownHallPhase>("idle");
  const [activeTownHallTurnId, setActiveTownHallTurnId] = useState<string | null>(null);
  const townHallAudioRef = useRef<HTMLAudioElement | null>(null);
  const townHallAbortRef = useRef<AbortController | null>(null);
  const townHallQuestion = townHallQuestions[townHallIndex] ?? townHallQuestions[0];
  const visibleTownHallQuestion = liveTownHallQuestion ?? townHallQuestion;
  const liveSessionAuditoriumMode = auditoriumMode;
  const activeTownHallTurnIndex = useMemo(
    () => (activeTownHallTurnId ? contextTurns.findIndex((turn) => turn.id === activeTownHallTurnId) : -1),
    [activeTownHallTurnId, contextTurns],
  );
  const playerAnsweredCurrentTownHallQuestion = useMemo(() => {
    if (activeTownHallTurnIndex < 0) {
      return false;
    }
    return contextTurns.slice(activeTownHallTurnIndex + 1).some((turn) => turn.speaker === "user");
  }, [activeTownHallTurnIndex, contextTurns]);
  const opponentAnsweredCurrentTownHallQuestion = useMemo(() => {
    if (activeTownHallTurnIndex < 0) {
      return false;
    }
    const turnsAfterQuestion = contextTurns.slice(activeTownHallTurnIndex + 1);
    const playerTurnIndex = turnsAfterQuestion.findIndex((turn) => turn.speaker === "user");
    if (playerTurnIndex < 0) {
      return false;
    }
    return turnsAfterQuestion.slice(playerTurnIndex + 1).some((turn) => turn.speaker === "assistant");
  }, [activeTownHallTurnIndex, contextTurns]);

  useEffect(() => {
    if (townHallIndex < townHallQuestions.length) {
      return;
    }
    setTownHallIndex(0);
  }, [townHallIndex, townHallQuestions.length]);

  useEffect(() => {
    setLiveTownHallQuestion(null);
    setActiveTownHallTurnId(null);
    setTownHallPhase("idle");
  }, [townHallIndex]);

  useEffect(() => {
    if (auditoriumMode !== "town_hall") {
      setTownHallPhase("idle");
    }
  }, [auditoriumMode]);

  useEffect(() => {
    if (townHallPhase === "opponent_turn" && opponentAnsweredCurrentTownHallQuestion) {
      setTownHallPhase("idle");
    }
  }, [opponentAnsweredCurrentTownHallQuestion, townHallPhase]);

  useEffect(() => () => {
    stopTownHallPlayback(true);
  }, []);

  function stopTownHallPlayback(removeNode = false) {
    townHallAbortRef.current?.abort();
    townHallAbortRef.current = null;
    const audio = townHallAudioRef.current;
    if (audio) {
      audio.pause();
      audio.src = "";
      if (removeNode) {
        audio.remove();
        townHallAudioRef.current = null;
      }
    }
    setTownHallPlaying(false);
  }

  function handleNextTownHallVoice() {
    if (townHallQuestions.length <= 1) {
      return;
    }
    stopTownHallPlayback();
    setTownHallError(null);
    setLiveTownHallQuestion(null);
    setActiveTownHallTurnId(null);
    setTownHallPhase("idle");
    setTownHallIndex((current) => (current + 1) % townHallQuestions.length);
  }

  async function handlePlayTownHallQuestion(question = visibleTownHallQuestion) {
    if (!question || !question.question.trim()) {
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
    setTownHallPhase("generating");
    setActiveTownHallTurnId(null);
    try {
      const response = await generateTownHallQuestion(simulationId, townHallQuestion.citizenId, "voice");
      const generatedQuestion = {
        ...townHallQuestion,
        question: response.question_turn.text,
        cue: response.cue || townHallQuestion.cue,
      } satisfies TownHallQuestion;
      setLiveTownHallQuestion(generatedQuestion);
      setActiveTownHallTurnId(response.question_turn.id);
      onSimulationSync(response.simulation);
      setTownHallPhase("voter_speaking");
      await handlePlayTownHallQuestion(generatedQuestion);
      setTownHallPhase("player_turn");
      voiceDockRef?.current?.focusComposer();
    } catch (caught) {
      setTownHallPhase("idle");
      setTownHallError(caught instanceof Error ? caught.message : "Town hall question failed");
    }
  }

  async function handleRequestOpponentReply() {
    if (!visibleTownHallQuestion) {
      return;
    }
    setTownHallError(null);
    setTownHallPhase("opponent_turn");
    try {
      await new Promise((resolve) => window.setTimeout(resolve, 90));
      const requested = await voiceDockRef?.current?.requestAssistantReply(
        `Switch from audience-floor mode to opposing-candidate mode for one brief rebuttal. The same voter just asked: ${visibleTownHallQuestion.question} The player has now answered. Reply as the opposing candidate on stage, answer that voter's concern directly, make one real contrast, and keep it brief.`,
      );
      if (!requested) {
        setTownHallPhase("player_turn");
        setTownHallError("The opponent channel was not ready to answer yet.");
      }
    } catch (caught) {
      setTownHallPhase("player_turn");
      setTownHallError(caught instanceof Error ? caught.message : "Opponent reply failed");
    }
  }

  const townHallStatus = useMemo(() => {
    if (townHallPhase === "generating") {
      return {
        label: "Finding the next voice",
        detail: "A voter is stepping up to the microphone now.",
      };
    }
    if (townHallPhase === "voter_speaking" || townHallPlaying) {
      return {
        label: "Audience mic is live",
        detail: "Let the question land, then answer that person directly.",
      };
    }
    if (activeTownHallTurnId && !playerAnsweredCurrentTownHallQuestion) {
      return {
        label: "You have the floor",
        detail: "Answer them in the main mic before you move on.",
      };
    }
    if (townHallPhase === "opponent_turn") {
      return {
        label: "Rival rebuttal",
        detail: "The other candidate is taking one short answer in front of the same crowd.",
      };
    }
    if (activeTownHallTurnId && playerAnsweredCurrentTownHallQuestion) {
      return {
        label: "Answer landed",
        detail: "You answered that voter. Call on another person or let the rival answer too.",
      };
    }
    return {
      label: "Open the floor",
      detail: stage.main_split
        ? `Call on a voter and pressure-test your case on ${stage.main_split.toLowerCase()}.`
        : "Call on a voter, hear the question out loud, then answer in the same debate thread.",
    };
  }, [activeTownHallTurnId, playerAnsweredCurrentTownHallQuestion, stage.main_split, townHallPhase, townHallPlaying]);
  const crowdQueue = useMemo(
    () =>
      townHallQuestions
        .filter((item) => item.id !== visibleTownHallQuestion?.id)
        .slice(0, 3)
        .map((item) => `${item.displayName}, ${item.role}`),
    [townHallQuestions, visibleTownHallQuestion?.id],
  );

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
          sessionAuditoriumMode={liveSessionAuditoriumMode}
          autoResponse={auditoriumMode !== "town_hall"}
          title={auditoriumMode === "town_hall" ? `Town hall · ${stage.phase_label}` : `Debate stage · ${stage.phase_label}`}
          blurb={
            auditoriumMode === "town_hall"
              ? (stage.main_split || roomBrief)
              : (stage.debate_reply?.analyst_take || stage.main_split || roomBrief)
          }
          draftPlaceholder={
            auditoriumMode === "town_hall"
              ? `Answer ${visibleTownHallQuestion?.displayName ?? "the voter"} directly, then decide whether you want a rebuttal.`
              : `Make one clear case about ${stage.main_split || "the live split"} and challenge the rival on one concrete consequence.`
          }
          emptyStateText={
            auditoriumMode === "town_hall"
              ? "Call on a voter when you want the room to test your case."
              : "The debate turns live as soon as you make the case."
          }
          turns={debateTurns}
          metaChips={[
            stage.phase_label,
            auditoriumMode === "town_hall" ? "audience first" : "opponent live",
            "shared debate thread",
          ]}
          onSimulationSync={onSimulationSync}
          onPresenceChange={onPresenceChange}
          onModeCommand={onModeCommand}
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
            <div className="topbar__mode-group">
              <span className="topbar__mode-label">Auditorium mode</span>
              <div className="topbar__mode-switch" role="tablist" aria-label="Auditorium mode">
                <button
                  className={`topbar__mode-option ${auditoriumMode === "debate" ? "topbar__mode-option--active" : ""}`}
                  data-testid="auditorium-tab-debate"
                  onClick={() => {
                    if (auditoriumMode !== "debate") {
                      onToggleTownHall();
                    }
                  }}
                  role="tab"
                  aria-selected={auditoriumMode === "debate"}
                >
                  Debate stage
                </button>
                <button
                  className={`topbar__mode-option ${auditoriumMode === "town_hall" ? "topbar__mode-option--active" : ""}`}
                  data-testid="auditorium-tab-town-hall"
                  onClick={() => {
                    if (auditoriumMode !== "town_hall") {
                      onToggleTownHall();
                    }
                  }}
                  role="tab"
                  aria-selected={auditoriumMode === "town_hall"}
                >
                  Town hall floor
                </button>
              </div>
            </div>
            <button
              className="btn btn--primary"
              onClick={() => onResolve(resolvedPlatform, latestPlayerTurn ?? "")}
              disabled={pending || !resolvedPlatform.trim()}
            >
              {pending ? "Resolving..." : "Call election and advance"}
            </button>
            <p className="debate-room__callout">
              {auditoriumMode === "town_hall"
                ? (stage.main_split
                    ? `Use the audience floor to pressure-test your case on ${stage.main_split.toLowerCase()}. Let one voter land, answer them directly, then decide whether the rival gets a rebuttal.`
                    : "Use the audience floor to pressure-test your case. Let one voter land, answer them directly, then decide whether the rival gets a rebuttal.")
                : (stage.debate_reply?.analyst_take || "Keep debating as long as you want. Call the election only when you are ready to lock the public choice and move to the next stage.")}
            </p>
          </div>

          {auditoriumMode === "town_hall" && visibleTownHallQuestion ? (
            <section className="debate-room__audience-floor">
              <header className="debate-room__audience-floor-head">
                <div className="debate-room__audience-floor-copy">
                  <span>{townHallStatus.label}</span>
                  <strong>{visibleTownHallQuestion.displayName} has the next audience mic</strong>
                  <p>{visibleTownHallQuestion.role} · {visibleTownHallQuestion.region} · {visibleTownHallQuestion.supportLabel}</p>
                </div>
                <div className="debate-room__audience-floor-actions">
                  <button
                    className="btn btn--secondary"
                    onClick={handleNextTownHallVoice}
                    disabled={townHallQuestions.length <= 1 || townHallPhase === "generating" || townHallPhase === "voter_speaking"}
                  >
                    Another voice
                  </button>
                  <button
                    className="btn btn--primary"
                    data-testid="townhall-call-on-voter"
                    onClick={() => (liveTownHallQuestion ? void handlePlayTownHallQuestion() : void handleAskTownHallQuestion())}
                    disabled={!simulationId || townHallPhase === "generating" || townHallPhase === "voter_speaking"}
                  >
                    {townHallPhase === "generating"
                      ? "Opening the floor..."
                      : townHallPlaying
                        ? "Question live..."
                        : liveTownHallQuestion
                          ? "Replay question"
                          : `Give ${visibleTownHallQuestion.displayName.split(" ")[0]} the mic`}
                  </button>
                  {playerAnsweredCurrentTownHallQuestion ? (
                    <button
                      className="btn btn--ghost"
                      data-testid="townhall-opponent-reply"
                      onClick={() => void handleRequestOpponentReply()}
                      disabled={townHallPhase === "opponent_turn"}
                    >
                      {townHallPhase === "opponent_turn" ? "Rival answering..." : "Let rival answer too"}
                    </button>
                  ) : null}
                </div>
              </header>

              <div className="debate-room__audience-floor-strip">
                <article className="debate-room__audience-floor-note">
                  <span>What this voter wants</span>
                  <p>{liveTownHallQuestion?.cue || visibleTownHallQuestion.cue || townHallStatus.detail}</p>
                </article>
                <article className="debate-room__audience-floor-note">
                  <span>{liveTownHallQuestion ? "Live question" : "How town hall works"}</span>
                  <p>
                    {liveTownHallQuestion
                      ? liveTownHallQuestion.question
                      : "Call on one person from the crowd, hear the question out loud, answer them in the same thread, then decide whether the rival gets a rebuttal."}
                  </p>
                </article>
              </div>

              {crowdQueue.length > 0 ? (
                <p className="debate-room__audience-floor-queue">Still waiting in the crowd: {crowdQueue.join(" · ")}</p>
              ) : null}
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
