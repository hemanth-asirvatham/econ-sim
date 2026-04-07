#!/usr/bin/env node

const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { execFileSync, execSync } = require("node:child_process");
const { createRequire } = require("node:module");

const DEFAULT_BASE_URL = "http://127.0.0.1:5173/";
const DEFAULT_OUT_DIR = path.join("/Users/hemanth/code/econ-sim/output/playwright", "gcft-audio-playtest");
const DEFAULT_GCFT_BIN = path.join(
  os.homedir(),
  "Library/Caches/ms-playwright/chromium-1208/chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing",
);
const DEFAULT_RUNTIME_DIR = path.join(process.env.TMPDIR || os.tmpdir(), "econ-sim-playwright");

function parseArgs(argv) {
  const args = {
    url: process.env.ECON_SIM_BASE_URL || DEFAULT_BASE_URL,
    outDir: process.env.ECON_SIM_QA_DIR || DEFAULT_OUT_DIR,
    simId: process.env.ECON_SIM_SIM_ID || "",
    room: (process.env.ECON_SIM_ROOM || "advisor").toLowerCase(),
    advisorMode: (process.env.ECON_SIM_ADVISOR_MODE || "solo").toLowerCase(),
    auditoriumMode: (process.env.ECON_SIM_AUDITORIUM_MODE || "debate").toLowerCase(),
    setupPrompt: process.env.ECON_SIM_SETUP_PROMPT || "Launch the broad default simulation.",
    micText: process.env.ECON_SIM_MIC_TEXT || "",
    micAudioFile: process.env.ECON_SIM_MIC_AUDIO_FILE || "",
    audioCaptureMs: Number.parseInt(process.env.ECON_SIM_AUDIO_CAPTURE_MS || "15000", 10),
    readyTimeoutMs: Number.parseInt(process.env.ECON_SIM_READY_TIMEOUT_MS || "120000", 10),
    postMicWaitMs: Number.parseInt(process.env.ECON_SIM_POST_MIC_WAIT_MS || "18000", 10),
    openSetup: true,
    keepOpen: process.env.ECON_SIM_KEEP_OPEN === "1",
    headless: process.env.ECON_SIM_HEADLESS === "1",
    restoreFocus: process.env.ECON_SIM_RESTORE_FOCUS !== "0",
    gcftBin: process.env.PLAYWRIGHT_GCFT_BIN || DEFAULT_GCFT_BIN,
    runtimeDir: process.env.PLAYWRIGHT_RUNTIME_DIR || DEFAULT_RUNTIME_DIR,
  };

  const positional = [];
  for (let index = 2; index < argv.length; index += 1) {
    const entry = argv[index];
    if (!entry.startsWith("--")) {
      positional.push(entry);
      continue;
    }
    const [flag, rawValue] = entry.split("=", 2);
    const value = rawValue ?? argv[index + 1];
    const hasInlineValue = rawValue !== undefined;
    switch (flag) {
      case "--url":
        args.url = value;
        break;
      case "--out-dir":
        args.outDir = value;
        break;
      case "--sim":
        args.simId = value;
        break;
      case "--room":
        args.room = String(value || "").toLowerCase();
        break;
      case "--advisor-mode":
        args.advisorMode = String(value || "").toLowerCase();
        break;
      case "--auditorium-mode":
        args.auditoriumMode = String(value || "").toLowerCase();
        break;
      case "--setup-prompt":
        args.setupPrompt = value;
        break;
      case "--mic-text":
        args.micText = value;
        break;
      case "--mic-audio":
        args.micAudioFile = value;
        break;
      case "--audio-capture-ms":
        args.audioCaptureMs = Number.parseInt(value, 10);
        break;
      case "--ready-timeout-ms":
        args.readyTimeoutMs = Number.parseInt(value, 10);
        break;
      case "--post-mic-wait-ms":
        args.postMicWaitMs = Number.parseInt(value, 10);
        break;
      case "--keep-open":
        args.keepOpen = true;
        break;
      case "--headless":
        args.headless = true;
        break;
      case "--allow-focus":
        args.restoreFocus = false;
        break;
      case "--open-setup":
        args.openSetup = true;
        break;
      case "--skip-setup":
        args.openSetup = false;
        break;
      case "--gcft-bin":
        args.gcftBin = value;
        break;
      case "--runtime-dir":
        args.runtimeDir = value;
        break;
      default:
        break;
    }
    if (!hasInlineValue && !["--keep-open", "--headless", "--open-setup", "--skip-setup", "--allow-focus"].includes(flag)) {
      index += 1;
    }
  }

  if (positional[0]) {
    args.url = positional[0];
  }
  if (positional[1]) {
    args.outDir = positional[1];
  }
  if (positional[2]) {
    args.room = String(positional[2]).toLowerCase();
  }
  if (positional[3]) {
    args.micText = positional[3];
  }

  if (!Number.isFinite(args.audioCaptureMs) || args.audioCaptureMs < 1000) {
    args.audioCaptureMs = 15000;
  }
  if (!Number.isFinite(args.readyTimeoutMs) || args.readyTimeoutMs < 30000) {
    args.readyTimeoutMs = 120000;
  }
  if (!Number.isFinite(args.postMicWaitMs) || args.postMicWaitMs < 2000) {
    args.postMicWaitMs = 18000;
  }

  return args;
}

function ensureRuntime(runtimeDir) {
  fs.mkdirSync(runtimeDir, { recursive: true });
  const packageJson = path.join(runtimeDir, "package.json");
  if (!fs.existsSync(packageJson)) {
    execSync("npm init -y >/dev/null 2>&1", { cwd: runtimeDir, stdio: "inherit", shell: "/bin/zsh" });
  }
  const playwrightDir = path.join(runtimeDir, "node_modules", "playwright");
  if (!fs.existsSync(playwrightDir)) {
    execSync("PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 npm install playwright >/dev/null 2>&1", {
      cwd: runtimeDir,
      stdio: "inherit",
      shell: "/bin/zsh",
    });
  }
}

function loadPlaywright(runtimeDir) {
  ensureRuntime(runtimeDir);
  const runtimeRequire = createRequire(path.join(runtimeDir, "package.json"));
  return runtimeRequire("playwright");
}

function ensureDir(dir) {
  fs.mkdirSync(dir, { recursive: true });
}

function frontmostAppName() {
  if (process.platform !== "darwin") {
    return "";
  }
  try {
    return String(
      execFileSync(
        "osascript",
        ["-e", 'tell application "System Events" to get name of first application process whose frontmost is true'],
        { encoding: "utf8" },
      ),
    ).trim();
  } catch {
    return "";
  }
}

function restoreFrontmostApp(appName) {
  if (process.platform !== "darwin" || !safeText(appName)) {
    return;
  }
  try {
    execFileSync("open", ["-g", "-a", appName], { stdio: "ignore" });
  } catch {
    // Best effort only.
  }
}

function timestamp() {
  return new Date().toISOString().replace(/[:.]/g, "-");
}

