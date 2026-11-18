"""Stripe test-mode billing foundation. Live payments are disabled."""

import hashlib
import hmac
import json
import os
import secrets
import time

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel

from app.auth import connection, current_user
from app.credits import ensure_account

router = APIRouter(prefix="/api/billing", tags=["billing"])

# Provisional packages, not finalized commercial prices.
PACKAGES = {
    "starter": {"credits": 100, "cents": 199},
    "growth": {"credits": 500, "cents": 499},
    "pro": {"credits": 1500, "cents": 999},
}


def initialize_billing():
    with connection() as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS billing_orders (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                package TEXT NOT NULL,
                credits INTEGER NOT NULL CHECK(credits > 0),
                amount INTEGER NOT NULL CHECK(amount > 0),
                currency TEXT NOT NULL,
                stripe_session TEXT UNIQUE,
                state TEXT NOT NULL DEFAULT 'pending'
                    CHECK(state IN ('pending','paid')),
                created_at INTEGER NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id)
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS billing_events (
                event_id TEXT PRIMARY KEY,
                order_id TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                FOREIGN KEY(order_id) REFERENCES billing_orders(id)
            )
        """)


def test_mode_enabled():
    key = os.getenv("STRIPE_SECRET_KEY", "")
    return (
        os.getenv("RESUMEPILOT_STRIPE_TEST_MODE") == "1"
        and key.startswith("sk_test_")
        and bool(os.getenv("STRIPE_WEBHOOK_SECRET", "").startswith("whsec_"))
    )


class CheckoutRequest(BaseModel):
    package: str


@router.get("/packages")
def packages():
    return {
        "currency": "usd",
        "packages": [
            {"id": name, **values}
            for name, values in PACKAGES.items()
        ],
        "checkout_enabled": test_mode_enabled(),
    }


@router.post("/checkout")
def checkout(payload: CheckoutRequest, user=Depends(current_user)):
    if not test_mode_enabled():
        raise HTTPException(503, "Stripe test checkout is disabled.")

    package = PACKAGES.get(payload.package)
    if package is None:
        raise HTTPException(400, "Unknown credit package.")

    origin = os.getenv("RESUMEPILOT_PUBLIC_ORIGIN", "")
    if not origin.startswith("https://") and origin not in (
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ):
        raise HTTPException(503, "Checkout origin is not configured.")

    try:
        import stripe
    except ImportError:
        raise HTTPException(503, "Stripe SDK is not installed.")

    order_id = secrets.token_hex(16)

    with connection() as db:
        db.execute(
            """INSERT INTO billing_orders
               (id,user_id,package,credits,amount,currency,created_at)
               VALUES(?,?,?,?,?,'usd',?)""",
            (
                order_id, user["id"], payload.package,
                package["credits"], package["cents"], int(time.time()),
            ),
        )

    try:
        stripe.api_key = os.environ["STRIPE_SECRET_KEY"]
        session = stripe.checkout.Session.create(
            mode="payment",
            client_reference_id=order_id,
            metadata={"order_id": order_id},
            line_items=[{
                "price_data": {
                    "currency": "usd",
                    "unit_amount": package["cents"],
                    "product_data": {
                        "name": f"ResumePilot {package['credits']} credits"
                    },
                },
                "quantity": 1,
            }],
            success_url=origin + "/?checkout=success",
            cancel_url=origin + "/?checkout=cancel",
            idempotency_key="resumepilot-order-" + order_id,
        )
        session_id = session.id
        session_url = session.url
        if not session_id.startswith("cs_test_"):
            raise ValueError("Expected a test Checkout session.")
        if not session_url.startswith("https://checkout.stripe.com/"):
            raise ValueError("Unexpected Checkout URL.")
    except Exception:
        raise HTTPException(502, "Unable to create test Checkout.")

    with connection() as db:
        db.execute(
            "UPDATE billing_orders SET stripe_session=? WHERE id=?",
            (session_id, order_id),
        )

    return {"checkout_url": session_url, "order_id": order_id}


def verify_signature(body, header, secret, now=None):
    """Verify Stripe's timestamped HMAC-SHA256 webhook signature."""
    now = int(time.time()) if now is None else int(now)
    pieces = {}
    for part in header.split(","):
        key, sep, value = part.partition("=")
        if sep:
            pieces.setdefault(key, []).append(value)

    try:
        timestamp = int(pieces.get("t", [""])[0])
    except ValueError:
        raise HTTPException(400, "Invalid webhook timestamp.")

    if abs(now - timestamp) > 300:
        raise HTTPException(400, "Expired webhook signature.")

    message = str(timestamp).encode() + b"." + body
    expected = hmac.new(
        secret.encode(), message, hashlib.sha256
    ).hexdigest()

    if not any(
        hmac.compare_digest(expected, signature)
        for signature in pieces.get("v1", [])
    ):
        raise HTTPException(400, "Invalid webhook signature.")


