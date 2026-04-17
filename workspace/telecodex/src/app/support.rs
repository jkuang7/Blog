use super::*;
use serde_json::Value;
use std::{
    fs,
    path::{Path, PathBuf},
    process::{Command, Stdio},
};

pub(super) fn app_version_label() -> String {
    format!(
        "v{}.{}",
        env!("TELECODEX_APP_VERSION"),
        env!("TELECODEX_BUILD_NUMBER")
    )
}

pub(super) fn is_primary_forum_dashboard(
    config: &Config,
    chat: &crate::telegram::Chat,
    thread_id: Option<i64>,
) -> bool {
    config.telegram.primary_forum_chat_id == Some(chat.id)
        && chat.is_forum.unwrap_or(false)
        && thread_id.unwrap_or(0) == 0
}

pub(super) fn prefer_primary_environment_session(
    session: &crate::models::SessionRecord,
    environment_key: &Path,
) -> bool {
    normalize_path(session.cwd.clone()) == normalize_path(environment_key.to_path_buf())
}

pub(super) fn command_uses_session_context(parsed: &ParsedInput) -> bool {
    match parsed {
        ParsedInput::Forward(_) => true,
        ParsedInput::Bridge(command) => matches!(
            command,
            BridgeCommand::Topic { .. }
                | BridgeCommand::Kanban
                | BridgeCommand::Review(_)
                | BridgeCommand::Cd { .. }
                | BridgeCommand::Pwd
                | BridgeCommand::Model { .. }
                | BridgeCommand::Think { .. }
                | BridgeCommand::Prompt { .. }
                | BridgeCommand::Approval { .. }
                | BridgeCommand::Sandbox { .. }
                | BridgeCommand::Search { .. }
                | BridgeCommand::AddDir { .. }
                | BridgeCommand::Limits
                | BridgeCommand::Copy
                | BridgeCommand::Clear
                | BridgeCommand::Unsupported { .. }
        ),
    }
}

pub(super) fn parsed_input_requires_codex_auth(parsed: &ParsedInput) -> bool {
    matches!(
        parsed,
        ParsedInput::Forward(_)
            | ParsedInput::Bridge(BridgeCommand::Review(_))
            | ParsedInput::Bridge(BridgeCommand::Kanban)
    )
}

#[derive(Debug, Clone)]
pub(super) struct RunnerWorkspace {
    pub project: String,
    pub project_root: PathBuf,
}

#[derive(Debug, Clone)]
pub(super) struct RunnerCommandOutcome {
    pub stdout: String,
    pub stderr: String,
}

pub(super) fn session_title_is_present(session: &crate::models::SessionRecord) -> bool {
    session
        .session_title
        .as_deref()
        .map(str::trim)
        .filter(|title| !title.is_empty())
        .is_some()
}

pub(super) fn derive_session_title_from_text(text: &str) -> Option<String> {
    let lines = text
        .lines()
        .map(str::trim)
        .filter(|line| !line.is_empty())
        .collect::<Vec<_>>();
    let first_line = *lines.first()?;
    if first_line.starts_with("RUNNER_DISPATCH ") {
        let project = first_line
            .split_whitespace()
            .find_map(|part| part.strip_prefix("project="))
            .filter(|value| !value.is_empty());
        let issue = lines
            .iter()
            .find_map(|line| line.strip_prefix("LINEAR_ISSUE="))
            .filter(|value| !value.is_empty());
        return match (project, issue) {
            (Some(project), Some(issue)) => Some(format!("{project} / {issue}")),
            (Some(project), None) => Some(format!("{project} runner")),
            (None, Some(issue)) => Some(format!("Runner / {issue}")),
            (None, None) => Some("Runner".to_string()),
        };
    }
    let collapsed = first_line.split_whitespace().collect::<Vec<_>>().join(" ");
    if collapsed.is_empty() {
        return None;
    }
    const LIMIT: usize = 48;
    if collapsed.chars().count() <= LIMIT {
        return Some(collapsed);
    }
    let truncated = collapsed.chars().take(LIMIT - 1).collect::<String>();
    Some(format!("{truncated}…"))
}

pub(super) fn active_session_state_key(user_id: i64, chat_id: i64) -> String {
    format!("active_session:{user_id}:{chat_id}")
}

pub(super) fn forum_sync_cooldown_key(chat_id: i64) -> String {
    format!("forum_sync_cooldown:{chat_id}")
}

pub(super) fn forum_sync_error_key(chat_id: i64) -> String {
    format!("forum_sync_error:{chat_id}")
}

