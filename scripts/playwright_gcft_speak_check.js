#!/usr/bin/env node

const fs = require("node:fs");
const path = require("node:path");
const os = require("node:os");
const { execSync } = require("node:child_process");
const { createRequire } = require("node:module");

const TARGET_URL =
  process.argv[2] || "http://127.0.0.1:5173/?sim=sim_2e7152bb4306&advisor=multi&auditorium=debate";
const OUT_DIR =
  process.argv[3] || "/Users/hemanth/code/econ-sim/output/playwright/gcft-speak-check";
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
    headless: true,
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

    await page.screenshot({ path: path.join(OUT_DIR, "01-before.png"), fullPage: true });
    const speakTrigger = page.locator(".scene__voice-trigger").first();
    await speakTrigger.click();
    notes.push("Clicked scene Speak trigger.");
    await page.waitForTimeout(2400);
    await page.screenshot({ path: path.join(OUT_DIR, "02-after.png"), fullPage: true });

    const summary = {
      url: page.url(),
      drawerOpen: (await page.locator(".immersive-drawer--open").count()) > 0,
      roomPaneVisible: (await page.locator(".immersive-drawer__pane--active .voice-dock, .immersive-drawer__pane--active .debate-room").count()) > 0,
      liveModeLabel: await page.locator(".voice-dock__mode").first().textContent({ timeout: 1200 }).catch(() => null),
      errorText: await page.locator(".voice-dock__error").first().textContent({ timeout: 1200 }).catch(() => null),
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
