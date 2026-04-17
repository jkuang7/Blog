use super::*;
use crate::app::forum::{
    BotNameSyncStatus, parse_bot_name_sync_status, project_only_display_name,
    should_attempt_bot_name_sync,
};
use crate::config::SearchMode;
use std::fs;
use std::path::PathBuf;
use tempfile::NamedTempFile;
use tempfile::TempDir;

fn sample_workspace() -> PathBuf {
    std::env::temp_dir()
        .join("telecodex-tests")
        .join("workspace")
}

fn sample_voice_file() -> PathBuf {
    std::env::temp_dir()
        .join("telecodex-tests")
        .join("attachments")
        .join("voice.ogg")
}

fn sample_turn_workspace() -> TurnWorkspace {
    let root = std::env::temp_dir().join("telecodex-tests").join("turn");
    let out_dir = root.join("out");
    TurnWorkspace { root, out_dir }
}

fn sample_defaults() -> SessionDefaults {
    SessionDefaults {
        cwd: sample_workspace(),
        model: Some("gpt-5.4-mini".to_string()),
        reasoning_effort: Some("medium".to_string()),
        session_prompt: None,
        sandbox_mode: "workspace-write".to_string(),
        approval_policy: "never".to_string(),
        search_mode: SearchMode::Disabled,
        add_dirs: vec![],
    }
}

fn sample_codex_config() -> crate::config::CodexConfig {
    crate::config::CodexConfig {
        binary: PathBuf::from("codex"),
        default_cwd: sample_workspace(),
        default_model: Some("gpt-5.4-mini".to_string()),
        default_reasoning_effort: Some("medium".to_string()),
        execution_model: Some("gpt-5.4".to_string()),
        execution_reasoning_effort: Some("high".to_string()),
        default_sandbox: "workspace-write".to_string(),
        default_approval: "never".to_string(),
        default_search_mode: SearchMode::Disabled,
        default_add_dirs: vec![],
        seed_workspaces: vec![],
        import_desktop_history: true,
        import_cli_history: true,
    }
}

fn sample_turn_request(session_key: SessionKey) -> TurnRequest {
    TurnRequest {
        session_key,
        from_user_id: 100,
        prompt: "hello".to_string(),
        runtime_instructions: None,
        attachments: vec![],
        review_mode: None,
        override_search_mode: None,
    }
}

fn sample_config(db_path: PathBuf) -> crate::config::Config {
    crate::config::Config {
        telegram: crate::config::TelegramConfig {
            bot_token: Some("test-token".to_string()),
            bot_token_env: None,
            api_base: "http://127.0.0.1:9".to_string(),
            use_message_drafts: true,
            primary_forum_chat_id: None,
            auto_create_topics: false,
            forum_sync_topics_per_poll: 2,
            stale_topic_days: None,
            stale_topic_action: crate::config::StaleTopicAction::None,
        },
        codex: sample_codex_config(),
        orx: None,
        db_path,
        startup_admin_ids: vec![100],
        poll_timeout_seconds: 30,
        edit_debounce_ms: 900,
        max_text_chunk: 3500,
        tmp_dir: None,
    }
}

#[test]
fn detects_stale_codex_thread_errors() {
    let error = anyhow::anyhow!("no rollout found for thread id 019abc | code -32600");

    assert!(should_reset_session_after_error(&error));
}

#[test]
fn detects_stale_codex_thread_errors_in_error_context() {
    let error = anyhow::anyhow!("codex turn failed")
        .context("no rollout found for thread id 019abc | code -32600");

    assert!(should_reset_session_after_error(&error));
}

#[test]
fn ignores_unrelated_invalid_request_errors() {
    let error = anyhow::anyhow!("json-rpc request rejected with code -32600");

    assert!(!should_reset_session_after_error(&error));
}

#[test]
fn validates_absolute_directories() {
    let cwd = std::env::current_dir().unwrap();
    assert!(validate_directory(cwd.to_str().unwrap()).is_ok());
    assert!(validate_directory("relative\\path").is_err());
}

#[test]
fn validates_sandbox_values() {
    assert!(ensure_sandbox_mode("read-only").is_ok());
    assert!(ensure_sandbox_mode("boom").is_err());
}

#[test]
fn enables_live_search_for_latest_queries() {
    assert_eq!(
        auto_search_mode_for_prompt("what's new in the world over the last day?"),
        Some(SearchMode::Live)
    );
    assert_eq!(auto_search_mode_for_prompt("explain this code"), None);
}

#[test]
fn parses_bot_name_sync_status_from_orx_payload() {
    let payload = serde_json::json!({
        "bot": {
            "desired_display_name": "alpha - fix lock drift",
            "current_display_name": "alpha - stale state",
            "name_sync_state": "pending",
            "name_sync_retry_at": "2026-04-16T16:00:00+00:00"
        }
    });

    let status = parse_bot_name_sync_status(&payload).expect("status");

    assert_eq!(status.desired_name, "alpha - fix lock drift");
    assert_eq!(status.current_name.as_deref(), Some("alpha - stale state"));
    assert_eq!(status.sync_state.as_deref(), Some("pending"));
    assert_eq!(
        status.retry_at.map(|value| value.to_rfc3339()),
        Some("2026-04-16T16:00:00+00:00".to_string())
    );
}

#[test]
fn skips_bot_name_sync_while_rate_limited() {
    let status = BotNameSyncStatus {
        desired_name: "alpha - fix lock drift".to_string(),
        current_name: Some("alpha - stale state".to_string()),
        sync_state: Some("rate_limited".to_string()),
        retry_at: Some(
            chrono::DateTime::parse_from_rfc3339("2026-04-16T16:00:00+00:00")
                .unwrap()
                .with_timezone(&chrono::Utc),
        ),
    };

    let should_attempt = should_attempt_bot_name_sync(
        &status,
        chrono::DateTime::parse_from_rfc3339("2026-04-16T15:59:00+00:00")
            .unwrap()
            .with_timezone(&chrono::Utc),
    );

    assert!(!should_attempt);
}

#[test]
fn retries_bot_name_sync_after_rate_limit_expires() {
    let status = BotNameSyncStatus {
        desired_name: "alpha - fix lock drift".to_string(),
        current_name: Some("alpha - stale state".to_string()),
        sync_state: Some("rate_limited".to_string()),
        retry_at: Some(
            chrono::DateTime::parse_from_rfc3339("2026-04-16T16:00:00+00:00")
                .unwrap()
                .with_timezone(&chrono::Utc),
        ),
    };

    let should_attempt = should_attempt_bot_name_sync(
        &status,
        chrono::DateTime::parse_from_rfc3339("2026-04-16T16:01:00+00:00")
            .unwrap()
            .with_timezone(&chrono::Utc),
    );

    assert!(should_attempt);
}

#[test]
fn derives_project_only_display_name_from_issue_summary_name() {
    assert_eq!(
        project_only_display_name("tmux-codex - fix runner drift"),
        Some("tmux-codex".to_string())
    );
    assert_eq!(project_only_display_name("tmux-codex"), None);
}

#[test]
fn truncates_live_updates_to_single_chunk() {
    let text = "line one\n\nline two\n\nline three";
    let truncated = truncate_for_live_update(text, 16);
    assert!(truncated.len() <= 16);
    assert!(!truncated.is_empty());
}

#[test]
fn execution_profile_detection_promotes_kanban_and_review_turns() {
    let session_key = SessionKey::new(1, Some(2));
    let mut planning = sample_turn_request(session_key);
    planning.prompt =
        "I'm treating this as a planning pass for the kanban control flow.".to_string();
    assert!(should_use_execution_profile(&planning));

    let mut kanban = sample_turn_request(session_key);
    kanban.prompt = "/kanban".to_string();
    assert!(should_use_execution_profile(&kanban));

    let mut continue_kanban = sample_turn_request(session_key);
    continue_kanban.prompt = "continue kanban".to_string();
    assert!(should_use_execution_profile(&continue_kanban));

    let mut review = sample_turn_request(session_key);
    review.review_mode = Some(crate::models::ReviewRequest {
        base: Some("main".to_string()),
        commit: None,
        uncommitted: true,
        title: None,
        prompt: None,
    });
    assert!(should_use_execution_profile(&review));

    let normal = sample_turn_request(session_key);
    assert!(!should_use_execution_profile(&normal));
}