pub(super) fn runner_watch_enabled_key(key: SessionKey) -> String {
    format!("runner_watch_enabled:{}:{}", key.chat_id, key.thread_id)
}

pub(super) fn runner_watch_state_key(key: SessionKey) -> String {
    format!("runner_watch_state:{}:{}", key.chat_id, key.thread_id)
}

pub(super) fn normalize_forum_sync_issue(issue: &str) -> String {
    issue
        .split(": retry after ")
        .next()
        .unwrap_or(issue)
        .trim()
        .to_string()
}

pub(super) fn forum_sync_cooldown_active(store: &Store, chat_id: i64) -> Result<bool> {
    let Some(value) = store.bot_state_value(&forum_sync_cooldown_key(chat_id))? else {
        return Ok(false);
    };
    let until = DateTime::parse_from_rfc3339(&value)
        .map(|value| value.with_timezone(&Utc))
        .ok();
    Ok(until.map(|until| until > Utc::now()).unwrap_or(false))
}

pub(super) fn active_session_identity(
    session_key: SessionKey,
    session: &crate::models::SessionRecord,
) -> String {
    format!(
        "{}:{}",
        session_key.thread_id,
        session.codex_thread_id.as_deref().unwrap_or("new")
    )
}

#[cfg(windows)]
pub(super) fn spawn_restarted_process() -> Result<()> {
    let exe = std::env::current_exe().context("failed to resolve current executable")?;
    let args = std::env::args_os().skip(1).collect::<Vec<_>>();
    let cwd = std::env::current_dir().context("failed to resolve current working directory")?;
    let mut command = std::process::Command::new(exe);
    command
        .args(args)
        .env("TELECODEX_RESTART_DELAY_MS", "2000")
        .current_dir(cwd)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null());
    #[cfg(windows)]
    {
        const DETACHED_PROCESS: u32 = 0x0000_0008;
        const CREATE_NEW_PROCESS_GROUP: u32 = 0x0000_0200;
        command.creation_flags(DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP);
    }
    command.spawn().context("failed to spawn restarted bot")?;
    Ok(())
}

#[cfg(not(windows))]
pub(super) fn spawn_restarted_process() -> Result<()> {
    let exe = std::env::current_exe().context("failed to resolve current executable")?;
    let args = std::env::args_os().skip(1).collect::<Vec<_>>();
    let cwd = std::env::current_dir().context("failed to resolve current working directory")?;
    let mut command = std::process::Command::new(exe);
    command
        .args(args)
        .env("TELECODEX_RESTART_DELAY_MS", "2000")
        .current_dir(cwd)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null());
    command.spawn().context("failed to spawn restarted bot")?;
    Ok(())
}

pub(super) fn ensure_admin(user: &crate::models::UserRecord) -> Result<()> {
    if user.role != UserRole::Admin {
        bail!("admin role required");
    }
    Ok(())
}

pub(super) fn ensure_approval_policy(value: &str) -> Result<()> {
    match value {
        "never" | "on-request" | "untrusted" => Ok(()),
        _ => bail!("/approval <never|on-request|untrusted>"),
    }
}

pub(super) fn ensure_sandbox_mode(value: &str) -> Result<()> {
    match value {
        "read-only" | "workspace-write" | "danger-full-access" => Ok(()),
        _ => bail!("/sandbox <read-only|workspace-write|danger-full-access>"),
    }
}

pub(super) fn normalize_reasoning_effort(value: &str) -> Result<String> {
    let normalized = value.trim().to_ascii_lowercase();
    match normalized.as_str() {
        "minimal" | "low" | "medium" | "high" => Ok(normalized),
        _ => bail!("/think <minimal|low|medium|high|default>"),
    }
}

pub(super) fn is_clear_value(value: &str) -> bool {
    matches!(
        value.trim().to_ascii_lowercase().as_str(),
        "-" | "clear" | "none" | "default"
    )
}

pub(super) fn validate_directory(path: &str) -> Result<PathBuf> {
    let path = PathBuf::from(path);
    if !path.is_absolute() {
        bail!("path must be absolute");
    }
    let path = normalize_path(
        fs::canonicalize(&path)
            .with_context(|| format!("failed to canonicalize {}", path.display()))?,
    );
    if !path.is_dir() {
        bail!("path is not a directory: {}", path.display());
    }
    Ok(path)
}

pub(super) fn resolve_runner_workspace(cwd: &Path) -> Result<RunnerWorkspace> {
    let project_root = resolve_git_root(cwd)?.unwrap_or_else(|| cwd.to_path_buf());
    let project = project_root
        .file_name()
        .and_then(|value| value.to_str())
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .ok_or_else(|| {
            anyhow!(
                "failed to infer project name for {}",
                project_root.display()
            )
        })?
        .to_string();
    Ok(RunnerWorkspace {
        project,
        project_root,
    })
}

