# Career Narrative: Jian Kuang

---
## Session State
last_phase: 4
last_step: 4.4
timestamp: 2025-12-25T00:00:00Z
stories_hardened: [STORY-02, STORY-03, STORY-05]
stories_in_progress: []
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

## HARDENED: STORY-05 (Blue/Green Deploys) ✅

**Hook**: Pushed blue/green adoption, cut deploy latency P99 4.77s→150ms

**Stats**:
- P99 latency: 4.77s → 150ms (cutover time)
- Migrated team to blue/green (all 4 apps)

**What I Did**:
- Implemented blue/green on PBRI first as proof of concept
- Demoed to manager (Jessi) and team (Chanana, Ron) — showed value, explained why
- Got manager sponsorship → she pushed team in team meeting
- Provided ROSA framework links/resources for teams
- Made myself available for questions, enabled team leads to trickle down

**Tradeoffs**:
- Cost of second env vs developer velocity (minimal — storage only)
- Skipped docs — demo was faster, teams could figure out implementation

**Answer Versions**:

**1-Line**:
Pushed blue/green adoption across team, cut deploy cutover latency from 4.77s to 150ms.

**2-Minute**:
After seeing outages across the company during cloud migrations, I implemented blue/green on PBRI as a proof of concept. Demoed to my manager and key engineers — showed the value: test on green, instant cutover, easy rollback. Got manager buy-in, she pushed it in a team meeting. I provided ROSA framework resources, made myself available for questions. Team leads adopted it for their projects and trained their engineers. Result: P99 cutover latency dropped from 4.77s to 150ms.

**Follow-Ups**:
| Question | Answer |
|----------|--------|
| Why not write docs? | Demo was faster, teams could figure out implementation details |
| Any resistance? | No — once manager sponsored it, team leads adopted it |
| What if someone didn't use ROSA? | Pointed them to Terraform approach, let them figure it out |
| How did you measure P99? | ROSA/OpenShift metrics during cutover window |

---

## HARDENED: STORY-02 (NBUS Architecture) ✅

**Hook**: Designed divide-and-conquer architecture for IBM BPM migration, shipped 6 months early

**Stats**:
- Delivered 6 months ahead of schedule
- 10K daily requests
- 5 engineers working in parallel without blocking

**What I Did**:
- Designed master state pattern: pass full state into each Step Function, destructure what you need, update, return updated state
- Built skeleton architecture, let team leads (Abit, Vijaya) own parallel tracks
- Created living documentation (Migration Runbook) on main branch — PRs required to update it
- "Measure twice, cut once": internal meeting with leads first, got buy-in, then presented to whole team
- Centralized ticket ownership to Abit for single source of truth
- Used TypeScript for type safety on data contracts between Steps

**Why Step Functions**:
- Waiting states for vendor responses (async by nature)
- State snapshots stored automatically — can resume from any point
- Test each Step in isolation before integration

**Tradeoffs**:
- More upfront architecture time → paid off with parallel execution
- Centralized ticket ownership → slight bottleneck but eliminated confusion

**Answer Versions**:

**1-Line**:
Designed divide-and-conquer architecture for IBM BPM migration, enabled 5 engineers to work in parallel, shipped 6 months early.

**2-Minute**:
IBM BPM was a monolith — you couldn't touch one piece without breaking another. I designed a master state pattern: each Step Function receives full state, destructures what it needs, updates its piece, returns the merged state. This let engineers work on different Steps without stepping on each other. I built the skeleton, then let team leads own their tracks. Created a Migration Runbook on main branch — PRs required to update it, so it stayed current. Met with leads first to get buy-in, then presented the strategy to the whole team. Centralized ticket ownership to Abit so there was one source of truth. Result: 5 engineers working in parallel, shipped 6 months early.

**Follow-Ups**:
| Question | Answer |
|----------|--------|
| Why Step Functions? | Waiting states for vendor responses, state snapshots for debugging, test Steps in isolation |
| Why centralize tickets? | Single source of truth, avoided duplicate work and confusion |
| How did you get buy-in? | Met with leads first, showed the architecture, addressed concerns, then presented together to team |
| What if someone didn't follow the pattern? | PRs required to update Runbook — code review caught deviations |
| What was the hardest part? | Getting the state contract right upfront — once that was stable, parallel work flowed |

---

## PENDING STORIES

| ID | Hook | Key Stat | Status |
|----|------|----------|--------|
| STORY-01 | FIMS API migration | 34M calls/year | Not probed |
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
