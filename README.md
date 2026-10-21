# 𝗫𝗧𝗥 𝗔𝗗 𝗕𝗢𝗧

A powerful Telegram Manager Bot and Userbot that handles advertisements, automation, music streaming, and voice chat operations seamlessly.

## 🌟 Features
- Complete multi-userbot management with a premium UI.
- Advanced Music Streaming in VC (Powered by PyTgCalls and Yuki/Meow music API).
- Automated Advertisements, Broadcasts, DM Welcome messages and Tag Auto-Reply.
- **Bulk "All Slots" dashboard** — control every userbot at once.
- **Multiple API IDs and Hashes** (env *and* admin panel) to prevent flood-waits.
- **Slot pricing in INR** with automatic USDT / TON conversion.
- **Mandatory branding** that no user can remove (branding lock).
- Profile **cloning** (name, bio, static *and* video profile photos).
- **Session file discovery** — load and log in every account from disk.
- Error reporting to the **log group and the owner** (never raw tracebacks to users).
- Integrated MongoDB Database.

---

## 📁 Project Structure

```
.
├── main.py                     # 🚀 Entry point (manager bot + userbot runtime)
├── bot.py                      # Legacy entry point
├── requirements.txt
├── .env.example
│
└── villain_ads_bot/            # 📦 The application package
    ├── __init__.py             #    Package metadata / version
    ├── config.py               #    ⚙️  Environment + default global settings
    ├── translations.py         #    🌍 Multi-language message catalogue
    ├── thumb_gen.py            #    🖼️  Now-playing thumbnail renderer
    │
    ├── core/                   #    🧱 Framework agnostic helpers
    │   ├── utils.py            #       Buttons, guards, text tools, pytgcalls compat
    │   ├── error_reporter.py   #       Log-group + owner error reporting
    │   ├── pricing.py          #       INR ⇄ USDT / TON conversion
    │   ├── music.py            #       Music gateway credentials (key / url)
    │   ├── joiner.py           #       Bulk joining with flood-limit control
    │   └── ui.py               #       Premium UI card builders
    │
    ├── data/                   #    💾 Persistence layer
    │   ├── database.py         #       MongoDB access layer
    │   └── models.py           #       Document factories
    │
    ├── services/               #    ⚙️  Domain services
    │   ├── userbot.py          #       UserBot runtime (Telethon + PyTgCalls)
    │   ├── userbot_manager.py  #       Registry, clone, branding, session files
    │   └── youtube.py          #       YouTube / Yuki music API
    │
    └── handlers/               #    🎛️  Telegram event handlers
        ├── __init__.py         #       Registration entry point
        ├── start.py            #       /start, onboarding, main menu
        ├── callbacks.py        #       Global callback guard
        ├── add_bot.py          #       Session import + login flow
        ├── my_bots.py          #       Userbot dashboards (single + all slots)
        ├── settings.py         #       User settings + slot purchase
        ├── admin.py            #       Owner control panel
        ├── status.py           #       System status dashboard
        ├── player.py           #       VC player (play/pause/skip/queue)
        ├── payments.py         #       Admin payment approval
        └── payments_extended.py#       Checkout, UPI / USDT / TON invoices
```

### Layer rules
| Layer | May import | Must never import |
|-------|-----------|-------------------|
| `core/` | `config`, `data`, `translations` | `services`, `handlers` |
| `data/` | `config` | `core`, `services`, `handlers` |
| `services/` | `config`, `data`, `core` | `handlers` (only lazily, inside functions) |
| `handlers/` | everything | — |

---

## 📋 Requirements
- Python 3.10+
- FFmpeg (Required for audio/video streaming)
- Git

---

## 🚀 Deployment on VPS (Ubuntu/Debian)

Follow these exact steps to host your bot on a fresh VPS:

### 1. Update System Packages
```bash
sudo apt-get update -y && sudo apt-get upgrade -y
```

### 2. Install Required Dependencies
```bash
sudo apt-get install -y python3 python3-venv python3-pip ffmpeg git tmux
```

### 3. Clone the Repository
```bash
git clone <YOUR_GITHUB_REPO_URL>
cd <REPOSITORY_FOLDER_NAME>
```
*(Make sure to replace `<YOUR_GITHUB_REPO_URL>` with the URL of your GitHub repository once you upload it)*