function safeText(value) {
  return String(value ?? "").replace(/\s+/g, " ").trim();
}

function slug(text) {
  return safeText(text)
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 80) || "playtest";
}

function buildRoomUrl(baseUrl, simId, room, advisorMode, auditoriumMode) {
  const url = new URL(baseUrl);
  if (simId) {
    url.searchParams.set("sim", simId);
  }
  url.searchParams.set("advisor", advisorMode === "council" ? "multi" : "solo");
  url.searchParams.set("auditorium", auditoriumMode);
  if (room === "citizens") {
    url.searchParams.set("room", "citizens");
  } else if (room === "advisor") {
    url.searchParams.set("room", "advisor");
  } else if (room === "debate") {
    url.searchParams.set("room", "debate");
  }
  return url.toString();
}

function extensionForMimeType(mimeType) {
  const normalized = String(mimeType || "").toLowerCase();
  if (normalized.includes("mpeg") || normalized.includes("mp3")) {
    return "mp3";
  }
  if (normalized.includes("ogg")) {
    return "ogg";
  }
  if (normalized.includes("wav")) {
    return "wav";
  }
  return "bin";
}

function computeProbeMetrics(probe) {
  if (!probe || !Array.isArray(probe.events)) {
    return null;
  }
  const firstEventAt = (type) => probe.events.find((event) => event.type === type)?.t_ms ?? null;
  const firstAssistantTranscriptAt = Array.isArray(probe.assistantTranscripts) && probe.assistantTranscripts.length > 0
    ? probe.assistantTranscripts[0]?.completed_at_ms ?? null
    : null;
  const firstUserTranscriptAt = Array.isArray(probe.userTranscripts) && probe.userTranscripts.length > 0
    ? probe.userTranscripts[0]?.completed_at_ms ?? null
    : null;
  const firstSpeechFetchAt = firstEventAt("speech_fetch");
  const firstAudioPlayAt = firstEventAt("audio_play");
  const firstSpeechStartedAt = firstEventAt("input_audio_buffer.speech_started");
  const firstSpeechStoppedAt = firstEventAt("input_audio_buffer.speech_stopped");
  return {
    firstSpeechStartedAt,
    firstSpeechStoppedAt,
    firstUserTranscriptAt,
    firstAssistantTranscriptAt,
    firstSpeechFetchAt,
    firstAudioPlayAt,
    speechStartToStopMs:
      firstSpeechStartedAt !== null && firstSpeechStoppedAt !== null
        ? Math.max(0, firstSpeechStoppedAt - firstSpeechStartedAt)
        : null,
    speechStopToTranscriptMs:
      firstSpeechStoppedAt !== null && firstUserTranscriptAt !== null
        ? Math.max(0, firstUserTranscriptAt - firstSpeechStoppedAt)
        : null,
    transcriptToAssistantTranscriptMs:
      firstUserTranscriptAt !== null && firstAssistantTranscriptAt !== null
        ? Math.max(0, firstAssistantTranscriptAt - firstUserTranscriptAt)
        : null,
    transcriptToSpeechFetchMs:
      firstUserTranscriptAt !== null && firstSpeechFetchAt !== null
        ? Math.max(0, firstSpeechFetchAt - firstUserTranscriptAt)
        : null,
    assistantTranscriptToAudioPlayMs:
      firstAssistantTranscriptAt !== null && firstAudioPlayAt !== null
        ? Math.max(0, firstAudioPlayAt - firstAssistantTranscriptAt)
        : null,
  };
}

