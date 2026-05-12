"""Tests for the JWKS cache + JWKS-backed validator path.

The fixtures generate a real RSA key locally, expose the public half
as a JWKS document, and sign tokens with the private half via
python-jose. This gives a real cryptographic round-trip with no
network dependency.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jose import jwk as jose_jwk
from jose import jwt
from jose.constants import ALGORITHMS

from panakoes_auth_client import JwksJwtValidator, JwtInvalidError, JwtValidator
from panakoes_auth_client.errors import JwtConfigError
from panakoes_auth_client.jwks import JwksCache

ISSUER = "https://auth.test.panakoes.com"
AUDIENCE = "panakoes-test-api"
KID = "test-kid-jwks"


@pytest.fixture
def rsa_keypair_pem() -> tuple[str, str]:
    """Generate a 2048-bit RSA keypair and return (private_pem, public_pem)."""
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")
    public_pem = (
        private_key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode("utf-8")
    )
    return private_pem, public_pem


@pytest.fixture
def jwks_document(rsa_keypair_pem: tuple[str, str]) -> dict[str, Any]:
    """Build a JWKS-shaped dict from the test public key."""
    _, public_pem = rsa_keypair_pem
    jwk_obj = jose_jwk.construct(public_pem, ALGORITHMS.RS256)
    jwk_dict: dict[str, Any] = jwk_obj.to_dict()
    # python-jose's `to_dict` produces n + e + kty as bytes-like; normalise
    # to str so the cache lookups work.
    for key, value in list(jwk_dict.items()):
        if isinstance(value, bytes):
            jwk_dict[key] = value.decode("ascii")
    jwk_dict.update({"alg": "RS256", "use": "sig", "kid": KID})
    return {"keys": [jwk_dict]}


@pytest.fixture
def make_rs_token(rsa_keypair_pem: tuple[str, str]) -> Callable[..., str]:
    """Build an RS256-signed token; defaults match the standard payload."""
    private_pem, _ = rsa_keypair_pem

    def _make(
        *,
        overrides: dict[str, Any] | None = None,
        kid: str | None = KID,
        issuer: str = ISSUER,
        audience: str = AUDIENCE,
        ttl_seconds: int = 300,
    ) -> str:
        issued_at = int(time.time())
        payload: dict[str, Any] = {
            "sub": "user_jwks",
            "iss": issuer,
            "aud": audience,
            "iat": issued_at,
            "exp": issued_at + ttl_seconds,
        }
        if overrides:
            payload.update(overrides)
        headers: dict[str, Any] = {}
        if kid is not None:
            headers["kid"] = kid
        return jwt.encode(payload, private_pem, algorithm="RS256", headers=headers)

    return _make


@pytest.fixture
def fetcher_counter() -> Iterator[dict[str, int]]:
    """Tracks how many times a `JwksFetcher` stub was called."""
    counter = {"calls": 0}
    yield counter


@pytest.mark.unit
def test_jwks_cache_caches_until_ttl(
    jwks_document: dict[str, Any], fetcher_counter: dict[str, int]
) -> None:
    """First call fetches; second call within TTL is a cache hit."""
    clock = [0.0]

    def fetcher(_url: str) -> dict[str, Any]:
        fetcher_counter["calls"] += 1
        return jwks_document

    cache = JwksCache(
        "https://auth.test/.well-known/jwks.json",
        ttl_seconds=10,
        fetcher=fetcher,
        now=lambda: clock[0],
    )

    cache.get(KID)
    cache.get(KID)
    assert fetcher_counter["calls"] == 1

    clock[0] = 15.0
    cache.get(KID)
    assert fetcher_counter["calls"] == 2


@pytest.mark.unit
def test_jwks_cache_misses_force_refresh(
    jwks_document: dict[str, Any], fetcher_counter: dict[str, int]
) -> None:
    """An unknown kid forces one extra refresh before raising."""
    def fetcher(_url: str) -> dict[str, Any]:
        fetcher_counter["calls"] += 1
        return jwks_document

    cache = JwksCache("https://auth.test/.well-known/jwks.json", fetcher=fetcher)

    with pytest.raises(JwtInvalidError, match="no key for kid"):
        cache.get("unknown-kid")

    # 1 initial fetch on first get, 1 refresh after the miss = 2 total.
    assert fetcher_counter["calls"] == 2


@pytest.mark.unit
def test_jwks_cache_recovers_after_rotation(
    rsa_keypair_pem: tuple[str, str],
) -> None:
    """If the cache misses but a refresh brings in the kid, return it.

    Simulates an auth-service key rotation that completed between the
    cache's first fetch and the validator's first token verify for the
    new kid: the second fetch is the rotated JWKS and contains the kid.
    """
    _, public_pem = rsa_keypair_pem
    jwk_obj = jose_jwk.construct(public_pem, ALGORITHMS.RS256)
    jwk_dict: dict[str, Any] = jwk_obj.to_dict()
    for k, v in list(jwk_dict.items()):
        if isinstance(v, bytes):
            jwk_dict[k] = v.decode("ascii")

    old = {"keys": [{**jwk_dict, "kid": "old-kid"}]}
    new = {"keys": [{**jwk_dict, "kid": "new-kid"}]}
    responses = [old, new]

    def fetcher(_url: str) -> dict[str, Any]:
        return responses.pop(0)

    cache = JwksCache("https://auth.test/.well-known/jwks.json", fetcher=fetcher)
    jwk = cache.get("new-kid")
    assert jwk["kid"] == "new-kid"


@pytest.mark.unit
def test_jwks_cache_rejects_non_object_document() -> None:
    """A JWKS endpoint returning a list (not an object) is a config error."""
    cache = JwksCache(
        "https://auth.test/.well-known/jwks.json",
        fetcher=lambda _url: {},  # missing 'keys' array
    )
    with pytest.raises(JwtConfigError, match="no 'keys' array"):
        cache.get(KID)


@pytest.mark.unit
def test_jwks_cache_ignores_non_dict_keys(rsa_keypair_pem: tuple[str, str]) -> None:
    """Non-dict entries and keys without `kid` are silently dropped."""
    _, public_pem = rsa_keypair_pem
    jwk_obj = jose_jwk.construct(public_pem, ALGORITHMS.RS256)
    jwk_dict: dict[str, Any] = jwk_obj.to_dict()
    for key, value in list(jwk_dict.items()):
        if isinstance(value, bytes):
            jwk_dict[key] = value.decode("ascii")
    jwk_dict.update({"kid": KID, "alg": "RS256", "use": "sig"})

    cache = JwksCache(
        "https://auth.test/.well-known/jwks.json",
        fetcher=lambda _url: {"keys": [jwk_dict, "not-a-dict", {"no": "kid"}]},
    )
    fetched = cache.get(KID)
    assert fetched["kid"] == KID


@pytest.mark.unit
def test_default_fetcher_parses_real_http_response(
    monkeypatch: pytest.MonkeyPatch, jwks_document: dict[str, Any]
) -> None:
    """The default urllib-based fetcher parses a JWKS JSON response body."""

    class _FakeResponse:
        def __init__(self, body: bytes) -> None:
            self._body = body

        def __enter__(self) -> _FakeResponse:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def read(self) -> bytes:
            return self._body

    def fake_urlopen(_request: Any, timeout: int = 5) -> Any:
        return _FakeResponse(json.dumps(jwks_document).encode("utf-8"))

    monkeypatch.setattr(
        "panakoes_auth_client.jwks.urllib.request.urlopen",
        fake_urlopen,
    )
    from panakoes_auth_client.jwks import _default_fetcher

    document = _default_fetcher("https://auth.test/.well-known/jwks.json")
    assert document == jwks_document


@pytest.mark.unit
def test_default_fetcher_rejects_non_object_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A JWKS endpoint returning a JSON array instead of an object is invalid."""
    class _FakeResponse:
        def __enter__(self) -> _FakeResponse:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def read(self) -> bytes:
            return b"[]"

    monkeypatch.setattr(
        "panakoes_auth_client.jwks.urllib.request.urlopen",
        lambda _r, timeout=5: _FakeResponse(),
    )
    from panakoes_auth_client.jwks import _default_fetcher

    with pytest.raises(JwtConfigError, match="non-object"):
        _default_fetcher("https://auth.test/.well-known/jwks.json")