#[test]
fn execution_profile_upgrades_default_session_settings_for_coding_turns() {
    let config = sample_codex_config();
    let session = crate::models::SessionRecord {
        id: 1,
        key: SessionKey::new(1, Some(2)),
        session_title: Some("Test".to_string()),
        codex_thread_id: None,
        force_fresh_thread: false,
        updated_at: "2026-04-14T00:00:00Z".to_string(),
        cwd: sample_workspace(),
        model: Some("gpt-5.4-mini".to_string()),
        reasoning_effort: Some("medium".to_string()),
        session_prompt: None,
        sandbox_mode: "workspace-write".to_string(),
        approval_policy: "never".to_string(),
        search_mode: SearchMode::Disabled,
        add_dirs: vec![],
        busy: false,
        state: crate::models::SessionState::Idle,
        state_detail: None,
        last_status_message_id: None,
        last_activity_at: None,
        last_summary: None,
        pending_turns: 0,
    };
    let mut request = sample_turn_request(session.key);
    request.prompt = "/kanban".to_string();

    let runtime_session = apply_execution_profile(&session, &config, &request);

    assert_eq!(runtime_session.model.as_deref(), Some("gpt-5.4"));
    assert_eq!(runtime_session.reasoning_effort.as_deref(), Some("high"));
}

#[test]
fn execution_profile_respects_manual_session_model_overrides() {
    let config = sample_codex_config();
    let session = crate::models::SessionRecord {
        id: 1,
        key: SessionKey::new(1, Some(2)),
        session_title: Some("Test".to_string()),
        codex_thread_id: None,
        force_fresh_thread: false,
        updated_at: "2026-04-14T00:00:00Z".to_string(),
        cwd: sample_workspace(),
        model: Some("gpt-5.3-codex".to_string()),
        reasoning_effort: Some("low".to_string()),
        session_prompt: None,
        sandbox_mode: "workspace-write".to_string(),
        approval_policy: "never".to_string(),
        search_mode: SearchMode::Disabled,
        add_dirs: vec![],
        busy: false,
        state: crate::models::SessionState::Idle,
        state_detail: None,
        last_status_message_id: None,
        last_activity_at: None,
        last_summary: None,
        pending_turns: 0,
    };
    let mut request = sample_turn_request(session.key);
    request.prompt = "/kanban".to_string();

    let runtime_session = apply_execution_profile(&session, &config, &request);

    assert_eq!(runtime_session.model.as_deref(), Some("gpt-5.3-codex"));
    assert_eq!(runtime_session.reasoning_effort.as_deref(), Some("low"));
}

#[test]
fn hides_sessions_overview_body_when_keyboard_is_available() {
    let session = crate::models::SessionRecord {
        id: 1,
        key: SessionKey::new(-1001234567890, Some(323)),
        session_title: Some("Water meter".to_string()),
        codex_thread_id: Some("019ce152-99e8-7c30-b5b7-166e6aebd550".to_string()),
        force_fresh_thread: false,
        updated_at: "2026-03-13T10:00:00Z".to_string(),
        cwd: sample_workspace(),
        model: None,
        reasoning_effort: None,
        session_prompt: None,
        sandbox_mode: "workspace-write".to_string(),
        approval_policy: "never".to_string(),
        search_mode: SearchMode::Disabled,
        add_dirs: vec![],
        busy: false,
        state: crate::models::SessionState::Idle,
        state_detail: None,
        last_status_message_id: None,
        last_activity_at: None,
        last_summary: None,
        pending_turns: 0,
    };
    let chat = crate::telegram::Chat {
        id: -1001234567890,
        kind: "supergroup".to_string(),
        is_forum: Some(true),
        username: Some("varv_alarms_bot_chat".to_string()),
        title: Some("Codex chat".to_string()),
    };

    let body = format_sessions_overview(&[session.clone()], session.key, &chat);

    assert_eq!(body, "\u{2063}");
}

#[test]
fn builds_clickable_chat_sessions_keyboard() {
    let session = crate::models::SessionRecord {
        id: 1,
        key: SessionKey::new(-1001234567890, Some(323)),
        session_title: Some("Water meter".to_string()),
        codex_thread_id: Some("019ce152-99e8-7c30-b5b7-166e6aebd550".to_string()),
        force_fresh_thread: false,
        updated_at: "2026-03-13T10:00:00Z".to_string(),
        cwd: sample_workspace(),
        model: None,
        reasoning_effort: None,
        session_prompt: None,
        sandbox_mode: "workspace-write".to_string(),
        approval_policy: "never".to_string(),
        search_mode: SearchMode::Disabled,
        add_dirs: vec![],
        busy: false,
        state: crate::models::SessionState::Idle,
        state_detail: None,
        last_status_message_id: None,
        last_activity_at: None,
        last_summary: None,
        pending_turns: 0,
    };
    let chat = crate::telegram::Chat {
        id: -1001234567890,
        kind: "supergroup".to_string(),
        is_forum: Some(true),
        username: Some("varv_alarms_bot_chat".to_string()),
        title: Some("Codex chat".to_string()),
    };

    let keyboard = chat_sessions_keyboard(&session, &chat, std::slice::from_ref(&session)).unwrap();

    assert_eq!(
        keyboard.inline_keyboard[0][0].callback_data,
        Some("ses:323".to_string())
    );
    assert_eq!(keyboard.inline_keyboard[0][0].url, None);
}

#[test]
fn builds_topic_links_for_dashboard_root_sessions_keyboard() {
    let root_session = crate::models::SessionRecord {
        id: 1,
        key: SessionKey::new(-1001234567890, None),
        session_title: Some("Dashboard".to_string()),
        codex_thread_id: None,
        force_fresh_thread: false,
        updated_at: "2026-03-13T10:00:00Z".to_string(),
        cwd: sample_workspace(),
        model: None,
        reasoning_effort: None,
        session_prompt: None,
        sandbox_mode: "workspace-write".to_string(),
        approval_policy: "never".to_string(),
        search_mode: SearchMode::Disabled,
        add_dirs: vec![],
        busy: false,
        state: crate::models::SessionState::Idle,
        state_detail: None,
        last_status_message_id: None,
        last_activity_at: None,
        last_summary: None,
        pending_turns: 0,
    };
    let topic_session = crate::models::SessionRecord {
        id: 2,
        key: SessionKey::new(-1001234567890, Some(323)),
        session_title: Some("Water meter".to_string()),
        codex_thread_id: Some("019ce152-99e8-7c30-b5b7-166e6aebd550".to_string()),
        force_fresh_thread: false,
        updated_at: "2026-03-13T10:00:00Z".to_string(),
        cwd: sample_workspace(),
        model: None,
        reasoning_effort: None,
        session_prompt: None,
        sandbox_mode: "workspace-write".to_string(),
        approval_policy: "never".to_string(),
        search_mode: SearchMode::Disabled,
        add_dirs: vec![],
        busy: false,
        state: crate::models::SessionState::Idle,
        state_detail: None,
        last_status_message_id: None,
        last_activity_at: None,
        last_summary: None,
        pending_turns: 0,
    };
    let chat = crate::telegram::Chat {
        id: -1001234567890,
        kind: "supergroup".to_string(),
        is_forum: Some(true),
        username: Some("varv_alarms_bot_chat".to_string()),
        title: Some("Codex chat".to_string()),
    };

    let keyboard =
        chat_sessions_keyboard(&root_session, &chat, std::slice::from_ref(&topic_session)).unwrap();

    assert_eq!(keyboard.inline_keyboard[0][0].callback_data, None);
    assert_eq!(
        keyboard.inline_keyboard[0][0].url,
        Some("https://t.me/varv_alarms_bot_chat/323?thread=323".to_string())
    );
}

#[test]
fn derives_private_topic_link_slug_from_bot_api_chat_id() {
    assert_eq!(private_topic_link_slug(-1001234567890), Some(1234567890));
    assert_eq!(private_topic_link_slug(275328656), None);
}

