#!/usr/bin/env node

const fs = require("node:fs");
const path = require("node:path");
const os = require("node:os");
const {
  DEFAULT_GCFT_BIN,
  loadPlaywright,
  launchChromiumBrowser,
} = require("./playwright_runtime");

const TARGET_URL = process.argv[2] || "http://127.0.0.1:5173/?sim=sim_2522a948b55e";
const OUT_DIR = process.argv[3] || "/Users/hemanth/code/econ-sim/output/playwright/gcft-stage-cycle";
const RUNTIME_DIR =
  process.env.PLAYWRIGHT_RUNTIME_DIR ||
  path.join(process.env.TMPDIR || os.tmpdir(), "econ-sim-playwright");

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
  fs.mkdirSync(OUT_DIR, { recursive: true });
  const { chromium } = loadPlaywright(RUNTIME_DIR);
  const { browser, launcher, launchErrors } = await launchChromiumBrowser(chromium, {
    headless: false,
    gcftBin: process.env.PLAYWRIGHT_GCFT_BIN || DEFAULT_GCFT_BIN,
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
      browserLauncher: launcher,
      browserLaunchErrors: launchErrors,
      notes,
      mic: (await page.locator(".scene__voice-trigger").count()) > 0 ? await page.locator(".scene__voice-trigger").first().innerText() : null,
      commandStrip: (await page.locator(".scene__command-strip").count()) > 0 ? await page.locator(".scene__command-strip").first().innerText() : null,
      actions: (await page.locator(".scene__action-row button").count()) > 0
        ? await page.locator(".scene__action-row button").allInnerTexts()
        : [],
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
