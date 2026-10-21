"""
Pricing engine.

INR is the base currency of the whole product. The admin sets a single
"price per slot" in INR from the admin panel and the USDT / TON amounts are
derived automatically from the configured exchange rates.

    1 USDT = usdt_rate INR
    1 TON  = ton_rate  INR

Crypto amounts are always rounded UP so a user can never underpay because of
rounding.
"""

from decimal import Decimal, ROUND_CEILING

from villain_ads_bot.data import database

DEFAULT_PRICE_PER_SLOT = 10.0
DEFAULT_USDT_RATE = 90.0
DEFAULT_TON_RATE = 500.0


def _settings() -> dict:
    try:
        return database.get_global_settings() or {}
    except Exception:
        return {}


def get_price_per_slot() -> float:
    """Returns the admin managed price of ONE slot in INR."""
    settings = _settings()
    for key in ("price_per_slot", "price_per_id"):
        value = settings.get(key)
        if value:
            try:
                price = float(value)
                if price > 0:
                    return price
            except (TypeError, ValueError):
                continue
    return DEFAULT_PRICE_PER_SLOT


def get_usdt_rate() -> float:
    """Returns how many INR one USDT is worth."""
    try:
        rate = float(_settings().get("usdt_rate", DEFAULT_USDT_RATE))
    except (TypeError, ValueError):
        rate = DEFAULT_USDT_RATE
    return rate if rate > 0 else DEFAULT_USDT_RATE


def get_ton_rate() -> float:
    """Returns how many INR one TON is worth."""
    try:
        rate = float(_settings().get("ton_rate", DEFAULT_TON_RATE))
    except (TypeError, ValueError):
        rate = DEFAULT_TON_RATE
    return rate if rate > 0 else DEFAULT_TON_RATE


def _round_up(value: float, places: int) -> float:
    """Rounds a value UP to the given number of decimal places."""
    try:
        quant = Decimal(1).scaleb(-places)
        return float(Decimal(str(value)).quantize(quant, rounding=ROUND_CEILING))
    except Exception:
        return round(value, places)


def inr_to_usdt(amount_inr: float, rate: float = None) -> float:
    """Converts an INR amount into USDT (rounded up to 2 decimals)."""
    rate = rate or get_usdt_rate()
    if not rate or rate <= 0:
        return 0.0
    return _round_up(float(amount_inr) / float(rate), 2)


def inr_to_ton(amount_inr: float, rate: float = None) -> float:
    """Converts an INR amount into TON (rounded up to 4 decimals)."""
    rate = rate or get_ton_rate()
    if not rate or rate <= 0:
        return 0.0
    return _round_up(float(amount_inr) / float(rate), 4)


def calculate_total(qty: int) -> float:
    """Total price in INR for `qty` slots."""
    return round(get_price_per_slot() * max(1, int(qty)), 2)


def breakdown(qty: int) -> dict:
    """
    Returns the full price breakdown for `qty` slots.

        {
            "qty": 3,
            "price_per_slot": 10.0,
            "inr": 30.0,
            "usdt": 0.34,
            "ton": 0.06,
            "usdt_rate": 90.0,
            "ton_rate": 500.0,
        }
    """
    qty = max(1, int(qty))
    per_slot = get_price_per_slot()
    total_inr = round(per_slot * qty, 2)
    usdt_rate = get_usdt_rate()
    ton_rate = get_ton_rate()
    return {
        "qty": qty,
        "price_per_slot": per_slot,
        "inr": total_inr,
        "usdt": inr_to_usdt(total_inr, usdt_rate),
        "ton": inr_to_ton(total_inr, ton_rate),
        "usdt_rate": usdt_rate,
        "ton_rate": ton_rate,
    }


def breakdown_text(qty: int, indent: str = "") -> str:
    """
    Returns a premium looking multi-currency price breakdown (HTML).
    """
    data = breakdown(qty)
    lines = [
        f"{indent}💎 <b>Slot Price :</b> <code>₹{data['price_per_slot']:.2f}</code> <i>/ slot</i>",
        f"{indent}📦 <b>Quantity :</b> <code>{data['qty']}</code> <i>slot(s)</i>",
        f"{indent}💴 <b>Total (INR) :</b> <code>₹{data['inr']:.2f}</code>",
        f"{indent}🪙 <b>Total (USDT) :</b> <code>{data['usdt']:.2f} USDT</code> <i>@ ₹{data['usdt_rate']:.2f}/$</i>",
        f"{indent}💎 <b>Total (TON) :</b> <code>{data['ton']:.4f} TON</code> <i>@ ₹{data['ton_rate']:.2f}/TON</i>",
    ]
    return "\n".join(lines)