#[test]
fn session_environment_match_requires_same_title_and_cwd() {
    let session = crate::models::SessionRecord {
        id: 1,
        key: SessionKey::new(1, Some(10)),
        session_title: Some("Ops Alerts".to_string()),
        codex_thread_id: Some("019".to_string()),
        force_fresh_thread: false,
        updated_at: "2026-03-14T10:00:00Z".to_string(),
        cwd: sample_workspace(),
        model: None,
        reasoning_effort: None,
        session_prompt: None,
        sandbox_mode: "workspace-write".to_string(),
        approval_policy: "never".to_string(),
        search_mode: SearchMode::Disabled,
        add_dirs: vec![],
        busy: false,
        state: crate::models::SessionState::Idle,
        state_detail: None,
        last_status_message_id: None,
        last_activity_at: None,
        last_summary: None,
        pending_turns: 0,
    };

    let same = CodexEnvironmentSummary {
        cwd: environment_identity_for_cwd(&session.cwd),
        name: "Ops Alerts".to_string(),
        latest_thread_id: Some("thr-1".to_string()),
        updated_at: "2026-03-14T10:05:00Z".to_string(),
    };
    let different_title = CodexEnvironmentSummary {
        cwd: environment_identity_for_cwd(&session.cwd),
        name: "ops alerts".to_string(),
        latest_thread_id: Some("thr-2".to_string()),
        updated_at: "2026-03-14T10:06:00Z".to_string(),
    };

    assert!(session_matches_environment(&session, &same));
    assert!(!session_matches_environment(&session, &different_title));
}

#[test]
fn forum_sync_preserves_manual_codex_binding() {
    let session = crate::models::SessionRecord {
        id: 1,
        key: SessionKey::new(-1001234567890, Some(323)),
        session_title: Some("kombez".to_string()),
        codex_thread_id: Some("manual-thread".to_string()),
        force_fresh_thread: false,
        updated_at: "2026-03-13T10:00:00Z".to_string(),
        cwd: sample_workspace(),
        model: None,
        reasoning_effort: None,
        session_prompt: None,
        sandbox_mode: "workspace-write".to_string(),
        approval_policy: "never".to_string(),
        search_mode: SearchMode::Disabled,
        add_dirs: vec![],
        busy: false,
        state: crate::models::SessionState::Idle,
        state_detail: None,
        last_status_message_id: None,
        last_activity_at: None,
        last_summary: None,
        pending_turns: 0,
    };
    let environment = crate::codex_history::CodexEnvironmentSummary {
        cwd: sample_workspace(),
        name: "kombez".to_string(),
        latest_thread_id: Some("latest-thread".to_string()),
        updated_at: "2026-03-13T10:00:00Z".to_string(),
    };

    assert_eq!(
        super::forum::environment_sync_thread_binding(&session, &environment),
        None
    );
}

#[test]
fn forum_sync_seeds_unbound_environment_session() {
    let session = crate::models::SessionRecord {
        id: 1,
        key: SessionKey::new(-1001234567890, Some(323)),
        session_title: Some("kombez".to_string()),
        codex_thread_id: None,
        force_fresh_thread: false,
        updated_at: "2026-03-13T10:00:00Z".to_string(),
        cwd: sample_workspace(),
        model: None,
        reasoning_effort: None,
        session_prompt: None,
        sandbox_mode: "workspace-write".to_string(),
        approval_policy: "never".to_string(),
        search_mode: SearchMode::Disabled,
        add_dirs: vec![],
        busy: false,
        state: crate::models::SessionState::Idle,
        state_detail: None,
        last_status_message_id: None,
        last_activity_at: None,
        last_summary: None,
        pending_turns: 0,
    };
    let environment = crate::codex_history::CodexEnvironmentSummary {
        cwd: sample_workspace(),
        name: "kombez".to_string(),
        latest_thread_id: Some("latest-thread".to_string()),
        updated_at: "2026-03-13T10:00:00Z".to_string(),
    };

    assert_eq!(
        super::forum::environment_sync_thread_binding(&session, &environment),
        Some("latest-thread")
    );
}

#[test]
fn forum_sync_preserves_fresh_thread_request() {
    let session = crate::models::SessionRecord {
        id: 1,
        key: SessionKey::new(-1001234567890, Some(323)),
        session_title: Some("kombez".to_string()),
        codex_thread_id: None,
        force_fresh_thread: true,
        updated_at: "2026-03-13T10:00:00Z".to_string(),
        cwd: sample_workspace(),
        model: None,
        reasoning_effort: None,
        session_prompt: None,
        sandbox_mode: "workspace-write".to_string(),
        approval_policy: "never".to_string(),
        search_mode: SearchMode::Disabled,
        add_dirs: vec![],
        busy: false,
        state: crate::models::SessionState::Idle,
        state_detail: None,
        last_status_message_id: None,
        last_activity_at: None,
        last_summary: None,
        pending_turns: 0,
    };
    let environment = crate::codex_history::CodexEnvironmentSummary {
        cwd: sample_workspace(),
        name: "kombez".to_string(),
        latest_thread_id: Some("latest-thread".to_string()),
        updated_at: "2026-03-13T10:00:00Z".to_string(),
    };

    assert_eq!(
        super::forum::environment_sync_thread_binding(&session, &environment),
        None
    );
}

#[test]
fn format_current_session_notice_is_concise() {
    let session = crate::models::SessionRecord {
        id: 1,
        key: SessionKey::new(-1001234567890, Some(323)),
        session_title: Some("Water meter".to_string()),
        codex_thread_id: None,
        force_fresh_thread: false,
        updated_at: "2026-03-13T10:00:00Z".to_string(),
        cwd: sample_workspace(),
        model: None,
        reasoning_effort: None,
        session_prompt: None,
        sandbox_mode: "workspace-write".to_string(),
        approval_policy: "never".to_string(),
        search_mode: SearchMode::Disabled,
        add_dirs: vec![],
        busy: false,
        state: crate::models::SessionState::Planning,
        state_detail: Some("Preparing Codex turn.".to_string()),
        last_status_message_id: None,
        last_activity_at: None,
        last_summary: Some("Checked existing issue threads.".to_string()),
        pending_turns: 1,
    };
    let chat = crate::telegram::Chat {
        id: -1001234567890,
        kind: "supergroup".to_string(),
        is_forum: Some(true),
        username: Some("varv_alarms_bot_chat".to_string()),
        title: Some("Codex chat".to_string()),
    };

    let notice = format_current_session_notice(&session, &chat);

    assert_eq!(
        notice,
        "**Current session:** Water meter\n- changed: Checked existing issue threads.\n- next: Preparing Codex turn."
    );
    assert!(!notice.contains("cwd"));
    assert!(!notice.contains("last summary"));
}

#[test]
fn format_session_status_marks_unbound_codex_session() {
    let session = crate::models::SessionRecord {
        id: 1,
        key: SessionKey::new(-1001234567890, Some(323)),
        session_title: Some("Water meter".to_string()),
        codex_thread_id: None,
        force_fresh_thread: false,
        updated_at: "2026-03-13T10:00:00Z".to_string(),
        cwd: sample_workspace(),
        model: None,
        reasoning_effort: None,
        session_prompt: None,
        sandbox_mode: "workspace-write".to_string(),
        approval_policy: "never".to_string(),
        search_mode: SearchMode::Disabled,
        add_dirs: vec![],
        busy: false,
        state: crate::models::SessionState::Idle,
        state_detail: None,
        last_status_message_id: None,
        last_activity_at: None,
        last_summary: None,
        pending_turns: 0,
    };
    let chat = crate::telegram::Chat {
        id: -1001234567890,
        kind: "supergroup".to_string(),
        is_forum: Some(true),
        username: Some("varv_alarms_bot_chat".to_string()),
        title: Some("Codex chat".to_string()),
    };

    let status = format_session_status(&session, &chat);

    assert!(status.contains("**Current Telegram session:** Water meter"));
    assert!(status.contains("- codex session title: unbound"));
    assert!(!status.contains("- codex session title: Water meter"));
}

#[test]
fn format_session_status_marks_fresh_codex_session() {
    let session = crate::models::SessionRecord {
        id: 1,
        key: SessionKey::new(-1001234567890, Some(323)),
        session_title: Some("Water meter".to_string()),
        codex_thread_id: None,
        force_fresh_thread: true,
        updated_at: "2026-03-13T10:00:00Z".to_string(),
        cwd: sample_workspace(),
        model: None,
        reasoning_effort: None,
        session_prompt: None,
        sandbox_mode: "workspace-write".to_string(),
        approval_policy: "never".to_string(),
        search_mode: SearchMode::Disabled,
        add_dirs: vec![],
        busy: false,
        state: crate::models::SessionState::Idle,
        state_detail: None,
        last_status_message_id: None,
        last_activity_at: None,
        last_summary: None,
        pending_turns: 0,
    };
    let chat = crate::telegram::Chat {
        id: -1001234567890,
        kind: "supergroup".to_string(),
        is_forum: Some(true),
        username: Some("varv_alarms_bot_chat".to_string()),
        title: Some("Codex chat".to_string()),
    };

    let status = format_session_status(&session, &chat);

    assert!(status.contains("- codex session title: fresh"));
}

