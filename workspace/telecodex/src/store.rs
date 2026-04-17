use std::{
    io,
    path::{Path, PathBuf},
    sync::Mutex,
};

use anyhow::{Context, Result, anyhow};
use chrono::Utc;
use rusqlite::{Connection, OptionalExtension, params};

use crate::{
    config::{CodexConfig, SearchMode},
    models::{
        PendingTurnRecord, PendingTurnStatus, SessionKey, SessionRecord, SessionState,
        TelegramTranscriptDirection, TelegramTranscriptEntry, TurnRequest, UserRecord, UserRole,
    },
};

pub struct Store {
    conn: Mutex<Connection>,
}

#[derive(Debug, Clone)]
pub struct SessionDefaults {
    pub cwd: PathBuf,
    pub model: Option<String>,
    pub reasoning_effort: Option<String>,
    pub session_prompt: Option<String>,
    pub sandbox_mode: String,
    pub approval_policy: String,
    pub search_mode: SearchMode,
    pub add_dirs: Vec<PathBuf>,
}

impl From<&CodexConfig> for SessionDefaults {
    fn from(value: &CodexConfig) -> Self {
        Self {
            cwd: value.default_cwd.clone(),
            model: value.default_model.clone(),
            reasoning_effort: value.default_reasoning_effort.clone(),
            session_prompt: None,
            sandbox_mode: value.default_sandbox.clone(),
            approval_policy: value.default_approval.clone(),
            search_mode: value.default_search_mode,
            add_dirs: value.default_add_dirs.clone(),
        }
    }
}

impl Store {
    pub fn open(db_path: &Path, admin_ids: &[i64], defaults: &SessionDefaults) -> Result<Self> {
        let conn = Connection::open(db_path)
            .with_context(|| format!("failed to open sqlite database {}", db_path.display()))?;
        conn.pragma_update(None, "journal_mode", "WAL")?;
        conn.pragma_update(None, "foreign_keys", "ON")?;
        let store = Self {
            conn: Mutex::new(conn),
        };
        store.init_schema()?;
        store.seed_admins(admin_ids)?;
        store.audit(
            None,
            "startup",
            serde_json::json!({ "admins_seeded": admin_ids }),
        )?;
        if admin_ids.is_empty() {
            tracing::warn!(
                "startup_admin_ids is empty; the bot will deny everyone until an admin is inserted manually"
            );
        }
        if defaults.cwd.as_os_str().is_empty() {
            return Err(anyhow!("default cwd is empty"));
        }
        Ok(store)
    }

    pub fn last_update_id(&self) -> Result<Option<i64>> {
        let conn = self.conn.lock().expect("store mutex poisoned");
        conn.query_row(
            "SELECT value FROM bot_state WHERE key = 'last_update_id'",
            [],
            |row| row.get::<_, String>(0),
        )
        .optional()?
        .map(|value| value.parse::<i64>().map_err(|error| anyhow!(error)))
        .transpose()
    }

    pub fn save_last_update_id(&self, update_id: i64) -> Result<()> {
        let conn = self.conn.lock().expect("store mutex poisoned");
        conn.execute(
            "INSERT INTO bot_state(key, value) VALUES ('last_update_id', ?1)
             ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            params![update_id.to_string()],
        )?;
        Ok(())
    }

    pub fn bot_state_value(&self, key: &str) -> Result<Option<String>> {
        let conn = self.conn.lock().expect("store mutex poisoned");
        conn.query_row(
            "SELECT value FROM bot_state WHERE key = ?1",
            params![key],
            |row| row.get::<_, String>(0),
        )
        .optional()
        .map_err(Into::into)
    }

    pub fn save_bot_state(&self, key: &str, value: &str) -> Result<()> {
        let conn = self.conn.lock().expect("store mutex poisoned");
        conn.execute(
            "INSERT INTO bot_state(key, value) VALUES (?1, ?2)
             ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            params![key, value],
        )?;
        Ok(())
    }

    pub fn get_user(&self, tg_user_id: i64) -> Result<Option<UserRecord>> {
        let conn = self.conn.lock().expect("store mutex poisoned");
        conn.query_row(
            "SELECT tg_user_id, role, allowed FROM users WHERE tg_user_id = ?1",
            params![tg_user_id],
            |row| {
                let role: String = row.get(1)?;
                Ok(UserRecord {
                    tg_user_id: row.get(0)?,
                    role: UserRole::try_from(role.as_str()).map_err(|error| {
                        rusqlite::Error::FromSqlConversionFailure(
                            1,
                            rusqlite::types::Type::Text,
                            Box::new(io::Error::new(
                                io::ErrorKind::InvalidData,
                                error.to_string(),
                            )),
                        )
                    })?,
                    allowed: row.get::<_, i64>(2)? != 0,
                })
            },
        )
        .optional()
        .map_err(Into::into)
    }

