#!/usr/bin/env node

const fs = require("node:fs");
const path = require("node:path");
const os = require("node:os");
const { execSync } = require("node:child_process");
const { createRequire } = require("node:module");

const TARGET_URL =
  process.argv[2] || "http://127.0.0.1:5173/?sim=sim_8e1f6ee20fae&auditorium=town_hall";
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
      notes.push(`[pageerror] ${error.message}`);
    });

    await page.goto(TARGET_URL, { waitUntil: "domcontentloaded" });
    await page.waitForTimeout(2000);

    if (await clickIfVisible(page, /Begin the chapter reel/i)) {
      notes.push("Clicked Begin the chapter reel.");
      await page.waitForTimeout(1600);
    }
    if (await clickIfVisible(page, /Skip/i)) {
      notes.push("Clicked Skip.");
      await page.waitForTimeout(2200);
    }

    if (await clickIfVisible(page, /Auditorium/i)) {
      notes.push("Moved to auditorium.");
      await page.waitForTimeout(2200);
    }
    await page.screenshot({ path: path.join(OUT_DIR, "01-auditorium.png") });

    if (await clickIfVisible(page, /Town hall questions|Main debate/i)) {
      notes.push("Used in-scene town hall hotspot.");
      await page.waitForTimeout(1800);
    }
    if (await clickIfVisible(page, /Open town hall Q&A|Return to debate/i)) {
      notes.push("Opened town hall.");
      await page.waitForTimeout(1800);
    }
    await page.screenshot({ path: path.join(OUT_DIR, "02-townhall-open.png") });

    const currentQuestion = await page.locator(".debate-room__townhall-card h4").first().innerText().catch(() => "");
    notes.push(`Question: ${currentQuestion}`);

    await page.getByRole("button", { name: /Ask the room/i }).first().click();
    await page.waitForTimeout(3500);
    await page.screenshot({ path: path.join(OUT_DIR, "03-after-ask.png") });

    const voiceEntries = await page.locator(".voice-log__entry").evaluateAll((nodes) =>
      nodes.map((node) => ({
        label: node.querySelector("span")?.textContent?.trim(),
        text: node.querySelector("p")?.textContent?.trim(),
      })).slice(-10),
    );
    notes.push(`Voice entries: ${voiceEntries.map((entry) => `${entry.label}:${entry.text}`).join(" || ")}`);

    const summary = {
      url: page.url(),
      currentQuestion,
      voiceEntries,
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
