---
category: Added
---

- `infra/dev/auth-db-rds`: new Terraform module provisioning an RDS PostgreSQL 16 instance (`db.t4g.micro`, single-AZ, gp3, 20 GB, AWS Free Tier-eligible) as the going-forward home for Better-Auth's tables. Replaces the Aurora Serverless v2 module at `infra/dev/auth-db` for the auth workload specifically. Motivation: measured ~11.6 s cold-start on the Aurora cluster's resume-from-pause sequence (`min_capacity_acu = 0`) and the auth workload doesn't use Aurora's auto-scaling capabilities. RDS is always-on, $0/mo for 12 months on Free Tier, then ~$12/mo. Aurora module remains in the repo during the 7-day burn-in window and is decommissioned in a follow-up PR.
