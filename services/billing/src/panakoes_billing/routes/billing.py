"""Billing endpoints: checkout session, portal, webhook, subscription view.

The webhook route is intentionally unauthenticated at the JWT layer:
Stripe does not send our JWT, and the security model relies on the
`Stripe-Signature` header verified against `STRIPE_WEBHOOK_SECRET`.
Every other endpoint requires a valid Bearer token.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any, cast, get_args
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from panakoes_audit import record_event

from panakoes_billing.auth import AuthenticatedUser, get_current_user
from panakoes_billing.config import Settings
from panakoes_billing.models import (
    CheckoutSessionRequest,
    CheckoutSessionResponse,
    PortalSessionRequest,
    PortalSessionResponse,
    SubscriptionStatus,
    SubscriptionView,
    Tier,
)
from panakoes_billing.storage.dynamodb import BillingEventStore
from panakoes_billing.storage.subscriptions import Plan, SubscriptionStore
from panakoes_billing.stripe_client import StripeAdapter, StripeSDKAdapter, StripeSignatureError

# Panakoes-owned origins permitted as Customer Portal `return_url` values.
# The Customer Portal redirects the end-user back to this URL when they
# close the portal session. Accepting an arbitrary user-supplied URL
# here would be an open-redirect: a phisher could craft a portal link
# whose return URL points at a credential-harvesting clone. The list is
# intentionally small and static so adding an environment requires a
# code change + code review, not an env-var flip.
_ALLOWED_RETURN_URL_ORIGINS: frozenset[str] = frozenset(
    {
        "https://dmaopcm3hnxog.cloudfront.net",
        "https://panakoes.com",
    }
)


def _is_allowed_return_url(return_url: str) -> bool:
    """Return True iff `return_url` is on the Panakoes-owned allowlist.

    Validation walks the parsed URL: scheme must be `https`, host must
    be non-empty and case-insensitively match one of the allowlisted
    origins. We compare against the full `scheme://host` so neither a
    `http://` downgrade nor a host-confusion like
    `https://panakoes.com.evil.com/...` (whose hostname parses as
    `panakoes.com.evil.com`) sneaks past the check.
    """
    parsed = urlparse(return_url)
    if parsed.scheme.lower() != "https":
        return False
    if not parsed.hostname:
        return False
    origin = f"https://{parsed.hostname.lower()}"
    return origin in _ALLOWED_RETURN_URL_ORIGINS


router = APIRouter(prefix="/billing", tags=["billing"])

# Locked pricing decision (CLAUDE.md): Team tier minimum is 3 seats.
TEAM_MIN_SEATS = 3

# Webhook event type to internal `event_type` discriminator. The five
# Stripe events Panakoes acts on are documented in services/billing/README.md.
_WEBHOOK_EVENT_TYPE_MAP: dict[str, str] = {
    "checkout.session.completed": "checkout_completed",
    "customer.subscription.created": "subscription_created",
    "customer.subscription.updated": "subscription_updated",
    "customer.subscription.deleted": "subscription_deleted",
    "invoice.paid": "invoice_paid",
    "invoice.payment_failed": "invoice_payment_failed",
}

# Webhook event type to audit `action` name.
_WEBHOOK_AUDIT_ACTION_MAP: dict[str, str] = {
    "checkout.session.completed": "billing.subscription_changed",
    "customer.subscription.created": "billing.subscription_changed",
    "customer.subscription.updated": "billing.subscription_changed",
    "customer.subscription.deleted": "billing.subscription_changed",
    "invoice.paid": "billing.payment_succeeded",
    "invoice.payment_failed": "billing.payment_failed",
}

# Stripe subscription event types that map to a row write in the
# subscriptions table. Invoice events are recorded in the event log
# only; they do not by themselves change plan state.
_SUBSCRIPTION_STATE_EVENTS: frozenset[str] = frozenset(
    {
        "checkout.session.completed",
        "customer.subscription.created",
        "customer.subscription.updated",
        "customer.subscription.deleted",
    }
)


def get_settings() -> Settings:
    """FastAPI dependency: build a fresh `Settings` per request.

    Tests override this with a fixture that returns deterministic env
    values; production accepts the cost of re-instantiation.
    """
    return Settings()


def get_event_store(
    settings: Annotated[Settings, Depends(get_settings)],
) -> BillingEventStore:
    """FastAPI dependency: provide a DynamoDB-backed billing event store."""
    return BillingEventStore(
        table_name=settings.ddb_billing_table,
        region_name=settings.aws_region,
    )


def get_subscription_store(
    settings: Annotated[Settings, Depends(get_settings)],
) -> SubscriptionStore:
    """FastAPI dependency: provide the DDB-backed subscription state store."""
    return SubscriptionStore(
        table_name=settings.ddb_subscriptions_table,
        region_name=settings.aws_region,
    )


def get_stripe_adapter(
    settings: Annotated[Settings, Depends(get_settings)],
) -> StripeAdapter:
    """FastAPI dependency: provide the Stripe SDK adapter (TEST-mode)."""
    return StripeSDKAdapter(settings)


def _resolve_price_for_tier(tier: str, settings: Settings) -> str:
    """Return the configured Stripe Price ID for a tier."""
    if tier == "pro":
        return settings.stripe_price_pro
    return settings.stripe_price_team


def _resolve_quantity(tier: str, seats: int | None) -> int:
    """Return the Checkout Session line-item quantity for a tier.

    `pro` is always quantity 1 regardless of the `seats` value (we
    silently drop a provided seat count instead of erroring; the JSON
    schema already permits the field for both tiers).

    `team` requires a seat count of at least `TEAM_MIN_SEATS`. A
    missing or below-minimum count raises 422 so the client gets a
    clear correction signal.
    """
    if tier == "pro":
        return 1
    if seats is None or seats < TEAM_MIN_SEATS:
        raise HTTPException(
            status_code=422,
            detail=f"team tier requires seats >= {TEAM_MIN_SEATS}",
        )
    return seats


@router.post(
    "/checkout-session",
    response_model=CheckoutSessionResponse,
    status_code=status.HTTP_200_OK,
)
async def create_checkout_session(
    request: CheckoutSessionRequest,
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    settings: Annotated[Settings, Depends(get_settings)],
    store: Annotated[BillingEventStore, Depends(get_event_store)],
    stripe_adapter: Annotated[StripeAdapter, Depends(get_stripe_adapter)],
) -> CheckoutSessionResponse:
    """Create a Stripe Checkout Session for the requested tier."""
    quantity = _resolve_quantity(request.tier, request.seats)
    price_id = _resolve_price_for_tier(request.tier, settings)

    session = stripe_adapter.create_checkout_session(
        customer_email=user.email,
        client_reference_id=user.user_id,
        price_id=price_id,
        quantity=quantity,
        success_url=settings.stripe_success_url,
        cancel_url=settings.stripe_cancel_url,
    )
    checkout_url = session.get("url")
    if not isinstance(checkout_url, str) or not checkout_url:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Stripe response missing checkout url",
        )

    event_id = store.append(
        user_id=user.user_id,
        event_type="checkout_started",
        attributes={
            "tier": request.tier,
            "seats": quantity,
            "stripe_session_id": session.get("id"),
        },
    )

    await record_event(
        actor_id=user.user_id,
        actor_type="user",
        action="billing.checkout_started",
        resource_type="billing_event",
        resource_id=event_id,
        source_service="billing",
        details={
            "tier": request.tier,
            "seats": quantity,
            "stripe_session_id": session.get("id"),
        },
    )

    return CheckoutSessionResponse(checkout_url=checkout_url)


@router.post(
    "/portal-session",
    response_model=PortalSessionResponse,
    status_code=status.HTTP_200_OK,
)
async def create_portal_session(
    request: PortalSessionRequest,
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    settings: Annotated[Settings, Depends(get_settings)],
    store: Annotated[BillingEventStore, Depends(get_event_store)],
    stripe_adapter: Annotated[StripeAdapter, Depends(get_stripe_adapter)],
) -> PortalSessionResponse:
    """Create a Stripe Customer Portal session for the JWT subject.

    Works for free-tier users too: if no Stripe customer is on file we
    create one on the fly so the user can land in the portal and
    upgrade. The portal triggers the same `customer.subscription.*`
    webhooks we already handle, so no extra wiring is needed on the
    write side.

    `return_url` is validated against a Panakoes-owned allowlist; any
    other origin is rejected with 422. Settings-driven default return
    URL is intentionally NOT used here: the SPA passes the current
    page so the user lands back where they started.
    """
    if not _is_allowed_return_url(request.return_url):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="return_url is not on the Panakoes allowlist",
        )

    customer_id = store.find_customer_id(user.user_id)
    if customer_id is None:
        customer = stripe_adapter.create_customer(
            email=user.email,
            metadata={"user_id": user.user_id},
        )
        new_customer_id = customer.get("id")
        if not isinstance(new_customer_id, str) or not new_customer_id:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Stripe response missing customer id",
            )
        customer_id = new_customer_id
        # Persist the new customer id as a billing event so future
        # portal sessions find it without re-creating a customer.
        store.append(
            user_id=user.user_id,
            event_type="customer_created",
            attributes={"stripe_customer_id": customer_id},
        )

    session = stripe_adapter.create_portal_session(
        customer_id=customer_id,
        return_url=request.return_url,
    )
    portal_url = session.get("url")
    if not isinstance(portal_url, str) or not portal_url:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Stripe response missing portal url",
        )
    return PortalSessionResponse(url=portal_url)


def _user_id_from_event(event_object: dict[str, Any]) -> str | None:
    """Extract the Panakoes user id from a Stripe event object.

    `client_reference_id` is the documented Stripe field we set when
    creating the Checkout Session. For non-checkout events Stripe does
    not echo it; in that case we fall back to the `metadata.user_id`
    convention. If neither is present, return `None` and let the
    caller decide how to proceed.
    """
    client_ref = event_object.get("client_reference_id")
    if isinstance(client_ref, str) and client_ref:
        return client_ref
    metadata = event_object.get("metadata")
    if isinstance(metadata, dict):
        user_id = metadata.get("user_id")
        if isinstance(user_id, str) and user_id:
            return user_id
    return None


def _extract_subscription_attributes(
    stripe_event_type: str,
    event_object: dict[str, Any],
) -> dict[str, Any]:
    """Build the DDB attribute dict for a webhook event payload."""
    attributes: dict[str, Any] = {
        "stripe_event_type": stripe_event_type,
    }
    customer_id = event_object.get("customer")
    if isinstance(customer_id, str) and customer_id:
        attributes["stripe_customer_id"] = customer_id
    subscription_id = event_object.get("subscription") or event_object.get("id")
    if isinstance(subscription_id, str) and subscription_id:
        attributes["stripe_subscription_id"] = subscription_id
    sub_status = event_object.get("status")
    if isinstance(sub_status, str) and sub_status:
        attributes["status"] = sub_status
    period_end = event_object.get("current_period_end")
    if isinstance(period_end, int):
        attributes["current_period_end"] = datetime.fromtimestamp(period_end, UTC).isoformat()
    items = event_object.get("items")
    if isinstance(items, dict):
        data = items.get("data")
        if isinstance(data, list) and data:
            first = data[0]
            if isinstance(first, dict):
                price = first.get("price")
                if isinstance(price, dict):
                    price_id = price.get("id")
                    if isinstance(price_id, str) and price_id:
                        attributes["stripe_price_id"] = price_id
    return attributes


def _plan_from_attributes(attributes: dict[str, Any]) -> Plan:
    """Derive the Panakoes plan from event attributes.

    Subscription-deleted events always collapse to ``free`` so the
    next /validate / sign-in sees the tenant downgraded. Other events
    use the tier resolved from the price id; an unknown price falls
    back to ``free`` rather than ``pro`` because we cannot assert what
    the customer is paying for.
    """
    tier = attributes.get("tier")
    if tier == "team":
        return "team"
    if tier == "pro":
        return "pro"
    return "free"


def _apply_subscription_state(
    *,
    event_type: str,
    event_id: str,
    tenant_id: str,
    event_object: dict[str, Any],
    attributes: dict[str, Any],
    subscription_store: SubscriptionStore,
) -> bool:
    """Project the webhook event onto the subscriptions table.

    Returns ``True`` when a write occurred and ``False`` when the
    conditional upsert detected a Stripe-replay (same event id already
    applied to this subscription).
    """
    subscription_id = attributes.get("stripe_subscription_id")
    if not isinstance(subscription_id, str) or not subscription_id:
        # No subscription identifier on the event (e.g. an early
        # checkout.session.completed before Stripe attached the
        # subscription). Acknowledged but nothing to project yet.
        return True

    # subscription.deleted always drops the tenant to free regardless
    # of the price id baked into the event.
    plan: Plan = "free" if event_type == "customer.subscription.deleted" else _plan_from_attributes(
        attributes
    )

    status_str = (
        "canceled"
        if event_type == "customer.subscription.deleted"
        else str(attributes.get("status") or event_object.get("status") or "active")
    )

    current_period_end: datetime | None = None
    raw_period_end = attributes.get("current_period_end")
    if isinstance(raw_period_end, str) and raw_period_end:
        try:
            current_period_end = datetime.fromisoformat(raw_period_end)
        except ValueError:
            current_period_end = None

    cancel_at: datetime | None = None
    raw_cancel_at = event_object.get("cancel_at")
    if isinstance(raw_cancel_at, int):
        cancel_at = datetime.fromtimestamp(raw_cancel_at, UTC)

    quantity = 1
    raw_quantity = event_object.get("quantity")
    if isinstance(raw_quantity, int) and raw_quantity > 0:
        quantity = raw_quantity
    else:
        items = event_object.get("items")
        if isinstance(items, dict):
            data = items.get("data")
            if isinstance(data, list) and data:
                first = data[0]
                if isinstance(first, dict):
                    qty = first.get("quantity")
                    if isinstance(qty, int) and qty > 0:
                        quantity = qty

    customer_id = attributes.get("stripe_customer_id")
    stripe_customer_id = customer_id if isinstance(customer_id, str) else None

    return subscription_store.upsert(
        tenant_id=tenant_id,
        subscription_id=subscription_id,
        event_id=event_id,
        plan=plan,
        status=status_str,
        current_period_end=current_period_end,
        cancel_at=cancel_at,
        quantity=quantity,
        stripe_customer_id=stripe_customer_id,
    )


@router.post("/webhook", status_code=status.HTTP_200_OK)
async def handle_webhook(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    store: Annotated[BillingEventStore, Depends(get_event_store)],
    subscription_store: Annotated[SubscriptionStore, Depends(get_subscription_store)],
    stripe_adapter: Annotated[StripeAdapter, Depends(get_stripe_adapter)],
    stripe_signature: Annotated[str | None, Header(alias="Stripe-Signature")] = None,
) -> dict[str, str]:
    """Handle a Stripe webhook payload. UNAUTHENTICATED at the JWT layer.

    Authentication is the Stripe-Signature header verified against
    `STRIPE_WEBHOOK_SECRET`. Any signature failure returns 400 and is
    audited as `billing.webhook_signature_invalid`. Successful events
    are appended to the billing event store and audited under the event
    type's mapped action.
    """
    payload = await request.body()
    if stripe_signature is None or not stripe_signature.strip():
        await record_event(
            actor_id="stripe",
            actor_type="service",
            action="billing.webhook_signature_invalid",
            resource_type="billing_webhook",
            resource_id="missing-signature",
            source_service="billing",
            details={"reason": "missing Stripe-Signature header"},
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="missing Stripe-Signature header",
        )

    try:
        event = stripe_adapter.verify_webhook_signature(
            payload=payload,
            signature_header=stripe_signature,
            secret=settings.stripe_webhook_secret,
        )
    except StripeSignatureError as exc:
        await record_event(
            actor_id="stripe",
            actor_type="service",
            action="billing.webhook_signature_invalid",
            resource_type="billing_webhook",
            resource_id="invalid-signature",
            source_service="billing",
            details={"reason": str(exc)},
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="invalid Stripe webhook signature",
        ) from exc

    stripe_event_type = event.get("type")
    if not isinstance(stripe_event_type, str):
        # The SDK should never produce this, but if it does we treat it
        # as a non-actionable event (200) rather than 400, since the
        # signature was already verified.
        return {"status": "ignored", "reason": "missing event type"}

    if stripe_event_type not in _WEBHOOK_EVENT_TYPE_MAP:
        # Stripe sends many event types; we only care about a curated
        # set. Unhandled events return 200 so Stripe stops retrying.
        return {"status": "ignored", "stripe_event_type": stripe_event_type}

    data = event.get("data")
    event_object: dict[str, Any] = {}
    if isinstance(data, dict):
        raw_object = data.get("object")
        if isinstance(raw_object, dict):
            event_object = raw_object

    user_id = _user_id_from_event(event_object)
    if user_id is None:
        # Without a user id we cannot bind the event to anything in our
        # DDB schema. Acknowledge with 200 to drain Stripe retries; the
        # next slice writes a "orphan" sink for follow-up reconciliation.
        return {"status": "ignored", "reason": "no user id on event"}

    internal_event_type = _WEBHOOK_EVENT_TYPE_MAP[stripe_event_type]
    # Resolve the tier from the price id when available.
    attributes = _extract_subscription_attributes(stripe_event_type, event_object)
    price_id = attributes.get("stripe_price_id")
    if price_id == settings.stripe_price_pro:
        attributes["tier"] = "pro"
    elif price_id == settings.stripe_price_team:
        attributes["tier"] = "team"

    # Idempotency: if this Stripe event id has already produced a row
    # for this subscription, the conditional upsert below returns False
    # and we return 200 without re-appending to the event log either.
    # Subscription-state events update the subscriptions table; invoice
    # events fall through to the event log only.
    stripe_event_id = event.get("id")
    if (
        stripe_event_type in _SUBSCRIPTION_STATE_EVENTS
        and isinstance(stripe_event_id, str)
        and stripe_event_id
    ):
        applied = _apply_subscription_state(
            event_type=stripe_event_type,
            event_id=stripe_event_id,
            tenant_id=user_id,
            event_object=event_object,
            attributes=attributes,
            subscription_store=subscription_store,
        )
        if not applied:
            return {"status": "duplicate", "stripe_event_id": stripe_event_id}

    event_id = store.append(
        user_id=user_id,
        event_type=internal_event_type,
        attributes=attributes,
    )

    audit_action = _WEBHOOK_AUDIT_ACTION_MAP[stripe_event_type]
    await record_event(
        actor_id=user_id,
        actor_type="user",
        action=audit_action,
        resource_type="billing_event",
        resource_id=event_id,
        source_service="billing",
        details={
            "stripe_event_type": stripe_event_type,
            "stripe_event_id": event.get("id"),
            "internal_event_type": internal_event_type,
        },
    )
    return {"status": "ok"}


@router.get("/subscription", response_model=SubscriptionView)
async def get_subscription(
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    store: Annotated[BillingEventStore, Depends(get_event_store)],
) -> SubscriptionView:
    """Return the JWT subject's current subscription view."""
    event = store.latest_subscription_event(user.user_id)
    if event is None:
        return SubscriptionView()

    tier = event.get("tier")
    sub_status = event.get("status")
    period_end_raw = event.get("current_period_end")

    period_end: datetime | None = None
    if isinstance(period_end_raw, str) and period_end_raw:
        # Reject malformed timestamps quietly: a corrupt event should
        # not 500 the read endpoint.
        try:
            period_end = datetime.fromisoformat(period_end_raw)
        except ValueError:
            period_end = None

    # Narrow `tier` and `sub_status` to the public Literal sets. A value
    # outside the documented enum surfaces as `None` rather than a 500;
    # the next slice tightens the schema once we trust the inputs.
    safe_tier: Tier | None = (
        cast("Tier", tier) if tier in get_args(Tier) else None
    )
    safe_status: SubscriptionStatus | None = (
        cast("SubscriptionStatus", sub_status)
        if isinstance(sub_status, str) and sub_status in get_args(SubscriptionStatus)
        else None
    )

    return SubscriptionView(
        tier=safe_tier,
        status=safe_status,
        current_period_end=period_end,
    )
