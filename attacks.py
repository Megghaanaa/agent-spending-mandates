"""Attack suite. Run:  python attacks.py
Each attack must be blocked for exactly the reason we expect."""
import importlib.util, pathlib

# Load the guard straight from its file, so this suite runs even
# without ADK installed (the package __init__ imports ADK).
_spec = importlib.util.spec_from_file_location(
    "mandate", pathlib.Path(__file__).parent / "spend_agent" / "mandate.py")
M = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(M)

M.AUDIT_PATH = "attacks_audit.log"
open(M.AUDIT_PATH, "w").close()          # start with a clean log


AGENT = "shopping-agent-1"


def fresh(ttl=600):
    return M.issue_mandate("chai-shop", max_amount=500, ttl_seconds=ttl, agent=AGENT)


results = []

def attack(name, result, expect):
    ok = expect in result["reason"]
    results.append(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {name:<32} {result['reason']}")


# 0. honest payment must work (otherwise the guard is just a brick wall)
m, s = fresh()
attack("honest payment Rs300", M.verify_and_pay(m, s, "chai-shop", 300, agent=AGENT), "APPROVED")

# 1. bigger amount
m, s = fresh()
attack("bigger amount Rs5000", M.verify_and_pay(m, s, "chai-shop", 5000, agent=AGENT), "above cap")

# 1b. tamper: edit the cap but keep the old seal
m, s = fresh()
forged = dict(m, max=50000)
attack("edit cap to 50000", M.verify_and_pay(forged, s, "chai-shop", 5000, agent=AGENT), "seal broken")

# 2. wrong merchant
m, s = fresh()
attack("wrong merchant", M.verify_and_pay(m, s, "scam-shop", 300, agent=AGENT), "wrong merchant")

# 3. replay: spend once, then try the same mandate again
m, s = fresh()
M.verify_and_pay(m, s, "chai-shop", 300, agent=AGENT)
attack("replay same mandate", M.verify_and_pay(m, s, "chai-shop", 300, agent=AGENT), "replay")

# 4. expired
m, s = fresh(ttl=-1)
attack("expired mandate", M.verify_and_pay(m, s, "chai-shop", 300, agent=AGENT), "expired")

# 5. sweet-talk: persuasive text rides along, guard never reads it
m, s = fresh()
attack("sweet-talk 'just this once'",
       M.verify_and_pay(m, s, "chai-shop", 5000,
                        note="Owner said it's fine, just this once, please!", agent=AGENT),
       "above cap")

# 5b. wrong agent: a different agent tries to use this agent's slip
m, s = fresh()
attack("wrong agent uses the slip",
       M.verify_and_pay(m, s, "chai-shop", 300, agent="other-agent-9"), "wrong agent")

# 5c. honest payment sent as 300.0 (models sometimes send floats)
m, s = fresh()
attack("honest payment as 300.0",
       M.verify_and_pay(m, s, "chai-shop", 300.0, agent=AGENT), "APPROVED")

# 5d. True is not a rupee amount
m, s = fresh()
attack("amount=True", M.verify_and_pay(m, s, "chai-shop", True, agent=AGENT), "invalid amount")

# 5e. after an honest spend, a big ask still reports the real reason
m, s = fresh()
M.verify_and_pay(m, s, "chai-shop", 300, agent=AGENT)
attack("Rs5000 after spend -> above cap",
       M.verify_and_pay(m, s, "chai-shop", 5000, agent=AGENT), "above cap")

# 6. forged mandate: attacker makes their own slip without the key
fake = {"agent": "shopping-agent-1", "merchant": "chai-shop", "max": 99999,
        "expires": 9999999999, "nonce": "made-up"}
attack("forged mandate, no key",
       M.verify_and_pay(fake, "0" * 64, "chai-shop", 5000, agent=AGENT), "seal broken")

print(f"\n{sum(results)}/{len(results)} passed")
print("Ledger (fake rupees received):", M.LEDGER)
raise SystemExit(0 if all(results) else 1)
