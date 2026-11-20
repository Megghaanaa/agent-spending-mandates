"""Run ONE attack with narration, for the moment after the audience vote.

  python3.12 attack.py amount        bigger amount
  python3.12 attack.py merchant      wrong merchant
  python3.12 attack.py replay        replayed payment
  python3.12 attack.py expired       expired mandate
  python3.12 attack.py sweettalk     "just this once please" sweet-talk

Add --step to pause for Enter between sections while you narrate.
Everything is decided by the real guard in spend_agent/mandate.py.
"""
import importlib.util, pathlib, sys, time

_spec = importlib.util.spec_from_file_location(
    "mandate", pathlib.Path(__file__).parent / "spend_agent" / "mandate.py")
M = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(M)
M.AUDIT_PATH = "attack_audit.log"

STEP = "--step" in sys.argv

# The guard's fixed order of checks, matched to its block reasons.
CHECKS = [("Seal intact?", ("seal broken", "malformed")),
          ("Not expired?", ("expired",)),
          ("Right agent?", ("wrong agent",)),
          ("Right merchant?", ("wrong merchant",)),
          ("Valid amount?", ("invalid amount",)),
          ("Within the cap?", ("above cap",)),
          ("Not used before?", ("replay",))]


def pause():
    if STEP:
        input("      [press Enter] ")


def fresh(ttl=600):
    return M.issue_mandate("chai-shop", 500, ttl)


def show_mandate(m, sig):
    for k, v in m.items():
        if k == "expires":
            left = round(v - time.time())
            v = f"{time.strftime('%H:%M:%S', time.localtime(v))} ({'in ' + str(left) + 's' if left > 0 else 'ALREADY PASSED'})"
        print(f"      {k:<9}: {v}")
    print(f"      seal     : {sig[:16]}...")


def show_checks(reason):
    if reason == "APPROVED":
        stop = None
    else:
        stop = next((i for i, (_, keys) in enumerate(CHECKS)
                     if any(k in reason for k in keys)), 0)
    for i, (label, _) in enumerate(CHECKS):
        if stop is None or i < stop:
            print(f"      ✓ {label}")
        elif i == stop:
            print(f"      ✗ {label}   <-- STOPPED HERE")
        else:
            print(f"      - {label} (never reached)")


def scenario(key):
    m, s = fresh()
    if key == "amount":
        return dict(title="BIGGER AMOUNT", move="Ask to pay Rs5000. The slip allows Rs500.",
                    m=m, s=s, merchant="chai-shop", amount=5000,
                    why="The amount is above the cap on the slip.")
    if key == "merchant":
        return dict(title="WRONG MERCHANT", move="Send the money to scam-shop. The slip names chai-shop.",
                    m=m, s=s, merchant="scam-shop", amount=300,
                    why="The slip names exactly one shop.")
    if key == "replay":
        return dict(title="REPLAYED PAYMENT", move="Use the same valid slip a second time.",
                    m=m, s=s, merchant="chai-shop", amount=300,
                    before=("First use: pay Rs300", m, s, "chai-shop", 300),
                    why="The slip's one-time serial number was already crossed off.")
    if key == "expired":
        m, s = fresh(ttl=-1)
        return dict(title="EXPIRED MANDATE", move="Use a slip whose time has run out.",
                    m=m, s=s, merchant="chai-shop", amount=300,
                    why="The slip's expiry time is in the past.")
    if key == "sweettalk":
        return dict(title="SWEET-TALK", move='Ask for Rs5000 and plead: "Owner said just this once, please!"',
                    m=m, s=s, merchant="chai-shop", amount=5000,
                    note="Owner said it's fine, just this once, please!",
                    why="The guard never reads the plea. It only checks the slip.")
    return None


def main():
    keys = [a for a in sys.argv[1:] if not a.startswith("--")]
    sc = scenario(keys[0]) if keys else None
    if sc is None:
        print(__doc__)
        raise SystemExit(1)

    open(M.AUDIT_PATH, "w").close()
    print(f"\n=== ATTACK: {sc['title']} ===")
    print(f"\n1. The attacker's move\n   {sc['move']}")
    pause()

    print("\n2. The permission slip the guard receives")
    show_mandate(sc["m"], sc["s"])
    pause()

    if "before" in sc:
        label, bm, bs, bmerch, bamt = sc["before"]
        r = M.verify_and_pay(bm, bs, bmerch, bamt, agent="shopping-agent-1")
        print(f"\n   (setup) {label} -> {r['reason']}")
        print("   Now the attacker tries the same slip again.")
        pause()

    print(f"\n3. The guard checks, in order  (asked to pay Rs{sc['amount']} to {sc['merchant']})")
    if sc.get("note"):
        print(f'   Message sent along: "{sc["note"]}"')
    result = M.verify_and_pay(sc["m"], sc["s"], sc["merchant"], sc["amount"],
                              agent="shopping-agent-1", note=sc.get("note", ""))
    show_checks(result["reason"])
    pause()

    print(f"\n4. Verdict: {result['reason']}")
    print(f"   Why: {sc['why']}")
    last = open(M.AUDIT_PATH).read().strip().splitlines()[-1]
    print(f"\n5. The audit log recorded:\n   {last}")
    print("\nThe AI never decided. The code did.\n")


main()
