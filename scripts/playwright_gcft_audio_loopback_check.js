#!/usr/bin/env node

const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { execFileSync, execSync } = require("node:child_process");
const { createRequire } = require("node:module");
const { installHybridAudioProbe } = require("./hybrid_audio_probe");

const TARGET_URL =
  process.argv[2] || "http://127.0.0.1:8000/?sim=sim_864434c8b4a9&advisor=solo";
const OUT_DIR =
  process.argv[3] || "/Users/hemanth/code/econ-sim/output/playwright/gcft-audio-loopback";
const USER_TEXT =
  process.argv[4] || "What do you think is happening in the economy right now, and what should I ignore for the moment?";
const FLOW = (process.argv[5] || "solo-advisor").trim().toLowerCase();
const RECORD_MS = Number.parseInt(process.argv[6] || "18000", 10);
const SAY_VOICE = process.env.ECON_SIM_LOOPBACK_VOICE || "Samantha";
const GCFT_BIN =
  process.env.PLAYWRIGHT_GCFT_BIN ||
  path.join(
    os.homedir(),
    "Library/Caches/ms-playwright/chromium-1208/chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing",
  );
const RUNTIME_DIR =
  process.env.PLAYWRIGHT_RUNTIME_DIR ||
  path.join("/Users/hemanth/code/econ-sim/output/playwright", "runtime");

function ensureRuntime() {
  fs.mkdirSync(RUNTIME_DIR, { recursive: true });
  const packageJson = path.join(RUNTIME_DIR, "package.json");
  if (!fs.existsSync(packageJson)) {
    execSync("npm init -y >/dev/null 2>&1", { cwd: RUNTIME_DIR, stdio: "inherit", shell: "/bin/zsh" });
  }
  const playwrightDir = path.join(RUNTIME_DIR, "node_modules", "playwright");
  if (!fs.existsSync(playwrightDir)) {
    execSync("PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 npm install playwright >/dev/null 2>&1", {
      cwd: RUNTIME_DIR,
      stdio: "inherit",
      shell: "/bin/zsh",
    });
  }
}

function loadPlaywright() {
  ensureRuntime();
  const runtimeRequire = createRequire(path.join(RUNTIME_DIR, "package.json"));
  return runtimeRequire("playwright");
}

function ensureDir(dirPath) {
  fs.mkdirSync(dirPath, { recursive: true });
}

function buildFakeMicWav(text, outDir) {
  const aiffPath = path.join(outDir, "loopback-user-input.aiff");
  const wavPath = path.join(outDir, "loopback-user-input.wav");
  execFileSync("/usr/bin/say", ["-v", SAY_VOICE, "-o", aiffPath, text], { stdio: "ignore" });
  execFileSync("/opt/homebrew/bin/ffmpeg", ["-y", "-i", aiffPath, "-ar", "48000", "-ac", "1", wavPath], {
    stdio: "ignore",
  });
  return wavPath;
}

async function safeClick(locator) {
  if (!(await locator.count())) {
    return false;
  }
  try {
    await locator.first().click();
    return true;
  } catch {
    try {
      await locator.first().click({ force: true });
      return true;
    } catch {
      return false;
    }
  }
}

async function clickIfVisible(page, label) {
  const button = page.getByRole("button", { name: label }).first();
  if (!(await button.count()) || !(await button.isVisible().catch(() => false))) {
    return false;
  }
  return safeClick(button);
}

async function clickRoomHotspot(page, label) {
  const textLocator = page.getByText(label, { exact: false }).first();
  if (!(await textLocator.count())) {
    return false;
  }
  return safeClick(textLocator);
}

async function skipStageReel(page, notes) {
  if (await clickIfVisible(page, /Begin the chapter reel/i)) {
    notes.push("Clicked Begin the chapter reel.");
    await page.waitForTimeout(1600);
  }
  if (await clickIfVisible(page, /^Skip$/i)) {
    notes.push("Clicked Skip.");
    await page.waitForTimeout(1800);
  }
}

