use std::{fs, path::Path};

use super::support::{derive_session_title_from_text, is_message_thread_not_found};
use super::*;
use crate::codex::CodexApprovalRequest;
use html_escape::encode_safe;
use serde_json::Value;
use uuid::Uuid;

pub(super) fn approval_waiting_text(kind: CodexApprovalKind) -> String {
    match kind {
        CodexApprovalKind::CommandExecution => {
            "Waiting for command approval in Telegram.".to_string()
        }
        CodexApprovalKind::FileChange => {
            "Waiting for file-change approval in Telegram.".to_string()
        }
    }
}

pub(super) fn approval_decision_status(decision: CodexApprovalDecision) -> &'static str {
    match decision {
        CodexApprovalDecision::Accept => "accepted",
        CodexApprovalDecision::AcceptForSession => "accepted for this session",
        CodexApprovalDecision::Decline => "declined",
        CodexApprovalDecision::Cancel => "cancelled",
    }
}

fn approval_button_text(decision: CodexApprovalDecision) -> &'static str {
    match decision {
        CodexApprovalDecision::Accept => "Allow once",
        CodexApprovalDecision::AcceptForSession => "Allow session",
        CodexApprovalDecision::Decline => "Decline",
        CodexApprovalDecision::Cancel => "Cancel turn",
    }
}

fn approval_button_code(decision: CodexApprovalDecision) -> &'static str {
    match decision {
        CodexApprovalDecision::Accept => "a",
        CodexApprovalDecision::AcceptForSession => "s",
        CodexApprovalDecision::Decline => "d",
        CodexApprovalDecision::Cancel => "c",
    }
}

pub(super) fn parse_approval_callback_data(data: &str) -> Option<(String, CodexApprovalDecision)> {
    let mut parts = data.split(':');
    if parts.next()? != "apr" {
        return None;
    }
    let token = parts.next()?.to_string();
    let decision = match parts.next()? {
        "a" => CodexApprovalDecision::Accept,
        "s" => CodexApprovalDecision::AcceptForSession,
        "d" => CodexApprovalDecision::Decline,
        "c" => CodexApprovalDecision::Cancel,
        _ => return None,
    };
    Some((token, decision))
}

