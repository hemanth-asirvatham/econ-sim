import { useDeferredValue, useEffect, useMemo, useRef, useState } from "react";
import type { ReasoningEffort, SetupDraft, SetupSessionState } from "../types";

const REASONING_OPTIONS: ReasoningEffort[] = ["none", "low", "medium", "high"];

interface BrowserSpeechRecognitionEvent {
  results: ArrayLike<ArrayLike<{ transcript: string }>>;
}

interface BrowserSpeechRecognition {
  continuous: boolean;
  interimResults: boolean;
  lang: string;
  onend: (() => void) | null;
  onerror: (() => void) | null;
  onresult: ((event: BrowserSpeechRecognitionEvent) => void) | null;
  start: () => void;
  stop: () => void;
}

interface BrowserSpeechRecognitionConstructor {
  new (): BrowserSpeechRecognition;
}

declare global {
  interface Window {
    SpeechRecognition?: BrowserSpeechRecognitionConstructor;
    webkitSpeechRecognition?: BrowserSpeechRecognitionConstructor;
  }
}

interface SetupChamberProps {
  advancedOpen: boolean;
  asking: boolean;
  draftDirty: boolean;
  error?: string | null;
  loading: boolean;
  session: SetupSessionState | null;
  starting: boolean;
  syncing: boolean;
  themeMode?: "light" | "dark";
  onDraftFieldChange: (field: keyof SetupDraft, value: SetupDraft[keyof SetupDraft]) => void;
  onRefresh: () => Promise<void>;
  onSendPrompt: (message: string) => Promise<void>;
  onStartSimulation: () => Promise<void>;
  onSyncDraft: () => Promise<void>;
  onToggleAdvanced: () => void;
  onToggleTheme?: () => void;
}

function speechRecognitionConstructor() {
  if (typeof window === "undefined") {
    return null;
  }
  return window.SpeechRecognition ?? window.webkitSpeechRecognition ?? null;
}

function speakerLabel(speaker: "user" | "assistant" | "system") {
  if (speaker === "assistant") {
    return "Orchestrator";
  }
  if (speaker === "system") {
    return "System";
  }
  return "You";
}

function fieldValue(value?: string | null) {
  return value ?? "";
}

function clip(text?: string | null, limit = 120) {
  const cleaned = (text ?? "").trim().replace(/\s+/g, " ");
  if (!cleaned) {
    return "";
  }
  return cleaned.length > limit ? `${cleaned.slice(0, limit - 1)}…` : cleaned;
}