async function prepareFlow(page, flow, notes) {
  if (flow === "solo-advisor" || flow === "council") {
    if (await clickIfVisible(page, /^War Room$/i) || await clickRoomHotspot(page, /^WAR ROOM$/i)) {
      notes.push("Moved to war room.");
      await page.waitForTimeout(1800);
    }
    if (flow === "council") {
      const multiAdvisorTab = page.getByRole("button", { name: /^Multi-advisor$/i }).first();
      if (await multiAdvisorTab.count()) {
        const selected = await multiAdvisorTab.getAttribute("aria-selected").catch(() => null);
        if (selected !== "true") {
          await safeClick(multiAdvisorTab);
          notes.push("Switched to multi-advisor mode.");
          await page.waitForTimeout(1200);
        }
      }
    }
    return;
  }
  if (flow === "street") {
    if (await clickIfVisible(page, /^Street$/i) || await clickRoomHotspot(page, /^STREET$/i)) {
      notes.push("Moved to street.");
      await page.waitForTimeout(1800);
    }
    return;
  }
  if (flow === "townhall" || flow === "town-hall") {
    if (await clickIfVisible(page, /^Town Hall$/i) || await clickRoomHotspot(page, /^TOWN HALL$/i)) {
      notes.push("Moved to town hall.");
      await page.waitForTimeout(1800);
    }
    return;
  }
  if (flow === "debate") {
    if (await clickIfVisible(page, /^Auditorium$/i) || await clickRoomHotspot(page, /^AUDITORIUM$/i)) {
      notes.push("Moved to auditorium.");
      await page.waitForTimeout(1800);
    }
  }
}

function installAudioProbe(page) {
  return page.addInitScript(() => {
    const tracked = [];
    const events = [];

    const registerAudio = (element) => {
      if (!(element instanceof HTMLAudioElement)) {
        return element;
      }
      if (!tracked.includes(element)) {
        tracked.push(element);
        element.dataset.econProbeAudio = `audio-${tracked.length}`;
        element.addEventListener("play", () => {
          events.push({ type: "play", id: element.dataset.econProbeAudio, at: Date.now() });
        });
        element.addEventListener("ended", () => {
          events.push({ type: "ended", id: element.dataset.econProbeAudio, at: Date.now() });
        });
      }
      return element;
    };

    const originalCreateElement = Document.prototype.createElement;
    Document.prototype.createElement = function patchedCreateElement(tagName, options) {
      const element = originalCreateElement.call(this, tagName, options);
      if (String(tagName).toLowerCase() === "audio") {
        registerAudio(element);
      }
      return element;
    };

    window.__econAudioProbe = {
      tracked,
      events,
      snapshot() {
        return tracked.map((audio) => ({
          id: audio.dataset.econProbeAudio || "",
          paused: audio.paused,
          muted: audio.muted,
          currentTime: audio.currentTime,
          hasSrcObject: audio.srcObject instanceof MediaStream,
          src: audio.getAttribute("src") || "",
        }));
      },
      async record(durationMs = 15000) {
        const AudioContextCtor = window.AudioContext || window.webkitAudioContext;
        if (!AudioContextCtor) {
          return { error: "AudioContext unavailable", tracked: tracked.length };
        }
        const audioContext = new AudioContextCtor();
        const destination = audioContext.createMediaStreamDestination();
        let sourceCount = 0;

        for (const audio of tracked) {
          let mediaStream = null;
          try {
            if (typeof audio.captureStream === "function") {
              mediaStream = audio.captureStream();
            }
          } catch {}
          if (!mediaStream && audio.srcObject instanceof MediaStream) {
            mediaStream = audio.srcObject;
          }
          if (!(mediaStream instanceof MediaStream)) {
            continue;
          }
          try {
            const source = audioContext.createMediaStreamSource(mediaStream);
            source.connect(destination);
            sourceCount += 1;
          } catch {}
        }

        if (sourceCount === 0 || destination.stream.getAudioTracks().length === 0) {
          await audioContext.close().catch(() => undefined);
          return { error: "No assistant audio stream was captureable", tracked: tracked.length };
        }

        const mimeType = MediaRecorder.isTypeSupported("audio/webm;codecs=opus")
          ? "audio/webm;codecs=opus"
          : "audio/webm";
        const chunks = [];
        const recorder = new MediaRecorder(destination.stream, { mimeType });
        recorder.ondataavailable = (event) => {
          if (event.data && event.data.size > 0) {
            chunks.push(event.data);
          }
        };

        const stopPromise = new Promise((resolve) => {
          recorder.addEventListener("stop", resolve, { once: true });
        });

        recorder.start();
        await new Promise((resolve) => window.setTimeout(resolve, durationMs));
        recorder.stop();
        await stopPromise;

        const blob = new Blob(chunks, { type: mimeType });
        const bytes = Array.from(new Uint8Array(await blob.arrayBuffer()));
        destination.stream.getTracks().forEach((track) => track.stop());
        await audioContext.close().catch(() => undefined);
        return {
          bytes,
          mimeType,
          tracked: tracked.length,
          sourceCount,
          events,
        };
      },
    };
  });
}

