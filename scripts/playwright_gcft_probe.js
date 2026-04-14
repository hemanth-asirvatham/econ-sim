#!/usr/bin/env node

const fs = require("node:fs");
const path = require("node:path");
const os = require("node:os");
const {
  DEFAULT_GCFT_BIN,
  loadPlaywright,
  launchChromiumBrowser,
} = require("./playwright_runtime");

const BASE_URL = process.env.ECON_SIM_BASE_URL || process.argv[2] || "http://127.0.0.1:5173/";
const API_ORIGIN = new URL(BASE_URL).origin;
const OUT_DIR = process.env.ECON_SIM_QA_DIR || process.argv[3] || "/Users/hemanth/code/econ-sim/output/playwright/gcft-probe";
const SETUP_PROMPT = process.env.ECON_SIM_SETUP_PROMPT || process.argv[4] || "Launch the broad default United States simulation.";
const FOLLOWUP_PROMPT = process.env.ECON_SIM_FOLLOWUP_PROMPT || process.argv[5] || "go";
const READY_TIMEOUT_MS = Number.parseInt(process.env.ECON_SIM_READY_TIMEOUT_MS || process.argv[6] || "90000", 10);
const KEEP_OPEN = process.env.PLAYWRIGHT_KEEP_OPEN === "1";
const RUNTIME_DIR =
  process.env.PLAYWRIGHT_RUNTIME_DIR ||
  path.join(process.env.TMPDIR || os.tmpdir(), "econ-sim-playwright");

async function sleep(ms) {
  await new Promise((resolve) => setTimeout(resolve, ms));
}

async function fetchSimulation(simId) {
  const response = await fetch(`${API_ORIGIN}/api/simulations/${simId}`);
  if (!response.ok) {
    throw new Error(`Failed to fetch simulation ${simId}: ${response.status}`);
  }
  return response.json();
}

async function waitForSimulation(simId, { timeoutMs = 240000, intervalMs = 3000 } = {}) {
  const started = Date.now();
  while (Date.now() - started < timeoutMs) {
    try {
      const payload = await fetchSimulation(simId);
      if (payload.status === "stage_ready") {
        return payload;
      }
      if (payload.status === "initializing" || payload.status === "resolving" || payload.progress) {
        await sleep(intervalMs);
        continue;
      }
      return payload;
    } catch {
      await sleep(intervalMs);
    }
  }
  throw new Error(`Timed out waiting for simulation ${simId}`);
}

async function clickComposerSend(page) {
  await page.locator(".scene__inline-composer button").click();
}

async function screenshot(page, name, consoleLines, options = {}) {
  try {
    await page.screenshot({
      path: path.join(OUT_DIR, name),
      timeout: 12000,
      ...options,
    });
  } catch (error) {
    consoleLines.push({
      type: "screenshot",
      text: `${name}: ${error instanceof Error ? error.message : String(error)}`,
    });
  }
}

async function run() {
  fs.mkdirSync(OUT_DIR, { recursive: true });
  const { chromium } = loadPlaywright(RUNTIME_DIR);
  const { browser, launcher, launchErrors } = await launchChromiumBrowser(chromium, {
    headless: false,
    gcftBin: process.env.PLAYWRIGHT_GCFT_BIN || DEFAULT_GCFT_BIN,
  });

  let keepOpenBrowser = false;
  try {
    const page = await browser.newPage({
      viewport: { width: 1510, height: 960 },
    });
    const consoleLines = [];
    page.on("console", (message) => {
      consoleLines.push({ type: message.type(), text: message.text() });
    });
    page.on("pageerror", (error) => {
      consoleLines.push({ type: "pageerror", text: error.message });
    });

    await page.goto(BASE_URL, { waitUntil: "domcontentloaded" });
    try {
      await page.locator(".scene__channel-bar--setup .scene__inline-composer input").first().waitFor({ timeout: 60000 });
    } catch (error) {
      await screenshot(page, "00-root-timeout.png", consoleLines, { fullPage: true });
      throw error;
    }
    await page.waitForTimeout(1500);
    await screenshot(page, "01-root.png", consoleLines);

    const input = page.locator(".scene__channel-bar--setup .scene__inline-composer input");
    await input.fill(SETUP_PROMPT);
    await clickComposerSend(page);
    await page.waitForTimeout(3500);
    await screenshot(page, "02-root-after-setup-prompt.png", consoleLines);

    let launchStartedAt = Date.now();
    if (!/\?sim=/.test(page.url())) {
      const launchButton = page.getByRole("button", { name: /Launch|Begin simulation/i }).first();
      if (await launchButton.isVisible().catch(() => false)) {
        await launchButton.click();
      } else {
        const followupInput = page.locator(".scene__channel-bar--setup .scene__inline-composer input");
        await followupInput.waitFor({ timeout: 30000 });
        await followupInput.fill(FOLLOWUP_PROMPT);
        await clickComposerSend(page);
      }
      await page.waitForURL(/\?sim=/, { timeout: 30000 });
      launchStartedAt = Date.now();
    }
    await page.waitForTimeout(2500);
    await screenshot(page, "03-after-launch.png", consoleLines);

    const currentUrl = new URL(page.url());
    const simId = currentUrl.searchParams.get("sim");
    if (!simId) {
      throw new Error("Expected a simulation id in the URL after launch.");
    }

    let simulation;
    let stageReady = false;
    let stageReadyAt = null;
    try {
      simulation = await waitForSimulation(simId, { timeoutMs: READY_TIMEOUT_MS, intervalMs: 3000 });
      stageReady = simulation.status === "stage_ready";
      if (stageReady) {
        stageReadyAt = Date.now();
      }
    } catch {
      simulation = await fetchSimulation(simId);
      stageReady = simulation.status === "stage_ready";
      if (stageReady) {
        stageReadyAt = Date.now();
      }
    }
    await page.goto(`${BASE_URL.replace(/\/?$/, "")}/?sim=${simId}`, { waitUntil: "domcontentloaded" });
    await page.waitForTimeout(2500);
    await screenshot(page, stageReady ? "04-stage-ready.png" : "04-stage-pending.png", consoleLines);

    const summary = {
      simId,
      url: page.url(),
      browserLauncher: launcher,
      browserLaunchErrors: launchErrors,
      country: simulation.config?.country,
      status: simulation.status,
      stageReady,
      launchStartedAt,
      stageReadyAt,
      stageReadyMs: stageReadyAt ? stageReadyAt - launchStartedAt : null,
      currentRoom: simulation.current_room,
      phase: simulation.stages?.[simulation.active_stage_index]?.phase_label,
      title: simulation.stages?.[simulation.active_stage_index]?.title,
      policyNotes: simulation.stages?.[simulation.active_stage_index]?.policy_notes ?? [],
      consoleLines,
    };
    fs.writeFileSync(path.join(OUT_DIR, "summary.json"), JSON.stringify(summary, null, 2));
    console.log(JSON.stringify(summary, null, 2));
    keepOpenBrowser = KEEP_OPEN;
  } finally {
    if (!keepOpenBrowser) {
      await browser.close();
    }
  }
}

run().catch((error) => {
  console.error(error);
  process.exit(1);
});
