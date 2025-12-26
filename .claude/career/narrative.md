# Career Narrative: Jian Kuang

---
## Session State
last_phase: 4.5
last_step: 4.5.2
timestamp: 2025-12-26T00:00:00Z
stories_hardened: [STORY-02, STORY-03, STORY-05, STORY-08]
stories_refined: [STORY-02, STORY-03, STORY-05, STORY-08]
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

### Context (Rich — For Downstream Understanding)

**Problem**: Xactware migration stalled at 1-year estimate. SSO portal for insurance agents to access vendor system. Previous engineer couldn't make progress.

**Why It Mattered**: $200K+ SLA penalties if portal unavailable. 7.1M annual requests from agents. Business-critical for underwriting workflow.

**Key Nuances**:
- SSO bridge using form POST requests to vendor system
- Had to reverse-engineer legacy Spring Boot without docs
- Multi-region failover required (US-East/US-West) for SLA
- Jim Buckley as TA for validation
- Junior engineer (Zahid) available but needed mentoring
- Stand-ups were time sinks, opted for async deep work

**Resolution Flow**:
1. Broke tasks into musts vs nice-to-haves → cut scope to 4-6 months
2. Used AI to understand legacy Spring Boot, rebuilt from first principles
3. Scaffolded React/TypeScript from scratch
4. Mentored junior to work in parallel — split tasks, no overlap
5. Built multi-region failover with chaos engineering
6. Skipped tests, debugged E2E flow instead
7. Added docs + deploy warnings as cheap guardrails

### Refined (Translated — For Interview Use)

**Hook**: Took over stalled migration, shipped multi-region SSO portal in 4-6 months

**Skeleton STAR**:
- S: Stalled migration, 1-year estimate, $200K SLA risk
- T: Ship SSO portal, enable failover
- A: Cut scope, scaffolded React, mentored junior, chaos tested
- R: 7.1M requests, $200K saved, 4-6 months

**Core Insight**: I unblock stalled projects by cutting scope ruthlessly and shipping fast.

**Question Triggers**:
- "Tell me about a time you took over a struggling project"
- "How do you handle ambiguity?"
- "Describe a time you shipped under pressure"

**Answer Versions**:

**1-Line**: Took over stalled migration, cut scope from 1 year to 4 months, shipped multi-region portal handling 7M+ requests.

**2-Minute**: Inherited a project that was behind and chaotic. Previous estimate: 1 year. I broke tasks into musts vs nice-to-haves, leveraged existing Terraform modules, used AI to understand the legacy code. Mentored a junior to work in parallel while I handled scaffolding and deployment. Shipped a React/TypeScript SSO bridge for insurance agents. Built multi-region failover — when AWS East went down, West stayed up, avoided SLA penalties. Tradeoff: skipped tests, focused on E2E flow working.

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

### Context (Rich — For Downstream Understanding)

**Problem**: Rolling deploys caused outages during cloud migrations. Cutover latency was P99 4.77s — users experienced downtime during every deploy.

**Why It Mattered**: Company-wide outages were damaging trust. Developer velocity suffered because deploys were risky. All 4 apps needed a better strategy.

**Key Nuances**:
- Blue/green: two envs, test on green, instant traffic switch
- ROSA (Red Hat OpenShift on AWS) framework available
- Team leads: Abit, Vijaya, me. Manager: Jessi
- Had to influence without authority — couldn't mandate, had to demo value
- Cost concern: second env costs, but minimal (storage only during idle)

**Resolution Flow**:
1. Saw outages across company during cloud migrations
2. Implemented blue/green on PBRI first as proof of concept
3. Demoed to manager (Jessi) and key engineers (Chanana, Ron)
4. Got manager sponsorship → she pushed in team meeting
5. Provided ROSA framework resources, made myself available for questions
6. Team leads adopted it, trained their engineers
7. All 4 apps migrated to blue/green

### Refined (Translated — For Interview Use)

**Hook**: Pushed blue/green adoption, cut deploy latency P99 4.77s→150ms

