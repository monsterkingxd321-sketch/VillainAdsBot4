# 𝗫𝗧𝗥 𝗔𝗗 𝗕𝗢𝗧

A powerful Telegram Manager Bot and Userbot that handles advertisements, automation, music streaming, and voice chat operations seamlessly.

## 🌟 Features
- Complete multi-userbot management.
- Advanced Music Streaming in VC (Powered by PyTgCalls and Pyrogram).
- Automated Advertisements and Broadcasts.
- Supports Multiple API IDs and Hashes to prevent floodwaits.
- Integrated MongoDB Database.

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
```
Save and exit (`Ctrl+O`, `Enter`, `Ctrl+X`).

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

## 🛠 Useful Commands for VPS Management

- **Check disk space:** `df -h`
- **Clear RAM Cache:** `sudo sysctl -w vm.drop_caches=3`
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
