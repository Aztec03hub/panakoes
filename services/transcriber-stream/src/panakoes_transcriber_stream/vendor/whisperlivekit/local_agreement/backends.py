# Vendored from QuentinFuxa/WhisperLiveKit (Apache-2.0).
# Upstream path: whisperlivekit/local_agreement/backends.py
#
# MODIFIED (Panakoes vendor lift, see vendor/README.md):
#   1. Removed MLXWhisper + WhisperASR + OpenaiApiASR classes; we only ship
#      FasterWhisperASR + base ASRBase. (Mod #1 in vendor/README.md.)
#   3. FasterWhisperASR.__init__ accepts ``condition_on_previous_text``
#      (default False) and ``beam_size`` (default 1) constructor args;
#      transcribe() reads them so streaming partial stability with
#      LocalAgreement-2 is preserved without flapping. (Mods #3 + #4.)
#   6. FasterWhisperASR.load_model routes a directory through
#      ``WhisperModel(model_size_or_path=<full path>, local_files_only=True)``
#      so an AMI-baked weights directory at /opt/whisper/models/<size>-ct2
#      does NOT trigger a HuggingFace fallback download. (Mod #6.)
import logging
import sys
from typing import List

import numpy as np

from ..model_paths import detect_model_format, resolve_model_path
from ..timed_objects import ASRToken

logger = logging.getLogger(__name__)


class ASRBase:
    sep = " "  # join transcribe words with this character (" " for whisper_timestamped,
              # "" for faster-whisper because it emits the spaces when needed)

    def __init__(
        self,
        lan,
        model_size=None,
        cache_dir=None,
        model_dir=None,
        lora_path=None,
        logfile=sys.stderr,
    ):
        self.logfile = logfile
        self.transcribe_kargs = {}
        self.lora_path = lora_path
        if lan == "auto":
            self.original_language = None
        else:
            self.original_language = lan
        self.model = self.load_model(model_size, cache_dir, model_dir)

    def load_model(self, model_size, cache_dir, model_dir):
        raise NotImplementedError("must be implemented in the child class")

    def transcribe(self, audio, init_prompt=""):
        raise NotImplementedError("must be implemented in the child class")

    def use_vad(self):
        raise NotImplementedError("must be implemented in the child class")


class FasterWhisperASR(ASRBase):
    """Uses faster-whisper as the backend.

    MODIFIED (Mods #3 + #4 + #6): see module-level header for the contract.
    """

    sep = ""

    def __init__(
        self,
        lan,
        model_size=None,
        cache_dir=None,
        model_dir=None,
        lora_path=None,
        logfile=sys.stderr,
        # Mod #3: streaming-stability default. Upstream hardcoded True.
        condition_on_previous_text: bool = False,
        # Mod #4: greedy decode by default. Upstream hardcoded beam_size=5.
        beam_size: int = 1,
    ):
        self._condition_on_previous_text = condition_on_previous_text
        self._beam_size = beam_size
        super().__init__(
            lan=lan,
            model_size=model_size,
            cache_dir=cache_dir,
            model_dir=model_dir,
            lora_path=lora_path,
            logfile=logfile,
        )

    def load_model(self, model_size=None, cache_dir=None, model_dir=None):
        from faster_whisper import WhisperModel

        if model_dir is not None:
            # Mod #6: route AMI-baked path through model_size_or_path AS A
            # FULL PATH and ``local_files_only=True``. The upstream code
            # passes the path string but does not set local_files_only, so a
            # missing or oddly-laid-out directory would silently HF-fallback.
            resolved_path = resolve_model_path(model_dir)
            logger.debug(
                "Loading faster-whisper model from %s (mod #6: local_files_only=True; "
                "model_size and cache_dir parameters are not used).",
                resolved_path,
            )
            model_size_or_path = str(resolved_path)
            return WhisperModel(
                model_size_or_path,
                device="auto",
                compute_type="auto",
                local_files_only=True,
            )

        if model_size is None:
            raise ValueError("Either model_size or model_dir must be set")

        # No baked directory: this is the dev / HF-fallback path. Upstream's
        # default behavior is fine here.
        return WhisperModel(
            model_size,
            device="auto",
            compute_type="auto",
            download_root=cache_dir,
        )

    def transcribe(self, audio: np.ndarray, init_prompt: str = "") -> list:
        # Mods #3 + #4: read condition_on_previous_text + beam_size from
        # constructor-set instance state so the vendor lift is the single
        # place that owns the policy.
        segments, info = self.model.transcribe(
            audio,
            language=self.original_language,
            initial_prompt=init_prompt,
            beam_size=self._beam_size,
            word_timestamps=True,
            condition_on_previous_text=self._condition_on_previous_text,
            **self.transcribe_kargs,
        )
        return list(segments)

    def ts_words(self, segments) -> List[ASRToken]:
        tokens = []
        for segment in segments:
            if segment.no_speech_prob > 0.9:
                continue
            for word in segment.words:
                token = ASRToken(word.start, word.end, word.word, probability=word.probability)
                tokens.append(token)
        return tokens

    def segments_end_ts(self, segments) -> List[float]:
        return [segment.end for segment in segments]

    def use_vad(self):
        self.transcribe_kargs["vad_filter"] = True
