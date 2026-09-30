import time
from typing import Dict, Any, List

def create_default_user(user_id: int) -> Dict[str, Any]:
    """
    Returns a dictionary structure representing a new bot user.
    Each user gets 1 free ID slot and must accept the TOS and select a language.
    """
    return {
        "user_id": user_id,
        "language": None,          # Selected on first /start
        "tos_accepted": False,      # Agreement to terms
        "allowed_slots": 1,         # Freemium model: 1 free ID
        "wallet_balance": 0.0,      # Wallet balance in INR
        "referred_by": None,        # Referrer User ID
        "is_banned": False          # Banned status
    }

def create_default_session(session_id: str, user_id: int, phone: str, session_file: str) -> Dict[str, Any]:
    """
    Returns a dictionary structure representing a Telegram account (session) added by a user.
    """
    return {
        "session_id": session_id,   # Unique string identifier for the session (usually phone)
        "user_id": user_id,         # Owner of the session
        "phone": phone,
        "session_file": session_file, # Local path to .session file
        "name": "",                 # Current Telegram account name
        "username": "",             # Telegram username
        "original_name": "",        # Saved original first/last name for restoration
        "original_bio": "",         # Saved original bio for restoration
        "status": "stopped",        # "running" or "stopped"
        "settings": {
            "broadcast_msg": "Hello! This is an automated broadcast message.",
            "welcome_msg": "Hello! Welcome to our chat.",
            "welcome_messages": [],
            "auto_spam": False,       # Auto Broadcast (broadcast message) toggle
            "auto_welcome": False,    # Auto-Welcome message toggle
            "auto_add_contact": False, # Auto-add to contacts on tag/reply toggle
            "vc_join": False,         # Auto-Join voice chat toggle
            "auto_reply": False,      # Tag Auto-Reply toggle
            "auto_reply_mode": "single", # Auto-Reply mode: single or multiple
            "auto_reply_msg": "Hello! How can I help you?",
            "auto_reply_messages": [
                "Hello! How can I help you? 😊",
                "Hey there! Thanks for reaching out. Please leave a message! 💬",
                "Hello! I am currently away, will get back to you soon. ✨"
            ],
            "tag_reply": False,       # Tag Reply toggle (legacy backward-compatibility)
            "tag_messages": [
                "Hey! You mentioned me?",
                "Hello, I am currently busy. Let's chat later!",
                "Yes? Mention received. 📱"
            ],
            "broadcast_interval": 300, # default interval in seconds
            "gpt_enabled": False      # Use OpenAI API for replying
        },
        "stats": {
            "group_count": 0,
            "user_count": 0,
            "welcomed_users": [],     # List of user IDs that received a welcome message (for one-time check)
            "broadcast_count": 0
        }
    }

def create_payment_request(payment_id: str, user_id: int, count: int) -> Dict[str, Any]:
    """
    Returns a dictionary structure representing a payment approval request for extra ID slots.
    """
    return {
        "payment_id": payment_id,   # Unique request ID
        "user_id": user_id,         # Requesting user
        "count": count,             # Number of requested extra IDs (1-5)
        "status": "pending",        # "pending", "approved", "rejected"
        "timestamp": time.time()
    }