@pytest.mark.unit
def test_from_jwks_url_validates_rs256_token(
    jwks_document: dict[str, Any], make_rs_token: Callable[..., str]
) -> None:
    """Happy path: JWKS-backed validator accepts a fresh RS256 token."""
    validator = JwtValidator.from_jwks_url(
        url="https://auth.test/.well-known/jwks.json",
        issuer=ISSUER,
        audience=AUDIENCE,
        fetcher=lambda _url: jwks_document,
    )
    assert isinstance(validator, JwksJwtValidator)

    claims = validator.validate(make_rs_token(overrides={"sub": "user_jwks"}))
    assert claims.sub == "user_jwks"


@pytest.mark.unit
def test_jwks_validator_rejects_token_with_missing_kid(
    jwks_document: dict[str, Any], make_rs_token: Callable[..., str]
) -> None:
    """A token whose header has no `kid` cannot be matched to a JWK."""
    validator = JwtValidator.from_jwks_url(
        url="https://auth.test/.well-known/jwks.json",
        issuer=ISSUER,
        audience=AUDIENCE,
        fetcher=lambda _url: jwks_document,
    )
    with pytest.raises(JwtInvalidError, match="missing kid"):
        validator.validate(make_rs_token(kid=None))


@pytest.mark.unit
def test_jwks_validator_rejects_unknown_kid(
    jwks_document: dict[str, Any], make_rs_token: Callable[..., str]
) -> None:
    """A token signed under a kid not in the JWKS fails closed."""
    validator = JwtValidator.from_jwks_url(
        url="https://auth.test/.well-known/jwks.json",
        issuer=ISSUER,
        audience=AUDIENCE,
        fetcher=lambda _url: jwks_document,
    )
    with pytest.raises(JwtInvalidError):
        validator.validate(make_rs_token(kid="unrelated-kid"))