#[test]
fn format_session_status_includes_local_runner_summary_when_present() {
    let temp = TempDir::new().unwrap();
    let cwd = temp.path().to_path_buf();
    let runner_dir = cwd.join(".memory").join("runner");
    fs::create_dir_all(&runner_dir).unwrap();
    fs::write(
        runner_dir.join("RUNNER_STATUS.json"),
        r#"{
            "runtime_policy": {"runner_mode": "exec", "task_source": "linear_orx"},
            "current_phase": "verify",
            "phase_status": "active",
            "done_gate_status": "passed",
            "next_task": "Finalize the kanban transition",
            "next_task_reason": "Need the ticket-native loop next",
            "current_goal": "Move runner state to ticket-native control",
            "kanban": {
                "phase": "selecting",
                "continue_until": "board_complete_or_all_blocked",
                "active_issue_url": "https://linear.app/jkprojects/issue/PRO-3"
            }
        }"#,
    )
    .unwrap();

    let session = crate::models::SessionRecord {
        id: 1,
        key: SessionKey::new(-1001234567890, Some(323)),
        session_title: Some("BentoBox".to_string()),
        codex_thread_id: None,
        force_fresh_thread: false,
        updated_at: "2026-03-13T10:00:00Z".to_string(),
        cwd,
        model: None,
        reasoning_effort: None,
        session_prompt: None,
        sandbox_mode: "workspace-write".to_string(),
        approval_policy: "never".to_string(),
        search_mode: SearchMode::Disabled,
        add_dirs: vec![],
        busy: false,
        state: crate::models::SessionState::Running,
        state_detail: None,
        last_status_message_id: None,
        last_activity_at: None,
        last_summary: None,
        pending_turns: 0,
    };
    let chat = crate::telegram::Chat {
        id: -1001234567890,
        kind: "supergroup".to_string(),
        is_forum: Some(true),
        username: Some("bentobox_bot_chat".to_string()),
        title: Some("BentoBox".to_string()),
    };

    let status = format_session_status(&session, &chat);

    assert!(status.contains("**Local runner:**"));
    assert!(status.contains("- mode: `exec`"));
    assert!(status.contains("- phase: `verify`"));
    assert!(status.contains("- queue phase: `selecting`"));
    assert!(status.contains("https://linear.app/jkprojects/issue/PRO-3"));
}

#[test]
fn runner_notification_helpers_produce_stable_status_summary() {
    let temp = TempDir::new().unwrap();
    let cwd = temp.path().to_path_buf();
    let runner_dir = cwd.join(".memory").join("runner");
    fs::create_dir_all(&runner_dir).unwrap();
    fs::write(
        runner_dir.join("RUNNER_STATUS.json"),
        r#"{
            "project": "validation-os",
            "runtime_policy": {"runner_mode": "exec", "task_source": "linear_orx"},
            "current_phase": "execute",
            "phase_status": "active",
            "done_gate_status": "pending",
            "next_task": "Implement the ticket-native runner",
            "next_task_reason": "Need deterministic Telegram control",
            "current_goal": "Ship the Telegram-driven infinite runner",
            "kanban": {
                "phase": "executing",
                "continue_until": "board_complete_or_all_blocked",
                "active_issue_url": "https://linear.app/jkprojects/issue/PRO-3"
            }
        }"#,
    )
    .unwrap();

    let snapshot = runner_status_snapshot(&cwd).expect("runner snapshot");
    let fingerprint = runner_notification_fingerprint(&snapshot).expect("runner fingerprint");
    let text = format_runner_notification(&snapshot).expect("runner notification");

    assert!(fingerprint.contains("execute"));
    assert!(fingerprint.contains("executing"));
    assert!(text.contains("Runner update for `validation-os`"));
    assert!(text.contains("- phase: `execute`"));
    assert!(text.contains("- queue phase: `executing`"));
    assert!(text.contains("https://linear.app/jkprojects/issue/PRO-3"));
}

#[test]
fn history_page_cache_evicts_oldest_entry_when_size_limit_is_hit() {
    let ttl = Duration::from_secs(300);
    let base = Instant::now();
    let mut cache = HistoryPageCache::default();
    let page = HistoryPageData {
        thread_title: "Session".to_string(),
        pages: vec![crate::codex_history::CodexHistoryEntry {
            role: "assistant".to_string(),
            text: "answer".to_string(),
            timestamp: "2026-03-13T09:00:00Z".to_string(),
        }],
    };
    let first = HistoryPageCacheKey {
        codex_thread_id: "thread-1".to_string(),
        message_id: 10,
    };
    let second = HistoryPageCacheKey {
        codex_thread_id: "thread-2".to_string(),
        message_id: 11,
    };
    let third = HistoryPageCacheKey {
        codex_thread_id: "thread-3".to_string(),
        message_id: 12,
    };

    cache.insert(first.clone(), page.clone(), base, ttl, 2);
    cache.insert(
        second.clone(),
        page.clone(),
        base + Duration::from_secs(1),
        ttl,
        2,
    );
    cache.insert(
        third.clone(),
        page.clone(),
        base + Duration::from_secs(2),
        ttl,
        2,
    );

    assert!(
        cache
            .get(&first, base + Duration::from_secs(2), ttl)
            .is_none()
    );
    assert_eq!(
        cache.get(&second, base + Duration::from_secs(2), ttl),
        Some(page.clone())
    );
    assert_eq!(
        cache.get(&third, base + Duration::from_secs(2), ttl),
        Some(page)
    );
}

#[test]
fn history_page_cache_expires_stale_entries() {
    let ttl = Duration::from_secs(60);
    let base = Instant::now();
    let mut cache = HistoryPageCache::default();
    let key = HistoryPageCacheKey {
        codex_thread_id: "thread-1".to_string(),
        message_id: 10,
    };

    cache.insert(
        key.clone(),
        HistoryPageData {
            thread_title: "Session".to_string(),
            pages: vec![crate::codex_history::CodexHistoryEntry {
                role: "assistant".to_string(),
                text: "answer".to_string(),
                timestamp: "2026-03-13T09:00:00Z".to_string(),
            }],
        },
        base,
        ttl,
        4,
    );

    assert!(
        cache
            .get(&key, base + Duration::from_secs(61), ttl)
            .is_none()
    );
    assert!(cache.entries.is_empty());
}

#[test]
fn picks_last_assistant_text_from_history() {
    let history = vec![
        crate::codex_history::CodexHistoryEntry {
            role: "user".to_string(),
            text: "first".to_string(),
            timestamp: "2026-03-13T09:00:00Z".to_string(),
        },
        crate::codex_history::CodexHistoryEntry {
            role: "assistant".to_string(),
            text: "alpha".to_string(),
            timestamp: "2026-03-13T09:00:01Z".to_string(),
        },
        crate::codex_history::CodexHistoryEntry {
            role: "assistant".to_string(),
            text: "beta".to_string(),
            timestamp: "2026-03-13T09:00:02Z".to_string(),
        },
    ];

    assert_eq!(latest_assistant_text_from_history(&history), Some("beta"));
}

#[test]
fn assistant_history_pages_keep_only_assistant_messages_and_start_latest() {
    let history = vec![
        crate::codex_history::CodexHistoryEntry {
            role: "user".to_string(),
            text: "u1".to_string(),
            timestamp: "2026-03-13T09:00:00Z".to_string(),
        },
        crate::codex_history::CodexHistoryEntry {
            role: "assistant".to_string(),
            text: "a1".to_string(),
            timestamp: "2026-03-13T09:00:01Z".to_string(),
        },
        crate::codex_history::CodexHistoryEntry {
            role: "user".to_string(),
            text: "u2".to_string(),
            timestamp: "2026-03-13T09:00:02Z".to_string(),
        },
        crate::codex_history::CodexHistoryEntry {
            role: "assistant".to_string(),
            text: "a2".to_string(),
            timestamp: "2026-03-13T09:00:03Z".to_string(),
        },
    ];

    let pages = assistant_history_pages(&history);

    assert_eq!(pages.len(), 2);
    assert_eq!(pages[0].text, "a2");
    assert_eq!(pages[1].text, "a1");
}