def apply_paid_event(event):
    """Grant once per stored paid order, in one SQLite transaction."""
    if event.get("type") not in (
        "checkout.session.completed",
        "checkout.session.async_payment_succeeded",
    ):
        return False

    event_id = event.get("id")
    session = event.get("data", {}).get("object", {})
    order_id = session.get("client_reference_id")
    session_id = session.get("id")

    if not all(
        isinstance(value, str) and value
        for value in (event_id, order_id, session_id)
    ):
        raise HTTPException(400, "Invalid Checkout event.")

    if session.get("payment_status") != "paid":
        return False

    with connection() as db:
        db.execute("BEGIN IMMEDIATE")
        order = db.execute(
            "SELECT * FROM billing_orders WHERE id=?",
            (order_id,),
        ).fetchone()

        if order is None:
            raise HTTPException(409, "Unknown billing order.")

        if (
            order["stripe_session"] != session_id
            or session.get("amount_total") != order["amount"]
            or session.get("currency") != order["currency"]
            or session.get("metadata", {}).get("order_id") != order_id
        ):
            raise HTTPException(409, "Checkout order mismatch.")

        if order["state"] == "paid":
            return False

        ensure_account(db, order["user_id"])
        db.execute(
            """UPDATE credit_accounts SET balance=balance+?
               WHERE user_id=?""",
            (order["credits"], order["user_id"]),
        )
        db.execute(
            "UPDATE billing_orders SET state='paid' WHERE id=?",
            (order_id,),
        )
        db.execute(
            """INSERT INTO billing_events(event_id,order_id,created_at)
               VALUES(?,?,?)""",
            (event_id, order_id, int(time.time())),
        )
        return True



def reconciliation_report():
    """Read-only development audit; never adjusts customer balances."""
    with connection() as db:
        orders = db.execute(
            """SELECT id, user_id, credits, amount, currency,
                      stripe_session, state
               FROM billing_orders ORDER BY created_at, id"""
        ).fetchall()

        issues = []
        paid_count = 0
        pending_count = 0

        for order in orders:
            if order["state"] == "paid":
                paid_count += 1
                events = db.execute(
                    "SELECT COUNT(*) AS n FROM billing_events WHERE order_id=?",
                    (order["id"],),
                ).fetchone()["n"]

                if events != 1:
                    issues.append({
                        "order_id": order["id"],
                        "issue": "paid_order_event_count_mismatch",
                    })
            else:
                pending_count += 1

            if not order["stripe_session"]:
                issues.append({
                    "order_id": order["id"],
                    "issue": "missing_checkout_session",
                })

        return {
            "orders": len(orders),
            "paid": paid_count,
            "pending": pending_count,
            "issues": issues,
            "note": (
                "Local database consistency only. Stripe-side "
                "payment and refund reconciliation is not implemented."
            ),
        }


@router.post("/webhook")
async def webhook(
    request: Request,
    stripe_signature: str = Header(default="", alias="Stripe-Signature"),
):
    if not test_mode_enabled():
        raise HTTPException(503, "Stripe test webhooks are disabled.")

    body = await request.body()
    if len(body) > 262144:
        raise HTTPException(413, "Webhook payload too large.")

    verify_signature(
        body, stripe_signature, os.environ["STRIPE_WEBHOOK_SECRET"]
    )

    try:
        event = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        raise HTTPException(400, "Invalid webhook JSON.")

    applied = apply_paid_event(event)
    return {"received": True, "credits_granted": applied}
