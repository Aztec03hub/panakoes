/**
 * @name AUTH_JWT_* env var read outside services/auth
 * @description Per ADR-039 / ADR-041, only the auth service signs JWTs and
 *              may read AUTH_JWT_* env vars (signing key material). Every
 *              other service is a JWT validator and must read JWT_*
 *              variables instead. A reader outside `services/auth/` that
 *              reaches for `AUTH_JWT_*` is the env-var prefix mismatch
 *              that caused PR #218 (validator silently fell back to a
 *              dev-default key).
 * @kind problem
 * @problem.severity error
 * @security-severity 7.5
 * @precision high
 * @id panakoes/jwt-env-var-prefix-mismatch
 * @tags security
 *       panakoes
 *       jwt
 *       configuration
 */

import python

from Call c, StrConst key
where
  (
    // os.environ['AUTH_JWT_*'] / os.environ.get('AUTH_JWT_*')
    c.getFunc().(Attribute).getName() in ["get", "__getitem__"] and
    c.getArg(0) = key
    or
    // os.getenv('AUTH_JWT_*')
    c.getFunc().(Attribute).getName() = "getenv" and
    c.getArg(0) = key
  ) and
  key.getText().matches("AUTH\\_JWT\\_%") and
  not c.getLocation().getFile().getRelativePath().matches("services/auth/%") and
  not c.getLocation().getFile().getRelativePath().matches(".github/codeql/test-fixtures/%")
select c, "Reads AUTH_JWT_* env var '" + key.getText() + "' outside services/auth/. Validators must use JWT_* (ADR-039/041)."