async function installAppProbe(page) {
  await page.evaluate(() => {
    const startedAt = performance.now();
    const nowMs = () => Math.round(performance.now() - startedAt);
    const clean = (value, maxLength = 240) =>
      String(value ?? "")
        .replace(/\s+/g, " ")
        .trim()
        .slice(0, maxLength);
    const events = [];
    const audioElements = [];
    const speechRequests = [];
    const apiCalls = [];
    const userTranscripts = [];
    const assistantTranscripts = [];
    const responseAudioDrafts = Object.create(null);
    const responseTextDrafts = Object.create(null);

    const pushEvent = (type, payload = {}) => {
      events.push({ t_ms: nowMs(), type, ...payload });
      if (events.length > 400) {
        events.splice(0, events.length - 400);
      }
    };

    const registerAudio = (audio) => {
      if (!(audio instanceof HTMLAudioElement)) {
        return audio;
      }
      if (audio.__econPlaytestRegistered) {
        return audio;
      }
      audio.__econPlaytestRegistered = true;
      audio.dataset.econPlaytestId = audio.dataset.econPlaytestId || `audio-${audioElements.length + 1}`;
      audioElements.push(audio);
      audio.addEventListener("play", () => {
        pushEvent("audio_play", {
          audio_id: audio.dataset.econPlaytestId,
          has_stream: Boolean(audio.__econProbeStream || audio.srcObject),
        });
      });
      audio.addEventListener("pause", () => {
        pushEvent("audio_pause", { audio_id: audio.dataset.econPlaytestId });
      });
      audio.addEventListener("ended", () => {
        pushEvent("audio_ended", { audio_id: audio.dataset.econPlaytestId });
      });
      audio.addEventListener("error", () => {
        pushEvent("audio_error", { audio_id: audio.dataset.econPlaytestId });
      });
      return audio;
    };

    const existingAudios = Array.from(document.querySelectorAll("audio"));
    existingAudios.forEach((audio) => registerAudio(audio));

    const mediaProto = HTMLMediaElement.prototype;
    const srcObjectDescriptor = Object.getOwnPropertyDescriptor(mediaProto, "srcObject");
    if (srcObjectDescriptor?.get && srcObjectDescriptor?.set) {
      Object.defineProperty(mediaProto, "srcObject", {
        configurable: true,
        enumerable: srcObjectDescriptor.enumerable ?? false,
        get() {
          return srcObjectDescriptor.get.call(this);
        },
        set(value) {
          registerAudio(this);
          if (value instanceof MediaStream) {
            this.__econProbeStream = value;
            pushEvent("audio_src_object", {
              audio_id: this.dataset?.econPlaytestId || "",
              track_count: value.getAudioTracks().length,
            });
          } else {
            this.__econProbeStream = null;
          }
          return srcObjectDescriptor.set.call(this, value);
        },
      });
    }

    const originalCreateElement = Document.prototype.createElement;
    Document.prototype.createElement = function patchedCreateElement(tagName, options) {
      const element = originalCreateElement.call(this, tagName, options);
      if (String(tagName).toLowerCase() === "audio") {
        registerAudio(element);
      }
      return element;
    };

    const interestingRealtimeEvents = new Set([
      "input_audio_buffer.speech_started",
      "input_audio_buffer.speech_stopped",
      "conversation.interrupted",
      "conversation.item.input_audio_transcription.completed",
      "response.created",
      "response.done",
      "response.output_audio.delta",
      "response.output_audio_transcript.delta",
      "response.output_audio_transcript.done",
      "response.audio_transcript.delta",
      "response.audio_transcript.done",
      "response.output_text.done",
      "output_audio_buffer.started",
      "output_audio_buffer.stopped",
      "output_audio_buffer.cleared",
    ]);

    const captureRealtimePayload = (payload) => {
      if (!payload || typeof payload !== "object") {
        return;
      }
      const eventType = clean(payload.type, 80);
      if (!eventType || !interestingRealtimeEvents.has(eventType)) {
        return;
      }
      const responseId = clean(payload.response_id ?? payload.response?.id ?? "", 80);
      pushEvent(eventType, {
        response_id: responseId || undefined,
        transcript: clean(payload.transcript ?? "", 220) || undefined,
        text: clean(payload.text ?? "", 220) || undefined,
        delta: clean(payload.delta ?? "", 220) || undefined,
      });
      if (eventType === "conversation.item.input_audio_transcription.completed") {
        const transcript = clean(payload.transcript ?? "", 320);
        if (transcript) {
          userTranscripts.push({
            transcript,
            completed_at_ms: nowMs(),
          });
          if (userTranscripts.length > 8) {
            userTranscripts.splice(0, userTranscripts.length - 8);
          }
        }
        return;
      }
      if (eventType === "response.output_audio_transcript.delta" || eventType === "response.audio_transcript.delta") {
        if (responseId && payload.delta) {
          responseAudioDrafts[responseId] = `${responseAudioDrafts[responseId] ?? ""}${String(payload.delta)}`;
        }
        return;
      }
      if (eventType === "response.output_audio_transcript.done" || eventType === "response.audio_transcript.done") {
        const transcript = clean(payload.transcript ?? responseAudioDrafts[responseId] ?? "", 420);
        if (transcript) {
          assistantTranscripts.push({
            response_id: responseId || undefined,
            transcript,
            source: "audio",
            completed_at_ms: nowMs(),
          });
          if (assistantTranscripts.length > 12) {
            assistantTranscripts.splice(0, assistantTranscripts.length - 12);
          }
        }
        delete responseAudioDrafts[responseId];
        return;
      }
      if (eventType === "response.output_text.done") {
        const text = clean(payload.text ?? responseTextDrafts[responseId] ?? "", 420);
        if (text) {
          assistantTranscripts.push({
            response_id: responseId || undefined,
            transcript: text,
            source: "text",
            completed_at_ms: nowMs(),
          });
          if (assistantTranscripts.length > 12) {
            assistantTranscripts.splice(0, assistantTranscripts.length - 12);
          }
        }
        delete responseTextDrafts[responseId];
      }
    };

    const originalCreateDataChannel = RTCPeerConnection.prototype.createDataChannel;
    RTCPeerConnection.prototype.createDataChannel = function patchedCreateDataChannel(label, options) {
      const channel = originalCreateDataChannel.call(this, label, options);
      if (label === "oai-events" && !channel.__econPlaytestWrapped) {
        channel.__econPlaytestWrapped = true;
        channel.addEventListener("open", () => pushEvent("data_channel_open", { label }));
        channel.addEventListener("close", () => pushEvent("data_channel_close", { label }));
        channel.addEventListener("message", (event) => {
          try {
            captureRealtimePayload(JSON.parse(event.data));
          } catch {
            pushEvent("data_channel_parse_error", { label });
          }
        });
      }
      return channel;
    };

    const originalFetch = window.fetch.bind(window);
    window.fetch = async (...args) => {
      const [input, init] = args;
      const url = typeof input === "string" ? input : input instanceof URL ? input.toString() : input?.url || "";
      const requestBody = typeof init?.body === "string" ? init.body : "";
      let apiEntry = null;
      if (
        url.includes("/api/simulations/") ||
        url.includes("/api/realtime/session") ||
        url.includes("/api/realtime/tools/")
      ) {
        apiEntry = {
          id: `api-${apiCalls.length + 1}`,
          requested_at_ms: nowMs(),
          completed_at_ms: null,
          url,
          status: null,
          request_body: requestBody,
          response_text: "",
          error: "",
        };
        apiCalls.push(apiEntry);
        if (apiCalls.length > 16) {
          apiCalls.splice(0, apiCalls.length - 16);
        }
        pushEvent("api_fetch_start", {
          request_id: apiEntry.id,
          path: url.split("/api/")[1] || url,
        });
      }
      let response;
      try {
        response = await originalFetch(...args);
      } catch (error) {
        if (apiEntry) {
          apiEntry.completed_at_ms = nowMs();
          apiEntry.error = clean(error?.message || String(error), 220);
          pushEvent("api_fetch_error", {
            request_id: apiEntry.id,
            path: url.split("/api/")[1] || url,
            error: apiEntry.error,
          });
        }
        throw error;
      }
      try {
        if (apiEntry) {
          apiEntry.completed_at_ms = nowMs();
          apiEntry.status = response.status;
          pushEvent("api_fetch", {
            request_id: apiEntry.id,
            status: response.status,
            path: url.split("/api/")[1] || url,
          });
          response.clone().text()
            .then((bodyText) => {
              apiEntry.response_text = clean(bodyText, 420);
            })
            .catch(() => undefined);
        }
        if (url.includes("/api/audio/speech")) {
          let text = "";
          let voice = "";
          if (requestBody) {
            try {
              const parsed = JSON.parse(requestBody);
              text = clean(parsed?.text ?? "", 420);
              voice = clean(parsed?.voice ?? "", 40);
            } catch {
              // Ignore parse issues for telemetry.
            }
          }
          const requestEntry = {
            id: `speech-${speechRequests.length + 1}`,
            requested_at_ms: nowMs(),
            voice,
            text,
            status: response.status,
            content_type: response.headers.get("content-type") || "",
            bytes: null,
            byte_length: 0,
          };
          speechRequests.push(requestEntry);
          if (speechRequests.length > 8) {
            speechRequests.splice(0, speechRequests.length - 8);
          }
          pushEvent("speech_fetch", {
            request_id: requestEntry.id,
            voice,
            chars: text.length,
            status: response.status,
          });
          response.clone().arrayBuffer()
            .then((buffer) => {
              requestEntry.bytes = Array.from(new Uint8Array(buffer));
              requestEntry.byte_length = buffer.byteLength;
              pushEvent("speech_fetch_body", {
                request_id: requestEntry.id,
                bytes: buffer.byteLength,
              });
            })
            .catch((error) => {
              requestEntry.error = clean(error?.message || String(error), 180);
              pushEvent("speech_fetch_body_error", {
                request_id: requestEntry.id,
                error: requestEntry.error,
              });
            });
        }
      } catch {
        // Ignore probe-only failures.
      }
      return response;
    };

    window.__econSimPlaytestProbe = {
      snapshot() {
        return {
          events: events.slice(-120),
          userTranscripts: userTranscripts.slice(-6),
          assistantTranscripts: assistantTranscripts.slice(-8),
          apiCalls: apiCalls.slice(-8).map((entry) => ({
            id: entry.id,
            requested_at_ms: entry.requested_at_ms,
            completed_at_ms: entry.completed_at_ms,
            url: entry.url,
            status: entry.status,
            request_body: clean(entry.request_body, 220),
            response_text: clean(entry.response_text, 220),
            error: clean(entry.error, 220),
          })),
          speechRequests: speechRequests.map((entry) => ({
            id: entry.id,
            requested_at_ms: entry.requested_at_ms,
            voice: entry.voice,
            text: entry.text,
            status: entry.status,
            content_type: entry.content_type,
            byte_length: entry.byte_length,
            has_bytes: Array.isArray(entry.bytes) && entry.bytes.length > 0,
          })),
          audioElements: audioElements.map((audio) => ({
            id: audio.dataset.econPlaytestId || "",
            paused: audio.paused,
            muted: audio.muted,
            currentTime: audio.currentTime,
            hasSrcObject: Boolean(audio.__econProbeStream || audio.srcObject),
            canCapture: typeof audio.captureStream === "function",
          })),
        };
      },
      export() {
        return {
          events: [...events],
          userTranscripts: [...userTranscripts],
          assistantTranscripts: [...assistantTranscripts],
          apiCalls: apiCalls.map((entry) => ({ ...entry })),
          speechRequests: speechRequests.map((entry) => ({ ...entry })),
          audioElements: audioElements.map((audio) => ({
            id: audio.dataset.econPlaytestId || "",
            paused: audio.paused,
            muted: audio.muted,
            currentTime: audio.currentTime,
            hasSrcObject: Boolean(audio.__econProbeStream || audio.srcObject),
            canCapture: typeof audio.captureStream === "function",
          })),
        };
      },
      note(entry) {
        if (!entry || typeof entry !== "object") {
          return;
        }
        const nextType = clean(entry.type ?? "note", 80) || "note";
        const detail = Object.fromEntries(
          Object.entries(entry)
            .filter(([key]) => key !== "type")
            .map(([key, value]) => [key, typeof value === "string" ? clean(value, 240) : value]),
        );
        pushEvent(nextType, detail);
      },
      async startRecorder(durationMs = 15000) {
        const target = audioElements.find(
          (audio) => audio.__econProbeStream instanceof MediaStream && audio.__econProbeStream.getAudioTracks().length > 0,
        ) || audioElements.find((audio) => typeof audio.captureStream === "function");
        if (!target) {
          return { supported: false, reason: "No capturable audio element found yet." };
        }
        if (typeof MediaRecorder === "undefined") {
          return { supported: false, reason: "MediaRecorder is unavailable in this browser." };
        }
        const stream = target.__econProbeStream instanceof MediaStream
          ? new MediaStream(target.__econProbeStream.getAudioTracks())
          : target.captureStream();
        if (!stream || !stream.getAudioTracks().length) {
          return { supported: false, reason: "Audio capture stream had no tracks." };
        }
        const mimeType = ["audio/webm;codecs=opus", "audio/webm", "audio/ogg;codecs=opus"]
          .find((candidate) => MediaRecorder.isTypeSupported(candidate)) || "";
        const recorder = mimeType ? new MediaRecorder(stream, { mimeType }) : new MediaRecorder(stream);
        const chunks = [];
        let resolveStop;
        const stopPromise = new Promise((resolve) => {
          resolveStop = resolve;
        });
        recorder.ondataavailable = (event) => {
          if (event.data && event.data.size > 0) {
            chunks.push(event.data);
          }
        };
        recorder.onstop = async () => {
          try {
            const blob = new Blob(chunks, { type: recorder.mimeType || "audio/webm" });
            const buffer = await blob.arrayBuffer();
            resolveStop({
              mimeType: recorder.mimeType || "audio/webm",
              bytes: Array.from(new Uint8Array(buffer)),
            });
          } catch (error) {
            resolveStop({
              error: error instanceof Error ? error.message : String(error),
            });
          }
        };
        recorder.start(250);
        const watchdog = window.setTimeout(() => {
          if (recorder.state !== "inactive") {
            recorder.stop();
          }
        }, Math.max(1000, durationMs || 15000));
        window.__econSimPlaytestRecorder = {
          stop: async () => {
            window.clearTimeout(watchdog);
            if (recorder.state !== "inactive") {
              recorder.stop();
            }
            return stopPromise;
          },
          mimeType: recorder.mimeType || "audio/webm",
        };
        return { supported: true, mimeType: recorder.mimeType || "audio/webm" };
      },
    };
  });
}

