---
category: Fixed
---

- `services/gpu-spawner`: fix EventBridge consumer lifespan wiring. `GpuInstanceManager.__init__()` takes individual settings fields, not a `Settings` object; the PR #454 fix-commit incorrectly called `GpuInstanceManager(client=ec2_client, settings=settings)` which crashed the container at FastAPI startup with `TypeError: unexpected keyword argument 'settings'`. Now mirrors the dependency-injected pattern in `routes/spawn.py:get_instance_manager`.
