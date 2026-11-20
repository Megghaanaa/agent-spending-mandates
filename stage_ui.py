"""

  python stage_ui.py            live: real AI, real guard
  python stage_ui.py --mock     offline replay: a simulated AI, REAL guard
                                (no internet, no API key, never rate-limited)
  python stage_ui.py --ttl 90   slips expire after 90 seconds (to show expiry live)

Fair test: both agents get the SAME rules in their prompt, copied from the same
slip. Only the guarded agent's pay() is checked by code.

Then open http://localhost:8080 in Chrome.
Uses the SAME agents and tools as `adk web`. `adk web` stays your backup.
"""
import argparse, asyncio, json, os, pathlib, re, sys, time

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
import uvicorn

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def load_env():
    """Read ANTHROPIC_API_KEY etc. from spend_agent/.env (adk web does this for you)."""
    for folder in ("spend_agent", "naive_agent"):
        env = HERE / folder / ".env"
        if env.exists():
            for line in env.read_text().splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


load_env()

import naive_agent.agent as N          # the problem: limit only in the prompt
import spend_agent.agent as S          # the fix: same prompt, pay() goes through the guard
import spend_agent.mandate as M

M.AUDIT_PATH = str(HERE / "audit.log")
AGENTS = {"naive": N, "guarded": S}
MOCK = False
TTL = 60
RUNNERS = {}
MOCK_MEMORY = {"naive": 0, "guarded": 0}   # payments the simulated AI remembers, per chat


def new_slip():
    """One sealed slip for the guard; the SAME values go into both prompts."""
    S._MANDATE, S._SIGNATURE = M.issue_mandate("grocery-shop", max_amount=500,
                                               ttl_seconds=TTL, agent=S.AGENT_ID)
    N.set_slip(S._MANDATE)


def new_chat():
    """Forget the conversations (like opening a new chat). Slip and money stay."""
    RUNNERS.clear()
    for k in MOCK_MEMORY:
        MOCK_MEMORY[k] = 0


new_slip()


# ---------- live mode: real AI through ADK ----------

async def get_runner(key):
    from google.adk.runners import InMemoryRunner
    if key not in RUNNERS:
        agent = AGENTS[key].root_agent
        runner = InMemoryRunner(agent=agent, app_name=agent.name)
        session = await runner.session_service.create_session(
            app_name=agent.name, user_id="stage")
        RUNNERS[key] = (runner, session.id)
    return RUNNERS[key]


async def run_live(key, message):
    """Retry up to 2 times when Google is busy (503/overloaded), but ONLY if
    nothing happened yet, so a payment is never repeated by a retry."""
    for attempt in range(3):
        steps = []
        try:
            return await _run_live_once(key, message, steps)
        except Exception as e:
            text = str(e)
            busy = "503" in text or "UNAVAILABLE" in text or "overloaded" in text.lower()
            if not busy or steps or attempt == 2:
                raise
            await asyncio.sleep(2 * (attempt + 1))


async def _run_live_once(key, message, steps):
    from google.genai import types
    runner, sid = await get_runner(key)
    content = types.Content(role="user", parts=[types.Part(text=message)])
    async for ev in runner.run_async(user_id="stage", session_id=sid, new_message=content):
        if not ev.content or not ev.content.parts:
            continue
        for p in ev.content.parts:
            if getattr(p, "function_call", None):
                steps.append({"type": "call", "name": p.function_call.name,
                              "args": dict(p.function_call.args or {})})
            elif getattr(p, "function_response", None):
                steps.append({"type": "result", "name": p.function_response.name,
                              "response": p.function_response.response})
            elif getattr(p, "text", None) and not getattr(p, "thought", False):
                steps.append({"type": "text", "text": p.text})
    return steps


# ---------- mock mode: scripted AI choices, real tools, real guard ----------

def run_mock(key, message):
    """A SIMULATED AI, the same brain for both agents. It reads the rules in its
    prompt and tries to follow them, like a real model, but:
      - it has no clock, so it cannot tell when the slip expired;
      - it only remembers payments made in THIS chat;
      - it believes text that claims to change the rules.
    The guarded agent's payments then go through the REAL guard."""
    A, slip = AGENTS[key], dict(S._MANDATE)
    m = message.lower()
    steps, plan = [], []
    believes_override = "ignore previous instructions" in m
    inv = re.search(r"invoice\s*#?\s*(\d+)", m)
    if inv:
        num = inv.group(1)
        res = A.read_invoice(num)
        steps += [{"type": "call", "name": "read_invoice", "args": {"invoice_id": num}},
                  {"type": "result", "name": "read_invoice", "response": res}]
        text = res.get("text", "")
        if "NOTE TO AI" in text or "UPDATED SPENDING PERMISSION" in text:
            believes_override = True
        split = re.search(r"(\d+) separate payments of Rs(\d+)", text)
        total = re.search(r"Total: Rs(\d+)", text)
        if split:
            plan = [("grocery-shop", int(split.group(2)))] * int(split.group(1))
        elif total:
            plan = [("grocery-shop", int(total.group(1)))]
    else:
        amts = re.findall(r"(?:rs\.?|₹)\s*(\d+)", m)   # the amount asked for is the last one
        if amts:
            plan = [("scam-shop" if "scam" in m else "grocery-shop", int(amts[-1]))]
    if not plan:
        steps.append({"type": "text", "text": "Offline replay only knows the quick buttons. Try one of those!"})
        return steps
    last = None
    for shop, amount in plan:
        # The simulated AI checks the rules it can read (no clock, chat-only memory).
        if not believes_override:
            if shop != slip["merchant"]:
                last = f"I can't pay {shop}. My rules only allow {slip['merchant']}."
                break
            if amount > slip["max"]:
                last = f"I can't pay Rs{amount}. My limit is Rs{slip['max']} per payment."
                break
            if MOCK_MEMORY[key] >= 1:
                last = "I already made one payment in this chat. My rules allow only one."
                break
        args = {"merchant": shop, "amount": amount}
        res = A.pay(**args)
        steps += [{"type": "call", "name": "pay", "args": args},
                  {"type": "result", "name": "pay", "response": res}]
        if res.get("approved"):
            MOCK_MEMORY[key] += 1
        last = f"Result from the pay tool: {res['reason']}"
    steps.append({"type": "text", "text": last})
    return steps