async function collectSnapshot(page) {
  return page.evaluate(() => {
    const text = (selector) =>
      Array.from(document.querySelectorAll(selector))
        .map((node) => node.textContent || "")
        .map((item) => item.replace(/\s+/g, " ").trim())
        .filter(Boolean);
    const audioInfo = Array.from(document.querySelectorAll("audio")).map((audio, index) => ({
      index,
      paused: audio.paused,
      muted: audio.muted,
      currentTime: audio.currentTime,
      src: audio.getAttribute("src") || "",
      hasSrcObject: Boolean(audio.srcObject),
      canCapture: typeof audio.captureStream === "function",
    }));
    return {
      url: location.href,
      title: document.title,
      voiceMode: document.querySelector(".voice-dock__mode")?.textContent?.replace(/\s+/g, " ").trim() || null,
      voiceError: document.querySelector(".voice-dock__error")?.textContent?.replace(/\s+/g, " ").trim() || null,
      setupCaption: document.querySelector(".setup-room__caption p")?.textContent?.replace(/\s+/g, " ").trim() || null,
      setupSceneLabel: document.querySelector(".scene__hud--setup-room .scene__eyebrow")?.textContent?.replace(/\s+/g, " ").trim() || null,
      sceneCaption:
        document.querySelector(".scene__caption p")?.textContent?.replace(/\s+/g, " ").trim() ||
        document.querySelector(".scene__caption-lines")?.textContent?.replace(/\s+/g, " ").trim() ||
        null,
      assistantTurns: text(".voice-log__entry--assistant p, .setup-console__turn--assistant p"),
      userTurns: text(".voice-log__entry--user p, .setup-console__turn--user p"),
      systemTurns: text(".voice-log__entry--system p, .setup-console__turn--system p"),
      roomButtons: text(".scene-hotspot span, .scene__primary span, .scene__focusplate span"),
      audioInfo,
      probe: window.__econSimPlaytestProbe?.snapshot?.() ?? null,
    };
  });
}

async function sleep(ms) {
  await new Promise((resolve) => setTimeout(resolve, ms));
}

async function waitForText(page, predicate, timeoutMs, intervalMs = 400) {
  const started = Date.now();
  while (Date.now() - started < timeoutMs) {
    const snapshot = await collectSnapshot(page);
    if (predicate(snapshot)) {
      return snapshot;
    }
    await page.waitForTimeout(intervalMs);
  }
  throw new Error("Timed out waiting for the expected room state");
}

