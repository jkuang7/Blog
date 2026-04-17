use super::*;

pub(super) struct OrxIntakeSubmission {
    pub body: String,
    pub keyboard: Option<InlineKeyboardMarkup>,
}

pub(super) struct OrxOperatorResponse {
    pub body: String,
    pub keyboard: Option<InlineKeyboardMarkup>,
}

impl App {
    pub(super) fn orx_binding(&self) -> Option<(&OrxClient, &crate::config::OrxConfig)> {
        Some((self.shared.orx.as_ref()?, self.shared.config.orx.as_ref()?))
    }

    pub(super) async fn orx_status_response(
        &self,
        message: &Message,
        session_key: SessionKey,
    ) -> Result<OrxOperatorResponse> {
        let (orx_client, _orx_config) = self
            .orx_binding()
            .ok_or_else(|| anyhow!("ORX is not configured"))?;
        let payload = orx_client.bot_status(&self.shared.bot_identity).await?;
        let mut body = summarize_orx_status(&payload);
        if !is_primary_forum_dashboard(
            &self.shared.config,
            &message.chat,
            message.message_thread_id,
        ) {
            if let Some(session) = self.shared.store.get_session(session_key)? {
                body.push_str("\n\nLocal Telegram session:\n");
                body.push_str(&format_session_status(&session, &message.chat));
            }
        }
        let keyboard = orx_operator_keyboard_for_payload(&payload);
        Ok(OrxOperatorResponse { body, keyboard })
    }

    pub(super) async fn orx_dispatch_run_response(
        &self,
        message: &Message,
    ) -> Result<OrxOperatorResponse> {
        let (orx_client, _orx_config) = self
            .orx_binding()
            .ok_or_else(|| anyhow!("ORX is not configured"))?;
        let payload = orx_client
            .dispatch_run(
                &self.shared.bot_identity,
                message.chat.id,
                message.message_thread_id,
            )
            .await?;
        let body = summarize_orx_dispatch(&payload);
        let keyboard = orx_operator_keyboard_for_payload(&payload);
        Ok(OrxOperatorResponse { body, keyboard })
    }

    pub(super) async fn orx_submit_intake_response(
        &self,
        user: &crate::models::UserRecord,
        message: &Message,
        request: &str,
    ) -> Result<OrxIntakeSubmission> {
        let (orx_client, _orx_config) = self
            .orx_binding()
            .ok_or_else(|| anyhow!("ORX is not configured"))?;
        let payload = orx_client
            .submit_intake(
                &self.shared.bot_identity,
                message.chat.id,
                message.message_thread_id,
                request,
            )
            .await?;
        let body = summarize_orx_intake_submission(&payload);
        let keyboard = if intake_requires_approval(&payload) {
            if let Some(intake_key) = intake_key_from_payload(&payload) {
                let token = Uuid::now_v7().simple().to_string();
                self.shared.pending_intake_approvals.lock().await.insert(
                    token.clone(),
                    PendingIntakeApproval {
                        requester_user_id: user.tg_user_id,
                        intake_key,
                    },
                );
                Some(orx_intake_keyboard(&token))
            } else {
                None
            }
        } else {
            None
        };
        Ok(OrxIntakeSubmission { body, keyboard })
    }
}
