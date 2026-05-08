"""Panakoes GPU Spawner microservice.

Wraps `ec2:RunInstances` and `ec2:TerminateInstances` to spawn a per
session g4dn.xlarge Spot instance (built from the streaming-transcriber
Packer AMI) for live WebSocket transcription. Lifecycle is owned by the
session-manager, which calls this service over HTTP with a service JWT.

The service has compute-spawning authority. The IAM role attached to it
in `infra/dev/iam` constrains `ec2:RunInstances` by instance type, AMI,
and tag-on-create requirements (`Project=panakoes`,
`Spawner=panakoes-dev-gpu-spawner`); `ec2:TerminateInstances` is gated
to instances that already carry those tags. This service replicates the
tag-based ownership check at the application layer as defence in depth
against confused-deputy attacks.
"""