async function waitForProbe(page, predicate, timeoutMs, intervalMs = 350) {
  const started = Date.now();
  while (Date.now() - started < timeoutMs) {
    const probe = await page.evaluate(() => window.__econSimPlaytestProbe?.snapshot?.() ?? null);
    if (predicate(probe)) {
      return probe;
    }
    await page.waitForTimeout(intervalMs);
  }
  throw new Error("Timed out waiting for probe activity");
}

async function waitForSimulationReady(simId, timeoutMs) {
  const started = Date.now();
  const apiUrl = `http://127.0.0.1:8000/api/simulations/${simId}`;
  while (Date.now() - started < timeoutMs) {
    const requestController = new AbortController();
    const requestTimer = setTimeout(() => requestController.abort(), Math.min(5000, Math.max(1000, timeoutMs)));
    try {
      const response = await fetch(apiUrl, { signal: requestController.signal });
      if (!response.ok) {
        throw new Error(`Failed to inspect simulation ${simId}: ${response.status}`);
      }
      const payload = await response.json();
      if (payload.status === "stage_ready" || payload.status === "completed") {
        return payload;
      }
      if (payload.status === "error") {
        throw new Error(`Simulation ${simId} entered error state`);
      }
    } catch (error) {
      if (error?.name !== "AbortError") {
        throw error;
      }
    } finally {
      clearTimeout(requestTimer);
    }
    await sleep(3000);
  }
  throw new Error(`Timed out waiting for simulation ${simId} to reach stage_ready`);
}

async function maybeClickFirst(page, candidates) {
  for (const candidate of candidates) {
    const escaped = candidate.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    const locator = page.getByRole("button", { name: new RegExp(escaped, "i") }).first();
    if (await locator.count()) {
      await locator.click();
      return candidate;
    }
  }
  return null;
}

async function clickIfVisible(page, label) {
  const locator = page.getByRole("button", { name: label }).first();
  if (!(await locator.count())) {
    return false;
  }
  try {
    await locator.click();
    return true;
  } catch {
    try {
      await locator.click({ force: true });
      return true;
    } catch {
      return false;
    }
  }
}

async function clickLocatorIfVisible(locator, timeout = 8000) {
  if (!(await locator.count())) {
    return false;
  }
  if (!(await locator.isVisible().catch(() => false))) {
    return false;
  }
  try {
    await locator.click({ timeout });
    return true;
  } catch {
    try {
      await locator.click({ timeout, force: true });
      return true;
    } catch {
      return false;
    }
  }
}

async function skipStageReel(page, notes) {
  if (await clickIfVisible(page, /^Begin the chapter reel$/i)) {
    notes.push("Clicked Begin the chapter reel.");
    await page.waitForTimeout(1600);
  }
  if (await clickIfVisible(page, /^Skip$/i)) {
    notes.push("Clicked Skip.");
    await page.waitForTimeout(1600);
  }
}

async function clickRoom(page, room, advisorMode, auditoriumMode) {
  if (room === "advisor") {
    const clicked = await maybeClickFirst(
      page,
      advisorMode === "council"
        ? ["Council", "Multi-advisor table", "War Room", "Advisor"]
        : ["War Room", "Advisor", "Solo room", "Chief advisor room"],
    );
    if (clicked) {
      return clicked;
    }
  }
  if (room === "debate") {
    const clicked = await maybeClickFirst(page, ["Auditorium", "Town hall", "Debate stage", "Town hall floor"]);
    if (clicked) {
      return clicked;
    }
  }
  if (room === "citizens") {
    const clicked = await maybeClickFirst(page, ["Street", "Neighborhood street"]);
    if (clicked) {
      return clicked;
    }
  }
  if (room === "advisor" && advisorMode === "council") {
    const clicked = await maybeClickFirst(page, ["Council", "Multi-advisor table"]);
    if (clicked) {
      return clicked;
    }
  }
  if (room === "debate" && auditoriumMode === "town_hall") {
    const clicked = await maybeClickFirst(page, ["Town hall"]);
    if (clicked) {
      return clicked;
    }
  }
  return null;
}

async function writeTranscriptArtifacts(outDir, snapshots, audioMeta, probeMeta) {
  const transcriptJson = {
    generated_at: new Date().toISOString(),
    snapshots,
    audio: audioMeta,
    probe: probeMeta,
  };
  fs.writeFileSync(path.join(outDir, "transcript.json"), JSON.stringify(transcriptJson, null, 2));
  const lines = [
    "# Econ-sim audio playtest",
    "",
    `Generated at: ${transcriptJson.generated_at}`,
    "",
  ];
  for (const snapshot of snapshots) {
    lines.push(`## ${snapshot.label}`);
    lines.push(`URL: ${snapshot.url}`);
    if (snapshot.voiceMode) {
      lines.push(`Voice mode: ${snapshot.voiceMode}`);
    }
    if (snapshot.voiceError) {
      lines.push(`Voice error: ${snapshot.voiceError}`);
    }
    if (snapshot.setupCaption) {
      lines.push(`Setup caption: ${snapshot.setupCaption}`);
    }
    if (snapshot.sceneCaption) {
      lines.push(`Scene caption: ${snapshot.sceneCaption}`);
    }
    if (snapshot.assistantTurns.length > 0) {
      lines.push("Assistant turns:");
      for (const turn of snapshot.assistantTurns.slice(-8)) {
        lines.push(`- ${turn}`);
      }
    }
    if (snapshot.userTurns.length > 0) {
      lines.push("User turns:");
      for (const turn of snapshot.userTurns.slice(-8)) {
        lines.push(`- ${turn}`);
      }
    }
    if (snapshot.systemTurns.length > 0) {
      lines.push("System turns:");
      for (const turn of snapshot.systemTurns.slice(-6)) {
        lines.push(`- ${turn}`);
      }
    }
    if (snapshot.audioInfo.length > 0) {
      lines.push("Audio elements:");
      for (const audio of snapshot.audioInfo) {
        lines.push(
          `- #${audio.index} paused=${audio.paused} muted=${audio.muted} srcObject=${audio.hasSrcObject} canCapture=${audio.canCapture} src=${audio.src || "(empty)"}`,
        );
      }
    }
    if (snapshot.probe) {
      lines.push("Probe snapshot:");
      lines.push(`- user transcripts: ${snapshot.probe.userTranscripts?.length ?? 0}`);
      lines.push(`- assistant transcripts: ${snapshot.probe.assistantTranscripts?.length ?? 0}`);
      lines.push(`- speech fetches: ${snapshot.probe.speechRequests?.length ?? 0}`);
      lines.push(`- recent events: ${snapshot.probe.events?.length ?? 0}`);
    }
    lines.push("");
  }
  if (audioMeta?.capturedFile) {
    lines.push(`Captured audio: ${audioMeta.capturedFile}`);
    lines.push("");
  }
  if (Array.isArray(audioMeta?.speechFiles) && audioMeta.speechFiles.length > 0) {
    lines.push("Speech files:");
    for (const item of audioMeta.speechFiles) {
      const voice = item.voice ? ` voice=${item.voice}` : "";
      lines.push(`- ${item.filePath}${voice}`);
    }
    lines.push("");
  }
  if (probeMeta?.metrics) {
    lines.push("Probe metrics:");
    for (const [key, value] of Object.entries(probeMeta.metrics)) {
      lines.push(`- ${key}: ${value ?? "(n/a)"}`);
    }
    lines.push("");
  }
  fs.writeFileSync(path.join(outDir, "transcript.md"), `${lines.join("\n")}\n`);
  fs.writeFileSync(path.join(outDir, "summary.json"), JSON.stringify(transcriptJson, null, 2));
}

