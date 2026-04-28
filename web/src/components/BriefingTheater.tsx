import { useEffect, useMemo, useRef, useState } from "react";
import { toAbsoluteAssetUrl } from "../lib/api";
import { stageRoomBrief, stageWorldOpening } from "../lib/stageText";
import type { CountryThemeProfile } from "../lib/themeProfiles";
import type { NarrativeBeat, StagePackage } from "../types";

interface BriefingTheaterProps {
  stage: StagePackage;
  variant?: "cinematic" | "drawer";
  hidden?: boolean;
  themeProfile?: CountryThemeProfile;
  onEnterWarRoom?: () => void;
}

const BRIEFING_AUDIO_PLAYBACK_RATE = 1.06;

export function BriefingTheater({
  stage,
  variant = "drawer",
  hidden = false,
  themeProfile,
  onEnterWarRoom,
}: BriefingTheaterProps) {
  const [activeBeat, setActiveBeat] = useState(0);
  const [readyToEnter, setReadyToEnter] = useState(false);
  const [showTitleCard, setShowTitleCard] = useState(variant === "cinematic");
  const [loadedImageUrl, setLoadedImageUrl] = useState<string | null>(null);
  const [previousImageUrl, setPreviousImageUrl] = useState<string | null>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const timerRef = useRef<number | null>(null);
  const audioGuardTimerRef = useRef<number | null>(null);
  const previousImageTimerRef = useRef<number | null>(null);
  const playbackGenerationRef = useRef(0);
  const activeBeatRef = useRef(0);
  const loadedImageUrlRef = useRef<string | null>(null);
  const beats = useMemo<NarrativeBeat[]>(() => {
    if (stage.narrative_beats.length > 0) {
      return stage.narrative_beats;
    }
    const fallbackLine =
      stageWorldOpening(stage, 220) || stage.montage_logline || stageRoomBrief(stage) || "The room waits for the world to resolve.";
    return [
      {
        id: `${stage.index}-fallback-beat`,
        line: fallbackLine,
        image_prompt: "",
        image_path: null,
        image_url: null,
        audio_path: null,
        audio_url: null,
      },
    ];
  }, [stage]);
  const beat = beats[activeBeat] ?? beats[0];
  const imageUrl = toAbsoluteAssetUrl(beat?.image_url);
  const audioUrl = toAbsoluteAssetUrl(beat?.audio_url);

  const trackingHeadline = useMemo(() => {
    return [
      `${stage.tracking.approval.display} approval`,
      `${stage.tracking.better_off.display} better off`,
      `${stage.tracking.ai_comfort.display} AI comfort`,
    ].join(" · ");
  }, [stage.tracking.ai_comfort.display, stage.tracking.approval.display, stage.tracking.better_off.display]);
  function clearPlayback() {
    if (timerRef.current) {
      window.clearTimeout(timerRef.current);
      timerRef.current = null;
    }
    if (audioGuardTimerRef.current) {
      window.clearTimeout(audioGuardTimerRef.current);
      audioGuardTimerRef.current = null;
    }
    if (audioRef.current) {
      audioRef.current.pause();
      audioRef.current.currentTime = 0;
      audioRef.current = null;
    }
    if (previousImageTimerRef.current) {
      window.clearTimeout(previousImageTimerRef.current);
      previousImageTimerRef.current = null;
    }
  }

  function settleLoadedImage(nextImageUrl: string | null) {
    setLoadedImageUrl(nextImageUrl);
    loadedImageUrlRef.current = nextImageUrl;
    if (previousImageTimerRef.current) {
      window.clearTimeout(previousImageTimerRef.current);
    }
    previousImageTimerRef.current = window.setTimeout(() => {
      setPreviousImageUrl(null);
      previousImageTimerRef.current = null;
    }, 980);
  }

  function queueEnterWarRoom() {
    if (variant !== "cinematic") {
      return;
    }
    setReadyToEnter(true);
  }

  function scheduleAdvance(index: number) {
    if (variant !== "cinematic") {
      return;
    }
    const nextIndex = index + 1;
    if (nextIndex >= beats.length) {
      queueEnterWarRoom();
      return;
    }
    timerRef.current = window.setTimeout(() => {
      void playBeat(nextIndex);
    }, 420);
  }

  function beatDurationMs(line: string) {
    const wordCount = line.trim().split(/\s+/).filter(Boolean).length;
    return Math.max(11600, Math.min(24800, 8600 + wordCount * 520));
  }

  async function preloadBeatAssets(nextImageUrl: string | null, audio: HTMLAudioElement | null) {
    const tasks: Array<Promise<void>> = [];
    if (nextImageUrl) {
      tasks.push(new Promise<void>((resolve) => {
        const image = new Image();
        let settled = false;
        const done = () => {
          if (settled) {
            return;
          }
          settled = true;
          resolve();
        };
        image.onload = done;
        image.onerror = done;
        window.setTimeout(done, 1400);
        image.src = nextImageUrl;
      }));
    }
    if (audio) {
      tasks.push(new Promise<void>((resolve) => {
        let settled = false;
        const done = () => {
          if (settled) {
            return;
          }
          settled = true;
          resolve();
        };
        audio.addEventListener("canplaythrough", done, { once: true });
        audio.addEventListener("loadeddata", done, { once: true });
        window.setTimeout(done, 1600);
      }));
    }
    if (tasks.length > 0) {
      await Promise.all(tasks);
    }
  }

  async function playBeat(index: number) {
    playbackGenerationRef.current += 1;
    const playbackGeneration = playbackGenerationRef.current;
    clearPlayback();
    const nextBeat = beats[index];
    const nextImageUrl = toAbsoluteAssetUrl(nextBeat?.image_url) ?? null;
    const nextAudioUrl = toAbsoluteAssetUrl(nextBeat?.audio_url);
    const audio = nextAudioUrl ? new Audio(nextAudioUrl) : null;
    if (audio) {
      audio.preload = "auto";
      audio.playbackRate = BRIEFING_AUDIO_PLAYBACK_RATE;
    }
    await preloadBeatAssets(nextImageUrl, audio);
    if (playbackGenerationRef.current !== playbackGeneration) {
      audio?.pause();
      audio && (audio.currentTime = 0);
      return;
    }
    const priorImageUrl = loadedImageUrlRef.current ?? toAbsoluteAssetUrl(beats[activeBeatRef.current]?.image_url) ?? null;
    if (priorImageUrl && priorImageUrl !== nextImageUrl) {
      setPreviousImageUrl(priorImageUrl);
    } else {
      setPreviousImageUrl(null);
    }
    setShowTitleCard(false);
    setReadyToEnter(false);
    setActiveBeat(index);
    activeBeatRef.current = index;
    settleLoadedImage(nextImageUrl);
    await new Promise((resolve) => window.setTimeout(resolve, 240));
    if (playbackGenerationRef.current !== playbackGeneration) {
      audio?.pause();
      audio && (audio.currentTime = 0);
      return;
    }
    if (variant !== "cinematic") {
      if (!audio) {
        return;
      }
      audioRef.current = audio;
      audio.playbackRate = BRIEFING_AUDIO_PLAYBACK_RATE;
      void audio.play().catch(() => undefined);
      return;
    }
    if (!audio) {
      timerRef.current = window.setTimeout(() => {
        scheduleAdvance(index);
      }, beatDurationMs(nextBeat?.line ?? ""));
      return;
    }
    audioRef.current = audio;
    audio.playbackRate = BRIEFING_AUDIO_PLAYBACK_RATE;
    audio.onended = () => {
      if (audioGuardTimerRef.current) {
        window.clearTimeout(audioGuardTimerRef.current);
        audioGuardTimerRef.current = null;
      }
      audioRef.current = null;
      scheduleAdvance(index);
    };
    const armGuard = () => {
      if (audioGuardTimerRef.current) {
        window.clearTimeout(audioGuardTimerRef.current);
      }
      const metadataDurationMs =
        Number.isFinite(audio.duration) && audio.duration > 0 ? Math.ceil(audio.duration * 1000) + 5400 : undefined;
      const fallbackMs = beatDurationMs(nextBeat?.line ?? "") + 5600;
      const guardMs = Math.min(42000, Math.max(18800, metadataDurationMs ?? fallbackMs));
      audioGuardTimerRef.current = window.setTimeout(() => {
        if (audioRef.current === audio) {
          audio.pause();
          audio.currentTime = 0;
          audioRef.current = null;
        }
        audioGuardTimerRef.current = null;
        scheduleAdvance(index);
      }, guardMs);
    };
    try {
      await audio.play();
      if (playbackGenerationRef.current !== playbackGeneration) {
        audio.pause();
        audio.currentTime = 0;
        return;
      }
      armGuard();
    } catch {
      if (audioGuardTimerRef.current) {
        window.clearTimeout(audioGuardTimerRef.current);
        audioGuardTimerRef.current = null;
      }
      scheduleAdvance(index);
    }
  }

  useEffect(() => {
    activeBeatRef.current = activeBeat;
  }, [activeBeat]);

  useEffect(() => {
    loadedImageUrlRef.current = loadedImageUrl;
  }, [loadedImageUrl]);

  useEffect(() => {
    playbackGenerationRef.current += 1;
    clearPlayback();
    setActiveBeat(0);
    activeBeatRef.current = 0;
    setReadyToEnter(false);
    setShowTitleCard(variant === "cinematic");
    setLoadedImageUrl(null);
    loadedImageUrlRef.current = null;
    setPreviousImageUrl(null);
    if (variant === "cinematic" && beats.length > 0) {
      timerRef.current = window.setTimeout(() => {
        void playBeat(0);
      }, 2600);
    }
    return clearPlayback;
  }, [stage.index, stage.generated_at, variant]);

  if (!beat) {
    return null;
  }

  if (variant === "cinematic") {
    const chromeStyle = themeProfile
      ? ({
          ["--briefing-accent" as string]: themeProfile.accent,
          ["--briefing-fill" as string]: themeProfile.fill,
          ["--briefing-halo" as string]: themeProfile.halo,
        } satisfies Record<string, string>)
      : undefined;
    return (
      <section className={`briefing briefing--cinematic ${hidden ? "briefing--hidden" : ""}`} style={chromeStyle}>
        <div className={`briefing__media briefing__media--cinematic briefing__media--motion-${activeBeat % 4}`}>
          {imageUrl ? (
            <div
              className="briefing__frame-backdrop"
              style={{ backgroundImage: `url(${imageUrl})` }}
              aria-hidden="true"
            />
          ) : null}
          {previousImageUrl ? (
            <img
              className="briefing__image briefing__image--previous"
              src={previousImageUrl}
              alt=""
              aria-hidden="true"
            />
          ) : null}
          {imageUrl ? (
            <img
              key={beat.id}
              className={`briefing__image briefing__image--motion-${activeBeat % 4} ${
                loadedImageUrl === imageUrl ? "briefing__image--ready" : "briefing__image--loading"
              }`}
              src={imageUrl}
              alt={beat.line ?? stage.title}
            />
          ) : null}
          <div className="briefing__cinematic-chrome">
            <div className="briefing__cinematic-topline">
              <div className="briefing__cinematic-meta">
                <span>{stage.phase_label}</span>
                <strong>{stage.year_label}</strong>
              </div>
              <div className="briefing__cinematic-controls">
                {readyToEnter ? (
                  <button className="btn btn--secondary" onClick={() => void playBeat(0)}>
                    Replay
                  </button>
                ) : null}
                {onEnterWarRoom ? (
                  <button className="btn btn--primary" onClick={onEnterWarRoom}>
                    Skip
                  </button>
                ) : null}
              </div>
            </div>
            {showTitleCard ? (
              <div className="briefing__cinematic-titlecard">
                <span className="briefing__eyebrow">Documentary montage</span>
                <h2>{stage.title}</h2>
                <p>{stage.montage_logline || stageRoomBrief(stage)}</p>
              </div>
            ) : null}
            {!showTitleCard ? (
              <div className="briefing__cinematic-subtitle">
                <p>{beat.line}</p>
              </div>
            ) : null}
            {readyToEnter && onEnterWarRoom ? (
              <div className="briefing__cinematic-cta">
                <button className="btn btn--primary" onClick={onEnterWarRoom}>
                  Enter the live room
                </button>
              </div>
            ) : null}
          </div>
        </div>
      </section>
    );
  }

  return (
    <section className="briefing briefing--drawer">
      <div className="briefing__media">
        {imageUrl ? <img className="briefing__image" src={imageUrl} alt={beat.line ?? stage.title} /> : null}
        <div className="briefing__overlay">
          <span className="briefing__eyebrow">{stage.phase_label}</span>
          <h2>{stage.title}</h2>
          <p>{stageRoomBrief(stage)}</p>
          <small className="briefing__voiceover-line">{beat.line}</small>
          <div className="briefing__overlay-actions">
            <button className="btn btn--secondary" onClick={() => void playBeat(activeBeat)} disabled={!audioUrl}>
              Play voiceover
            </button>
          </div>
        </div>
      </div>
      <div className="briefing__meta">
        <div>
          <span className="briefing__label">World frame</span>
          <p>{stageWorldOpening(stage, 220)}</p>
        </div>
        <div>
          <span className="briefing__label">Command brief</span>
          <p>{stageRoomBrief(stage)}</p>
        </div>
        <div>
          <span className="briefing__label">Public pulse</span>
          <p>{trackingHeadline}</p>
        </div>
      </div>
      <div className="briefing__beats">
        {beats.map((entry, index) => (
          <button
            key={entry.id}
            className={`briefing__beat ${index === activeBeat ? "briefing__beat--active" : ""}`}
            onClick={() => setActiveBeat(index)}
          >
            <span>Beat {index + 1}</span>
            <div>
              <strong>{entry.line}</strong>
            </div>
          </button>
        ))}
      </div>
    </section>
  );
}
