use super::presentation::quick_reply_keyboard;
use super::support::{
    is_message_not_modified, is_message_thread_not_found, should_drop_telegram_rate_limited_send,
};
use super::turns::{classify_document_kind, sanitize_file_name};
use super::*;
use crate::models::TelegramTranscriptDirection;

impl App {
    pub(super) fn append_outbound_transcript(
        &self,
        chat_id: i64,
        thread_id: Option<i64>,
        telegram_message_id: Option<i64>,
        markdown: &str,
    ) {
        if let Err(error) = self.shared.store.append_telegram_transcript_entry(
            SessionKey::new(chat_id, thread_id),
            telegram_message_id,
            TelegramTranscriptDirection::Outbound,
            markdown,
        ) {
            tracing::warn!(
                "failed to append outbound telegram transcript for {}:{:?}: {error:#}",
                chat_id,
                thread_id
            );
        }
    }

    pub(super) async fn send_markdown_with_audit(
        &self,
        chat_id: i64,
        thread_id: Option<i64>,
        markdown: &str,
        reply_markup: Option<InlineKeyboardMarkup>,
    ) -> Result<Message> {
        send_markdown_with_shared_audit(&self.shared, chat_id, thread_id, markdown, reply_markup)
            .await
    }

    pub(super) async fn send_notification(
        &self,
        chat_id: i64,
        thread_id: Option<i64>,
        markdown: &str,
    ) -> Result<Message> {
        self.send_markdown_with_audit(chat_id, thread_id, markdown, None)
            .await
    }

    async fn send_or_edit_session_status_html(
        &self,
        chat_id: i64,
        thread_id: Option<i64>,
        html: &str,
        fallback_markdown: &str,
    ) -> Result<bool> {
        let key = SessionKey::new(chat_id, thread_id);
        let Some(session) = self.shared.store.get_session(key)? else {
            return Ok(false);
        };
        let should_edit_existing = session.busy
            || matches!(
                session.state,
                crate::models::SessionState::Planning
                    | crate::models::SessionState::Coding
                    | crate::models::SessionState::Running
                    | crate::models::SessionState::WaitingApproval
                    | crate::models::SessionState::Blocked
            );
        if should_edit_existing {
            if let Some(message_id) = session.last_status_message_id {
                let edit_result = self
                    .shared
                    .telegram
                    .edit_message_text(EditMessageText::html(chat_id, message_id, html.to_string()))
                    .await;
                match edit_result {
                    Ok(_) => {
                        self.append_outbound_transcript(
                            chat_id,
                            thread_id,
                            Some(message_id),
                            fallback_markdown,
                        );
                        return Ok(true);
                    }
                    Err(error) if is_message_not_modified(&error) => return Ok(true),
                    Err(error) if is_message_thread_not_found(&error) => {
                        tracing::debug!(
                            "session status message edit fell back to resend due to missing thread: {error:#}"
                        );
                        self.shared
                            .store
                            .set_session_last_status_message(key, None)?;
                    }
                    Err(error) => {
                        tracing::debug!("session status message edit failed, resending: {error:#}");
                        self.shared
                            .store
                            .set_session_last_status_message(key, None)?;
                    }
                }
            }
        }

        let message = self
            .shared
            .telegram
            .send_message(SendMessage::html(chat_id, thread_id, html.to_string()))
            .await?;
        self.shared
            .store
            .set_session_last_status_message(key, Some(message.message_id))?;
        self.append_outbound_transcript(
            chat_id,
            thread_id,
            Some(message.message_id),
            fallback_markdown,
        );
        Ok(true)
    }

