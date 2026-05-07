# AWS Activate Founders Application: LaFayette Labs LLC

Copy-paste source for the AWS Activate Founders application form. Each section maps to a typical form field.

---

## 1. Company Name and Description

**Company:** LaFayette Labs LLC

LaFayette Labs LLC is an Illinois-based technology company filed 2026-04-23, building developer-friendly audio intelligence infrastructure. Our first product is Panakoes, an open-source cloud platform for audio capture, transcription, and conversation insights, which also serves as the cloud backend for an AI wearable device the company is developing in parallel.

---

## 2. Project / Product Description

Panakoes is an open-source cloud audio capture, transcription, and insights platform. It ingests audio (uploaded files or live browser/device streams), transcribes it with Whisper-class models on GPU, and produces structured summaries and searchable insights using Anthropic Claude. The target users are individual professionals, small teams that record meetings or interviews, and developers who want a self-hostable alternative to closed-source tools like Otter or Plaud. Panakoes is also the backend for a forthcoming LaFayette Labs hardware wearable (an open-source pendant recorder forked from the BasedHardware Omi project), so the platform has both standalone-product value and a hardware-attached path to scale.

---

## 3. Technical Architecture Summary

Panakoes runs on AWS in us-east-1 and is fully provisioned via Terraform with remote state in S3, DynamoDB locking, and KMS encryption. The web frontend is a SvelteKit app hosted on S3 behind CloudFront. API Gateway (REST + WebSocket) fronts a set of Python microservices on ECS Fargate, plus a TypeScript auth service implementing Better-Auth that issues JWTs validated by the Python services. Async transcription jobs run on AWS Batch with EC2 g4dn Spot GPU instances executing Whisper-large-v3 at float16; live streaming sessions spawn dedicated g4dn.xlarge Spot workers running faster-whisper-large with Silero VAD. Step Functions orchestrates audio chunking and fan-out, EventBridge handles event routing, and Lambda fills in the lightweight glue. Data lives in RDS Postgres (relational) and DynamoDB (audit log, session state), with raw and processed audio in S3. Secrets Manager and SSM Parameter Store hold configuration; KMS handles encryption keys; IAM and OIDC federation from GitHub Actions handle access (no static keys anywhere). Observability is CloudWatch metrics, logs, and alarms plus X-Ray tracing, instrumented with OpenTelemetry via ADOT, and Athena for log analytics. CloudTrail plus a custom DynamoDB audit table provide the compliance trail. Cost Explorer is wired into the admin dashboard. Container images ship through ECR.

---

## 4. Why We're Building on AWS

Three concrete reasons. First, the workload is GPU-bursty and audio-heavy: EC2 Spot g4dn instances for Whisper inference, S3 for cheap object storage of raw audio, and AWS Batch for queue-driven fan-out is the cleanest combination on any cloud, and Spot pricing brings GPU transcription cost to roughly a tenth of on-demand. Second, the platform needs both serverless edges (API Gateway, Lambda, Step Functions, EventBridge) and stateful workloads (RDS, DynamoDB, ECS Fargate) under one IAM and one observability plane; AWS is the only provider where that full mix is mature and where Terraform coverage is complete enough to manage the whole estate as code. Third, the founder has multi-year production experience on AWS and has already designed the IaC, OIDC federation, and CloudWatch/X-Ray observability stack the project will use, so build velocity is highest here. The wearable backend (forthcoming) will use AWS IoT Core for BLE-bridged device telemetry, which keeps the entire LaFayette Labs platform on a single cloud.

---

## 5. Anticipated AWS Monthly Spend (Next 12 Months)

Numbers are conservative estimates based on architecture sizing, not marketing.

- **Months 1-3 (build + private alpha):** $10 to $30 per month. Most usage falls inside the 12-month free tier (Lambda, DynamoDB on-demand, CloudWatch baseline, S3 first 5 GB, RDS db.t3.micro). GPU spend dominates the rest, kept low by session-spawned Spot g4dn.xlarge that only runs during active transcription.
- **Months 4-6 (public beta, small user base):** $50 to $150 per month. RDS moves off free tier, S3 storage grows with retained audio, GPU minutes rise with concurrent users, CloudFront bandwidth grows.
- **Months 7-12 (paid tiers active, wearable backend integration begins):** $200 to $500 per month at the low end; could climb to roughly $1,500 per month if a public demo or press cycle hits and concurrent sessions spike. Stripe revenue is intended to cover infrastructure beyond this point.
- **Build-week ceiling:** $100 hard cap, enforced via Cost Explorer budget alerts.

