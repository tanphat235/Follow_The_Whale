"""Nhan dien thuc the qua nametag. Danh sach nay chi la LOP DAU;
dia chi khong khop nhan nao se duoc phan loai bang heuristic thong ke trong classify.py.
"""
from __future__ import annotations

CEX = (
    "binance", "okx", "okex", "gate.io", "gate ", "gate:", "gate dep", "bybit", "coinbase",
    "bitget", "bitstamp", "kraken", "hitbtc", "robinhood", "paxos", "bitvavo", "kucoin",
    "mexc", "crypto.com", "huobi", "htx", "bitfinex", "gemini", "upbit", "bithumb",
    "bingx", "lbank", "poloniex", "whitebit", "deribit", "backpack", "bitmart", "coinex",
    "bitmex", "phemex", "korbit", "coinone", "bitso", "luno", "btcturk", "exchange",
)

DEX_POOL = (
    "uniswap v2", "uniswap v3", "uniswap v4", "pool manager", "sushiswap", "pancakeswap",
    "curve", "balancer", "shibaswap", "dodo", "bancor", "maverick", "fluid dex",
    "uniswap v3: uni", "lp pool", ": uni-", "liquidity pool",
)

ROUTER = (
    "1inch", "cow protocol", "gpv2settlement", "uniswapx", "rizzolver", "0x:", "0x protocol",
    "odos", "paraswap", "kyber", "metamask: swap", "okx: dex", "bebop", "matcha",
    "universal router", "permit2", "aggregation router", "swaprouter", "router",
    "settlement", "solver", "zeroex", "li.fi", "socket", "enso", "banana gun", "maestro",
    "unibot", "sigma", "looter", "dexscreener",
)

MEV = ("mev bot", "mev-bot", "jaredfromsubway", "sandwich", "arb bot", "arbitrage bot", "searcher")

MARKET_MAKER = (
    "market maker", "wintermute", "gsr", "amber group", "amber:", "cumberland", "jump trading",
    "jump crypto", "alameda", "dwf", "flow traders", "b2c2", "keyrock", "woo network",
    "galaxy digital", "qcp", "auros", "gotbit", "kronos research", "selini", "portofino",
)

BRIDGE = (
    "bridge", "arbitrum: ", "optimism: ", "polygon: ", "wormhole", "layerzero", "across",
    "hop protocol", "stargate", "celer", "orbiter", "portal", "gateway", "canonical",
    "base: ", "scroll: ", "linea: ", "zksync", "starkgate", "synapse", "multichain",
)

PROTOCOL = (
    "uniswap: team", "uniswap governance", "uniswap: treasury", "timelock", "aave", "compound",
    "makerdao", "lido", "eigenlayer", "vesting", "token distributor", "merkle distributor",
    "airdrop", "staking", "gnosis safe", "safe:", "multisig", "null address", "burn",
)

FUND = ("fund:", "venture", "capital", "a16z", "paradigm", "polychain", "pantera", "dragonfly")

# Dia chi dac biet luon phai nhan dung nhan, khong phu thuoc nametag.
FIXED = {
    "0x0000000000000000000000000000000000000000": "burn",
    "0x000000000000000000000000000000000000dead": "burn",
    "0x1f9840a85d5af5bf1d1762f925bdaddc4201f984": "token_contract",
    "0x000000000004444c5dc75cb358380d2e3de08a90": "dex_pool",  # Uniswap V4 Pool Manager
    "0x1f98400000000000000000000000000000000004": "dex_pool",  # Uniswap V4 (moi)
    "0x3fc91a3afd70395cd496c647d5a6cc9d4b2b7fad": "router",    # Universal Router
    "0x000000000022d473030f116ddee9f6b43ac78ba3": "router",    # Permit2
    "0x9008d19f58aabd9ed0d60971565aa8510560ab41": "router",    # CoW GPv2Settlement
    # Vi kho lanh / vi nong cua san. Blockscout KHONG co nametag cho chung, nen neu
    # khong liet ke o day thi chung bi xep nham la "whale_candidate" va keo theo ca
    # cac vi trung chuyen cua san cung bi xep nham (da gap that: 0x5a52e96bac chuyen
    # 2 trieu UNI Binance-lanh -> Binance-nong, suyt bi bao la whale xa hang).
    "0xf977814e90da44bfa03b6295a0616a897441acec": "cex",  # Binance 8 (kho lanh)
    "0x28c6c06298d514db089934071355e5743bf21d60": "cex",  # Binance 14
    "0x21a31ee1afc51d94c2efccaa2092ad1028285549": "cex",  # Binance 15
    "0xdfd5293d8e347dfe59e90efd55b2956a1343963d": "cex",  # Binance 16
    "0x56eddb7aa87536c09ccc2793473599fd21a8b17f": "cex",  # Binance 17
    "0x9696f59e4d72e237be84ffd425dcad154bf96976": "cex",  # Binance 18
    "0x4976a4a02f38326660d17bf34b431dc6e2eb2327": "cex",  # Binance 19
    "0xd88b55467f58af508dbfdc597e8ebd2ad2de49b3": "cex",  # Binance 20
    "0x5a52e96bacdabb82fd05763e25335261b270efcb": "cex",  # Binance (vi trung chuyen)
}

GROUPS = (
    ("mev_bot", MEV),
    ("market_maker", MARKET_MAKER),
    ("cex", CEX),
    ("dex_pool", DEX_POOL),
    ("router", ROUTER),
    ("bridge", BRIDGE),
    ("fund", FUND),
    ("protocol", PROTOCOL),
)


def match_label(text: str | None) -> tuple[str, str] | None:
    """Tra ve (nhan, tu_khoa_khop) hoac None."""
    if not text:
        return None
    low = text.lower()
    for klass, needles in GROUPS:
        for needle in needles:
            if needle in low:
                return klass, needle
    return None


def is_deposit_tag(text: str | None) -> bool:
    """Etherscan dat ten vi nap tien la 'Binance Dep: 0x...' hoac 'Gate Dep: 0x...'."""
    if not text:
        return False
    low = text.lower()
    return " dep:" in low or low.startswith("dep:") or "deposit" in low
