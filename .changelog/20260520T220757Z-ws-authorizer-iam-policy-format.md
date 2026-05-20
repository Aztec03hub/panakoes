---
category: Fixed
---

- `services/ws-authorizer`: return the IAM-policy authorizer response shape required by API Gateway v1 WebSocket APIs. The previous `{"isAuthorized": ...}` shape is HTTP API v2 only; WebSocket APIs reject it with `AUTHORIZER_CONFIGURATION_ERROR: Invalid JSON in response: Unrecognized field "isAuthorized"` and return a 500 BEFORE the integration is invoked. Now returns `{principalId, policyDocument: {Statement: [{Effect: Allow|Deny, Action: execute-api:Invoke, Resource: <methodArn>}]}, context: {...}}`. The downstream streaming-router already reads the authorizer context under both `requestContext.authorizer.lambda` and `requestContext.authorizer` directly, so no change is needed there.
