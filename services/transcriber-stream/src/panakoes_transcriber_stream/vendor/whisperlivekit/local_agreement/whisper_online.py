#!/usr/bin/env python3
# Vendored from QuentinFuxa/WhisperLiveKit (Apache-2.0).
# Upstream path: whisperlivekit/local_agreement/whisper_online.py
#
# MODIFIED (Panakoes vendor lift, see vendor/README.md):
#   1. Removed mlx-whisper / openai-api / vLLM / Voxtral / Qwen branches and
#      their imports; this service ships faster-whisper only.
#   5. backend_factory now calls ``asr.use_vad()`` UNCONDITIONALLY after
#      the asr instantiation so faster-whisper's bundled Silero VAD
#      filter activates on every inference call. (Architect IMP-04 +
#      adversarial CRIT-03; without this the anti-hallucination claim is
#      non-functional.)
#   7. Imports rewritten to package-relative form.
import logging
import time

from ..backend_support import faster_backend_available
from ..model_paths import detect_model_format, resolve_model_path
from ..warmup import warmup_asr
from .backends import FasterWhisperASR

logger = logging.getLogger(__name__)


WHISPER_LANG_CODES = (
    "af,am,ar,as,az,ba,be,bg,bn,bo,br,bs,ca,cs,cy,da,de,el,en,es,et,eu,fa,fi,"
    "fo,fr,gl,gu,ha,haw,he,hi,hr,ht,hu,hy,id,is,it,ja,jw,ka,kk,km,kn,ko,la,lb,"
    "ln,lo,lt,lv,mg,mi,mk,ml,mn,mr,ms,mt,my,ne,nl,nn,no,oc,pa,pl,ps,pt,ro,ru,"
    "sa,sd,si,sk,sl,sn,so,sq,sr,su,sv,sw,ta,te,tg,th,tk,tl,tr,tt,uk,ur,uz,vi,"
    "yi,yo,zh"
).split(",")


def create_tokenizer(lan):
    """Return an object exposing ``split(text) -> list[str]``.

    MOD #1 simplification: we only ever transcribe a known set of languages
    in the Panakoes runtime path. The full upstream dispatch table tried to
    cover every language; we keep the practically-useful branches and fall
    back to a no-op tokenizer for anything obscure rather than pulling
    ``wtpsplit`` and ``tokenize_uk`` into the container image.
    """

    assert (
        lan in WHISPER_LANG_CODES
    ), "language must be Whisper's supported lang code: " + " ".join(WHISPER_LANG_CODES)

    # mosestokenizer covers the practical hot path. If it is not installed,
    # the caller should pass buffer_trimming="segment" (which sets tokenizer
    # to None upstream of this function) so this branch is never taken.
    try:
        from mosestokenizer import MosesSentenceSplitter
    except ImportError:
        class _NoopTokenizer:
            def split(self, sent):
                return [sent]

        return _NoopTokenizer()

    if lan in (
        "as bn ca cs de el en es et fi fr ga gu hi hu is it kn lt lv ml mni mr "
        "nl or pa pl pt ro ru sk sl sv ta te yue zh"
    ).split():
        return MosesSentenceSplitter(lan)

    class _NoopTokenizer:
        def split(self, sent):
            return [sent]

    return _NoopTokenizer()


def backend_factory(
    backend,
    lan,
    model_size,
    model_cache_dir,
    model_dir,
    model_path,
    lora_path,
    direct_english_translation,
    buffer_trimming,
    buffer_trimming_sec,
    confidence_validation,
    warmup_file=None,
    min_chunk_size=None,
):
    """Build a configured FasterWhisperASR instance.

    Returns a single asr object (not a tuple); ``OnlineASRProcessor`` is
    constructed separately by the caller (see the service's
    ``asr_proxy.SeededOnlineASRProcessor``).
    """

    backend_choice = backend
    custom_reference = model_path or model_dir
    resolved_root = None
    has_fw_weights = False
    has_pytorch = False

    if custom_reference:
        resolved_root = resolve_model_path(custom_reference)
        if resolved_root.is_dir():
            model_info = detect_model_format(resolved_root)
            has_fw_weights = model_info.compatible_faster_whisper
            has_pytorch = model_info.has_pytorch
        else:
            has_pytorch = True

    # MOD #1: the only backend we ship is faster-whisper. We honor the
    # ``backend`` argument for upstream-compat but treat anything other
    # than "faster-whisper" as an error rather than silently falling
    # through to an unimplemented branch.
    backend_choice = _normalize_backend_choice(
        backend_choice,
        resolved_root,
        has_fw_weights,
    )

    asr_cls = FasterWhisperASR
    if resolved_root is not None and not resolved_root.is_dir():
        raise ValueError(
            "Faster-Whisper backend expects a directory with CTranslate2 weights."
        )
    model_override = str(resolved_root) if resolved_root is not None else None

    t = time.time()
    logger.info(
        "Loading Whisper %s model for language %s using backend %s...",
        model_size,
        lan,
        backend_choice,
    )
    asr = asr_cls(
        lan=lan,
        model_size=model_size,
        cache_dir=model_cache_dir,
        model_dir=model_override,
        lora_path=lora_path,
    )
    e = time.time()
    logger.info("done. It took %s seconds.", round(e - t, 2))

    if direct_english_translation:
        tgt_language = "en"
        asr.transcribe_kargs["task"] = "translate"
    else:
        tgt_language = lan

    if buffer_trimming == "sentence":
        tokenizer = create_tokenizer(tgt_language)
    else:
        tokenizer = None

    # MOD #5: ALWAYS enable VAD. Upstream gates this on a CLI flag; for the
    # Panakoes streaming path we always want vad_filter=True on the
    # inference path (anti-hallucination on silence).
    asr.use_vad()

    warmup_asr(asr, warmup_file)

    asr.confidence_validation = confidence_validation
    asr.tokenizer = tokenizer
    asr.buffer_trimming = buffer_trimming
    asr.buffer_trimming_sec = buffer_trimming_sec
    asr.backend_choice = backend_choice
    return asr


def _normalize_backend_choice(preferred_backend, resolved_root, has_fw_weights):
    """MOD #1: faster-whisper is the only backend this vendor lift supports."""

    if preferred_backend in (None, "auto", "faster-whisper"):
        if not faster_backend_available():
            raise RuntimeError(
                "faster-whisper backend requested but faster-whisper is not installed."
            )
        if resolved_root is not None and not has_fw_weights:
            raise FileNotFoundError(
                f"faster-whisper backend requested but no Faster-Whisper weights "
                f"were found under {resolved_root}"
            )
        return "faster-whisper"

    raise ValueError(
        f"Backend '{preferred_backend}' is not supported by the Panakoes vendor "
        f"lift. Only 'faster-whisper' is shipped."
    )
