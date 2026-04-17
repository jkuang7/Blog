use anyhow::{Context, Result, bail};
use reqwest::Client;
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};

#[derive(Debug, Clone)]
pub struct OrxClient {
    http: Client,
    api_base: String,
}

#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct OrxNotification {
    pub notification_id: i64,
    pub project_key: String,
    pub owning_bot: String,
    pub target_bot: Option<String>,
    pub assignment_id: Option<String>,
    pub ingress_bot: Option<String>,
    pub issue_key: Option<String>,
    pub kind: String,
    pub payload: Value,
}

impl OrxClient {
    pub fn new(api_base: String) -> Self {
        Self {
            http: Client::new(),
            api_base: api_base.trim_end_matches('/').to_string(),
        }
    }

    pub async fn register_bot(
        &self,
        bot_identity: &str,
        default_display_name: &str,
        telegram_chat_id: Option<i64>,
        telegram_thread_id: Option<i64>,
    ) -> Result<Value> {
        self.post(
            "/registry/bots",
            json!({
                "bot_identity": bot_identity,
                "default_display_name": default_display_name,
                "telegram_chat_id": telegram_chat_id,
                "telegram_thread_id": telegram_thread_id,
                "metadata": {
                    "source": "telecodex",
                },
            }),
        )
        .await
    }

    pub async fn dispatch_run(
        &self,
        ingress_bot: &str,
        ingress_chat_id: i64,
        ingress_thread_id: Option<i64>,
    ) -> Result<Value> {
        self.post(
            "/dispatch/run",
            json!({
                "ingress_bot": ingress_bot,
                "ingress_chat_id": ingress_chat_id,
                "ingress_thread_id": ingress_thread_id,
            }),
        )
        .await
    }

    pub async fn bot_status(&self, bot_identity: &str) -> Result<Value> {
        self.get(&format!("/bot/status?bot={bot_identity}")).await
    }

    pub async fn bot_queue(&self, bot_identity: &str) -> Result<Value> {
        self.get(&format!("/bot/queue?bot={bot_identity}")).await
    }

    pub async fn bot_pause(&self, bot_identity: &str, payload: Value) -> Result<Value> {
        self.post(
            "/bot/pause",
            json!({
                "bot_identity": bot_identity,
                "payload": payload,
            }),
        )
        .await
    }

    pub async fn bot_resume(&self, bot_identity: &str, payload: Value) -> Result<Value> {
        self.post(
            "/bot/resume",
            json!({
                "bot_identity": bot_identity,
                "payload": payload,
            }),
        )
        .await
    }

    pub async fn submit_intake(
        &self,
        ingress_bot: &str,
        ingress_chat_id: i64,
        ingress_thread_id: Option<i64>,
        request_text: &str,
    ) -> Result<Value> {
        self.post(
            "/intake/submit",
            json!({
                "ingress_bot": ingress_bot,
                "ingress_chat_id": ingress_chat_id,
                "ingress_thread_id": ingress_thread_id,
                "request_text": request_text,
            }),
        )
        .await
    }

    pub async fn approve_intake(&self, intake_key: &str) -> Result<Value> {
        self.post(
            "/intake/approve",
            json!({
                "intake_key": intake_key,
            }),
        )
        .await
    }

    pub async fn reject_intake(&self, intake_key: &str, note: Option<&str>) -> Result<Value> {
        self.post(
            "/intake/reject",
            json!({
                "intake_key": intake_key,
                "note": note,
            }),
        )
        .await
    }

    pub async fn poll_notifications(
        &self,
        bot_identity: &str,
        limit: usize,
    ) -> Result<Vec<OrxNotification>> {
        let payload = self
            .get(&format!(
                "/notifications?bot={bot_identity}&limit={limit}"
            ))
            .await?;
        let notifications = payload
            .get("notifications")
            .and_then(Value::as_array)
            .ok_or_else(|| anyhow::anyhow!("ORX notifications response missing notifications array"))?;
        notifications
            .iter()
            .cloned()
            .map(serde_json::from_value::<OrxNotification>)
            .collect::<std::result::Result<Vec<_>, _>>()
            .context("failed to decode ORX notifications")
    }

    pub async fn acknowledge_notifications(&self, notification_ids: &[i64]) -> Result<Value> {
        self.post(
            "/notifications/ack",
            json!({
                "notification_ids": notification_ids,
            }),
        )
        .await
    }

    pub async fn sync_bot_name(
        &self,
        bot_identity: &str,
        current_display_name: Option<&str>,
        desired_display_name: Option<&str>,
        sync_state: &str,
        retry_at: Option<&str>,
    ) -> Result<Value> {
        self.post(
            "/bot/name-sync",
            json!({
                "bot_identity": bot_identity,
                "current_display_name": current_display_name,
                "desired_display_name": desired_display_name,
                "sync_state": sync_state,
                "retry_at": retry_at,
            }),
        )
        .await
    }

    async fn get(&self, path: &str) -> Result<Value> {
        let response = self
            .http
            .get(format!("{}{}", self.api_base, path))
            .send()
            .await
            .with_context(|| format!("failed to call ORX GET {path}"))?;
        self.decode_response(response).await
    }

    async fn post(&self, path: &str, body: Value) -> Result<Value> {
        let response = self
            .http
            .post(format!("{}{}", self.api_base, path))
            .json(&body)
            .send()
            .await
            .with_context(|| format!("failed to call ORX POST {path}"))?;
        self.decode_response(response).await
    }

    async fn decode_response(&self, response: reqwest::Response) -> Result<Value> {
        let status = response.status();
        let body = response
            .text()
            .await
            .context("failed to read ORX response body")?;
        let payload: Value = serde_json::from_str(&body)
            .with_context(|| format!("failed to decode ORX JSON: {body}"))?;
        if !status.is_success() {
            let error = payload
                .get("error")
                .and_then(Value::as_str)
                .unwrap_or("unknown ORX error");
            bail!("ORX request failed with {status}: {error}");
        }
        Ok(payload)
    }
}
