# AeroSpace Window Management

Float-by-default tiling with persistent per-workspace state.

## Design Principles

1. **No floating home apps** — Apps are either **tiled** or **hidden**
2. **Per-workspace state** — Each workspace remembers browser choice and UpNote visibility
3. **State survives round-trips** — `w1 (UpNote tiled) → w2 → w1` = UpNote still tiled
4. **Single rebuild function** — All paths use `rebuild_workspace()` for consistency

## Layouts

### w1: Focus Mode (2 or 3 columns)
```
┌─────────────┬───────────────────────────────┐
│   VS Code   │           Browser             │
│    (45%)    │            (55%)              │
└─────────────┴───────────────────────────────┘

With UpNote (optional):
┌─────────────┬───────────────────────────┬───────────┐
│   VS Code   │          Browser          │  UpNote   │
│    (33%)    │           (45%)           │   (22%)   │
└─────────────┴───────────────────────────┴───────────┘
```

### w2: Reference Mode (3 columns, UpNote always visible)
```
┌─────────────┬───────────────────────────┬───────────┐
│   VS Code   │          Browser          │  UpNote   │
│    (33%)    │           (45%)           │   (22%)   │
└─────────────┴───────────────────────────┴───────────┘
```

## Keybindings

| Key | Action |
|-----|--------|
| `ctrl+1` | Switch to w1 (restore w1 state) |
| `ctrl+2` | Switch to w2 (UpNote always visible) |
| `ctrl+w` | Reset workspace to defaults |
| `ctrl+e` | Repair layout and preserve current widths when possible |
| `ctrl+q` | Toggle float/tile |
| `ctrl+f` | Toggle fullscreen |
| `alt+h/j/k/l` | Focus left/down/up/right |
| `alt+shift+h/j/k/l` | Move window |
| `alt+-/=` | Resize |

## Home Apps

| App | Bundle ID | Column |
|-----|-----------|--------|
| VS Code | `com.microsoft.VSCode` | 1 (always) |
| Zen Browser | `app.zen-browser.zen` | 2 (primary) |
| Safari | `com.apple.Safari` | 2 (secondary) |
| UpNote | `com.getupnote.desktop` | 3 (optional in w1, required in w2) |

## Architecture

```
┌─────────────────────────────────────────────────────┐
│                    ENTRY POINTS                      │
├─────────────────────────────────────────────────────┤
│  ctrl+1/2        │  on-focus-changed  │  on-window-detected
│  switch_ws.sh    │  on_focus.sh       │  on_window.sh
└──────────────────┴────────────────────┴─────────────┘
                            │
                            ▼
               ┌────────────────────────┐
               │   rebuild_workspace()  │  ← Single source of truth
               │        (lib.sh)        │
               └────────────────────────┘
                            │
                ┌───────────┴───────────┐
                ▼                       ▼
          read_state()            write_state()
                │                       │
                ▼                       ▼
          /tmp/aerospace_state/{ws}.state
```

## State Files

| File | Purpose |
|------|---------|
| `/tmp/aerospace_state/w1.state` | w1 state: `BROWSER=zen`, `UPNOTE_TILED=false` |
| `/tmp/aerospace_state/w1.state.v2.json` | typed state v2 (`version`, `workspace`, `browser`, `upnoteTiled`, `tiledOrder`) |
| `/tmp/aerospace_state/w2.state` | w2 state: `BROWSER=zen`, `UPNOTE_TILED=true` |
| `/tmp/aerospace_state/last_ws` | Last workspace (for detecting switches) |
| `/tmp/aerospace_state/churn_until` | Ignore focus events until this timestamp |
| `/tmp/aerospace_state/rebuild.lock` | Prevent concurrent rebuilds |

State write mode:
- `AEROSPACE_STATE_WRITE_MODE=dual` (default): write legacy + v2.
- `AEROSPACE_STATE_WRITE_MODE=legacy-only` (compat-only): write legacy file only.
- `AEROSPACE_STATE_WRITE_MODE=v2-only`: write v2 JSON file only.

## Scripts

| Script | Purpose |
|--------|---------|
| `config.sh` | Bundle IDs, sizing percentages, paths |
| `lib.sh` | Core functions: `read_state`, `write_state`, `rebuild_workspace` |
| `engine_runtime.sh` | Dispatch mode wrapper (`ts-shadow`, `ts-active`) |
| `engine_callback.sh` | Typed engine callback hook (progressive promotion) |
| `callbacks/*.sh` | Shell side-effect implementations called by engine runtime |
| `switch_ws.sh` | Mode-aware `ctrl+1` wrapper |
| `on_focus.sh` | Mode-aware focus callback wrapper |
| `on_window.sh` | Mode-aware window callback wrapper |
| `reset_ws.sh` | Mode-aware `ctrl+w` wrapper |
| `balance.sh` | Mode-aware `ctrl+e` wrapper |

## Engine Modes

- `AEROSPACE_ENGINE_MODE=ts-shadow`: run typed engine shadow hook, then run callbacks implementation.
- `AEROSPACE_ENGINE_MODE=ts-active` (default): run typed engine active hook if callback is promoted; otherwise fallback to callbacks implementation.
- Current promoted callbacks in `ts-active`: `switch_ws`, `reset_ws`, `balance`, `on_focus`, `on_window`.

## Migration Gate

- Run `/Users/jian/Dev/workspace/configs/aerospace/scripts/gate.sh` for the unified migration gate.
- The gate appends timestamped results to `/tmp/aerospace-gate.log`.

## State Transitions

| Event | State Change |
|-------|--------------|
| Open UpNote in w1 | `UPNOTE_TILED=true` |
| Close UpNote | `UPNOTE_TILED=false` |
| Switch to w2 | Load w2 state (UpNote always visible) |
| Switch to w1 | Load w1 state (preserves previous UpNote choice) |
| Open Zen | `BROWSER=zen` |
| Open Safari | `BROWSER=safari` |
| Focus other browser | Swap browsers |
| Close active browser | Promote other browser |

## Debugging

```bash
# View log
tail -f /tmp/aerospace.log

# Optional callback latency logs (perf:on_focus / perf:on_window)
AEROSPACE_PERF_LOG=1 ~/.config/aerospace/on_focus.sh
AEROSPACE_PERF_LOG=1 ~/.config/aerospace/on_window.sh com.apple.Safari

# Check state
cat /tmp/aerospace_state/w1.state
cat /tmp/aerospace_state/w2.state

# Manual reset
rm -rf /tmp/aerospace_state
aerospace reload-config

# Rebalance current workspace
~/.config/aerospace/balance.sh
```
