#!/usr/bin/env bash
# install-runtime.sh
#
# Pulls the panakoes streaming-transcriber container image from the
# environment-scoped ECR repository so that boot-time session warmup skips
# the multi-second image-pull leg.
#
# Required environment variables (set by the Packer provisioner):
#   ECR_ACCOUNT_ID   AWS account hosting the ECR repository.
#   ECR_IMAGE_URI    Full image URI to pull (account.dkr.ecr.region.amazonaws.com/repo:tag).
#   AWS_REGION       Region of the ECR repository.
#
# Behavior:
#   - Authenticates Docker against ECR via `aws ecr get-login-password`.
#     The build instance role must allow `ecr:GetAuthorizationToken` and
#     `ecr:BatchGetImage` against the target repository ARN.
#   - Pulls the image into the local Docker daemon. Subsequent `docker run`
#     invocations on instances launched from the AMI find the image in
#     /var/lib/docker and skip the network pull.
#   - If the pull fails (image does not yet exist, transient ECR error, IAM
#     scope mismatch), logs a warning and exits 0 so the AMI still bakes.
#     Rationale: during early scaffolding the transcriber-stream image is
#     not yet built; we want this AMI to exist as the base for subsequent
#     work, not to block on a chicken-and-egg dependency.
#
# Exit codes:
#   0   Image pulled successfully OR pull failed but bake should continue.
#   1   A required env var is missing.

set -euo pipefail

log() {
    printf '[install-runtime] %s\n' "$*"
}

require_var() {
    local name="$1"
    if [ -z "${!name:-}" ]; then
        echo "[install-runtime] ERROR: required env var $name is unset" >&2
        exit 1
    fi
}

require_var ECR_ACCOUNT_ID
require_var ECR_IMAGE_URI
require_var AWS_REGION

ECR_REGISTRY="${ECR_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com"

log "Authenticating Docker to ECR registry $ECR_REGISTRY"
if ! aws ecr get-login-password --region "$AWS_REGION" \
        | sudo docker login \
            --username AWS \
            --password-stdin \
            "$ECR_REGISTRY"; then
    log "WARNING: ECR login failed. Proceeding without runtime image pre-pull."
    log "Likely causes: build instance role missing ecr:GetAuthorizationToken,"
    log "or the dev ECR repository has not been provisioned yet."
    exit 0
fi

log "Pulling transcriber image: $ECR_IMAGE_URI"
if ! sudo docker pull "$ECR_IMAGE_URI"; then
    log "WARNING: docker pull failed for $ECR_IMAGE_URI. Continuing AMI bake."
    log "The image will be pulled on first session launch instead of from the AMI cache."
    log "This adds approximately 5 to 30 seconds to the first-session warmup."
    exit 0
fi

log "Image $ECR_IMAGE_URI is now baked into the AMI."

# Drop ECR credentials from the AMI; they are short-lived but should not be
# persisted into a snapshot regardless. /home/ubuntu/.docker/config.json is
# the default location of the docker login credential store on Ubuntu.
sudo rm -f /root/.docker/config.json
sudo rm -f /home/ubuntu/.docker/config.json

log "Runtime image pre-pull complete."
