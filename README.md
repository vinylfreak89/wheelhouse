# Wheelhouse

A native macOS client for the Codex app-server, built because the official
desktop app cannot display threads created on a remote host and offers no way to
start one (openai/codex #27284, #22438, #24280).

    NSWindow + WKWebView  ->  bridge.py (127.0.0.1:8770)  ->  codex app-server

## Run

    open ~/.claude/skills/codex-run/Wheelhouse.app

The app starts the bridge, which starts `codex app-server`. Quitting takes both
down. If a bridge is already listening on 8770 the app reuses it.

Requires: `codex` on PATH (brew cask), `python3`, and `~/.codex/auth.json`.

## What it does

- **Projects / Running / Done** sidebar, grouped by working directory
- **Model, reasoning effort, service tier, approvals** — all model-driven from
  `model/list`, showing the *resolved* default rather than the word "default"
- **Live streaming**: agent text, reasoning, command execution with output,
  updated in place as `item/started` → `item/completed` arrive
- **Steering**: typing while a turn runs sends `turn/steer` into the running
  turn instead of queueing a new one
- **Approvals** render inline with Approve/Deny when policy isn't `never`
- **Protocol pane** (`protocol` button): every JSON-RPC frame in and out
- **Usage**: 5-hour and weekly rate-limit windows with reset times
- **Rename / Archive / Delete** via right-click on a thread

Enter sends, Shift+Enter makes a newline. ⌘N new thread, ⌘R reload, ⌘Q quit.

## Files

    Wheelhouse.app        the bundle (unsigned)
    bridge.py          HTTP/SSE <-> app-server stdio bridge
    ui/index.html      the whole UI, no build step, no dependencies
    native/main.swift  NSWindow + WKWebView shell and menu bar

## Driving it from Claude

The protocol has no thread ownership model, so Claude can attach to the same
app-server the UI is watching:

    curl -s -XPOST 127.0.0.1:8770/rpc -H 'Content-Type: application/json' \
      -d '{"method":"thread/list","params":{"limit":10}}'

Anything Claude does shows up live in the window.
