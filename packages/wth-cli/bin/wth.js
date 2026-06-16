#!/usr/bin/env node
"use strict";

const { spawn } = require("child_process");
const path = require("path");
const { ensureRuntime, maybeRunSetup, VERSION } = require("../lib/installer");

async function main() {
  const args = process.argv.slice(2);

  if (args[0] === "--version" || args[0] === "-V") {
    console.log(`wth-cli v${VERSION}`);
    process.exit(0);
  }

  let launcher;
  try {
    launcher = await ensureRuntime();
    await maybeRunSetup(launcher);
  } catch (err) {
    console.error(err.message);
    process.exit(1);
  }

  const env = {
    ...process.env,
    WTH_HOME: process.env.WTH_HOME || path.join(process.env.HOME || ".", ".wth-ai"),
  };

  let child;
  if (launcher.type === "python") {
    child = spawn("python3", [launcher.path, ...args], { stdio: "inherit", env });
  } else {
    child = spawn(launcher.path, args, { stdio: "inherit", env });
  }

  child.on("exit", (code, signal) => {
    if (signal) process.kill(process.pid, signal);
    process.exit(code ?? 1);
  });
  child.on("error", (err) => {
    console.error(`Failed to spawn WTH-AI: ${err.message}`);
    process.exit(1);
  });
}

main();
