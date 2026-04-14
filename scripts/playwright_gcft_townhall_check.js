#!/usr/bin/env node

const fs = require("node:fs");
const path = require("node:path");
const os = require("node:os");
const { execSync } = require("node:child_process");
const { createRequire } = require("node:module");

const TARGET_URL =
  process.argv[2] || "http://127.0.0.1:5173/?sim=sim_515e64b42952&advisor=multi&auditorium=town_hall&room=debate&view=live";
const OUT_DIR =
  process.argv[3] || "/Users/hemanth/code/econ-sim/output/playwright/gcft-townhall-check";
const GCFT_BIN =
  process.env.PLAYWRIGHT_GCFT_BIN ||
  path.join(
    os.homedir(),
    "Library/Caches/ms-playwright/chromium-1208/chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing",
  );
const RUNTIME_DIR =
  process.env.PLAYWRIGHT_RUNTIME_DIR ||
  path.join("/Users/hemanth/code/econ-sim/output/playwright", "runtime");
const SKIP_CALL = process.env.PLAYWRIGHT_SKIP_TOWNHALL_CALL === "1";

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
  const button = page.getByRole("button", { name: label }).first();
  if (await button.count()) {
    await button.click();
    return true;
  }
  return false;
}

async function clickLocatorIfVisible(locator, timeout = 5000) {
  if (!(await locator.count())) {
    return false;
  }
  if (!(await locator.isVisible().catch(() => false))) {
    return false;
  }
  await locator.click({ timeout });
  return true;
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
    page.on("console", (message) => {
      notes.push(`[console:${message.type()}] ${message.text()}`);
    });
    page.on("pageerror", (error) => {
      notes.push(`[pageerror] ${error.stack || error.message}`);
    });

    await page.goto(TARGET_URL, { waitUntil: "domcontentloaded" });
    await page.waitForTimeout(2200);

    if (await clickIfVisible(page, /Begin the chapter reel/i)) {
      notes.push("Clicked Begin the chapter reel.");
      await page.waitForTimeout(1600);
    }
    if (await clickIfVisible(page, /^Skip$/i)) {
      notes.push("Clicked Skip.");
      await page.waitForTimeout(2200);
    }

    if ((await page.locator(".debate-room").count()) === 0) {
      const auditoriumHotspot = page.getByText(/^AUDITORIUM$/i).first();
      if (await auditoriumHotspot.count()) {
        await auditoriumHotspot.click();
        notes.push("Clicked auditorium hotspot.");
        await page.waitForTimeout(1800);
      }
    }

    const townHallTab = page.getByTestId("auditorium-tab-town-hall");
    const debateTab = page.getByTestId("auditorium-tab-debate");
    let townHallSelected = (await townHallTab.getAttribute("aria-selected").catch(() => null)) === "true";
    if (!townHallSelected) {
      const sceneTownHallHotspot = page.getByRole("button", { name: /Town hall\./i }).first();
      if (await clickLocatorIfVisible(sceneTownHallHotspot)) {
        notes.push("Clicked 3D town hall hotspot.");
      } else if (await townHallTab.count()) {
        await townHallTab.click({ timeout: 5000 });
        notes.push("Clicked drawer town hall tab.");
      }
      await page.waitForTimeout(1400);
      townHallSelected = (await townHallTab.getAttribute("aria-selected").catch(() => null)) === "true";
    }
    const debateSelected = (await debateTab.getAttribute("aria-selected").catch(() => null)) === "true";

    await page.screenshot({ path: path.join(OUT_DIR, "01-room.png") });

    const questionPanel = page.locator(".debate-room__audience-floor-note").nth(1).locator("p").first();
    const sceneQuestionPanel = page.locator(".scene-townhall-floor p").first();
    const questionPreview =
      await sceneQuestionPanel.innerText().catch(async () => await questionPanel.innerText().catch(() => null));
    const sceneVoiceButton = page.locator(".scene__townhall-action").filter({ hasText: /Audience question|Call on voter|Finding voter|Voter speaking|Answer the voter/i }).first();
    const callButton = page.getByTestId("townhall-call-on-voter");
    const callVisible = await callButton.isVisible().catch(() => false);
    const callEnabled = callVisible && await callButton.isEnabled().catch(() => false);
    if (callVisible && callEnabled && !SKIP_CALL) {
      if (await clickLocatorIfVisible(sceneVoiceButton)) {
        notes.push("Clicked 3D audience question button.");
      } else {
        await callButton.click({ timeout: 5000, force: true });
        notes.push("Clicked audience question in drawer.");
      }
      try {
        await page.waitForFunction(
          (previousText) => {
            const sceneText = document.querySelector(".scene-townhall-floor p")?.textContent?.trim() ?? "";
            const note = document.querySelectorAll(".debate-room__audience-floor-note")[1];
            const drawerText = note?.querySelector("p")?.textContent?.trim() ?? "";
            const nextText = sceneText || drawerText;
            return Boolean(nextText) && nextText !== (previousText || "").trim();
          },
          questionPreview,
          { timeout: 10000 },
        );
      } catch {
        await page.waitForTimeout(3000);
      }
    } else if (callVisible && !callEnabled) {
      notes.push("Town hall question was already live; did not click disabled drawer button.");
      await page.waitForTimeout(3000);
    } else {
      if (!SKIP_CALL && await clickLocatorIfVisible(sceneVoiceButton)) {
        notes.push("Clicked 3D audience question button.");
        await page.waitForTimeout(9000);
      }
    }

    await page.screenshot({ path: path.join(OUT_DIR, "02-townhall.png") });
    const questionText =
      await sceneQuestionPanel.innerText().catch(async () => await questionPanel.innerText().catch(() => null));
    const summary = {
      url: page.url(),
      townHallSelected,
      debateSelected,
      callVisible,
      callEnabled,
      questionPreview,
      questionText,
      notes,
    };
    fs.writeFileSync(path.join(OUT_DIR, "summary.json"), JSON.stringify(summary, null, 2));
    console.log(JSON.stringify(summary, null, 2));
  } finally {
    await browser.close();
  }
}

run()
  .then(() => process.exit(0))
  .catch((error) => {
    console.error(error);
    process.exit(1);
  });
