# Em-dash scanner fixture (synthesized at test time)

This file cannot ship literal U+2014 or U+2013 characters because the
repo's pre-commit em-dash detector rejects them. The local test driver
described in `.github/codeql/README.md` derives a `triggering.runtime.md`
by substituting the markers `<<EM>>` and `<<EN>>` with the corresponding
characters before invoking `bash .github/codeql/scripts/scan-em-dash.sh`.

This sentence has an em dash <<EM>> right here.
And an en dash <<EN>> right here.
