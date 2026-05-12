# Fixture for panakoes/em-dash-in-source.
#
# This fixture cannot contain literal U+2014 / U+2013 characters because
# the repo's pre-commit em-dash detector (scripts/check_no_em_dashes.sh)
# would reject the commit. Instead, the fixture builds the chars at
# runtime via chr(); local test instructions in .github/codeql/README.md
# describe how to generate a derived `.runtime.py` containing the
# literal characters before invoking `codeql test run`.
EM = chr(0x2014)
EN = chr(0x2013)
TRIGGERING = "em dash " + EM + " here"
TRIGGERING_EN = "en dash " + EN + " here"
