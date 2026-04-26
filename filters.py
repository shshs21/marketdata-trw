STABLE_SYMS = {
    "USDT","USDC","DAI","TUSD","BUSD","FDUSD","EURS","USDE",
    "PYUSD","GUSD","USDD","USDP","USDJ","EURT",
    "EURA","EURL","USD1","USDTB","USDY","RLUSD","XUSD","CRVUSD","TAI","UST",
    "PAX","USDS","USDQ","USDK","SUSD","EUSD","USDN","USDX","HUSD",
    "CUSD","MUSD","FRAX","LUSD","OUSD","RSV","AEUR","MKUSD","USDB",
    "DEUSD","USD0","USDL","USDG","USDF","STUSDT","USDO","FRXUSD",
    "AUSD","USDH","BFUSD","DUSD","BITUSD","SBD","USDAI","USDON",
    "CEUR","EUROC","EURC","EURCV","EURI",
    "SUSDE","SFRAX","CUSDO","SYRUPUSDC","SYRUPUSDT","AETHUSDT",
    "XAUT","PAXG",
    # USTC intentionally excluded, collapsed Terra USD trades as speculative asset
}

BTC_LIKE = {
    "WBTC","FBTC","IBTC","TBTC","SBTC","RENBTC","BTCB",
    "LBTC","BTC.B","MBTC","BBTC","HBTC","PBTC",
    "CBBTC","RBTC","BTCT","GTBTC","UNIBTC",
    "SOLVBTC","PUMPBTC","EBTC","XSOLVBTC","SOLVBTC.BBN",
    "VBTC",
}

ETH_LIKE = {
    "WETH","SETH","AETH","ANETH","AETHWETH",
    "STETH","WSTETH",
    "CBETH","WBETH",
    "RETH",
    "FRXETH","SFRXETH","AXLFRXETH",
    "EETH","WEETH",
    "AETHC","ANKRETH",
    "METH","CMETH","RSETH","EZETH","ETHX","LSETH","BETH",
    "SWETH","OSETH","PUFETH","PZETH","TETH","CDCETH",
    "RSWETH","WRSETH","MSTETH","WOETH",
    "VETH",
    # ETHFI and SWELL intentionally excluded, governance tokens with independent price action
}

LSD_OTHER = {
    "MSOL","JITOSOL","BSOL","BNSOL","JSOL","EZSOL","JUPSOL","BBSOL","EDGESOL",
    "SAVAX",
    "STMATIC",
    "SLISBNB","ASBNB",
}

EXCHANGE_TOKENS = {
    "OKB","HT","KCS","GT","LEO","BGB","ZB","MXM",
    "FTT","CRO","NEXO","CEL","WRX","BTSE","ASD","BTMX","HSK","TWT",
    # MEXC removed, exchange name not a token symbol
    # BNB kept despite DEX use, primarily exchange token
    # CAKE intentionally excluded, PancakeSwap DEX token with independent price action
}

WRAPPED_OTHER = {
    "WBNB","WMATIC","WAVAX","WTRX","WFTM","WHBAR","WEOS","WKAVA",
    "WNXM","WNCG","WVLX","WEVER","WKAI","WASTR","WPLS","WCFG",
    "WCRO","WXDC","WISLM","WZEDX","WTAO","WCHZ","WS","WONUS",
    "WBERA","WCORE","WXTZ","WTHETA","WTFUEL","WFLR","WHYPE","WAPTM",
}

JUNK_SYMS = {"", "999", "ERC20"}
# "R" removed, Revain token technically legitimate

def is_probable_stable(symbol: str, name: str | None = None) -> bool:
    s = (symbol or "").upper()
    n = (name or "").lower() if name else ""
    return s in STABLE_SYMS or "stable" in n

def is_btc_eth_variant(symbol: str, name: str | None = None) -> bool:
    s = (symbol or "").upper()
    n = (name or "").lower() if name else ""

    if s in BTC_LIKE or s in ETH_LIKE or s in LSD_OTHER:
        return True

    if any(x in n for x in [
        "wrapped bitcoin",
        "bitcoin-pegged",
        "wrapped ether",
        "staked ether",
        "liquid staked ether",
        "restaked eth",
        "staked sol",
        "staked avax",
        "staked bnb",
        "staked matic",
    ]):
        return True

    return False

def is_exchange_token(symbol: str, name: str | None = None) -> bool:
    return (symbol or "").upper() in EXCHANGE_TOKENS

def is_other_wrapper(symbol: str, name: str | None = None) -> bool:
    # Removed: `s.startswith("W") and len(s) <= 6` was too aggressive,
    # incorrectly catches WLD, WIF, WOO, WAVES, WAXP, WIN, WILD etc.
    # Explicit WRAPPED_OTHER set covers real wrappers instead.
    s = (symbol or "").upper()
    n = (name or "").lower() if name else ""

    if s in WRAPPED_OTHER:
        return True

    if any(x in n for x in ["wrapped", "pegged"]):
        return True

    return False

def is_junk(symbol: str) -> bool:
    return (symbol or "").upper() in JUNK_SYMS

def should_exclude(symbol: str, name: str | None = None) -> bool:
    return (
        is_junk(symbol)
        or is_probable_stable(symbol, name)
        or is_btc_eth_variant(symbol, name)
        or is_exchange_token(symbol, name)
        or is_other_wrapper(symbol, name)
    )
