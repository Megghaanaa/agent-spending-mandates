"""Spot the Safe Mandate:  python3.12 spot_the_safe.py

Three mandates. One is safe. The order is shuffled each run.
Press Enter to reveal the grading by the four questions:
  1. Who can pay?   2. How much?   3. Until when?   4. Can you prove it?
"""
import importlib.util, json, pathlib, random, time

_spec = importlib.util.spec_from_file_location(
    "mandate", pathlib.Path(__file__).parent / "spend_agent" / "mandate.py")
M = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(M)

MAX_SAFE_CAP = 1000          # above this, the cap is too generous for a demo
MAX_SAFE_TTL = 24 * 3600     # longer than a day is too long-lived


def build_mandates():
    now = time.time()
    # Weak: no expiry, no nonce, no seal at all.
    weak = ({"agent": "grocery-agent", "merchant": "grocery-shop", "max": 500}, None)
    # Signed but sloppy: valid seal, wide open, lives for a year.
    sloppy = {"agent": "*", "merchant": "*", "max": 100000,
              "expires": now + 365 * 24 * 3600, "nonce": "sloppy-001"}
    sloppy = (sloppy, M._sign(sloppy))
    # Safe: named agent, one merchant, small cap, short life, nonce, seal.
    safe = M.issue_mandate("grocery-shop", 500, 600)
    return [("weak", *weak), ("sloppy", *sloppy), ("safe", *safe)]


def grade(m, sig):
    """Answer the four questions. Returns list of (question, ok, why)."""
    who = m.get("agent") not in (None, "*", "any")
    how = (isinstance(m.get("max"), int) and m["max"] <= MAX_SAFE_CAP
           and m.get("merchant") not in (None, "*", "any"))
    exp = m.get("expires")
    until = exp is not None and (exp - time.time()) <= MAX_SAFE_TTL
    proof = (sig is not None and "nonce" in m
             and M.hmac.compare_digest(M._sign(m), sig))
    return [
        ("1. Who can pay?",   who,   "named agent" if who else "anyone / unnamed"),
        ("2. How much?",      how,   "small cap, one merchant" if how else "cap too big or any merchant"),
        ("3. Until when?",    until, "short expiry" if until else "no expiry or far too long"),
        ("4. Can you prove it?", proof, "valid seal + nonce" if proof else "no seal / no nonce / broken"),
    ]


def show(m, sig):
    shown = dict(m)
    if "expires" in shown:
        shown["expires"] = time.strftime("%Y-%m-%d %H:%M", time.localtime(shown["expires"]))
    print(json.dumps(shown, indent=2))
    print("seal:", (sig[:16] + "...") if sig else "(none)")


def main():
    mandates = build_mandates()
    random.shuffle(mandates)
    letters = "ABC"

    for letter, (_, m, sig) in zip(letters, mandates):
        print(f"\n===== MANDATE {letter} =====")
        show(m, sig)

    input("\nWhich one is safe? Vote, then press Enter to reveal... ")

    for letter, (kind, m, sig) in zip(letters, mandates):
        results = grade(m, sig)
        verdict = "SAFE" if all(ok for _, ok, _ in results) else "NOT SAFE"
        print(f"\n----- MANDATE {letter}: {verdict} -----")
        for q, ok, why in results:
            print(f"  {'PASS' if ok else 'FAIL'}  {q:<24} {why}")

    print("\nLesson: a valid seal alone is not safety. Signed is not the same as safe.")


if __name__ == "__main__":
    main()