---
name: envsetup
description: Audit and set up project-specific `.env` values and provider resources for the current repo or a freshly bootstrapped app. Use when the user wants env vars filled, provider dashboards checked, or credentials/resources verified as belonging only to one project. Prefer the repo's local env helper if it exists, verify enabled integrations against the current project name, use Playwright for dashboard work, prefer existing OAuth or signed-in browser sessions, fall back to 1Password autofill when available, and pause for manual login or re-login whenever browser auth still blocks automation. Remember that the MCP browser may use a dedicated Chrome profile rather than the user's everyday Chrome session.
---

# Env Setup

Use this skill for project-scoped env and provider setup work, especially after bootstrapping a new app from a starter.

## Workflow

1. Ground in the repo first:
   - Read the nearest `AGENTS.md`, `.env.example`, env schema/runtime file, and setup docs.
   - If the repo exposes a helper such as `pnpm run env:codex` or `pnpm run setup:check`, run it first and use its output as the checklist.
2. Resolve the project identity before touching providers:
   - Prefer the explicit project name from the user.
   - Otherwise derive it from `package.json` or the repo directory name.
   - Build simple match tokens from that name, such as original, kebab-case, and compact forms.
3. Inventory env state without leaking secrets:
   - Distinguish `set` vs `blank`.
   - Do not print full secrets back to the user unless they explicitly ask for them.
4. Prefer API or CLI verification over dashboards when possible:
   - Stripe, Neon, GitHub, and similar providers are better verified by scoped keys than by UI navigation.
   - Use Playwright only when dashboard-only steps are required.
5. Browser auth policy:
   - Safest default for credentialed dashboard work: use a dedicated browser profile scoped to the current setup task, with only the needed provider sessions/extensions enabled. Prefer reusing that narrow profile over the user's everyday browser profile.
   - Prefer an already signed-in browser session, especially when the provider already supports OAuth login in that session.
   - When a provider offers Google OAuth and that path is available, prefer Google OAuth first before 1Password/autofill. Reuse the existing Google session when possible.
   - Do not assume the MCP browser has the user's normal Chrome extensions or cookies. Verify whether it is attached to a dedicated Chrome profile, a bridge-connected browser, or an isolated Playwright profile before promising 1Password or existing logins.
   - When extension-backed login is important, prefer a dedicated real Chrome profile launched with remote debugging and attached over CDP. Keep that profile narrow to the providers needed for setup work.
   - Distinguish between page-level autofill signals and true extension control. A visible "1Password is available" hint does not guarantee that the extension picker, vault list, or toolbar popup is programmatically accessible.
   - Do not assume you can click the browser toolbar extension button or inspect the extension's account chooser from normal page automation. Browser chrome and extension popups are often outside the page DOM exposed to the MCP browser tools.
   - Only rely on direct extension automation when the workflow is running in a persistent Chromium/Chrome context where the extension is explicitly loaded and its extension page is directly reachable. Otherwise treat 1Password as best-effort autofill, not guaranteed extension UI control.
   - If a fresh login is needed and Google OAuth is not available or does not complete auth, then use 1Password or browser autofill if it is available in the active browser. Use browser/UI automation only; do not inspect, reveal, copy, or request the password value.
   - If 1Password is locked, ask the user only to unlock it, then continue the autofill flow yourself when possible.
   - If 1Password/autofill is unavailable, does not apply, the extension picker is not accessible, or the provider still needs extra interactive auth after autofill, stop and ask the user to complete only the minimal remaining login steps manually in the browser before continuing.
   - When manual intervention is required, warn explicitly and minimally. State: what is blocked, exactly which step the user needs to complete in-browser, what the agent will resume doing afterward, and whether the boundary is password entry, 1Password unlock, MFA, passkey, account chooser, consent, or captcha.
   - If the site session expires later, stop and ask the user to re-login before continuing, again naming the minimal required step.
   - Never inspect, request, or type the user's password.
6. Project-isolation policy:
   - Every enabled provider resource should clearly belong to the current project.
   - If a configured resource looks shared, mislabeled, or ambiguous, create or select a dedicated project-specific replacement before updating `.env`.
   - Leave optional integrations blank if they are intentionally disabled.
7. Finish with repo-local verification:
   - Re-run the setup helper, env audit command, or targeted smoke checks.
   - Summarize which integrations are verified, newly created, intentionally blank, or still blocked by login.

## Starter-Specific Rule

For repos bootstrapped from `create-t3-jian`:

- Use `$envsetup` as the primary workflow.
- Run `pnpm run env:codex -- --project "<project-name>"` only as an internal helper when it is available.
- Treat that helper output as the repo-specific checklist for project-scoped env replacement.
- Keep provider resources tied to the bootstrapped project name, not to the original starter.