pub(super) fn resolve_tmux_codex_home() -> PathBuf {
    std::env::var_os("TMUX_CODEX_HOME")
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("/Users/jian/Dev/workspace/tmux-codex"))
}

pub(super) fn summarize_orx_dispatch(payload: &Value) -> String {
    let Some(dispatch) = payload.get("dispatch") else {
        return "ORX did not return a dispatch summary.".to_string();
    };
    let mut lines = Vec::new();
    if let Some(message) = dispatch.get("ingress_message").and_then(Value::as_str) {
        lines.push(message.to_string());
    }
    if let Some(lane_state) = dispatch.get("lane_state").and_then(Value::as_str) {
        match lane_state {
            "awaiting_orx_review" => {
                if let Some(feature_key) = dispatch.get("feature_key").and_then(Value::as_str) {
                    lines.push(format!(
                        "Reserved feature lane: `{feature_key}` is waiting for ORX reconciliation."
                    ));
                }
                lines.push(
                    "ORX is interpreting the last runner outcome before this lane can resume."
                        .to_string(),
                );
                if let Some(project_key) = dispatch.get("project_key").and_then(Value::as_str) {
                    lines.push(format!(
                        "Use `/status` in `{project_key}` to review the gate details or use the inline operator controls below."
                    ));
                }
            }
            "awaiting_hil_release" => {
                if let Some(feature_key) = dispatch.get("feature_key").and_then(Value::as_str) {
                    lines.push(format!(
                        "Reserved feature lane: `{feature_key}` is waiting for HIL release."
                    ));
                }
                lines.push(
                    "Choose a release action below when the checkpointed feature is ready."
                        .to_string(),
                );
            }
            "launch_failed" => {
                lines.push(
                    "Managed start left the lane in a failed state; repair it before retrying."
                        .to_string(),
                );
            }
            _ => {}
        }
    }
    if lines.is_empty() {
        "ORX did not return a dispatch summary.".to_string()
    } else {
        lines.join("\n")
    }
}

pub(super) fn summarize_orx_intake_submission(payload: &Value) -> String {
    let Some(intake) = payload.get("intake") else {
        return "ORX did not return intake details.".to_string();
    };
    let intake_key = intake
        .get("intake_key")
        .and_then(Value::as_str)
        .unwrap_or("unknown");
    let status = intake
        .get("status")
        .and_then(Value::as_str)
        .unwrap_or("unknown");
    let mut lines = vec![format!("ORX intake `{intake_key}` is `{status}`.")];
    if let Some(groups) = intake
        .get("plan")
        .and_then(|value| value.get("groups"))
        .and_then(Value::as_array)
    {
        let total_items = groups
            .iter()
            .map(|group| {
                group
                    .get("items")
                    .and_then(Value::as_array)
                    .map(|items| items.len())
                    .unwrap_or(0)
            })
            .sum::<usize>();
        if total_items > 0 {
            let project_count = groups.len();
            let ticket_word = if total_items == 1 {
                "ticket"
            } else {
                "tickets"
            };
            let project_word = if project_count == 1 {
                "project"
            } else {
                "projects"
            };
            lines.push(format!(
                "I would create {total_items} Linear {ticket_word} across {project_count} {project_word}."
            ));
            lines.push(String::new());
        }
        let mut item_index = 1usize;
        for group in groups {
            let display_name = group
                .get("display_name")
                .and_then(Value::as_str)
                .unwrap_or("unknown");
            if let Some(items) = group.get("items").and_then(Value::as_array) {
                for item in items {
                    lines.push(format!(
                        "**Ticket {} · {}**",
                        item_index,
                        super::presentation::escape_markdown_label(display_name)
                    ));
                    if let Some(draft_ticket) = item.get("draft_ticket").and_then(Value::as_object)
                    {
                        append_ticket_preview_sections(&mut lines, draft_ticket, item);
                    } else {
                        append_ticket_preview_fallback(&mut lines, item);
                    }
                    lines.push(String::new());
                    item_index += 1;
                }
            }
        }
    }
    while lines.last().is_some_and(|line| line.is_empty()) {
        lines.pop();
    }
    if status == "pending_approval" {
        lines.push(String::new());
        lines.push(
            "Review the draft tickets above, then use **Create tickets** to send them to Linear or **Reject** to discard the plan.".to_string(),
        );
    } else if status == "clarification_required" {
        lines.push(String::new());
        lines
            .push("Clarification is required before ORX can create tickets in Linear.".to_string());
    }
    lines.join("\n")
}