#[test]
fn history_callback_matches_only_current_session_binding() {
    let session = crate::models::SessionRecord {
        id: 1,
        key: SessionKey::new(1, Some(2)),
        session_title: Some("kombez".to_string()),
        codex_thread_id: Some("019ce672-9445-7612-bc5e-c8243a0d1915".to_string()),
        force_fresh_thread: false,
        updated_at: "2026-03-13T10:00:00Z".to_string(),
        cwd: sample_workspace(),
        model: None,
        reasoning_effort: None,
        session_prompt: None,
        sandbox_mode: "workspace-write".to_string(),
        approval_policy: "never".to_string(),
        search_mode: SearchMode::Disabled,
        add_dirs: vec![],
        busy: false,
        state: crate::models::SessionState::Idle,
        state_detail: None,
        last_status_message_id: None,
        last_activity_at: None,
        last_summary: None,
        pending_turns: 0,
    };

    assert!(history_callback_matches_current_session(
        &session,
        "019ce672-9445-7612-bc5e-c8243a0d1915"
    ));
    assert!(!history_callback_matches_current_session(
        &session,
        "019ce672-9445-7612-bc5e-c8243a0d1916"
    ));
}

#[test]
fn history_callback_rejects_unbound_fresh_session() {
    let session = crate::models::SessionRecord {
        id: 1,
        key: SessionKey::new(1, Some(2)),
        session_title: Some("kombez".to_string()),
        codex_thread_id: None,
        force_fresh_thread: true,
        updated_at: "2026-03-13T10:00:00Z".to_string(),
        cwd: sample_workspace(),
        model: None,
        reasoning_effort: None,
        session_prompt: None,
        sandbox_mode: "workspace-write".to_string(),
        approval_policy: "never".to_string(),
        search_mode: SearchMode::Disabled,
        add_dirs: vec![],
        busy: false,
        state: crate::models::SessionState::Idle,
        state_detail: None,
        last_status_message_id: None,
        last_activity_at: None,
        last_summary: None,
        pending_turns: 0,
    };

    assert!(!history_callback_matches_current_session(
        &session,
        "019ce672-9445-7612-bc5e-c8243a0d1915"
    ));
}

#[test]
fn formats_stale_history_page_for_rebound_topic() {
    let session = crate::models::SessionRecord {
        id: 1,
        key: SessionKey::new(1, Some(2)),
        session_title: Some("kombez".to_string()),
        codex_thread_id: Some("019ce672-9445-7612-bc5e-c8243a0d1916".to_string()),
        force_fresh_thread: false,
        updated_at: "2026-03-13T10:00:00Z".to_string(),
        cwd: sample_workspace(),
        model: None,
        reasoning_effort: None,
        session_prompt: None,
        sandbox_mode: "workspace-write".to_string(),
        approval_policy: "never".to_string(),
        search_mode: SearchMode::Disabled,
        add_dirs: vec![],
        busy: false,
        state: crate::models::SessionState::Idle,
        state_detail: None,
        last_status_message_id: None,
        last_activity_at: None,
        last_summary: None,
        pending_turns: 0,
    };

    let text = format_stale_history_page(&session, "019ce672-9445-7612-bc5e-c8243a0d1915");

    assert!(text.contains("This `/history` view is stale."));
    assert!(text.contains("019ce672"));
    assert!(text.contains("Run `/history` again"));
}

#[test]
fn builds_import_button_for_seed_environment() {
    let session = crate::models::SessionRecord {
        id: 1,
        key: SessionKey::new(-1001234567890, Some(323)),
        session_title: Some("Current topic".to_string()),
        codex_thread_id: Some("019ce152-99e8-7c30-b5b7-166e6aebd550".to_string()),
        force_fresh_thread: false,
        updated_at: "2026-03-13T10:00:00Z".to_string(),
        cwd: sample_workspace(),
        model: None,
        reasoning_effort: None,
        session_prompt: None,
        sandbox_mode: "workspace-write".to_string(),
        approval_policy: "never".to_string(),
        search_mode: SearchMode::Disabled,
        add_dirs: vec![],
        busy: false,
        state: crate::models::SessionState::Idle,
        state_detail: None,
        last_status_message_id: None,
        last_activity_at: None,
        last_summary: None,
        pending_turns: 0,
    };
    let chat = crate::telegram::Chat {
        id: -1001234567890,
        kind: "supergroup".to_string(),
        is_forum: Some(true),
        username: Some("varv_alarms_bot_chat".to_string()),
        title: Some("Codex chat".to_string()),
    };
    let environment = CodexEnvironmentSummary {
        cwd: sample_workspace().join("seeded"),
        name: "Seeded".to_string(),
        latest_thread_id: None,
        updated_at: String::new(),
    };

    let keyboard = environment_dashboard_keyboard(&chat, &session, &[environment], &[]).unwrap();
    let button = &keyboard.inline_keyboard[0][0];

    assert_eq!(button.url, None);
    assert!(
        button
            .callback_data
            .as_deref()
            .unwrap()
            .starts_with("env:cwd:")
    );
}

#[test]
fn builds_model_quick_commands_from_current_and_default() {
    let commands = model_quick_commands(&[], Some("gpt-5.4"), Some("gpt-5"));

    assert_eq!(
        commands,
        vec![
            vec!["/model gpt-5.4".to_string(), "/model gpt-5".to_string()],
            vec!["/model default".to_string()],
        ]
    );
}

#[test]
fn deduplicates_model_quick_commands_when_current_matches_default() {
    let commands = model_quick_commands(&[], Some("gpt-5.4"), Some("gpt-5.4"));

    assert_eq!(
        commands,
        vec![vec![
            "/model gpt-5.4".to_string(),
            "/model default".to_string(),
        ]]
    );
}

#[test]
fn includes_catalog_models_in_model_quick_commands() {
    let commands = model_quick_commands(
        &[
            AvailableModel {
                id: "gpt-5.4".to_string(),
                display_name: Some("gpt-5.4".to_string()),
                description: None,
                is_default: true,
            },
            AvailableModel {
                id: "gpt-5.3-codex".to_string(),
                display_name: Some("gpt-5.3-codex".to_string()),
                description: None,
                is_default: false,
            },
        ],
        Some("gpt-5.4"),
        None,
    );

    assert_eq!(
        commands,
        vec![
            vec![
                "/model gpt-5.4".to_string(),
                "/model gpt-5.3-codex".to_string(),
            ],
            vec!["/model default".to_string()],
        ]
    );
}

#[test]
fn formats_model_help_text_from_catalog() {
    let text = format_model_help_text(
        "gpt-5.4",
        &[
            AvailableModel {
                id: "gpt-5.4".to_string(),
                display_name: Some("gpt-5.4".to_string()),
                description: None,
                is_default: true,
            },
            AvailableModel {
                id: "gpt-5.3-codex".to_string(),
                display_name: Some("gpt-5.3-codex".to_string()),
                description: None,
                is_default: false,
            },
        ],
    );

    assert!(text.contains("Current model: `gpt-5.4`"));
    assert_eq!(text, "Current model: `gpt-5.4`");
}

#[test]
fn builds_clickable_codex_sessions_keyboard() {
    let session = crate::models::SessionRecord {
        id: 1,
        key: SessionKey::new(1, Some(2)),
        session_title: Some("Telecodex".to_string()),
        codex_thread_id: Some("019ce672-9445-7612-bc5e-c8243a0d1915".to_string()),
        force_fresh_thread: false,
        updated_at: "2026-03-13T10:00:00Z".to_string(),
        cwd: sample_workspace(),
        model: None,
        reasoning_effort: None,
        session_prompt: None,
        sandbox_mode: "workspace-write".to_string(),
        approval_policy: "never".to_string(),
        search_mode: SearchMode::Disabled,
        add_dirs: vec![],
        busy: false,
        state: crate::models::SessionState::Idle,
        state_detail: None,
        last_status_message_id: None,
        last_activity_at: None,
        last_summary: None,
        pending_turns: 0,
    };
    let summaries = vec![CodexThreadSummary {
        id: "019ce672-9445-7612-bc5e-c8243a0d1915".to_string(),
        title: "Check OpenAI app server".to_string(),
        cwd: sample_workspace(),
        updated_at: "2026-03-13T10:00:00Z".to_string(),
        source: crate::codex_history::CodexHistorySource::Desktop,
    }];

    let keyboard = codex_sessions_keyboard(&session, &summaries).expect("keyboard");

    assert_eq!(
        keyboard.inline_keyboard[0][0].callback_data,
        Some("cmd:/use 019ce672-9445-7612-bc5e-c8243a0d1915".to_string())
    );
    assert_eq!(
        keyboard.inline_keyboard[1][0].callback_data,
        Some("cmd:/use latest".to_string())
    );
    assert_eq!(
        keyboard.inline_keyboard[1][1].callback_data,
        Some("cmd:/clear".to_string())
    );
}

