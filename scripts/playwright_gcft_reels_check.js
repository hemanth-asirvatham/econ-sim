#!/usr/bin/env node

const fs = require("node:fs");
const path = require("node:path");
const os = require("node:os");
const { execSync } = require("node:child_process");
const { createRequire } = require("node:module");

const TARGET_URL =
  process.argv[2] || "http://127.0.0.1:5173/?sim=sim_7db636bffb06&advisor=multi&auditorium=debate";
const OUT_DIR =
  process.argv[3] || "/Users/hemanth/code/econ-sim/output/playwright/gcft-reels-check";
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
  const button = page.getByRole("button", { name: label }).first();
  if (await button.count()) {
    await button.click();
    return true;
  }
  return false;
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
      notes.push(`[pageerror] ${error.message}`);
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

    await page.screenshot({ path: path.join(OUT_DIR, "01-stage.png"), fullPage: true });

    const reelTrigger = page.getByTestId("topbar-reels-button").or(
      page.getByRole("button", { name: /Future reels|Explore future|Reels/i }),
    ).first();
    await reelTrigger.click();
    await page.waitForTimeout(1200);
    await page.screenshot({ path: path.join(OUT_DIR, "02-overlay.png"), fullPage: true });

    let directOverlayOpen = (await page.locator(".featurette-overlay").count()) > 0;
    if (!directOverlayOpen) {
      const drawerOpenButton = page.getByRole("button", { name: /Open future reels/i }).first();
      if (await drawerOpenButton.count()) {
        notes.push("Topbar reels button did not open the overlay directly.");
        await drawerOpenButton.click();
        await page.waitForTimeout(1000);
        directOverlayOpen = (await page.locator(".featurette-overlay").count()) > 0;
      }
    }

    const cinema = page.locator(".featurette-cinema").first();
    let cinemaOpen = (await cinema.count()) > 0;
    const cards = directOverlayOpen && !cinemaOpen
      ? page.locator(".featurette-overlay .featurette-card")
      : page.locator(".immersive-drawer__reel-card, .featurette-card");
    const reelTitles = await cards.locator("strong").evaluateAll((nodes) =>
      nodes.map((node) => (node.textContent || "").replace(/\s+/g, " ").trim()).filter(Boolean),
    );
    const reelQuestions = await cards.locator(".featurette-card__question, p").evaluateAll((nodes) =>
      nodes.map((node) => (node.textContent || "").replace(/\s+/g, " ").trim()).filter(Boolean),
    );

    let viewerTitle = null;
    let reelAutoplayLabel = null;
    if (cinemaOpen) {
      viewerTitle = await page.locator(".featurette-cinema__title strong").first().innerText().catch(() => null);
      reelAutoplayLabel = await page.getByRole("button", { name: /Stop|Replay|Play/i }).first().innerText().catch(() => null);
    }
    const openableCard = directOverlayOpen
      ? page.locator(".featurette-overlay .featurette-card.featurette-card--ready").first()
      : page.locator(".featurette-card.featurette-card--ready, .immersive-drawer__reel-card.immersive-drawer__reel-card--ready").first();
    if (!cinemaOpen && await openableCard.count()) {
      await openableCard.click();
      await page.waitForTimeout(1000);
      await page.screenshot({ path: path.join(OUT_DIR, "03-viewer.png"), fullPage: true });
      cinemaOpen = (await cinema.count()) > 0;
      viewerTitle = await page
        .locator(".featurette-cinema__title strong, .featurette-viewer h3")
        .first()
        .innerText()
        .catch(() => null);
      reelAutoplayLabel = await page.getByRole("button", { name: /Stop reel|Replay reel|Play reel/i }).first().innerText().catch(() => null);
    }

    const summary = {
      url: page.url(),
      directOverlayOpen,
      cinemaOpen,
      reelCardCount: await cards.count(),
      reelTitles,
      reelQuestions,
      viewerTitle,
      reelAutoplayLabel,
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
