# Claude Code Monitor

A lightweight Windows floating window that monitors your active [Claude Code](https://docs.anthropic.com/en/docs/claude-code) sessions in real time.

![screenshot](screenshot.png)
<!-- Replace with an actual screenshot -->

## Features

- Always-on-top floating widget — stays visible while you work
- Auto-detects running Claude Code instances
- Shows per-session status: **Working** (generating), **Waiting** (needs your input)
- Monitors `~/.claude/projects/` JSONL conversation logs for live state changes
- Draggable window, minimal resource usage
- No dependencies beyond Python's standard library (uses `tkinter`)

## Quick Start

```
start.bat
```

Or run directly:

```
python claude_code_monitor.py
```

Requires Python 3.8+ with tkinter (included in standard Windows Python installs).

## How It Works

1. Scans `~/.claude/projects/` for recently-modified `.jsonl` conversation files
2. Tracks file size changes — a growing file means Claude is actively generating
3. Reads the last log entry to determine if Claude finished responding or is still calling tools
4. Periodically checks for `node.exe` processes matching `claude-code` to detect closed sessions

## License

[MIT](LICENSE)
