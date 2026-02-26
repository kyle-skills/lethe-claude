# Future: Terminal Detection Improvements

**Status:** Deferred from Review #3
**Date:** 2026-02-25

## M10: tmux/screen sessions prevent terminal detection

Process tree walking cannot find the terminal emulator when running inside
tmux or screen — the terminal is in a separate process tree. `detect_terminal()`
returns null, forcing manual fallback.

**Possible approach:** Check `$TMUX` and `$STY` environment variables first.
If set, the session is inside a multiplexer. The actual terminal can sometimes
be found via `tmux display-message -p '#{client_termname}'` or by examining
the tmux server's parent process.

**Impact:** Affects a significant portion of developer users who work in
terminal multiplexers.

## M11: macOS primary terminals unsupported

Terminal.app and iTerm2 are absent from `TERMINAL_TEMPLATES`. The script is
effectively Linux-only for automated terminal relaunch.

**Possible templates:**
- `Terminal.app`: `open -a Terminal <command>` (cwd handling unclear)
- `iTerm2`: AppleScript or `open -a iTerm <command>` with profile support

**Impact:** macOS users always hit the manual fallback path.

## M12: gnome-terminal `--working-directory` deprecated

GNOME 41+ (Ubuntu 22.04+, Fedora 35+) deprecated `--working-directory`.
The flag is silently ignored, so the terminal opens in the wrong directory.

**Possible fix:** Use `gnome-terminal -- sh -c 'cd {cwd} && exec {command}'`
pattern matching the `st` fix.
