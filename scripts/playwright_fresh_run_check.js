#!/usr/bin/env node

const fs = require("node:fs");
const path = require("node:path");
const os = require("node:os");
const {
  DEFAULT_GCFT_BIN,
  loadPlaywright,
  launchChromiumBrowser,
} = require("./playwright_runtime");

const TARGET_URL = process.argv[2] || `http://127.0.0.1:5173/?fresh=${Date.now()}`;
const OUT_DIR =
  process.argv[3] || path.join(process.cwd(), "output", "playwright", `fresh-run-${new Date().toISOString().replace(/[:.]/g, "-")}`);
const COUNCIL_PROMPT =
  process.argv[4] ||
  "What are two serious policy ideas for a world where AI can do most computer work, and put the best ones on the board.";
const RUNTIME_DIR =
  process.env.PLAYWRIGHT_RUNTIME_DIR ||
  path.join(process.env.TMPDIR || os.tmpdir(), "econ-sim-playwright");

async function screenshot(page, name, notes) {
  const file = path.join(OUT_DIR, name);
  try {
    const capture = page.screenshot({ path: file, timeout: 24000 });
    capture.catch(() => undefined);
    await Promise.race([
      capture,
      new Promise((_, reject) => setTimeout(() => reject(new Error("outer screenshot timeout")), 28000)),
    ]);
    notes.push(`[screenshot] ${file}`);
  } catch (error) {
    notes.push(`[screenshot:${name}] ${error instanceof Error ? error.message : String(error)}`);
  }
}

async function clickUnique(page, selector, notes, label, options = {}) {
  const locator = page.locator(selector);
  const count = await locator.count();
  notes.push(`[locator] ${label}: ${count}`);
  if (count !== 1) {
    return false;
  }
  await locator.click({ timeout: options.timeout ?? 12000, force: Boolean(options.force) });
  notes.push(`[click] ${label}`);
  return true;
}

async function clickByText(page, text, notes, label = text) {
  const button = page.locator("button").filter({ hasText: text });
  const count = await button.count();
  notes.push(`[button-text] ${label}: ${count}`);
  if (count < 1) {
    return false;
  }
  await button.first().click({ timeout: 12000, force: true });
  notes.push(`[click] ${label}`);
  return true;
}

async function safeText(page, selector) {
  try {
    const locator = page.locator(selector).first();
    if (await locator.count()) {
      return await locator.innerText({ timeout: 3000 });
    }
  } catch {
    return null;
  }
  return null;
}

async function waitForStageReady(page, notes) {
  const started = Date.now();
  await page.waitForFunction(
    () =>
      Array.from(document.querySelectorAll("button")).some((button) =>
        (button.textContent || "").includes("Begin the chapter reel"),
      ) || Boolean(document.querySelector(".scene__channel-bar")),
    undefined,
    { timeout: Number(process.env.FRESH_RUN_READY_TIMEOUT_MS || 420000) },
  );
  const elapsedMs = Date.now() - started;
  notes.push(`[timing] stage-ready-ms=${elapsedMs}`);
  return elapsedMs;
}

function simulationIdFromUrl(rawUrl) {
  try {
    return new URL(rawUrl).searchParams.get("sim");
  } catch {
    return null;
  }
}

async function fetchSimulation(simulationId) {
  const response = await fetch(`http://127.0.0.1:8000/api/simulations/${simulationId}`);
  if (!response.ok) {
    throw new Error(`simulation fetch failed ${response.status}`);
  }
  return response.json();
}

