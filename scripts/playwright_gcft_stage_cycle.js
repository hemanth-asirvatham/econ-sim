#!/usr/bin/env node

const fs = require("node:fs");
const path = require("node:path");
const os = require("node:os");
const { execSync } = require("node:child_process");
const { createRequire } = require("node:module");

const TARGET_URL = process.argv[2] || "http://127.0.0.1:5173/?sim=sim_2522a948b55e";
const OUT_DIR = process.argv[3] || "/Users/hemanth/code/econ-sim/output/playwright/gcft-stage-cycle";
const GCFT_BIN =
  process.env.PLAYWRIGHT_GCFT_BIN ||
  path.join(
    os.homedir(),
    "Library/Caches/ms-playwright/chromium-1208/chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing",
  );
const RUNTIME_DIR =
  process.env.PLAYWRIGHT_RUNTIME_DIR ||
  path.join(process.env.TMPDIR || os.tmpdir(), "econ-sim-playwright");

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

async function screenshot(page, outDir, name, notes) {
  try {
    const capture = page.screenshot({ path: path.join(outDir, name), timeout: 10000 });
    capture.catch(() => undefined);
    await Promise.race([
      capture,
      new Promise((_, reject) => setTimeout(() => reject(new Error("outer screenshot timeout")), 14000)),
    ]);
  } catch (error) {
    notes.push(`[screenshot:${name}] ${error instanceof Error ? error.message : String(error)}`);
  }
}

async function clickIfVisible(page, selectors, notes, message) {
  for (const selector of selectors) {
    const locator = page.locator(selector).first();
    if (await locator.count()) {
      try {
        await locator.click({ timeout: 5000, force: true });
        notes.push(message);
        return true;
      } catch (error) {
        notes.push(`[click:${selector}] ${error instanceof Error ? error.message : String(error)}`);
      }
    }
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
    headless: false,
    executablePath: GCFT_BIN,
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
    await page.waitForTimeout(1800);
    await screenshot(page, OUT_DIR, "01-loaded.png", notes);

    await clickIfVisible(page, ['button:has-text("Begin the chapter reel")'], notes, "Clicked Begin the chapter reel.");
    await page.waitForTimeout(2600);
    await screenshot(page, OUT_DIR, "02-reel.png", notes);

    await clickIfVisible(page, ['button:has-text("Skip")'], notes, "Clicked Skip.");
    await page.waitForTimeout(2200);
    await screenshot(page, OUT_DIR, "03-after-skip.png", notes);

    await clickIfVisible(
      page,
      ['button:has-text("Head to the street")', 'button:has-text("Street")'],
      notes,
      "Moved to Street.",
    );
    await page.waitForTimeout(2400);
    await screenshot(page, OUT_DIR, "04-street.png", notes);

    await page.keyboard.press("KeyW");
    await page.waitForTimeout(900);
    await screenshot(page, OUT_DIR, "05-street-move.png", notes);

    await clickIfVisible(
      page,
      ['button:has-text("Head to the auditorium")', 'button:has-text("Auditorium")'],
      notes,
      "Moved to Auditorium.",
    );
    await page.waitForTimeout(2400);
    await screenshot(page, OUT_DIR, "06-auditorium.png", notes);

    const summary = {
      url: page.url(),
      title: await page.title(),
      notes,
      mic: (await page.locator(".scene__voice-trigger").count()) > 0 ? await page.locator(".scene__voice-trigger").first().innerText() : null,
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
