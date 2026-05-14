---
category: Changed
---

- `infra/dev`: remove 16 VPC Interface Endpoints (replaced by NAT Gateway routing), both WAF WebACLs (protecting zero resources), and Aurora Serverless V2 cluster (superseded by RDS db.t4g.micro in PR #314); eliminates approximately $358/month in gross AWS charges
