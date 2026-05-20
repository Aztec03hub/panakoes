# Gold-standard audio test fixtures

Audio files with known transcripts, used by Playwright e2e tests and
manual smoke checks of the upload + transcription + summarization flow.

The Whisper-large-v3 transcript output should be substring-equal to the
contents of the matching `<name>.transcript.txt` file (modulo trailing
punctuation and whitespace differences). E2E assertions should use a
case-insensitive substring match on a few high-confidence words rather
than a full equality check, since the model occasionally varies casing
and trailing punctuation.

| File | Source | Known transcript | Length |
|---|---|---|---|
| `panakoes-test-recording.mp3` | Recorded by Phil 2026-05-19 | "Here is a test recording. Testing, 1, 2, 3... For Panakoes." | ~3s |

When adding a new fixture:
1. Drop the audio file here.
2. Drop a matching `<name>.transcript.txt` with the human-verified transcript.
3. Update this table.
4. If used by an automated test, link the test file in the table's source column.