pub(super) fn parse_orx_intake_callback_data(data: &str) -> Option<(String, OrxIntakeDecision)> {
    let mut parts = data.split(':');
    if parts.next()? != "int" {
        return None;
    }
    let token = parts.next()?.to_string();
    let decision = match parts.next()? {
        "a" => OrxIntakeDecision::Approve,
        "r" => OrxIntakeDecision::Reject,
        _ => return None,
    };
    Some((token, decision))
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(super) enum OrxOperatorDecision {
    ApproveDesign,
    RequestUiEvidence,
    MergeAndRelease,
    CherryPickAndRelease,
    DiscardAndRelease,
    KeepReserved,
}

pub(super) fn parse_orx_operator_callback_data(
    data: &str,
) -> Option<(String, OrxOperatorDecision)> {
    let mut parts = data.split(':');
    if parts.next()? != "orx" {
        return None;
    }
    let project_key = parts.next()?.to_string();
    let decision = match parts.next()? {
        "ad" => OrxOperatorDecision::ApproveDesign,
        "ui" => OrxOperatorDecision::RequestUiEvidence,
        "mr" => OrxOperatorDecision::MergeAndRelease,
        "cr" => OrxOperatorDecision::CherryPickAndRelease,
        "dr" => OrxOperatorDecision::DiscardAndRelease,
        "kr" => OrxOperatorDecision::KeepReserved,
        _ => return None,
    };
    Some((project_key, decision))
}

pub(super) fn parse_history_callback_data(data: &str) -> Option<(String, usize)> {
    let mut parts = data.split(':');
    if parts.next()? != "his" {
        return None;
    }
    let thread_id = parts.next()?.to_string();
    let index = parts.next()?.parse::<usize>().ok()?;
    Some((thread_id, index))
}

pub(super) fn approval_keyboard(
    token: &str,
    options: &[CodexApprovalDecision],
) -> Option<InlineKeyboardMarkup> {
    let buttons = options
        .iter()
        .copied()
        .map(|decision| InlineKeyboardButton {
            text: approval_button_text(decision).to_string(),
            callback_data: Some(format!("apr:{token}:{}", approval_button_code(decision))),
            url: None,
        })
        .collect::<Vec<_>>();
    if buttons.is_empty() {
        return None;
    }
    let inline_keyboard = buttons
        .chunks(2)
        .map(|chunk| chunk.to_vec())
        .collect::<Vec<_>>();
    Some(InlineKeyboardMarkup { inline_keyboard })
}

pub(super) fn orx_intake_keyboard(token: &str) -> InlineKeyboardMarkup {
    InlineKeyboardMarkup {
        inline_keyboard: vec![vec![
            InlineKeyboardButton {
                text: "Create tickets".to_string(),
                callback_data: Some(format!("int:{token}:a")),
                url: None,
            },
            InlineKeyboardButton {
                text: "Reject".to_string(),
                callback_data: Some(format!("int:{token}:r")),
                url: None,
            },
        ]],
    }
}

pub(super) fn orx_operator_keyboard(
    project_key: &str,
    lane_state: Option<&str>,
    review_kind: Option<&str>,
) -> Option<InlineKeyboardMarkup> {
    let mut inline_keyboard: Vec<Vec<InlineKeyboardButton>> = Vec::new();
    if lane_state == Some("awaiting_orx_review") {
        match review_kind {
            Some("design_review_required") => {
                inline_keyboard.push(vec![InlineKeyboardButton {
                    text: "Approve design".to_string(),
                    callback_data: Some(format!("orx:{project_key}:ad")),
                    url: None,
                }]);
            }
            Some("ui_evidence_missing") => {
                inline_keyboard.push(vec![InlineKeyboardButton {
                    text: "Request Playwright evidence".to_string(),
                    callback_data: Some(format!("orx:{project_key}:ui")),
                    url: None,
                }]);
            }
            _ => {}
        }
    }
    if lane_state == Some("awaiting_hil_release") {
        inline_keyboard.push(vec![
            InlineKeyboardButton {
                text: "Merge + release".to_string(),
                callback_data: Some(format!("orx:{project_key}:mr")),
                url: None,
            },
            InlineKeyboardButton {
                text: "Keep reserved".to_string(),
                callback_data: Some(format!("orx:{project_key}:kr")),
                url: None,
            },
        ]);
        inline_keyboard.push(vec![
            InlineKeyboardButton {
                text: "Cherry-pick + release".to_string(),
                callback_data: Some(format!("orx:{project_key}:cr")),
                url: None,
            },
            InlineKeyboardButton {
                text: "Discard + release".to_string(),
                callback_data: Some(format!("orx:{project_key}:dr")),
                url: None,
            },
        ]);
    }
    if inline_keyboard.is_empty() {
        None
    } else {
        Some(InlineKeyboardMarkup { inline_keyboard })
    }
}

pub(super) fn history_keyboard(
    thread_id: &str,
    index: usize,
    total: usize,
) -> Option<InlineKeyboardMarkup> {
    if total == 0 {
        return None;
    }
    let current = index % total;
    let previous = if current == 0 { total - 1 } else { current - 1 };
    let next = (current + 1) % total;
    let mut row = vec![InlineKeyboardButton {
        text: "Prev".to_string(),
        callback_data: Some(format!("his:{thread_id}:{previous}")),
        url: None,
    }];
    row.push(InlineKeyboardButton {
        text: "Next".to_string(),
        callback_data: Some(format!("his:{thread_id}:{next}")),
        url: None,
    });
    Some(InlineKeyboardMarkup {
        inline_keyboard: vec![row],
    })
}

pub(super) async fn send_markdown_message(
    telegram: &TelegramClient,
    chat_id: i64,
    thread_id: Option<i64>,
    markdown: &str,
    reply_markup: Option<InlineKeyboardMarkup>,
) -> Result<Message> {
    let html = render_markdown_to_html(markdown);
    let mut request = SendMessage::html(chat_id, thread_id, html);
    request.reply_markup = reply_markup.clone();
    match telegram.send_message(request).await {
        Ok(message) => Ok(message),
        Err(error) => {
            if is_message_thread_not_found(&error) && thread_id.unwrap_or_default() != 0 {
                let fallback = html_escape::encode_safe(markdown).to_string();
                let mut fallback_request = SendMessage::html(chat_id, None, fallback);
                fallback_request.reply_markup = reply_markup.clone();
                return telegram.send_message(fallback_request).await;
            }
            let fallback = html_escape::encode_safe(markdown).to_string();
            let mut fallback_request = SendMessage::html(chat_id, thread_id, fallback);
            fallback_request.reply_markup = reply_markup;
            telegram
                .send_message(fallback_request)
                .await
                .with_context(|| format!("failed to send message after html fallback: {error:#}"))
        }
    }
}

pub(super) async fn request_telegram_approval(
    shared: Arc<AppShared>,
    chat_id: i64,
    thread_id: Option<i64>,
    requester_user_id: i64,
    request: CodexApprovalRequest,
    cancel: CancellationToken,
) -> Result<CodexApprovalDecision> {
    let token = Uuid::now_v7().simple().to_string();
    let keyboard = approval_keyboard(&token, &request.options);
    let (sender, receiver) = oneshot::channel();
    shared.pending_approvals.lock().await.insert(
        token.clone(),
        PendingApproval {
            requester_user_id,
            responder: sender,
        },
    );

    let header = match request.kind {
        CodexApprovalKind::CommandExecution => "Approval required: command execution",
        CodexApprovalKind::FileChange => "Approval required: file change",
    };
    let message_text = format!("{header}\n\n{}", request.prompt);
    if let Err(error) = send_markdown_message(
        &shared.telegram,
        chat_id,
        thread_id,
        &message_text,
        keyboard,
    )
    .await
    {
        shared.pending_approvals.lock().await.remove(&token);
        return Err(error);
    }

    let timeout = sleep(Duration::from_secs(15 * 60));
    tokio::pin!(timeout);

    let (decision, send_status) = tokio::select! {
        result = receiver => (match result {
            Ok(decision) => decision,
            Err(_) => CodexApprovalDecision::Decline,
        }, false),
        _ = cancel.cancelled() => (CodexApprovalDecision::Cancel, true),
        _ = &mut timeout => (CodexApprovalDecision::Decline, true),
    };

    shared.pending_approvals.lock().await.remove(&token);

    if send_status {
        let status = format!("Approval {}", approval_decision_status(decision));
        if let Err(error) =
            send_markdown_message(&shared.telegram, chat_id, thread_id, &status, None).await
        {
            tracing::debug!("failed to send approval status: {error:#}");
        }
    }

    Ok(decision)
}

pub(super) fn quick_reply_keyboard(commands: &[Vec<String>]) -> Option<InlineKeyboardMarkup> {
    let inline_keyboard = commands
        .iter()
        .filter_map(|row| {
            let buttons = row
                .iter()
                .filter(|text| !text.trim().is_empty())
                .map(|text| InlineKeyboardButton {
                    text: text.clone(),
                    callback_data: Some(format!("cmd:{text}")),
                    url: None,
                })
                .collect::<Vec<_>>();
            if buttons.is_empty() {
                None
            } else {
                Some(buttons)
            }
        })
        .collect::<Vec<_>>();
    if inline_keyboard.is_empty() {
        None
    } else {
        Some(InlineKeyboardMarkup { inline_keyboard })
    }
}

pub(super) fn model_quick_commands(
    available_models: &[AvailableModel],
    session_model: Option<&str>,
    default_model: Option<&str>,
) -> Vec<Vec<String>> {
    let mut choices = Vec::<String>::new();
    for model in prioritized_model_ids(available_models, session_model, default_model) {
        let command = format!("/model {model}");
        if !choices.contains(&command) {
            choices.push(command);
        }
    }
    choices.push("/model default".to_string());
    choices
        .chunks(2)
        .map(|chunk| chunk.to_vec())
        .collect::<Vec<_>>()
}

pub(super) fn format_model_help_text(
    current_label: &str,
    available_models: &[AvailableModel],
) -> String {
    let _ = available_models;
    format!("Current model: `{current_label}`")
}

fn prioritized_model_ids(
    available_models: &[AvailableModel],
    session_model: Option<&str>,
    default_model: Option<&str>,
) -> Vec<String> {
    let mut ordered = Vec::<String>::new();
    let mut push_model = |model: &str| {
        let model = model.trim();
        if model.is_empty() {
            return;
        }
        if !ordered.iter().any(|existing| existing == model) {
            ordered.push(model.to_string());
        }
    };

    if let Some(model) = session_model {
        push_model(model);
    }
    if let Some(model) = default_model {
        push_model(model);
    }
    for model in available_models
        .iter()
        .filter(|candidate| candidate.is_default)
    {
        push_model(&model.id);
    }
    for model in available_models {
        push_model(&model.id);
    }

    ordered
}

pub(super) fn format_sessions_overview(
    sessions: &[crate::models::SessionRecord],
    current: SessionKey,
    chat: &crate::telegram::Chat,
) -> String {
    let Some(current_session) = sessions.iter().find(|session| session.key == current) else {
        return "\u{2063}".to_string();
    };
    if chat_sessions_keyboard(current_session, chat, sessions).is_some() {
        return "\u{2063}".to_string();
    }
    let mut blocks = Vec::with_capacity(sessions.len() + 1);
    blocks.push(format!(
        "**Sessions**\n`{}` active in this chat",
        sessions.len()
    ));
    for session in sessions {
        blocks.push(format_session_card(session, current, chat));
    }
    blocks.join("\n\n")
}

pub(super) fn format_environment_dashboard(environments: &[CodexEnvironmentSummary]) -> String {
    if environments.is_empty() {
        return "No Codex environments found.".to_string();
    }
    "\u{2063}".to_string()
}

fn format_session_card(
    session: &crate::models::SessionRecord,
    current: SessionKey,
    chat: &crate::telegram::Chat,
) -> String {
    let pointer = if session.key == current {
        "👉"
    } else {
        "•"
    };
    let status = if session.busy { "busy" } else { "idle" };
    let title = session_title_label(session, chat);
    let title = match session_topic_url(chat, session.key.thread_id) {
        Some(url) => format!("[{}]({url})", escape_markdown_label(&title)),
        None => format!("**{}**", escape_markdown_label(&title)),
    };
    let current_badge = if session.key == current {
        " · current"
    } else {
        ""
    };
    let codex = session
        .codex_thread_id
        .as_deref()
        .map(short_codex_thread_id)
        .unwrap_or_else(|| "new".to_string());
    format!(
        "{pointer} {title}{current_badge}\n`#{}` · {} · codex `{}`\n`{}`",
        session.key.thread_id,
        status,
        codex,
        session.cwd.display()
    )
}

pub(super) fn format_codex_sessions_overview(sessions: &[CodexThreadSummary]) -> String {
    if sessions.is_empty() {
        return "No Codex sessions found for this cwd yet.".to_string();
    }
    "\u{2063}".to_string()
}

pub(super) fn format_codex_history_preview_plain(entries: &[CodexHistoryEntry]) -> String {
    let merged = merge_history_preview_entries(entries);
    let mut blocks = vec!["**Recent Codex History**".to_string()];
    for entry in merged {
        let label = if entry.role == "assistant" {
            "Codex"
        } else {
            "You"
        };
        let preview = truncate_history_preview(&entry.text);
        if preview.is_empty() {
            continue;
        }
        blocks.push(format!(
            "**{label}**\n{}",
            format_history_preview_plain_block(&preview)
        ));
    }
    blocks.join("\n")
}

pub(super) fn format_codex_history_preview_html(entries: &[CodexHistoryEntry]) -> String {
    let merged = merge_history_preview_entries(entries);
    let mut blocks = vec!["<b>Recent Codex History</b>".to_string()];
    for entry in merged {
        let label = if entry.role == "assistant" {
            "Codex"
        } else {
            "You"
        };
        let preview = truncate_history_preview(&entry.text);
        if preview.is_empty() {
            continue;
        }
        blocks.push(format!(
            "<b>{}</b>\n<blockquote>{}</blockquote>",
            encode_safe(label),
            format_history_preview_html_block(&preview)
        ));
    }
    blocks.join("\n")
}

fn format_history_preview_plain_block(text: &str) -> String {
    text.lines()
        .map(|line| format!("│ {}", sanitize_history_preview_line(line)))
        .collect::<Vec<_>>()
        .join("\n")
}

fn format_history_preview_html_block(text: &str) -> String {
    render_markdown_to_html(text)
}

fn sanitize_history_preview_line(line: &str) -> String {
    line.chars()
        .map(|ch| match ch {
            '`' => '\'',
            '*' => '∗',
            '_' => 'ˍ',
            '[' => '⟦',
            ']' => '⟧',
            _ => ch,
        })
        .collect()
}

fn merge_history_preview_entries(entries: &[CodexHistoryEntry]) -> Vec<CodexHistoryEntry> {
    let mut merged: Vec<CodexHistoryEntry> = Vec::new();
    for entry in entries {
        let text = entry.text.trim();
        if text.is_empty() {
            continue;
        }
        if let Some(last) = merged.last_mut() {
            if last.role == entry.role {
                if !last.text.is_empty() {
                    last.text.push('\n');
                }
                last.text.push_str(text);
                last.timestamp = entry.timestamp.clone();
                continue;
            }
        }
        merged.push(CodexHistoryEntry {
            role: entry.role.clone(),
            text: text.to_string(),
            timestamp: entry.timestamp.clone(),
        });
    }
    merged
}

fn truncate_history_preview(text: &str) -> String {
    const MAX_CHARS: usize = 1200;
    const MAX_LINES: usize = 16;

    let normalized = text.replace("\r\n", "\n").replace('\r', "\n");
    let mut lines = normalized
        .lines()
        .map(str::trim)
        .filter(|line| !line.is_empty())
        .collect::<Vec<_>>();

    if lines.is_empty() {
        return String::new();
    }

    let mut truncated = false;
    if lines.len() > MAX_LINES {
        lines.truncate(MAX_LINES);
        truncated = true;
    }

    let mut preview = lines.join("\n");
    if preview.chars().count() > MAX_CHARS {
        preview = preview.chars().take(MAX_CHARS).collect::<String>();
        truncated = true;
    }

    if truncated {
        preview.push_str("\n...");
    }
    preview
}

pub(super) fn codex_sessions_keyboard(
    session: &crate::models::SessionRecord,
    sessions: &[CodexThreadSummary],
) -> Option<InlineKeyboardMarkup> {
    let mut inline_keyboard = sessions
        .iter()
        .take(12)
        .map(|summary| {
            let current = session.codex_thread_id.as_deref() == Some(summary.id.as_str());
            let text = if current {
                format!("Current: {}", session_button_label(summary))
            } else {
                session_button_label(summary)
            };
            vec![InlineKeyboardButton {
                text,
                callback_data: Some(format!("cmd:/use {}", summary.id)),
                url: None,
            }]
        })
        .collect::<Vec<_>>();
    inline_keyboard.push(vec![
        InlineKeyboardButton {
            text: "Latest".to_string(),
            callback_data: Some("cmd:/use latest".to_string()),
            url: None,
        },
        InlineKeyboardButton {
            text: "Fresh".to_string(),
            callback_data: Some("cmd:/clear".to_string()),
            url: None,
        },
    ]);
    Some(InlineKeyboardMarkup { inline_keyboard })
}

pub(super) fn chat_sessions_keyboard(
    current_session: &crate::models::SessionRecord,
    chat: &crate::telegram::Chat,
    sessions: &[crate::models::SessionRecord],
) -> Option<InlineKeyboardMarkup> {
    let dashboard_root = current_session.key.thread_id == 0 && chat.is_forum.unwrap_or(false);
    let mut inline_keyboard = Vec::new();
    for session in sessions
        .iter()
        .filter(|session| session.key.thread_id > 0)
        .take(24)
    {
        let label = session_title_label(session, chat);
        let current = current_session.key == session.key
            || current_session.codex_thread_id == session.codex_thread_id
                && current_session.codex_thread_id.is_some();
        let text = if current {
            format!("Current: {}", truncate_button_label(&label))
        } else {
            truncate_button_label(&label)
        };
        let topic_url = session_topic_url(chat, session.key.thread_id);
        let (callback_data, url) = if dashboard_root {
            match topic_url {
                Some(url) => (None, Some(url)),
                None => (Some(format!("ses:{}", session.key.thread_id)), None),
            }
        } else {
            (Some(format!("ses:{}", session.key.thread_id)), None)
        };
        inline_keyboard.push(vec![InlineKeyboardButton {
            text,
            callback_data,
            url,
        }]);
    }
    if inline_keyboard.is_empty() {
        None
    } else {
        Some(InlineKeyboardMarkup { inline_keyboard })
    }
}

pub(super) fn environment_dashboard_keyboard(
    chat: &crate::telegram::Chat,
    current_session: &crate::models::SessionRecord,
    environments: &[CodexEnvironmentSummary],
    sessions: &[crate::models::SessionRecord],
) -> Option<InlineKeyboardMarkup> {
    let mut inline_keyboard = Vec::new();
    for environment in environments.iter().take(24) {
        let existing = sessions
            .iter()
            .find(|session| session_matches_environment(session, environment));
        let current = session_matches_environment(current_session, environment);
        let text = if current {
            format!("Current: {}", truncate_button_label(&environment.name))
        } else {
            truncate_button_label(&environment.name)
        };
        let button_text = if existing.is_some() {
            text
        } else {
            format!("+ {text}")
        };
        let button =
            match existing.and_then(|session| session_topic_url(chat, session.key.thread_id)) {
                Some(url) => InlineKeyboardButton {
                    text: button_text,
                    callback_data: None,
                    url: Some(url),
                },
                None => InlineKeyboardButton {
                    text: button_text,
                    callback_data: Some(format!("env:{}", environment_selector_key(environment))),
                    url: None,
                },
            };
        inline_keyboard.push(vec![button]);
    }
    if inline_keyboard.is_empty() {
        None
    } else {
        Some(InlineKeyboardMarkup { inline_keyboard })
    }
}

fn truncate_button_label(label: &str) -> String {
    const LIMIT: usize = 28;
    let compact = label
        .trim()
        .lines()
        .next()
        .unwrap_or(label)
        .trim()
        .chars()
        .take(LIMIT)
        .collect::<String>();
    if label.chars().count() > LIMIT {
        format!("{compact}...")
    } else {
        compact
    }
}

fn session_button_label(summary: &CodexThreadSummary) -> String {
    const LIMIT: usize = 28;
    let title = summary.title.trim();
    if title.is_empty() {
        return short_codex_thread_id(&summary.id);
    }
    let compact = title
        .lines()
        .next()
        .unwrap_or(title)
        .trim()
        .chars()
        .take(LIMIT)
        .collect::<String>();
    if title.chars().count() > LIMIT {
        format!("{compact}...")
    } else {
        compact
    }
}

pub(super) fn session_title_label(
    session: &crate::models::SessionRecord,
    chat: &crate::telegram::Chat,
) -> String {
    if let Some(title) = session.session_title.as_deref().map(str::trim) {
        if !title.is_empty() {
            return derive_session_title_from_text(title).unwrap_or_else(|| title.to_string());
        }
    }
    if session.key.thread_id == 0 {
        match chat.kind.as_str() {
            "private" => "Direct chat".to_string(),
            _ => chat
                .title
                .as_deref()
                .map(str::trim)
                .filter(|title| !title.is_empty())
                .map(ToOwned::to_owned)
                .unwrap_or_else(|| "Main thread".to_string()),
        }
    } else {
        format!("Topic #{}", session.key.thread_id)
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Hash)]
pub(super) struct ForumEnvironmentBindingKey {
    pub(super) cwd: PathBuf,
    pub(super) topic_title: String,
}

pub(super) fn session_environment_binding_key(
    session: &crate::models::SessionRecord,
) -> Option<ForumEnvironmentBindingKey> {
    let topic_title = session
        .session_title
        .as_deref()
        .map(str::trim)
        .filter(|title| !title.is_empty())?
        .to_string();
    Some(ForumEnvironmentBindingKey {
        cwd: environment_identity_for_cwd(&session.cwd),
        topic_title,
    })
}

fn environment_binding_key(environment: &CodexEnvironmentSummary) -> ForumEnvironmentBindingKey {
    ForumEnvironmentBindingKey {
        cwd: environment.cwd.clone(),
        topic_title: environment.name.trim().to_string(),
    }
}

pub(super) fn session_matches_environment(
    session: &crate::models::SessionRecord,
    environment: &CodexEnvironmentSummary,
) -> bool {
    if session.key.thread_id <= 0 {
        return false;
    }
    session_environment_binding_key(session)
        .map(|binding| binding == environment_binding_key(environment))
        .unwrap_or(false)
}

fn session_topic_url(chat: &crate::telegram::Chat, thread_id: i64) -> Option<String> {
    if thread_id <= 0 {
        return None;
    }
    let forum_suffix = if chat.is_forum.unwrap_or(false) {
        format!("{thread_id}?thread={thread_id}")
    } else {
        thread_id.to_string()
    };
    if let Some(username) = chat.username.as_deref().filter(|value| !value.is_empty()) {
        return Some(format!("https://t.me/{username}/{forum_suffix}"));
    }
    private_topic_link_slug(chat.id).map(|slug| format!("https://t.me/c/{slug}/{forum_suffix}"))
}

pub(super) fn current_session_label(
    session: &crate::models::SessionRecord,
    chat: &crate::telegram::Chat,
) -> String {
    if let Some(thread_id) = session.codex_thread_id.as_deref() {
        if let Ok(Some(summary)) = find_thread_by_id(&default_codex_home(), thread_id) {
            let title = summary.title.trim();
            if !title.is_empty() {
                return derive_session_title_from_text(title).unwrap_or_else(|| title.to_string());
            }
        }
    }
    session_title_label(session, chat)
}

fn session_next_step(session: &crate::models::SessionRecord) -> String {
    if let Some(detail) = session
        .state_detail
        .as_deref()
        .map(str::trim)
        .filter(|value| !value.is_empty())
    {
        return detail.to_string();
    }

    match session.state {
        crate::models::SessionState::Idle => {
            if session.pending_turns > 0 {
                "Queued turn is waiting to run.".to_string()
            } else {
                "Ready for the next turn.".to_string()
            }
        }
        crate::models::SessionState::Planning => "Preparing the next turn.".to_string(),
        crate::models::SessionState::Coding | crate::models::SessionState::Running => {
            "Working on the current turn.".to_string()
        }
        crate::models::SessionState::WaitingApproval => "Waiting for your approval.".to_string(),
        crate::models::SessionState::Blocked => "Blocked for now.".to_string(),
        crate::models::SessionState::Interrupted => "Turn was interrupted.".to_string(),
        crate::models::SessionState::Completed => "Turn completed.".to_string(),
        crate::models::SessionState::Failed => "Turn failed.".to_string(),
    }
}

fn session_change_summary(session: &crate::models::SessionRecord) -> String {
    if let Some(summary) = session
        .last_summary
        .as_deref()
        .map(str::trim)
        .filter(|value| !value.is_empty())
    {
        return summary.to_string();
    }

    if let Some(detail) = session
        .state_detail
        .as_deref()
        .map(str::trim)
        .filter(|value| !value.is_empty())
    {
        return detail.to_string();
    }

    match session.state {
        crate::models::SessionState::Idle => {
            if session.pending_turns > 0 {
                "Queued turn is waiting to run.".to_string()
            } else {
                "No active changes yet.".to_string()
            }
        }
        crate::models::SessionState::Planning => "Preparing the next turn.".to_string(),
        crate::models::SessionState::Coding | crate::models::SessionState::Running => {
            "Working on the current turn.".to_string()
        }
        crate::models::SessionState::WaitingApproval => "Waiting for your approval.".to_string(),
        crate::models::SessionState::Blocked => "Blocked for now.".to_string(),
        crate::models::SessionState::Interrupted => "Turn was interrupted.".to_string(),
        crate::models::SessionState::Completed => "Turn completed.".to_string(),
        crate::models::SessionState::Failed => "Turn failed.".to_string(),
    }
}

pub(super) fn format_current_session_notice(
    session: &crate::models::SessionRecord,
    chat: &crate::telegram::Chat,
) -> String {
    let title = escape_markdown_label(&current_session_label(session, chat));
    let changed = escape_markdown_label(&session_change_summary(session));
    let next_step = escape_markdown_label(&session_next_step(session));
    format!("**Current session:** {title}\n- changed: {changed}\n- next: {next_step}")
}

pub(super) fn format_session_status(
    session: &crate::models::SessionRecord,
    chat: &crate::telegram::Chat,
) -> String {
    let telegram_title = escape_markdown_label(&session_title_label(session, chat));
    let codex_title = session
        .codex_thread_id
        .as_deref()
        .map(|_| escape_markdown_label(&current_session_label(session, chat)))
        .unwrap_or_else(|| {
            escape_markdown_label(if session.force_fresh_thread {
                "fresh"
            } else {
                "unbound"
            })
        });
    let state = session.state.as_str();
    let codex_thread = session
        .codex_thread_id
        .as_deref()
        .map(short_codex_thread_id)
        .unwrap_or_else(|| "new".to_string());
    let model = session.model.as_deref().unwrap_or("default");
    let reasoning = session.reasoning_effort.as_deref().unwrap_or("default");
    let prompt = if session
        .session_prompt
        .as_deref()
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .is_some()
    {
        "set"
    } else {
        "none"
    };
    let state_detail = session
        .state_detail
        .as_deref()
        .map(escape_markdown_label)
        .unwrap_or_else(|| "-".to_string());
    let last_activity = session
        .last_activity_at
        .as_deref()
        .unwrap_or(&session.updated_at);
    let last_summary = session
        .last_summary
        .as_deref()
        .map(escape_markdown_label)
        .unwrap_or_else(|| "-".to_string());
    let runner_section = format_runner_status(&session.cwd)
        .map(|value| format!("\n\n{value}"))
        .unwrap_or_default();

    format!(
        "**Current Telegram session:** {telegram_title}\n- codex session title: {codex_title}\n- state: `{state}`\n- detail: {state_detail}\n- queued turns: `{}`\n- cwd: `{}`\n- codex thread: `{}`\n- model: `{model}`\n- reasoning: `{reasoning}`\n- approval: `{}`\n- sandbox: `{}`\n- search: `{}`\n- prompt: `{prompt}`\n- last activity: `{last_activity}`\n- last summary: {last_summary}{runner_section}",
        session.pending_turns,
        session.cwd.display(),
        codex_thread,
        session.approval_policy,
        session.sandbox_mode,
        session.search_mode.as_codex_value(),
    )
}

fn format_runner_status(cwd: &Path) -> Option<String> {
    let runner_status = runner_status_snapshot(cwd);
    if let Some(status) = runner_status {
        return format_runner_status_snapshot(&status);
    }
    let runner_dir = cwd.join(".memory").join("runner");
    let runtime_state = read_json_value(&runner_dir.join("runtime").join("RUNNER_STATE.json"))?;
    let kanban_state = read_json_value(&runner_dir.join("KANBAN_STATE.json"));

    let runner_mode = runtime_state
        .get("runtime_policy")
        .and_then(|value| value.get("runner_mode"))
        .and_then(Value::as_str)
        .unwrap_or("unknown");
    let current_phase = runtime_state
        .get("current_phase")
        .and_then(Value::as_str)
        .unwrap_or("unknown");
    let phase_status = runtime_state
        .get("phase_status")
        .and_then(Value::as_str)
        .unwrap_or("unknown");
    let done_gate_status = runtime_state
        .get("done_gate_status")
        .and_then(Value::as_str)
        .unwrap_or("unknown");
    let next_task = runtime_state
        .get("next_task")
        .and_then(Value::as_str)
        .unwrap_or("none");
    let next_task_reason = runtime_state
        .get("next_task_reason")
        .and_then(Value::as_str)
        .unwrap_or("-");
    let current_goal = runtime_state
        .get("current_goal")
        .and_then(Value::as_str)
        .unwrap_or("-");

    let mut lines = vec![
        "**Local runner:**".to_string(),
        format!("- mode: `{runner_mode}`"),
        format!("- phase: `{current_phase}`"),
        format!("- phase status: `{phase_status}`"),
        format!("- done gate: `{done_gate_status}`"),
        format!("- next task: {next_task}"),
        format!("- next reason: {}", escape_markdown_label(next_task_reason)),
        format!("- current goal: {}", escape_markdown_label(current_goal)),
    ];

    if let Some(kanban_state) = kanban_state {
        let runner_phase = kanban_state
            .get("phase")
            .and_then(Value::as_str)
            .unwrap_or("unknown");
        let continue_until = kanban_state
            .get("loop")
            .and_then(|value| value.get("continue_until"))
            .and_then(Value::as_str)
            .unwrap_or("unknown");
        let active_issue_url = kanban_state
            .get("active_issue")
            .and_then(|value| value.get("url"))
            .and_then(Value::as_str)
            .unwrap_or("-");
        lines.push(format!("- queue phase: `{runner_phase}`"));
        lines.push(format!("- continue until: `{continue_until}`"));
        lines.push(format!("- active Linear issue: {active_issue_url}"));
    }

    Some(lines.join("\n"))
}

fn format_runner_status_snapshot(status: &Value) -> Option<String> {
    let runtime_policy = status.get("runtime_policy")?;
    let kanban = status.get("kanban");
    let runner_mode = runtime_policy
        .get("runner_mode")
        .and_then(Value::as_str)
        .unwrap_or("unknown");
    let current_phase = status
        .get("current_phase")
        .and_then(Value::as_str)
        .unwrap_or("unknown");
    let phase_status = status
        .get("phase_status")
        .and_then(Value::as_str)
        .unwrap_or("unknown");
    let done_gate_status = status
        .get("done_gate_status")
        .and_then(Value::as_str)
        .unwrap_or("unknown");
    let next_task = status
        .get("next_task")
        .and_then(Value::as_str)
        .unwrap_or("none");
    let next_task_reason = status
        .get("next_task_reason")
        .and_then(Value::as_str)
        .unwrap_or("-");
    let current_goal = status
        .get("current_goal")
        .and_then(Value::as_str)
        .unwrap_or("-");

    let mut lines = vec![
        "**Local runner:**".to_string(),
        format!("- mode: `{runner_mode}`"),
        format!("- phase: `{current_phase}`"),
        format!("- phase status: `{phase_status}`"),
        format!("- done gate: `{done_gate_status}`"),
        format!("- next task: {next_task}"),
        format!("- next reason: {}", escape_markdown_label(next_task_reason)),
        format!("- current goal: {}", escape_markdown_label(current_goal)),
    ];

    if let Some(kanban) = kanban {
        let runner_phase = kanban
            .get("phase")
            .and_then(Value::as_str)
            .unwrap_or("unknown");
        let continue_until = kanban
            .get("continue_until")
            .and_then(Value::as_str)
            .unwrap_or("unknown");
        let active_issue_url = kanban
            .get("active_issue_url")
            .and_then(Value::as_str)
            .unwrap_or("-");
        lines.push(format!("- queue phase: `{runner_phase}`"));
        lines.push(format!("- continue until: `{continue_until}`"));
        lines.push(format!("- active Linear issue: {active_issue_url}"));
    }

    Some(lines.join("\n"))
}

pub(super) fn runner_status_snapshot(cwd: &Path) -> Option<Value> {
    read_json_value(
        &cwd.join(".memory")
            .join("runner")
            .join("RUNNER_STATUS.json"),
    )
}

pub(super) fn runner_notification_fingerprint(status: &Value) -> Option<String> {
    let runtime_policy = status.get("runtime_policy")?;
    let kanban = status.get("kanban");
    let runner_mode = runtime_policy
        .get("runner_mode")
        .and_then(Value::as_str)
        .unwrap_or("unknown");
    let current_phase = status
        .get("current_phase")
        .and_then(Value::as_str)
        .unwrap_or("unknown");
    let phase_status = status
        .get("phase_status")
        .and_then(Value::as_str)
        .unwrap_or("unknown");
    let done_gate_status = status
        .get("done_gate_status")
        .and_then(Value::as_str)
        .unwrap_or("unknown");
    let next_task = status
        .get("next_task")
        .and_then(Value::as_str)
        .unwrap_or("none");
    let runner_phase = kanban
        .and_then(|value| value.get("phase"))
        .and_then(Value::as_str)
        .unwrap_or("unknown");
    let active_issue = kanban
        .and_then(|value| value.get("active_issue_url"))
        .and_then(Value::as_str)
        .unwrap_or("-");
    Some(format!(
        "{runner_mode}|{current_phase}|{phase_status}|{done_gate_status}|{next_task}|{runner_phase}|{active_issue}"
    ))
}

pub(super) fn format_runner_notification(status: &Value) -> Option<String> {
    let runtime_policy = status.get("runtime_policy")?;
    let kanban = status.get("kanban");
    let project = status
        .get("project")
        .and_then(Value::as_str)
        .unwrap_or("runner");
    let runner_mode = runtime_policy
        .get("runner_mode")
        .and_then(Value::as_str)
        .unwrap_or("unknown");
    let current_phase = status
        .get("current_phase")
        .and_then(Value::as_str)
        .unwrap_or("unknown");
    let phase_status = status
        .get("phase_status")
        .and_then(Value::as_str)
        .unwrap_or("unknown");
    let done_gate_status = status
        .get("done_gate_status")
        .and_then(Value::as_str)
        .unwrap_or("unknown");
    let next_task = status
        .get("next_task")
        .and_then(Value::as_str)
        .unwrap_or("none");
    let next_task_reason = status
        .get("next_task_reason")
        .and_then(Value::as_str)
        .unwrap_or("-");
    let current_goal = status
        .get("current_goal")
        .and_then(Value::as_str)
        .unwrap_or("-");
    let mut lines = vec![
        format!("Runner update for `{project}`"),
        format!("- mode: `{runner_mode}`"),
        format!("- phase: `{current_phase}`"),
        format!("- phase status: `{phase_status}`"),
        format!("- done gate: `{done_gate_status}`"),
        format!("- next task: {}", escape_markdown_label(next_task)),
        format!("- next reason: {}", escape_markdown_label(next_task_reason)),
        format!("- current goal: {}", escape_markdown_label(current_goal)),
    ];
    if let Some(kanban) = kanban {
        let runner_phase = kanban
            .get("phase")
            .and_then(Value::as_str)
            .unwrap_or("unknown");
        let active_issue_url = kanban
            .get("active_issue_url")
            .and_then(Value::as_str)
            .unwrap_or("-");
        lines.push(format!("- queue phase: `{runner_phase}`"));
        lines.push(format!(
            "- issue: {}",
            escape_markdown_label(active_issue_url)
        ));
    }
    Some(lines.join("\n"))
}

fn read_json_value(path: &Path) -> Option<Value> {
    let raw = fs::read_to_string(path).ok()?;
    serde_json::from_str(&raw).ok()
}

pub(super) fn format_history_page(
    thread_title: &str,
    thread_id: &str,
    index: usize,
    total: usize,
    entry: &CodexHistoryEntry,
) -> String {
    let thread_title = escape_markdown_label(thread_title.trim());
    let role = escape_markdown_label(&entry.role);
    let timestamp = escape_markdown_label(&entry.timestamp);
    format!(
        "**Session history**\n- codex session title: {thread_title}\n- codex thread: `{}`\n- message: `{}/{}`\n- role: `{role}`\n- time: `{timestamp}`\n\n```text\n{}\n```",
        short_codex_thread_id(thread_id),
        index + 1,
        total,
        truncate_history_page_text(&entry.text),
    )
}

fn truncate_history_page_text(text: &str) -> String {
    const MAX_CHARS: usize = 3200;
    let normalized = text
        .replace("\r\n", "\n")
        .replace('\r', "\n")
        .replace("```", "'''");
    if normalized.chars().count() <= MAX_CHARS {
        return normalized;
    }
    let mut truncated = normalized.chars().take(MAX_CHARS).collect::<String>();
    truncated.push_str("\n...");
    truncated
}

pub(super) fn environment_topic_name(environment: &CodexEnvironmentSummary) -> String {
    environment.name.trim().to_string()
}

pub(super) fn private_topic_link_slug(chat_id: i64) -> Option<i64> {
    let abs = chat_id.checked_abs()?;
    abs.checked_sub(1_000_000_000_000)
        .filter(|value| *value > 0)
}

pub(super) fn short_codex_thread_id(thread_id: &str) -> String {
    const EDGE: usize = 8;
    if thread_id.len() <= EDGE * 2 + 1 {
        thread_id.to_string()
    } else {
        format!(
            "{}…{}",
            &thread_id[..EDGE],
            &thread_id[thread_id.len() - EDGE..]
        )
    }
}

pub(super) fn escape_markdown_label(text: &str) -> String {
    let mut escaped = String::with_capacity(text.len());
    for ch in text.chars() {
        match ch {
            '\\' | '[' | ']' | '(' | ')' | '*' | '_' | '`' => {
                escaped.push('\\');
                escaped.push(ch);
            }
            _ => escaped.push(ch),
        }
    }
    escaped
}