async function generateMicWav({ outDir, micText, micAudioFile }) {
  if (micAudioFile) {
    const resolved = path.resolve(micAudioFile);
    if (!fs.existsSync(resolved)) {
      throw new Error(`Mic audio file not found: ${resolved}`);
    }
    return resolved;
  }

  const utterance = safeText(micText) || "What should I do first?";
  const sayAvailable = (() => {
    try {
      execSync("command -v say >/dev/null 2>&1", { stdio: "ignore", shell: "/bin/zsh" });
      return true;
    } catch {
      return false;
    }
  })();
  const ffmpegPath = (() => {
    try {
      return execSync("command -v ffmpeg", { encoding: "utf8", shell: "/bin/zsh" }).trim();
    } catch {
      return "";
    }
  })();
  const afconvertPath = (() => {
    try {
      return execSync("command -v afconvert", { encoding: "utf8", shell: "/bin/zsh" }).trim();
    } catch {
      return "";
    }
  })();

  const wavPath = path.join(outDir, `fake-mic-${slug(utterance)}-${timestamp()}.wav`);
  if (sayAvailable) {
    const aiffPath = wavPath.replace(/\.wav$/, ".aiff");
    execFileSync("/usr/bin/say", ["-o", aiffPath, utterance], { stdio: "ignore" });
    if (ffmpegPath) {
      execFileSync(ffmpegPath, ["-y", "-i", aiffPath, "-ar", "24000", "-ac", "1", wavPath], { stdio: "ignore" });
    } else if (afconvertPath) {
      execFileSync(afconvertPath, ["-f", "WAVE", "-d", "LEI16@24000", "-c", "1", aiffPath, wavPath], { stdio: "ignore" });
    } else {
      throw new Error("Could not convert spoken audio to wav. Install ffmpeg or afconvert.");
    }
    try {
      fs.unlinkSync(aiffPath);
    } catch {
      // Ignore cleanup noise.
    }
    return wavPath;
  }

  const sampleRate = 24000;
  const seconds = 2.0;
  const totalSamples = Math.floor(sampleRate * seconds);
  const channels = 1;
  const bitsPerSample = 16;
  const bytesPerSample = bitsPerSample / 8;
  const data = Buffer.alloc(totalSamples * bytesPerSample * channels);
  for (let index = 0; index < totalSamples; index += 1) {
    const amplitude = index < totalSamples / 4 ? 0.02 : 0.008;
    const sample = Math.sin((index / sampleRate) * Math.PI * 2 * 220) * amplitude;
    data.writeInt16LE(Math.round(sample * 32767), index * bytesPerSample);
  }
  const header = Buffer.alloc(44);
  header.write("RIFF", 0);
  header.writeUInt32LE(36 + data.length, 4);
  header.write("WAVE", 8);
  header.write("fmt ", 12);
  header.writeUInt32LE(16, 16);
  header.writeUInt16LE(1, 20);
  header.writeUInt16LE(channels, 22);
  header.writeUInt32LE(sampleRate, 24);
  header.writeUInt32LE(sampleRate * channels * bytesPerSample, 28);
  header.writeUInt16LE(channels * bytesPerSample, 32);
  header.writeUInt16LE(bitsPerSample, 34);
  header.write("data", 36);
  header.writeUInt32LE(data.length, 40);
  fs.writeFileSync(wavPath, Buffer.concat([header, data]));
  return wavPath;
}

async function startAudioCapture(page, outDir, captureMs) {
  const supported = await page.evaluate((timeoutMs) => {
    return window.__econSimPlaytestProbe?.startRecorder?.(timeoutMs) ?? {
      supported: false,
      reason: "Playtest probe unavailable.",
    };
  }, captureMs);
  if (!supported?.supported) {
    return {
      supported: false,
      reason: supported?.reason || "Recorder did not start.",
    };
  }
  return {
    supported: true,
    mimeType: supported.mimeType,
    stop: async () => {
      const recorded = await page.evaluate(async () => {
        const recorder = window.__econSimPlaytestRecorder;
        if (!recorder || typeof recorder.stop !== "function") {
          return null;
        }
        const payload = await recorder.stop();
        delete window.__econSimPlaytestRecorder;
        return payload;
      });
      if (!recorded || recorded.error) {
        return recorded || { error: "Recorder did not return data." };
      }
      const extension = (recorded.mimeType || "audio/webm").includes("ogg") ? "ogg" : "webm";
      const filePath = path.join(outDir, `assistant-audio-${timestamp()}.${extension}`);
      fs.writeFileSync(filePath, Buffer.from(recorded.bytes));
      return { filePath, mimeType: recorded.mimeType };
    },
  };
}

async function bootSetupIfNeeded(page, args) {
  const currentUrl = new URL(page.url());
  const existingSimId = args.simId || currentUrl.searchParams.get("sim") || "";
  if (existingSimId) {
    return { simId: existingSimId, usedSetup: false };
  }

  await page.waitForSelector(".scene__inline-composer input", { timeout: args.readyTimeoutMs });
  await page.locator(".scene__inline-composer input").fill(args.setupPrompt);
  await page.locator(".scene__inline-composer button").click();
  await page.waitForTimeout(2500);

  if (!page.url().includes("?sim=")) {
    const launchButton = page.getByRole("button", { name: /Launch|Begin simulation/i }).first();
    await launchButton.waitFor({ timeout: args.readyTimeoutMs });
    await launchButton.click();
  }

  await page.waitForURL(/sim=/, { timeout: args.readyTimeoutMs });
  const simUrl = new URL(page.url());
  return {
    simId: simUrl.searchParams.get("sim") || "",
    usedSetup: true,
  };
}

