const fs = require("node:fs");
const path = require("node:path");
const os = require("node:os");
const { execSync } = require("node:child_process");
const { createRequire } = require("node:module");

const DEFAULT_GCFT_BIN =
  process.env.PLAYWRIGHT_GCFT_BIN ||
  path.join(
    os.homedir(),
    "Library/Caches/ms-playwright/chromium-1208/chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing",
  );

const DEFAULT_RUNTIME_DIR =
  process.env.PLAYWRIGHT_RUNTIME_DIR ||
  path.join(process.env.TMPDIR || os.tmpdir(), "econ-sim-playwright");

function ensureRuntime(runtimeDir = DEFAULT_RUNTIME_DIR) {
  fs.mkdirSync(runtimeDir, { recursive: true });
  const packageJson = path.join(runtimeDir, "package.json");
  if (!fs.existsSync(packageJson)) {
    execSync("npm init -y >/dev/null 2>&1", { cwd: runtimeDir, stdio: "inherit", shell: "/bin/zsh" });
  }
  const playwrightDir = path.join(runtimeDir, "node_modules", "playwright");
  if (!fs.existsSync(playwrightDir)) {
    execSync("PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 npm install playwright >/dev/null 2>&1", {
      cwd: runtimeDir,
      stdio: "inherit",
      shell: "/bin/zsh",
    });
  }
}

function loadPlaywright(runtimeDir = DEFAULT_RUNTIME_DIR) {
  ensureRuntime(runtimeDir);
  const runtimeRequire = createRequire(path.join(runtimeDir, "package.json"));
  return runtimeRequire("playwright");
}

async function launchChromiumBrowser(chromium, { headless = false, gcftBin = DEFAULT_GCFT_BIN } = {}) {
  const launchErrors = [];
  const launchers = [];
  if (gcftBin && fs.existsSync(gcftBin)) {
    launchers.push({ kind: "gcft", options: { executablePath: gcftBin } });
  }
  launchers.push(
    { kind: "chrome", options: { channel: "chrome" } },
    { kind: "msedge", options: { channel: "msedge" } },
  );

  const headlessModes = headless ? [true] : [false, true];
  for (const headlessMode of headlessModes) {
    for (const launcher of launchers) {
      try {
        const browser = await chromium.launch({
          headless: headlessMode,
          ...launcher.options,
        });
        const suffix = headlessMode ? "-headless" : "";
        return { browser, launcher: `${launcher.kind}${suffix}`, launchErrors };
      } catch (error) {
        launchErrors.push({
          launcher: `${launcher.kind}${headlessMode ? "-headless" : ""}`,
          message: error instanceof Error ? error.message : String(error),
        });
      }
    }
  }

  const detail = launchErrors.map((entry) => `${entry.launcher}: ${entry.message}`).join("\n\n");
  throw new Error(`Unable to launch a headed Chromium browser.\n\n${detail}`);
}

module.exports = {
  DEFAULT_GCFT_BIN,
  DEFAULT_RUNTIME_DIR,
  ensureRuntime,
  loadPlaywright,
  launchChromiumBrowser,
};