@pytest.mark.unit
def test_jwks_validator_rejects_wrong_issuer(
    jwks_document: dict[str, Any], make_rs_token: Callable[..., str]
) -> None:
    """A token issued by a different `iss` is rejected."""
    validator = JwtValidator.from_jwks_url(
        url="https://auth.test/.well-known/jwks.json",
        issuer=ISSUER,
        audience=AUDIENCE,
        fetcher=lambda _url: jwks_document,
    )
    with pytest.raises(JwtInvalidError):
        validator.validate(make_rs_token(issuer="https://attacker.example"))


@pytest.mark.unit
def test_jwks_validator_rejects_wrong_audience(
    jwks_document: dict[str, Any], make_rs_token: Callable[..., str]
) -> None:
    """A token with a mismatched `aud` is rejected."""
    validator = JwtValidator.from_jwks_url(
        url="https://auth.test/.well-known/jwks.json",
        issuer=ISSUER,
        audience=AUDIENCE,
        fetcher=lambda _url: jwks_document,
    )
    with pytest.raises(JwtInvalidError):
        validator.validate(make_rs_token(audience="someone-else"))


@pytest.mark.unit
def test_jwks_validator_rejects_expired_token(
    jwks_document: dict[str, Any], make_rs_token: Callable[..., str]
) -> None:
    """`token expired` is raised when `exp` is in the past."""
    validator = JwtValidator.from_jwks_url(
        url="https://auth.test/.well-known/jwks.json",
        issuer=ISSUER,
        audience=AUDIENCE,
        fetcher=lambda _url: jwks_document,
    )
    expired = make_rs_token(ttl_seconds=-10)
    with pytest.raises(JwtInvalidError, match="expired"):
        validator.validate(expired)


@pytest.mark.unit
def test_jwks_validator_rejects_malformed_token(
    jwks_document: dict[str, Any],
) -> None:
    """A garbage string fails before the kid lookup with `invalid token`."""
    validator = JwtValidator.from_jwks_url(
        url="https://auth.test/.well-known/jwks.json",
        issuer=ISSUER,
        audience=AUDIENCE,
        fetcher=lambda _url: jwks_document,
    )
    with pytest.raises(JwtInvalidError, match="invalid token"):
        validator.validate("not-a-real-jwt")


@pytest.mark.unit
def test_jwks_validator_rejects_token_with_invalid_claims(
    jwks_document: dict[str, Any], make_rs_token: Callable[..., str]
) -> None:
    """A token missing the required `sub` claim raises `invalid token claims`."""
    validator = JwtValidator.from_jwks_url(
        url="https://auth.test/.well-known/jwks.json",
        issuer=ISSUER,
        audience=AUDIENCE,
        fetcher=lambda _url: jwks_document,
    )
    token = make_rs_token(overrides={"sub": ""})
    with pytest.raises(JwtInvalidError, match="invalid token claims"):
        validator.validate(token)


@pytest.mark.unit
def test_jwks_validator_rejects_token_with_empty_kid_string(
    jwks_document: dict[str, Any], rsa_keypair_pem: tuple[str, str]
) -> None:
    """A token whose `kid` header is an empty string fails the kid check."""
    private_pem, _ = rsa_keypair_pem
    now = int(time.time())
    token = jwt.encode(
        {"sub": "u", "iss": ISSUER, "aud": AUDIENCE, "iat": now, "exp": now + 100},
        private_pem,
        algorithm="RS256",
        headers={"kid": ""},
    )
    validator = JwtValidator.from_jwks_url(
        url="https://auth.test/.well-known/jwks.json",
        issuer=ISSUER,
        audience=AUDIENCE,
        fetcher=lambda _url: jwks_document,
    )
    with pytest.raises(JwtInvalidError, match="missing kid"):
        validator.validate(token)