async function run() {
  fs.mkdirSync(OUT_DIR, { recursive: true });
  const { chromium } = loadPlaywright(RUNTIME_DIR);
  const { browser, launcher, launchErrors } = await launchChromiumBrowser(chromium, {
    headless: process.env.PLAYWRIGHT_HEADLESS === "1",
    gcftBin: process.env.PLAYWRIGHT_GCFT_BIN || DEFAULT_GCFT_BIN,
  });

    const notes = [];
    let watchCouncilTurn = false;
    let councilTurnResponses = 0;
    let page;
  try {
    page = await browser.newPage({
      viewport: { width: 1510, height: 960 },
      permissions: [],
    });
    page.on("console", (message) => {
      const rendered = message.text();
      if (!rendered.includes("[vite]")) {
        notes.push(`[console:${message.type()}] ${rendered}`);
      }
    });
    page.on("pageerror", (error) => notes.push(`[pageerror] ${error.message}`));
    page.on("response", (response) => {
      const url = response.url();
      if (url.includes("/advisor/council-turn") || url.includes("/api/simulations")) {
        notes.push(`[response] ${response.status()} ${url}`);
      }
      if (watchCouncilTurn && url.includes("/advisor/council-turn")) {
        councilTurnResponses += 1;
      }
    });

    await page.goto(TARGET_URL, { waitUntil: "domcontentloaded" });
    await page.waitForLoadState("domcontentloaded");
    await page.waitForTimeout(1800);
    await screenshot(page, "01-setup.png", notes);

    let loadMs = 0;
    const launchButton = page.locator("button.scene__launch-button--inline");
    if (await launchButton.count()) {
      await launchButton.waitFor({ state: "visible", timeout: 45000 });
      if (!(await launchButton.isEnabled())) {
        throw new Error("setup launch button visible but not enabled");
      }
      await launchButton.click();
      notes.push("[click] setup Launch");
      await screenshot(page, "02-loading.png", notes);
      loadMs = await waitForStageReady(page, notes);
    } else {
      notes.push("[setup] existing simulation URL; waiting for chapter reel gate");
      loadMs = await waitForStageReady(page, notes);
    }
    await screenshot(page, "03-stage-ready.png", notes);
    const simId = simulationIdFromUrl(page.url());
    if (!simId) {
      throw new Error(`could not read simulation id from ${page.url()}`);
    }

    const stageSnapshot = await fetchSimulation(simId);
    const activeStage = stageSnapshot.stages?.[stageSnapshot.active_stage_index ?? 0];
    notes.push(`[stage] ${activeStage?.year ?? "?"} ${activeStage?.title ?? "untitled"}`);
    notes.push(`[world] ${(activeStage?.world_brief ?? "").slice(0, 420)}`);

    await clickByText(page, "Begin the chapter reel", notes);
    await page.waitForTimeout(3600);
    await screenshot(page, "04-reel.png", notes);
    await clickByText(page, "Skip", notes);
    await page.waitForTimeout(2000);
    await screenshot(page, "05-after-reel.png", notes);

    await page.goto(`http://127.0.0.1:5173/?sim=${simId}&advisor=council&room=advisor&view=live&fresh=${Date.now()}`, {
      waitUntil: "domcontentloaded",
    });
    await page.locator(".scene__canvas canvas").first().waitFor({ state: "visible", timeout: 20000 }).catch(() => undefined);
    await page.waitForTimeout(5200);
    await screenshot(page, "06-council-room.png", notes);

    const textButton = page.getByTestId("scene-text-trigger").first();
    if (await textButton.count()) {
      await textButton.click({ force: true });
      notes.push("[click] text composer");
      await page.waitForTimeout(500);
    }
    const composer = page.getByTestId("scene-inline-input").first();
    if ((await composer.count()) < 1) {
      throw new Error("no visible council text composer");
    }
    const beforeCaption = await safeText(page, ".scene__caption p");
    watchCouncilTurn = true;
    councilTurnResponses = 0;
    const councilResponsePromise = page.waitForResponse(
      (response) => response.url().includes("/advisor/council-turn"),
      { timeout: 90000 },
    ).catch((error) => {
      notes.push(`[wait:council-turn] ${error instanceof Error ? error.message : String(error)}`);
      return null;
    });
    await composer.fill(COUNCIL_PROMPT);
    const inlineSend = page.getByTestId("scene-inline-send").first();
    await inlineSend.click({ force: true });
    notes.push(`[council] before-caption=${beforeCaption ?? ""}`);
    const councilResponse = await councilResponsePromise;
    await page.waitForFunction(
      (previous) => {
        const caption = document.querySelector(".scene__caption p")?.textContent?.trim() ?? "";
        return Boolean(caption && caption !== previous);
      },
      beforeCaption ?? "",
      { timeout: 90000 },
    ).catch((error) => notes.push(`[wait:council-caption] ${error instanceof Error ? error.message : String(error)}`));
    await page.waitForTimeout(1200);
    watchCouncilTurn = false;
    if (councilTurnResponses < 1 || !councilResponse) {
      throw new Error("council text turn did not call /advisor/council-turn");
    }
    await screenshot(page, "07-council-after-prompt.png", notes);

    const afterCouncil = await fetchSimulation(simId);
    const afterStage = afterCouncil.stages?.[afterCouncil.active_stage_index ?? 0];
    const summary = {
      targetUrl: TARGET_URL,
      url: page.url(),
      browserLauncher: launcher,
      browserLaunchErrors: launchErrors,
      simulationId: simId,
      loadMs,
      stage: {
        index: afterStage?.index,
        year: afterStage?.year,
        title: afterStage?.title,
        worldBrief: afterStage?.world_brief,
        macroStats: afterStage?.macro_stats,
        policyNotes: afterStage?.policy_notes,
        pollSummaries: afterStage?.poll_summaries?.slice?.(0, 5) ?? [],
      },
      caption: await safeText(page, ".scene__caption p"),
      floor: await safeText(page, ".scene-council-floor"),
      commandStrip: await safeText(page, ".scene__command-strip"),
      actions:
        (await page.locator(".scene__action-row button").count()) > 0
          ? await page.locator(".scene__action-row button").allInnerTexts()
          : [],
      notes,
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
