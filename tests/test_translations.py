"""
Guard test: every translation key that the code uses must exist in ALL
supported languages. A new screen with a missing translation fails here.
"""
import os
import re
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
PKG = os.path.join(ROOT, "villain_ads_bot")

sys.path.insert(0, ROOT)

from villain_ads_bot.translations import TRANSLATIONS
from villain_ads_bot.premium_translations import PREMIUM_TRANSLATIONS

LANGS = ("en", "hi", "ru", "ja", "fr", "de", "zh", "ar")

# every language must have the same keys
missing_per_lang = {}
for lang in LANGS:
    missing = [k for k in TRANSLATIONS["en"] if k not in TRANSLATIONS.get(lang, {})]
    if missing:
        missing_per_lang[lang] = missing

# premium keys
for lang in LANGS:
    missing = [k for k in PREMIUM_TRANSLATIONS
               if lang not in PREMIUM_TRANSLATIONS[k]]
    if missing:
        missing_per_lang.setdefault(lang, []).extend(missing)

# find every get_text("key") usage in the source
used = set()
pattern = re.compile(r"""get_text\(\s*["']([a-zA-Z0-9_]+)["']""")
for dirpath, dirnames, filenames in os.walk(PKG):
    dirnames[:] = [d for d in dirnames if d != "__pycache__"]
    for fn in filenames:
        if not fn.endswith(".py"):
            continue
        with open(os.path.join(dirpath, fn), encoding="utf-8") as f:
            used |= set(pattern.findall(f.read()))

unknown = []
for key in sorted(used):
    if key in TRANSLATIONS["en"] or key in PREMIUM_TRANSLATIONS:
        continue
    unknown.append(key)

print(f"languages            : {len(LANGS)}")
print(f"legacy keys (en)     : {len(TRANSLATIONS['en'])}")
print(f"premium keys         : {len(PREMIUM_TRANSLATIONS)}")
print(f"keys used in code    : {len(used)}")
print()

problems = 0
if missing_per_lang:
    for lang, keys in missing_per_lang.items():
        problems += 1
        print(f"MISSING in '{lang}' ({len(keys)}): {', '.join(keys[:12])}")
if unknown:
    problems += 1
    print(f"UNKNOWN keys used in the code ({len(unknown)}): {', '.join(unknown[:12])}")

if problems:
    raise SystemExit(f"\n{problems} translation problem(s) found")
print("ALL TRANSLATIONS COMPLETE (every key exists in all 8 languages)")
