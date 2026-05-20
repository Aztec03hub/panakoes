# Vendored from QuentinFuxa/WhisperLiveKit (Apache-2.0).
# Upstream path: whisperlivekit/backend_support.py
#
# MODIFIED (Panakoes vendor lift, see vendor/README.md):
#   1. Trimmed mlx_backend_available / voxtral_hf_backend_available; this
#      service only ever runs the faster-whisper backend (Linux + CUDA).
import importlib.util
import logging
import platform

logger = logging.getLogger(__name__)


def module_available(module_name):
    """Return True if the given module can be imported."""
    return importlib.util.find_spec(module_name) is not None


def faster_backend_available(warn_on_missing=False):
    available = module_available("faster_whisper")
    if not available and warn_on_missing and platform.system() != "Darwin":
        logger.warning(
            "=" * 50
            + "\nFaster-Whisper not found. Consider installing faster-whisper "
              "for better performance: `pip install faster-whisper`\n"
            + "=" * 50
        )
    return available