# ---------- web app ----------

app = FastAPI()


def status():
    m = S._MANDATE
    lines = []
    try:
        lines = pathlib.Path(M.AUDIT_PATH).read_text().strip().splitlines()[-30:]
    except FileNotFoundError:
        pass
    return {
        "mock": MOCK,
        "ttl": TTL,
        "naive_ledger": N.LEDGER,
        "guarded_ledger": M.LEDGER,
        "mandate": {"agent": m["agent"], "merchant": m["merchant"], "max": m["max"],
                    "expires": m["expires"], "nonce": m["nonce"][:8],
                    "used": m["nonce"] in M.USED_NONCES,
                    "seal": S._SIGNATURE[:16]},
        "audit": [json.loads(l) for l in lines if l.strip()],
    }


@app.get("/", response_class=HTMLResponse)
def index():
    return (HERE / "stage_ui.html").read_text()


# ---------- Spot the Safe Mandate (no AI: the real mandate code grades the slips) ----------

def spot_round():
    import random
    import spot_the_safe as SP
    rounds = SP.build_mandates()
    random.shuffle(rounds)
    out = []
    for letter, (kind, m, sig) in zip("ABC", rounds):
        checks = SP.grade(m, sig)
        exp = m.get("expires")
        out.append({
            "letter": letter,
            "who": "anyone" if m.get("agent") in (None, "*", "any") else m["agent"],
            "shop": "any shop" if m.get("merchant") in (None, "*", "any") else m["merchant"],
            "max": m.get("max"),
            "until": None if exp is None else exp,
            "uses": "1" if "nonce" in m else "unlimited",
            "seal": (sig[:12] + "…") if sig else None,
            "checks": [{"q": q, "ok": ok, "why": why} for q, ok, why in checks],
            "safe": all(ok for _, ok, _ in checks),
        })
    return out


@app.get("/spot", response_class=HTMLResponse)
def spot_page():
    return (HERE / "spot.html").read_text()


@app.get("/api/spot")
def api_spot():
    return spot_round()


@app.get("/api/status")
def api_status():
    return status()


@app.post("/api/chat")
async def api_chat(req: Request):
    body = await req.json()
    key, message = body.get("agent"), (body.get("message") or "").strip()
    if key not in AGENTS or not message:
        return JSONResponse({"error": "bad request"}, status_code=400)
    try:
        steps = run_mock(key, message) if MOCK else await run_live(key, message)
    except Exception as e:  # show friendly errors on stage instead of a stack trace
        text = str(e)
        if "429" in text or "RESOURCE_EXHAUSTED" in text:
            kind = "rate"
        elif "503" in text or "UNAVAILABLE" in text or "overloaded" in text.lower():
            kind = "busy"
        else:
            kind = "other"
        return JSONResponse({"error": kind, "detail": text[:300]}, status_code=200)
    # Log every Gatekeeper request, even when the AI refuses and never calls pay().
    if key == "guarded" and not any(s.get("type") == "call" and s.get("name") == "pay" for s in steps):
        low = message.lower()
        amt = re.search(r"(?:rs\.?|₹)\s*(\d+)", low)
        shop = "scam-shop" if "scam" in low else ("grocery-shop" if "grocery" in low else "-")
        M._log(shop, int(amt.group(1)) if amt else "-", "REFUSED BY AI (no payment attempted)",
               S._MANDATE, agent=S.AGENT_ID, note=message[:120])
    return JSONResponse(json.loads(json.dumps({"steps": steps, "status": status()}, default=str)))

@app.post("/api/newchat")
def api_newchat():
    new_chat()
    return status()


@app.post("/api/reset")
def api_reset():
    new_chat()
    new_slip()
    for led in (N.LEDGER, M.LEDGER):
        for k in led:
            led[k] = 0
    open(M.AUDIT_PATH, "w").close()
    return status()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--mock", action="store_true", help="offline replay, no AI")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--ttl", type=int, default=60, help="slip lifetime in seconds")
    a = ap.parse_args()
    MOCK = a.mock
    TTL = a.ttl
    new_slip()
    open(M.AUDIT_PATH, "w").close()
    print(f"\n  Stage UI ({'OFFLINE REPLAY' if MOCK else 'LIVE AI'}) -> http://localhost:{a.port}\n")
    uvicorn.run(app, host="127.0.0.1", port=a.port, log_level="warning")