#[test]
fn formats_recent_codex_history_preview() {
    let preview = format_codex_history_preview_plain(&[
        CodexHistoryEntry {
            role: "user".to_string(),
            text: "weather".to_string(),
            timestamp: "2026-03-13T09:00:01Z".to_string(),
        },
        CodexHistoryEntry {
            role: "assistant".to_string(),
            text: "done".to_string(),
            timestamp: "2026-03-13T09:00:03Z".to_string(),
        },
    ]);

    assert!(preview.contains("**Recent Codex History**"));
    assert!(preview.contains("**You**\n│ weather"));
    assert!(preview.contains("**Codex**\n│ done"));
}

#[test]
fn merges_adjacent_history_entries_with_same_role() {
    let preview = format_codex_history_preview_plain(&[
        CodexHistoryEntry {
            role: "assistant".to_string(),
            text: "first answer".to_string(),
            timestamp: "2026-03-13T09:00:01Z".to_string(),
        },
        CodexHistoryEntry {
            role: "assistant".to_string(),
            text: "second answer".to_string(),
            timestamp: "2026-03-13T09:00:02Z".to_string(),
        },
    ]);

    assert!(preview.contains("│ first answer\n│ second answer"));
    assert_eq!(preview.matches("**Codex**").count(), 1);
}

#[test]
fn formats_recent_codex_history_preview_as_html_blockquotes() {
    let preview = format_codex_history_preview_html(&[
        CodexHistoryEntry {
            role: "user".to_string(),
            text: "weather".to_string(),
            timestamp: "2026-03-13T09:00:01Z".to_string(),
        },
        CodexHistoryEntry {
            role: "assistant".to_string(),
            text: "done".to_string(),
            timestamp: "2026-03-13T09:00:03Z".to_string(),
        },
    ]);

    assert!(preview.contains("<b>Recent Codex History</b>"));
    assert!(preview.contains("<b>You</b>\n<blockquote>weather</blockquote>"));
    assert!(preview.contains("<b>Codex</b>\n<blockquote>done</blockquote>"));
}

#[test]
fn preserves_markdown_inside_history_html_blockquotes() {
    let preview = format_codex_history_preview_html(&[CodexHistoryEntry {
        role: "assistant".to_string(),
        text: "Then yes, **counting** is already in progress and there is `code`.".to_string(),
        timestamp: "2026-03-13T09:00:03Z".to_string(),
    }]);

    assert!(preview.contains(
            "<blockquote>Then yes, <b>counting</b> is already in progress and there is <code>code</code>.</blockquote>"
        ));
}

#[test]
fn formats_codex_history_context_for_runtime() {
    let context = format_codex_history_context(&[
        CodexHistoryEntry {
            role: "user".to_string(),
            text: "I need a script".to_string(),
            timestamp: "2026-03-13T09:00:01Z".to_string(),
        },
        CodexHistoryEntry {
            role: "assistant".to_string(),
            text: "working on the script".to_string(),
            timestamp: "2026-03-13T09:00:03Z".to_string(),
        },
    ]);

    assert!(context.contains("Recent conversation context from the selected Codex session"));
    assert!(context.contains("User: I need a script"));
    assert!(context.contains("Assistant: working on the script"));
}

#[test]
fn keeps_audio_transcript_in_user_prompt_only() {
    let voice_path = sample_voice_file();
    let voice_path_display = voice_path.display().to_string();
    let workspace = sample_turn_workspace();
    let out_dir_display = workspace.out_dir.display().to_string();
    let session = crate::models::SessionRecord {
        id: 1,
        key: SessionKey::new(1, Some(2)),
        session_title: Some("Voice notes".to_string()),
        codex_thread_id: None,
        force_fresh_thread: false,
        updated_at: "2026-03-13T10:00:00Z".to_string(),
        cwd: sample_workspace(),
        model: None,
        reasoning_effort: None,
        session_prompt: None,
        sandbox_mode: "workspace-write".to_string(),
        approval_policy: "never".to_string(),
        search_mode: SearchMode::Disabled,
        add_dirs: vec![],
        busy: false,
        state: crate::models::SessionState::Idle,
        state_detail: None,
        last_status_message_id: None,
        last_activity_at: None,
        last_summary: None,
        pending_turns: 0,
    };
    let request = TurnRequest {
        session_key: session.key,
        from_user_id: 100,
        prompt: "summarize".to_string(),
        runtime_instructions: None,
        attachments: vec![crate::models::LocalAttachment {
            path: voice_path.clone(),
            file_name: "voice.ogg".to_string(),
            mime_type: Some("audio/ogg".to_string()),
            kind: AttachmentKind::Voice,
            transcript: Some(crate::models::AttachmentTranscript {
                engine: "Handy Parakeet".to_string(),
                text: "Hello world".to_string(),
            }),
        }],
        review_mode: None,
        override_search_mode: None,
    };

    let runtime_request = prepare_runtime_request(&session, &request, &workspace);

    assert_eq!(runtime_request.prompt, "summarize\n\nHello world");
    assert!(!runtime_request.prompt.contains(&format!(
        "Attached local files:\n- voice.ogg -> {voice_path_display}"
    )));
    assert!(
        !runtime_request
            .prompt
            .contains("If you generate final deliverable files for the user")
    );
    assert!(!runtime_request.prompt.contains(&voice_path_display));
    let runtime_instructions = runtime_request.runtime_instructions.unwrap();
    assert!(runtime_instructions.contains(&out_dir_display));
    assert!(!runtime_instructions.contains(&voice_path_display));
}

#[test]
fn keeps_non_transcribed_audio_paths_in_user_prompt() {
    let voice_path = sample_voice_file();
    let voice_path_display = voice_path.display().to_string();
    let workspace = sample_turn_workspace();
    let session = crate::models::SessionRecord {
        id: 1,
        key: SessionKey::new(1, Some(2)),
        session_title: Some("Voice notes".to_string()),
        codex_thread_id: None,
        force_fresh_thread: false,
        updated_at: "2026-03-13T10:00:00Z".to_string(),
        cwd: sample_workspace(),
        model: None,
        reasoning_effort: None,
        session_prompt: None,
        sandbox_mode: "workspace-write".to_string(),
        approval_policy: "never".to_string(),
        search_mode: SearchMode::Disabled,
        add_dirs: vec![],
        busy: false,
        state: crate::models::SessionState::Idle,
        state_detail: None,
        last_status_message_id: None,
        last_activity_at: None,
        last_summary: None,
        pending_turns: 0,
    };
    let request = TurnRequest {
        session_key: session.key,
        from_user_id: 100,
        prompt: "Analyze the attached files.".to_string(),
        runtime_instructions: None,
        attachments: vec![crate::models::LocalAttachment {
            path: voice_path.clone(),
            file_name: "voice.ogg".to_string(),
            mime_type: Some("audio/ogg".to_string()),
            kind: AttachmentKind::Voice,
            transcript: None,
        }],
        review_mode: None,
        override_search_mode: None,
    };

    let runtime_request = prepare_runtime_request(&session, &request, &workspace);

    assert!(
        runtime_request
            .prompt
            .contains("Local files for this turn:")
    );
    assert!(
        runtime_request
            .prompt
            .contains(&format!("voice.ogg -> {voice_path_display}"))
    );
    assert!(
        runtime_request
            .runtime_instructions
            .unwrap()
            .contains(&format!(
                "Attached local files:\n- voice.ogg -> {voice_path_display}"
            ))
    );
}

#[test]
fn parses_approval_callback_payloads() {
    assert_eq!(
        parse_approval_callback_data("apr:abc123:a"),
        Some(("abc123".to_string(), CodexApprovalDecision::Accept))
    );
    assert_eq!(
        parse_approval_callback_data("apr:abc123:s"),
        Some((
            "abc123".to_string(),
            CodexApprovalDecision::AcceptForSession
        ))
    );
    assert_eq!(parse_approval_callback_data("cmd:/help"), None);
}

#[test]
fn parses_history_callback_payloads() {
    assert_eq!(
        parse_history_callback_data("his:019ce672-9445-7612-bc5e-c8243a0d1915:7"),
        Some(("019ce672-9445-7612-bc5e-c8243a0d1915".to_string(), 7))
    );
    assert_eq!(parse_history_callback_data("his:bad"), None);
}

