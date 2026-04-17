#!/bin/bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PLIST_TEMPLATE="$REPO_ROOT/ops/launchd/telecodex.plist.template"
HEALTH_PLIST_TEMPLATE="$REPO_ROOT/ops/launchd/telecodex-health.plist.template"
BIN_PATH="$REPO_ROOT/target/release/telecodex"
PATH_DEFAULT="$HOME/.cargo/bin:$HOME/.nvm/versions/node/v23.11.0/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
export PATH="${PATH:-$PATH_DEFAULT}:$PATH_DEFAULT"
PROFILE_LOCK_DIR=""
PROFILE_LOCK_HOLDER_PATH=""
PROFILE_LOCK_CHILD_PATH=""
PROFILE_ARGS_REST=()

normalize_profile_name() {
  local raw="${1:-default}"
  local normalized
  normalized="$(printf '%s' "$raw" | tr '[:upper:]' '[:lower:]' | tr -cs 'a-z0-9' '-')"
  normalized="${normalized#-}"
  normalized="${normalized%-}"
  if [[ -z "$normalized" ]]; then
    normalized="default"
  fi
  printf '%s' "$normalized"
}

configure_profile_layout() {
  TELECODEX_PROFILE="$(normalize_profile_name "${1:-${TELECODEX_PROFILE:-default}}")"
  export TELECODEX_PROFILE

  if [[ "$TELECODEX_PROFILE" == "default" ]]; then
    APP_LABEL="dev.jian.telecodex"
    STATE_DIR="$REPO_ROOT/.telecodex"
  else
    APP_LABEL="dev.jian.telecodex-$TELECODEX_PROFILE"
    STATE_DIR="$REPO_ROOT/.telecodex/profiles/$TELECODEX_PROFILE"
  fi

  RUNTIME_DIR="$STATE_DIR/runtime"
  LOG_DIR="$STATE_DIR/logs"
  DATA_DIR="$STATE_DIR/data"
  DISABLE_SENTINEL="$STATE_DIR/service-disabled"
  CONFIG_PATH="$RUNTIME_DIR/telecodex.toml"
  PROFILE_LOCK_DIR="$STATE_DIR/run-lock"
  PROFILE_LOCK_HOLDER_PATH="$PROFILE_LOCK_DIR/holder.pid"
  PROFILE_LOCK_CHILD_PATH="$PROFILE_LOCK_DIR/child.pid"
  PLIST_PATH="$HOME/Library/LaunchAgents/$APP_LABEL.plist"
  HEALTH_APP_LABEL="$APP_LABEL-health"
  HEALTH_PLIST_PATH="$HOME/Library/LaunchAgents/$HEALTH_APP_LABEL.plist"
}

profile_var_name() {
  local base="$1"
  if [[ "$TELECODEX_PROFILE" == "default" ]]; then
    printf '%s' "$base"
  else
    printf '%s_%s' "$(printf '%s' "$TELECODEX_PROFILE" | tr '[:lower:]' '[:upper:]' | tr '-' '_')" "$base"
  fi
}

resolve_profile_var() {
  local base="$1"
  local fallback="${2-__MISSING__}"
  local profile_var
  profile_var="$(profile_var_name "$base")"
  if [[ -n "${!profile_var:-}" ]]; then
    printf '%s' "${!profile_var}"
    return
  fi
  if [[ -n "${!base:-}" ]]; then
    printf '%s' "${!base}"
    return
  fi
  if [[ "$fallback" != "__MISSING__" ]]; then
    printf '%s' "$fallback"
    return
  fi
  return 1
}

parse_profile_args() {
  local profile="${TELECODEX_PROFILE:-default}"
  PROFILE_ARGS_REST=()
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --profile)
        shift
        if [[ $# -eq 0 ]]; then
          echo "--profile requires a value" >&2
          exit 1
        fi
        profile="$1"
        shift
        ;;
      --profile=*)
        profile="${1#*=}"
        shift
        ;;
      *)
        PROFILE_ARGS_REST+=("$1")
        shift
        ;;
    esac
  done
  configure_profile_layout "$profile"
}

load_env() {
  if [[ -f "$REPO_ROOT/.env" ]]; then
    set -a
    # shellcheck disable=SC1091
    source "$REPO_ROOT/.env"
    set +a
  fi
}