fn append_ticket_preview_sections(
    lines: &mut Vec<String>,
    draft_ticket: &serde_json::Map<String, Value>,
    item: &Value,
) {
    let title = draft_ticket
        .get("title")
        .and_then(Value::as_str)
        .unwrap_or("New intake item");
    let why = draft_ticket
        .get("why")
        .and_then(Value::as_str)
        .unwrap_or("No problem statement was provided.");
    let goal = draft_ticket
        .get("goal")
        .and_then(Value::as_str)
        .unwrap_or("Define the desired end state for this work.");
    lines.push(format!(
        "**{}**",
        super::presentation::escape_markdown_label(title.trim())
    ));
    push_preview_compact_value(lines, "Problem", &compact_preview_why(why));
    push_preview_compact_value(lines, "Goal", &compact_preview_goal(goal, title));

    let scope = draft_ticket.get("scope").and_then(Value::as_object);
    push_preview_list(
        lines,
        "Scope",
        scope
            .and_then(|value| value.get("in_scope"))
            .and_then(Value::as_array)
            .map(Vec::as_slice),
        Some(1),
    );
    let ordered_steps = draft_ticket
        .get("ordered_steps")
        .and_then(Value::as_array)
        .map(Vec::as_slice);
    if preview_values_have_signal(ordered_steps) {
        push_preview_list(lines, "Process", ordered_steps, Some(2));
    }
    let verification = draft_ticket
        .get("verification")
        .and_then(Value::as_array)
        .map(Vec::as_slice);
    if preview_values_have_signal(verification) {
        push_preview_list(lines, "Verify", verification, Some(1));
    }
    push_preview_done_when(
        lines,
        draft_ticket
            .get("success_criteria")
            .or_else(|| draft_ticket.get("acceptance_criteria"))
            .and_then(Value::as_array)
            .map(Vec::as_slice),
    );
    let stopping = draft_ticket
        .get("stopping_conditions")
        .or_else(|| draft_ticket.get("blocked_escalation"))
        .and_then(Value::as_array)
        .map(Vec::as_slice);
    if preview_values_have_signal(stopping) {
        push_preview_list(lines, "Escalate if", stopping, Some(1));
    }

    if let Some(rationale) = item
        .get("rationale")
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|value| !value.is_empty())
    {
        push_preview_compact_value(lines, "Why here", &humanize_project_rationale(rationale));
    }

    let risks = draft_ticket
        .get("dependencies_risks")
        .and_then(Value::as_array)
        .map(Vec::as_slice);
    if preview_values_have_signal(risks) {
        push_preview_list(lines, "Risks", risks, Some(1));
    }
}

fn append_ticket_preview_fallback(lines: &mut Vec<String>, item: &Value) {
    let title = item
        .get("title")
        .and_then(Value::as_str)
        .filter(|value| !value.trim().is_empty())
        .unwrap_or("Untitled ticket")
        .to_string();
    let problem = item
        .get("source_text")
        .and_then(Value::as_str)
        .filter(|value| !value.trim().is_empty())
        .or_else(|| item.get("description").and_then(Value::as_str))
        .unwrap_or("No source text provided.")
        .to_string();
    lines.push(format!(
        "**{}**",
        super::presentation::escape_markdown_label(title.trim())
    ));
    push_preview_compact_value(lines, "Problem", &problem);
    push_preview_compact_value(
        lines,
        "Goal",
        &format!(
            "Deliver `{}` and leave the project in a verifiable state.",
            title
        ),
    );
    lines.push("*Scope*".to_string());
    let in_scope = [Value::String(problem.clone())];
    push_preview_bullets(lines, Some(&in_scope), Some(2));
    if let Some(rationale) = item
        .get("rationale")
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|value| !value.is_empty())
    {
        push_preview_compact_value(lines, "Why here", &humanize_project_rationale(rationale));
    }
}

fn push_preview_compact_value(lines: &mut Vec<String>, label: &str, value: &str) {
    lines.push(format!(
        "{}: {}",
        super::presentation::escape_markdown_label(label),
        super::presentation::escape_markdown_label(value.trim())
    ));
}

