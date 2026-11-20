"""Signed spending mandate + verifier. Plain Python. No AI in here.

Rule of the talk: the agent ASKS, this code DECIDES.
"""
import hashlib, hmac, json, time, uuid

# Held by YOUR backend only. The agent / LLM never sees it.
SECRET_KEY = b"demo-issuer-secret-change-me"

AUDIT_PATH = "audit.log"     # append-only, one JSON line per attempt
USED_NONCES = set()          # mandates that were already spent
LEDGER = {"grocery-shop": 0, "scam-shop": 0}   # fake merchants, fake rupees


def _sign(mandate: dict) -> str:
    body = json.dumps(mandate, sort_keys=True).encode()
    return hmac.new(SECRET_KEY, body, hashlib.sha256).hexdigest()


def issue_mandate(merchant: str, max_amount: int, ttl_seconds: int,
                  agent: str = "grocery-agent"):
    """Issuer (your backend) creates a permission slip and seals it."""
    mandate = {
        "agent": agent,                      # who can pay
        "merchant": merchant,                # to whom
        "max": max_amount,                   # how much
        "expires": time.time() + ttl_seconds,  # until when
        "nonce": str(uuid.uuid4()),          # one-time serial number
    }
    return mandate, _sign(mandate)           # the seal = can you prove it


def _log(merchant, amount, decision, mandate, agent="", note=""):
    row = {"ts": round(time.time(), 1), "agent": agent, "merchant": merchant,
           "amount": amount, "decision": decision,
           "nonce": str(mandate.get("nonce", "?"))[:8]}
    if note:
        row["note"] = note       # evidence: the plea is saved, never obeyed
    with open(AUDIT_PATH, "a") as f:
        f.write(json.dumps(row) + "\n")


def _clean_amount(amount):
    """Whole rupees only. 300 and 300.0 are fine; True, 300.5, "300" are not."""
    if isinstance(amount, bool):
        return None
    if isinstance(amount, float) and amount.is_integer():
        amount = int(amount)
    if isinstance(amount, int) and amount > 0:
        return amount
    return None


def verify_and_pay(mandate: dict, signature: str, merchant: str, amount,
                   *, agent: str, note: str = "") -> dict:
    """The guard. Checks in a fixed order. `note` (any chat text) is logged, never read.

    Order: seal, expiry, agent, merchant, amount, cap, replay.
    Replay is last so a blocked attempt reports its real reason, and only a
    payment that passes every other check spends the mandate.
    """
    try:
        clean = _clean_amount(amount)
        if not hmac.compare_digest(_sign(mandate), signature):
            decision = "BLOCKED: seal broken (mandate was edited)"
        elif time.time() > mandate["expires"]:
            decision = "BLOCKED: mandate expired"
        elif agent != mandate["agent"]:
            decision = "BLOCKED: wrong agent"
        elif merchant != mandate["merchant"]:
            decision = "BLOCKED: wrong merchant"
        elif clean is None:
            decision = "BLOCKED: invalid amount"
        elif clean > mandate["max"]:
            decision = "BLOCKED: amount above cap"
        elif mandate["nonce"] in USED_NONCES:
            decision = "BLOCKED: mandate already used (replay)"
        else:
            USED_NONCES.add(mandate["nonce"])
            LEDGER[merchant] = LEDGER.get(merchant, 0) + clean
            decision = "APPROVED"
    except (KeyError, TypeError, AttributeError):
        decision = "BLOCKED: malformed mandate"
    _log(merchant, amount, decision, mandate if isinstance(mandate, dict) else {},
         agent=agent, note=note)
    return {"approved": decision == "APPROVED", "reason": decision}