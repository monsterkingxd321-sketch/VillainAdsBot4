import subprocess
import re
import os
import sys
import time
import threading
import urllib.request
import json
from dotenv import load_dotenv

# Define paths
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
env_path = os.path.join(BASE_DIR, ".env")

def log(msg):
    print(f"[RUNNER] {msg}", flush=True)

def update_env_url(new_url):
    log(f"Updating .env file with new URL: {new_url}")
    if not os.path.exists(env_path):
        log("Error: .env file not found!")
        return
        
    with open(env_path, "r", encoding="utf-8") as f:
        lines = f.readlines()
        
    updated = False
    for i, line in enumerate(lines):
        if line.strip().startswith("PERSONAL_WEBAPP_URL="):
            lines[i] = f'PERSONAL_WEBAPP_URL="{new_url}"\n'
            updated = True
            break
            
    if not updated:
        lines.append(f'\nPERSONAL_WEBAPP_URL="{new_url}"\n')
        
    with open(env_path, "w", encoding="utf-8") as f:
        f.writelines(lines)
        
    log(".env file updated successfully.")

def update_telegram_button(new_url):
    log("Updating Telegram Menu Button via HTTP API...")
    load_dotenv(env_path, override=True)
    bot_token = os.getenv("PERSONAL_BOT_TOKEN") or os.getenv("BOT_TOKEN")
    if not bot_token:
        log("Error: Bot token not found in environment!")
        return
        
    api_url = f"https://api.telegram.org/bot{bot_token}/setChatMenuButton"
    payload = {
        "menu_button": {
            "type": "web_app",
            "text": "🚀 Connect App",
            "web_app": {
                "url": new_url
            }
        }
    }
    try:
        req = urllib.request.Request(
            api_url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            log("Telegram Bot Menu Button successfully updated.")
    except Exception as e:
        log(f"Failed to update Bot Menu Button: {e}")

def start_tunnel():
    log("Starting localtunnel via npx...")
    cmd = ["npx.cmd", "-y", "localtunnel", "--port", "5000", "--local-host", "127.0.0.1", "--subdomain", "v90001adsbot"]
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1
    )
    return proc

def wait_for_tunnel_url(tunnel_proc):
    start_time = time.time()
    while time.time() - start_time < 30:
        try:
            line = tunnel_proc.stdout.readline()
            if not line:
                break
            stripped_line = line.strip()
            if stripped_line:
                print(f"[TUNNEL] {stripped_line}", flush=True)
                
            match = re.search(r"https://[a-zA-Z0-9.-]+\.loca\.lt", line)
            if match:
                return match.group(0)
        except Exception as e:
            print(f"Error reading tunnel output: {e}", flush=True)
            break
    return None

def run_tunnel_and_bot():
    # 1. Start tunnel
    tunnel_proc = start_tunnel()
    public_url = wait_for_tunnel_url(tunnel_proc)
    
    if not public_url:
        log("Error: Could not extract public URL from tunnel. Terminating.")
        tunnel_proc.terminate()
        sys.exit(1)
        
    log(f"FOUND PUBLIC TUNNEL URL: {public_url}")
    update_env_url(public_url)
    update_telegram_button(public_url)
    
    # Reload dotenv so the main script picks up the updated environment vars
    load_dotenv(env_path, override=True)
    
    # 2. Start bot process
    log("Starting Bot process...")
    bot_cmd = [sys.executable, "-u", os.path.join(BASE_DIR, "main.py")]
    child_env = os.environ.copy()
    child_env["PYTHONUNBUFFERED"] = "1"
    child_env["PYTHONIOENCODING"] = "utf-8"
    
    bot_proc = subprocess.Popen(
        bot_cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        env=child_env
    )
    
    def log_bot_output():
        while True:
            line = bot_proc.stdout.readline()
            if not line:
                break
            print(f"[BOT] {line.strip()}", flush=True)
            
    threading.Thread(target=log_bot_output, daemon=True).start()
    
    # 3. Process monitor and self-healing loop
    failures = 0
    try:
        while True:
            # If bot died, exit runner
            if bot_proc.poll() is not None:
                log("Bot process exited. Terminating tunnel and runner.")
                break
                
            # If tunnel process exited, restart it immediately
            if tunnel_proc.poll() is not None:
                log("Tunnel process exited. Restarting tunnel...")
                tunnel_proc = start_tunnel()
                new_url = wait_for_tunnel_url(tunnel_proc)
                if new_url:
                    public_url = new_url
                    update_env_url(public_url)
                    update_telegram_button(public_url)
                failures = 0
                
            # Ping check active tunnel
            if public_url:
                try:
                    req = urllib.request.Request(
                        public_url,
                        headers={"Bypass-Tunnel-Reminder": "true", "User-Agent": "Mozilla/5.0"}
                    )
                    # Small 5s timeout to catch drops/freezes
                    with urllib.request.urlopen(req, timeout=5) as resp:
                        failures = 0
                except Exception as e:
                    failures += 1
                    log(f"Tunnel ping failed ({failures}/3): {e}")
                    if failures >= 3:
                        log("WARNING: Tunnel is unresponsive/503 for 3 consecutive checks. Initiating self-healing restart...")
                        try:
                            tunnel_proc.terminate()
                        except:
                            pass
                        time.sleep(2)
                        
                        tunnel_proc = start_tunnel()
                        new_url = wait_for_tunnel_url(tunnel_proc)
                        if new_url:
                            public_url = new_url
                            log(f"Tunnel healed successfully! New URL: {public_url}")
                            update_env_url(public_url)
                            update_telegram_button(public_url)
                        failures = 0
                        
            time.sleep(60)
            
    except KeyboardInterrupt:
        log("Shutdown requested by user.")
    finally:
        log("Cleaning up processes...")
        try:
            bot_proc.terminate()
        except:
            pass
        try:
            tunnel_proc.terminate()
        except:
            pass
        log("Runner finished.")

if __name__ == "__main__":
    run_tunnel_and_bot()