fn preview_lines(values: Option<&[Value]>, limit: Option<usize>) -> Vec<String> {
    let mut lines = Vec::new();
    match values {
        Some(values) if !values.is_empty() => {
            let max_items = limit.unwrap_or(usize::MAX);
            for value in values {
                if let Some(text) = value
                    .as_str()
                    .map(str::trim)
                    .filter(|text| !text.is_empty())
                {
                    if text.starts_with(
                        "Touch only the code, docs, tests, and runtime wiring inside `",
                    ) {
                        continue;
                    }
                    lines.push(format!(
                        "- {}",
                        super::presentation::escape_markdown_label(&text.replace('`', ""))
                    ));
                    if lines.len() >= max_items {
                        break;
                    }
                }
            }
        }
        _ => lines.push("-".to_string()),
    }
    lines
}

fn push_preview_done_when(lines: &mut Vec<String>, values: Option<&[Value]>) {
    lines.push("*Done when*".to_string());
    let rendered = values
        .and_then(|values| {
            values
                .iter()
                .rev()
                .filter_map(Value::as_str)
                .map(str::trim)
                .find(|text| !text.is_empty())
        })
        .map(|text| {
            if let Some(stripped) = text.strip_prefix("Then ") {
                stripped.trim().to_string()
            } else {
                text.to_string()
            }
        })
        .unwrap_or_else(|| "The requested outcome is true and verified.".to_string());
    lines.push(format!(
        "- {}",
        super::presentation::escape_markdown_label(&rendered.replace('`', ""))
    ));
}

fn push_preview_bullets(lines: &mut Vec<String>, values: Option<&[Value]>, limit: Option<usize>) {
    lines.extend(preview_lines(values, limit));
}

fn push_preview_list(
    lines: &mut Vec<String>,
    heading: &str,
    values: Option<&[Value]>,
    limit: Option<usize>,
) {
    lines.push(format!(
        "*{}*",
        super::presentation::escape_markdown_label(heading)
    ));
    if heading == "Done when" {
        let labels = ["Given", "When", "Then"];
        match values {
            Some(values) if !values.is_empty() => {
                for (index, value) in values.iter().take(limit.unwrap_or(usize::MAX)).enumerate() {
                    if let Some(text) = value
                        .as_str()
                        .map(str::trim)
                        .filter(|text| !text.is_empty())
                    {
                        let label = labels.get(index).copied().unwrap_or("And");
                        let rendered = if text.starts_with(&format!("{label} ")) {
                            text.to_string()
                        } else {
                            format!("{label} {text}")
                        };
                        lines.push(format!(
                            "- {}",
                            super::presentation::escape_markdown_label(&rendered)
                        ));
                    }
                }
            }
            _ => lines.extend([
                "- Given ...".to_string(),
                "- When ...".to_string(),
                "- Then ...".to_string(),
            ]),
        }
    } else {
        push_preview_bullets(lines, values, limit);
    }
}

fn preview_values_have_signal(values: Option<&[Value]>) -> bool {
    values
        .is_some_and(|values| values.iter().filter_map(Value::as_str).map(str::trim).any(|text| {
            !text.is_empty()
                && text
                    != "If implementation reveals additional project owners, split that follow-up into separate tickets instead of broadening this one in place."
        }))
}

fn humanize_project_rationale(rationale: &str) -> String {
    if let Some(project) = rationale
        .strip_prefix("Matched explicit project reference for `")
        .and_then(|value| value.split("`").next())
    {
        if rationale.contains("instead of the bot default") {
            return format!(
                "Assigned here because the request explicitly mentioned {project}, which overrides the bot's default project."
            );
        }
        return format!("Assigned here because the request explicitly mentioned {project}.");
    }
    if rationale == "Defaulted to the receiving bot's project." {
        return "Assigned here because this bot already owns the default project lane.".to_string();
    }
    if rationale == "Matched more than one project reference in the same intake item." {
        return "This request still mentions more than one project, so it needs clarification before ticket creation.".to_string();
    }
    if rationale == "No default or explicit project match was available for this intake item." {
        return "ORX could not confidently map this request to a single project yet.".to_string();
    }
    rationale.to_string()
}

