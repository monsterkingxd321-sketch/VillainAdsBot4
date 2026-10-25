"""Offline test for the branding engine (no network / no Telegram)."""
import asyncio
import sys
import types

from villain_ads_bot import config

# --- stub the database so no MongoDB is required ---------------------
from villain_ads_bot.data import database

_FAKE_SETTINGS = {
    "branding_name_enabled": True,
    "branding_bio_enabled": True,
    "branding_name_text": " via @Bot",
    "branding_bio_text": " via @Bot",
    "branding_username": "Bot",
}

database.get_global_settings = lambda: dict(_FAKE_SETTINGS)
database.save_session = lambda s: None

from villain_ads_bot.services import userbot as U

TG_NAME = {"value": "John"}
TG_BIO = {"value": "Original bio"}


class FakeFullUser:
    def __init__(self):
        self.users = [types.SimpleNamespace(id=1, first_name=TG_NAME["value"], last_name="")]
        self.full_user = types.SimpleNamespace(about=TG_BIO["value"])


class FakeClient:
    def __init__(self):
        self.updates = []

    async def __call__(self, req):
        if isinstance(req, U.GetFullUserRequestStub):
            return FakeFullUser()
        # UpdateProfileRequest - Telethon only applies the fields that are set
        if hasattr(req, "first_name") and req.first_name is not None:
            TG_NAME["value"] = req.first_name
        if hasattr(req, "about") and req.about is not None:
            TG_BIO["value"] = req.about
        self.updates.append(req)
        return True


# GetFullUserRequest is imported inside the function, so patch the module attr
from telethon.tl.functions.users import GetFullUserRequest
from telethon.tl.functions.account import UpdateProfileRequest
U.GetFullUserRequestStub = GetFullUserRequest


async def main():
    client = FakeClient()
    sess = {"phone": "+911", "user_id": 1}

    # 1. First apply
    await U.apply_branding(client, "Bot", sess)
    print("1) after first apply   :", repr(TG_NAME["value"]), "|", repr(TG_BIO["value"]))
    assert TG_NAME["value"] == "John via @Bot", TG_NAME["value"]
    assert TG_BIO["value"] == "Original bio via @Bot", TG_BIO["value"]

    # 2. Change the brand -> must NOT stack
    _FAKE_SETTINGS["branding_name_text"] = " via @NewBot"
    _FAKE_SETTINGS["branding_bio_text"] = " via @NewBot"
    await U.apply_branding(client, "Bot", sess)
    print("2) after brand change  :", repr(TG_NAME["value"]), "|", repr(TG_BIO["value"]))
    assert TG_NAME["value"] == "John via @NewBot", TG_NAME["value"]
    assert TG_BIO["value"] == "Original bio via @NewBot", TG_BIO["value"]

    # 3. Disable branding -> must revert to the pure profile
    _FAKE_SETTINGS["branding_name_enabled"] = False
    _FAKE_SETTINGS["branding_bio_enabled"] = False
    await U.apply_branding(client, "Bot", sess)
    print("3) after disable       :", repr(TG_NAME["value"]), "|", repr(TG_BIO["value"]))
    assert TG_NAME["value"] == "John", TG_NAME["value"]
    assert TG_BIO["value"] == "Original bio", TG_BIO["value"]

    # 4. Re-enable -> applied again from the pure base
    _FAKE_SETTINGS["branding_name_enabled"] = True
    _FAKE_SETTINGS["branding_bio_enabled"] = True
    await U.apply_branding(client, "Bot", sess)
    print("4) after re-enable     :", repr(TG_NAME["value"]), "|", repr(TG_BIO["value"]))
    assert TG_NAME["value"] == "John via @NewBot", TG_NAME["value"]

    # 5. Bio only branding (name branding toggle OFF -> name must stay untouched)
    _FAKE_SETTINGS["branding_name_enabled"] = False
    _FAKE_SETTINGS["branding_bio_enabled"] = True
    _FAKE_SETTINGS["branding_name_text"] = ""
    _FAKE_SETTINGS["branding_bio_text"] = " | powered by X"
    TG_NAME["value"] = "PureName"
    TG_BIO["value"] = "PureBio"
    sess2 = {"phone": "+922", "user_id": 2}
    await U.apply_branding(client, "Bot", sess2)
    print("5) bio only            :", repr(TG_NAME["value"]), "|", repr(TG_BIO["value"]))
    assert TG_NAME["value"] == "PureName", TG_NAME["value"]
    assert TG_BIO["value"] == "PureBio | powered by X", TG_BIO["value"]

    # 6. Name branding ON, bio branding OFF -> only the name changes
    _FAKE_SETTINGS["branding_name_enabled"] = True
    _FAKE_SETTINGS["branding_bio_enabled"] = False
    _FAKE_SETTINGS["branding_name_text"] = " via @Brand"
    _FAKE_SETTINGS["branding_bio_text"] = ""
    TG_NAME["value"] = "NameOnly"
    TG_BIO["value"] = "BioUntouched"
    sess3 = {"phone": "+933", "user_id": 3}
    await U.apply_branding(client, "Bot", sess3)
    print("6) name only           :", repr(TG_NAME["value"]), "|", repr(TG_BIO["value"]))
    assert TG_NAME["value"] == "NameOnly via @Brand", TG_NAME["value"]
    assert TG_BIO["value"] == "BioUntouched", TG_BIO["value"]

    # 7. Pre-existing untracked branding (e.g. '@BharatMusicBot') revert
    TG_NAME["value"] = "Samiya @BharatMusicBot"
    TG_BIO["value"] = "Bio line @BharatMusicBot"
    sess4 = {"phone": "+944", "name": "Samiya @BharatMusicBot"}
    await U._revert_branding(client, sess4)
    print("7) untracked revert    :", repr(TG_NAME["value"]), "|", repr(TG_BIO["value"]))
    assert TG_NAME["value"] == "Samiya", TG_NAME["value"]
    assert TG_BIO["value"] == "Bio line", TG_BIO["value"]
    assert sess4["branding_applied_name_suffix"] == ""
    assert sess4["branding_applied_bio_suffix"] == ""

    print("\nALL BRANDING TESTS PASSED")


asyncio.run(main())
