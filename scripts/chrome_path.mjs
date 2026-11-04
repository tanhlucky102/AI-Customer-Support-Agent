// Where Google Chrome (or Chromium) is: $CHROME, else the usual install paths of Windows, macOS and Linux.
import { execFileSync } from "node:child_process";
import { existsSync } from "node:fs";
import path from "node:path";

export function findChrome() {
  if (process.env.CHROME) return process.env.CHROME;
  const candidates = [];
  if (process.platform === "win32") {
    for (const base of [process.env.PROGRAMFILES, process.env["PROGRAMFILES(X86)"], process.env.LOCALAPPDATA]) {
      if (base) candidates.push(path.join(base, "Google", "Chrome", "Application", "chrome.exe"));
    }
    if (process.env["PROGRAMFILES(X86)"]) {
      candidates.push(path.join(process.env["PROGRAMFILES(X86)"], "Microsoft", "Edge", "Application", "msedge.exe"));
    }
  } else if (process.platform === "darwin") {
    candidates.push("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                    "/Applications/Chromium.app/Contents/MacOS/Chromium");
  } else {
    for (const name of ["google-chrome", "google-chrome-stable", "chromium", "chromium-browser"]) {
      try {
        candidates.push(execFileSync("which", [name], { encoding: "utf8" }).trim());
      } catch { /* not installed */ }
    }
  }
  const found = candidates.find((candidate) => candidate && existsSync(candidate));
  if (!found) throw new Error("Chrome not found: set CHROME to the browser's executable");
  return found;
}
