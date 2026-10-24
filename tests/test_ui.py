"""Validates the HTML of every screen that was upgraded to the premium UI."""
import os, sys
sys.path.insert(0, os.path.abspath("."))
from villain_ads_bot.core import ui

SCREENS = {
    "main menu": (
        f"{ui.card('Control Panel', 'Premium UserBot Manager', 'Manage every Telegram account from one place', '✦')}\n\n"
        f"{ui.kv('Slots used', '1 / 2', '📦')}\n"
        f"{ui.kv('Usage', f'{ui.progress_bar(1, 2)}  50%', '📈')}\n\n"
        f"{ui.help_hint('Add an account.')}\n"
        f"{ui.footer()}"
    ),
    "bots list": (
        f"{ui.card_open('Connected Userbots', 'Your active Telegram accounts')}\n"
        f"\n🟢 <b>MyBot</b>  <code>@mybot</code>"
        f"\n\n{ui.help_hint('Select an account below.')}"
        f"\n{ui.card_close()}"
    ),
    "all slots": (
        f"{ui.card_open('All Slots Control Dashboard', 'Bulk control centre')}\n\n"
        f"{ui.kv('Total linked bots', '5', '📊')}\n"
        f"{ui.kv('Auto broadcast (all)', '🟢 ON', '✉️')}\n\n"
        f"{ui.card_close()}"
    ),
    "admin panel": (
        f"{ui.card_open('Admin Control Panel', 'Owner tools')}\n\n"
        f"{ui.kv('Slot price', '₹25.00', '💸')}\n"
        f"{ui.kv('USDT rate', '₹90.00', '🪙')}\n\n"
        f"{ui.card_close()}"
    ),
    "settings": (
        f"{ui.card('User Settings', 'Account, wallet and language', 'Everything about your account', '⚙️')}\n\n"
        f"{ui.kv('Wallet balance', '₹0.00', '👛')}\n\n"
        f"{ui.help_hint('Pick an action below.')}\n"
        f"{ui.footer()}"
    ),
    "status": (
        f"{ui.card('System Status', 'Live overview of your automation', 'Everything your userbots did', '📊')}\n\n"
        f"{ui.kv('Connected bots', '3', '📱')}\n\n"
        f"{ui.footer()}"
    ),
    "payment method": (
        f"{ui.card('Select Payment Method', '', 'Review the amount', '💳')}\n\n"
        f"{ui.kv('Quantity', '3 slot(s)', '📦')}\n"
        f"{ui.kv('Total (INR)', '₹75.00', '💴')}\n"
        f"{ui.kv('Total (USDT)', '0.84 USDT', '🪙')}\n"
        f"{ui.kv('Total (TON)', '0.1500 TON', '💎')}\n\n"
        f"{ui.help_hint('UPI / USDT / TON are all accepted.')}"
    ),
    "invoice": (
        f"{ui.card('Make Payment', 'Method : <b>USDT</b>', 'Pay the exact amount', '💳')}\n\n"
        f"{ui.kv('Pay exactly', '0.84 USDT', '🪙')}\n"
        f"{ui.kv('Reference', 'XTR-1A2B3C4D', '🏷')}\n\n"
        f"{ui.help_hint('Include the reference in the payment note.')}"
    ),
    "branding": (
        f"{ui.card('Branding Configurations', '', 'Global branding for every hosted userbot', '🎨')}\n\n"
        f"{ui.kv('Name branding', '✅ ON', '📛')}\n\n"
        f"{ui.help_hint('Applied instantly.')}"
    ),
    "api credentials": (
        f"{ui.card('API Credentials', '', 'Multiple API_ID / API_HASH pairs', '🔐')}\n\n"
        f"{ui.kv('Total available', '4', '✅')}\n\n"
        f"{ui.help_hint('Round robin.')}"
    ),
}

failed = 0
for name, text in SCREENS.items():
    ok, msg = ui.validate_html(text)
    print(f"{'OK  ' if ok else 'FAIL'}  {name:20} {msg}")
    if not ok:
        failed += 1
        print(text)

print()
if failed:
    raise SystemExit(f"{failed} screen(s) with invalid HTML")
print("ALL UI SCREENS VALID")
