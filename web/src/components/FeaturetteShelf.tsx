import { useEffect, useMemo, useRef, useState } from "react";
import { toAbsoluteAssetUrl } from "../lib/api";
import { featuretteQuestionLabel } from "../lib/featurettes";
import type { DocumentaryFeaturette, StagePackage } from "../types";

interface FeaturetteShelfProps {
  stage: StagePackage;
  variant?: "drawer" | "overlay";
  requestedFeaturetteId?: string | null;
  onRequestedFeaturetteClear?: () => void;
  onClose?: () => void;
  onCinemaStateChange?: (active: boolean) => void;
}

const FEATURETTE_AUDIO_PLAYBACK_RATE = 1.09;

function beatDurationMs(line: string) {
  const wordCount = line.trim().split(/\s+/).filter(Boolean).length;
  return Math.max(3200, Math.min(7800, 1800 + wordCount * 220));
}

async function settleAudioStart(playPromise: Promise<unknown>, timeoutMs = 1200) {
  let started = false;
  await Promise.race([
    playPromise
      .then(() => {
        started = true;
      })
      .catch(() => undefined),
    new Promise<void>((resolve) => {
      window.setTimeout(resolve, timeoutMs);
    }),
  ]);
  return started;
}

function waitForFeaturetteAudio(audio: HTMLAudioElement, fallbackMs: number) {
  return new Promise<void>((resolve) => {
    let settled = false;
    const cleanup = () => {
      audio.removeEventListener("ended", finish);
      audio.removeEventListener("error", finish);
      window.clearInterval(pollTimer);
      window.clearTimeout(timeoutTimer);
    };
    const finish = () => {
      if (settled) {
        return;
      }
      settled = true;
      cleanup();
      resolve();
    };
    const pollTimer = window.setInterval(() => {
      if (
        audio.error ||
        audio.ended ||
        (Number.isFinite(audio.duration) && audio.duration > 0 && audio.currentTime >= audio.duration - 0.06)
      ) {
        finish();
      }
    }, 260);
    const timeoutTimer = window.setTimeout(finish, fallbackMs);
    audio.addEventListener("ended", finish, { once: true });
    audio.addEventListener("error", finish, { once: true });
  });
}

function isPlayable(featurette: DocumentaryFeaturette) {
  return featurette.status === "ready" && featurette.narrative_beats.length > 0;
}