    pub fn upsert_user(&self, tg_user_id: i64, role: UserRole, allowed: bool) -> Result<()> {
        let now = now_string();
        let conn = self.conn.lock().expect("store mutex poisoned");
        conn.execute(
            "INSERT INTO users(tg_user_id, role, allowed, created_at, updated_at)
             VALUES (?1, ?2, ?3, ?4, ?4)
             ON CONFLICT(tg_user_id) DO UPDATE
             SET role = excluded.role, allowed = excluded.allowed, updated_at = excluded.updated_at",
            params![tg_user_id, role.as_str(), bool_to_i64(allowed), now],
        )?;
        Ok(())
    }

    pub fn ensure_session(
        &self,
        key: SessionKey,
        creator_user_id: i64,
        defaults: &SessionDefaults,
    ) -> Result<SessionRecord> {
        if let Some(existing) = self.get_session(key)? {
            return Ok(existing);
        }

        let now = now_string();
        let add_dirs = serde_json::to_string(&path_vec_to_strings(&defaults.add_dirs))?;
        let conn = self.conn.lock().expect("store mutex poisoned");
        conn.execute(
            "INSERT INTO sessions(
                chat_id, thread_id, session_title, codex_thread_id, force_fresh_thread, cwd, model, reasoning_effort, session_prompt, sandbox_mode, approval_policy,
                search_mode, add_dirs_json, creator_user_id, busy, last_assistant_text, state, state_detail, last_status_message_id, last_activity_at, last_summary, created_at, updated_at
             ) VALUES (?1, ?2, NULL, NULL, 0, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, 0, NULL, 'idle', NULL, NULL, ?12, NULL, ?12, ?12)",
            params![
                key.chat_id,
                key.thread_id,
                defaults.cwd.display().to_string(),
                defaults.model,
                defaults.reasoning_effort,
                defaults.session_prompt,
                defaults.sandbox_mode,
                defaults.approval_policy,
                defaults.search_mode.as_codex_value(),
                add_dirs,
                creator_user_id,
                now
            ],
        )?;
        drop(conn);
        self.get_session(key)?
            .ok_or_else(|| anyhow!("failed to reload newly created session"))
    }

    pub fn get_session(&self, key: SessionKey) -> Result<Option<SessionRecord>> {
        let conn = self.conn.lock().expect("store mutex poisoned");
        conn.query_row(
            "SELECT id, chat_id, thread_id, session_title, codex_thread_id, force_fresh_thread, cwd, model, reasoning_effort, session_prompt, sandbox_mode, approval_policy,
                    search_mode, add_dirs_json, creator_user_id, busy, last_assistant_text, updated_at, state, state_detail, last_status_message_id, last_activity_at, last_summary,
                    (SELECT COUNT(*) FROM pending_turns WHERE session_id = sessions.id)
             FROM sessions
             WHERE chat_id = ?1 AND thread_id = ?2",
            params![key.chat_id, key.thread_id],
            map_session,
        )
        .optional()
        .map_err(Into::into)
    }

    pub fn list_chat_sessions(&self, chat_id: i64) -> Result<Vec<SessionRecord>> {
        let conn = self.conn.lock().expect("store mutex poisoned");
        let mut stmt = conn.prepare(
            "SELECT id, chat_id, thread_id, session_title, codex_thread_id, force_fresh_thread, cwd, model, reasoning_effort, session_prompt, sandbox_mode, approval_policy,
                    search_mode, add_dirs_json, creator_user_id, busy, last_assistant_text, updated_at, state, state_detail, last_status_message_id, last_activity_at, last_summary,
                    (SELECT COUNT(*) FROM pending_turns WHERE session_id = sessions.id)
             FROM sessions
             WHERE chat_id = ?1
             ORDER BY updated_at DESC, id DESC",
        )?;
        let rows = stmt.query_map(params![chat_id], map_session)?;
        rows.collect::<std::result::Result<Vec<_>, _>>()
            .map_err(Into::into)
    }

    pub fn list_all_sessions(&self) -> Result<Vec<SessionRecord>> {
        let conn = self.conn.lock().expect("store mutex poisoned");
        let mut stmt = conn.prepare(
            "SELECT id, chat_id, thread_id, session_title, codex_thread_id, force_fresh_thread, cwd, model, reasoning_effort, session_prompt, sandbox_mode, approval_policy,
                    search_mode, add_dirs_json, creator_user_id, busy, last_assistant_text, updated_at, state, state_detail, last_status_message_id, last_activity_at, last_summary,
                    (SELECT COUNT(*) FROM pending_turns WHERE session_id = sessions.id)
             FROM sessions
             ORDER BY updated_at DESC, id DESC",
        )?;
        let rows = stmt.query_map([], map_session)?;
        rows.collect::<std::result::Result<Vec<_>, _>>()
            .map_err(Into::into)
    }

    pub fn delete_session(&self, key: SessionKey) -> Result<()> {
        let conn = self.conn.lock().expect("store mutex poisoned");
        conn.execute(
            "DELETE FROM sessions WHERE chat_id = ?1 AND thread_id = ?2",
            params![key.chat_id, key.thread_id],
        )?;
        Ok(())
    }

    pub fn set_session_busy(&self, key: SessionKey, busy: bool) -> Result<()> {
        self.update_session_field(
            key,
            "UPDATE sessions SET busy = ?3, updated_at = ?4 WHERE chat_id = ?1 AND thread_id = ?2",
            params![key.chat_id, key.thread_id, bool_to_i64(busy), now_string()],
        )
    }

    pub fn set_session_codex_thread(&self, key: SessionKey, codex_thread_id: &str) -> Result<()> {
        self.update_session_field(
            key,
            "UPDATE sessions SET codex_thread_id = ?3, force_fresh_thread = 0, updated_at = ?4 WHERE chat_id = ?1 AND thread_id = ?2",
            params![key.chat_id, key.thread_id, codex_thread_id, now_string()],
        )
    }

    pub fn set_session_title(&self, key: SessionKey, session_title: Option<&str>) -> Result<()> {
        self.update_session_field(
            key,
            "UPDATE sessions SET session_title = ?3, updated_at = ?4 WHERE chat_id = ?1 AND thread_id = ?2",
            params![key.chat_id, key.thread_id, session_title, now_string()],
        )
    }

    pub fn apply_session_template(&self, key: SessionKey, template: &SessionRecord) -> Result<()> {
        let add_dirs = serde_json::to_string(&path_vec_to_strings(&template.add_dirs))?;
        self.update_session_field(
            key,
            "UPDATE sessions
             SET session_title = ?3,
                 codex_thread_id = ?4,
                 force_fresh_thread = ?5,
                 cwd = ?6,
                 model = ?7,
                 reasoning_effort = ?8,
                 session_prompt = ?9,
                 sandbox_mode = ?10,
                 approval_policy = ?11,
                 search_mode = ?12,
                 add_dirs_json = ?13,
                 updated_at = ?14
             WHERE chat_id = ?1 AND thread_id = ?2",
            params![
                key.chat_id,
                key.thread_id,
                template.session_title,
                template.codex_thread_id,
                bool_to_i64(template.force_fresh_thread),
                template.cwd.display().to_string(),
                template.model,
                template.reasoning_effort,
                template.session_prompt,
                template.sandbox_mode,
                template.approval_policy,
                template.search_mode.as_codex_value(),
                add_dirs,
                now_string(),
            ],
        )
    }

    pub fn clear_session_conversation(&self, key: SessionKey) -> Result<()> {
        self.update_session_field(
            key,
            "UPDATE sessions
             SET codex_thread_id = NULL, force_fresh_thread = 1, busy = 0, last_assistant_text = NULL, state = 'idle', state_detail = NULL, last_summary = NULL, updated_at = ?3
             WHERE chat_id = ?1 AND thread_id = ?2",
            params![key.chat_id, key.thread_id, now_string()],
        )
    }

    pub fn set_session_cwd(&self, key: SessionKey, cwd: &Path) -> Result<()> {
        self.update_session_field(
            key,
            "UPDATE sessions SET cwd = ?3, updated_at = ?4 WHERE chat_id = ?1 AND thread_id = ?2",
            params![
                key.chat_id,
                key.thread_id,
                cwd.display().to_string(),
                now_string()
            ],
        )
    }

    pub fn set_session_model(&self, key: SessionKey, model: Option<&str>) -> Result<()> {
        self.update_session_field(
            key,
            "UPDATE sessions SET model = ?3, updated_at = ?4 WHERE chat_id = ?1 AND thread_id = ?2",
            params![key.chat_id, key.thread_id, model, now_string()],
        )
    }

    pub fn set_session_reasoning_effort(
        &self,
        key: SessionKey,
        reasoning_effort: Option<&str>,
    ) -> Result<()> {
        self.update_session_field(
            key,
            "UPDATE sessions SET reasoning_effort = ?3, updated_at = ?4 WHERE chat_id = ?1 AND thread_id = ?2",
            params![key.chat_id, key.thread_id, reasoning_effort, now_string()],
        )
    }

    pub fn set_session_prompt(&self, key: SessionKey, session_prompt: Option<&str>) -> Result<()> {
        self.update_session_field(
            key,
            "UPDATE sessions SET session_prompt = ?3, updated_at = ?4 WHERE chat_id = ?1 AND thread_id = ?2",
            params![key.chat_id, key.thread_id, session_prompt, now_string()],
        )
    }

    pub fn set_session_approval(&self, key: SessionKey, approval: &str) -> Result<()> {
        self.update_session_field(
            key,
            "UPDATE sessions SET approval_policy = ?3, updated_at = ?4 WHERE chat_id = ?1 AND thread_id = ?2",
            params![key.chat_id, key.thread_id, approval, now_string()],
        )
    }

    pub fn set_session_sandbox(&self, key: SessionKey, sandbox: &str) -> Result<()> {
        self.update_session_field(
            key,
            "UPDATE sessions SET sandbox_mode = ?3, updated_at = ?4 WHERE chat_id = ?1 AND thread_id = ?2",
            params![key.chat_id, key.thread_id, sandbox, now_string()],
        )
    }

    pub fn set_session_search_mode(&self, key: SessionKey, mode: SearchMode) -> Result<()> {
        self.update_session_field(
            key,
            "UPDATE sessions SET search_mode = ?3, updated_at = ?4 WHERE chat_id = ?1 AND thread_id = ?2",
            params![key.chat_id, key.thread_id, mode.as_codex_value(), now_string()],
        )
    }

    pub fn add_session_dir(&self, key: SessionKey, path: &Path) -> Result<Vec<PathBuf>> {
        let mut session = self
            .get_session(key)?
            .ok_or_else(|| anyhow!("session not found"))?;
        if !session.add_dirs.iter().any(|existing| existing == path) {
            session.add_dirs.push(path.to_path_buf());
        }
        let json = serde_json::to_string(&path_vec_to_strings(&session.add_dirs))?;
        self.update_session_field(
            key,
            "UPDATE sessions SET add_dirs_json = ?3, updated_at = ?4 WHERE chat_id = ?1 AND thread_id = ?2",
            params![key.chat_id, key.thread_id, json, now_string()],
        )?;
        Ok(session.add_dirs)
    }

    pub fn set_session_runtime_state(
        &self,
        key: SessionKey,
        state: SessionState,
        detail: Option<&str>,
        summary: Option<&str>,
    ) -> Result<()> {
        self.update_session_field(
            key,
            "UPDATE sessions
             SET state = ?3,
                 state_detail = ?4,
                 last_summary = COALESCE(?5, last_summary),
                 last_activity_at = ?6,
                 updated_at = ?6
             WHERE chat_id = ?1 AND thread_id = ?2",
            params![
                key.chat_id,
                key.thread_id,
                state.as_str(),
                detail,
                summary,
                now_string()
            ],
        )
    }

    pub fn set_session_last_status_message(
        &self,
        key: SessionKey,
        message_id: Option<i64>,
    ) -> Result<()> {
        self.update_session_field(
            key,
            "UPDATE sessions
             SET last_status_message_id = ?3, last_activity_at = ?4, updated_at = ?4
             WHERE chat_id = ?1 AND thread_id = ?2",
            params![key.chat_id, key.thread_id, message_id, now_string()],
        )
    }

    pub fn enqueue_pending_turn(
        &self,
        session_id: i64,
        request: &TurnRequest,
        chat_kind: &str,
    ) -> Result<i64> {
        let request_json = serde_json::to_string(request)?;
        let now = now_string();
        let conn = self.conn.lock().expect("store mutex poisoned");
        conn.execute(
            "INSERT INTO pending_turns(session_id, request_json, chat_kind, status, enqueued_at, started_at)
             VALUES (?1, ?2, ?3, ?4, ?5, NULL)",
            params![session_id, request_json, chat_kind, PendingTurnStatus::Queued.as_str(), now],
        )?;
        Ok(conn.last_insert_rowid())
    }

    pub fn claim_next_pending_turn(&self, key: SessionKey) -> Result<Option<PendingTurnRecord>> {
        let mut conn = self.conn.lock().expect("store mutex poisoned");
        let tx = conn.transaction()?;
        let row = tx
            .query_row(
                "SELECT p.id, p.request_json, p.chat_kind
                 FROM pending_turns p
                 JOIN sessions s ON s.id = p.session_id
                 WHERE s.chat_id = ?1 AND s.thread_id = ?2 AND p.status = 'queued'
                 ORDER BY p.id ASC
                 LIMIT 1",
                params![key.chat_id, key.thread_id],
                |row| {
                    Ok((
                        row.get::<_, i64>(0)?,
                        row.get::<_, String>(1)?,
                        row.get::<_, String>(2)?,
                    ))
                },
            )
            .optional()?;
        let Some((id, request_json, chat_kind)) = row else {
            tx.commit()?;
            return Ok(None);
        };
        tx.execute(
            "UPDATE pending_turns SET status = ?2, started_at = ?3 WHERE id = ?1",
            params![id, PendingTurnStatus::Running.as_str(), now_string()],
        )?;
        tx.commit()?;
        let request = serde_json::from_str(&request_json)?;
        Ok(Some(PendingTurnRecord {
            id,
            request,
            chat_kind,
        }))
    }

    pub fn complete_pending_turn(&self, pending_turn_id: i64) -> Result<()> {
        let conn = self.conn.lock().expect("store mutex poisoned");
        conn.execute(
            "DELETE FROM pending_turns WHERE id = ?1",
            params![pending_turn_id],
        )?;
        Ok(())
    }

    pub fn recover_interrupted_work(&self) -> Result<Vec<SessionKey>> {
        let conn = self.conn.lock().expect("store mutex poisoned");
        let now = now_string();
        let mut keys = Vec::new();
        let mut stmt = conn.prepare(
            "SELECT DISTINCT s.chat_id, s.thread_id
             FROM sessions s
             LEFT JOIN pending_turns p ON p.session_id = s.id
             WHERE s.busy = 1 OR p.status = 'running'",
        )?;
        let rows = stmt.query_map([], |row| {
            Ok(SessionKey {
                chat_id: row.get(0)?,
                thread_id: row.get(1)?,
            })
        })?;
        for row in rows {
            keys.push(row?);
        }
        conn.execute(
            "UPDATE pending_turns
             SET status = 'queued', started_at = NULL
             WHERE status = 'running'",
            [],
        )?;
        for key in &keys {
            conn.execute(
                "UPDATE sessions
                 SET busy = 0,
                     state = 'interrupted',
                     state_detail = 'Recovered after a process restart before the previous turn finished.',
                     last_summary = COALESCE(last_summary, 'Recovered interrupted work after restart.'),
                     last_activity_at = ?3,
                     updated_at = ?3
                 WHERE chat_id = ?1 AND thread_id = ?2",
                params![key.chat_id, key.thread_id, now],
            )?;
        }
        Ok(keys)
    }

    pub fn record_turn_started(&self, session_id: i64, request: &TurnRequest) -> Result<i64> {
        let review_json = serde_json::to_string(&request.review_mode)?;
        let now = now_string();
        let conn = self.conn.lock().expect("store mutex poisoned");
        conn.execute(
            "INSERT INTO turns(session_id, from_user_id, prompt, review_json, status, started_at, completed_at, assistant_text)
             VALUES (?1, ?2, ?3, ?4, 'running', ?5, NULL, NULL)",
            params![session_id, request.from_user_id, request.prompt, review_json, now],
        )?;
        Ok(conn.last_insert_rowid())
    }

    pub fn record_turn_finished(
        &self,
        turn_id: i64,
        status: &str,
        assistant_text: Option<&str>,
    ) -> Result<()> {
        let now = now_string();
        let conn = self.conn.lock().expect("store mutex poisoned");
        conn.execute(
            "UPDATE turns
             SET status = ?2, assistant_text = COALESCE(?3, assistant_text), completed_at = ?4
             WHERE id = ?1",
            params![turn_id, status, assistant_text, now],
        )?;
        Ok(())
    }

    pub fn set_last_assistant_text(&self, key: SessionKey, text: Option<&str>) -> Result<()> {
        self.update_session_field(
            key,
            "UPDATE sessions SET last_assistant_text = ?3, updated_at = ?4 WHERE chat_id = ?1 AND thread_id = ?2",
            params![key.chat_id, key.thread_id, text, now_string()],
        )
    }

    pub fn last_assistant_text(&self, key: SessionKey) -> Result<Option<String>> {
        let conn = self.conn.lock().expect("store mutex poisoned");
        conn.query_row(
            "SELECT last_assistant_text FROM sessions WHERE chat_id = ?1 AND thread_id = ?2",
            params![key.chat_id, key.thread_id],
            |row| row.get::<_, Option<String>>(0),
        )
        .optional()
        .map(|value| value.flatten())
        .map_err(Into::into)
    }

    pub fn audit(
        &self,
        actor_user_id: Option<i64>,
        action: &str,
        details: serde_json::Value,
    ) -> Result<()> {
        let conn = self.conn.lock().expect("store mutex poisoned");
        conn.execute(
            "INSERT INTO audit_log(actor_user_id, action, details_json, created_at)
             VALUES (?1, ?2, ?3, ?4)",
            params![actor_user_id, action, details.to_string(), now_string()],
        )?;
        Ok(())
    }

    pub fn append_telegram_transcript_entry(
        &self,
        key: SessionKey,
        telegram_message_id: Option<i64>,
        direction: TelegramTranscriptDirection,
        text: &str,
    ) -> Result<()> {
        let conn = self.conn.lock().expect("store mutex poisoned");
        conn.execute(
            "INSERT INTO telegram_transcript(
                chat_id,
                thread_id,
                telegram_message_id,
                direction,
                text,
                created_at
             ) VALUES (?1, ?2, ?3, ?4, ?5, ?6)",
            params![
                key.chat_id,
                key.thread_id,
                telegram_message_id,
                direction.as_str(),
                text,
                now_string()
            ],
        )?;
        Ok(())
    }

    pub fn recent_telegram_transcript(
        &self,
        key: SessionKey,
        limit: usize,
    ) -> Result<Vec<TelegramTranscriptEntry>> {
        let conn = self.conn.lock().expect("store mutex poisoned");
        let mut stmt = conn.prepare(
            "SELECT telegram_message_id, direction, text, created_at
             FROM telegram_transcript
             WHERE chat_id = ?1 AND thread_id = ?2
             ORDER BY id DESC
             LIMIT ?3",
        )?;
        let rows = stmt.query_map(params![key.chat_id, key.thread_id, limit as i64], |row| {
            let direction: String = row.get(1)?;
            Ok(TelegramTranscriptEntry {
                telegram_message_id: row.get(0)?,
                direction: TelegramTranscriptDirection::from_db(&direction),
                text: row.get(2)?,
                created_at: row.get(3)?,
            })
        })?;
        let mut entries = rows.collect::<std::result::Result<Vec<_>, _>>()?;
        entries.reverse();
        Ok(entries)
    }

    fn init_schema(&self) -> Result<()> {
        let conn = self.conn.lock().expect("store mutex poisoned");
        conn.execute_batch(
            "
            CREATE TABLE IF NOT EXISTS users(
                tg_user_id INTEGER PRIMARY KEY,
                role TEXT NOT NULL,
                allowed INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS sessions(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                thread_id INTEGER NOT NULL,
                session_title TEXT,
                codex_thread_id TEXT,
                force_fresh_thread INTEGER NOT NULL DEFAULT 0,
                cwd TEXT NOT NULL,
                model TEXT,
                reasoning_effort TEXT,
                session_prompt TEXT,
                sandbox_mode TEXT NOT NULL,
                approval_policy TEXT NOT NULL,
                search_mode TEXT NOT NULL,
                add_dirs_json TEXT NOT NULL,
                creator_user_id INTEGER NOT NULL,
                busy INTEGER NOT NULL DEFAULT 0,
                last_assistant_text TEXT,
                state TEXT NOT NULL DEFAULT 'idle',
                state_detail TEXT,
                last_status_message_id INTEGER,
                last_activity_at TEXT,
                last_summary TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(chat_id, thread_id)
            );

            CREATE TABLE IF NOT EXISTS turns(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id INTEGER NOT NULL,
                from_user_id INTEGER NOT NULL,
                prompt TEXT NOT NULL,
                review_json TEXT,
                status TEXT NOT NULL,
                assistant_text TEXT,
                started_at TEXT NOT NULL,
                completed_at TEXT,
                FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS pending_turns(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id INTEGER NOT NULL,
                request_json TEXT NOT NULL,
                chat_kind TEXT NOT NULL,
                status TEXT NOT NULL,
                enqueued_at TEXT NOT NULL,
                started_at TEXT,
                FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS bot_state(
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS audit_log(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                actor_user_id INTEGER,
                action TEXT NOT NULL,
                details_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS telegram_transcript(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                thread_id INTEGER NOT NULL,
                telegram_message_id INTEGER,
                direction TEXT NOT NULL,
                text TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_telegram_transcript_chat_thread_id
            ON telegram_transcript(chat_id, thread_id, id DESC);
            ",
        )?;
        add_column_if_missing(&conn, "sessions", "session_title", "TEXT")?;
        add_column_if_missing(&conn, "sessions", "reasoning_effort", "TEXT")?;
        add_column_if_missing(&conn, "sessions", "session_prompt", "TEXT")?;
        add_column_if_missing(
            &conn,
            "sessions",
            "force_fresh_thread",
            "INTEGER NOT NULL DEFAULT 0",
        )?;
        add_column_if_missing(&conn, "sessions", "state", "TEXT NOT NULL DEFAULT 'idle'")?;
        add_column_if_missing(&conn, "sessions", "state_detail", "TEXT")?;
        add_column_if_missing(&conn, "sessions", "last_status_message_id", "INTEGER")?;
        add_column_if_missing(&conn, "sessions", "last_activity_at", "TEXT")?;
        add_column_if_missing(&conn, "sessions", "last_summary", "TEXT")?;
        Ok(())
    }

    fn seed_admins(&self, admin_ids: &[i64]) -> Result<()> {
        for admin_id in admin_ids {
            self.upsert_user(*admin_id, UserRole::Admin, true)?;
        }
        Ok(())
    }

    fn update_session_field<P>(&self, key: SessionKey, sql: &str, params: P) -> Result<()>
    where
        P: rusqlite::Params,
    {
        let conn = self.conn.lock().expect("store mutex poisoned");
        let changed = conn.execute(sql, params)?;
        if changed == 0 {
            return Err(anyhow!(
                "session not found for chat_id={} thread_id={}",
                key.chat_id,
                key.thread_id
            ));
        }
        Ok(())
    }

    #[cfg(test)]
    pub(crate) fn turn_status(&self, turn_id: i64) -> Result<Option<String>> {
        let conn = self.conn.lock().expect("store mutex poisoned");
        conn.query_row(
            "SELECT status FROM turns WHERE id = ?1",
            params![turn_id],
            |row| row.get::<_, String>(0),
        )
        .optional()
        .map_err(Into::into)
    }
}

fn map_session(row: &rusqlite::Row<'_>) -> rusqlite::Result<SessionRecord> {
    let add_dirs_json: String = row.get(13)?;
    let add_dirs_strings: Vec<String> = serde_json::from_str(&add_dirs_json).map_err(|error| {
        rusqlite::Error::FromSqlConversionFailure(13, rusqlite::types::Type::Text, Box::new(error))
    })?;
    let search_mode: String = row.get(12)?;
    let state: String = row.get(18)?;
    Ok(SessionRecord {
        id: row.get(0)?,
        key: SessionKey {
            chat_id: row.get(1)?,
            thread_id: row.get(2)?,
        },
        session_title: row.get(3)?,
        codex_thread_id: row.get(4)?,
        force_fresh_thread: row.get::<_, i64>(5)? != 0,
        updated_at: row.get(17)?,
        cwd: normalize_path(PathBuf::from(row.get::<_, String>(6)?)),
        model: row.get(7)?,
        reasoning_effort: row.get(8)?,
        session_prompt: row.get(9)?,
        sandbox_mode: row.get(10)?,
        approval_policy: row.get(11)?,
        search_mode: match search_mode.as_str() {
            "live" => SearchMode::Live,
            "cached" => SearchMode::Cached,
            _ => SearchMode::Disabled,
        },
        add_dirs: add_dirs_strings
            .into_iter()
            .map(PathBuf::from)
            .map(normalize_path)
            .collect(),
        busy: row.get::<_, i64>(15)? != 0,
        state: SessionState::from_db(&state),
        state_detail: row.get(19)?,
        last_status_message_id: row.get(20)?,
        last_activity_at: row.get(21)?,
        last_summary: row.get(22)?,
        pending_turns: row.get::<_, i64>(23)? as usize,
    })
}

fn add_column_if_missing(
    conn: &Connection,
    table: &str,
    column: &str,
    definition: &str,
) -> Result<()> {
    let pragma = format!("PRAGMA table_info({table})");
    let mut stmt = conn.prepare(&pragma)?;
    let columns = stmt
        .query_map([], |row| row.get::<_, String>(1))?
        .collect::<std::result::Result<Vec<_>, _>>()?;
    if columns.iter().any(|existing| existing == column) {
        return Ok(());
    }

    conn.execute(
        &format!("ALTER TABLE {table} ADD COLUMN {column} {definition}"),
        [],
    )?;
    Ok(())
}

fn now_string() -> String {
    Utc::now().to_rfc3339()
}

fn bool_to_i64(value: bool) -> i64 {
    if value { 1 } else { 0 }
}

fn path_vec_to_strings(paths: &[PathBuf]) -> Vec<String> {
    paths
        .iter()
        .map(|path| path.display().to_string())
        .collect()
}

fn normalize_path(path: PathBuf) -> PathBuf {
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

#[cfg(test)]
mod tests {
    use tempfile::NamedTempFile;

    use super::*;
    use crate::models::ReviewRequest;

    fn defaults() -> SessionDefaults {
        SessionDefaults {
            cwd: std::env::current_dir().unwrap(),
            model: Some("gpt-5.4".to_string()),
            reasoning_effort: Some("medium".to_string()),
            session_prompt: None,
            sandbox_mode: "read-only".to_string(),
            approval_policy: "never".to_string(),
            search_mode: SearchMode::Disabled,
            add_dirs: vec![],
        }
    }

    #[test]
    fn seeds_admins_and_sessions() {
        let tmp = NamedTempFile::new().unwrap();
        let store = Store::open(tmp.path(), &[100], &defaults()).unwrap();
        let admin = store.get_user(100).unwrap().unwrap();
        assert!(admin.allowed);
        assert_eq!(admin.role, UserRole::Admin);

        let session = store
            .ensure_session(SessionKey::new(1, Some(10)), 100, &defaults())
            .unwrap();
        assert_eq!(session.key.thread_id, 10);
    }

    #[test]
    fn records_turns() {
        let tmp = NamedTempFile::new().unwrap();
        let store = Store::open(tmp.path(), &[100], &defaults()).unwrap();
        let session = store
            .ensure_session(SessionKey::new(1, Some(10)), 100, &defaults())
            .unwrap();
        let request = TurnRequest {
            session_key: session.key,
            from_user_id: 100,
            prompt: "hello".to_string(),
            runtime_instructions: None,
            attachments: vec![],
            review_mode: Some(ReviewRequest {
                base: Some("main".to_string()),
                commit: None,
                uncommitted: false,
                title: None,
                prompt: None,
            }),
            override_search_mode: None,
        };
        let turn_id = store.record_turn_started(session.id, &request).unwrap();
        store
            .record_turn_finished(turn_id, "completed", Some("answer"))
            .unwrap();
        let last = store.last_assistant_text(session.key).unwrap();
        assert!(last.is_none());
    }

    #[test]
    fn persists_pending_turns_and_recovery_state() {
        let tmp = NamedTempFile::new().unwrap();
        let store = Store::open(tmp.path(), &[100], &defaults()).unwrap();
        let session = store
            .ensure_session(SessionKey::new(1, Some(10)), 100, &defaults())
            .unwrap();
        let request = TurnRequest {
            session_key: session.key,
            from_user_id: 100,
            prompt: "hello".to_string(),
            runtime_instructions: None,
            attachments: vec![],
            review_mode: None,
            override_search_mode: None,
        };

        store
            .set_session_busy(session.key, true)
            .expect("mark busy");
        store
            .enqueue_pending_turn(session.id, &request, "private")
            .expect("enqueue");

        let pending = store
            .claim_next_pending_turn(session.key)
            .expect("claim")
            .expect("pending turn");
        assert_eq!(pending.request.prompt, "hello");

        let recovered = store.recover_interrupted_work().expect("recover");
        assert_eq!(recovered, vec![session.key]);

        let recovered_session = store
            .get_session(session.key)
            .expect("load session")
            .expect("session");
        assert_eq!(recovered_session.state, SessionState::Interrupted);
        assert_eq!(recovered_session.pending_turns, 1);

        let queued_again = store
            .claim_next_pending_turn(session.key)
            .expect("claim after recover")
            .expect("pending turn after recover");
        assert_eq!(queued_again.chat_kind, "private");
    }
}
