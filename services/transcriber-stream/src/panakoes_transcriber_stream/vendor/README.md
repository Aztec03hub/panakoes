# Vendored third-party code

This subtree holds source vendored from upstream projects under their
original licenses. Any change to a file under this directory MUST be
reflected in both ``services/transcriber-stream/NOTICE`` (Apache-2.0
section 4 attribution) and in the modification list below. The vendor
NOTICE drift test (``tests/unit/test_vendor_attribution.py``) parses
both files at CI time and fails the build if either side drifts.

## Inventory

| Path (under this README) | Upstream | License | Modifications |
|---|---|---|---|
| `whisperlivekit/__init__.py` | NEW (vendor marker) | Apache-2.0 | n/a |
| `whisperlivekit/timed_objects.py` | WhisperLiveKit | Apache-2.0 | verbatim |
| `whisperlivekit/model_paths.py` | WhisperLiveKit | Apache-2.0 | verbatim |
| `whisperlivekit/silero_vad_iterator.py` | WhisperLiveKit | Apache-2.0 | verbatim |
| `whisperlivekit/warmup.py` | WhisperLiveKit | Apache-2.0 | verbatim |
| `whisperlivekit/backend_support.py` | WhisperLiveKit | Apache-2.0 | MOD #1 |
| `whisperlivekit/local_agreement/__init__.py` | NEW (empty) | Apache-2.0 | n/a |
| `whisperlivekit/local_agreement/online_asr.py` | WhisperLiveKit | Apache-2.0 | MOD #7 |
| `whisperlivekit/local_agreement/backends.py` | WhisperLiveKit | Apache-2.0 | MODS #1, #3, #4, #6 |
| `whisperlivekit/local_agreement/whisper_online.py` | WhisperLiveKit | Apache-2.0 | MODS #1, #5, #7 |
| `whisperlivekit/silero_vad_models/silero_vad_16k_op15.onnx` | silero-vad (via WhisperLiveKit) | Apache-2.0 | verbatim binary |

## Upstream

- Project: https://github.com/QuentinFuxa/WhisperLiveKit
- Pinned commit SHA: `adaefeb26dba8f8f5eaa2af8b25f4a2740adf999`
- Vendor lift date (UTC): 2026-05-20

## Modifications

These are the exact transformations applied at vendor lift. The
canonical NOTICE file's modification list MUST match this list one-to-one;
the vendor-attribution test ``tests/unit/test_vendor_attribution.py``
diffs the two and fails the build if either drifts.

1. **Trim non-faster-whisper backend branches.** Removed
   ``MLXWhisper``, ``WhisperASR``, ``OpenaiApiASR`` classes from
   ``backends.py``. Removed mlx-whisper / openai-api / vLLM / Voxtral /
   Qwen branches and their imports from
   ``whisper_online.py:_normalize_backend_choice``.
   ``backend_support.py`` retains only ``faster_backend_available``.
2. (Reserved.) Future structured-logging hook. The current vendor lift
   keeps upstream's ``logging.getLogger`` usage because it is already
   compatible with our log-level wiring; no change at this lift.
3. ``FasterWhisperASR`` exposes ``condition_on_previous_text`` as a
   constructor arg defaulting to ``False`` (was hardcoded ``True``).
   Required for streaming partial stability with LocalAgreement-2.
4. ``FasterWhisperASR`` exposes ``beam_size`` as a constructor arg
   defaulting to ``1`` (greedy) instead of upstream's hardcoded ``5``.
   Streaming-latency budget requires greedy decoding.
5. ``backend_factory`` calls ``asr.use_vad()`` unconditionally after the
   asr instantiation so faster-whisper's bundled Silero VAD filter
   activates on every inference call.
6. ``FasterWhisperASR.load_model`` routes a directory through
   ``WhisperModel(model_size_or_path=<full path>, local_files_only=True)``
   so an AMI-baked weights directory does NOT trigger a HuggingFace
   fallback download.
7. Rewrote vendor-internal imports from upstream's top-level
   ``whisperlivekit`` namespace to package-relative imports so the
   subtree resolves under ``panakoes_transcriber_stream.vendor``.

## Bump procedure

To resync against a newer upstream:

1. Fetch the new commit SHA and update the "Upstream" section above.
2. Re-copy each file in the inventory verbatim from the new SHA.
3. Re-apply each numbered modification above. Each MOD comment in the
   vendored source identifies the patch sites by number, so a
   ``grep -nR 'MOD #' src/panakoes_transcriber_stream/vendor`` enumerates
   every site that needs to be touched.
4. Update the NOTICE file's modification list to match.
5. Run ``pytest tests/unit/test_vendor_attribution.py`` to confirm both
   files agree.