fn compact_preview_why(value: &str) -> String {
    let text = value.trim().replace('`', "");
    if let Some(problem) = text
        .split(" still has outdated behavior around ")
        .nth(1)
        .or_else(|| text.split(" still has legacy behavior around ").nth(1))
    {
        let problem = problem
            .split(", which ")
            .next()
            .unwrap_or(problem)
            .trim()
            .trim_end_matches('.');
        return format!("Legacy behavior is still present: {problem}.");
    }
    if let Some(problem) = text
        .strip_prefix("The current behavior around ")
        .and_then(|value| value.split(" is still weaker than it should be").next())
    {
        let trimmed = problem.trim().trim_end_matches('.');
        let problem = trimmed
            .rsplit_once(" in ")
            .and_then(|(head, tail)| {
                if tail
                    .chars()
                    .all(|char| char.is_ascii_lowercase() || char.is_ascii_digit() || char == '-')
                {
                    Some(head)
                } else {
                    None
                }
            })
            .unwrap_or(trimmed)
            .trim();
        return format!("Current behavior still needs tightening: {problem}.");
    }
    if let Some(problem) = text
        .strip_prefix("There is not enough confidence that ")
        .and_then(|value| value.split(" is correct today").next())
    {
        return format!(
            "This still needs verification: {}.",
            problem.trim().trim_end_matches('.')
        );
    }
    text.replace(
        "which makes the current workflow harder for operators to understand.",
        "This is still confusing for operators.",
    )
    .replace(
        "which creates avoidable operator or runtime risk.",
        "This still creates avoidable operator/runtime risk.",
    )
}

fn compact_preview_goal(value: &str, fallback_title: &str) -> String {
    let text = value.trim().replace('`', "");
    if let Some(project) = text
        .strip_prefix("Make ")
        .and_then(|value| value.split(" true in ").nth(1))
        .and_then(|value| value.split(" and leave behind").next())
    {
        return format!("Land the change in {project} and verify it.");
    }
    if text.starts_with("Establish whether ") {
        return "Confirm the behavior and land the exact follow-up if it is wrong.".to_string();
    }
    if text.is_empty() {
        return format!("Land {fallback_title} and verify it.");
    }
    text
}

pub(super) fn summarize_orx_intake_result(payload: &Value) -> String {
    let Some(intake) = payload.get("intake") else {
        return "ORX did not return intake details.".to_string();
    };
    let intake_key = intake
        .get("intake_key")
        .and_then(Value::as_str)
        .unwrap_or("unknown");
    let status = intake
        .get("status")
        .and_then(Value::as_str)
        .unwrap_or("unknown");
    let mut lines = vec![format!("ORX intake `{intake_key}` is now `{status}`.")];
    if let Some(issues) = payload.get("created_issues").and_then(Value::as_array) {
        if issues.is_empty() {
            lines.push("- created issues: none".to_string());
        } else {
            for issue in issues {
                let identifier = issue
                    .get("identifier")
                    .and_then(Value::as_str)
                    .unwrap_or("unknown");
                let title = issue
                    .get("title")
                    .and_then(Value::as_str)
                    .unwrap_or("untitled");
                lines.push(format!("- created `{identifier}`: {title}"));
            }
        }
    }
    lines.join("\n")
}

pub(super) fn intake_requires_approval(payload: &Value) -> bool {
    payload
        .get("intake")
        .and_then(|value| value.get("status"))
        .and_then(Value::as_str)
        == Some("pending_approval")
}

pub(super) fn intake_key_from_payload(payload: &Value) -> Option<String> {
    payload
        .get("intake")
        .and_then(|value| value.get("intake_key"))
        .and_then(Value::as_str)
        .map(ToOwned::to_owned)
}

