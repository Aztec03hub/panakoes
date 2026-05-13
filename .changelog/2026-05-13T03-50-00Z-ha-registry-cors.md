### Fixed
- Health-aggregator registry trimmed to only deployed ECS services (auth, admin-api, cost-api). The 9 undeployed services were producing "ecs service not found / Unknown" noise on the dashboard.
- Added OPTIONS handler for `/services/{name}` so the "View details" preflight no longer fails CORS and returns "Failed to fetch".