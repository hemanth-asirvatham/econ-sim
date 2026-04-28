#!/usr/bin/env node

const fs = require("node:fs");
const path = require("node:path");
const os = require("node:os");
const { execSync } = require("node:child_process");
const { createRequire } = require("node:module");

const TARGET_URL =
  process.argv[2] || "http://127.0.0.1:5173/?sim=sim_6a82891d616a&advisor=council&room=advisor&view=live";
const OUT_DIR =
  process.argv[3] || "/Users/hemanth/code/econ-sim/output/playwright/gcft-council-check";
const PROMPT =
  process.argv[4] || "What does the room think about keeping AI more open right now?";
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

async function clickIfVisible(page, label) {
  const candidates = [
    page.getByRole("button", { name: label }).first(),
    page.locator("button").filter({ hasText: label }).first(),
  ];
  for (const button of candidates) {
    if (await button.count() && await button.isVisible().catch(() => false)) {
      try {
        await button.click();
      } catch {
        await button.click({ force: true });
      }
      return true;
    }
  }
  return false;
}

async function safeText(locator) {
  try {
    if (await locator.count()) {
      return await locator.first().innerText();
    }
  } catch {
    return null;
  }
  return null;
}

async function run() {
  if (!fs.existsSync(GCFT_BIN)) {
    throw new Error(`GCFT binary not found at ${GCFT_BIN}`);
  }
  fs.mkdirSync(OUT_DIR, { recursive: true });
  const { chromium } = loadPlaywright();
  const browser = await chromium.launch({
    headless: process.env.PLAYWRIGHT_HEADLESS === "1",
    executablePath: GCFT_BIN,
    args: [
      "--use-fake-ui-for-media-stream",
      "--use-fake-device-for-media-stream",
      "--autoplay-policy=no-user-gesture-required",
    ],
  });

  try {
    const page = await browser.newPage({
      viewport: { width: 1510, height: 960 },
    });
    const notes = [];
    await page.addInitScript(() => {
      window.__econSimPlaytestProbe = {
        events: [],
        note(entry) {
          const next = { ...entry, at: Date.now() };
          this.events.push(next);
          // Keep this visible in Playwright summaries without polluting app code.
          console.debug(`[econ-sim-probe] ${JSON.stringify(next)}`);
        },
      };
    });
    page.on("console", (message) => {
      notes.push(`[console:${message.type()}] ${message.text()}`);
    });
    page.on("pageerror", (error) => {
      notes.push(`[pageerror] ${error.message}`);
    });
    page.on("request", (request) => {
      if (request.url().includes("/advisor/council-turn")) {
        notes.push(`[request] ${request.method()} ${request.url()}`);
      }
    });
    page.on("response", (response) => {
      if (response.url().includes("/advisor/council-turn")) {
        notes.push(`[response] ${response.status()} ${response.url()}`);
      }
    });

    await page.goto(TARGET_URL, { waitUntil: "domcontentloaded" });
    await page.waitForTimeout(2000);

    if (await clickIfVisible(page, /Begin the chapter reel/i)) {
      notes.push("Clicked Begin the chapter reel.");
      await page.waitForTimeout(1800);
    }
    if (await clickIfVisible(page, /Skip/i)) {
      notes.push("Clicked Skip.");
      await page.waitForTimeout(2200);
    }

    if (await clickIfVisible(page, /^Council$/i)) {
      notes.push("Moved to council.");
      await page.waitForTimeout(2200);
    }
    if (await clickIfVisible(page, /^Intel$/i)) {
      notes.push("Opened immersive drawer.");
      await page.waitForTimeout(1200);
    }
    if (await clickIfVisible(page, /^Room$/i)) {
      notes.push("Focused room drawer tab.");
      await page.waitForTimeout(1200);
    }
    const multiAdvisorTab = page.getByRole("button", { name: /^Multi-advisor$/i }).first();
    if (await multiAdvisorTab.count()) {
      const selected = await multiAdvisorTab.getAttribute("aria-selected").catch(() => null);
      if (selected !== "true") {
          await multiAdvisorTab.click();
        notes.push("Switched to multi-advisor mode.");
        await page.waitForTimeout(1400);
      }
    }

    await page.screenshot({ path: path.join(OUT_DIR, "01-room.png") });

    let inlineInputs = page.locator(".scene__channel-bar .scene__inline-composer input:visible");
    if ((await inlineInputs.count()) === 0) {
      const typeButton = page.locator(".scene__text-trigger:visible").first();
      if (await typeButton.count()) {
        await typeButton.click();
        notes.push("Opened inline text composer.");
        await page.waitForTimeout(450);
      }
      inlineInputs = page.locator(".scene__channel-bar .scene__inline-composer input:visible");
    }
    const drawerTextareas = page.locator(".voice-dock textarea:visible");
    const inlineCount = await inlineInputs.count();
    const drawerCount = await drawerTextareas.count();
    notes.push(`Inline composer count: ${inlineCount}`);
    notes.push(`Drawer composer count: ${drawerCount}`);
    const input = inlineCount > 0 ? inlineInputs.nth(inlineCount - 1) : drawerTextareas.first();
    const assistantEntryCountBefore = await page.locator(".voice-log__entry--assistant").count();
    await input.fill(PROMPT);
    const drawerSend = page.locator(".voice-dock .btn.btn--primary:visible").first();
    if (inlineCount > 0) {
      const inlineButtons = page.locator(".scene__channel-bar .scene__inline-composer:visible button");
      const buttonCount = await inlineButtons.count();
      notes.push(`Inline send button count: ${buttonCount}`);
      if (buttonCount > 0) {
        await inlineButtons.nth(buttonCount - 1).click();
      } else {
        await page.getByRole("button", { name: /^Send$/i }).first().click();
      }
    } else if (drawerCount > 0 && await drawerSend.count()) {
      await drawerSend.click();
    } else {
      await page.getByRole("button", { name: /^Send$/i }).first().click();
    }
    await page.screenshot({ path: path.join(OUT_DIR, "02-submitted.png") });
    await page.waitForFunction(
      (countBefore) =>
        document.querySelectorAll(".voice-log__entry--assistant").length > countBefore ||
        Boolean(document.querySelector(".scene__caption p")?.textContent?.trim()) ||
        Boolean(window.__econSimPlaytestProbe?.events?.some((entry) => entry.type === "council_turn_response" || entry.type === "council_turn_error")),
      assistantEntryCountBefore,
      { timeout: 60000 },
    ).catch(() => {});
    await page.waitForTimeout(1800);

    const floor = await safeText(page.locator(".scene-council-floor"));
    const caption = await safeText(page.locator(".scene__caption p"));
    const labels = await page.locator(".scene-council-label").allTextContents();
    const voiceEntries = await page.locator(".voice-log__entry").allTextContents();
    const probes = await page.evaluate(() => window.__econSimPlaytestProbe?.events ?? []);
    notes.push(`Council floor: ${floor}`);
    notes.push(`Caption: ${caption}`);
    notes.push(`Labels: ${labels.join(" | ")}`);
    notes.push(`Voice entries: ${voiceEntries.join(" || ")}`);
    notes.push(`Probe events: ${JSON.stringify(probes)}`);
    await page.screenshot({ path: path.join(OUT_DIR, "02-after-prompt.png") });

    const summary = {
      url: page.url(),
      notes,
      probes,
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
