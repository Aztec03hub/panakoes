---
category: Fixed
---

- `services/admin`: `/realtime` Transcript card stays visible after the session transitions to `ended` or `failed`, even when no `partial` or `final` was ever received. Previously the card was unmounted on terminal status, so a session that ended without producing any transcript (silent recording, too-short audio for LocalAgreement-2 to commit, or backend failure) left the user staring at a vanished card and no explanation. The card now renders for every non-`idle` status and the empty-state copy explains the most common cause when the session ends without segments.