    pub(super) async fn download_attachments(
        &self,
        message: &Message,
        session: &crate::models::SessionRecord,
    ) -> Result<Vec<LocalAttachment>> {
        let inbox_dir = self.session_inbox_dir(session)?;
        let mut attachments = Vec::new();

        if let Some(file_id) = preferred_image_file_id(message) {
            attachments.push(
                self.download_attachment(
                    file_id,
                    None,
                    Some("image/png"),
                    AttachmentKind::Image,
                    &inbox_dir,
                )
                .await?,
            );
        }

        if let Some(document) = &message.document {
            let kind = classify_document_kind(
                document.mime_type.as_deref(),
                document.file_name.as_deref(),
            );
            if kind != AttachmentKind::Image {
                attachments.push(
                    self.download_attachment(
                        &document.file_id,
                        document.file_name.as_deref(),
                        document.mime_type.as_deref(),
                        kind,
                        &inbox_dir,
                    )
                    .await?,
                );
            }
        }

        if let Some(audio) = &message.audio {
            attachments.push(
                self.download_attachment(
                    &audio.file_id,
                    audio.file_name.as_deref(),
                    audio.mime_type.as_deref(),
                    AttachmentKind::Audio,
                    &inbox_dir,
                )
                .await?,
            );
        }

        if let Some(voice) = &message.voice {
            attachments.push(
                self.download_attachment(
                    &voice.file_id,
                    Some("voice.ogg"),
                    voice.mime_type.as_deref(),
                    AttachmentKind::Voice,
                    &inbox_dir,
                )
                .await?,
            );
        }

        if let Some(video) = &message.video {
            attachments.push(
                self.download_attachment(
                    &video.file_id,
                    video.file_name.as_deref(),
                    video.mime_type.as_deref(),
                    AttachmentKind::Video,
                    &inbox_dir,
                )
                .await?,
            );
        }

        Ok(attachments)
    }

    pub(super) fn session_inbox_dir(
        &self,
        session: &crate::models::SessionRecord,
    ) -> Result<PathBuf> {
        let dir = session
            .cwd
            .join(".telecodex")
            .join("inbox")
            .join(Uuid::now_v7().to_string());
        fs::create_dir_all(&dir)?;
        Ok(dir)
    }

    pub(super) async fn download_attachment(
        &self,
        file_id: &str,
        file_name: Option<&str>,
        mime_type: Option<&str>,
        kind: AttachmentKind,
        target_dir: &Path,
    ) -> Result<LocalAttachment> {
        let file = self
            .shared
            .telegram
            .get_file(file_id)
            .await
            .context("telegram getFile failed")?;
        let file_path = file
            .file_path
            .ok_or_else(|| anyhow!("telegram file_path missing for {file_id}"))?;
        let bytes = self.shared.telegram.download_file(&file_path).await?;
        let extension = Path::new(&file_path)
            .extension()
            .and_then(|value| value.to_str())
            .unwrap_or("bin");
        let file_name = sanitize_file_name(
            file_name.unwrap_or(match kind {
                AttachmentKind::Image => "image",
                AttachmentKind::Text => "text",
                AttachmentKind::Audio => "audio",
                AttachmentKind::Voice => "voice",
                AttachmentKind::Video => "video",
                AttachmentKind::Document => "document",
            }),
            extension,
        );
        let path = target_dir.join(format!("{}_{}", Uuid::now_v7(), file_name));
        fs::write(&path, bytes)
            .with_context(|| format!("failed to write attachment {}", path.display()))?;
        Ok(LocalAttachment {
            path,
            file_name,
            mime_type: mime_type.map(ToOwned::to_owned),
            kind,
            transcript: None,
        })
    }

    pub(super) async fn send_status(
        &self,
        chat_id: i64,
        thread_id: Option<i64>,
        markdown: &str,
    ) -> Result<()> {
        let html = render_markdown_to_html(markdown);
        if self
            .send_or_edit_session_status_html(chat_id, thread_id, &html, markdown)
            .await?
        {
            return Ok(());
        }
        match self
            .shared
            .telegram
            .send_message(SendMessage::html(chat_id, thread_id, html))
            .await
        {
            Ok(message) => {
                self.append_outbound_transcript(
                    chat_id,
                    thread_id,
                    Some(message.message_id),
                    markdown,
                );
                Ok(())
            }
            Err(error) => {
                if should_drop_telegram_rate_limited_send(&error) {
                    tracing::warn!("dropping status send due to Telegram rate limit");
                    return Ok(());
                }
                if self
                    .retry_status_without_missing_thread(chat_id, thread_id, markdown, &error)
                    .await?
                {
                    return Ok(());
                }
                let fallback = html_escape::encode_safe(markdown).to_string();
                match self
                    .shared
                    .telegram
                    .send_message(SendMessage::html(chat_id, thread_id, fallback))
                    .await
                {
                    Ok(message) => {
                        self.append_outbound_transcript(
                            chat_id,
                            thread_id,
                            Some(message.message_id),
                            markdown,
                        );
                    }
                    Err(fallback_error)
                        if should_drop_telegram_rate_limited_send(&fallback_error) =>
                    {
                        tracing::warn!("dropping fallback status send due to Telegram rate limit");
                    }
                    Err(fallback_error) => {
                        return Err(fallback_error).with_context(|| {
                            format!("failed to send status after html fallback: {error:#}")
                        });
                    }
                }
                Ok(())
            }
        }
    }

