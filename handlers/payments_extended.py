import logging
import time
import secrets
import asyncio
from telethon import events, Button
import database
import models
import config
import utils

logger = logging.getLogger(__name__)

# State cache for payments and coupons
# Structure: { user_id: { "payment_id": str, "method": str, "step": str } }
_payment_user_states = {}

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
            from handlers.start import show_main_menu
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
        from handlers.settings import show_settings_menu
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
        plan = next((p for p in plans if p["id"] == plan_id), None)
        
        if plan and plan.get("slots"):
            qty = plan["slots"]
            import uuid
            payment_id = str(uuid.uuid4())[:8]
            amount = plan["price"]
            days = plan["days"]
            
            req_data = models.create_payment_request(payment_id, user_id, qty)
            req_data["plan_id"] = plan_id
            req_data["plan_name"] = plan["button_name"]
            req_data["amount"] = amount
            req_data["days"] = days
            database.save_payment_request(req_data)
            
            class MockMatch:
                def group(self, group_idx):
                    if group_idx == 1:
                        return qty
                    elif group_idx == 2:
                        return payment_id
                    return None
                    
            event.pattern_match = MockMatch()
            await choose_method_callback(event)
            return
        
        # Ask quantity
        text = (
            "⚙️ <b>Select Slots Quantity</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "How many bot account slots would you like to purchase under this plan?"
        )
        buttons = []
        for qty in range(1, 6):
            buttons.append([
                utils.styled_button(f"Request {qty} Account Slot(s)", f"buy_plan_qty_{plan_id}_{qty}", style="primary")
            ])
        buttons.append([utils.styled_button("🔙 Back", "settings_buy_slots", style="primary")])
        
        await event.respond(text, buttons=buttons)
        try:
            await event.delete()
        except Exception:
            pass

    @client.on(events.CallbackQuery(pattern=r"^buy_plan_qty_(.+)_(.+)$"))
    async def buy_plan_qty_callback(event):
        plan_id = event.pattern_match.group(1)
        qty = int(event.pattern_match.group(2))
        user_id = event.sender_id
        
        global_settings = database.get_global_settings()
        plans = global_settings.get("subscription_plans", [])
        plan = next((p for p in plans if p["id"] == plan_id), None)
        if not plan:
            await event.respond("❌ Selected plan not found.")
            return
            
        import uuid
        payment_id = str(uuid.uuid4())[:8]
        
        # Calculate cost
        amount = plan["price"] * qty
        days = plan["days"]
        
        req_data = models.create_payment_request(payment_id, user_id, qty)
        req_data["plan_id"] = plan_id
        req_data["plan_name"] = plan["button_name"]
        req_data["amount"] = amount
        req_data["days"] = days
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
        lang = user.get("language", "en") if user else "en"
        
        # Get actual price from payment record
        pay_record = database.get_payment_request(payment_id)
        if pay_record and "amount" in pay_record:
            cost_inr = pay_record["amount"]
        else:
            global_settings = database.get_global_settings()
            price_per_id = global_settings.get("price_per_id", 10.0)
            cost_inr = price_per_id * qty
            
        wallet_bal = user.get("wallet_balance", 0.0)
        
        text = (
            f"<blockquote><b>» 💳 Select Payment Method</b>\n\n"
            f"Quantity : <b>{qty} Id(s)</b>\n"
            f"Total Cost : <b>₹{cost_inr:.2f}</b>\n"
            f"Wallet Balance : <b>₹{wallet_bal:.2f}</b>\n\n"
            f"⚡ <i>Choose Your Payment Method Below :</i></blockquote>"
        )
        
        global_settings = database.get_global_settings()
        buttons = []
        
        if global_settings.get("payment_upi_enabled", True):
            buttons.append([utils.styled_button("💳 Upi Payment", f"pay_method_upi_{qty}_{payment_id}", style="primary")])
        if global_settings.get("payment_usdt_enabled", True):
            buttons.append([utils.styled_button("🪙 Usdt (BEP20)", f"pay_method_usdt_{qty}_{payment_id}", style="primary")])
        if global_settings.get("payment_ton_enabled", True):
            buttons.append([utils.styled_button("💎 Ton (Toncoin)", f"pay_method_ton_{qty}_{payment_id}", style="primary")])
        
        # Allow paying using wallet if balance covers it
        if wallet_bal >= cost_inr:
            buttons.append([utils.styled_button("👛 Pay Via Wallet Balance", f"pay_method_wallet_{qty}_{payment_id}", style="success")])
            
        if not buttons:
            buttons = [[utils.styled_button("⚠️ No Payment Methods Available", "menu_settings", style="danger")]]
        
        buttons.append([utils.styled_button("🔙 Cancel", "menu_settings", style="danger")])
        await event.respond(text, buttons=buttons, parse_mode="html")
        try:
            await event.delete()
        except Exception:
            pass

    # ==================== Payment Handler (Wallet Instant) ====================
    @client.on(events.CallbackQuery(pattern=r"^pay_method_wallet_(\d+)_(.+)$"))
    async def pay_wallet_callback(event):
        qty = int(event.pattern_match.group(1))
        payment_id = event.pattern_match.group(2)
        user_id = event.sender_id
        
        user = database.get_user(user_id)
        lang = user.get("language", "en") if user else "en"
        
        # Get actual price from payment record
        pay_record = database.get_payment_request(payment_id)
        if pay_record and "amount" in pay_record:
            cost_inr = pay_record["amount"]
        else:
            global_settings = database.get_global_settings()
            price_per_id = global_settings.get("price_per_id", 10.0)
            cost_inr = price_per_id * qty
            
        wallet_bal = user.get("wallet_balance", 0.0)
        
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
        from handlers.settings import show_settings_menu
        await show_settings_menu(event, user_id)

    # ==================== Payment Handler (UPI/USDT/TON details) ====================
    @client.on(events.CallbackQuery(pattern=r"^pay_method_(upi|usdt|ton)_(\d+)_(.+)$"))
    async def pay_invoice_callback(event):
        method = event.pattern_match.group(1)
        qty = int(event.pattern_match.group(2))
        payment_id = event.pattern_match.group(3)
        user_id = event.sender_id
        
        user = database.get_user(user_id)
        lang = user.get("language", "en") if user else "en"
        
        # Fetch payment record to get exact amount
        pay_record = database.get_payment_request(payment_id)
        if pay_record and "amount" in pay_record:
            cost_inr = pay_record["amount"]
        else:
            global_settings = database.get_global_settings()
            price_per_id = global_settings.get("price_per_id", 10.0)
            cost_inr = price_per_id * qty
            
        global_settings = database.get_global_settings()
        
        # Save state
        _payment_user_states[user_id] = {
            "payment_id": payment_id,
            "method": method,
            "qty": qty,
            "amount": cost_inr,
            "action": "WAITING_FOR_SCREENSHOT"
        }
        
        if method == "upi":
            upi_id = global_settings.get('upi_id', 'merchant@upi')
            address_text = f"🏦 UPI ID: `{upi_id}`"
            import urllib.parse
            import config
            pn_encoded = urllib.parse.quote(config.BOT_NAME)
            upi_uri = f"upi://pay?pa={upi_id}&pn={pn_encoded}&am={cost_inr:.2f}&cu=INR&tn={payment_id}"
            qr_url = f"https://api.qrserver.com/v1/create-qr-code/?size=300x300&format=jpg&data={urllib.parse.quote(upi_uri)}"
            amount_text = f"Amount To Pay: <b>₹{cost_inr:.2f}</b>"
        elif method == "usdt":
            address_text = f"🪙 Usdt (BEP20) Address:\n`{global_settings.get('usdt_bep20_address', '0x000')}`"
            qr_url = None
            usdt_rate = global_settings.get("usdt_rate", 90.0)
            cost_crypto = cost_inr / usdt_rate if usdt_rate > 0 else 0
            amount_text = f"Amount To Pay: <b>₹{cost_inr:.2f}</b> (Approx <b>${cost_crypto:.2f} Usdt</b>)"
        else: # ton
            address_text = f"💎 Ton (Toncoin) Address:\n`{global_settings.get('ton_address', 'UQ00000000000000000000000000000000000000000000000')}`"
            qr_url = None
            ton_rate = global_settings.get("ton_rate", 500.0)
            cost_crypto = cost_inr / ton_rate if ton_rate > 0 else 0
            amount_text = f"Amount To Pay: <b>₹{cost_inr:.2f}</b> (Approx <b>{cost_crypto:.2f} Ton</b>)"
            
        text = (
            f"<blockquote><b>» 💳 Make Payment</b>\n\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"Method: <b>{method.upper()}</b>\n"
            f"{amount_text}\n"
            f"{address_text}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📸 Send Your Payment Confirmation <b>Screenshot (As A Photo Or Image Link)</b>:</blockquote>"
        )
        
        buttons = [[utils.styled_button("❌ Cancel", "menu_settings", style="danger")]]
        if qr_url:
            import urllib.request
            import tempfile
            import os
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
            
            buttons = [[utils.styled_button("❌ Cancel", "menu_settings", style="danger")]]
            await event.reply(
                "📸 <b>Screenshot received!</b>\n\n"
                "🔢 <b>Now enter your UTR / Transaction Hash:</b>\n"
                "Example: `612207806800` or transaction reference code.",
                buttons=buttons
            )

        # 2. UTR transaction code input
        elif action == "WAITING_FOR_UTR":
            utr_code = event.text.strip()
            if not utr_code:
                await event.reply("❌ Please enter a valid Transaction Hash/UTR:")
                return
                
            # Pop state
            _payment_user_states.pop(user_id, None)
            
            payment_id = state["payment_id"]
            method = state["method"]
            qty = state["qty"]
            amount = state["amount"]
            photo_file_id = state["photo_file_id"]
            
            # Save detail in payment record
            pay_record = database.get_payment_request(payment_id)
            if pay_record:
                pay_record["method"] = method
                pay_record["utr_code"] = utr_code
                if isinstance(photo_file_id, str):
                    pay_record["screenshot"] = photo_file_id
                else:
                    pay_record["screenshot"] = f"photo_{photo_file_id.id}"
                database.save_payment_request(pay_record)
                
            # Confirm to User
            await event.reply("📩 <b>Payment submitted successfully!</b>\nAn administrator will review your submission shortly.")
            
            # Forward notification and screenshot to Admin log group
            global_settings = database.get_global_settings()
            log_group_id = global_settings.get("log_group_id")
            if log_group_id:
                try:
                    user_mention = f"[{user_id}](tg://user?id={user_id})"
                    admin_text = (
                        f"🚨 <b>New Payment Verification Request</b>\n"
                        f"━━━━━━━━━━━━━━━━━━━━\n"
                        f"User: {user_mention} (`{user_id}`)\n"
                        f"Method: <b>{method.upper()}</b>\n"
                        f"Amount: <b>₹{amount:.2f}</b> (qty: {qty})\n"
                        f"UTR/Hash: `{utr_code}`\n"
                        f"━━━━━━━━━━━━━━━━━━━━"
                    )
                    
                    buttons = [
                        [
                            utils.styled_button("✅ Approve", f"approve_payment_{payment_id}", style="success"),
                            utils.styled_button("❌ Reject", f"reject_payment_{payment_id}", style="danger")
                        ]
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