async function ensureRoomVisible(page, args) {
  await page.waitForLoadState("domcontentloaded");
  await page.waitForTimeout(1200);
  await page.waitForSelector(".app-shell", { timeout: args.readyTimeoutMs });

  await skipStageReel(page, []);

  const requestedRoom = new URL(page.url()).searchParams.get("room");
  const needsRoomClick = !requestedRoom || requestedRoom !== args.room;

  if (needsRoomClick && (args.room === "advisor" || args.room === "citizens" || args.room === "debate")) {
    const clicked = await clickRoom(page, args.room, args.advisorMode, args.auditoriumMode);
    if (clicked) {
      await page.waitForTimeout(1400);
    }
  }

  if (needsRoomClick && args.room === "advisor" && args.advisorMode === "council") {
    const councilButton = page.getByRole("button", { name: /Council|Multi-advisor table/i }).first();
    if (await councilButton.count()) {
      await councilButton.click().catch(() => undefined);
      await page.waitForTimeout(1000);
    }
  }

  if (needsRoomClick && args.room === "debate" && args.auditoriumMode === "town_hall") {
    const townHallButton = page.getByRole("button", { name: /Town hall/i }).first();
    if (await townHallButton.count()) {
      await townHallButton.click().catch(() => undefined);
      await page.waitForTimeout(1000);
    }
  }
}

async function openVoice(page, args) {
  if (args.room === "debate" && args.auditoriumMode === "town_hall") {
    await clickLocatorIfVisible(page.getByRole("button", { name: /^Hide$/i }).first(), 3000);
    const callButton = page.getByTestId("townhall-call-on-voter").first();
    const visibleCallButton = page.getByRole("button", { name: /Call on voter/i }).last();
    const sceneCallButton = page.locator(".scene__voice-trigger").filter({ hasText: /Call on voter/i }).first();
    const callClicked =
      await clickLocatorIfVisible(sceneCallButton) ||
      await clickLocatorIfVisible(visibleCallButton) ||
      await clickLocatorIfVisible(callButton);
    if (callClicked) {
      await page.waitForTimeout(1200);
      await Promise.race([
        page.getByRole("button", { name: /Answer voter/i }).first().waitFor({ timeout: 20000 }).catch(() => undefined),
        page.locator(".scene-townhall-floor p").first().waitFor({ timeout: 20000 }).catch(() => undefined),
      ]);
    }
    const answerButton = page.getByRole("button", { name: /Answer voter/i }).last();
    if (await clickLocatorIfVisible(answerButton)) {
      return;
    }
  }
  const voiceButton = page.getByRole("button", { name: /Speak|Resume|Join|Voice/i }).first();
  await voiceButton.waitFor({ timeout: 15000 });
  await voiceButton.click();
}