    pub(super) async fn send_html_status(
        &self,
        chat_id: i64,
        thread_id: Option<i64>,
        html: &str,
        fallback_text: Option<&str>,
    ) -> Result<()> {
        let fallback = fallback_text.unwrap_or_default();
        if self
            .send_or_edit_session_status_html(chat_id, thread_id, html, fallback)
            .await?
        {
            return Ok(());
        }
        match self
            .shared
            .telegram
            .send_message(SendMessage::html(chat_id, thread_id, html.to_string()))
            .await
        {
            Ok(message) => {
                self.append_outbound_transcript(
                    chat_id,
                    thread_id,
                    Some(message.message_id),
                    fallback,
                );
                Ok(())
            }
            Err(error) => {
                if should_drop_telegram_rate_limited_send(&error) {
                    tracing::warn!("dropping html status send due to Telegram rate limit");
                    return Ok(());
                }
                if self
                    .retry_status_without_missing_thread(chat_id, thread_id, fallback, &error)
                    .await?
                {
                    return Ok(());
                }
                let fallback = html_escape::encode_safe(fallback).to_string();
                match self
                    .shared
                    .telegram
                    .send_message(SendMessage::html(chat_id, thread_id, fallback))
                    .await
                {
                    Ok(message) => {
                        self.append_outbound_transcript(
                            chat_id,
                            thread_id,
                            Some(message.message_id),
                            fallback_text.unwrap_or_default(),
                        );
                        Ok(())
                    }
                    Err(fallback_error)
                        if should_drop_telegram_rate_limited_send(&fallback_error) =>
                    {
                        tracing::warn!("dropping fallback html status due to Telegram rate limit");
                        Ok(())
                    }
                    Err(fallback_error) => Err(fallback_error).with_context(|| {
                        format!("failed to send html status after fallback: {error:#}")
                    }),
                }
            }
        }
    }

    pub(super) async fn send_command_help(
        &self,
        chat_id: i64,
        thread_id: Option<i64>,
        help: &CommandHelp,
    ) -> Result<()> {
        let html = render_markdown_to_html(&help.text);
        let keyboard = quick_reply_keyboard(&help.quick_commands);
        let mut request = SendMessage::html(chat_id, thread_id, html);
        request.reply_markup = keyboard.clone();
        match self.shared.telegram.send_message(request).await {
            Ok(message) => {
                self.append_outbound_transcript(
                    chat_id,
                    thread_id,
                    Some(message.message_id),
                    &help.text,
                );
                Ok(())
            }
            Err(error) => {
                if should_drop_telegram_rate_limited_send(&error) {
                    tracing::warn!("dropping command help due to Telegram rate limit");
                    return Ok(());
                }
                if self
                    .retry_help_without_missing_thread(
                        chat_id,
                        thread_id,
                        help,
                        keyboard.clone(),
                        &error,
                    )
                    .await?
                {
                    return Ok(());
                }
                let fallback = html_escape::encode_safe(&help.text).to_string();
                let mut fallback_request = SendMessage::html(chat_id, thread_id, fallback);
                fallback_request.reply_markup = keyboard;
                match self.shared.telegram.send_message(fallback_request).await {
                    Ok(message) => {
                        self.append_outbound_transcript(
                            chat_id,
                            thread_id,
                            Some(message.message_id),
                            &help.text,
                        );
                    }
                    Err(fallback_error)
                        if should_drop_telegram_rate_limited_send(&fallback_error) =>
                    {
                        tracing::warn!("dropping fallback command help due to Telegram rate limit");
                    }
                    Err(fallback_error) => {
                        return Err(fallback_error).with_context(|| {
                            format!("failed to send command help after html fallback: {error:#}")
                        });
                    }
                }
                Ok(())
            }
        }
    }

