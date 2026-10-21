import logging
import time
import secrets
import asyncio
import uuid
import urllib.parse
import urllib.request
import tempfile
import os
import re
from telethon import events, Button
from villain_ads_bot.data import database
from villain_ads_bot.data import models
from villain_ads_bot import config
from villain_ads_bot.core import utils
from villain_ads_bot.core import pricing
from villain_ads_bot.core import ui

logger = logging.getLogger(__name__)

# State cache for payments and coupons
# Structure: { user_id: { "payment_id": str, "method": str, "step": str } }
_payment_user_states = {}

# How many slot quantities the user may pick in one order.
MAX_SLOT_QUANTITY = 10


def _new_payment_id() -> str:
    """Collision free payment id (12 hex chars)."""
    return uuid.uuid4().hex[:12]


def _new_reference() -> str:
    """
    Human friendly payment reference that the user has to mention in the
    payment app (it is embedded in the UPI intent and shown on the invoice).
    """
    return f"XTR-{uuid.uuid4().hex[:8].upper()}"


def _validate_utr(utr: str) -> tuple:
    """
    Validates a user supplied UTR / transaction hash.

    Returns (is_valid: bool, message: str)
    """
    if not utr:
        return False, "❌ The transaction reference cannot be empty."
    utr = utr.strip()
    if len(utr) < 6:
        return False, "❌ That reference is too short. A UTR / TxID is at least 6 characters long."
    if len(utr) > 120:
        return False, "❌ That reference is too long. Please send only the UTR / TxID."
    if not re.fullmatch(r"[A-Za-z0-9_\-]{6,120}", utr):
        return False, "❌ Invalid reference. Only letters, numbers, `_` and `-` are allowed."
    return True, utr


def _payment_summary(pay_record: dict, qty: int) -> str:
    """Multi currency summary of a payment (used for the user + the admin)."""
    amount = float(pay_record.get("amount", 0) or 0)
    usdt = float(pay_record.get("amount_usdt", 0) or 0)
    ton = float(pay_record.get("amount_ton", 0) or 0)
    lines = [
        ui.kv("Quantity", f"{qty} slot(s)", "📦"),
        ui.kv("Total (INR)", f"₹{amount:.2f}", "💴"),
    ]
    if usdt:
        lines.append(ui.kv("Total (USDT)", f"{usdt:.2f} USDT", "🪙"))
    if ton:
        lines.append(ui.kv("Total (TON)", f"{ton:.4f} TON", "💎"))
    return "\n".join(lines)


def is_admin(user_id: int) -> bool:
    global_settings = database.get_global_settings()
    admins = global_settings.get("admins", [])
    return user_id in admins or user_id in config.ORIGINAL_ADMIN_IDS