toml_escape() {
  printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g'
}

trim() {
  local value="$1"
  value="${value#"${value%%[![:space:]]*}"}"
  value="${value%"${value##*[![:space:]]}"}"
  printf '%s' "$value"
}

quote_toml_array_from_colon_list() {
  local input="${1:-}"
  local first=1
  printf '['
  IFS=':' read -r -a parts <<<"$input"
  for part in "${parts[@]}"; do
    part="$(trim "$part")"
    [[ -z "$part" ]] && continue
    if [[ $first -eq 0 ]]; then
      printf ', '
    fi
    first=0
    printf '"%s"' "$(toml_escape "$part")"
  done
  printf ']'
}

ensure_dirs() {
  mkdir -p "$STATE_DIR" "$RUNTIME_DIR" "$LOG_DIR" "$DATA_DIR" "$HOME/Library/LaunchAgents"
}

service_disabled() {
  [[ -f "$DISABLE_SENTINEL" ]]
}

mark_service_disabled() {
  ensure_dirs
  : >"$DISABLE_SENTINEL"
}

clear_service_disabled() {
  rm -f "$DISABLE_SENTINEL"
}

pid_is_live() {
  local pid="${1:-}"
  [[ -n "$pid" ]] || return 1
  kill -0 "$pid" >/dev/null 2>&1
}

read_profile_lock_pid() {
  local path="$1"
  [[ -f "$path" ]] || return 1
  tr -d '[:space:]' <"$path"
}

active_profile_lock_pid() {
  local pid=""
  pid="$(read_profile_lock_pid "$PROFILE_LOCK_CHILD_PATH" 2>/dev/null || true)"
  if pid_is_live "$pid"; then
    printf '%s' "$pid"
    return 0
  fi
  pid="$(read_profile_lock_pid "$PROFILE_LOCK_HOLDER_PATH" 2>/dev/null || true)"
  if pid_is_live "$pid"; then
    printf '%s' "$pid"
    return 0
  fi
  return 1
}

clear_profile_lock() {
  rm -f "$PROFILE_LOCK_CHILD_PATH" "$PROFILE_LOCK_HOLDER_PATH"
  rmdir "$PROFILE_LOCK_DIR" >/dev/null 2>&1 || true
}

acquire_profile_lock() {
  ensure_dirs
  if mkdir "$PROFILE_LOCK_DIR" >/dev/null 2>&1; then
    printf '%s\n' "$$" >"$PROFILE_LOCK_HOLDER_PATH"
    return 0
  fi

  local existing_pid=""
  existing_pid="$(active_profile_lock_pid || true)"
  if pid_is_live "$existing_pid"; then
    printf '%s' "$existing_pid"
    return 1
  fi

  clear_profile_lock
  mkdir "$PROFILE_LOCK_DIR"
  printf '%s\n' "$$" >"$PROFILE_LOCK_HOLDER_PATH"
}

record_profile_lock_child() {
  local child_pid="$1"
  printf '%s\n' "$child_pid" >"$PROFILE_LOCK_CHILD_PATH"
}

profile_lock_state() {
  local existing_pid=""
  existing_pid="$(active_profile_lock_pid || true)"
  if pid_is_live "$existing_pid"; then
    printf 'held:%s' "$existing_pid"
    return 0
  fi
  if [[ -d "$PROFILE_LOCK_DIR" ]]; then
    printf 'stale'
    return 0
  fi
  printf 'unlocked'
}

profile_runtime_lock_state() {
  local lock_state
  lock_state="$(profile_lock_state)"
  if [[ "$lock_state" == held:* ]]; then
    printf '%s' "$lock_state"
    return 0
  fi
  if service_active "$APP_LABEL"; then
    printf 'drift:active-without-lock'
    return 0
  fi
  printf '%s' "$lock_state"
}

canonicalize_path() {
  local candidate="$1"
  if [[ -d "$candidate" ]]; then
    (cd "$candidate" && pwd -P)
  else
    local dir base
    dir="$(dirname "$candidate")"
    base="$(basename "$candidate")"
    (cd "$dir" && printf '%s/%s\n' "$(pwd -P)" "$base")
  fi
}

resolve_binary_path() {
  local raw="$1"
  if [[ "$raw" == */* ]]; then
    printf '%s' "$(canonicalize_path "$raw")"
    return 0
  fi

  local resolved
  resolved="$(command -v "$raw" 2>/dev/null || true)"
  if [[ -z "$resolved" ]]; then
    return 1
  fi
  printf '%s' "$(canonicalize_path "$resolved")"
}

validate_env() {
  TELEGRAM_TOKEN_ENV_NAME="$(profile_var_name TELEGRAM_BOT_TOKEN)"
  if [[ "$TELECODEX_PROFILE" == "default" ]]; then
    TELEGRAM_BOT_TOKEN="$(resolve_profile_var TELEGRAM_BOT_TOKEN)" || {
      echo "$TELEGRAM_TOKEN_ENV_NAME is required" >&2
      exit 1
    }
  else
    TELEGRAM_BOT_TOKEN="${!TELEGRAM_TOKEN_ENV_NAME:-}"
    if [[ -z "$TELEGRAM_BOT_TOKEN" ]]; then
      echo "$TELEGRAM_TOKEN_ENV_NAME is required for profile $TELECODEX_PROFILE" >&2
      exit 1
    fi
  fi
  TELEGRAM_ALLOWED_USER_ID="$(resolve_profile_var TELEGRAM_ALLOWED_USER_ID)" || {
    echo "$(profile_var_name TELEGRAM_ALLOWED_USER_ID) is required" >&2
    exit 1
  }

  DEFAULT_CWD="$(resolve_profile_var DEFAULT_CWD "/Users/jian/Dev")"
  CODEX_BINARY_RAW="$(resolve_profile_var CODEX_BINARY "codex")"
  DEFAULT_MODEL="$(resolve_profile_var DEFAULT_MODEL "gpt-5.4-mini")"
  DEFAULT_REASONING_EFFORT="$(resolve_profile_var DEFAULT_REASONING_EFFORT "medium")"
  EXECUTION_MODEL="$(resolve_profile_var EXECUTION_MODEL "gpt-5.4")"
  EXECUTION_REASONING_EFFORT="$(resolve_profile_var EXECUTION_REASONING_EFFORT "high")"
  DEFAULT_SANDBOX="$(resolve_profile_var DEFAULT_SANDBOX "workspace-write")"
  DEFAULT_APPROVAL_POLICY="$(resolve_profile_var DEFAULT_APPROVAL_POLICY "on-request")"
  DEFAULT_SEARCH_MODE="$(resolve_profile_var DEFAULT_SEARCH_MODE "disabled")"
  EXTRA_ADD_DIRS="$(resolve_profile_var EXTRA_ADD_DIRS "$DEFAULT_CWD")"
  SEED_WORKSPACES="$(resolve_profile_var SEED_WORKSPACES "$DEFAULT_CWD")"
  POLL_TIMEOUT_SECONDS="$(resolve_profile_var POLL_TIMEOUT_SECONDS "30")"
  EDIT_DEBOUNCE_MS="$(resolve_profile_var EDIT_DEBOUNCE_MS "900")"
  MAX_TEXT_CHUNK="$(resolve_profile_var MAX_TEXT_CHUNK "3500")"
  RUST_LOG="$(resolve_profile_var RUST_LOG "telecodex=info,reqwest=warn")"
  TELECODEX_RESTART_DELAY_MS="$(resolve_profile_var TELECODEX_RESTART_DELAY_MS "1500")"
  TELEGRAM_PRIMARY_FORUM_CHAT_ID="$(resolve_profile_var TELEGRAM_PRIMARY_FORUM_CHAT_ID "")"
  ORX_API_BASE="$(resolve_profile_var ORX_API_BASE "")"
  ORX_PROJECT_KEY="$(resolve_profile_var ORX_PROJECT_KEY "")"
  ORX_PROJECT_DISPLAY_NAME="$(resolve_profile_var ORX_PROJECT_DISPLAY_NAME "")"
  ORX_DEFAULT_DISPLAY_NAME="$(resolve_profile_var ORX_DEFAULT_DISPLAY_NAME "")"
  ORX_REPO_ROOT_RAW="$(resolve_profile_var ORX_REPO_ROOT "$DEFAULT_CWD")"
  ORX_OWNER_CHAT_ID="$(resolve_profile_var ORX_OWNER_CHAT_ID "")"
  ORX_OWNER_THREAD_ID="$(resolve_profile_var ORX_OWNER_THREAD_ID "")"
  ORX_LINEAR_TEAM_ID="$(resolve_profile_var ORX_LINEAR_TEAM_ID "")"
  ORX_LINEAR_PROJECT_ID="$(resolve_profile_var ORX_LINEAR_PROJECT_ID "")"

  if [[ ! -d "$DEFAULT_CWD" ]]; then
    echo "DEFAULT_CWD does not exist: $DEFAULT_CWD" >&2
    exit 1
  fi
  if [[ -n "$ORX_REPO_ROOT_RAW" ]]; then
    if [[ ! -d "$ORX_REPO_ROOT_RAW" ]]; then
      echo "ORX_REPO_ROOT does not exist: $ORX_REPO_ROOT_RAW" >&2
      exit 1
    fi
    ORX_REPO_ROOT="$(canonicalize_path "$ORX_REPO_ROOT_RAW")"
  else
    ORX_REPO_ROOT=""
  fi
  CODEX_BINARY="$(resolve_binary_path "$CODEX_BINARY_RAW" || true)"
  if [[ -z "$CODEX_BINARY" || ! -x "$CODEX_BINARY" ]]; then
    echo "codex binary could not be resolved: $CODEX_BINARY_RAW" >&2
    exit 1
  fi
  case "$DEFAULT_APPROVAL_POLICY" in
    never|on-request|untrusted) ;;
    *)
      echo "DEFAULT_APPROVAL_POLICY must be one of: never, on-request, untrusted" >&2
      exit 1
      ;;
  esac
  case "$DEFAULT_SANDBOX" in
    read-only|workspace-write|danger-full-access) ;;
    *)
      echo "DEFAULT_SANDBOX must be one of: read-only, workspace-write, danger-full-access" >&2
      exit 1
      ;;
  esac
  case "$DEFAULT_SEARCH_MODE" in
    disabled|live|cached) ;;
    *)
      echo "DEFAULT_SEARCH_MODE must be one of: disabled, live, cached" >&2
      exit 1
      ;;
  esac
}

render_config() {
  ensure_dirs
  validate_env

  local primary_forum_block=""
  local orx_block=""
  if [[ -n "${TELEGRAM_PRIMARY_FORUM_CHAT_ID:-}" ]]; then
    primary_forum_block=$'\nprimary_forum_chat_id = '"$TELEGRAM_PRIMARY_FORUM_CHAT_ID"
  fi
  if [[ -n "${ORX_API_BASE:-}" || -n "${ORX_PROJECT_KEY:-}" ]]; then
    local default_display_name="$ORX_DEFAULT_DISPLAY_NAME"
    if [[ -z "$default_display_name" ]]; then
      if [[ -n "$ORX_PROJECT_DISPLAY_NAME" ]]; then
        default_display_name="$ORX_PROJECT_DISPLAY_NAME"
      else
        default_display_name="$ORX_PROJECT_KEY"
      fi
    fi
    orx_block=$'\n\n[orx]\n'
    if [[ -n "$ORX_API_BASE" ]]; then
      orx_block+="api_base = \"$(toml_escape "$ORX_API_BASE")\""$'\n'
    fi
    if [[ -n "$ORX_PROJECT_KEY" ]]; then
      orx_block+="project_key = \"$(toml_escape "$ORX_PROJECT_KEY")\""$'\n'
    fi
    if [[ -n "$ORX_PROJECT_DISPLAY_NAME" ]]; then
      orx_block+="project_display_name = \"$(toml_escape "$ORX_PROJECT_DISPLAY_NAME")\""$'\n'
    fi
    if [[ -n "$default_display_name" ]]; then
      orx_block+="default_display_name = \"$(toml_escape "$default_display_name")\""$'\n'
    fi
    if [[ -n "$ORX_REPO_ROOT" ]]; then
      orx_block+="repo_root = \"$(toml_escape "$ORX_REPO_ROOT")\""$'\n'
    fi
    if [[ -n "$ORX_OWNER_CHAT_ID" ]]; then
      orx_block+="owner_chat_id = ${ORX_OWNER_CHAT_ID}"$'\n'
    fi
    if [[ -n "$ORX_OWNER_THREAD_ID" ]]; then
      orx_block+="owner_thread_id = ${ORX_OWNER_THREAD_ID}"$'\n'
    fi
    if [[ -n "$ORX_LINEAR_TEAM_ID" ]]; then
      orx_block+="linear_team_id = \"$(toml_escape "$ORX_LINEAR_TEAM_ID")\""$'\n'
    fi
    if [[ -n "$ORX_LINEAR_PROJECT_ID" ]]; then
      orx_block+="linear_project_id = \"$(toml_escape "$ORX_LINEAR_PROJECT_ID")\""$'\n'
    fi
  fi

  cat >"$CONFIG_PATH" <<EOF
db_path = "$(toml_escape "$DATA_DIR/telecodex.sqlite3")"
startup_admin_ids = [${TELEGRAM_ALLOWED_USER_ID}]
poll_timeout_seconds = ${POLL_TIMEOUT_SECONDS}
edit_debounce_ms = ${EDIT_DEBOUNCE_MS}
max_text_chunk = ${MAX_TEXT_CHUNK}
tmp_dir = "$(toml_escape "$STATE_DIR/tmp")"

[telegram]
bot_token_env = "$(toml_escape "$TELEGRAM_TOKEN_ENV_NAME")"
api_base = "https://api.telegram.org"
use_message_drafts = true${primary_forum_block}
auto_create_topics = false
forum_sync_topics_per_poll = 2
stale_topic_action = "none"

[codex]
binary = "$(toml_escape "$CODEX_BINARY")"
default_cwd = "$(toml_escape "$DEFAULT_CWD")"
default_model = "$(toml_escape "$DEFAULT_MODEL")"
default_reasoning_effort = "$(toml_escape "$DEFAULT_REASONING_EFFORT")"
execution_model = "$(toml_escape "$EXECUTION_MODEL")"
execution_reasoning_effort = "$(toml_escape "$EXECUTION_REASONING_EFFORT")"
default_sandbox = "$(toml_escape "$DEFAULT_SANDBOX")"
default_approval = "$(toml_escape "$DEFAULT_APPROVAL_POLICY")"
default_search_mode = "$(toml_escape "$DEFAULT_SEARCH_MODE")"
import_desktop_history = true
import_cli_history = true
seed_workspaces = $(quote_toml_array_from_colon_list "$SEED_WORKSPACES")
default_add_dirs = $(quote_toml_array_from_colon_list "$EXTRA_ADD_DIRS")
EOF

  if [[ -n "$orx_block" ]]; then
    printf '%s' "$orx_block" >>"$CONFIG_PATH"
  fi
}

build_binary() {
  if [[ ! -x "$BIN_PATH" || "${FORCE_BUILD:-0}" == "1" ]]; then
    (cd "$REPO_ROOT" && cargo build --release)
  fi
}

launchctl_domain() {
  printf 'gui/%s' "$(id -u)"
}

service_loaded() {
  local label="$1"
  launchctl print "$(launchctl_domain)/$label" >/dev/null 2>&1
}

launchctl_state() {
  local label="$1"
  local line
  line="$(launchctl print "$(launchctl_domain)/$label" 2>/dev/null | awk -F' = ' '/^[[:space:]]*state = / {print $2; exit}')"
  printf '%s' "$line"
}

service_active() {
  local label="$1"
  local state
  state="$(launchctl_state "$label")"
  [[ "$state" == "running" || "$state" == "spawn scheduled" ]]
}

launchctl_print_status() {
  local label="$1"
  launchctl print "$(launchctl_domain)/$label" 2>/dev/null || echo "not loaded"
}

telegram_bot_getme() {
  curl -fsS "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/getMe"
}

telegram_webhook_info() {
  curl -fsS "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/getWebhookInfo"
}

export_runtime_env() {
  export "$TELEGRAM_TOKEN_ENV_NAME=$TELEGRAM_BOT_TOKEN"
  export RUST_LOG
  export TELECODEX_RESTART_DELAY_MS
}

configure_profile_layout "${TELECODEX_PROFILE:-default}"
