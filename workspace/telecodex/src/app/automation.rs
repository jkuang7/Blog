use super::presentation::send_markdown_message;
use super::*;

const LINEAR_ISSUE_URL_PREFIX: &str = "https://linear.app/jkprojects/issue/";

#[derive(Debug, Clone, PartialEq, Eq)]
pub(super) struct TelecodexFooter {
    pub status: String,
    pub next: FooterNext,
    pub linear_issue: Option<String>,
    pub phase: Option<String>,
    pub branch: Option<String>,
    pub raw: String,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(super) enum FooterNext {
    Review,
    Run,
    Stop,
}

impl FooterNext {
    fn parse(value: &str) -> Option<Self> {
        match value.trim().to_ascii_lowercase().as_str() {
            "review" => Some(Self::Review),
            "run" => Some(Self::Run),
            "stop" => Some(Self::Stop),
            _ => None,
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(super) enum RunFooterDisposition {
    ReviewCurrentSession,
    FreshRun,
    Pause,
    StopIdle,
    StopBlocked,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(super) enum FooterScopeValidation {
    Valid,
    Mismatch { expected: String, actual: String },
}

impl TelecodexFooter {
    pub(super) fn status_is(&self, expected: &str) -> bool {
        self.status.trim().eq_ignore_ascii_case(expected)
    }

    pub(super) fn status_is_one_of(&self, expected: &[&str]) -> bool {
        expected.iter().any(|status| self.status_is(status))
    }
}

pub(super) fn validate_footer_scope(
    footer: &TelecodexFooter,
    runner_scope_issue: Option<&str>,
) -> FooterScopeValidation {
    let Some(expected) = runner_scope_issue else {
        return FooterScopeValidation::Valid;
    };
    let actual = footer.linear_issue.as_deref().unwrap_or("none");
    if actual.eq_ignore_ascii_case(expected) {
        FooterScopeValidation::Valid
    } else {
        FooterScopeValidation::Mismatch {
            expected: expected.to_string(),
            actual: actual.to_string(),
        }
    }
}

pub(super) fn decide_run_footer_disposition(
    footer: &TelecodexFooter,
    automation_enabled: bool,
    scoped_issue: bool,
) -> RunFooterDisposition {
    match footer.next {
        FooterNext::Review => RunFooterDisposition::ReviewCurrentSession,
        FooterNext::Run => {
            if automation_enabled && footer.status_is_one_of(&["needs_followup", "retry"]) {
                RunFooterDisposition::FreshRun
            } else {
                RunFooterDisposition::Pause
            }
        }
        FooterNext::Stop => {
            if footer.status_is("blocked") {
                if automation_enabled && !scoped_issue {
                    RunFooterDisposition::FreshRun
                } else {
                    RunFooterDisposition::StopBlocked
                }
            } else {
                RunFooterDisposition::StopIdle
            }
        }
    }
}

pub(super) fn parse_telecodex_footer(text: &str) -> Option<TelecodexFooter> {
    let mut status = None;
    let mut next = None;
    let mut linear_issue = None;
    let mut phase = None;
    let mut branch = None;
    let mut raw_lines = Vec::new();

    for line in text.lines() {
        let trimmed = line.trim();
        let Some((key, value)) = trimmed.split_once('=') else {
            continue;
        };
        if !key.starts_with("TELECODEX_") {
            continue;
        }
        raw_lines.push(trimmed.to_string());
        match key {
            "TELECODEX_STATUS" => status = Some(value.trim().to_string()),
            "TELECODEX_NEXT" => next = FooterNext::parse(value),
            "TELECODEX_LINEAR_ISSUE" => linear_issue = non_empty_footer_value(value),
            "TELECODEX_PHASE" => phase = non_empty_footer_value(value),
            "TELECODEX_BRANCH" => branch = non_empty_footer_value(value),
            _ => {}
        }
    }

    Some(TelecodexFooter {
        status: status?,
        next: next?,
        linear_issue,
        phase,
        branch,
        raw: raw_lines.join("\n"),
    })
}

fn non_empty_footer_value(value: &str) -> Option<String> {
    let trimmed = value.trim();
    if trimmed.is_empty() || trimmed == "-" || trimmed.eq_ignore_ascii_case("none") {
        None
    } else {
        Some(trimmed.to_string())
    }
}

pub(super) fn linear_issue_url(issue_key: &str) -> Option<String> {
    let key = normalize_linear_issue_key(issue_key)?;
    Some(format!("{LINEAR_ISSUE_URL_PREFIX}{key}"))
}

pub(super) fn linear_issue_markdown_link(issue_key: &str) -> Option<String> {
    let key = normalize_linear_issue_key(issue_key)?;
    let url = linear_issue_url(&key)?;
    Some(format!("[{key}]({url})"))
}

pub(super) fn add_completion_message(footer: Option<&TelecodexFooter>) -> String {
    let Some(issue_key) = footer.and_then(|footer| footer.linear_issue.as_deref()) else {
        return "`/add` finished. Linear should now contain the work context, phase comments, and Todo execution tickets."
            .to_string();
    };
    let Some(link) = linear_issue_markdown_link(issue_key) else {
        return "`/add` finished. Linear should now contain the work context, phase comments, and Todo execution tickets."
            .to_string();
    };
    format!(
        "`/add` finished: {link}. Linear should now contain the work context, phase comments, and Todo execution tickets."
    )
}

pub(super) fn link_linear_issue_keys_for_markdown(text: &str) -> String {
    let mut output = String::with_capacity(text.len());
    let mut in_fenced_code = false;

    for line in text.lines() {
        let trimmed = line.trim_start();
        if trimmed.starts_with("```") {
            in_fenced_code = !in_fenced_code;
            output.push_str(line);
            output.push('\n');
            continue;
        }
        if in_fenced_code {
            output.push_str(line);
            output.push('\n');
            continue;
        }
        output.push_str(&link_linear_issue_keys_in_line(line));
        output.push('\n');
    }

    if !text.ends_with('\n') {
        output.pop();
    }
    output
}

fn link_linear_issue_keys_in_line(line: &str) -> String {
    let chars: Vec<char> = line.chars().collect();
    let mut output = String::with_capacity(line.len());
    let mut idx = 0;
    let mut in_inline_code = false;

    while idx < chars.len() {
        if chars[idx] == '`' {
            in_inline_code = !in_inline_code;
            output.push(chars[idx]);
            idx += 1;
            continue;
        }

        if !in_inline_code {
            if let Some((key, consumed)) = issue_key_at(&chars, idx) {
                if let Some(link) = linear_issue_markdown_link(&key) {
                    output.push_str(&link);
                    idx += consumed;
                    continue;
                }
            }
        }

        output.push(chars[idx]);
        idx += 1;
    }

    output
}

fn issue_key_at(chars: &[char], start: usize) -> Option<(String, usize)> {
    let previous = start.checked_sub(1).and_then(|idx| chars.get(idx)).copied();
    if previous.is_some_and(|ch| ch.is_ascii_alphanumeric() || ch == '-' || ch == '/' || ch == '[')
    {
        return None;
    }

    let mut idx = start;
    while chars.get(idx).is_some_and(|ch| ch.is_ascii_alphabetic()) {
        idx += 1;
    }
    if idx == start || chars.get(idx) != Some(&'-') {
        return None;
    }
    idx += 1;
    let digits_start = idx;
    while chars.get(idx).is_some_and(|ch| ch.is_ascii_digit()) {
        idx += 1;
    }
    if idx == digits_start {
        return None;
    }
    if chars
        .get(idx)
        .is_some_and(|ch| ch.is_ascii_alphanumeric() || *ch == '-')
    {
        return None;
    }

    let key: String = chars[start..idx].iter().collect();
    Some((key, idx - start))
}

fn normalize_linear_issue_key(issue_key: &str) -> Option<String> {
    let key = issue_key.trim().to_ascii_uppercase();
    if key.is_empty()
        || !key.contains('-')
        || !key
            .chars()
            .all(|character| character.is_ascii_alphanumeric() || character == '-')
    {
        return None;
    }
    Some(key)
}

pub(super) fn runner_status_text(state: &crate::models::RunnerStateRecord) -> String {
    let controller = match (state.controller_chat_id, state.controller_thread_id) {
        (Some(chat_id), Some(thread_id)) if thread_id != 0 => {
            format!("{chat_id}/{thread_id}")
        }
        (Some(chat_id), _) => chat_id.to_string(),
        _ => "none".to_string(),
    };
    format!(
        "Runner state: `{}`\nautomation_enabled=`{}`\ncontroller=`{}`\ncurrent_step=`{}`\nscope=`{}`\ngeneration=`{}`\nactive_codex_thread=`{}`\nupdated_at=`{}`",
        state.state.as_str(),
        state.automation_enabled,
        controller,
        state
            .current_step
            .map(|step| step.as_str())
            .unwrap_or("none"),
        state.runner_scope_issue.as_deref().unwrap_or("none"),
        state.generation_id.as_deref().unwrap_or("none"),
        state.active_codex_thread_id.as_deref().unwrap_or("none"),
        state.updated_at
    )
}

pub(super) fn build_runner_prompt(
    step: crate::models::AutomationStep,
    user_context: Option<&str>,
    state: &crate::models::RunnerStateRecord,
) -> String {
    let command = match step {
        crate::models::AutomationStep::Add => {
            let context = user_context.unwrap_or_default().trim();
            if context.is_empty() {
                "/add".to_string()
            } else {
                format!("/add {context}")
            }
        }
        crate::models::AutomationStep::Run => match state.runner_scope_issue.as_deref() {
            Some(issue) => format!("/run {issue}"),
            None => "/run".to_string(),
        },
        crate::models::AutomationStep::Review => match user_context.map(str::trim) {
            Some(context) if !context.is_empty() => format!("/review {context}"),
            _ => "/review".to_string(),
        },
    };
    let step_contract = runner_step_contract(step, state.runner_scope_issue.as_deref());
    let context = format!(
        "Telecodex controller context:\n- state={}\n- automation_enabled={}\n- generation_id={}\n- previous_step={}\n- runner_scope_issue={}\n- active_codex_thread={}\n- last_footer={}\n\nUse this only as orientation. Linear comments and git state are the source of truth. If runner_scope_issue is set, inspect and update only that Linear issue.\n\nController contract: your final answer MUST end with TELECODEX_STATUS, TELECODEX_NEXT, TELECODEX_LINEAR_ISSUE, TELECODEX_PHASE, and TELECODEX_BRANCH lines. Do not put prose after the footer.\n\n{step_contract}",
        state.state.as_str(),
        state.automation_enabled,
        state.generation_id.as_deref().unwrap_or("none"),
        state
            .current_step
            .map(|step| step.as_str())
            .unwrap_or("none"),
        state.runner_scope_issue.as_deref().unwrap_or("none"),
        state.active_codex_thread_id.as_deref().unwrap_or("none"),
        state.last_footer.as_deref().unwrap_or("none")
    );
    format!("{command}\n\n{context}")
}

fn runner_step_contract(step: crate::models::AutomationStep, scope_issue: Option<&str>) -> String {
    let scope_rule = match step {
        crate::models::AutomationStep::Add => {
            "Intake scope rule: `/add` is planning and ticket materialization, not local runner queueing. Create new execution tickets in Linear `Todo` by default, because `To Do` is the approval signal for `/run`; the user can manually move tickets to `Backlog` when they should not be touched. If the request spans multiple repos or projects, create separate feature tickets for each repo/project and, when coordination is useful, a coordination ticket that links them. Do not ask whether to split obvious multi-project work; split it automatically and record dependencies explicitly. Do not look for local tmux-codex runner state.".to_string()
        }
        _ => match scope_issue {
            Some(issue) => format!(
                "Scoped issue rule: this turn is restricted to `{issue}`. First verify the Linear issue state is exactly `Todo`; if it is not `Todo`, do not inspect, claim, implement, review, update, block, or advance it, and stop with TELECODEX_STATUS=no_ready_work and TELECODEX_NEXT=stop. Do not inspect, claim, update, block, or advance any other Linear issue. TELECODEX_LINEAR_ISSUE must be `{issue}`."
            ),
            None => "Unscoped rule: select only one pickable PRO issue at a time, and only from Linear issues whose state is exactly `Todo`. Ignore `Backlog` issues completely, even when they contain ready telecodex phase comments. Record any blocker before moving on.".to_string(),
        },
    };

    let shared_phase_rules = "Phase comment contract: use existing `telecodex:phase` comments as the durable work queue. Update an existing phase comment instead of creating duplicate phase comments. Canonical phase header format is `<!-- telecodex:phase id=\"phase-01\" status=\"ready\" depends=\"\" branch=\"<branch>\" worker=\"codex\" lease_expires_at=\"\" proof=\"\" commit=\"\" -->`.";

    let footer_rules = "Footer status rules: use `implemented` + `review` after a run implements one phase; use `done` + `run` after review closes a phase and more scoped work may remain; use `needs_followup` + `run` only after writing follow-up evidence into Linear; use `blocked` + `stop` only after writing the blocker into Linear; use `no_ready_work` + `stop` only when no ready or follow-up work remains in the allowed scope.";

    let step_rules = match step {
        crate::models::AutomationStep::Add => {
            "For `/add`: create or update Linear issue(s) with complete execution context and put new executable tickets in `Todo` by default. A single-project request becomes one self-contained feature ticket with phase comments. A multi-project request becomes one self-contained feature ticket per repo/project, plus a coordination ticket when cross-project sequencing matters. When decomposing work, create phase comments with the canonical phase header from the start. Each phase comment must include branch, acceptance criteria, proof plan, no-code/code scope, and evidence placeholders. In the human-readable final response, include actual Linear URLs for the coordination issue and every feature issue, using Markdown links like `[PRO-270](https://linear.app/jkprojects/issue/PRO-270)`. Return the primary issue key or coordination issue key in TELECODEX_LINEAR_ISSUE; use TELECODEX_PHASE=- when no single phase was executed."
        }
        crate::models::AutomationStep::Run => {
            "For `/run`: only work Linear issues in state `Todo`. If scoped, verify the scoped issue is `Todo` before reading phase details; if unscoped, select only from `Todo`. Ignore `Backlog` completely. Claim the next ready phase in scope, update only that phase comment, record concrete evidence, and emit `TELECODEX_NEXT=review` when the phase is implemented. If all `Todo` work in scope is done or no ready/follow-up phase work remains, record the final stop-check in Linear, move the issue to `In Review` when appropriate, and emit `TELECODEX_STATUS=no_ready_work` with `TELECODEX_NEXT=stop`."
        }
        crate::models::AutomationStep::Review => {
            "For `/review`: review only the phase identified by the last footer and the current scoped issue. Do not discover or claim new Linear work during review. Re-read the phase comment and git state, then update that same phase comment to `done`, `needs_followup`, or `blocked` with evidence. Emit `TELECODEX_NEXT=run` after a done/follow-up review so the controller starts a fresh run; the next `/run` must re-apply the `To Do` gate. Emit `TELECODEX_NEXT=stop` only for blocked or terminal review outcomes."
        }
    };

    format!("{scope_rule}\n\n{shared_phase_rules}\n\n{footer_rules}\n\n{step_rules}")
}

pub(super) async fn send_runner_update(
    shared: &Arc<AppShared>,
    session_key: crate::models::SessionKey,
    text: &str,
) -> Result<()> {
    let linked_text = link_linear_issue_keys_for_markdown(text);
    if let Err(error) = shared
        .store
        .record_runner_event(session_key, None, "status", &linked_text)
    {
        tracing::debug!("failed to record runner status event: {error:#}");
    }
    send_markdown_message(
        &shared.telegram,
        session_key.chat_id,
        Some(session_key.thread_id).filter(|value| *value != 0),
        &linked_text,
        None,
    )
    .await?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_footer_from_final_text() {
        let footer = parse_telecodex_footer(
            "done\nTELECODEX_STATUS=implemented\nTELECODEX_NEXT=review\nTELECODEX_LINEAR_ISSUE=PRO-123\nTELECODEX_PHASE=phase-02\nTELECODEX_BRANCH=feature/pro-123",
        )
        .unwrap();

        assert_eq!(footer.status, "implemented");
        assert_eq!(footer.next, FooterNext::Review);
        assert_eq!(footer.linear_issue.as_deref(), Some("PRO-123"));
        assert_eq!(footer.phase.as_deref(), Some("phase-02"));
        assert_eq!(footer.branch.as_deref(), Some("feature/pro-123"));
    }

    #[test]
    fn rejects_footer_without_next() {
        assert!(parse_telecodex_footer("TELECODEX_STATUS=implemented").is_none());
    }

    #[test]
    fn matches_footer_status_case_insensitively() {
        let footer = parse_telecodex_footer(
            "TELECODEX_STATUS=Needs_Followup\nTELECODEX_NEXT=Run\nTELECODEX_LINEAR_ISSUE=PRO-123\nTELECODEX_PHASE=phase-02\nTELECODEX_BRANCH=feature/pro-123",
        )
        .unwrap();

        assert!(footer.status_is("needs_followup"));
        assert!(footer.status_is_one_of(&["implemented", "needs_followup"]));
        assert_eq!(footer.next, FooterNext::Run);
    }

    #[test]
    fn validates_scoped_footer_issue() {
        let footer = parse_telecodex_footer(
            "TELECODEX_STATUS=implemented\nTELECODEX_NEXT=review\nTELECODEX_LINEAR_ISSUE=PRO-123\nTELECODEX_PHASE=phase-01\nTELECODEX_BRANCH=main",
        )
        .unwrap();

        assert_eq!(
            validate_footer_scope(&footer, Some("PRO-123")),
            FooterScopeValidation::Valid
        );
        assert_eq!(
            validate_footer_scope(&footer, Some("PRO-999")),
            FooterScopeValidation::Mismatch {
                expected: "PRO-999".to_string(),
                actual: "PRO-123".to_string()
            }
        );
    }

    #[test]
    fn unscoped_blocked_run_keeps_continuous_runner_going() {
        let footer = parse_telecodex_footer(
            "TELECODEX_STATUS=blocked\nTELECODEX_NEXT=stop\nTELECODEX_LINEAR_ISSUE=PRO-266\nTELECODEX_PHASE=phase-01\nTELECODEX_BRANCH=feature/pro-266",
        )
        .unwrap();

        assert_eq!(
            decide_run_footer_disposition(&footer, true, false),
            RunFooterDisposition::FreshRun
        );
    }

    #[test]
    fn scoped_blocked_run_stops_on_selected_ticket() {
        let footer = parse_telecodex_footer(
            "TELECODEX_STATUS=blocked\nTELECODEX_NEXT=stop\nTELECODEX_LINEAR_ISSUE=PRO-266\nTELECODEX_PHASE=phase-01\nTELECODEX_BRANCH=feature/pro-266",
        )
        .unwrap();

        assert_eq!(
            decide_run_footer_disposition(&footer, true, true),
            RunFooterDisposition::StopBlocked
        );
    }

    #[test]
    fn needs_followup_run_starts_fresh_run_session() {
        let footer = parse_telecodex_footer(
            "TELECODEX_STATUS=needs_followup\nTELECODEX_NEXT=run\nTELECODEX_LINEAR_ISSUE=PRO-266\nTELECODEX_PHASE=phase-01\nTELECODEX_BRANCH=feature/pro-266",
        )
        .unwrap();

        assert_eq!(
            decide_run_footer_disposition(&footer, true, false),
            RunFooterDisposition::FreshRun
        );
    }

    #[test]
    fn no_ready_work_stops_continuous_runner() {
        let footer = parse_telecodex_footer(
            "TELECODEX_STATUS=no_ready_work\nTELECODEX_NEXT=stop\nTELECODEX_LINEAR_ISSUE=PRO-123\nTELECODEX_PHASE=-\nTELECODEX_BRANCH=-",
        )
        .unwrap();

        assert_eq!(
            validate_footer_scope(&footer, Some("PRO-123")),
            FooterScopeValidation::Valid
        );
        assert_eq!(
            decide_run_footer_disposition(&footer, true, true),
            RunFooterDisposition::StopIdle
        );
    }

    #[test]
    fn builds_linear_issue_url_from_footer_key() {
        assert_eq!(
            linear_issue_url("pro-270").as_deref(),
            Some("https://linear.app/jkprojects/issue/PRO-270")
        );
        assert_eq!(linear_issue_url("not a key"), None);
    }

    #[test]
    fn links_linear_issue_keys_for_markdown() {
        let linked = link_linear_issue_keys_for_markdown(
            "Updated phase-02 on PRO-273. Existing [PRO-270](https://linear.app/jkprojects/issue/PRO-270) stayed linked. `PRO-999` stayed code.",
        );

        assert!(linked.contains("[PRO-273](https://linear.app/jkprojects/issue/PRO-273)"));
        assert!(linked.contains("[PRO-270](https://linear.app/jkprojects/issue/PRO-270)"));
        assert!(linked.contains("`PRO-999`"));
        assert!(!linked.contains("[[PRO-270]"));
    }

    #[test]
    fn does_not_link_inside_code_fences_or_urls() {
        let linked = link_linear_issue_keys_for_markdown(
            "url https://linear.app/jkprojects/issue/PRO-273\n```text\nPRO-274\n```\nPRO-275",
        );

        assert!(linked.contains("https://linear.app/jkprojects/issue/PRO-273"));
        assert!(linked.contains("```text\nPRO-274\n```"));
        assert!(linked.contains("[PRO-275](https://linear.app/jkprojects/issue/PRO-275)"));
        assert!(!linked.contains("issue/[PRO-273]"));
    }

    #[test]
    fn add_completion_message_links_primary_linear_issue() {
        let footer = parse_telecodex_footer(
            "TELECODEX_STATUS=done\nTELECODEX_NEXT=run\nTELECODEX_LINEAR_ISSUE=PRO-270\nTELECODEX_PHASE=-\nTELECODEX_BRANCH=-",
        )
        .unwrap();

        let message = add_completion_message(Some(&footer));

        assert!(message.contains("[PRO-270](https://linear.app/jkprojects/issue/PRO-270)"));
        assert!(message.contains("Todo execution tickets"));
    }

    #[test]
    fn builds_scoped_run_prompt() {
        let state = crate::models::RunnerStateRecord {
            automation_enabled: true,
            state: crate::models::RunnerState::Running,
            controller_chat_id: Some(1),
            controller_thread_id: Some(2),
            active_codex_thread_id: None,
            generation_id: Some("gen-1".to_string()),
            current_step: Some(crate::models::AutomationStep::Run),
            runner_scope_issue: Some("PRO-123".to_string()),
            last_footer: None,
            stop_requested: false,
            started_at: None,
            updated_at: "now".to_string(),
        };

        let prompt = build_runner_prompt(crate::models::AutomationStep::Run, None, &state);

        assert!(prompt.starts_with("/run PRO-123"));
        assert!(prompt.contains("runner_scope_issue=PRO-123"));
        assert!(prompt.contains("TELECODEX_LINEAR_ISSUE must be `PRO-123`"));
        assert!(prompt.contains("state is exactly `Todo`"));
        assert!(prompt.contains("no ready or follow-up work remains in the allowed scope"));
    }

    #[test]
    fn builds_add_prompt_with_canonical_phase_header_contract() {
        let state = runner_state(crate::models::AutomationStep::Add, None);

        let prompt = build_runner_prompt(
            crate::models::AutomationStep::Add,
            Some("create work"),
            &state,
        );

        assert!(prompt.starts_with("/add create work"));
        assert!(prompt.contains("Canonical phase header format"));
        assert!(prompt.contains("<!-- telecodex:phase id=\"phase-01\" status=\"ready\""));
        assert!(prompt.contains("split it automatically"));
        assert!(prompt.contains("Do not ask whether to split obvious multi-project work"));
        assert!(prompt.contains("Do not look for local tmux-codex runner state"));
        assert!(prompt.contains("Create new execution tickets in Linear `Todo` by default"));
        assert!(prompt.contains("actual Linear URLs"));
        assert!(prompt.contains("https://linear.app/jkprojects/issue/PRO-270"));
    }

    #[test]
    fn builds_review_prompt_for_last_footer_phase_only() {
        let mut state = runner_state(crate::models::AutomationStep::Review, Some("PRO-123"));
        state.last_footer = Some(
            "TELECODEX_STATUS=implemented\nTELECODEX_NEXT=review\nTELECODEX_LINEAR_ISSUE=PRO-123\nTELECODEX_PHASE=phase-02\nTELECODEX_BRANCH=main"
                .to_string(),
        );

        let prompt = build_runner_prompt(crate::models::AutomationStep::Review, None, &state);

        assert!(prompt.starts_with("/review"));
        assert!(prompt.contains("review only the phase identified by the last footer"));
        assert!(prompt.contains("Do not discover or claim new Linear work during review"));
        assert!(prompt.contains("update that same phase comment"));
        assert!(prompt.contains("TELECODEX_PHASE=phase-02"));
    }

    fn runner_state(
        step: crate::models::AutomationStep,
        scope: Option<&str>,
    ) -> crate::models::RunnerStateRecord {
        crate::models::RunnerStateRecord {
            automation_enabled: step != crate::models::AutomationStep::Add,
            state: match step {
                crate::models::AutomationStep::Review => crate::models::RunnerState::Reviewing,
                _ => crate::models::RunnerState::Running,
            },
            controller_chat_id: Some(1),
            controller_thread_id: Some(2),
            active_codex_thread_id: None,
            generation_id: Some("gen-1".to_string()),
            current_step: Some(step),
            runner_scope_issue: scope.map(str::to_string),
            last_footer: None,
            stop_requested: false,
            started_at: None,
            updated_at: "now".to_string(),
        }
    }
}