async function run() {
  const args = parseArgs(process.argv);
  ensureDir(args.outDir);

  if (!fs.existsSync(args.gcftBin)) {
    throw new Error(`Google Chrome for Testing binary not found at: ${args.gcftBin}`);
  }

  const micText = safeText(args.micText) || (args.room === "debate" && args.auditoriumMode === "town_hall"
    ? "What should voters hear from me first?"
    : args.room === "advisor" && args.advisorMode === "council"
      ? "Where do you disagree, and what should we do first?"
      : "What should I do first, and what is the main tradeoff?");
  const micFile = await generateMicWav({ outDir: args.outDir, micText, micAudioFile: args.micAudioFile });

  const { chromium } = loadPlaywright(args.runtimeDir);
  const priorFrontmostApp = !args.headless && args.restoreFocus ? frontmostAppName() : "";
  const browser = await chromium.launch({
    headless: args.headless,
    executablePath: args.gcftBin,
    args: [
      "--autoplay-policy=no-user-gesture-required",
      "--use-fake-ui-for-media-stream",
      "--use-fake-device-for-media-stream",
      `--use-file-for-fake-audio-capture=${micFile}%noloop`,
    ],
  });
  if (priorFrontmostApp) {
    restoreFrontmostApp(priorFrontmostApp);
  }

  const page = await browser.newPage({
    viewport: { width: 1600, height: 980 },
  });

  const consoleLines = [];
  page.on("console", (message) => {
    consoleLines.push(`[console:${message.type()}] ${message.text()}`);
  });
  page.on("pageerror", (error) => {
    consoleLines.push(`[pageerror] ${error.stack || error.message}`);
  });
  page.on("requestfailed", (request) => {
    consoleLines.push(`[requestfailed] ${request.method()} ${request.url()} :: ${request.failure()?.errorText || "failed"}`);
  });

  const snapshots = [];
  const baseUrl = args.simId ? buildRoomUrl(args.url, args.simId, args.room, args.advisorMode, args.auditoriumMode) : args.url;
  await page.goto(baseUrl, { waitUntil: "domcontentloaded" });
  if (priorFrontmostApp) {
    restoreFrontmostApp(priorFrontmostApp);
  }
  await installAppProbe(page);
  await page.waitForTimeout(1500);
  await page.screenshot({ path: path.join(args.outDir, "01-loaded.png") });
  snapshots.push({ label: "loaded", ...(await collectSnapshot(page)) });

  let setupResult = { simId: new URL(page.url()).searchParams.get("sim") || args.simId || "", usedSetup: false };
  if (args.openSetup) {
    setupResult = await bootSetupIfNeeded(page, args);
    if (setupResult.usedSetup) {
      await page.screenshot({ path: path.join(args.outDir, "02-after-setup.png") });
      snapshots.push({ label: "after-setup", ...(await collectSnapshot(page)) });
    }
  } else if (!setupResult.simId) {
    throw new Error("No simulation id available. Pass --sim or allow setup bootstrap.");
  }

  if (setupResult.usedSetup && setupResult.simId) {
    await page.goto("about:blank", { waitUntil: "domcontentloaded" }).catch(() => undefined);
  }

  if (!page.url().includes("sim=") && setupResult.simId) {
    const simUrl = buildRoomUrl(args.url, setupResult.simId, args.room, args.advisorMode, args.auditoriumMode);
    await page.goto(simUrl, { waitUntil: "domcontentloaded" });
  }

  if (setupResult.simId) {
    try {
      await waitForSimulationReady(setupResult.simId, args.readyTimeoutMs);
      await page.waitForTimeout(3000);
    } catch (error) {
      consoleLines.push(`[ready] ${error instanceof Error ? error.message : String(error)}`);
    }
  }

  try {
    await ensureRoomVisible(page, args);
  } catch (error) {
    consoleLines.push(`[room] ${error instanceof Error ? error.message : String(error)}`);
  }
  await page.screenshot({ path: path.join(args.outDir, "03-room-visible.png") });
  snapshots.push({ label: "room-visible", ...(await collectSnapshot(page)) });

  let voiceOpened = false;
  try {
    await openVoice(page, args);
    await waitForText(
      page,
      (snapshot) => Boolean(snapshot.voiceError) || Boolean(snapshot.voiceMode && !/connecting|opening/i.test(snapshot.voiceMode)),
      Math.min(args.readyTimeoutMs, 45000),
    );
    voiceOpened = true;
  } catch (error) {
    consoleLines.push(`[voice] ${error instanceof Error ? error.message : String(error)}`);
  }
  if (voiceOpened) {
    await page.waitForTimeout(1200);
  }
  await page.screenshot({ path: path.join(args.outDir, "04-voice-open.png") });
  snapshots.push({ label: "voice-open", ...(await collectSnapshot(page)) });

  const recorder = await startAudioCapture(page, args.outDir, args.audioCaptureMs);
  if (!recorder.supported) {
    consoleLines.push(`[audio] capture unavailable: ${recorder.reason}`);
  } else {
    consoleLines.push(`[audio] recorder started (${recorder.mimeType})`);
  }
  if (voiceOpened) {
    try {
      await waitForProbe(
        page,
        (probe) =>
          Boolean(probe && (
            (probe.userTranscripts?.length ?? 0) > 0 ||
            (probe.events ?? []).some((event) => event.type === "input_audio_buffer.speech_stopped")
          )),
        Math.min(args.postMicWaitMs, 15000),
      );
    } catch (error) {
      consoleLines.push(`[probe:user] ${error instanceof Error ? error.message : String(error)}`);
    }
    try {
      const expectingHybridSpeech = (args.room === "advisor" && args.advisorMode === "council")
        || (args.room === "debate" && args.auditoriumMode === "town_hall");
      await waitForProbe(
        page,
        (probe) => {
          if (!probe) {
            return false;
          }
          const events = probe.events ?? [];
          if (expectingHybridSpeech) {
            const firstUserTranscriptAt = Number(probe.userTranscripts?.[0]?.completed_at_ms ?? 0);
            const townHallReplyPlayback =
              args.room === "debate" &&
              args.auditoriumMode === "town_hall" &&
              firstUserTranscriptAt > 0 &&
              events.some((event) =>
                Number(event.t_ms ?? 0) >= firstUserTranscriptAt &&
                (
                  event.type === "audio_play" ||
                  String(event.path || "").includes("town-hall-opponent-reply")
                ),
              );
            return (
              (probe.speechRequests?.length ?? 0) > 0 ||
              townHallReplyPlayback ||
              events.some((event) =>
                event.type === "council_audio_ready" ||
                event.type === "council_audio_error" ||
                event.type === "townhall_audio_ready" ||
                event.type === "townhall_audio_error",
              )
            );
          }
          return (probe.assistantTranscripts?.length ?? 0) > 0 || events.some((event) => event.type === "audio_play");
        },
        Math.min(args.postMicWaitMs + 8000, 26000),
      );
      await page.waitForTimeout(Math.min(4500, Math.max(1800, Math.floor(args.postMicWaitMs / 3))));
    } catch (error) {
      consoleLines.push(`[probe:assistant] ${error instanceof Error ? error.message : String(error)}`);
      await page.waitForTimeout(args.postMicWaitMs);
    }
  } else {
    await page.waitForTimeout(args.postMicWaitMs);
  }
  await page.screenshot({ path: path.join(args.outDir, "05-after-mic.png") });
  snapshots.push({ label: "after-mic", ...(await collectSnapshot(page)) });

  const audioMeta = {
    micText,
    micFile,
    recorderSupported: recorder.supported,
    recorderMimeType: recorder.supported ? recorder.mimeType : null,
  };

  if (recorder.supported) {
    try {
      if (recorder.filePath) {
        audioMeta.capturedFile = recorder.filePath;
        audioMeta.capturedMimeType = recorder.mimeType || null;
        consoleLines.push(`[audio] captured assistant playback to ${recorder.filePath}`);
      } else if (typeof recorder.stop === "function") {
        const stopped = await recorder.stop();
        if (stopped && stopped.filePath) {
          audioMeta.capturedFile = stopped.filePath;
          audioMeta.capturedMimeType = stopped.mimeType;
          consoleLines.push(`[audio] captured assistant playback to ${stopped.filePath}`);
        } else if (stopped && stopped.error) {
          audioMeta.captureError = stopped.error;
          consoleLines.push(`[audio] capture error: ${stopped.error}`);
        }
      }
    } catch (error) {
      audioMeta.captureError = error instanceof Error ? error.message : String(error);
      consoleLines.push(`[audio] capture error: ${audioMeta.captureError}`);
    }
  }

  const probeExport = await page.evaluate(() => window.__econSimPlaytestProbe?.export?.() ?? null);
  if (Array.isArray(probeExport?.speechRequests) && probeExport.speechRequests.length > 0) {
    audioMeta.speechFiles = [];
    for (const [index, entry] of probeExport.speechRequests.entries()) {
      if (!Array.isArray(entry?.bytes) || entry.bytes.length === 0) {
        continue;
      }
      const mimeType = String(entry.content_type || "audio/mpeg");
      const extension = extensionForMimeType(mimeType);
      const filePath = path.join(
        args.outDir,
        `speech-${String(index + 1).padStart(2, "0")}-${slug(`${entry.voice || "voice"} ${entry.text || ""}`)}.${extension}`,
      );
      fs.writeFileSync(filePath, Buffer.from(entry.bytes));
      audioMeta.speechFiles.push({
        filePath,
        mimeType,
        voice: String(entry.voice || ""),
        text: String(entry.text || ""),
        byteLength: entry.byte_length || entry.bytes.length,
      });
      consoleLines.push(`[audio] saved speech artifact ${filePath}`);
    }
  }

  snapshots.push({ label: "final", ...(await collectSnapshot(page)) });
  const probeMeta = probeExport
    ? {
        metrics: computeProbeMetrics(probeExport),
        userTranscripts: probeExport.userTranscripts ?? [],
        assistantTranscripts: probeExport.assistantTranscripts ?? [],
        eventCount: Array.isArray(probeExport.events) ? probeExport.events.length : 0,
        speechRequestCount: Array.isArray(probeExport.speechRequests) ? probeExport.speechRequests.length : 0,
      }
    : null;
  await writeTranscriptArtifacts(args.outDir, snapshots, audioMeta, probeMeta);
  fs.writeFileSync(path.join(args.outDir, "console.log"), `${consoleLines.join("\n")}\n`);

  const summary = {
    url: page.url(),
    outDir: args.outDir,
    room: args.room,
    advisorMode: args.advisorMode,
    auditoriumMode: args.auditoriumMode,
    micText,
    micFile,
    recorderSupported: audioMeta.recorderSupported,
    recorderMimeType: audioMeta.recorderMimeType,
    capturedAudio: audioMeta.capturedFile || null,
    probeMetrics: probeMeta?.metrics ?? null,
    userTranscripts: probeMeta?.userTranscripts ?? [],
    assistantTranscripts: probeMeta?.assistantTranscripts ?? [],
    speechArtifacts: audioMeta.speechFiles?.map((item) => item.filePath) ?? [],
    voiceMode: snapshots.at(-1)?.voiceMode || null,
    voiceError: snapshots.at(-1)?.voiceError || null,
  };
  fs.writeFileSync(path.join(args.outDir, "run-summary.json"), JSON.stringify(summary, null, 2));
  console.log(JSON.stringify(summary, null, 2));

  if (!args.keepOpen) {
    await browser.close();
  }
}

run().catch((error) => {
  console.error(error);
  process.exit(1);
});