pub(super) fn summarize_orx_status(payload: &Value) -> String {
    let project = payload
        .get("project")
        .and_then(|value| value.get("project_key"))
        .and_then(Value::as_str)
        .unwrap_or("idle");
    let mut lines = vec![format!("ORX status for `{project}`:")];
    if project == "idle" {
        if let Some(bot) = payload
            .get("bot")
            .and_then(|value| value.get("bot_identity"))
            .and_then(Value::as_str)
        {
            lines.push(format!("- bot: {bot}"));
        }
    }
    let active_issue = payload
        .get("active_issue_key")
        .and_then(Value::as_str)
        .unwrap_or("none");
    lines.push(format!("- active issue: {active_issue}"));
    lines.push(format!(
        "- queue depth: {}",
        payload
            .get("queue_depth")
            .and_then(Value::as_i64)
            .unwrap_or(0)
    ));
    if let Some(feature_lane) = payload
        .get("project")
        .and_then(|value| value.get("feature_lane"))
        .and_then(Value::as_object)
    {
        if let Some(feature_key) = feature_lane.get("feature_key").and_then(Value::as_str) {
            let lane_state = feature_lane
                .get("lane_state")
                .and_then(Value::as_str)
                .unwrap_or("unknown");
            lines.push(format!("- feature lane: {lane_state} (`{feature_key}`)"));
        }
    }
    if let Some(reconciliation) = payload
        .get("project")
        .and_then(|value| value.get("reconciliation"))
        .and_then(Value::as_object)
    {
        if let Some(status) = reconciliation.get("status").and_then(Value::as_str) {
            let mut line = format!("- reconciliation: {status}");
            if let Some(action) = reconciliation.get("action").and_then(Value::as_str) {
                line.push_str(&format!(" ({action})"));
            }
            if let Some(reason) = reconciliation.get("reason").and_then(Value::as_str) {
                line.push_str(&format!(" - {reason}"));
            }
            lines.push(line);
        }
        if let Some(review_kind) = reconciliation.get("review_kind").and_then(Value::as_str) {
            lines.push(format!("- review gate: {review_kind}"));
        }
        if let Some(ui_mode) = reconciliation.get("ui_mode").and_then(Value::as_str) {
            lines.push(format!("- ui mode: {ui_mode}"));
        }
        if let Some(design_state) = reconciliation.get("design_state").and_then(Value::as_str) {
            lines.push(format!("- design state: {design_state}"));
        }
        if let Some(design_reference) = reconciliation
            .get("design_reference")
            .and_then(Value::as_str)
            .filter(|value| !value.trim().is_empty())
        {
            lines.push(format!("- design reference: {design_reference}"));
        }
        if let Some(verification_surface) = reconciliation
            .get("verification_surface")
            .and_then(Value::as_str)
            .filter(|value| !value.trim().is_empty())
        {
            lines.push(format!("- verification surface: {verification_surface}"));
        }
        if let Some(design_artifacts) = reconciliation
            .get("design_artifacts")
            .and_then(Value::as_array)
        {
            let artifacts = design_artifacts
                .iter()
                .filter_map(Value::as_str)
                .filter(|value| !value.trim().is_empty())
                .take(3)
                .collect::<Vec<_>>();
            if !artifacts.is_empty() {
                lines.push(format!("- design artifacts: {}", artifacts.join(", ")));
            }
        }
    }
    if let Some(session) = payload.get("session").or_else(|| payload.get("continuity")) {
        if let Some(session_name) = session.get("session_name").and_then(Value::as_str) {
            lines.push(format!("- tmux session: {session_name}"));
        }
    } else if let Some(runners) = payload.get("runners").and_then(Value::as_array) {
        if let Some(runner) = runners.first() {
            if let Some(state) = runner.get("state").and_then(Value::as_str) {
                lines.push(format!("- runner state: {state}"));
            }
        }
    }
    if let Some(daemon) = payload.get("daemon") {
        if let Some(tick) = daemon.get("tick").and_then(Value::as_str) {
            lines.push(format!("- daemon tick: {tick}"));
        }
    }
    lines.join("\n")
}

pub(super) fn orx_operator_keyboard_for_payload(payload: &Value) -> Option<InlineKeyboardMarkup> {
    let project = payload.get("project");
    let dispatch = payload.get("dispatch");
    let project_key = dispatch
        .and_then(|value| value.get("project_key"))
        .or_else(|| project.and_then(|value| value.get("project_key")))
        .and_then(Value::as_str)?;
    let lane_state = dispatch
        .and_then(|value| value.get("lane_state"))
        .or_else(|| {
            project
                .and_then(|value| value.get("feature_lane"))
                .and_then(|lane| lane.get("lane_state"))
        })
        .and_then(Value::as_str);
    let review_kind = project
        .and_then(|value| value.get("reconciliation"))
        .and_then(|value| value.get("review_kind"))
        .and_then(Value::as_str);
    orx_operator_keyboard(project_key, lane_state, review_kind)
}

pub(super) fn summarize_orx_notification(notification: &crate::orx::OrxNotification) -> String {
    let mut lines = Vec::new();
    lines.push(format!("ORX handoff for `{}`.", notification.project_key));
    if let Some(target_bot) = notification
        .target_bot
        .as_deref()
        .filter(|value| !value.trim().is_empty())
    {
        lines.push(format!("Assigned bot: `{target_bot}`."));
    }
    if let Some(ingress_bot) = notification
        .ingress_bot
        .as_deref()
        .filter(|value| !value.trim().is_empty())
    {
        lines.push(format!("Requested from `{ingress_bot}`."));
    }
    if let Some(message) = notification
        .payload
        .get("message")
        .and_then(Value::as_str)
        .filter(|value| !value.trim().is_empty())
    {
        lines.push(message.to_string());
    } else if let Some(issue_key) = notification.issue_key.as_deref() {
        lines.push(format!(
            "Continue `{issue_key}` on the assigned project bot."
        ));
    }
    lines.join("\n")
}

pub(super) fn stop_tmux_codex_runner(workspace: &RunnerWorkspace) -> Result<RunnerCommandOutcome> {
    let args = vec!["stop".to_string(), workspace.project.clone()];
    run_tmux_codex_command(&args)
}

