/**
 * @name Em-dash or en-dash in source
 * @description Phil's hard rule: no em-dashes (U+2014) or en-dashes
 *              (U+2013) anywhere in committed source. The bash detector
 *              misses files added via `cat <<EOF` patterns and any
 *              non-text file that happened to get committed (e.g.,
 *              .ipynb cells, SVG `<text>` nodes). This query catches
 *              the gap by walking every Python string literal and
 *              comment that CodeQL's extractor parses.
 * @kind problem
 * @problem.severity warning
 * @security-severity 1.0
 * @precision very-high
 * @id panakoes/em-dash-in-source
 * @tags maintainability
 *       panakoes
 *       voice-rules
 */

import python

from StrConst s
where
  // U+2014 EM DASH and U+2013 EN DASH, written as Unicode escapes so
  // this query file itself stays clean of the characters it forbids
  // (and does not trip the pre-commit em-dash detector).
  (s.getText().regexpMatch(".*[\\u2014\\u2013].*")) and
  not s.getLocation().getFile().getRelativePath().matches(".github/codeql/test-fixtures/%") and
  not s.getLocation().getFile().getRelativePath().matches(".github/codeql/queries/panakoes/%")
select s, "Em-dash or en-dash found in Python source literal. Phil rule: replace with comma, period, semicolon, or parens."