async function collectDomSnapshot(page) {
  return page.evaluate(() => {
    const text = (selector) => document.querySelector(selector)?.textContent?.replace(/\s+/g, " ").trim() || null;
    const texts = (selector) =>
      Array.from(document.querySelectorAll(selector))
        .map((node) => node.textContent?.replace(/\s+/g, " ").trim() || "")
        .filter(Boolean);
    return {
      caption: text(".scene__caption p"),
      captionSpeaker: text(".scene__caption span"),
      councilFloor: text(".scene-council-floor"),
      townHallBanner: text(".scene-townhall-floor p"),
      townHallLabel: text(".scene-townhall-floor span"),
      townHallSpeaker: text(".scene-townhall-floor strong"),
      voiceDockError: text(".voice-dock__error"),
      voiceLogEntries: texts(".voice-log__entry"),
      probeSignals: window.__econAudioProbe?.getSignals?.() ?? null,
      probeAudios: window.__econAudioProbe?.snapshot?.() ?? [],
    };
  });
}

async function run() {
  if (!fs.existsSync(GCFT_BIN)) {
    throw new Error(`GCFT binary not found at ${GCFT_BIN}`);
  }
  ensureDir(OUT_DIR);
  const fakeMicPath = buildFakeMicWav(USER_TEXT, OUT_DIR);
  const fakeMicArg = `${fakeMicPath}%noloop`;

  const { chromium } = loadPlaywright();
  const browser = await chromium.launch({
    headless: true,
    executablePath: GCFT_BIN,
    args: [
      "--use-fake-ui-for-media-stream",
      "--use-fake-device-for-media-stream",
      `--use-file-for-fake-audio-capture=${fakeMicArg}`,
      "--autoplay-policy=no-user-gesture-required",
    ],
  });

  try {
  const page = await browser.newPage({
    viewport: { width: 1510, height: 960 },
  });
  const notes = [];
  const consoleLines = [];

    page.on("console", (message) => {
      const line = `[console:${message.type()}] ${message.text()}`;
      consoleLines.push(line);
      notes.push(line);
    });
    page.on("pageerror", (error) => {
      const line = `[pageerror] ${error.stack || error.message}`;
      consoleLines.push(line);
      notes.push(line);
    });

    await page.goto(TARGET_URL, { waitUntil: "domcontentloaded" });
    await page.waitForTimeout(2200);
    await skipStageReel(page, notes);
    await prepareFlow(page, FLOW, notes);
    await installHybridAudioProbe(page);

    await page.screenshot({ path: path.join(OUT_DIR, "01-before-loopback.png"), fullPage: true });

    const voiceTrigger = page.locator(".scene__voice-trigger").first();
    if (!(await voiceTrigger.count())) {
      throw new Error("Could not find the main scene voice trigger.");
    }

    await safeClick(voiceTrigger);
    notes.push("Clicked scene voice trigger.");
    await page.waitForTimeout(1200);

    const recording = await page.evaluate(async (durationMs) => {
      return window.__econAudioProbe?.record?.(durationMs);
    }, RECORD_MS);

    await page.waitForTimeout(600);
    await page.screenshot({ path: path.join(OUT_DIR, "02-after-loopback.png"), fullPage: true });

    const snapshot = await collectDomSnapshot(page);

    let audioWebmPath = null;
    let audioWavPath = null;
    if (recording && Array.isArray(recording.bytes) && recording.bytes.length > 0) {
      audioWebmPath = path.join(OUT_DIR, "assistant-output.webm");
      fs.writeFileSync(audioWebmPath, Buffer.from(recording.bytes));
      audioWavPath = path.join(OUT_DIR, "assistant-output.wav");
      try {
        execFileSync("/opt/homebrew/bin/ffmpeg", ["-y", "-i", audioWebmPath, audioWavPath], { stdio: "ignore" });
      } catch (error) {
        notes.push(`ffmpeg wav conversion failed: ${error instanceof Error ? error.message : String(error)}`);
        audioWavPath = null;
      }
    }

    const summary = {
      url: page.url(),
      flow: FLOW,
      userText: USER_TEXT,
      fakeMicPath,
      recording,
      audioWebmPath,
      audioWavPath,
      snapshot,
      notes,
      consoleLines,
    };
    fs.writeFileSync(path.join(OUT_DIR, "summary.json"), JSON.stringify(summary, null, 2));
    console.log(JSON.stringify(summary, null, 2));
  } finally {
    await browser.close();
  }
}

run().catch((error) => {
  console.error(error);
  process.exit(1);
});
