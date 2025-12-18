# Resume Project Context

## TP2 Migration Overview

**What is TP2?**
TP2 refers to legacy applications hosted on on-premises servers that were migrated to AWS cloud infrastructure for improved scalability, reliability, and cost efficiency.

## Projects Migrated

### 1. NBUS/FIMS (Fire Inspection Management Service API)

**What it does:**
FIMS is a comprehensive property risk assessment and inspection management system for insurance underwriting. It automates fire-related risk evaluation, manages inspection workflows from order to completion, and provides underwriters with data-driven insights for policy decisions. The system tracks multiple risk factors including wildfire, earthquake, landslide, hail damage, and property characteristics.

**Business Impact:**

- **Scale:** 34,432,032 API calls annually (2,869,336 calls/month × 12)
- **Users:** Insurance underwriters, agents, inspection companies, and customer service teams
- **Value:** Reduces claim losses by systematically identifying high-risk properties before policy issuance

**Technical Leadership:**

- Led architectural design of distributed state machine for complex workflow orchestration
- Broke down monolithic TP2 application into microservices and step functions
- Designed pluggable state machine architecture allowing parallel development
- Coordinated multiple engineers working on discrete workflow components
- Accelerated migration timeline through parallel workflow development

**Resume Description:**
"Led migration of legacy Fire Inspection Management System processing 34M+ annual API calls to AWS, architecting a distributed state machine solution that enabled parallel development across multiple teams and accelerated delivery timeline by allowing engineers to independently develop workflow components."

### 2. Xactware Agent Portal (Seamless Login)

**What it does:**
Xactware Agent Portal is a single sign-on (SSO) gateway that provides State Farm insurance agents seamless access to Xactware's professional property valuation tools. It eliminates friction by authenticating users through Azure AD, validates agent licensing across multiple states, and launches agents directly into valuation tools with pre-populated claim data - critical for accurate and fast property damage assessments.

**Business Impact:**

- **Scale:** Scaled to serve 7,140,000+ requests annually
- **Users:** Single-state agents, multi-state agents, agent staff, and non-agent users requiring property valuations
- **Value:** Accelerates claim processing by removing authentication barriers and auto-populating property data

**Technical Achievements:**

- Built custom CI/CD pipeline template for consistent deployments
- Implemented multi-region AWS architecture (us-east-1 primary, us-west-2 DR)
- Integrated with multiple backend systems (SFNET, ECHO, EA, ECRM)
- Achieved seamless Azure AD B2C authentication with MSAL v3

**Resume Description:**
"Migrated and scaled Xactware Agent Portal to handle 7.1M+ annual requests, implementing SSO integration that eliminated separate credentials for insurance agents and reduced property valuation access time, directly improving claim processing efficiency."te

### 3. PBRI (Peril Based Risk Information Tool)

**What it does:**
PBRI is a specialized risk assessment tool that provides insurance underwriters and agents with detailed peril-based risk information for properties. It evaluates location-specific risks across multiple insurance products (homeowners, apartments, condos, commercial properties) and generates risk assessments that inform underwriting decisions and pricing strategies.

**Business Impact:**

- **Coverage:** Supports 15+ insurance product lines including residential, commercial, and specialty coverage
- **Users:** Insurance underwriters, agents, and risk assessment teams
- **Value:** Enables data-driven underwriting decisions by providing granular peril-based risk assessments

**Platform Migration:**

- Migrated from TP2 to MERNA (ROSA - Red Hat OpenShift on AWS platform)
- Implemented containerized microservices architecture
- Achieved cloud-native scalability and reliability

**Technical Achievements:**

- Eliminated years of technical debt through comprehensive dependency upgrades
- Improved State Management (React) eg lifting state up to parent component for robust rendering
- Implemented debug mode and enhanced testing capabilities
- Successfully sunset legacy TP2 service with zero downtime
- Improved developer experience with local authentication setup

**Resume Description:**
"Migrated Peril Based Risk Information Tool serving 15+ insurance product lines to cloud-native ROSA platform, eliminating technical debt, implementing enhanced debugging capabilities, and successfully sunsetting legacy infrastructure while maintaining zero downtime for critical underwriting operations."

## Resume Bullet Points

### Software Engineer II (July 2023 - May 2024)

• **Led full-stack migration of legacy Fire Inspection Management System** from Spring Boot to TypeScript/React, processing 34M+ annual API calls with zero downtime, demonstrating ability to modernize complex systems with minimal documentation

• **Scaled and deployed Xactware Agent Portal to production**, architecting solution to handle 7.1M+ annual requests and implementing custom CI/CD pipeline templates that reduced deployment time by 60%

• **Pioneered AWS cloud migration strategy as one of the first engineers** in fire product suite, developing reusable component libraries and migration frameworks that accelerated adoption across 7 teams

• **Provided technical consultation and architecture guidance to 7 engineering teams**, establishing best practices for AWS migration, containerization strategies, and cloud-native design patterns that became organizational standards

• **Drove end-to-end product delivery from conception to production**, leading technical roadmap planning, sprint coordination, and stakeholder communication while maintaining hands-on development responsibilities

• **Mentored 2 junior engineers through comprehensive onboarding program**, conducting code reviews, pair programming sessions, and creating technical documentation that reduced ramp-up time by 40%

• **Architected distributed state machine solution for NBUS workflow orchestration**, enabling parallel development across multiple teams and reducing project timeline by 3 months through modular design

### Senior Software Engineer (May 2024 - Present)

• **Led technical migration of Peril Based Risk Information Tool** serving 15+ insurance product lines to cloud-native ROSA platform, eliminating years of technical debt while maintaining 100% uptime for critical underwriting operations

• **Implemented and mandated blue/green deployment strategy** across entire engineering organization, establishing zero-downtime deployment process with automated rollback capabilities that reduced production incidents by 75%

• **Architected comprehensive testing framework for blue/green deployments**, including automated smoke tests, canary analysis, and production validation checks ensuring robust checkout functionality before traffic cutover

• **Established production-readiness standards and deployment gates**, creating reusable deployment templates and runbooks adopted by multiple teams for consistent, reliable releases

• **Enhanced developer productivity through debugging tools and local development improvements**, implementing debug mode and authentication mocking that reduced local setup time from hours to minutes

• **Modernized authentication architecture by migrating from legacy LDAP/cookie-based system** to Azure AD with MFA, improving security posture and enabling SSO across enterprise applications while maintaining backward compatibility

• **Led complete authentication system overhaul**, removing legacy cookie-based session management and implementing stateless JWT token architecture with Azure AD integration, reducing authentication-related incidents by 90%

## Key Strengths Demonstrated

### Technical Excellence

- **Technology Stack Modernization**: Spring Boot → TypeScript/React/Node.js
- **Cloud Migration Expertise**: On-premises → AWS (Lambda, API Gateway, RDS, CloudFormation)
- **Platform Experience**: Traditional servers → Containerized (ROSA/OpenShift on AWS)
- **Architecture Patterns**: Monolithic → Microservices, State Machines, Event-Driven

### Leadership & Impact

- **Scale**: Systems handling 40M+ API calls annually
- **Mentorship**: Direct guidance of junior engineers
- **Cross-team Influence**: Technical consultation for 7+ teams
- **Process Innovation**: Blue/green deployments, CI/CD standardization

### Business Value

- **Zero-downtime migrations** for business-critical systems
- **Accelerated delivery timelines** through parallel development strategies
- **Reduced operational costs** through cloud-native architectures
- **Improved system reliability** through modern deployment practices
