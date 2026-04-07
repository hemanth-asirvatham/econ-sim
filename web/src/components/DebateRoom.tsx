import { forwardRef, useEffect, useEffectEvent, useImperativeHandle, useMemo, useRef, useState } from "react";
import type { RefObject } from "react";
import { VoiceDock, type VoiceDockHandle } from "./VoiceDock";
import { generateTownHallOpponentReply, generateTownHallQuestion, speechStreamUrl } from "../lib/api";
import { stagePolicyAxes, stageRoomBrief } from "../lib/stageText";
import { buildTownHallQuestions, type TownHallQuestion } from "../lib/townHall";
import type { AdvisorMode, AuditoriumMode, ConversationTurn, RoomName, ScenePresence, SimulationState, StagePackage } from "../types";

type TownHallPhase = "idle" | "generating" | "voter_speaking" | "player_turn" | "opponent_turn";
const SILENT_AUDIO_DATA_URI = "data:audio/wav;base64,UklGRiQAAABXQVZFZm10IBAAAAABAAEAESsAACJWAAACABAAZGF0YQAAAAA=";

export interface TownHallSceneState {
  phase: TownHallPhase;
  label: string;
  detail: string;
  question: TownHallQuestion | null;
  activeTurnId: string | null;
  awaitingPlayer: boolean;
  readyForNextQuestion: boolean;
  playerAnswered: boolean;
  opponentAnswered: boolean;
  playing: boolean;
  error: string | null;
}

export interface DebateRoomHandle {
  askTownHallQuestion: () => Promise<void>;
  primeTownHallAudio: () => Promise<void>;
  replayTownHallQuestion: () => Promise<void>;
  requestOpponentReply: () => Promise<void>;
  focusComposer: () => void;
}

interface DebateRoomProps {
  simulationId?: string;
  themeMode?: "light" | "dark";
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
  onTownHallStateChange?: (state: TownHallSceneState | null) => void;
  townHallLaunchNonce?: number;
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

async function settleAudioStart(audio: HTMLMediaElement, playPromise: Promise<unknown>, timeoutMs = 1200) {
  let started = !audio.paused && !audio.ended;
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
        audio.removeEventListener("play", handleStarted);
        audio.removeEventListener("playing", handleStarted);
        audio.removeEventListener("timeupdate", handleStarted);
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
      audio.addEventListener("play", handleStarted, { once: true });
      audio.addEventListener("playing", handleStarted, { once: true });
      audio.addEventListener("timeupdate", handleStarted, { once: true });
    }),
  ]);
  return started || (!audio.paused && !audio.ended);
}

function guardedPlay(audio: HTMLMediaElement) {
  const playPromise = audio.play();
  void playPromise.catch(() => undefined);
  return playPromise;
}

function waitForAudioCompletion(audio: HTMLAudioElement, fallbackMs = 16000) {
  return new Promise<"ended" | "error">((resolve) => {
    const cleanup = () => {
      audio.removeEventListener("ended", handleEnded);
      audio.removeEventListener("error", handleError);
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
      if (audio.error) {
        handleError();
        return;
      }
      if (
        audio.ended ||
        (
          Number.isFinite(audio.duration) &&
          audio.duration > 0 &&
          audio.currentTime >= audio.duration - 0.06
        )
      ) {
        handleEnded();
      }
    }, 240);
    const timeoutTimer = window.setTimeout(() => {
      cleanup();
      resolve("ended");
    }, fallbackMs);
    audio.addEventListener("ended", handleEnded, { once: true });
    audio.addEventListener("error", handleError, { once: true });
  });
}

function spokenQuestionDurationMs(question: string) {
  const words = question.trim().split(/\s+/).filter(Boolean).length;
  return Math.max(4200, Math.min(18000, 1400 + words * 320));
}

function spokenReplyDurationMs(text: string) {
  const words = text.trim().split(/\s+/).filter(Boolean).length;
  return Math.max(3600, Math.min(14000, 1000 + words * 280));
}