def register_handlers(client):
    
    # ==================== Force Subscribe Verification ====================
    @client.on(events.CallbackQuery(pattern="^verify_sub$"))
    async def verify_sub_callback(event):
        user_id = event.sender_id
        user = database.get_user(user_id)
        lang = user.get("language", "en") if user else "en"
        
        not_joined = await utils.check_force_sub(client, user_id)
        if not_joined:
            # Re-render check-join warning popup and message
            await event.answer(utils.get_text("tos_blocked_msg", lang), alert=True)
            
            buttons = []
            for i, ch in enumerate(not_joined, 1):
                label = ch.get("channel_name") or f"Channel {i}"
                buttons.append([Button.url(f"➕ Join {label}", url=ch.get("channel_link"))])
            buttons.append([utils.styled_button("✅ I've Joined — Verify", "verify_sub", style="success")])
            
            lines = [
                "❌ <b>Still not joined all channels!</b>\n━━━━━━━━━━━━━━━━━━━━\nJoin these channels to continue:\n"
            ]
            for ch in not_joined:
                lines.append(f"• {ch.get('channel_name') or ch.get('channel_id')}")
            lines.append("\n━━━━━━━━━━━━━━━━━━━━\n_Tap Verify after joining._")
            
            try:
                await event.edit("\n".join(lines), buttons=buttons)
            except Exception:
                pass
        else:
            await event.answer("✅ Verification successful!", alert=True)
            try:
                await event.delete()
            except Exception:
                pass
            # Check onboarding next (Language & TOS)
            from villain_ads_bot.handlers.start import show_main_menu
            await show_main_menu(event, user_id)

    # ==================== Redeem Coupon System ====================
    @client.on(events.CallbackQuery(pattern="^settings_redeem_coupon$"))
    async def redeem_coupon_prompt(event):
        user_id = event.sender_id
        user = database.get_user(user_id)
        lang = user.get("language", "en") if user else "en"
        
        _payment_user_states[user_id] = {"action": "WAITING_FOR_COUPON"}
        
        text = (
            "<blockquote><b>» 🎟️ Redeem Coupon</b>\n\n"
            "⚡ <i>Enter Your Coupon Code Below To Credit Your Wallet :</i></blockquote>"
        )
        buttons = [[utils.styled_button("🔙 Cancel", "menu_settings", style="danger")]]
        await event.respond(text, buttons=buttons, parse_mode="html")
        try:
            await event.delete()
        except Exception:
            pass

    @client.on(events.NewMessage)
    async def coupon_input_handler(event):
        if not event.is_private:
            return
        user_id = event.sender_id
        if user_id not in _payment_user_states or _payment_user_states[user_id].get("action") != "WAITING_FOR_COUPON":
            return
            
        if event.text.startswith("/start"):
            _payment_user_states.pop(user_id, None)
            return
            
        _payment_user_states.pop(user_id, None)
        code = event.text.strip().upper()
        
        user = database.get_user(user_id)
        lang = user.get("language", "en") if user else "en"
        
        coupon = database.get_coupon(code)
        if not coupon or not coupon.get("is_active"):
            await event.reply("❌ Invalid or expired coupon code.")
            return
            
        if coupon.get("current_uses", 0) >= coupon.get("max_uses", 1):
            await event.reply("❌ This coupon has reached its maximum uses.")
            return
            
        if database.has_used_coupon(code, user_id):
            await event.reply("❌ You have already used this coupon code.")
            return
            
        # Apply Coupon
        amount = coupon.get("amount_inr", 0.0)
        coupon["current_uses"] = coupon.get("current_uses", 0) + 1
        if coupon["current_uses"] >= coupon["max_uses"]:
            coupon["is_active"] = False
            
        database.save_coupon(coupon)
        database.save_coupon_usage(code, user_id)
        
        # Credit wallet balance
        user["wallet_balance"] = user.get("wallet_balance", 0.0) + amount
        database.save_user(user)
        
        await event.reply(
            f"🎉 <b>Coupon Redeemed Successfully!</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"💰 Amount: <b>₹{amount:.2f}</b> has been credited to your wallet balance."
        )
        # Return to settings
        from villain_ads_bot.handlers.settings import show_settings_menu
        await show_settings_menu(event, user_id)

    # ==================== Referrals Dashboard ====================
    @client.on(events.CallbackQuery(pattern="^settings_referrals$"))
    async def referrals_dashboard_callback(event):
        user_id = event.sender_id
        user = database.get_user(user_id) or {}
        lang = user.get("language", "en") if user else "en"
        
        # Calculate stats
        referred_count = database.count_referred_users(user_id)
        earnings = user.get("referral_earnings", 0.0)
        
        # Generate referral link
        me = await client.get_me()
        bot_username = me.username
        ref_link = f"https://t.me/{bot_username}?start=ref_{user_id}"
        
        global_settings = database.get_global_settings()
        comm_rate = global_settings.get("referral_commission", 0.10) * 100
        
        text = utils.format_html_message(
            f"<blockquote><b>» 👥 Refer & Earn Program</b>\n\n"
            f"🔗 <b>Your Referral Link :</b>\n<code>{ref_link}</code>\n\n"
            f"💰 <b>Signup Bonus :</b> Get <b>₹1.00</b> Instantly When A Friend Joins!\n"
            f"📈 <b>Commission :</b> Earn <b>{comm_rate:.0f}%</b> Commission On All Their Slot Upgrades!\n\n"
            f"📊 <b>Your Statistics :</b>\n"
            f"👤 <b>Total Referred :</b> <code>{referred_count}</code>\n"
            f"👛 <b>Total Earnings :</b> <code>₹{earnings:.2f}</code>\n\n"
            f"💡 <i>Share Your Link & Earn Free Wallet Balance!</i></blockquote>"
        )
        
        kb = [
            [Button.url("🔗 Share Referral Link", url=f"https://t.me/share/url?url={ref_link}&text=Manage%20your%20Telegram%20UserBots%20easily!")],
            [utils.styled_button("🔙 Back", "menu_settings", style="primary")]
        ]
        
        await event.respond(text, buttons=kb)
        try:
            await event.delete()
        except Exception:
            pass
        except Exception:
            pass

    # ==================== Subscription Plan Upgrades checkout ====================
    @client.on(events.CallbackQuery(pattern=r"^buy_plan_(.+)$"))
    async def buy_plan_callback(event):
        data = event.pattern_match.group(1)
        if "_qty_" in data:
            return
            
        plan_id = data
        user_id = event.sender_id
        
        global_settings = database.get_global_settings()
        plans = global_settings.get("subscription_plans", [])
        plan = next((p for p in plans if str(p.get("id")) == str(plan_id)), None)
        if not plan:
            await event.answer("⚠️ This plan is no longer available.", alert=True)
            return

        # The price is ALWAYS per slot - the admin sets one price per slot in
        # INR and the user simply picks how many slots they want.
        per_slot = pricing.get_price_per_slot()
        plan_days = int(plan.get("days", 30) or 30)
        text = (
            f"{ui.card('Select Slot Quantity', '', 'Choose how many userbot slots you want to activate', '💎')}\n\n"
            f"{ui.kv('Price per slot', f'₹{per_slot:.2f}', '💎')}\n"
            f"{ui.kv('Validity', f'{plan_days} days', '📅')}\n\n"
            f"{ui.help_hint('Tap the quantity you want — the total is calculated automatically.')}"
        )
        buttons = []
        for qty in range(1, MAX_SLOT_QUANTITY + 1):
            total = round(per_slot * qty, 2)
            buttons.append([
                utils.styled_button(
                    f"➕ {qty} Slot{'s' if qty > 1 else ''}  •  ₹{total:.2f}",
                    f"buy_plan_qty_{plan_id}_{qty}", style="primary"
                )
            ])
        buttons.append([utils.styled_button("🔙 Back", "settings_buy_slots", style="danger")])
        
        await event.respond(text, buttons=buttons, parse_mode="html")
        try:
            await event.delete()
        except Exception:
            pass

    @client.on(events.CallbackQuery(pattern=r"^buy_plan_qty_(.+)_(.+)$"))
    async def buy_plan_qty_callback(event):
        plan_id = event.pattern_match.group(1)
        try:
            qty = int(event.pattern_match.group(2))
        except (TypeError, ValueError):
            await event.answer("⚠️ Invalid quantity.", alert=True)
            return
        if qty < 1 or qty > MAX_SLOT_QUANTITY:
            await event.answer("⚠️ Invalid quantity.", alert=True)
            return
        user_id = event.sender_id
        
        global_settings = database.get_global_settings()
        plans = global_settings.get("subscription_plans", [])
        plan = next((p for p in plans if str(p.get("id")) == str(plan_id)), None)
        if not plan:
            await event.respond("❌ Selected plan not found.")
            return
            
        payment_id = _new_payment_id()
        
        # Price everything from the single per-slot price.
        price = pricing.breakdown(qty)
        amount = price["inr"]
        days = int(plan.get("days", 30) or 30)
        
        req_data = models.create_payment_request(payment_id, user_id, qty)
        req_data["plan_id"] = plan_id
        req_data["plan_name"] = plan.get("button_name", "Slot Upgrade")
        req_data["amount"] = amount
        req_data["amount_usdt"] = price["usdt"]
        req_data["amount_ton"] = price["ton"]
        req_data["price_per_slot"] = price["price_per_slot"]
        req_data["usdt_rate"] = price["usdt_rate"]
        req_data["ton_rate"] = price["ton_rate"]
        req_data["days"] = days
        req_data["reference"] = _new_reference()
        database.save_payment_request(req_data)
        
        # Redirect
        class MockMatch:
            def group(self, group_idx):
                if group_idx == 1:
                    return qty
                elif group_idx == 2:
                    return payment_id
                return None
                
        event.pattern_match = MockMatch()
        await choose_method_callback(event)

    # ==================== UPI/USDT Payment Method selection ====================
    @client.on(events.CallbackQuery(pattern=r"^select_method_(\d+)_(.+)$"))
    async def choose_method_callback(event):
        qty = int(event.pattern_match.group(1))
        payment_id = event.pattern_match.group(2)
        user_id = event.sender_id
        
        user = database.get_user(user_id)
        if not user:
            user = models.create_default_user(user_id)
            database.save_user(user)
        lang = user.get("language", "en") or "en"
        
        # Get the exact price from the payment record
        pay_record = database.get_payment_request(payment_id)
        if not pay_record:
            await event.answer("⚠️ This payment request expired. Please start again.", alert=True)
            return
        if str(pay_record.get("user_id")) != str(user_id):
            await event.answer("⛔ This payment request does not belong to you.", alert=True)
            return
            
        cost_inr = float(pay_record.get("amount", 0) or 0)
        wallet_bal = float(user.get("wallet_balance", 0.0) or 0)
        
        text = (
            f"{ui.card('Select Payment Method', '', 'Review the amount and choose how you want to pay', '💳')}\n\n"
            f"{_payment_summary(pay_record, qty)}\n"
            f"{ui.kv('Wallet balance', f'₹{wallet_bal:.2f}', '👛')}\n\n"
            f"{ui.help_hint('UPI · USDT · TON are all accepted — the crypto amounts are exact, not approximate.')}"
        )
        
        global_settings = database.get_global_settings()
        buttons = []
        
        if global_settings.get("payment_upi_enabled", True):
            buttons.append([utils.styled_button("💳 UPI / QR", f"pay_method_upi_{qty}_{payment_id}", style="success")])
        if global_settings.get("payment_usdt_enabled", True):
            buttons.append([utils.styled_button("🪙 USDT (BEP20)", f"pay_method_usdt_{qty}_{payment_id}", style="primary")])
        if global_settings.get("payment_ton_enabled", True):
            buttons.append([utils.styled_button("💎 TON (Toncoin)", f"pay_method_ton_{qty}_{payment_id}", style="primary")])
        
        # Allow paying using wallet if balance covers it
        if wallet_bal >= cost_inr:
            buttons.append([utils.styled_button("👛 Pay Via Wallet Balance", f"pay_method_wallet_{qty}_{payment_id}", style="success")])
            
        if not buttons:
            buttons = [[utils.styled_button("⚠️ No Payment Methods Available", "menu_settings", style="danger")]]
        
        buttons.append([utils.styled_button("❌ Cancel Payment", "payment_cancel", style="danger")])
        await event.respond(text, buttons=buttons, parse_mode="html")
        try:
            await event.delete()
        except Exception:
            pass

    @client.on(events.CallbackQuery(pattern="^payment_cancel$"))
    async def payment_cancel_callback(event):
        """Cancels an in-flight payment and clears the pending state."""
        user_id = event.sender_id
        _payment_user_states.pop(user_id, None)
        await event.answer("❌ Payment cancelled.", alert=True)
        try:
            await event.delete()
        except Exception:
            pass
        from villain_ads_bot.handlers.settings import show_settings_menu
        await show_settings_menu(event, user_id)

    # ==================== Payment Handler (Wallet Instant) ====================
    @client.on(events.CallbackQuery(pattern=r"^pay_method_wallet_(\d+)_(.+)$"))
    async def pay_wallet_callback(event):
        qty = int(event.pattern_match.group(1))
        payment_id = event.pattern_match.group(2)
        user_id = event.sender_id
        
        user = database.get_user(user_id)
        if not user:
            await event.answer("❌ User not found. Please run /start first.", alert=True)
            return
        lang = user.get("language", "en") or "en"
        
        pay_record = database.get_payment_request(payment_id)
        if not pay_record:
            await event.answer("⚠️ This payment request expired. Please start again.", alert=True)
            return
        if str(pay_record.get("user_id")) != str(user_id):
            await event.answer("⛔ This payment request does not belong to you.", alert=True)
            return
            
        cost_inr = float(pay_record.get("amount", 0) or 0)
        wallet_bal = float(user.get("wallet_balance", 0.0) or 0)
        
        if wallet_bal < cost_inr:
            await event.answer("❌ Insufficient wallet balance.", alert=True)
            return
            
        # Deduct wallet balance
        user["wallet_balance"] = wallet_bal - cost_inr
        database.save_user(user)
        
        # Save payment record
        if pay_record:
            pay_record["status"] = "approved"
            pay_record["method"] = "wallet"
            database.save_payment_request(pay_record)
            
        # Allocate subscription slots
        days = pay_record.get("days", 30) if pay_record else 30
        plan_name = pay_record.get("plan_name", "Slot Upgrade") if pay_record else "Slot Upgrade"
        expires_at = utils.allocate_slots_subscription(
            user_id, qty, days, plan_name, payment_id
        )
        
        import datetime
        expiry_str = datetime.datetime.fromtimestamp(expires_at).strftime('%d %b %Y %H:%M')
        
        await event.answer("🎉 Payment successful! Slots upgraded.", alert=True)
        await event.respond(
            f"🎉 <b>Upgrade Successful!</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"Plan: <b>{plan_name}</b>\n"
            f"Slots purchased: <b>{qty}</b>\n"
            f"Cost: <b>₹{cost_inr:.2f}</b> deducted from wallet balance.\n"
            f"Expiry Date: <b>{expiry_str}</b>"
        )
        
        # Handle referral commission
        referrer_id = user.get("referred_by")
        if referrer_id:
            ref_user = database.get_user(referrer_id)
            if ref_user:
                global_settings = database.get_global_settings()
                comm_rate = global_settings.get("referral_commission", 0.10)
                commission = cost_inr * comm_rate
                ref_user["wallet_balance"] = ref_user.get("wallet_balance", 0.0) + commission
                ref_user["referral_earnings"] = ref_user.get("referral_earnings", 0.0) + commission
                database.save_user(ref_user)
                try:
                    await client.send_message(
                        referrer_id,
                        f"💰 <b>Commission Received!</b>\n"
                        f"Referred user upgraded slots. <b>₹{commission:.2f}</b> added to your wallet."
                    )
                except Exception:
                    pass
                    
        # Go to settings
        from villain_ads_bot.handlers.settings import show_settings_menu
        await show_settings_menu(event, user_id)

    # ==================== Payment Handler (UPI/USDT/TON details) ====================
    @client.on(events.CallbackQuery(pattern=r"^pay_method_(upi|usdt|ton)_(\d+)_(.+)$"))
    async def pay_invoice_callback(event):
        method = event.pattern_match.group(1)
        qty = int(event.pattern_match.group(2))
        payment_id = event.pattern_match.group(3)
        user_id = event.sender_id
        
        user = database.get_user(user_id)
        if not user:
            user = models.create_default_user(user_id)
            database.save_user(user)
        lang = user.get("language", "en") or "en"
        
        # Fetch payment record to get the exact amounts
        pay_record = database.get_payment_request(payment_id)
        if not pay_record:
            await event.answer("⚠️ This payment request expired. Please start again.", alert=True)
            return
        if str(pay_record.get("user_id")) != str(user_id):
            await event.answer("⛔ This payment request does not belong to you.", alert=True)
            return
            
        cost_inr = float(pay_record.get("amount", 0) or 0)
        amount_usdt = float(pay_record.get("amount_usdt", 0) or 0)
        amount_ton = float(pay_record.get("amount_ton", 0) or 0)
        reference = pay_record.get("reference") or pay_record.get("payment_id", "")
        
        global_settings = database.get_global_settings()
        
        # Save state
        _payment_user_states[user_id] = {
            "payment_id": payment_id,
            "method": method,
            "qty": qty,
            "amount": cost_inr,
            "reference": reference,
            "action": "WAITING_FOR_SCREENSHOT"
        }
        
        if method == "upi":
            upi_id = global_settings.get('upi_id', 'merchant@upi')
            address_text = f"🏦 <b>UPI ID :</b> <code>{upi_id}</code>"
            pn_encoded = urllib.parse.quote(config.BOT_NAME)
            tr_encoded = urllib.parse.quote(str(reference))
            # tr = the transaction reference the user must send, tn = the note
            upi_uri = (f"upi://pay?pa={upi_id}&pn={pn_encoded}&am={cost_inr:.2f}"
                       f"&cu=INR&tr={tr_encoded}&tn={tr_encoded}")
            qr_url = (f"https://api.qrserver.com/v1/create-qr-code/?size=300x300&format=jpg"
                      f"&data={urllib.parse.quote(upi_uri)}")
            amount_text = f"💳 <b>Pay Exactly :</b> <code>₹{cost_inr:.2f}</code>"
        elif method == "usdt":
            address_text = (f"🪙 <b>USDT (BEP20) Address :</b>\n"
                            f"<code>{global_settings.get('usdt_bep20_address', '0x000')}</code>")
            qr_url = None
            amount_text = (f"🪙 <b>Send Exactly :</b> <code>{amount_usdt:.2f} USDT</code>\n"
                           f"💳 <i>Worth : ₹{cost_inr:.2f}</i>")
        else:  # ton
            address_text = (f"💎 <b>TON Address :</b>\n"
                            f"<code>{global_settings.get('ton_address', 'UQ00000000000000000000000000000000000000000000000')}</code>")
            qr_url = None
            amount_text = (f"💎 <b>Send Exactly :</b> <code>{amount_ton:.4f} TON</code>\n"
                           f"💳 <i>Worth : ₹{cost_inr:.2f}</i>")
            
        text = (
            f"{ui.card('Make Payment', f'Method : <b>{method.upper()}</b>', 'Pay the exact amount and send the screenshot', '💳')}\n\n"
            f"{amount_text}\n"
            f"{address_text}\n\n"
            f"{ui.kv('Reference', str(reference), '🏷')}\n"
            f"{ui.help_hint('Include the reference above in the payment note / UTR so we can verify it faster.')}\n"
            f"{ui.help_hint('📸 Now send your payment screenshot (photo or image link).')}"
        )
        
        buttons = [[utils.styled_button("❌ Cancel Payment", "payment_cancel", style="danger")]]
        if qr_url:
            try:
                # Download QR code image from the API
                req = urllib.request.Request(qr_url, headers={'User-Agent': 'Mozilla/5.0'})
                with urllib.request.urlopen(req) as response:
                    qr_bytes = response.read()
                
                # Write to temp file with .jpg extension to force photo upload
                fd, temp_file_path = tempfile.mkstemp(suffix=".jpg")
                try:
                    with os.fdopen(fd, 'wb') as tmp:
                        tmp.write(qr_bytes)
                    await event.respond(text, file=temp_file_path, buttons=buttons)
                finally:
                    try:
                        os.remove(temp_file_path)
                    except Exception:
                        pass
            except Exception as e:
                logger.error(f"Failed to fetch or send QR code: {e}")
                # Fallback to sending URL directly if download fails
                await event.respond(text, file=qr_url, buttons=buttons)
        else:
            await event.respond(text, buttons=buttons)
        try:
            await event.delete()
        except Exception:
            pass

    # ==================== Screenshot and UTR Receivers ====================
    @client.on(events.NewMessage)
    async def screenshot_and_utr_handler(event):
        if not event.is_private:
            return
        user_id = event.sender_id
        if user_id not in _payment_user_states:
            return
            
        state = _payment_user_states[user_id]
        action = state.get("action")
        
        user = database.get_user(user_id)
        lang = user.get("language", "en") if user else "en"
        
        # 1. Screenshot photo input
        if action == "WAITING_FOR_SCREENSHOT":
            photo = event.message.photo or event.photo
            file_to_save = None
            
            if photo:
                file_to_save = photo
            elif event.text and ("http://" in event.text or "https://" in event.text):
                import re
                urls = re.findall(r'(https?://[^\s]+)', event.text)
                if urls:
                    file_to_save = urls[0]
            
            if not file_to_save:
                await event.reply("❌ Please upload the payment screenshot as a photo or send a valid image link (e.g. ending in .jpg/.png).")
                return
                
            state["photo_file_id"] = file_to_save
            state["action"] = "WAITING_FOR_UTR"
            
            buttons = [[utils.styled_button("❌ Cancel Payment", "payment_cancel", style="danger")]]
            ref_txt = state.get("reference", "")
            state_amount = float(state.get("amount", 0) or 0)
            await event.reply(
                f"{ui.card('Screenshot Received', '', 'One last step — send your transaction reference', '📸')}\n\n"
                f"{ui.kv('Reference', str(ref_txt), '🏷')}\n"
                f"{ui.kv('Amount', f'₹{state_amount:.2f}', '💳')}\n\n"
                f"{ui.help_hint('🔢 Now enter the UTR / Transaction Hash / TxID from your payment app.')}\n"
                f"{ui.help_hint('Example : 612207806800')}",
                buttons=buttons
            )

        # 2. UTR transaction code input
        elif action == "WAITING_FOR_UTR":
            raw_text = (event.raw_text or event.text or "").strip()
            ok, utr_code = _validate_utr(raw_text)
            if not ok:
                await event.reply(utr_code)
                return
                
            # The same UTR must never be used twice (prevents double credit).
            existing = database.get_payment_request_by_utr_and_status(utr_code, "approved")
            if existing:
                await event.reply("❌ This transaction reference has already been used. Please send the correct UTR / TxID.")
                return
            pending = database.get_payment_request_by_utr_and_status(utr_code, "pending")
            if pending and str(pending.get("payment_id")) != str(state.get("payment_id")):
                await event.reply("❌ This transaction reference is already attached to another payment request.")
                return
                
            payment_id = state["payment_id"]
            method = state["method"]
            qty = state["qty"]
            amount = state["amount"]
            reference = state.get("reference", "")
            photo_file_id = state["photo_file_id"]
            
            # Save detail in payment record FIRST, only then drop the state so
            # a database error never loses the user's UTR / screenshot.
            pay_record = database.get_payment_request(payment_id)
            if pay_record:
                pay_record["method"] = method
                pay_record["utr_code"] = utr_code
                pay_record["reference"] = reference or pay_record.get("reference", "")
                if isinstance(photo_file_id, str):
                    pay_record["screenshot"] = photo_file_id
                else:
                    pay_record["screenshot"] = getattr(photo_file_id, "id", None)
                    pay_record["screenshot_file_ref"] = getattr(photo_file_id, "file_reference", None)
                database.save_payment_request(pay_record)
            
            # Pop the state only after everything was persisted.
            _payment_user_states.pop(user_id, None)
                
            # Confirm to User
            await event.reply(
                f"{ui.card('Payment Submitted', '', 'Your payment details were received', '📩')}\n\n"
                f"{ui.kv('Reference', str(reference), '🏷')}\n"
                f"{ui.kv('UTR / TxID', utr_code, '🔢')}\n"
                f"{ui.kv('Amount', f'₹{float(amount):.2f}', '💳')}\n\n"
                f"{ui.help_hint('An administrator will verify and activate your slots shortly.')}"
            )
            
            # Forward notification and screenshot to Admin log group
            global_settings = database.get_global_settings()
            log_group_id = global_settings.get("log_group_id")
            if log_group_id:
                try:
                    user_mention = f"[{user_id}](tg://user?id={user_id})"
                    admin_text = (
                        f"{ui.card('Payment Verification Request', '', 'A user is waiting for slot activation', '🚨')}\n\n"
                        f"{ui.kv('User', f'{user_mention} (`{user_id}`)', '👤')}\n"
                        f"{ui.kv('Method', method.upper(), '💳')}\n"
                        f"{_payment_summary(pay_record, qty)}\n"
                        f"{ui.kv('Reference', str(reference), '🏷')}\n"
                        f"{ui.kv('UTR / TxID', utr_code, '🔢')}\n"
                    )
                    
                    # Approve and Reject live in SEPARATE rows so the button
                    # styler does not paint both of them red.
                    buttons = [
                        [utils.styled_button("✅ Approve & Activate Slots", f"approve_payment_{payment_id}", style="success")],
                        [utils.styled_button("❌ Reject Payment", f"reject_payment_{payment_id}", style="danger")],
                    ]
                    
                    await client.send_message(
                        log_group_id,
                        admin_text,
                        file=photo_file_id,
                        buttons=buttons
                    )
                except Exception as e:
                    logger.error(f"Failed to log payment to admin log group: {e}")

    # ==================== Admin Force Sub commands ====================
    @client.on(events.NewMessage(pattern=r"^/addchannel"))
    async def addchannel_cmd(event):
        if not is_admin(event.sender_id):
            return
            
        args = event.text.split()
        if len(args) < 3:
            await event.reply("Usage: `/addchannel <channel_id_or_username> <invite_link> [Channel Name]`")
            return
            
        ch_id = args[1]
        ch_link = args[2]
        ch_name = " ".join(args[3:]) if len(args) > 3 else ch_id
        
        database.add_force_channel(ch_id, ch_link, ch_name)
        await event.reply(f"✅ Channel added to force subscribe list: <b>{ch_name}</b>")

    @client.on(events.NewMessage(pattern=r"^/removechannel"))
    async def removechannel_cmd(event):
        if not is_admin(event.sender_id):
            return
            
        args = event.text.split()
        if len(args) < 2:
            await event.reply("Usage: `/removechannel <channel_id_or_username>`")
            return
            
        ch_id = args[1]
        database.delete_force_channel(ch_id)
        await event.reply(f"✅ Channel <b>{ch_id}</b> removed from force subscribe list.")

    # ==================== Admin Coupon commands ====================
    @client.on(events.NewMessage(pattern=r"^/addcoupon"))
    async def addcoupon_cmd(event):
        if not is_admin(event.sender_id):
            return
            
        args = event.text.split()
        if len(args) < 3:
            await event.reply("Usage: `/addcoupon <amount_inr> <max_uses>`")
            return
            
        try:
            amount = float(args[1])
            max_uses = int(args[2])
            if amount <= 0 or max_uses < 1:
                raise ValueError
        except ValueError:
            await event.reply("❌ Invalid parameters. Enter a positive amount and max uses.")
            return
            
        # Generate code
        code = "SLOT-" + secrets.token_hex(4).upper()
        coupon_data = {
            "code": code,
            "amount_inr": amount,
            "max_uses": max_uses,
            "current_uses": 0,
            "is_active": True
        }
        database.save_coupon(coupon_data)
        await event.reply(
            f"🎟️ <b>Coupon Code Generated Successfully!</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🔑 Code: `{code}`\n"
            f"💰 Amount: <b>₹{amount:.2f}</b>\n"
            f"👥 Max Uses: <b>{max_uses}</b>"
        )

    @client.on(events.NewMessage(pattern=r"^/removecoupon"))
    async def removecoupon_cmd(event):
        if not is_admin(event.sender_id):
            return
            
        args = event.text.split()
        if len(args) < 2:
            await event.reply("Usage: `/removecoupon <code>`")
            return
            
        code = args[1].upper()
        database.delete_coupon(code)
        await event.reply(f"✅ Coupon `{code}` removed.")

    # ==================== Admin Subscription Plan commands ====================
    @client.on(events.NewMessage(pattern=r"^/addplan"))
    async def addplan_cmd(event):
        if not is_admin(event.sender_id):
            return
            
        args = event.text.split()
        if len(args) < 4:
            await event.reply("Usage: `/addplan <days> <price_per_account> <button_name>`")
            return
            
        try:
            days = int(args[1])
            price = float(args[2])
            button_name = " ".join(args[3:])
            if days <= 0 or price <= 0 or not button_name:
                raise ValueError
        except ValueError:
            await event.reply("❌ Invalid parameters. Enter a positive number of days and price.")
            return
            
        import uuid
        plan_id = "plan_" + str(uuid.uuid4())[:6]
        
        global_settings = database.get_global_settings()
        plans = global_settings.setdefault("subscription_plans", [])
        plans.append({
            "id": plan_id,
            "days": days,
            "price": price,
            "button_name": button_name
        })
        database.save_global_settings(global_settings)
        await event.reply(
            f"✅ <b>Subscription Plan Added Successfully!</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"Plan ID: `{plan_id}`\n"
            f"Name: <b>{button_name}</b>\n"
            f"Days: <b>{days}</b>\n"
            f"Price per account: <b>₹{price:.2f}</b>"
        )

    @client.on(events.NewMessage(pattern=r"^/removeplan"))
    async def removeplan_cmd(event):
        if not is_admin(event.sender_id):
            return
            
        args = event.text.split()
        if len(args) < 2:
            await event.reply("Usage: `/removeplan <plan_id>`")
            return
            
        plan_id = args[1]
        
        global_settings = database.get_global_settings()
        plans = global_settings.get("subscription_plans", [])
        original_len = len(plans)
        global_settings["subscription_plans"] = [p for p in plans if p["id"] != plan_id]
        
        if len(global_settings["subscription_plans"]) < original_len:
            database.save_global_settings(global_settings)
            await event.reply(f"✅ Subscription plan `{plan_id}` successfully removed.")
        else:
            await event.reply(f"❌ Plan ID `{plan_id}` not found.")


# ==================== Gmail Auto-Approval (Disabled) ====================

async def start_gmail_polling(bot_client=None):
    """
    Gmail auto-approval polling disabled per request.
    Manual approval via admin log group buttons is active.
    """
    return
