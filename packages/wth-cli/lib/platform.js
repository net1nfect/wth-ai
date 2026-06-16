"use strict";

const os = require("os");

function detectPlatform() {
  const platform = os.platform();
  const arch = os.arch();
  let osName;
  if (platform === "linux") osName = "linux";
  else if (platform === "darwin") osName = "darwin";
  else if (platform === "win32") osName = "windows";
  else throw new Error(`Unsupported platform: ${platform}`);

  let archName;
  if (arch === "x64") archName = "amd64";
  else if (arch === "arm64") archName = "arm64";
  else throw new Error(`Unsupported arch: ${arch}`);

  return `${osName}-${archName}`;
}

function binaryName() {
  return os.platform() === "win32" ? "wth-ai.exe" : "wth-ai";
}

module.exports = { detectPlatform, binaryName };
