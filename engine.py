#!/usr/bin/env python3
# backend/engine.py
import requests, json, os, sys
import logging
from datetime import datetime, timezone

try:
    from backend.config import ETHERSCAN_API_KEY, DEFAULT_ETH_AMOUNT, ETH_PRICE_USD, GAS_UNITS_REBALANCE
except ImportError:
    ETHERSCAN_API_KEY = os.environ.get("ETHERSCAN_API_KEY", "")
    DEFAULT_ETH_AMOUNT = float(os.environ.get("DEFAULT_ETH_AMOUNT", "1.0"))
    ETH_PRICE_USD = float(os.environ.get("ETH_PRICE_USD", "1600.0"))
    GAS_UNITS_REBALANCE = int(os.environ.get("GAS_UNITS_REBALANCE", "210000"))

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("engine.log"),
        logging.StreamHandler(sys.stdout)
    ]
)

OUT_PATH = os.path.join(os.path.dirname(__file__), "snapshot.json")

def fetch_defillama_pools():
    candidates = [
        "https://yields.llama.fi/pools",
        "https://api.llama.fi/pools",
        "https://yields.llama.fi/poolsV2"
    ]
    for url in candidates:
        try:
            r = requests.get(url, timeout=12)
            if r.status_code == 200:
                try:
                    return r.json()
                except Exception:
                    return r.text
        except Exception:
            continue
    # print("Failed to fetch pools from DefiLlama endpoints.")
    logging.warning("Failed to fetch pools from DefiLlama endpoints.")

    return []

def fetch_eth_price():
    try:
        r = requests.get("https://api.coingecko.com/api/v3/simple/price?ids=ethereum&vs_currencies=usd", timeout=8)
        r.raise_for_status()
        return float(r.json().get("ethereum", {}).get("usd", ETH_PRICE_USD))
    except Exception:
        return ETH_PRICE_USD

def fetch_gas_gwei():
    try:
        r = requests.get(f"https://api.etherscan.io/api?module=gastracker&action=gasoracle&apikey={ETHERSCAN_API_KEY}", timeout=8)
        r.raise_for_status()
        jr = r.json()
        if jr.get("result"):
            return float(jr["result"].get("ProposeGasPrice") or jr["result"].get("SafeGasPrice") or jr["result"].get("FastGasPrice") or 50)
    except Exception:
        pass
    return 50.0

def percent_to_decimal(v):
    try:
        v = float(v)
        if v > 1:
            return v/100.0
        return v
    except Exception:
        return 0.0

def estimate_gas_eth(gas_units, gas_gwei):
    wei = gas_units * gas_gwei * 1e9
    return wei/1e18

def compute_net_apy(base_apy_decimal, gas_eth, eth_price_usd, user_eth_amount):
    stake_usd = user_eth_amount * eth_price_usd
    gas_usd = gas_eth * eth_price_usd
    gas_impact_pct = (gas_usd / stake_usd) if stake_usd > 0 else 0.0
    net = base_apy_decimal - gas_impact_pct
    return max(0.0, net)

def normalize_pools(raw):
    out = []
    if not raw:
        return out
    arr = raw.get("data", raw) if isinstance(raw, dict) else raw
    for p in arr:
        try:
            project = (p.get("project") or p.get("pool") or p.get("title") or p.get("name") or "").strip()
            symbol = p.get("symbol") or ""
            # apy = p.get("apy") or p.get("apyBase") or p.get("apyMean30d") or 0
            apy = p.get("apy") or p.get("apyBase") or 0.0
            # apy = percent_to_decimal(apy)  # convert to decimal
            tvl = p.get("tvlUsd") or p.get("tvl") or 0

            # Construct pool URL if available, fallback to DefiLlama
            pool_id = p.get("pool") or ""
	    # pool_id = p.get("id") or p.get("pool") or p.get("poolId") or ""
            # url = f"https://defillama.com/yields/pool/{pool_id}" if pool_id else None

            # Filter pools by TVL and APY
       	    if tvl > 10000 and symbol in ["ETH", "WETH"]:
                out.append({
                    "protocol": project,
                    "symbol": symbol,
                    "base_apy":apy,
                    "tvlUsd": tvl,
		#    "url": f"https://app.defillama.com/pool/{p.get('pool')}" if p.get("pool") else "n/a"
		    "url": f"https://defillama.com/yields/pool/{pool_id}" if pool_id else "n/a"
                  			
                })
        except Exception:
            continue
    return out

def build_snapshot(user_eth_amount=DEFAULT_ETH_AMOUNT):

    raw = fetch_defillama_pools()
    pools = normalize_pools(raw)
    eth_price = fetch_eth_price()
    gas_gwei = fetch_gas_gwei()
    gas_eth = estimate_gas_eth(GAS_UNITS_REBALANCE, gas_gwei)


    logging.info(f"Fetched {len(pools)} pools from DefiLlama")
    logging.info(f"ETH price: ${eth_price}, Gas: {gas_gwei} gwei, Gas impact: {gas_eth:.6f} ETH")

    snapshot = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "eth_price_usd": eth_price,
        "gas_gwei": gas_gwei,
        "gas_eth": gas_eth,
        "user_eth_amount": user_eth_amount,
        "results": []
    }

    for p in pools:
        try:
            base = p.get("base_apy", 0.0)
            net = compute_net_apy(base, gas_eth, eth_price, user_eth_amount)
            snapshot["results"].append({
                "protocol": p.get("protocol"),
                "symbol": p.get("symbol"),
                "tvlUsd": p.get("tvlUsd"),
                "base_apy": round(base,6),
                "net_apy": round(net,6),
                "url": p.get("url")   # <-- ADD THIS LINE
            })

        #    logging.info(f"Pool added: {p.get('protocol')} {p.get('symbol')} base {round(base,2)}% net {round(net,2)}%")
        except Exception as e:
            logging.error(f"Error processing pool {p}: {e}")
        # except Exception:
        #    continue

    snapshot["results"] = sorted(snapshot["results"], key=lambda x: x["net_apy"], reverse=True)

    try:
        os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
        with open(OUT_PATH, "w") as f:
            json.dump(snapshot, f, indent=2)
    except Exception as e:
        logging.error(f"Failed to write snapshot: {e}")
    return snapshot

if __name__ == '__main__':
    amt = DEFAULT_ETH_AMOUNT
    if len(sys.argv) > 1:
        try:
            amt = float(sys.argv[1])
        except:
            logging.warning(f"Invalid ETH amount input: {sys.argv[1]}, using default {DEFAULT_ETH_AMOUNT}")
            # pass
    s = build_snapshot(amt)
    logging.info(f"Snapshot built: {s.get('timestamp')}")
    logging.info("Top results:")	
    # print("Snapshot built:", s.get("timestamp"))
    # print("Top results:")
    for r in s.get("results", [])[:8]:
      #  print(f" - {r.get('protocol')} {r.get('symbol')} base {r.get('base_apy')*100:.2f}% net {r.get('net_apy')*100:.2f}%")
         print(f" - {r.get('protocol')} {r.get('symbol')} base {r.get('base_apy')*1:.2f}% net {r.get('net_apy')*1:.2f}% · {r.get('url','n/a')}")



