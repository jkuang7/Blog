# Career Narrative: Jian Kuang

---
## Session State
last_phase: 4
last_step: 4.2
timestamp: 2025-12-25T00:00:00Z
stories_hardened: [STORY-03]
stories_in_progress: [STORY-05]
---

## Professional Summary

Led cloud migrations for 4 underwriting apps (40M+ requests/year, 99.9% uptime). Scoped a stalled 1-year project to 4 months, shipped with multi-region failover. TypeScript/React/AWS, 2+ years as team lead.

---

## Career Timeline

### State Farm (Jul 2023 - Sep 2025)
**Role**: Mid-Level → Team Lead

**Achievements**:
- Shipped 4 cloud migrations (FIMS, NBUS, Xactware, PBRI) — 40M+ annual requests, 99.9% uptime
- Cut project estimate 1 year → 4-6 months, delivered Xactware portal with multi-region failover
- Eliminated $200K+ SLA penalties via failover architecture

**Projects**:

| Project | Type | Impact | My Role |
|---------|------|--------|---------|
| FIMS | API | 34M calls/year | Led architecture, coordinated 4 engineers |
| Xactware | Full-stack | 7.1M requests, $200K SLA saved | Solo E2E: scaffold, deploy, failover |
| NBUS | API + Step Functions | 10K daily, 6mo early | Built skeleton, divide-and-conquer strategy |
| PBRI | Full-stack | 15 product lines | E2E + auth migration (LDAP→Entra) |

---

### Attentive (Feb 2022 - Jan 2023)
**Role**: Software Engineer

**Achievements**:
- Built self-serve UI for 150+ clients — cut support tickets 80%
- Fixed data pipeline bug — 50× larger datasets, 98% less memory
- Owned Pixel SDK for 200+ clients — 15% attribution lift on $10M+ ad spend

---

## Story Inventory

| ID | Story | Type | Key Metric |
|----|-------|------|------------|
| STORY-03 | Xactware Portal | Full-stack/Reliability | 7.1M req, $200K saved |
| STORY-05 | Blue/Green Deploys | Leadership/Process | P99: 4.77s→150ms |
| STORY-02 | NBUS Architecture | Migration | 6 months early |
| STORY-08 | Data Pipeline | Debugging | 50×, 98% memory |
| STORY-04 | PBRI | Full-stack/Auth | 15 product lines |
| STORY-09 | Mentorship | Leadership | 40% faster ramp |

---

## HARDENED: STORY-03 (Xactware Agent Portal) ✅

**Hook**: Took over stalled migration, shipped multi-region SSO portal in 4-6 months

**Stats**:
- 7.1M+ annual requests
- $200K+ SLA penalties eliminated
- Scoped 1 year → 4-6 months

**What I Did**:
- Scaffolded React/TypeScript project from scratch
- Built multi-region failover (US-East/US-West) with chaos engineering
- Mentored junior to work in parallel — split tasks, no overlap
- Skipped stand-ups for focused deep work
- Used AI to understand legacy Spring Boot, rebuilt from first principles

**Tradeoffs**:
- Skipped tests → debugged E2E flow instead (faster)
- Deferred UI polish, secrets rotation
- Added docs + deploy warnings as cheap guardrails

**Answer Versions**:

**1-Line**:
Took over stalled migration, cut scope from 1 year to 4 months, shipped multi-region portal handling 7M+ requests.

**2-Minute**:
Inherited a project that was behind and chaotic. Previous estimate: 1 year. I broke tasks into musts vs nice-to-haves, leveraged existing Terraform modules, used AI to understand the legacy code. Mentored a junior to work in parallel while I handled scaffolding and deployment. Shipped a React/TypeScript SSO bridge for insurance agents. Built multi-region failover — when AWS East went down, West stayed up, avoided SLA penalties. Tradeoff: skipped tests, focused on E2E flow working.

**Deep Dive (on tests)**:
Tests slow you down when requirements change. I debug through the app and validate with the TA instead. React state management makes unit tests flaky. After shipping, add integration tests for critical paths. Junior wanted tests before refactoring — I said refactor first. He tried it his way, came back agreeing. If it's hard to test, refactor the code first.

**Follow-Ups**:
| Question | Answer |
|----------|--------|
| More time? | Abstract Terraform into shared repo |
| Previous engineer? | Project needed restructuring, focused on deadline |
| Why no tests? | Flaky for React, requirements changing, would add integration tests after |
| How 4-6 months? | Broke down tasks, understood costs, leveraged existing work |
| AWS outage? | West stayed up, failover worked |

---

## IN PROGRESS: STORY-05 (Blue/Green Deploys) 🔄

**Hook**: Pushed blue/green adoption, cut deploy latency P99 4.77s→150ms

**Stats**:
- P99 latency: 4.77s → 150ms (cutover time)
- Migrated team to blue/green

**What I Did**:
- Pushed for blue/green after seeing outages across company
- Migrated team to blue/green deploys
- Cut deploy risk — test on green, instant cutover to prod

**Tradeoffs**:
- Cost of second env vs developer velocity (minimal — storage only)

**Clarified**:
- P99 improvement = cutover latency (not app latency)
- Services: team's apps (FIMS, NBUS, Xactware, PBRI)
- Onboarding: [still needs answer]

---

## PENDING STORIES

| ID | Hook | Key Stat | Status |
|----|------|----------|--------|
| STORY-01 | FIMS API migration | 34M calls/year | Not probed |
| STORY-02 | NBUS divide-and-conquer | 6 months early | Not probed |
| STORY-04 | PBRI auth modernization | 15 product lines | Not probed |
| STORY-06 | Self-serve UI | 80% ticket reduction | Not probed |
| STORY-07 | Pixel SDK CSS hacks | 15% attribution lift | Not probed |
| STORY-08 | Data pipeline fix | 50×, 98% memory | Not probed |
| STORY-09 | Mentorship | 40% faster ramp | Not probed |

---

## Interview Tips

**Do**:
- Lead with the win, then explain how
- Use specific numbers (not "improved" or "various")
- Say "I" for your work, clarify team contributions separately

**Don't**:
- Trash colleagues ("project needed restructuring" not "he was entry-level")
- Trash leadership ("cost concerns at higher levels")
- Say "helped with" / "worked on" — say what you did

**Know**:
- Blue/green: two envs, instant traffic switch
- Rolling: gradual instance replacement
- Canary: small % traffic to new version first

**Defend**:
- 99.9% uptime: "After initial deployment stabilization — early phase had expected issues as we learned the prod environment. Once stable, maintained 99.9%."
- 40M+ requests: FIMS (34M) + Xactware (7.1M) + NBUS (3.6M) = ~45M. Source doc says 40M+.
