"""Thin subclass of the vendored ``OnlineASRProcessor``.

Design v7 DEG-01 fix: the v5 design seeded prompt context by synthesizing
fake ``ASRToken`` entries with ``start=-1.0, end=-0.5`` and extending
``online.committed``. That approach has two fragile corner cases the
adversarial review caught:

* ``process_iter()``'s freeze-prevention reset (``self.init(offset=...)``)
  wipes ``self.committed = []``.
* ``chunk_completed_segment()`` computes ``last_committed_time = -0.5``
  from the synthetic-only committed list and calls ``chunk_at(-0.5)``
  which truncates ``audio_buffer`` to its last 0.5 sec and sets
  ``buffer_time_offset = -0.5``.

The cleaner approach (and the one the design now mandates) is to
subclass ``OnlineASRProcessor`` and override ``prompt()`` so the seed
text is INJECTED INTO THE PROMPT only -- never into ``committed``. The
seed survives every reset path the upstream class can take because
``committed`` never sees it.
"""

from __future__ import annotations

import sys

from .vendor.whisperlivekit.local_agreement.online_asr import OnlineASRProcessor


class SeededOnlineASRProcessor(OnlineASRProcessor):
    """``OnlineASRProcessor`` with a one-shot prompt seed.

    The seed string is prepended to the prompt returned by upstream's
    ``prompt()`` helper. It does NOT enter ``committed`` / ``audio_buffer``
    / anywhere else, so it survives reset paths and segment-trimming.

    The seed is consumed only while ``committed`` is empty; once the new
    session has its own committed history, the seed is dropped so it
    cannot leak into the long-running transcript.
    """

    def __init__(self, asr, prompt_seed_text: str | None = None, logfile=sys.stderr):
        super().__init__(asr, logfile=logfile)
        self._prompt_seed = (prompt_seed_text or "").strip()

    def prompt(self) -> tuple[str, str]:
        base_prompt, context = super().prompt()
        if self._prompt_seed and not self.committed:
            # Only seed when no real committed tokens yet. Once the new
            # session has its own committed history, drop the seed.
            seeded = (self._prompt_seed + " " + base_prompt).strip()
            return seeded, context
        return base_prompt, context
