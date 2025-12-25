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

Full-stack engineer with 3+ years experience leading cloud migrations and building scalable systems at enterprise scale. Transitioned legacy on-prem apps to AWS/ROSA serving 50M+ annual requests with 99.9% uptime. Strong ownership signal: architected distributed workflows, mentored engineers, and shipped production systems under tight deadlines. Looking to bring enterprise-scale technical leadership to a high-growth startup.

---

## Career Timeline

### State Farm (Jul 2023 - Sep 2025)
**Role**: Mid-Level → Team Lead
**Context**: Insurance underwriting systems - property risk assessment for homes, businesses, natural disasters

**Key Projects**:

1. **FIMS (Fire Inspection Management Service)** — *API*
   - What: API for fire/building risk assessment (businesses, farms, commercial). Utilized drone data for acre footage risk calculations. Called by internal teams including NBUS
   - Your Role: Led API architecture and migration, coordinated 4+ engineers
   - Impact: 34M+ annual API calls, zero downtime migration

2. **Xactware Agent Portal (Seamless Login)** — *Full-stack E2E*
   - What: SSO bridge to Xactware (Verisk) vendor site. Sends property data via POST requests on behalf of agents, and allows agents to pull relevant info for their work
   - Your Role: Led full-stack development and deployment E2E
   - Impact: 7.1M+ annual requests, eliminated $200K+ SLA penalties

3. **NBUS (Inspection Workflow)** — *API + AWS Step Functions*
   - What: Multi-step orchestrated workflow using distributed state machine for DIY home surveys + non-DIY flows. Complex pipeline with external service calls, vendor waits, SQS queues. Migrated from legacy IBM BPM
   - Your Role: Built skeleton architecture and divide-and-conquer strategy for workflow breakdown. Large mapping effort from IBM BPM. Worked alongside another lead, handed off after establishing foundation
   - Impact: 10K+ daily transactions, delivered 6 months early

4. **PBRI (Peril Based Risk Information)** — *Full-stack E2E*
   - What: Natural disaster risk lookup tool (flooding, geographic risks, policy coverage). Agents quickly assess if homeowner needs specific insurance
   - Your Role: Led full-stack development and deployment E2E, auth modernization (LDAP → Entra)
   - Impact: 15+ insurance product lines, zero downtime sunset

### Attentive (Feb 2022 - Jan 2023)
**Role**: Software Engineer
**Context**: Mobile marketing platform for ecommerce clients

**Key Projects**:

1. **Self-Serve UI** — Metadata management for API integrations (Mailchimp, Klaviyo, etc). Clients update settings via UI instead of DB manual updates
   - Impact: 150+ clients, 80% reduction in support tickets

2. **Pixel SDK** — Web scraping for attribution data (ATC, checkout). CSS hacks to fix client website changes
   - Impact: 200+ clients, 15% improved attribution on $10M+ ad spend

3. **Data Pipeline** — Segmentation routing, analyzed columns to determine flow triggers. Major bug fix.
   - Impact: 50× larger datasets, 98% less memory, weekly outages → stable

---

## Story Inventory

| ID | Project/Topic | Type | Company |
|----|---------------|------|---------|
| STORY-01 | FIMS | Migration/API | State Farm |
| STORY-02 | NBUS | Architecture/Migration | State Farm |
| STORY-03 | Xactware Agent Portal | Full-stack/Reliability | State Farm |
| STORY-04 | PBRI UI | Full-stack/Auth Migration | State Farm |
| STORY-05 | Blue/Green Deploys | Leadership/Process | State Farm |
| STORY-06 | Self-Serve UI | Delivery | Attentive |
| STORY-07 | Pixel SDK | Debugging/Adaptability | Attentive |
| STORY-08 | Data Pipeline | Debugging/Optimization | Attentive |
| STORY-09 | Mentorship | Leadership/Mentorship | State Farm |

---

## Top 6 Stories to Harden

| Rank | Story | Score | Status |
|------|-------|-------|--------|
| 1 | STORY-03: Xactware Agent Portal | 23/25 | ✅ HARDENED |
| 2 | STORY-05: Blue/Green Deploys | 22/25 | 🔄 IN PROGRESS |
| 3 | STORY-02: NBUS Architecture | 21/25 | pending |
| 4 | STORY-08: Data Pipeline (Attentive) | 20/25 | pending |
| 5 | STORY-04: PBRI UI | 19/25 | pending |
| 6 | STORY-09: Mentorship | 18/25 | pending |

---

## HARDENED STORIES

### STORY-03: Xactware Agent Portal ✅

**Type**: Full-stack/Reliability
**Company**: State Farm
**Competencies**: Ownership, Technical Leadership, Architecture, Mentorship

**Claims/Stats**:
- 7.1M+ annual requests
- Eliminated $200K+ SLA penalties
- Scoped from 1 year → 4-6 months
- Multi-region failover (US-East/US-West)

**Context**:
- Inherited chaotic project that needed restructuring
- Previous estimate was 1 year, scoped to 4-6 months
- Built SSO bridge for insurance agents to access Xactware (Verisk) property valuation tools
- Form POST request sending property data, agent ID, state agent code to vendor

**What YOU Did**:
- Scaffolded entire project from scratch using React/TypeScript
- Pushed for TypeScript standardization across team
- Leveraged AI to understand legacy Spring Boot code, rebuilt from first principles
- Mentored Zahid to work in parallel, split work to avoid overlap
- Built multi-region failover with chaos engineering testing
- Broke tasks into musts vs nice-to-haves with buffer estimates
- Requested to skip stand-ups for focused deep work

