"""panakoes-transcriber-stream: GPU-side streaming transcription container.

A long-running container that runs on a session-spawned g4dn.xlarge Spot
instance. It polls a per-session SQS audio-frame queue, feeds 200 ms PCM
frames into a vendored LocalAgreement-2 OnlineASRProcessor wrapped around
faster-whisper-large, and emits partial and final transcripts over the
API Gateway WebSocket management API.

See ``services/transcriber-stream/README.md`` and
``docs/design/realtime-streaming-transcription.md`` (design v7) for the
full contract.
"""

__version__ = "0.1.0"