#[test]
fn builds_approval_keyboard_buttons() {
    let keyboard = approval_keyboard(
        "token123",
        &[
            CodexApprovalDecision::Accept,
            CodexApprovalDecision::Decline,
            CodexApprovalDecision::Cancel,
        ],
    )
    .expect("approval keyboard");

    assert_eq!(keyboard.inline_keyboard.len(), 2);
    assert_eq!(
        keyboard.inline_keyboard[0][0].callback_data,
        Some("apr:token123:a".to_string())
    );
    assert_eq!(
        keyboard.inline_keyboard[0][1].callback_data,
        Some("apr:token123:d".to_string())
    );
    assert_eq!(
        keyboard.inline_keyboard[1][0].callback_data,
        Some("apr:token123:c".to_string())
    );
}

#[test]
fn builds_history_keyboard_buttons() {
    let keyboard =
        history_keyboard("019ce672-9445-7612-bc5e-c8243a0d1915", 1, 3).expect("history keyboard");

    assert_eq!(keyboard.inline_keyboard.len(), 1);
    assert_eq!(keyboard.inline_keyboard[0].len(), 2);
    assert_eq!(
        keyboard.inline_keyboard[0][0].callback_data,
        Some("his:019ce672-9445-7612-bc5e-c8243a0d1915:0".to_string())
    );
    assert_eq!(
        keyboard.inline_keyboard[0][1].callback_data,
        Some("his:019ce672-9445-7612-bc5e-c8243a0d1915:2".to_string())
    );
}

#[test]
fn history_keyboard_wraps_around() {
    let keyboard =
        history_keyboard("019ce672-9445-7612-bc5e-c8243a0d1915", 0, 3).expect("history keyboard");

    assert_eq!(
        keyboard.inline_keyboard[0][0].callback_data,
        Some("his:019ce672-9445-7612-bc5e-c8243a0d1915:2".to_string())
    );
    assert_eq!(
        keyboard.inline_keyboard[0][1].callback_data,
        Some("his:019ce672-9445-7612-bc5e-c8243a0d1915:1".to_string())
    );
}

#[test]
fn formats_history_page() {
    let entry = crate::codex_history::CodexHistoryEntry {
        role: "assistant".to_string(),
        text: "Done".to_string(),
        timestamp: "2026-03-13T09:00:01Z".to_string(),
    };

    let page = format_history_page(
        "kombez",
        "019ce672-9445-7612-bc5e-c8243a0d1915",
        1,
        3,
        &entry,
    );

    assert!(page.contains("**Session history**"));
    assert!(page.contains("message: `2/3`"));
    assert!(page.contains("role: `assistant`"));
    assert!(page.contains("Done"));
}

#[test]
fn derives_session_title_from_first_non_empty_line() {
    assert_eq!(
        derive_session_title_from_text("\n  Check OpenAI app server   \nsecond line"),
        Some("Check OpenAI app server".to_string())
    );
}

#[test]
fn derives_human_friendly_title_from_runner_dispatch_prompt() {
    let title = derive_session_title_from_text(
        "RUNNER_DISPATCH mode=run_execute project=tmux-codex runner_id=main\nLINEAR_ISSUE=PRO-102\nLINEAR_TITLE=New runner live smoke",
    );
    assert_eq!(title, Some("tmux-codex / PRO-102".to_string()));
}

#[test]
fn summarizes_orx_intake_submission_with_human_readable_ticket_details() {
    let payload = serde_json::json!({
        "intake": {
            "intake_key": "intake-123",
            "status": "pending_approval",
            "plan": {
                "groups": [
                    {
                        "display_name": "tmux-codex",
                        "items": [
                            {
                                "title": "Verify the ORX-backed runner picker and remove leftover task wording",
                                "source_text": "tmux-codex: verify the ORX-backed runner picker and remove leftover task wording",
                                "draft_ticket": {
                                    "title": "Verify the ORX-backed runner picker and remove leftover task wording",
                                    "why": "The runner picker still uses legacy task wording instead of ORX queue language.",
                                    "goal": "Make the tmux-codex runner picker describe ORX queue state clearly.",
                                    "scope": {
                                        "in_scope": [
                                            "Update the picker wording to match ORX queue semantics."
                                        ],
                                        "out_of_scope": [
                                            "Unrelated tmux-codex cleanup."
                                        ]
                                    },
                                    "requirements": [
                                        "Use ORX queue language in the picker.",
                                        "Keep the picker Linear-native."
                                    ],
                                    "acceptance_criteria": [
                                        "Given the current picker wording",
                                        "When the ticket is completed",
                                        "Then the picker describes ORX queue state instead of legacy task counts."
                                    ],
                                    "technical_notes": [
                                        "Routing mode: `explicit-project`",
                                        "Routing rationale: Matched explicit project reference for `tmux-codex`."
                                    ],
                                    "dependencies_risks": [
                                        "Avoid reintroducing local task-count language."
                                    ],
                                    "definition_of_done": [
                                        "The picker wording is updated.",
                                        "The change is verified."
                                    ]
                                },
                                "routing_mode": "explicit-project",
                                "rationale": "Matched explicit project reference for `tmux-codex`.",
                                "needs_clarification": false
                            }
                        ]
                    },
                    {
                        "display_name": "validation-os",
                        "items": [
                            {
                                "title": "Audit ORX bot chat and thread routing for execution updates",
                                "source_text": "validation-os: audit ORX bot chat and thread routing for execution updates",
                                "draft_ticket": {
                                    "title": "Audit ORX bot chat and thread routing for execution updates",
                                    "why": "Execution updates need to land in the correct Telegram lane.",
                                    "goal": "Tighten bot thread routing summaries for validation-os execution updates.",
                                    "scope": {
                                        "in_scope": [
                                            "Audit current routing summaries."
                                        ],
                                        "out_of_scope": [
                                            "Unrelated ORX cleanup."
                                        ]
                                    },
                                    "requirements": [
                                        "Keep updates in the correct execution lane."
                                    ],
                                    "acceptance_criteria": [
                                        "Given the current routing summaries",
                                        "When the ticket is completed",
                                        "Then execution updates land in the right lane."
                                    ],
                                    "technical_notes": [
                                        "Routing mode: `explicit-project`"
                                    ],
                                    "dependencies_risks": [
                                        "Thread drift can confuse operators."
                                    ],
                                    "definition_of_done": [
                                        "The routing summary is clarified."
                                    ]
                                },
                                "routing_mode": "explicit-project",
                                "rationale": "Matched explicit project reference for `validation-os`.",
                                "needs_clarification": false
                            }
                        ]
                    }
                ]
            }
        }
    });

    let summary = summarize_orx_intake_submission(&payload);

    assert!(summary.contains("I would create 2 Linear tickets across 2 projects."));
    assert!(summary.contains("**Ticket 1 · tmux-codex**"));
    assert!(summary.contains("Verify the ORX-backed runner picker and remove leftover task wording"));
    assert!(summary.contains("Problem: The runner picker still uses legacy task wording instead of ORX queue language."));
    assert!(summary.contains("Goal: Make the tmux-codex runner picker describe ORX queue state clearly."));
    assert!(summary.contains("*Scope*"));
    assert!(summary.contains("*Done when*"));
    assert!(summary.contains("Why here: Assigned here because the request explicitly mentioned tmux-codex."));
    assert!(summary.contains("**Ticket 2 · validation-os**"));
    assert!(summary.contains("Review the draft tickets above"));
    assert!(summary.contains("Reject"));
}

#[test]
fn summarizes_orx_intake_submission_calls_out_clarification_items() {
    let payload = serde_json::json!({
        "intake": {
            "intake_key": "intake-clarify",
            "status": "clarification_required",
            "plan": {
                "groups": [
                    {
                        "display_name": "Clarification required",
                        "items": [
                            {
                                "title": "Clarify which project owns the follow-up work",
                                "source_text": "alpha and beta both need follow-up on this same change",
                                "draft_ticket": {
                                    "title": "Clarify which project owns the follow-up work",
                                    "why": "alpha and beta both need follow-up on this same change",
                                    "goal": "Resolve ownership before implementation starts.",
                                    "scope": {
                                        "in_scope": [
                                            "Clarify the owning project."
                                        ],
                                        "out_of_scope": [
                                            "Starting implementation before ownership is clear."
                                        ]
                                    },
                                    "requirements": [
                                        "Resolve the correct owner."
                                    ],
                                    "acceptance_criteria": [
                                        "Given the current ambiguity",
                                        "When clarification is complete",
                                        "Then one project clearly owns the work."
                                    ],
                                    "technical_notes": [
                                        "Routing mode: `clarification-required`"
                                    ],
                                    "dependencies_risks": [
                                        "No default or explicit project match was available for this intake item."
                                    ],
                                    "definition_of_done": [
                                        "The ticket has a single clear owner."
                                    ]
                                },
                                "routing_mode": "clarification-required",
                                "rationale": "No default or explicit project match was available for this intake item.",
                                "needs_clarification": true
                            }
                        ]
                    }
                ]
            }
        }
    });

    let summary = summarize_orx_intake_submission(&payload);

    assert!(summary.contains("**Ticket 1 · Clarification required**"));
    assert!(summary.contains("*Risks*"));
    assert!(summary.contains("Why here: ORX could not confidently map this request to a single project yet."));
    assert!(summary.contains("Clarification is required before ORX can create tickets in Linear."));
}