**Skeleton STAR**:
- S: Rolling deploys causing outages, P99 4.77s
- T: Get team to adopt blue/green
- A: Built POC on PBRI, demoed to manager, got sponsorship
- R: P99 150ms, all 4 apps migrated

**Core Insight**: I influence without authority by showing value first, then getting sponsorship.

**Question Triggers**:
- "Tell me about a time you influenced without authority"
- "How do you drive adoption of new practices?"
- "Describe a technical decision you led"

**Answer Versions**:

**1-Line**: Pushed blue/green adoption across team, cut deploy cutover latency from 4.77s to 150ms.

**2-Minute**: After seeing outages across the company during cloud migrations, I implemented blue/green on PBRI as a proof of concept. Demoed to my manager and key engineers — showed the value: test on green, instant cutover, easy rollback. Got manager buy-in, she pushed it in a team meeting. I provided ROSA framework resources, made myself available for questions. Team leads adopted it for their projects and trained their engineers. Result: P99 cutover latency dropped from 4.77s to 150ms.

**Follow-Ups**:
| Question | Answer |
|----------|--------|
| Why not write docs? | Demo was faster, teams could figure out implementation details |
| Any resistance? | No — once manager sponsored it, team leads adopted it |
| What if someone didn't use ROSA? | Pointed them to Terraform approach, let them figure it out |
| How did you measure P99? | ROSA/OpenShift metrics during cutover window |

---

## HARDENED: STORY-02 (NBUS Architecture) ✅

### Context (Rich — For Downstream Understanding)

**Problem**: IBM BPM migration — monolith where you couldn't touch one piece without breaking another. DIY surveys for insurance, distributed state machine needed.

**Why It Mattered**: 10K daily requests. Vendor responses were async, needed waiting states. Multiple engineers had to work in parallel without blocking.

**Key Nuances**:
- AWS Step Functions for distributed state machine
- Master state pattern: pass full state, destructure, update, return merged
- Team leads: Abit, Vijaya. Junior: James. Entry: Zahid
- Needed living documentation to keep architecture current
- TypeScript for type safety on data contracts between Steps
- SQS queues for async vendor response handling

**Resolution Flow**:
1. Designed master state pattern for divide-and-conquer
2. Built skeleton architecture, let team leads own parallel tracks
3. Created Migration Runbook on main branch — PRs required to update it
4. "Measure twice, cut once": internal meeting with leads first, got buy-in
5. Presented strategy to whole team after leads aligned
6. Centralized ticket ownership to Abit for single source of truth
7. 5 engineers worked in parallel, shipped 6 months early

### Refined (Translated — For Interview Use)

**Hook**: Designed divide-and-conquer architecture for IBM BPM migration, shipped 6 months early

**Skeleton STAR**:
- S: IBM BPM monolith, can't parallelize work
- T: Enable 5 engineers to work without blocking
- A: Master state pattern, skeleton arch, living docs
- R: 6 months early, 10K daily requests

**Core Insight**: I unblock ambiguity with first-principles architecture and living documentation.

**Question Triggers**:
- "Tell me about a time you led a technical migration"
- "How do you handle ambiguity?"
- "Describe a time you enabled team parallelism"

**Answer Versions**:

**1-Line**: Designed divide-and-conquer architecture for IBM BPM migration, enabled 5 engineers to work in parallel, shipped 6 months early.

**2-Minute**: IBM BPM was a monolith — you couldn't touch one piece without breaking another. I designed a master state pattern: each Step Function receives full state, destructures what it needs, updates its piece, returns the merged state. This let engineers work on different Steps without stepping on each other. I built the skeleton, then let team leads own their tracks. Created a Migration Runbook on main branch — PRs required to update it, so it stayed current. Met with leads first to get buy-in, then presented the strategy to the whole team. Centralized ticket ownership to Abit so there was one source of truth. Result: 5 engineers working in parallel, shipped 6 months early.