---

## 6. How $1,000 in Credits Will Be Deployed

Specific line items, prioritized:

1. **EC2 g4dn Spot GPU (transcription):** ~$400. Covers the bulk of compute through public beta. At Spot pricing of roughly $0.15 per hour for g4dn.xlarge, this is several thousand GPU hours.
2. **RDS Postgres (db.t3.small upgrade post free tier):** ~$200. Buffers months 4-12 when free tier expires.
3. **S3 storage and requests:** ~$150. Raw audio retention for paid tier users plus processed transcripts and summaries.
4. **CloudFront bandwidth:** ~$100. Frontend delivery plus signed URLs for audio playback during a demo or launch.
5. **Reserve / traffic-spike buffer:** ~$150. Held against a public-launch traffic event or an unexpected GPU concurrency spike.

The credits effectively de-risk the full first year of infrastructure cost while paid-tier revenue ramps.

---

## 7. Founder Background

**Phillip LaFayette**, sole founder and managing member of LaFayette Labs LLC. 10+ years as a senior full-stack engineer and solution architect. Currently Senior Software Engineer at Windy City Wire (one-person dev team), titled IT Programmer. Patent-filed IIoT inventor (US20200235608A1, conscious sensor-network platform with energy-harvesting wireless nodes). Stack spans TypeScript, JavaScript, Python, C#, SvelteKit, ASP.NET Core, Node.js, Docker, Linux, AWS, MQTT, PostgreSQL, MySQL, MongoDB, CouchDB, and Redis. Three-plus years of daily AI-augmented development, using Claude Code as primary tooling with custom orchestration patterns. Previously led an 8-person engineering team at MH Electric (2015-2022) in a player-coach capacity. Notable production systems include NavTrack (SvelteKit + ASP.NET Core fleet platform, 30+ vehicles, 99.9% uptime), TrackerAPI (cross-database integration handling 10,000+ daily requests), Savvy-Next (enterprise LMS with Better-Auth, Supabase, and ADP SSO), and a Hughes Network Systems MES built in Python and MySQL with heterogeneous southbound adapters.

---

## 8. Roadmap and Milestones

- **Week 1 (starts ~2026-05-08): Panakoes MVP v0.1.** Both async batch and live streaming pipelines, browser-mic demo, full feature set: auth, upload, transcribe, summarize, retrieve, billing (Stripe 3-tier), notify, audit, telemetry, web frontend, admin dashboard tiers 1, 2, and 3. Full test pyramid, CI/CD via GitHub Actions with OIDC federation, security hygiene baseline (KMS, Secrets Manager, IAM least-privilege, CloudTrail, custom DynamoDB audit log).
- **Weeks 2-4:** Iterate on real-use feedback. Add admin dashboard tier 4 (real-time event stream). Begin integrating wearable BLE-bridged audio pipeline against the same backend.
- **Q3 2026:** Hardware wearable prototype phase begins (forked from BasedHardware Omi, custom FPC with sensiBel SBM100B optical MEMS mic array). Cloud backend already in production.
- **Q4 2026 and beyond:** Wearable hardware fundraising / preorder runway; Panakoes paid-tier growth; potential AWS IoT Core integration for at-scale device fleet telemetry.

---

## Application Metadata

- **Legal entity:** LaFayette Labs LLC
- **State of formation:** Illinois
- **Date filed:** 2026-04-23
- **EIN:** Obtained
- **Business address:** [TBD: confirm Illinois business address to use on the application]
- **Website:** https://lafayettelabs.com (registered and LIVE on Cloudflare Pages as of 2026-05-07)
- **Founder name:** Phillip LaFayette
- **Founder email:** plafaydev@gmail.com
- **Project repository:** https://github.com/Aztec03hub/panakoes (public, MIT-licensed; on Phil's personal GitHub initially, mirrorable to a LaFayette Labs org later)
- **Funding stage:** Bootstrapped, no outside capital
- **AWS account ID:** [TBD: AWS account ID for credit application]
