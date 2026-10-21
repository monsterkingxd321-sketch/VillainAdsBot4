"""Offline test for the pricing engine."""
import sys

import villain_ads_bot.core.pricing as pricing

# Stub the settings
pricing._settings = lambda: {
    "price_per_slot": 25.0,
    "usdt_rate": 90.0,
    "ton_rate": 500.0,
}

print("price per slot :", pricing.get_price_per_slot())
assert pricing.get_price_per_slot() == 25.0

data = pricing.breakdown(3)
print("breakdown      :", data)
assert data["inr"] == 75.0
assert data["usdt"] == 0.84, data["usdt"]     # 75 / 90 = 0.8333 -> round UP
assert data["ton"] == 0.15, data["ton"]        # 75 / 500 = 0.15

# rounding always goes UP so a user can never underpay
assert pricing.inr_to_usdt(10.0, 90.0) == 0.12
assert pricing.inr_to_ton(1.0, 500.0) == 0.002
assert pricing.inr_to_ton(1.11, 500.0) == 0.0023   # 0.00222 -> rounded up

# a rate of zero must never produce a free payment - it falls back to the
# configured rate instead
assert pricing.inr_to_usdt(100.0, 0) > 0
assert pricing.get_usdt_rate() == 90.0

txt = pricing.breakdown_text(2)
assert "₹50.00" in txt, txt
assert "USDT" in txt and "TON" in txt
print("\nALL PRICING TESTS PASSED")
