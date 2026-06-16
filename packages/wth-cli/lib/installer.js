"use strict";

const fs = require("fs");
const path = require("path");
const https = require("https");
const http = require("http");
const { execSync } = require("child_process");
const { detectPlatform, binaryName } = require("./platform");

const RUNTIME_DIR = path.join(process.env.HOME || process.env.USERPROFILE || ".", ".wth-ai", "runtime");
const VERSION = "1.0.0";
const RELEASE_BASE =
  process.env.WTH_RELEASE_URL ||
  "https://github.com/wth-ai/wth-ai/releases/download/v1.0.0";

function runtimePaths() {
  const plat = detectPlatform();
  const bin = path.join(RUNTIME_DIR, binaryName());
  const manifest = path.join(RUNTIME_DIR, "manifest.json");
  return { plat, bin, manifest, RUNTIME_DIR };
}

function readManifest(manifestPath) {
  try {
    return JSON.parse(fs.readFileSync(manifestPath, "utf8"));
  } catch {
    return null;
  }
}

function isSetupComplete() {
  const userCfg = path.join(process.env.HOME || ".", ".wth-ai", "config", "user.yaml");
  try {
    const raw = fs.readFileSync(userCfg, "utf8");
    return /setup_complete:\s*true/.test(raw);
  } catch {
    return false;
  }
}

async function maybeRunSetup(launcher) {
  if (isSetupComplete() || process.env.WTH_SKIP_SETUP === "1") return;
  const { spawnSync } = require("child_process");
  const setupArgs = launcher.type === "python" ? [launcher.path, "setup"] : ["setup"];
  const cmd = launcher.type === "python" ? "python3" : launcher.path;
  console.log("[wth] First run — starting setup wizard...");
  const r = spawnSync(cmd, setupArgs, { stdio: "inherit", env: process.env });
  if (r.status !== 0) {
    console.warn("[wth] Setup wizard exited with code", r.status);
  }
}

function isInstalled() {
  const { bin, manifest } = runtimePaths();
  const m = readManifest(manifest);
  return fs.existsSync(bin) && m && m.version === VERSION;
}

function download(url, dest) {
  return new Promise((resolve, reject) => {
    const proto = url.startsWith("https") ? https : http;
    const file = fs.createWriteStream(dest);
    const headers = {};
    if (process.env.WTH_GITHUB_TOKEN) {
      headers.Authorization = `Bearer ${process.env.WTH_GITHUB_TOKEN}`;
    }
    proto
      .get(url, { headers }, (res) => {
        if (res.statusCode === 302 || res.statusCode === 301) {
          file.close();
          fs.unlinkSync(dest);
          return download(res.headers.location, dest).then(resolve).catch(reject);
        }
        if (res.statusCode !== 200) {
          file.close();
          fs.unlinkSync(dest);
          return reject(new Error(`Download failed: HTTP ${res.statusCode} for ${url}`));
        }
        res.pipe(file);
        file.on("finish", () => file.close(() => resolve(dest)));
      })
      .on("error", (err) => {
        file.close();
        try {
          fs.unlinkSync(dest);
        } catch {}
        reject(err);
      });
  });
}

function findWthMainPy() {
  const candidates = [
    path.join(process.cwd(), "wth-main.py"),
    path.join(__dirname, "..", "..", "..", "wth-main.py"),
    process.env.WTH_SOURCE_ROOT ? path.join(process.env.WTH_SOURCE_ROOT, "wth-main.py") : null,
  ].filter(Boolean);
  for (const p of candidates) {
    const resolved = path.resolve(p);
    if (fs.existsSync(resolved)) return resolved;
  }
  return null;
}

function findDevLauncher() {
  try {
    const which = execSync("which wth-ai 2>/dev/null || command -v wth-ai", {
      encoding: "utf8",
    }).trim();
    if (which && fs.existsSync(which)) return { type: "binary", path: which };
  } catch {}

  const mainPy = findWthMainPy();
  if (mainPy) return { type: "python", path: mainPy };

  const home = process.env.WTH_HOME || path.join(process.env.HOME || ".", ".wth-ai");
  const runtimeBin = path.join(home, "runtime", binaryName());
  if (fs.existsSync(runtimeBin)) return { type: "binary", path: runtimeBin };

  return null;
}

async function ensureRuntime() {
  const { plat, bin, manifest, RUNTIME_DIR } = runtimePaths();
  if (isInstalled()) return { type: "binary", path: bin };

  const dev = findDevLauncher();
  if (dev) return dev;

  fs.mkdirSync(RUNTIME_DIR, { recursive: true });
  const assetName = `wth-ai-${plat}${plat.startsWith("windows") ? ".exe" : ""}`;
  const url = `${RELEASE_BASE}/${assetName}`;
  const tmp = `${bin}.download`;

  try {
    await download(url, tmp);
    fs.renameSync(tmp, bin);
    if (!plat.startsWith("windows")) {
      fs.chmodSync(bin, 0o755);
    }
    fs.writeFileSync(
      manifest,
      JSON.stringify({ version: VERSION, platform: plat, installed_at: new Date().toISOString() }, null, 2)
    );
    return { type: "binary", path: bin };
  } catch (err) {
    if (fs.existsSync(tmp)) fs.unlinkSync(tmp);
    const fallback = findDevLauncher();
    if (fallback) return fallback;
    throw new Error(
      `Failed to download WTH-AI runtime: ${err.message}\n` +
        `Use: python3 wth-main.py run '/doctor' or npm i -g ./packages/wth-cli`
    );
  }
}

module.exports = { ensureRuntime, isInstalled, isSetupComplete, maybeRunSetup, runtimePaths, VERSION, RUNTIME_DIR, findDevLauncher };