#[test]
fn format_session_status_normalizes_existing_runner_dispatch_title() {
    let session = crate::models::SessionRecord {
        id: 1,
        key: SessionKey::new(6943633503, None),
        session_title: Some(
            "RUNNER_DISPATCH mode=run_execute project=tmux-codex runner_id=main\nLINEAR_ISSUE=PRO-102"
                .to_string(),
        ),
        codex_thread_id: Some("thread-1".to_string()),
        force_fresh_thread: false,
        updated_at: "2026-04-16T23:20:48Z".to_string(),
        cwd: sample_workspace(),
        model: None,
        reasoning_effort: None,
        session_prompt: None,
        sandbox_mode: "workspace-write".to_string(),
        approval_policy: "never".to_string(),
        search_mode: SearchMode::Disabled,
        add_dirs: vec![],
        busy: false,
        state: crate::models::SessionState::Idle,
        state_detail: None,
        last_status_message_id: None,
        last_activity_at: None,
        last_summary: None,
        pending_turns: 0,
    };
    let chat = crate::telegram::Chat {
        id: 6943633503,
        kind: "private".to_string(),
        is_forum: None,
        username: Some("BlastRadiusBot".to_string()),
        title: None,
    };

    let status = format_session_status(&session, &chat);
    assert!(status.contains("**Current Telegram session:** tmux-codex / PRO-102"));
    assert!(!status.contains("RUNNER_DISPATCH mode=run_execute"));
}

#[test]
fn truncates_long_session_titles() {
    let title = derive_session_title_from_text(
        "Check a very long session title so the Telegram layout stays readable and does not break",
    )
    .expect("title");
    assert!(title.ends_with('…'));
    assert!(title.chars().count() <= 48);
}

#[test]
fn detects_commands_that_use_session_context() {
    assert!(command_uses_session_context(&ParsedInput::Forward(
        "/help".to_string()
    )));
    assert!(command_uses_session_context(&ParsedInput::Bridge(
        BridgeCommand::Copy
    )));
    assert!(command_uses_session_context(&ParsedInput::Bridge(
        BridgeCommand::Kanban
    )));
    assert!(!command_uses_session_context(&ParsedInput::Bridge(
        BridgeCommand::Sessions
    )));
    assert!(!command_uses_session_context(&ParsedInput::Bridge(
        BridgeCommand::History
    )));
    assert!(!command_uses_session_context(&ParsedInput::Bridge(
        BridgeCommand::Status
    )));
    assert!(!command_uses_session_context(&ParsedInput::Bridge(
        BridgeCommand::RestartBot
    )));
}

#[test]
fn detects_commands_that_require_codex_auth() {
    assert!(!parsed_input_requires_codex_auth(&ParsedInput::Bridge(
        BridgeCommand::Status
    )));
    assert!(!parsed_input_requires_codex_auth(&ParsedInput::Bridge(
        BridgeCommand::History
    )));
    assert!(parsed_input_requires_codex_auth(&ParsedInput::Bridge(
        BridgeCommand::Review(crate::models::ReviewRequest {
            base: None,
            commit: None,
            uncommitted: true,
            title: None,
            prompt: None,
        })
    )));
    assert!(!parsed_input_requires_codex_auth(&ParsedInput::Bridge(
        BridgeCommand::Pwd
    )));
    assert!(parsed_input_requires_codex_auth(&ParsedInput::Bridge(
        BridgeCommand::Kanban
    )));
    assert!(!parsed_input_requires_codex_auth(&ParsedInput::Bridge(
        BridgeCommand::Login
    )));
}

#[tokio::test]
async fn upload_failure_marks_turn_failed_and_cleanup_still_runs() {
    let tmp = NamedTempFile::new().unwrap();
    let store = Store::open(tmp.path(), &[100], &sample_defaults()).unwrap();
    let session = store
        .ensure_session(SessionKey::new(1, Some(2)), 100, &sample_defaults())
        .unwrap();
    let turn_id = store
        .record_turn_started(session.id, &sample_turn_request(session.key))
        .unwrap();

    let attachment_dir = tempfile::tempdir().unwrap();
    let attachment_path = attachment_dir.path().join("input.txt");
    std::fs::write(&attachment_path, "payload").unwrap();
    let turn_root = attachment_dir.path().join("turn-root");
    std::fs::create_dir_all(&turn_root).unwrap();

    let attachment = LocalAttachment {
        path: attachment_path.clone(),
        file_name: "input.txt".to_string(),
        mime_type: Some("text/plain".to_string()),
        kind: AttachmentKind::Text,
        transcript: None,
    };
    let summary = crate::codex::RunSummary {
        codex_thread_id: Some("thread-123".to_string()),
        assistant_text: "answer".to_string(),
        stderr_text: String::new(),
    };
    let failure_messages = Arc::new(StdMutex::new(Vec::<String>::new()));
    let failure_messages_sink = failure_messages.clone();

    let result = finalize_foreground_turn(
        ForegroundTurnSuccess {
            store: &store,
            session: &session,
            turn_id,
            review_mode: false,
            summary: &summary,
        },
        || async { Err(anyhow!("upload failed")) },
        || async { Ok(()) },
        move |message| {
            let failure_messages_sink = failure_messages_sink.clone();
            async move {
                failure_messages_sink.lock().unwrap().push(message);
                Ok(())
            }
        },
    )
    .await;
    let result = finish_turn_cleanup(&[attachment], &turn_root, result);

    assert!(result.is_err());
    assert_eq!(
        store.turn_status(turn_id).unwrap().as_deref(),
        Some("failed")
    );
    assert!(!attachment_path.exists());
    assert!(!turn_root.exists());
    assert!(
        failure_messages
            .lock()
            .unwrap()
            .iter()
            .any(|message| message.contains("upload failed"))
    );
}

#[tokio::test]
async fn resume_recovered_sessions_starts_workers_for_interrupted_sessions() {
    let tmp = NamedTempFile::new().unwrap();
    let store = Store::open(tmp.path(), &[100], &sample_defaults()).unwrap();
    let session = store
        .ensure_session(SessionKey::new(42, Some(7)), 100, &sample_defaults())
        .unwrap();
    let request = sample_turn_request(session.key);

    store.set_session_busy(session.key, true).unwrap();
    store.enqueue_pending_turn(session.id, &request, "private").unwrap();
    let claimed = store.claim_next_pending_turn(session.key).unwrap().unwrap();
    assert_eq!(claimed.request.prompt, "hello");

    let recovered_sessions = store.recover_interrupted_work().unwrap();
    assert_eq!(recovered_sessions, vec![session.key]);

    let app = App {
        shared: Arc::new(AppShared {
            config: sample_config(tmp.path().to_path_buf()),
            store,
            telegram: crate::telegram::TelegramClient::new(
                "test-token".to_string(),
                "http://127.0.0.1:9".to_string(),
            ),
            codex: crate::codex::CodexRunner::new(PathBuf::from("/usr/bin/true")),
            orx: None,
            bot_username: Some("testbot".to_string()),
            bot_identity: "testbot".to_string(),
            service_user_id: 100,
            handy_model_dir: None,
            session_defaults: sample_defaults(),
            limits_cache: Mutex::new(None),
            history_page_cache: Mutex::new(HistoryPageCache::default()),
            pending_approvals: Mutex::new(HashMap::new()),
            pending_intake_approvals: Mutex::new(HashMap::new()),
            pending_codex_login: Mutex::new(None),
            codex_login_backoff_until: Mutex::new(None),
            recovered_sessions,
        }),
        workers: Arc::new(Mutex::new(HashMap::new())),
    };

    app.resume_recovered_sessions().await.unwrap();

    let workers = app.workers.lock().await;
    assert!(workers.contains_key(&session.key));
}