fn run_tmux_codex_command(args: &[String]) -> Result<RunnerCommandOutcome> {
    let home = resolve_tmux_codex_home();
    if !home.is_dir() {
        bail!("tmux-codex home not found: {}", home.display());
    }

    let output = Command::new("python3")
        .arg("-m")
        .arg("src.main")
        .args(args)
        .current_dir(&home)
        .output()
        .with_context(|| format!("failed to run tmux-codex command in {}", home.display()))?;

    if !output.status.success() {
        let stdout = String::from_utf8_lossy(&output.stdout).trim().to_string();
        let stderr = String::from_utf8_lossy(&output.stderr).trim().to_string();
        let detail = if stderr.is_empty() {
            stdout.clone()
        } else {
            stderr.clone()
        };
        bail!(
            "tmux-codex command failed: {}",
            if detail.is_empty() {
                output.status.to_string()
            } else {
                detail
            }
        );
    }

    Ok(RunnerCommandOutcome {
        stdout: String::from_utf8_lossy(&output.stdout).trim().to_string(),
        stderr: String::from_utf8_lossy(&output.stderr).trim().to_string(),
    })
}

fn resolve_git_root(cwd: &Path) -> Result<Option<PathBuf>> {
    let output = Command::new("git")
        .arg("-C")
        .arg(cwd)
        .args(["rev-parse", "--show-toplevel"])
        .output()
        .with_context(|| format!("failed to inspect git root for {}", cwd.display()))?;
    if !output.status.success() {
        return Ok(None);
    }
    let root = String::from_utf8_lossy(&output.stdout).trim().to_string();
    if root.is_empty() {
        return Ok(None);
    }
    let canonical = fs::canonicalize(&root)
        .with_context(|| format!("failed to canonicalize git root {root}"))?;
    Ok(Some(normalize_path(canonical)))
}

pub(super) fn normalize_path(path: PathBuf) -> PathBuf {
    #[cfg(windows)]
    {
        let raw = path.as_os_str().to_string_lossy();
        if let Some(rest) = raw.strip_prefix(r"\\?\UNC\") {
            return PathBuf::from(format!(r"\\{rest}"));
        }
        if let Some(rest) = raw.strip_prefix(r"\\?\") {
            return PathBuf::from(rest);
        }
    }
    path
}

pub(super) fn telegram_retry_after(error: &anyhow::Error) -> Option<u64> {
    error
        .downcast_ref::<TelegramError>()
        .and_then(|telegram| telegram.retry_after)
}

pub(super) fn should_drop_telegram_rate_limited_send(error: &anyhow::Error) -> bool {
    telegram_retry_after(error).is_some()
}

pub(super) fn telegram_status(error: &anyhow::Error) -> Option<reqwest::StatusCode> {
    error
        .downcast_ref::<TelegramError>()
        .map(|telegram| telegram.status)
}

pub(super) fn is_message_not_modified(error: &anyhow::Error) -> bool {
    error
        .downcast_ref::<TelegramError>()
        .map(|telegram| telegram.description.contains("message is not modified"))
        .unwrap_or(false)
}

pub(super) fn is_message_thread_not_found(error: &anyhow::Error) -> bool {
    error
        .downcast_ref::<TelegramError>()
        .map(|telegram| telegram.description.contains("message thread not found"))
        .unwrap_or(false)
}

pub(super) fn is_invalid_forum_topic_error(error: &anyhow::Error) -> bool {
    error
        .downcast_ref::<TelegramError>()
        .map(|telegram| {
            telegram.description.contains("TOPIC_ID_INVALID")
                || telegram
                    .description
                    .contains("invalid forum topic identifier specified")
                || telegram.description.contains("message thread not found")
        })
        .unwrap_or(false)
}

pub(super) fn is_forum_topic_not_modified(error: &anyhow::Error) -> bool {
    error
        .downcast_ref::<TelegramError>()
        .map(|telegram| telegram.description.contains("TOPIC_NOT_MODIFIED"))
        .unwrap_or(false)
}

pub(super) fn auto_search_mode_for_prompt(prompt: &str) -> Option<crate::config::SearchMode> {
    let prompt = prompt.to_lowercase();
    let needs_live_search = [
        "what's new",
        "last day",
        "last 24 hours",
        "today",
        "news",
        "latest",
        "last 24 hours",
        "today",
        "current",
        "news",
    ]
    .iter()
    .any(|needle| prompt.contains(needle));

    if needs_live_search {
        Some(crate::config::SearchMode::Live)
    } else {
        None
    }
}
