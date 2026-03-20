#!/usr/bin/env bash
set -euo pipefail

TARGET_URL="${1:-http://127.0.0.1:5173/}"
OUT_PREFIX="${2:-/Users/hemanth/code/econ-sim/output/playwright/gcft-smoke}"
RUNTIME_DIR="${PLAYWRIGHT_RUNTIME_DIR:-${TMPDIR:-/tmp}/econ-sim-playwright}"
GCFT_BIN="${PLAYWRIGHT_GCFT_BIN:-$HOME/Library/Caches/ms-playwright/chromium-1208/chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing}"
WAIT_MS="${PLAYWRIGHT_WAIT_MS:-5000}"
KEEP_OPEN="${PLAYWRIGHT_KEEP_OPEN:-0}"

if [[ ! -x "$GCFT_BIN" ]]; then
  echo "Google Chrome for Testing binary not found at: $GCFT_BIN" >&2
  exit 1
fi

mkdir -p "$RUNTIME_DIR" "$(dirname "$OUT_PREFIX")"

pushd "$RUNTIME_DIR" >/dev/null
if [[ ! -f package.json ]]; then
  npm init -y >/dev/null 2>&1
fi
if [[ ! -d node_modules/playwright ]]; then
  PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 npm install playwright >/dev/null 2>&1
fi

node - "$TARGET_URL" "$OUT_PREFIX" "$GCFT_BIN" "$WAIT_MS" "$KEEP_OPEN" <<'NODE'
const { chromium } = require("playwright");

const [targetUrl, outPrefix, executablePath, waitMsRaw, keepOpenRaw] = process.argv.slice(2);
const waitMs = Number.parseInt(waitMsRaw, 10) || 5000;
const keepOpen = keepOpenRaw === "1";

(async () => {
  const browser = await chromium.launch({
    headless: false,
    executablePath,
  });
  const page = await browser.newPage({
    viewport: { width: 1440, height: 900 },
  });

  await page.goto(targetUrl, { waitUntil: "networkidle" });
  await page.waitForSelector(".scene__voice-trigger", { timeout: 15000 });
  await page.waitForTimeout(waitMs);
  await page.screenshot({ path: `${outPrefix}.png` });

  const summary = {
    url: targetUrl,
    title: await page.title(),
    mic: await page.locator(".scene__voice-trigger").innerText(),
  };
  console.log(JSON.stringify(summary, null, 2));

  if (keepOpen) {
    await new Promise(() => {});
  }

  await browser.close();
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
NODE
popd >/dev/null