### 4. Setup Python Virtual Environment
*(This is critical on newer Ubuntu versions to avoid PEP 668 errors)*
```bash
python3 -m venv myenv
source myenv/bin/activate
```

### 5. Install Python Requirements
```bash
pip install --upgrade pip
pip install -r requirements.txt
```

### 6. Environment Configuration
Create a `.env` file in your root folder:
```bash
nano .env
```
Paste your environment variables (edit with your own details):
```env
BOT_TOKEN=your_bot_token_here
API_ID=your_api_id
API_HASH=your_api_hash
MONGODB_URI=your_mongodb_connection_string
OWNER_ID=your_telegram_user_id
LOG_GROUP_ID=-100xxxxxxxxxx

# Optional: extra API pairs (API_ID1 / API_HASH1, API_ID2 / API_HASH2, ...)
# You can also add them from the admin panel at runtime.

# Music gateway (Yuki / Meow)
MEOW_API_URL=https://music.yukiapi.site
MEOW_BACKUP_API_URL=https://play.yukiapi.site
MEOW_API_KEY=yuki_xxxxxxxxxxxxxxxx

# Slot pricing (INR). USDT / TON amounts are derived automatically.
PRICE_PER_SLOT=10
USDT_RATE=90
TON_RATE=500
```
Save and exit (`Ctrl+O`, `Enter`, `Ctrl+X`).

> 💡 Values that are **blank** in `.env` never override what you set in the
> admin panel — the panel always wins once a value was saved.

### 7. Run the Bot (Background Mode)
We will use `tmux` so the bot stays online even when you close the SSH terminal.

1. Start a new tmux session:
```bash
tmux new -s adbot
```
2. Activate environment and run the bot:
```bash
source myenv/bin/activate
python3 main.py
```
3. Detach from the session safely: Press **`Ctrl + B`**, then press **`D`**.

*(To view the bot's logs again later, type: `tmux attach -t adbot`)*

---

## 👑 Admin Panel cheat-sheet

| Action | Where |
|--------|-------|
| Set the **price of one slot** in INR | Admin Panel → 💸 Set Price Per Slot |
| Set the **USDT / TON rate** | Admin Panel → Set Usdt Rate / Set Ton Rate |
| Add **multiple API_ID / API_HASH** pairs | Admin Panel → 🔐 API ID / API Hash Manager |
| Set the **music API key / gateway** | Admin Panel → 🎵 Music API Key |
| Set the **branding** (name / bio suffix) | Admin Panel → 🎨 Branding Settings |
| **Apply branding to every userbot now** | Branding Settings → 🚀 Apply To All Userbots |
| **Lock the branding** (users cannot remove it) | Branding Settings → 🔒 Toggle Branding Lock |
| **Load every account from the session files** | Admin Panel → 📂 Load Accounts From Session Files |
| **Log in every account from disk** | → 🔑 Login All Accounts From Disk |
| Check the **VPS usage** | Admin Panel → 🖥️ Vps Usage |
| **Broadcast right now** (live progress) | Userbot dashboard → 📢 Broadcast Now |

> ℹ️ `villain_ads_bot/services/youtube.py` is kept **exactly** as the music API
> owner publishes it. The runtime configuration (API key / gateway) is pushed
> into it from `villain_ads_bot/core/music.py` — the module itself is never
> modified.

---

## 🛠 Useful Commands for VPS Management

- **Check disk space:** `df -h`
- **Clear RAM Cache:** `sudo sysctl -w vm.drop_drops=3`
- **Kill all tmux sessions:** `tmux kill-server`
- **List tmux sessions:** `tmux ls`

---

## ⚠️ Troubleshooting (For Minimal VPS / Docker Containers)

If you are running this on a completely fresh, minimal container (like a Docker sandbox) where commands like `sudo`, `python3`, or `nano` are missing, it means you are logged in as the `root` user and the OS is extremely stripped down.

Run these exact commands instead of the ones in Step 1 & 2 (do not use `sudo`):
```bash
apt-get update -y && apt-get upgrade -y
apt-get install -y python3 python3-venv python3-pip ffmpeg git nano tmux
```
After that, continue from **Step 3**.