**Tradeoffs**:
- Skipped tests, prioritized E2E flow working
- UI enhancements pushed out
- Tech debt accepted (secrets rotation deferred)
- Added docs and deploy script warnings as cheaper fixes

**Startup Translation**:
- Speed tradeoff: Skipped tests, debugging locally was faster than test suites
- Scope cuts: UI polish deferred, secrets rotation deferred
- Ambiguity: Old Spring Boot → React translation wasn't obvious, used AI + first principles
- Unblocking others: Scaffolded project, set up Terraform/deployment for junior
- Smaller team version: "This is exactly what I'd do at a startup — 2-person team, skipped ceremonies, focused on shipping"

**Answer Versions**:

**1-Line**:
"Took over a stalled migration, scoped it from 1 year to 4-6 months, shipped a multi-region SSO portal handling 7M+ requests with $200K+ SLA penalties eliminated."

**2-Minute**:
"I inherited a project that was behind schedule and needed restructuring. The previous estimate was a year — I scoped it down to 4-6 months by breaking tasks into musts vs nice-to-haves, leveraging existing Terraform modules, and using AI to understand the legacy Spring Boot code rather than translating line-by-line. I mentored a junior engineer to work in parallel while I focused on high-leverage work like scaffolding and deployment. We shipped a React/TypeScript SSO bridge that sends property data to Xactware for insurance agents. I built multi-region failover — when we had an AWS outage in one region, US-West stayed up and we avoided SLA penalties. Tradeoffs: skipped tests initially, focused on E2E flow working, added docs and deploy guardrails as cheaper risk mitigation."

**Deep Dive (tests)**:
"Tests slow you down when requirements are still changing. I'd rather debug through the app and validate with the TA than write tests I'll have to rewrite. React state management makes granular unit tests flaky anyway. After shipping, I'd add integration tests for critical paths. My junior wanted to write tests before refactoring — I told him refactor first. He tried it his way, came back saying I was right. If it's hard to test, the code needs refactoring, not more tests."

**Expected Follow-Ups**:
1. "What would you have done differently with more time?" → Abstract Terraform modules into one repo for reuse
2. "How did you handle conflict with previous engineer?" → Skill gap too large, focused on deadline, let him float
3. "Why not write tests?" → Flaky for React, requirements changing, debugging faster, would add integration tests after
4. "How did you know 4-6 months was realistic?" → Broke down tasks, understood costs, leveraged existing work + AI
5. "What happened during AWS outage?" → US-West stayed up, failover worked, waited for AWS to fix East

---

### STORY-05: Blue/Green Deploys 🔄 (IN PROGRESS)

**Type**: Leadership/Process
**Company**: State Farm
**Competencies**: Technical Leadership, Process Improvement, Mentorship

**Claims/Stats**:
- P99 latency: 4.77s → 150ms (during cutover)
- Migrated team to blue/green
- Platform-wide push (partial success)

**Context**:
- ROSA (Red Hat OpenShift on AWS) framework enabled blue/green
- Company having outages during cloud migration, teams not testing properly
- 5-10% of prod code differences can take down apps
- Old deploy: delete old versions, replace with new = 4.77s latency
- Blue/green: test on green, cutover instantly = 150ms

**What YOU Did**:
- Pushed aggressively for blue/green adoption
- Migrated team to use blue/green for testing environment
- Argued cost is minimal (only storage, traffic hits one env)
- Got director agreement but couldn't convince upper leadership for prod

**Tradeoffs**:
- Cost of second environment vs developer speed
- Got team adoption, not full org adoption

**Pending Clarifications**:
- P99 improvement: cutover latency or overall app latency?
- How many teams/services migrated?
- What did onboarding look like? (docs, demos, pairing?)

---

## PENDING STORIES (Not Yet Probed)

### STORY-01: FIMS
- API for fire/building risk assessment
- 34M+ annual API calls
- Zero downtime migration

### STORY-02: NBUS
- Distributed state machine with AWS Step Functions
- Divide-and-conquer strategy for IBM BPM migration
- 10K+ daily transactions, delivered 6 months early

### STORY-04: PBRI UI
- Full-stack natural disaster risk lookup
- Auth modernization (LDAP → Entra)
- 15+ insurance product lines

### STORY-06: Self-Serve UI (Attentive)
- Metadata management for API integrations
- 150+ clients, 80% ticket reduction

### STORY-07: Pixel SDK (Attentive)
- Web scraping + CSS hacks for attribution
- 200+ clients, 15% attribution improvement

### STORY-08: Data Pipeline (Attentive)
- Segmentation routing bug fix
- 50× larger datasets, 98% less memory

### STORY-09: Mentorship
- 10→6 week ramp time
- 40% faster time-to-first-PR
- Mentored James, Zahid

---

## Notes for Resume

### Key Themes to Emphasize:
- Ownership: Led E2E on 3 full-stack apps
- Speed: Scoped 1 year → 4-6 months, delivered 6 months early
- Reliability: Multi-region failover, blue/green deploys
- Mentorship: Accelerated junior engineers, parallel work strategies
- Pragmatism: Skip tests when moving fast, add guardrails cheaply

### Interview Tips:
- Don't mention colleague skill levels negatively — say "project needed restructuring"
- Don't trash leadership — say "cost concerns at higher levels"
- Know blue/green vs rolling vs canary definitions
- Lead with wins, then explain how