    pub(super) async fn edit_markdown_message(
        &self,
        chat_id: i64,
        message_id: i64,
        markdown: &str,
        reply_markup: Option<InlineKeyboardMarkup>,
    ) -> Result<()> {
        let html = render_markdown_to_html(markdown);
        let mut request = EditMessageText::html(chat_id, message_id, html);
        request.reply_markup = reply_markup.clone();
        match self.shared.telegram.edit_message_text(request).await {
            Ok(_) => Ok(()),
            Err(error) => {
                if is_message_not_modified(&error) {
                    return Ok(());
                }
                if should_drop_telegram_rate_limited_send(&error) {
                    tracing::warn!("dropping edit due to Telegram rate limit");
                    return Ok(());
                }
                let fallback = html_escape::encode_safe(markdown).to_string();
                let mut fallback_request = EditMessageText::html(chat_id, message_id, fallback);
                fallback_request.reply_markup = reply_markup;
                match self
                    .shared
                    .telegram
                    .edit_message_text(fallback_request)
                    .await
                {
                    Ok(_) => Ok(()),
                    Err(fallback_error) if is_message_not_modified(&fallback_error) => Ok(()),
                    Err(fallback_error)
                        if should_drop_telegram_rate_limited_send(&fallback_error) =>
                    {
                        tracing::warn!("dropping fallback edit due to Telegram rate limit");
                        Ok(())
                    }
                    Err(fallback_error) => Err(fallback_error).with_context(|| {
                        format!("failed to edit message after html fallback: {error:#}")
                    }),
                }
            }
        }
    }

    pub(super) async fn retry_status_without_missing_thread(
        &self,
        chat_id: i64,
        thread_id: Option<i64>,
        markdown: &str,
        error: &anyhow::Error,
    ) -> Result<bool> {
        let Some(thread_id) = thread_id.filter(|value| *value != 0) else {
            return Ok(false);
        };
        if !is_message_thread_not_found(error) {
            return Ok(false);
        }
        self.shared
            .store
            .delete_session(SessionKey::new(chat_id, Some(thread_id)))
            .ok();
        let fallback = html_escape::encode_safe(markdown).to_string();
        self.shared
            .telegram
            .send_message(SendMessage::html(chat_id, None, fallback))
            .await?;
        Ok(true)
    }

    pub(super) async fn retry_help_without_missing_thread(
        &self,
        chat_id: i64,
        thread_id: Option<i64>,
        help: &CommandHelp,
        keyboard: Option<InlineKeyboardMarkup>,
        error: &anyhow::Error,
    ) -> Result<bool> {
        let Some(thread_id) = thread_id.filter(|value| *value != 0) else {
            return Ok(false);
        };
        if !is_message_thread_not_found(error) {
            return Ok(false);
        }
        self.shared
            .store
            .delete_session(SessionKey::new(chat_id, Some(thread_id)))
            .ok();
        let fallback = html_escape::encode_safe(&help.text).to_string();
        let mut request = SendMessage::html(chat_id, None, fallback);
        request.reply_markup = keyboard;
        self.shared.telegram.send_message(request).await?;
        Ok(true)
    }

    pub(super) async fn latest_limits_snapshot(&self) -> Result<Option<LimitsSnapshot>> {
        let mut cache = self.shared.limits_cache.lock().await;
        if let Some(cached) = cache.as_ref() {
            if cached.fetched_at.elapsed() < Duration::from_secs(60) {
                return Ok(Some(cached.snapshot.clone()));
            }
        }

        let codex_home = default_codex_home();
        let snapshot = find_latest_limits_snapshot(&codex_home)?;
        if let Some(snapshot) = snapshot.clone() {
            *cache = Some(CachedLimitsSnapshot {
                fetched_at: Instant::now(),
                snapshot,
            });
        }
        Ok(snapshot)
    }
}

pub(super) async fn send_markdown_with_shared_audit(
    shared: &Arc<AppShared>,
    chat_id: i64,
    thread_id: Option<i64>,
    markdown: &str,
    reply_markup: Option<InlineKeyboardMarkup>,
) -> Result<Message> {
    let message = send_markdown_message(
        &shared.telegram,
        chat_id,
        thread_id,
        markdown,
        reply_markup,
    )
    .await?;
    if let Err(error) = shared.store.append_telegram_transcript_entry(
        SessionKey::new(chat_id, thread_id),
        Some(message.message_id),
        TelegramTranscriptDirection::Outbound,
        markdown,
    ) {
        tracing::warn!(
            "failed to append outbound telegram transcript for {}:{:?}: {error:#}",
            chat_id,
            thread_id
        );
    }
    Ok(message)
}
