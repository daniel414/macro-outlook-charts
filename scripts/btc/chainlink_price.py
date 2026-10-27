"""Chainlink BTC/USD (Ethereum mainnet aggregator) via free public RPCs. Daniel 2026-10-27 via Manager.
check(ref) -> dict(ok, price, updated_at, age_s, rpc, diff_pct, reason). Never raises."""
import json, sys, time, urllib.request
AGG = "0xF4030086522a5bEEa4988F8cA5B36dbC97BeE88c"
RPCS = ["https://ethereum-rpc.publicnode.com", "https://eth.llamarpc.com", "https://cloudflare-eth.com"]
MAX_AGE = 2 * 3600; MAX_DIFF = 0.5
def _call(rpc, data):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "eth_call", "params": [{"to": AGG, "data": data}, "latest"]}).encode()
    req = urllib.request.Request(rpc, body, {"Content-Type": "application/json", "User-Agent": "btc-sentiment/1.0"})
    r = json.load(urllib.request.urlopen(req, timeout=12))
    if not r.get("result") or r["result"] == "0x": raise RuntimeError(str(r.get("error"))[:120])
    return bytes.fromhex(r["result"][2:])
def fetch():
    errs = []
    for rpc in RPCS:
        try:
            dec = int.from_bytes(_call(rpc, "0x313ce567")[:32], "big") or 8
            b = _call(rpc, "0xfeaf968c")
            ans = int.from_bytes(b[32:64], "big", signed=True); upd = int.from_bytes(b[96:128], "big")
            return {"price": ans / 10 ** dec, "updated_at": upd, "rpc": rpc, "decimals": dec}
        except Exception as e:
            errs.append(f"{rpc}: {type(e).__name__}")
    raise RuntimeError("all RPC failed: " + "; ".join(errs))
def check(ref=None):
    try: d = fetch()
    except Exception as e: return {"ok": False, "reason": str(e)}
    d["age_s"] = int(time.time() - d["updated_at"])
    d["diff_pct"] = abs(d["price"] / ref - 1) * 100 if ref else None
    if d["price"] <= 0: d.update(ok=False, reason="non-positive answer")
    elif d["age_s"] > MAX_AGE: d.update(ok=False, reason=f"updatedAt too old ({d['age_s']}s)")
    elif d["diff_pct"] is not None and d["diff_pct"] >= MAX_DIFF: d.update(ok=False, reason=f"diff {d['diff_pct']:.3f}% >= {MAX_DIFF}%")
    else: d.update(ok=True, reason="")
    return d
if __name__ == "__main__":
    print(json.dumps(check(float(sys.argv[1]) if len(sys.argv) > 1 else None)))