**Follow-Ups**:
| Question | Answer |
|----------|--------|
| Why Step Functions? | Waiting states for vendor responses, state snapshots for debugging, test Steps in isolation |
| Why centralize tickets? | Single source of truth, avoided duplicate work and confusion |
| How did you get buy-in? | Met with leads first, showed the architecture, addressed concerns, then presented together to team |
| What if someone didn't follow the pattern? | PRs required to update Runbook — code review caught deviations |
| What was the hardest part? | Getting the state contract right upfront — once that was stable, parallel work flowed |

---

## HARDENED: STORY-08 (Data Pipeline Memory Fix) ✅

### Context (Rich — For Downstream Understanding)

**Problem**: ETL pipeline for SMS campaign analytics crashing on large files. Memory would spike and never come down. Tier 1 clients sending 20GB Excel files.

**Why It Mattered**: Business analytics team couldn't analyze subscription trends for enterprise clients. Campaigns couldn't be refined without this data. Pipeline was blocking revenue optimization.

**Key Nuances**:
- Python ETL script containerized with Airflow
- 15-min deploy cycles made debugging expensive
- Couldn't replicate S3 bucket locally
- Bug looked like normal file loading — hard to spot in code review
- Had to aggregate rows for analytics team downstream
- Issue only surfaced with large files (tier 1 clients)

**Resolution Flow**:
1. Noticed memory spike only occurred with large files
2. Suspected file ingestion code but 15-min deploys made testing expensive
3. Copied offending code portion, ran locally with actual file
4. Found bug: loading entire file into memory instead of streaming
5. Fixed with batch streaming — load rows X to Y, process, next block
6. Before/after memory comparison proved 98% reduction
7. Deployed fix, pipeline stable for 20GB files

### Refined (Translated — For Interview Use)

**Hook**: Debugged memory-hogging ETL pipeline, achieved 50× lighter memory footprint

**Skeleton STAR**:
- S: ETL pipeline crashing on 20GB files, 15-min debug cycles
- T: Find and fix memory bug
- A: Isolated code locally, found full-file load bug, batch streamed
- R: 98% memory reduction (50× lighter)

**Core Insight**: If something isn't testable, make it testable. First-principle thinking beats expensive deploy cycles.

**Question Triggers**:
- "Walk me through the hardest bug you've solved"
- "Tell me about a time you debugged a production issue"
- "How do you approach problems that are hard to reproduce?"

**Answer Versions**:

**1-Line**: Debugged ETL pipeline memory bug by isolating code locally, fixed with batch streaming, 98% memory reduction.

**2-Minute**: Enterprise clients were sending 20GB Excel files for SMS campaign analytics. Pipeline kept crashing — memory would spike and never come down. Hard to debug: 15-minute Airflow deploy cycles, couldn't replicate S3 locally. I suspected the file ingestion code. Copied just that portion, ran it locally with an actual file. Found the bug: loading entire file into memory instead of streaming. Fix was batch streaming — load rows X to Y, process, move to next block. Before/after comparison showed 98% memory reduction. Lesson: if something isn't testable, make it testable. First-principle thinking.

**Follow-Ups**:
| Question | Answer |
|----------|--------|
| How did you pick batch size? | Tuned empirically — small enough for memory safety, large enough for throughput |
| Why not row-by-row? | Too slow for 20GB files. Batching balanced speed and memory |
| How did you measure 98%? | Before/after memory profiling on same file |
| Why was original code loading full file? | Bug — looked like streaming but was accumulating in memory |
| Could you have caught this in code review? | Hard to spot — code looked like normal file loading, issue only surfaced with large files |

---

## PENDING STORIES

| ID | Hook | Key Stat | Status |
|----|------|----------|--------|
| STORY-01 | FIMS API migration | 34M calls/year | Not probed |
| STORY-04 | PBRI auth modernization | 15 product lines | Not probed |
| STORY-06 | Self-serve UI | 80% ticket reduction | Not probed |
| STORY-07 | Pixel SDK CSS hacks | 15% attribution lift | Not probed |
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
