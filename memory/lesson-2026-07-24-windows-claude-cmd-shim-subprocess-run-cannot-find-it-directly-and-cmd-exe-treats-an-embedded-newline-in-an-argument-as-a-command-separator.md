---
title: Windows claude .cmd shim: subprocess.run cannot find it directly, and cmd.exe treats an embedded newline in an argument as a command separator
date: 2026-07-24
confidence: high
---

On Windows, the npm-installed claude CLI is a .cmd shim, not a native exe. subprocess.run(["claude", ...]) (no shell=True) raises FileNotFoundError because Windows CreateProcess does not do PATHEXT resolution the way a shell does; shutil.which("claude") resolves the real path (e.g. C:\...\claude.CMD) and fixes it. Separately, .cmd files are executed via cmd.exe under the hood, and cmd.exe parses the command line itself — an embedded newline (
) inside a single subprocess argument is treated as a command separator, silently truncating the rest of the argument (e.g. a multi-line prompt). Fix: flatten any text going into a claude -p prompt to one line before passing it as a subprocess argument. Neither issue reproduces on Mac/Linux, where claude is a native executable and there is no cmd.exe in the middle — Windows-only gotchas.