export function FeaturetteShelf({
  stage,
  variant = "drawer",
  requestedFeaturetteId,
  onRequestedFeaturetteClear,
  onClose,
  onCinemaStateChange,
}: FeaturetteShelfProps) {
  const [activeFeaturetteId, setActiveFeaturetteId] = useState<string | null>(() => requestedFeaturetteId ?? null);
  const [activeBeatIndex, setActiveBeatIndex] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [playbackError, setPlaybackError] = useState<string | null>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const playbackRunRef = useRef(0);
  const previousStageIndexRef = useRef(stage.index);

  const featurettes = useMemo(() => stage.featurettes, [stage.featurettes]);
  const readyCount = useMemo(
    () => featurettes.filter((featurette) => featurette.status === "ready").length,
    [featurettes],
  );
  const renderPending = stage.featurettes_status !== "ready" && stage.featurettes_status !== "error";
  const lastHandledRequestRef = useRef<string | null>(null);

  function ensureAudio() {
    const existing = audioRef.current;
    if (existing) {
      return existing;
    }
    const audio = document.createElement("audio");
    audio.autoplay = true;
    audio.setAttribute("playsinline", "true");
    audio.playbackRate = FEATURETTE_AUDIO_PLAYBACK_RATE;
    audio.style.display = "none";
    document.body.appendChild(audio);
    audioRef.current = audio;
    return audio;
  }

  function stopPlayback() {
    playbackRunRef.current += 1;
    const audio = audioRef.current;
    if (audio) {
      audio.pause();
      audio.currentTime = 0;
      audio.removeAttribute("src");
      audio.load();
    }
    setPlaying(false);
  }

  useEffect(() => {
    if (previousStageIndexRef.current === stage.index) {
      return;
    }
    previousStageIndexRef.current = stage.index;
    stopPlayback();
    setActiveFeaturetteId(null);
    setActiveBeatIndex(0);
    setPlaybackError(null);
  }, [stage.index]);

  useEffect(() => {
    if (requestedFeaturetteId === undefined) {
      return;
    }
    const requested =
      requestedFeaturetteId
        ? featurettes.find((featurette) => featurette.id === requestedFeaturetteId) ?? null
        : null;
    const requestKey = `${stage.index}:${requestedFeaturetteId ?? "shelf"}:${requested && isPlayable(requested) ? "ready" : "pending"}`;
    if (lastHandledRequestRef.current === requestKey) {
      return;
    }
    lastHandledRequestRef.current = requestKey;
    stopPlayback();
    setPlaybackError(null);
    if (requested && isPlayable(requested)) {
      void playFeaturette(requested, 0);
      return;
    }
    setActiveFeaturetteId(requestedFeaturetteId);
    setActiveBeatIndex(0);
  }, [featurettes, requestedFeaturetteId, stage.index]);

  useEffect(() => {
    if (activeFeaturetteId && featurettes.some((featurette) => featurette.id === activeFeaturetteId)) {
      return;
    }
    setActiveFeaturetteId(null);
    setActiveBeatIndex(0);
  }, [activeFeaturetteId, featurettes]);

  useEffect(() => () => {
    stopPlayback();
    if (audioRef.current) {
      audioRef.current.remove();
      audioRef.current = null;
    }
  }, []);

  const activeFeaturette = useMemo(() => {
    if (!activeFeaturetteId) {
      return null;
    }
    return featurettes.find((featurette) => featurette.id === activeFeaturetteId) ?? null;
  }, [activeFeaturetteId, featurettes]);
  const requestedOverlayFeaturette = useMemo(() => {
    if (variant !== "overlay" || !requestedFeaturetteId) {
      return null;
    }
    const requested = featurettes.find((featurette) => featurette.id === requestedFeaturetteId) ?? null;
    return requested && isPlayable(requested) ? requested : null;
  }, [featurettes, requestedFeaturetteId, variant]);
  const visibleFeaturette = activeFeaturette ?? requestedOverlayFeaturette;
  const activeBeat = visibleFeaturette?.narrative_beats[activeBeatIndex] ?? visibleFeaturette?.narrative_beats[0] ?? null;
  const activeImageUrl = toAbsoluteAssetUrl(activeBeat?.image_url);
  const showHero = variant !== "overlay";

  useEffect(() => {
    if (variant !== "overlay") {
      return;
    }
    onCinemaStateChange?.(Boolean(visibleFeaturette));
    return () => {
      onCinemaStateChange?.(false);
    };
  }, [onCinemaStateChange, variant, visibleFeaturette]);

  function closeActiveFeaturette() {
    stopPlayback();
    setActiveFeaturetteId(null);
    setActiveBeatIndex(0);
    setPlaybackError(null);
  }

  function returnToShelf() {
    closeActiveFeaturette();
    onRequestedFeaturetteClear?.();
  }

  async function playFeaturette(featurette: DocumentaryFeaturette, startIndex = 0) {
    if (!isPlayable(featurette)) {
      return;
    }
    stopPlayback();
    const runId = playbackRunRef.current;
    const audio = ensureAudio();
    setPlaybackError(null);
    setActiveFeaturetteId(featurette.id);
    setPlaying(true);
    try {
      for (let index = startIndex; index < featurette.narrative_beats.length; index += 1) {
        if (playbackRunRef.current !== runId) {
          return;
        }
        const beat = featurette.narrative_beats[index];
        setActiveBeatIndex(index);
        const audioUrl = toAbsoluteAssetUrl(beat.audio_url);
        if (audioUrl) {
          audio.src = audioUrl;
          audio.playbackRate = FEATURETTE_AUDIO_PLAYBACK_RATE;
          const started = await settleAudioStart(audio.play(), 1100);
          await waitForFeaturetteAudio(audio, beatDurationMs(beat.line) + (started ? 3200 : 0));
        } else {
          await new Promise((resolve) => window.setTimeout(resolve, beatDurationMs(beat.line)));
        }
        if (index < featurette.narrative_beats.length - 1) {
          await new Promise((resolve) => window.setTimeout(resolve, 180));
        }
      }
    } finally {
      if (playbackRunRef.current === runId) {
        setPlaying(false);
      }
    }
  }

  if (variant === "overlay" && visibleFeaturette) {
    return (
      <section className="featurette-cinema" data-testid="featurette-cinema">
        <div className="featurette-cinema__media">
          {activeImageUrl ? (
            <div
              className="featurette-cinema__backdrop"
              style={{ backgroundImage: `url(${activeImageUrl})` }}
              aria-hidden="true"
            />
          ) : null}
          {activeImageUrl ? (
            <img className="featurette-cinema__image" src={activeImageUrl} alt={activeBeat?.line || visibleFeaturette.title} />
          ) : (
            <div className="featurette-cinema__placeholder">
              {visibleFeaturette.narrative_beats.length > 0
                ? "Loading the reel frame..."
                : "This reel is still rendering its documentary beats."}
            </div>
          )}
          <div className="featurette-cinema__chrome">
            <div className="featurette-cinema__topline">
              <div className="featurette-cinema__title">
                <span>{visibleFeaturette.subject}</span>
                <strong>{visibleFeaturette.title}</strong>
              </div>
              <div className="featurette-cinema__top-actions">
                <button
                  className="btn btn--primary"
                  onClick={() => {
                    void playFeaturette(visibleFeaturette, 0);
                  }}
                  disabled={!isPlayable(visibleFeaturette)}
                >
                  Replay
                </button>
                <button
                  className="btn btn--ghost"
                  onClick={() => {
                    stopPlayback();
                    if (onClose) {
                      onClose();
                      return;
                    }
                    returnToShelf();
                  }}
                >
                  {onClose ? "Close" : "Back"}
                </button>
              </div>
            </div>

            <div className="featurette-cinema__subtitle">
              <span>Beat {activeBeatIndex + 1} of {visibleFeaturette.narrative_beats.length}</span>
              <p>{activeBeat?.line || visibleFeaturette.logline}</p>
            </div>
            {playbackError ? <p className="voice-dock__error">{playbackError}</p> : null}
          </div>
        </div>
      </section>
    );
  }

  return (
    <section className={`featurette-shelf ${variant === "overlay" ? "featurette-shelf--overlay" : ""}`}>
      {showHero ? (
        <header className="featurette-shelf__hero">
          <div>
            <span>Future reels</span>
            <strong>
              {stage.featurettes_status === "error"
                ? "Side documentaries stalled"
                : readyCount > 0
                  ? "Choose a short side documentary"
                  : "Side documentaries are still arriving"}
            </strong>
            <p>
              {stage.featurettes_status === "error"
                ? (stage.featurettes_error || "The side reels hit a render problem, but the main chapter is still playable.")
                : readyCount > 0
                  ? "Each reel should teach one system clearly, then play through as its own short documentary."
                  : "The main chapter is already live while the side reels keep rendering in the background."}
            </p>
          </div>
          <div className="featurette-shelf__meta">
            <span>{readyCount} ready</span>
            <span>{renderPending ? "rendering more" : "shelf live"}</span>
          </div>
        </header>
      ) : null}

      <div className="featurette-shelf__cards">
        {featurettes.map((featurette, index) => (
          <button
            key={featurette.id}
            data-testid={`featurette-card-${index + 1}`}
            className={`featurette-card ${
              featurette.id === activeFeaturette?.id ? "featurette-card--active" : ""
            } ${
              isPlayable(featurette) ? "featurette-card--ready" : "featurette-card--pending"
            }`}
            onClick={() => {
              if (!isPlayable(featurette)) {
                return;
              }
              setActiveFeaturetteId(featurette.id);
              setActiveBeatIndex(0);
              setPlaybackError(null);
              void playFeaturette(featurette, 0);
            }}
            disabled={!isPlayable(featurette)}
          >
            <span>{featurette.subject || `Reel ${index + 1}`}</span>
            <strong>{featurette.title}</strong>
            <p className="featurette-card__question">{featuretteQuestionLabel(featurette)}</p>
            <small>
              {isPlayable(featurette)
                ? "Play this reel"
                : featurette.status === "error"
                  ? (featurette.error || "Render problem")
                  : "Still rendering in background"}
            </small>
          </button>
        ))}
        {renderPending ? (
          <article className="featurette-card featurette-card--pending">
            <span>More on the way</span>
            <strong>Still cutting another reel</strong>
            <p>The chapter can keep moving while another angle on this future finishes rendering.</p>
            <small>Background render still running</small>
          </article>
        ) : null}
      </div>

      {activeFeaturette ? (
        <section className="featurette-viewer">
          <div className="featurette-viewer__media">
            {activeBeat && toAbsoluteAssetUrl(activeBeat.image_url) ? (
              <img
                className="featurette-viewer__image"
                src={toAbsoluteAssetUrl(activeBeat.image_url) ?? undefined}
                alt={activeBeat.line}
              />
            ) : (
              <div className="featurette-viewer__placeholder">
                {activeFeaturette.narrative_beats.length > 0
                  ? "Rendering imagery..."
                  : "This reel is still rendering its documentary beats."}
              </div>
            )}
          </div>
          <div className="featurette-viewer__copy">
            <span>{activeFeaturette.subject}</span>
            <h3>{activeFeaturette.title}</h3>
            <p className="featurette-viewer__question">{featuretteQuestionLabel(activeFeaturette)}</p>
            <p>{activeFeaturette.logline}</p>
            {activeBeat ? (
              <>
                <strong>{activeBeat.line}</strong>
                <p className="featurette-viewer__progress">
                  Beat {activeBeatIndex + 1} of {activeFeaturette.narrative_beats.length}
                </p>
              </>
            ) : null}
            <div className="featurette-viewer__actions">
              <button
                className="btn btn--ghost"
                onClick={() => {
                  closeActiveFeaturette();
                }}
              >
                Back to reels
              </button>
              <button
                className="btn btn--secondary"
                onClick={() => {
                  if (!activeFeaturette) {
                    return;
                  }
                  if (playing) {
                    stopPlayback();
                    return;
                  }
                  void playFeaturette(activeFeaturette, activeBeatIndex);
                }}
                disabled={!isPlayable(activeFeaturette)}
              >
                {playing ? "Stop reel" : activeBeatIndex > 0 ? "Resume reel" : "Play reel"}
              </button>
              <button
                className="btn btn--ghost"
                onClick={() => {
                  if (!activeFeaturette) {
                    return;
                  }
                  void playFeaturette(activeFeaturette, 0);
                }}
                disabled={!isPlayable(activeFeaturette)}
              >
                Replay from start
              </button>
            </div>
            {activeFeaturette.narrative_beats.length > 0 ? (
              <div className="featurette-viewer__beats">
                {activeFeaturette.narrative_beats.map((beat, index) => (
                  <button
                    key={beat.id}
                    className={`featurette-viewer__beat ${index === activeBeatIndex ? "featurette-viewer__beat--active" : ""}`}
                    onClick={() => {
                      stopPlayback();
                      setActiveBeatIndex(index);
                    }}
                  >
                    <span>Beat {index + 1}</span>
                    <p>{beat.line}</p>
                  </button>
                ))}
              </div>
            ) : (
              <div className="featurette-viewer__beats">
                <div className="featurette-viewer__beat featurette-viewer__beat--active">
                  <span>Rendering</span>
                  <p>The chapter can keep playing while this reel finishes its documentary beats.</p>
                </div>
              </div>
            )}
            {playbackError ? <p className="voice-dock__error">{playbackError}</p> : null}
          </div>
        </section>
      ) : (
        <section className="featurette-shelf__chooser">
          <span>Pick a reel</span>
          <strong>
            {featurettes.length > 0 ? "Open one of the cards above to go deeper." : "The first side documentary is still rendering."}
          </strong>
          <p>
            {featurettes.length > 0
              ? "Each reel should teach one concrete thing about how this chapter's future actually works."
              : "The main chapter is already live while the background reels are still being assembled."}
          </p>
        </section>
      )}
    </section>
  );
}