export function SetupChamber({
  advancedOpen,
  asking,
  draftDirty,
  error,
  loading,
  session,
  starting,
  syncing,
  themeMode = "light",
  onDraftFieldChange,
  onRefresh,
  onSendPrompt,
  onStartSimulation,
  onSyncDraft,
  onToggleAdvanced,
  onToggleTheme,
}: SetupChamberProps) {
  const [prompt, setPrompt] = useState("");
  const [listening, setListening] = useState(false);
  const [fieldsOpen, setFieldsOpen] = useState(false);
  const recognitionRef = useRef<BrowserSpeechRecognition | null>(null);
  const deferredDraft = useDeferredValue(session?.draft);
  const voiceAvailable = Boolean(speechRecognitionConstructor());
  const transcript = session?.transcript ?? [];
  const latestTurns = useMemo(() => transcript.slice(-8).reverse(), [transcript]);
  const briefCards = useMemo(() => {
    if (!deferredDraft) {
      return [];
    }
    return [
      {
        label: "Jurisdiction",
        value: deferredDraft.country || "United States",
        detail: deferredDraft.region_focus?.trim() || "Broad national field",
      },
      {
        label: "Cast",
        value: `${deferredDraft.player_name || deferredDraft.player_role || "Player"} vs ${deferredDraft.opponent_name || deferredDraft.opponent_role || "Opponent"}`,
        detail: `${deferredDraft.persona_count} citizens · ${deferredDraft.stage_count} stages`,
      },
      {
        label: "Lens",
        value: deferredDraft.topic_lens?.trim() || "Broad AGI transition",
        detail: deferredDraft.premise?.trim() || "No extra premise locked",
      },
      {
        label: "Look",
        value: clip(deferredDraft.visual_style, 58) || "Industrial civic documentary",
        detail: deferredDraft.stakes?.trim() || "No special electoral stake locked",
      },
    ];
  }, [deferredDraft]);

  async function handlePromptSend() {
    const next = prompt.trim();
    if (!next) {
      return;
    }
    setPrompt("");
    await onSendPrompt(next);
  }

  function handleVoiceToggle() {
    if (listening) {
      recognitionRef.current?.stop();
      return;
    }
    const Recognition = speechRecognitionConstructor();
    if (!Recognition) {
      return;
    }
    const recognition = new Recognition();
    recognition.continuous = false;
    recognition.interimResults = true;
    recognition.lang = "en-US";
    recognition.onresult = (event) => {
      const transcriptText = Array.from(event.results)
        .flatMap((result) => Array.from(result))
        .map((result) => result.transcript)
        .join(" ")
        .trim();
      if (!transcriptText) {
        return;
      }
      setPrompt((current) => `${current.trim()} ${transcriptText}`.trim());
    };
    recognition.onerror = () => {
      setListening(false);
      recognitionRef.current = null;
    };
    recognition.onend = () => {
      setListening(false);
      recognitionRef.current = null;
    };
    recognitionRef.current = recognition;
    setListening(true);
    recognition.start();
  }

  useEffect(() => {
    return () => {
      recognitionRef.current?.stop();
      recognitionRef.current = null;
    };
  }, []);

  return (
    <section className={`setup-console ${advancedOpen ? "setup-console--open" : ""}`}>
      {advancedOpen ? (
        <section className="setup-console__drawer">
          <div className="setup-console__drawer-grid">
            <section className="setup-console__panel">
              <div className="setup-console__panel-head">
                <div>
                  <span className="setup-console__eyebrow">Channel</span>
                  <h3>Recent exchange</h3>
                </div>
                <span className="setup-console__status">
                  {session?.mode === "live" ? "Live backend" : "Fallback"}
                </span>
              </div>
              <div className="setup-console__transcript">
                {loading && !session ? <p className="setup-console__placeholder">Opening the chamber…</p> : null}
                {!loading && latestTurns.length === 0 ? <p className="setup-console__placeholder">The orchestrator is waiting for your first nudge.</p> : null}
                {latestTurns.map((turn) => (
                  <article key={turn.id} className={`setup-console__turn setup-console__turn--${turn.speaker}`}>
                    <span>{speakerLabel(turn.speaker)}</span>
                    <p>{turn.text}</p>
                  </article>
                ))}
              </div>
            </section>

            <section className="setup-console__panel">
              <div className="setup-console__panel-head">
                <div>
                  <span className="setup-console__eyebrow">Current frame</span>
                  <h3>Scope at a glance</h3>
                </div>
                <div className="setup-console__panel-actions">
                  {onToggleTheme ? (
                    <button className="btn btn--ghost" onClick={onToggleTheme}>
                      {themeMode === "light" ? "Dark" : "Light"}
                    </button>
                  ) : null}
                  <button className="btn btn--ghost" onClick={() => setFieldsOpen((current) => !current)}>
                    {fieldsOpen ? "Hide fields" : "Field controls"}
                  </button>
                </div>
              </div>
              <div className="setup-console__summary">
                {briefCards.map((item) => (
                  <article key={item.label}>
                    <span>{item.label}</span>
                    <strong>{item.value}</strong>
                    <p>{item.detail}</p>
                  </article>
                ))}
              </div>
              {session?.guidance?.applied_updates?.length ? (
                <div className="setup-console__guidance">
                  <span className="setup-console__eyebrow">Last changes</span>
                  <p>{session.guidance.applied_updates.slice(0, 4).join(" · ")}</p>
                </div>
              ) : null}
              {session?.guidance?.open_questions?.length ? (
                <div className="setup-console__guidance">
                  <span className="setup-console__eyebrow">Open question</span>
                  <p>{session.guidance.open_questions[0]}</p>
                </div>
              ) : null}
              <div className="setup-console__drawer-actions">
                <button className="btn btn--secondary" onClick={() => void onRefresh()} disabled={loading || syncing || asking || starting}>
                  Refresh chamber
                </button>
                <button className="btn btn--ghost" onClick={() => void onSyncDraft()} disabled={!session || loading || syncing || starting}>
                  {syncing ? "Syncing…" : draftDirty ? "Sync exact edits" : "Draft synced"}
                </button>
                <button className="btn btn--primary" onClick={() => void onStartSimulation()} disabled={!session || loading || syncing || asking || starting}>
                  {starting ? "Launching…" : "Begin simulation"}
                </button>
              </div>
            </section>
          </div>

          {fieldsOpen ? (
            <section className="setup-console__fields">
              <div className="setup-console__fields-grid">
                <label>
                  <span>Country</span>
                  <input value={fieldValue(session?.draft.country)} onChange={(event) => onDraftFieldChange("country", event.target.value)} />
                </label>
                <label>
                  <span>Region focus</span>
                  <input value={fieldValue(session?.draft.region_focus)} onChange={(event) => onDraftFieldChange("region_focus", event.target.value)} placeholder="Leave blank for broad national coverage" />
                </label>
                <label>
                  <span>Topic lens</span>
                  <input value={fieldValue(session?.draft.topic_lens)} onChange={(event) => onDraftFieldChange("topic_lens", event.target.value)} placeholder="Leave blank unless you want a specific emphasis" />
                </label>
                <label>
                  <span>Persona count</span>
                  <input type="number" min={8} max={256} value={session?.draft.persona_count ?? 48} onChange={(event) => onDraftFieldChange("persona_count", Number(event.target.value))} />
                </label>
                <label>
                  <span>Player</span>
                  <input value={fieldValue(session?.draft.player_name)} onChange={(event) => onDraftFieldChange("player_name", event.target.value)} />
                </label>
                <label>
                  <span>Player role</span>
                  <input value={fieldValue(session?.draft.player_role)} onChange={(event) => onDraftFieldChange("player_role", event.target.value)} />
                </label>
                <label>
                  <span>Opponent</span>
                  <input value={fieldValue(session?.draft.opponent_name)} onChange={(event) => onDraftFieldChange("opponent_name", event.target.value)} />
                </label>
                <label>
                  <span>Opponent role</span>
                  <input value={fieldValue(session?.draft.opponent_role)} onChange={(event) => onDraftFieldChange("opponent_role", event.target.value)} />
                </label>
                <label className="setup-console__field--wide">
                  <span>Population</span>
                  <textarea rows={3} value={fieldValue(session?.draft.population_description)} onChange={(event) => onDraftFieldChange("population_description", event.target.value)} />
                </label>
                <label className="setup-console__field--wide">
                  <span>Premise</span>
                  <textarea rows={2} value={fieldValue(session?.draft.premise)} onChange={(event) => onDraftFieldChange("premise", event.target.value)} placeholder="Leave blank to let the orchestrator infer the broad national frame" />
                </label>
                <label className="setup-console__field--wide">
                  <span>Stakes</span>
                  <textarea rows={2} value={fieldValue(session?.draft.stakes)} onChange={(event) => onDraftFieldChange("stakes", event.target.value)} placeholder="Leave blank unless you want a specific political question emphasized" />
                </label>
                <label className="setup-console__field--wide">
                  <span>Visual style</span>
                  <textarea rows={3} value={fieldValue(session?.draft.visual_style)} onChange={(event) => onDraftFieldChange("visual_style", event.target.value)} />
                </label>
                <label>
                  <span>Stage count</span>
                  <input type="number" min={3} max={8} value={session?.draft.stage_count ?? 5} onChange={(event) => onDraftFieldChange("stage_count", Number(event.target.value))} />
                </label>
                <label>
                  <span>Reasoning</span>
                  <select value={session?.draft.orchestrator_reasoning_effort ?? "low"} onChange={(event) => onDraftFieldChange("orchestrator_reasoning_effort", event.target.value as ReasoningEffort)}>
                    {REASONING_OPTIONS.map((option) => (
                      <option key={option} value={option}>
                        {option}
                      </option>
                    ))}
                  </select>
                </label>
              </div>
            </section>
          ) : null}
        </section>
      ) : null}

      <div className="setup-console__bar-shell">
        <button type="button" className="setup-console__menu" onClick={onToggleAdvanced}>
          {advancedOpen ? "Hide details" : "Details"}
        </button>
        <form
          className="setup-console__bar"
          onSubmit={(event) => {
            event.preventDefault();
            void handlePromptSend();
          }}
        >
          {voiceAvailable ? (
            <button
              type="button"
              className={`setup-console__speak ${listening ? "setup-console__speak--active" : ""}`}
              onClick={handleVoiceToggle}
              disabled={asking || starting}
            >
              <span className="setup-console__speak-icon">{listening ? "REC" : "MIC"}</span>
              <strong>{listening ? "Listening" : "Speak"}</strong>
            </button>
          ) : null}
          <label className="setup-console__composer">
            <span className="sr-only">Direct the orchestrator</span>
            <input
              value={prompt}
              onChange={(event) => setPrompt(event.target.value)}
              placeholder="Say go, or name a country, scale, electorate, lens, or art direction…"
            />
          </label>
          <button type="submit" className="setup-console__send" disabled={!session || !prompt.trim() || asking || starting}>
            {asking ? "…" : "Send"}
          </button>
        </form>
        {error ? <p className="setup-console__error">{error}</p> : null}
      </div>
    </section>
  );
}
