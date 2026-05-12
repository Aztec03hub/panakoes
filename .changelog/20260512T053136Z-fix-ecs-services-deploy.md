---
category: Fixed
---

- `infra/dev/ecs`: dedupe `summarization_image_tag`, `notification_image_tag`, `session_manager_image_tag`, `billing_image_tag` variables (block from PR #229 conflict-resolution comment left duplicate declarations that broke `terraform validate`); pin each to the freshly baked `initial-90be43b` multi-arch image so `terraform apply` lands the 4 placeholder services with a real ECR tag.