export const DebateRoom = forwardRef<DebateRoomHandle, DebateRoomProps>(function DebateRoom({
  simulationId,
  themeMode = "dark",
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
  onTownHallStateChange,
  townHallLaunchNonce = 0,
  voiceDockRef,
}, ref) {
  const ballotLine = useMemo(() => formatBallotLine(stage), [stage]);
  const contextTurns = auditoriumTurns && auditoriumTurns.length > 0 ? auditoriumTurns : debateTurns;
  const playerTurns = useMemo(
    () => contextTurns.filter((turn) => turn.speaker === "user").map((turn) => turn.text.trim()).filter(Boolean),
    [contextTurns],
  );
  const playerCase = useMemo(() => playerTurns.join("\n\n"), [playerTurns]);
  const latestPlayerTurn = playerTurns.at(-1);
  const handleVoiceDockPresenceChange = useEffectEvent((presence: ScenePresence) => {
    if (auditoriumMode === "town_hall" && townHallPlaying && presence.playerActivity === "speaking") {
      stopTownHallPlayback();
    }
    onPresenceChange?.(presence);
  });
  const platformNotes = stagePolicyAxes(stage, 4);
  const roomBrief = stageRoomBrief(stage);
  const townHallQuestions = useMemo(() => buildTownHallQuestions(stage, contextTurns), [contextTurns, stage]);
  const [townHallIndex, setTownHallIndex] = useState(0);
  const [townHallPlaying, setTownHallPlaying] = useState(false);
  const [townHallError, setTownHallError] = useState<string | null>(null);
  const [liveTownHallQuestion, setLiveTownHallQuestion] = useState<TownHallQuestion | null>(null);
  const [townHallPhase, setTownHallPhase] = useState<TownHallPhase>("idle");
  const [activeTownHallTurnId, setActiveTownHallTurnId] = useState<string | null>(null);
  const [townHallTurnBaseline, setTownHallTurnBaseline] = useState<number | null>(null);
  const townHallAudioRef = useRef<HTMLAudioElement | null>(null);
  const townHallAudioUnlockedRef = useRef(false);
  const lastTownHallLaunchRef = useRef(0);
  const townHallLaunchInFlightRef = useRef(false);
  const townHallOpponentReplyInFlightRef = useRef(false);
  const lastAutoOpponentReplyKeyRef = useRef<string | null>(null);
  const townHallQuestion = townHallQuestions[townHallIndex] ?? townHallQuestions[0];
  const visibleTownHallQuestion = liveTownHallQuestion ?? townHallQuestion ?? null;
  const liveSessionAuditoriumMode = auditoriumMode;
  const activeTownHallTurnIndex = useMemo(
    () => (activeTownHallTurnId ? contextTurns.findIndex((turn) => turn.id === activeTownHallTurnId) : -1),
    [activeTownHallTurnId, contextTurns],
  );
  const playerAnsweredCurrentTownHallQuestion = useMemo(() => {
    if (townHallTurnBaseline !== null) {
      return contextTurns.slice(townHallTurnBaseline).some((turn) => turn.speaker === "user");
    }
    if (activeTownHallTurnIndex < 0) {
      return false;
    }
    return contextTurns.slice(activeTownHallTurnIndex + 1).some((turn) => turn.speaker === "user");
  }, [activeTownHallTurnIndex, contextTurns, townHallTurnBaseline]);
  const opponentAnsweredCurrentTownHallQuestion = useMemo(() => {
    if (townHallTurnBaseline !== null) {
      const turnsAfterQuestion = contextTurns.slice(townHallTurnBaseline);
      const playerTurnIndex = turnsAfterQuestion.findIndex((turn) => turn.speaker === "user");
      if (playerTurnIndex < 0) {
        return false;
      }
      return turnsAfterQuestion.slice(playerTurnIndex + 1).some((turn) => turn.speaker === "assistant");
    }
    if (activeTownHallTurnIndex < 0) {
      return false;
    }
    const turnsAfterQuestion = contextTurns.slice(activeTownHallTurnIndex + 1);
    const playerTurnIndex = turnsAfterQuestion.findIndex((turn) => turn.speaker === "user");
    if (playerTurnIndex < 0) {
      return false;
    }
    return turnsAfterQuestion.slice(playerTurnIndex + 1).some((turn) => turn.speaker === "assistant");
  }, [activeTownHallTurnIndex, contextTurns, townHallTurnBaseline]);
  const awaitingPlayerAnswer =
    Boolean(visibleTownHallQuestion) &&
    (
      townHallPhase === "player_turn" ||
      (Boolean(activeTownHallTurnId) && !playerAnsweredCurrentTownHallQuestion)
    );
  const readyForNextQuestion =
    townHallPhase !== "generating" &&
    !townHallPlaying &&
    !awaitingPlayerAnswer;
  const sceneTownHallQuestion =
    townHallPhase === "idle" && !activeTownHallTurnId && !liveTownHallQuestion
      ? null
      : visibleTownHallQuestion;

  useEffect(() => {
    stopTownHallPlayback();
    setTownHallIndex(0);
    setLiveTownHallQuestion(null);
    setActiveTownHallTurnId(null);
    setTownHallTurnBaseline(null);
    setTownHallPhase("idle");
    setTownHallError(null);
    lastTownHallLaunchRef.current = 0;
  }, [simulationId, stage.index]);

  useEffect(() => {
    if (townHallIndex < townHallQuestions.length) {
      return;
    }
    setTownHallIndex(0);
  }, [townHallIndex, townHallQuestions.length]);

  useEffect(() => {
    setLiveTownHallQuestion(null);
    setActiveTownHallTurnId(null);
    setTownHallTurnBaseline(null);
    setTownHallPhase("idle");
  }, [townHallIndex]);

  useEffect(() => {
    if (auditoriumMode !== "town_hall") {
      setTownHallPhase("idle");
      setTownHallTurnBaseline(null);
    }
  }, [auditoriumMode]);

  useEffect(() => {
    if (auditoriumMode !== "town_hall") {
      return;
    }
    if (!simulationId || townHallLaunchNonce <= 0 || lastTownHallLaunchRef.current === townHallLaunchNonce) {
      return;
    }
    lastTownHallLaunchRef.current = townHallLaunchNonce;
    void handleAskTownHallQuestion();
  }, [auditoriumMode, handleAskTownHallQuestion, simulationId, townHallLaunchNonce]);

  useEffect(() => {
    if (townHallPhase === "opponent_turn" && opponentAnsweredCurrentTownHallQuestion) {
      setTownHallPhase("idle");
    }
  }, [opponentAnsweredCurrentTownHallQuestion, townHallPhase]);

  useEffect(() => {
    if (
      auditoriumMode !== "town_hall" ||
      !simulationId ||
      !visibleTownHallQuestion ||
      !playerAnsweredCurrentTownHallQuestion ||
      opponentAnsweredCurrentTownHallQuestion ||
      townHallPhase !== "player_turn" ||
      townHallPlaying ||
      townHallOpponentReplyInFlightRef.current
    ) {
      return;
    }
    const replyKey = `${activeTownHallTurnId ?? visibleTownHallQuestion.id}:${contextTurns.length}`;
    if (lastAutoOpponentReplyKeyRef.current === replyKey) {
      return;
    }
    lastAutoOpponentReplyKeyRef.current = replyKey;
    const timer = window.setTimeout(() => {
      void handleRequestOpponentReply();
    }, 900);
    return () => window.clearTimeout(timer);
  }, [
    activeTownHallTurnId,
    auditoriumMode,
    contextTurns.length,
    opponentAnsweredCurrentTownHallQuestion,
    playerAnsweredCurrentTownHallQuestion,
    simulationId,
    townHallPhase,
    townHallPlaying,
    visibleTownHallQuestion,
  ]);

  useEffect(() => () => {
    stopTownHallPlayback(true);
  }, []);

  function stopTownHallPlayback(removeNode = false) {
    const audio = townHallAudioRef.current;
    if (audio) {
      audio.pause();
      audio.src = "";
      audio.removeAttribute("src");
      audio.load();
      if (removeNode) {
        audio.remove();
        townHallAudioRef.current = null;
      }
    }
    setTownHallPlaying(false);
  }

  async function primeTownHallAudio() {
    if (townHallAudioUnlockedRef.current) {
      return;
    }
    const audio = townHallAudioRef.current ?? document.createElement("audio");
    if (!townHallAudioRef.current) {
      audio.autoplay = false;
      audio.setAttribute("playsinline", "true");
      audio.style.display = "none";
      document.body.appendChild(audio);
      townHallAudioRef.current = audio;
    }
    try {
      audio.src = SILENT_AUDIO_DATA_URI;
      audio.currentTime = 0;
      await settleAudioStart(audio, guardedPlay(audio), 900);
      audio.pause();
      audio.currentTime = 0;
      audio.removeAttribute("src");
      audio.load();
      townHallAudioUnlockedRef.current = true;
    } catch {
      audio.removeAttribute("src");
      audio.load();
    }
  }

  async function playTownHallAudio(text: string, voice: string, fallbackDurationMs: number, errorMessage: string) {
    if (!text.trim()) {
      return false;
    }
    setTownHallPlaying(true);
    setTownHallError(null);
    try {
      const audio = townHallAudioRef.current ?? document.createElement("audio");
      if (!townHallAudioRef.current) {
        audio.autoplay = true;
        audio.setAttribute("playsinline", "true");
        audio.style.display = "none";
        document.body.appendChild(audio);
        townHallAudioRef.current = audio;
      }
      audio.src = speechStreamUrl(text, voice);
      audio.load();
      const started = await settleAudioStart(audio, guardedPlay(audio), 1600);
      if (!started) {
        throw new Error(`${errorMessage}. Audio did not start.`);
      }
      const result = await waitForAudioCompletion(audio, fallbackDurationMs + 7000);
      if (result !== "ended") {
        throw new Error(`${errorMessage}. Audio playback errored.`);
      }
      return true;
    } catch (caught) {
      setTownHallError(caught instanceof Error ? caught.message : errorMessage);
      return false;
    } finally {
      const audio = townHallAudioRef.current;
      audio?.removeAttribute("src");
      audio?.load();
      setTownHallPlaying(false);
    }
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
      return false;
    }
    return await playTownHallAudio(
      question.question,
      question.voice,
      spokenQuestionDurationMs(question.question),
      "Town hall question playback failed",
    );
  }

  async function playTownHallReply(text: string, voice: string) {
    return await playTownHallAudio(text, voice, spokenReplyDurationMs(text), "Town hall reply playback failed");
  }

  async function handleReplayTownHallQuestion() {
    if (!visibleTownHallQuestion) {
      return;
    }
    setTownHallPhase("voter_speaking");
    const played = await handlePlayTownHallQuestion(visibleTownHallQuestion);
    setTownHallPhase(played ? "player_turn" : "idle");
    if (played) {
      voiceDockRef?.current?.focusComposer();
    }
  }

  async function handleAskTownHallQuestion() {
    if (!townHallQuestion || !simulationId) {
      return;
    }
    if (townHallLaunchInFlightRef.current) {
      return;
    }
    townHallLaunchInFlightRef.current = true;
    await primeTownHallAudio();
    setTownHallError(null);
    setLiveTownHallQuestion(null);
    setTownHallPhase("generating");
    setActiveTownHallTurnId(null);
    setTownHallTurnBaseline(contextTurns.length);
    try {
      const response = await generateTownHallQuestion(simulationId, townHallQuestion.citizenId, "voice");
      const generatedQuestion = {
        ...townHallQuestion,
        question: response.question_turn.text,
        cue: response.cue || townHallQuestion.cue,
        voice: response.question_turn.speaker_voice || townHallQuestion.voice,
      } satisfies TownHallQuestion;
      setLiveTownHallQuestion(generatedQuestion);
      setActiveTownHallTurnId(response.question_turn.id);
      onSimulationSync(response.simulation);
      setTownHallPhase("voter_speaking");
      const played = await handlePlayTownHallQuestion(generatedQuestion);
      setTownHallPhase(played ? "player_turn" : "idle");
      if (played) {
        voiceDockRef?.current?.focusComposer();
      }
    } catch (caught) {
      const fallbackQuestion = visibleTownHallQuestion ?? townHallQuestion;
      if (!fallbackQuestion) {
        setTownHallPhase("idle");
        setTownHallError(caught instanceof Error ? caught.message : "Town hall question failed");
        return;
      }
      setTownHallError(caught instanceof Error ? caught.message : "Using the prepared voter question instead.");
      void (async () => {
        try {
          await voiceDockRef?.current?.addTurn({
            speaker: "assistant",
            speaker_name: fallbackQuestion.displayName,
            speaker_voice: fallbackQuestion.voice,
            text: fallbackQuestion.question,
            mode: "voice",
          });
        } catch {
          // If thread persistence fails here, still surface the fallback question locally.
        }
      })();
      setLiveTownHallQuestion(fallbackQuestion);
      setActiveTownHallTurnId(`fallback-${fallbackQuestion.id}`);
      setTownHallPhase("voter_speaking");
      const played = await handlePlayTownHallQuestion(fallbackQuestion);
      setTownHallPhase(played ? "player_turn" : "idle");
    } finally {
      townHallLaunchInFlightRef.current = false;
    }
  }

  async function handleRequestOpponentReply() {
    if (!visibleTownHallQuestion || !simulationId) {
      return;
    }
    if (townHallOpponentReplyInFlightRef.current || opponentAnsweredCurrentTownHallQuestion) {
      return;
    }
    townHallOpponentReplyInFlightRef.current = true;
    setTownHallError(null);
    setTownHallPhase("opponent_turn");
    try {
      const response = await generateTownHallOpponentReply(
        simulationId,
        visibleTownHallQuestion.citizenId,
        visibleTownHallQuestion.question,
        "voice",
      );
      onSimulationSync(response.simulation);
      await playTownHallReply(
        response.reply_turn.text,
        response.reply_turn.speaker_voice || "ash",
      );
      setTownHallPhase("player_turn");
    } catch (caught) {
      setTownHallPhase("player_turn");
      setTownHallError(caught instanceof Error ? caught.message : "Opponent reply failed");
    } finally {
      townHallOpponentReplyInFlightRef.current = false;
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
    if (awaitingPlayerAnswer) {
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
    if (playerAnsweredCurrentTownHallQuestion) {
      return {
        label: "Answer landed",
        detail: "You answered that voter. Call on another person or let the rival answer too.",
      };
    }
    return {
      label: "Open the floor",
      detail: "Call on one voter, hear the question out loud, then answer them directly.",
    };
  }, [awaitingPlayerAnswer, playerAnsweredCurrentTownHallQuestion, stage.main_split, townHallPhase, townHallPlaying]);
  const crowdQueue = useMemo(
    () =>
      townHallQuestions
        .filter((item) => item.id !== visibleTownHallQuestion?.id)
        .slice(0, 3)
        .map((item) => `${item.displayName}, ${item.role}`),
    [townHallQuestions, visibleTownHallQuestion?.id],
  );

  useEffect(() => {
    if (auditoriumMode !== "town_hall") {
      onTownHallStateChange?.(null);
      return;
    }
      onTownHallStateChange?.({
      phase: townHallPhase,
      label: townHallStatus.label,
      detail: townHallError ?? townHallStatus.detail,
      question: sceneTownHallQuestion ?? null,
      activeTurnId: activeTownHallTurnId,
      awaitingPlayer: awaitingPlayerAnswer,
      readyForNextQuestion,
      playerAnswered: playerAnsweredCurrentTownHallQuestion,
      opponentAnswered: opponentAnsweredCurrentTownHallQuestion,
      playing: townHallPlaying,
      error: townHallError,
    });
  }, [
    activeTownHallTurnId,
    auditoriumMode,
    awaitingPlayerAnswer,
    onTownHallStateChange,
    opponentAnsweredCurrentTownHallQuestion,
    playerAnsweredCurrentTownHallQuestion,
    readyForNextQuestion,
    townHallError,
    townHallPhase,
    townHallPlaying,
    townHallStatus.detail,
    townHallStatus.label,
    sceneTownHallQuestion,
  ]);

  useImperativeHandle(ref, () => ({
    askTownHallQuestion: async () => {
      await handleAskTownHallQuestion();
    },
    primeTownHallAudio: async () => {
      await primeTownHallAudio();
    },
    replayTownHallQuestion: async () => {
      await handleReplayTownHallQuestion();
    },
    requestOpponentReply: async () => {
      await handleRequestOpponentReply();
    },
    focusComposer: () => {
      voiceDockRef?.current?.focusComposer();
    },
  }), [handleAskTownHallQuestion, handleReplayTownHallQuestion, handleRequestOpponentReply, voiceDockRef]);

  return (
    <section className={`debate-room debate-room--theme-${themeMode}`}>
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
          scopeKey={`${simulationId ?? "local"}:debate:${liveSessionAuditoriumMode}`}
          simulationId={simulationId}
          role="debate"
          themeMode={themeMode}
          presentation="drawer"
          auditoriumMode={auditoriumMode}
          sessionAuditoriumMode={liveSessionAuditoriumMode}
          autoResponse={auditoriumMode !== "town_hall"}
          externalPlaybackActive={auditoriumMode === "town_hall" && townHallPlaying}
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
          turns={contextTurns}
          metaChips={[
            stage.phase_label,
            auditoriumMode === "town_hall" ? "audience first" : "opponent live",
            "shared debate thread",
          ]}
          onSimulationSync={onSimulationSync}
          onPresenceChange={handleVoiceDockPresenceChange}
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
                    onClick={() => (liveTownHallQuestion ? void handleReplayTownHallQuestion() : void handleAskTownHallQuestion())}
                    disabled={!simulationId || townHallPhase === "generating" || (townHallPhase === "voter_speaking" && !townHallError)}
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
});
