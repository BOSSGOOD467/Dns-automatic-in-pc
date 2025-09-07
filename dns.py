#!/usr/bin/env python3
# dns_switcher.py — Ultimate merged DNS switcher (Chart.js dashboard)
# Requirements: flask, psutil, requests, dnspython
# pip install flask psutil requests dnspython

import os
import re
import sys
import time
import json
import ctypes
import signal
import platform
import subprocess
import threading
import logging
import csv
import psutil
import requests
import dns.resolver
from statistics import median
from concurrent.futures import ThreadPoolExecutor, as_completed
from logging.handlers import RotatingFileHandler
from flask import Flask, jsonify, render_template_string, request as flask_request
from shutil import which as shutil_which
from datetime import datetime
from collections import deque

# -------------------------
# CONFIG / DEFAULTS
# -------------------------
CONFIG_FILE = "config.json"
STATE_FILE = "dns_state.txt"
LOG_FILE = "dns_switcher.log"
CSV_FILE = "dns_history.csv"
MAX_LOG_BYTES = 10_000_000
LOG_BACKUPS = 3

DEFAULT_CONFIG = {
    "interval": 60,
    "threads": 10,
    "dns_query_count": 3,
    "dns_query_timeout_s": 1,
    "dns_query_delay_s": 0,  # [OPTIMASI] Default diubah ke 0 untuk benchmark lebih cepat
    "dns_query_domain": "google.com",
    "use_ipv6": True,
    "auto_disable_ipv6": True,
    "dashboard": {"enabled": True, "host": "127.0.0.1", "port": 8080, "refresh_s": 5},
    "fallback_dns": ["8.8.8.8", "1.1.1.1", "9.9.9.9"],
    "auto_restart_adapter": True,
    "game_pause": True,
    "game_cache_seconds": 3,
    "dns_update_url": "",
    "custom_dns": [],
    "games": [],
    "clear_terminal": True,
    "max_terminal_lines": 100,
    # [FITUR BARU] Opsi mode manual
    "dns_selection_mode": "auto",  # Opsi: "auto" atau "manual"
    "manual_dns": ["8.8.8.8"],     # DNS yang digunakan jika mode "manual"
}

# Master DNS lists (expanded)
DNS_IPV4_MASTER = [
    "8.8.8.8","8.8.4.4","1.1.1.1","1.0.0.1","9.9.9.9","149.112.112.112",
    "208.67.222.222","208.67.220.220","8.26.56.26","8.20.247.20",
    "185.228.168.9","185.228.169.9","94.140.14.14","94.140.15.15",
    "84.200.69.80","84.200.70.40","77.88.8.8","77.88.8.1",
    "4.2.2.1","4.2.2.2","37.235.1.174","37.235.1.177",
    "76.76.19.19","76.223.122.150","94.247.43.254","38.132.106.139","199.85.126.10",
    "64.6.64.6","64.6.65.6","156.154.70.1","156.154.71.1","185.121.177.177",
    "198.101.242.72","195.46.39.39","192.71.245.208","216.87.84.211",
    "178.22.122.100","45.90.28.0","45.90.30.0","1.1.1.2","1.0.0.2"
]

DNS_IPV6_MASTER = [
    "2606:4700:4700::1111","2606:4700:4700::1001","2001:4860:4860::8888","2001:4860:4860::8844",
    "2620:fe::fe","2620:fe::9","2a0d:2a00:1::1","2a0d:2a00:2::2",
    "2a01:4f8:fff0:200::2","2a01:4f8:fff0:200::3"
]

GAMES_BASE = ["valorant.exe","csgo.exe","dota2.exe","pubg.exe","apex.exe","fortnite.exe","overwatch.exe",
    "leagueoflegends.exe","minecraft.exe","gta5.exe","rust.exe","rainbowsix.exe","warzone.exe",
    "rocketleague.exe","escape_from_tarkov.exe","palworld.exe","starfield.exe","eldenring.exe","tiktok.exe"
"worldofwarcraft.exe","fifa24.exe","genshinimpact.exe","hogwartslegacy.exe","roblox.exe","CombatMaster.exe","HD-Player.exe"]

# -------------------------
# LOGGING
# -------------------------
logger = logging.getLogger("dns_switcher")
logger.setLevel(logging.INFO)
handler = RotatingFileHandler(LOG_FILE, maxBytes=MAX_LOG_BYTES, backupCount=LOG_BACKUPS, encoding="utf-8")
handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
logger.addHandler(handler)

def log_info(msg):
    print("ℹ️", msg)
    logger.info(msg)

def log_warn(msg):
    print("⚠️", msg)
    logger.warning(msg)

def log_err(msg):
    print("❌", msg)
    logger.error(msg)

def show_error_popup(msg):
    """Windows popup error (fallback ke console)"""
    try:
        if platform.system() == "Windows":
            ctypes.windll.user32.MessageBoxW(0, msg, "DNS Switcher — Error", 0x10)
        else:
            print("❌", msg)
    except:
        print("❌", msg)

# -------------------------
# load config
# -------------------------
def load_config():
    cfg = DEFAULT_CONFIG.copy()
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                user = json.load(f)
            cfg.update(user)
        except Exception as e:
            log_warn(f"Gagal baca {CONFIG_FILE}: {e} — pake default")
    # validation
    cfg["interval"] = max(30, cfg.get("interval", 60))
    cfg["threads"] = max(1, min(50, cfg.get("threads", 10)))
    if "games" not in cfg:
        cfg["games"] = []

    # [PERBAIKAN KEAMANAN] Peringatan jika dashboard diekspos ke jaringan
    if cfg.get("dashboard", {}).get("enabled") and cfg.get("dashboard", {}).get("host") not in ["127.0.0.1", "localhost"]:
        log_warn(f"Dashboard host diatur ke '{cfg['dashboard']['host']}'. Ini bisa mengekspos dashboard ke jaringan Anda. Gunakan '127.0.0.1' untuk akses lokal saja.")
        
    return cfg

config = load_config()

# merge DNS lists (master + custom)
def build_dns_list():
    v4 = list(dict.fromkeys(DNS_IPV4_MASTER + [d for d in config.get("custom_dns", []) if ":" not in d]))
    v6 = list(dict.fromkeys(DNS_IPV6_MASTER + [d for d in config.get("custom_dns", []) if ":" in d]))
    return v4, v6

DNS_IPV4, DNS_IPV6 = build_dns_list()

# -------------------------
# admin check (cross-platform)
# -------------------------
def is_admin():
    try:
        if platform.system() == "Windows":
            return ctypes.windll.shell32.IsUserAnAdmin() != 0
        else:
            return os.geteuid() == 0
    except Exception:
        return False

# -------------------------
# IPv6 connectivity check
# -------------------------
def check_ipv6_connectivity():
    """Mencoba ping ke alamat IPv6 untuk memeriksa konektivitas."""
    log_info("Memeriksa konektivitas IPv6...")
    system = platform.system()
    try:
        if system == "Windows":
            cmd = ["ping", "-6", "-n", "1", "2001:4860:4860::8888"]
        else: # Linux, Darwin
            cmd = ["ping6", "-c", "1", "2001:4860:4860::8888"]
        
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            log_info("✓ Konektivitas IPv6 terdeteksi.")
            return True
        else:
            log_warn("⚠ Konektivitas IPv6 tidak ditemukan.")
            return False
    except (FileNotFoundError, subprocess.TimeoutExpired, Exception) as e:
        log_warn(f"Pemeriksaan IPv6 gagal: {e}")
        return False

# -------------------------
# network interfaces utils
# -------------------------
def get_interfaces():
    # Menambahkan komentar untuk menjelaskan logika
    system = platform.system()
    interfaces = []
    try:
        if system == "Windows":
            # Metode utama: 'netsh' untuk mendapatkan nama interface yang terhubung
            out = subprocess.run(["netsh", "interface", "show", "interface"], capture_output=True, text=True, encoding="utf-8")
            for line in out.stdout.splitlines():
                if "Connected" in line or "Terhubung" in line:
                    parts = line.split()
                    if len(parts) >= 4:
                        interfaces.append(" ".join(parts[3:]).strip())
        
        elif system in ["Linux", "Darwin"]:
            # Metode utama: 'nmcli' untuk Linux jika tersedia, ini lebih andal
            if shutil_which("nmcli"):
                out = subprocess.run(["nmcli", "-t", "-f", "DEVICE,STATE", "device"], capture_output=True, text=True)
                for line in out.stdout.splitlines():
                    if ":" in line:
                        dev, state = line.split(":", 1)
                        if state.strip() == "connected":
                            interfaces.append(dev.strip())
            else: 
                # Fallback untuk sistem non-nmcli (Linux dasar atau BSD)
                out = subprocess.run(["ip", "link", "show", "up"], capture_output=True, text=True)
                for line in out.stdout.splitlines():
                    m = re.match(r"\d+: (\S+): <", line)
                    if m and m.group(1) != 'lo':
                        interfaces.append(m.group(1))
    except Exception as e:
        log_warn(f"get_interfaces error: {e}")
    return interfaces

# -------------------------
# Latency Test using DNS Query
# -------------------------
def test_dns_latency(dns_server):
    resolver = dns.resolver.Resolver(configure=False)
    resolver.nameservers = [dns_server]
    resolver.timeout = config.get("dns_query_timeout_s", 1)
    resolver.lifetime = config.get("dns_query_timeout_s", 1)
    domain_to_query = config.get("dns_query_domain", "google.com")
    
    query_count = max(1, config.get("dns_query_count", 3))
    delay_s = config.get("dns_query_delay_s", 0)
    latencies = []
    
    for _ in range(query_count):
        try:
            start_time = time.monotonic()
            resolver.resolve(domain_to_query, 'A')
            end_time = time.monotonic()
            latencies.append(int((end_time - start_time) * 1000))
        # [BUG FIX] Menangkap exception yang lebih spesifik, bukan Exception umum
        except (dns.resolver.Timeout, dns.resolver.NoNameservers, dns.exception.DNSException):
            # Gagal resolve dianggap latensi tak terhingga, jadi kita abaikan
            pass
        if delay_s > 0:
            time.sleep(delay_s)

    if latencies:
        return int(median(latencies))
    return None

# -------------------------
# DNS set/reset (cross-platform)
# -------------------------
def set_dns_on_interface(interface, dns):
    system = platform.system()
    try:
        if system == "Windows":
            proto = 'ipv6' if ":" in dns else 'ip'
            cmd = ['netsh', 'interface', proto, 'set', 'dns', f'name="{interface}"', 'static', dns]
            res = subprocess.run(cmd, capture_output=True, text=True, check=False)
            ok = (res.returncode == 0)
            if ok:
                subprocess.run(["ipconfig", "/flushdns"], capture_output=True, check=False)
            return ok
        elif system == "Linux" and shutil_which("nmcli"):
            proto = "ipv6.dns" if ":" in dns else "ipv4.dns"
            res = subprocess.run(["nmcli", "connection", "modify", interface, proto, dns], capture_output=True, text=True, check=False)
            if res.returncode == 0:
                # Re-apply connection to take effect
                subprocess.run(["nmcli", "connection", "up", interface], capture_output=True, text=True, check=False)
                return True
            return False
        elif system == "Darwin":
            res = subprocess.run(["networksetup", "-setdnsservers", interface, dns], capture_output=True, text=True, check=False)
            return res.returncode == 0
    except Exception as e:
        log_warn(f"set_dns error: {e}")
    return False

def reset_dns_on_interface(interface):
    system = platform.system()
    try:
        if system == "Windows":
            subprocess.run(['netsh','interface','ip','set','dns', f'name="{interface}"', 'dhcp'], capture_output=True, check=False)
            subprocess.run(['netsh','interface','ipv6','set','dns', f'name="{interface}"', 'dhcp'], capture_output=True, check=False)
        elif system == "Linux" and shutil_which("nmcli"):
            subprocess.run(["nmcli", "connection", 'modify', interface, "ipv4.dns", ""], capture_output=True, check=False)
            subprocess.run(["nmcli", "connection", 'modify', interface, "ipv6.dns", ""], capture_output=True, check=False)
            subprocess.run(["nmcli", "connection", "up", interface], capture_output=True, text=True, check=False)
        elif system == "Darwin":
            subprocess.run(["networksetup","-setdnsservers",interface,"Empty"], capture_output=True, check=False)
    except Exception as e:
        log_warn(f"reset_dns error: {e}")

# -------------------------
# game detection
# -------------------------
def is_game_running():
    if not config.get("game_pause", True):
        return False
    
    game_list = GAMES_BASE + config.get("games", [])
    if not game_list:
        return False
    
    game_set = {g.lower() for g in game_list}
    try:
        for proc in psutil.process_iter(['name']):
            if proc.info['name'].lower() in game_set:
                return True
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
        pass
    except Exception as e:
        log_warn(f"Game detection error: {e}")
    
    return False

# -------------------------
# [PENYEMPURNAAN AKHIR] Fungsi verifikasi DNS yang sangat andal
# -------------------------
def verify_dns_change(interfaces, expected_dns):
    system = platform.system()
    time.sleep(2)  # Beri waktu sistem untuk menerapkan perubahan

    for interface in interfaces:
        try:
            if system == "Windows":
                # Metode 1: PowerShell (lebih andal dan tidak tergantung bahasa sistem)
                try:
                    cmd = f'powershell -Command "Get-NetIPConfiguration -InterfaceAlias \'{interface}\' | Select-Object -ExpandProperty DnsServer | Select-Object -ExpandProperty ServerAddresses | ConvertTo-Json -Compress"'
                    result = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=5)
                    dns_servers = json.loads(result.stdout)
                    # PowerShell mungkin mengembalikan satu string jika hanya ada satu DNS
                    if isinstance(dns_servers, str):
                        dns_servers = [dns_servers]
                    if expected_dns in dns_servers:
                        log_info(f"✓ Verifikasi via PowerShell di '{interface}' berhasil: {expected_dns} aktif.")
                        return True
                except (subprocess.CalledProcessError, FileNotFoundError, json.JSONDecodeError, subprocess.TimeoutExpired):
                    # Metode 2: Fallback ke 'ipconfig' jika PowerShell gagal atau tidak ada
                    log_warn("PowerShell gagal, fallback ke 'ipconfig'.")
                    result = subprocess.run(['ipconfig', '/all'], capture_output=True, text=True, encoding='utf-8', errors='ignore')
                    # Regex yang lebih fleksibel untuk berbagai tipe adapter (Ethernet, Wireless LAN, dll.)
                    pattern = re.compile(rf".*?adapter {re.escape(interface)}:.*?DNS Servers.*?: ([\d\.:\s]+)", re.DOTALL | re.IGNORECASE)
                    match = pattern.search(result.stdout)
                    if match and expected_dns in match.group(1):
                        log_info(f"✓ Verifikasi via ipconfig di '{interface}' berhasil: {expected_dns} aktif.")
                        return True

            elif system == "Linux" and shutil_which("nmcli"):
                # Verifikasi spesifik via nmcli, mencari baris yang relevan
                cmd = ['nmcli', 'dev', 'show', interface]
                result = subprocess.run(cmd, capture_output=True, text=True)
                for line in result.stdout.splitlines():
                    # Cari baris IP4.DNS atau IP6.DNS yang berisi DNS yang diharapkan
                    if (line.strip().startswith("IP4.DNS") or line.strip().startswith("IP6.DNS")) and expected_dns in line:
                        log_info(f"✓ Verifikasi via nmcli di '{interface}' berhasil: {expected_dns} aktif.")
                        return True

            elif system == "Darwin":
                # Verifikasi spesifik untuk macOS menggunakan perintah yang konsisten dengan setter
                cmd = ['networksetup', '-getdnsservers', interface]
                result = subprocess.run(cmd, capture_output=True, text=True)
                if expected_dns in result.stdout.splitlines():
                    log_info(f"✓ Verifikasi via networksetup di '{interface}' berhasil: {expected_dns} aktif.")
                    return True
        except Exception as e:
            log_warn(f"Gagal saat verifikasi DNS di '{interface}': {e}")
            continue  # Coba interface berikutnya jika ada

    log_warn(f"⚠ Verifikasi DNS gagal. {expected_dns} tidak ditemukan di interface aktif manapun.")
    return False

# -------------------------
# CSV history & Dashboard (Flask)
# -------------------------
def save_to_csv(dns, latency):
    file_exists = os.path.isfile(CSV_FILE)
    try:
        with open(CSV_FILE, 'a', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            if not file_exists:
                writer.writerow(['Timestamp', 'DNS', 'Latency (ms)'])
            writer.writerow([datetime.now().isoformat(), dns, latency])
    except IOError as e:
        log_err(f"Gagal menyimpan ke CSV: {e}")

def load_history_from_csv(limit=20):
    history = []
    if not os.path.isfile(CSV_FILE):
        return history
    try:
        with open(CSV_FILE, 'r', newline='', encoding='utf-8') as f:
            reader = csv.reader(f)
            next(reader, None) # Lewati header
            all_data = list(reader)
            for row in all_data[-limit:]:
                try:
                    timestamp_str, _, latency_str = row
                    dt_obj = datetime.fromisoformat(timestamp_str)
                    history.append({
                        "time": dt_obj.strftime("%H:%M:%S"),
                        "latency": int(latency_str)
                    })
                except (ValueError, IndexError):
                    continue
    except Exception as e:
        log_warn(f"Gagal memuat history dari CSV: {e}")
    return history

app = Flask(__name__)

# Lock ini penting untuk mencegah 'race condition' di mana thread utama (worker) menulis
# data bersamaan dengan thread Flask (dashboard) yang membacanya.
data_lock = threading.Lock()
dashboard_data = {
    "current_dns": "N/A",
    "best_dns": "N/A",
    "latency": 0,
    "status": "Initializing...",
    "last_update": "N/A",
    "history": load_history_from_csv(20)
}

def get_client_info():
    user_agent = flask_request.user_agent.string.lower()
    platform_name, browser_name = "Unknown", "Unknown"
    if 'windows' in user_agent: platform_name = "Windows"
    elif 'linux' in user_agent: platform_name = "Linux"
    elif 'mac' in user_agent: platform_name = "macOS"
    elif 'android' in user_agent: platform_name = "Android"
    elif 'iphone' in user_agent: platform_name = "iOS"
    
    if 'chrome' in user_agent and 'edg' not in user_agent: browser_name = "Chrome"
    elif 'firefox' in user_agent: browser_name = "Firefox"
    elif 'safari' in user_agent and 'chrome' not in user_agent: browser_name = "Safari"
    elif 'edg' in user_agent: browser_name = "Edge"
    elif 'opera' in user_agent: browser_name = "Opera"
    return {"client_platform": platform_name, "client_browser": browser_name
}

# [KEMBALI KE FORMAT AWAL] Kode HTML dikembalikan ke format multi-baris agar mudah dibaca.
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="id">
<head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width,initial-scale=1" />
    <title>DNS Switcher Pro – BOSSGOOD467</title>
    <link rel="icon" href="https://cdn-icons-png.flaticon.com/512/5977/5977585.png" type="image/png" />
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css" />
    <link href="https://fonts.googleapis.com/css2?family=Poppins:wght@300;400;500;600;700&display=swap" rel="stylesheet" />
    <style>
        :root{
            --primary:#4361ee; --secondary:#3f37c9; --info:#4895ef;
            --bg:#f0f2f5; --card:#ffffff; --text:#222;
            --good:#2bcb77; --warn:#f39c12; --bad:#e74c3c;
            --transition: all 0.35s ease;
        }
        [data-theme="dark"]{
            --bg:#0f1112; --card:#151617; --text:#e9eef6;
        }

        *{box-sizing:border-box}
        html,body{height:100%}
        body{
            margin:0; font-family:'Poppins',sans-serif;
            background:linear-gradient(180deg,var(--bg),#e9eef6);
            color:var(--text); transition:var(--transition);
        }

        header{
            display:flex; justify-content:space-between; align-items:center;
            padding:18px; background:linear-gradient(120deg,var(--primary),var(--secondary)); color:#fff;
            box-shadow: 0 6px 24px rgba(0,0,0,0.12);
        }
        .logo{display:flex; gap:10px; align-items:center; font-weight:600; font-size:1.15rem}
        .logo i{font-size:1.2rem}
        .header-actions{display:flex; gap:10px; align-items:center}
        .icon-btn{background:transparent;border:none;color:#fff;font-size:1.05rem;cursor:pointer;padding:8px;border-radius:8px}
        .icon-btn:hover{background:rgba(255,255,255,0.06)}

        .wrap{max-width:1100px;margin:20px auto;padding:16px}
        .grid{display:grid; grid-template-columns:repeat(auto-fit,minmax(280px,1fr)); gap:16px}
        .card{
            background:var(--card); border-radius:12px; padding:16px;
            box-shadow: 0 8px 30px rgba(6,24,40,0.06);
            transition: transform 0.25s ease, box-shadow 0.25s ease, background 0.35s;
        }
        .card:hover{ transform:translateY(-6px); box-shadow: 0 14px 40px rgba(6,24,40,0.09) }
        h3{margin:0 0 8px 0; font-size:1rem}

        /* Status badge */
        .status-badge{
            display:inline-flex; align-items:center; gap:8px;
            padding:7px 12px; border-radius:22px; color:#fff; font-weight:600;
            box-shadow: 0 6px 18px rgba(0,0,0,0.06);
            transition: transform 0.25s ease, box-shadow 0.25s ease, background 0.25s;
        }
        .running{ background: var(--good); box-shadow: 0 8px 22px rgba(43,203,119,0.18) }
        .paused { background: var(--warn); box-shadow: 0 8px 22px rgba(243,156,18,0.14) }
        .error  { background: var(--bad); box-shadow: 0 8px 22px rgba(231,76,60,0.14) }

        /* animations */
        @keyframes pop { from{ transform: scale(.85); opacity:0 } to{ transform: scale(1); opacity:1 } }
        .pop-in { animation: pop .45s cubic-bezier(.22,1,.36,1) both }

        @keyframes flash-green { 0%{background: rgba(43,203,119,0.0)} 50%{background: rgba(43,203,119,0.18)} 100%{background: transparent} }
        @keyframes flash-red   { 0%{background: rgba(231,76,60,0.0)} 50%{background: rgba(231,76,60,0.14)} 100%{background: transparent} }

        .flash-good{ animation: flash-green .9s ease; }
        .flash-bad { animation: flash-red .9s ease; }

        .best-dns {
            font-weight:700; color:var(--primary); display:inline-block;
            animation: pulse 1.6s ease-in-out infinite;
        }
        @keyframes pulse { 0%,100%{ transform: scale(1) } 50%{ transform: scale(1.06) } }

        /* small info */
        .muted { color: rgba(0,0,0,0.55) }
        .muted-dark { color: rgba(255,255,255,0.68) }

        /* Chart area */
        .chart-wrap{ margin-top:16px; padding:12px; border-radius:10px; background:var(--card) ; box-shadow: 0 8px 30px rgba(6,24,40,0.04) }
        canvas{ width:100% !important; height: 260px !important }

        /* icons */
        .meta-row{display:flex; gap:12px; align-items:center; margin-top:8px}
        .meta { display:flex; gap:8px; align-items:center; padding:6px 10px; border-radius:10px; background: rgba(0,0,0,0.02) }
        .meta i{font-size:1.05rem}
        .meta span{font-weight:600}

        /* tooltip for best dns */
        .tooltip {
            position: relative; display:inline-block; cursor:help;
        }
        .tooltip .tt {
            visibility:hidden; opacity:0;
            position:absolute; left:50%; transform:translateX(-50%);
            bottom:calc(100% + 8px);
            background:var(--card); color:var(--text);
            padding:8px 10px; border-radius:8px; white-space:nowrap;
            box-shadow:0 8px 30px rgba(0,0,0,0.12);
            transition:opacity .18s ease, transform .18s ease;
            transform-origin:center bottom; font-size:0.9rem;
        }
        .tooltip:hover .tt { visibility:visible; opacity:1; transform:translateX(-50%) translateY(-6px) }
        footer{ text-align:center; margin-top:22px; color:rgba(0,0,0,0.55); padding:18px 0 }

        /* responsive tweaks */
        @media (max-width:640px){
            header{ flex-direction:column; gap:8px; text-align:center }
            .meta-row{ flex-direction:column; align-items:flex-start }
        }
    </style>
</head>
<body>
    <header>
        <div class="logo"><i class="fas fa-network-wired"></i> DNS Switcher Pro</div>
        <div class="header-actions">
            <a class="icon-btn" href="https://github.com/BOSSGOOD467/Dns-automatic-in-pc" target="_blank" title="GitHub"><i class="fab fa-github"></i></a>
            <button class="icon-btn" onclick="toggleTheme()" title="Toggle dark mode"><i class="fas fa-moon"></i></button>
        </div>
    </header>

    <main class="wrap">
        <section class="grid">
            <!-- STATUS -->
            <article class="card pop-in" id="card-status">
                <h3>Status</h3>
                <div id="statusBadge" class="status-badge running pop-in" aria-live="polite">
                    <i id="statusIcon" class="fas fa-play-circle"></i>
                    <span id="statusText">{{ data.status }}</span>
                </div>

                <div style="margin-top:12px">
                    <div class="muted">Current DNS</div>
                    <div style="font-weight:700; margin-top:6px"><span id="current">{{ data.current_dns }}</span></div>

                    <div class="muted" style="margin-top:8px">Latency</div>
                    <div id="latWrap" style="display:flex;align-items:center;gap:10px;margin-top:6px">
                        <div style="font-weight:700;font-size:1.05rem"><span id="lat">{{ data.latency }}</span> ms</div>
                        <div id="latDelta" style="font-size:0.9rem;color:rgba(0,0,0,0.45)"></div>
                    </div>
                </div>

                <div class="meta-row" style="margin-top:12px">
                    <div class="meta"><i class="fas fa-clock"></i><span id="last">{{ data.last_update }}</span></div>
                    <div class="meta tooltip"><i class="fas fa-info-circle"></i><span>Info</span>
                        <div class="tt">Dashboard refresh setiap {{ refresh_rate }} detik</div>
                    </div>
                </div>
            </article>

            <!-- BEST DNS -->
            <article class="card pop-in" id="card-best">
                <h3>Best DNS</h3>
                <p style="margin:6px 0">
                    <i class="fas fa-star" style="color:#f1c40f"></i>
                    <span id="best" class="best-dns" title="Klik untuk detail"> {{ data.best_dns }}</span>
                    <span class="tooltip" style="margin-left:8px">
                        <i class="fas fa-question-circle" style="color:rgba(0,0,0,0.35)"></i>
                        <div class="tt" id="bestTooltip">Dipilih berdasarkan hasil latency</div>
                    </span>
                </p>

                <div style="margin-top:8px">
                    <div class="muted">Sumber / catatan</div>
                    <div style="margin-top:6px;color:rgba(0,0,0,0.65)" id="bestNote">Latency-based selection</div>
                </div>
            </article>

            <!-- CLIENT -->
            <article class="card pop-in" id="card-client">
                <h3>Client</h3>
                <div style="display:flex;align-items:center;gap:12px;margin-top:8px">
                    <div id="platformBox" style="display:flex;flex-direction:column;align-items:center">
                        <div id="platformIcon" style="font-size:1.6rem"><i class="fas fa-desktop"></i></div>
                        <div style="margin-top:6px;font-weight:600"><span id="plat">{{ data.client_platform }}</span></div>
                    </div>

                    <div id="browserBox" style="display:flex;flex-direction:column;align-items:center">
                        <div id="browserIcon" style="font-size:1.6rem"><i class="fas fa-globe"></i></div>
                        <div style="margin-top:6px;font-weight:600"><span id="brow">{{ data.client_browser }}</span></div>
                    </div>
                </div>

                <div style="margin-top:12px;color:rgba(0,0,0,0.6)">Tips: buka dashboard di device yang mau kamu pantau.</div>
            </article>
        </section>

        <div class="chart-wrap">
            <canvas id="chartCanvas"></canvas>
        </div>

        <footer>DNS Switcher Pro © 2024 – <a href="https://github.com/BOSSGOOD467/Dns-automatic-in-pc" target="_blank">BOSSGOOD467</a></footer>
    </main>

    <script>
    // Small helpers
    const refreshRate = {{ refresh_rate }};

    function toggleTheme(){
        const isDark = document.body.getAttribute('data-theme') === 'dark';
        document.body.setAttribute('data-theme', isDark ? '' : 'dark');
        if(myChart) updateChartColors();
    }

    // Chart.js setup
    let myChart = null;
    function createChart(history){
        const ctx = document.getElementById('chartCanvas').getContext('2d');
        const labels = (history||[]).map(d=>d.time);
        const values = (history||[]).map(d=>d.latency);
        myChart = new Chart(ctx, {
            type: 'line',
            data: {
                labels: labels,
                datasets: [{
                    label: 'Latency (ms)',
                    data: values,
                    borderWidth: 2,
                    borderColor: '#4361ee',
                    backgroundColor: 'rgba(67,97,238,0.12)',
                    fill: true,
                    tension: 0.34,
                    pointRadius: 3,
                    pointHoverRadius: 6
                }]
            },
            options: {
                responsive:true,
                animation:{ duration: 700, easing: 'easeOutQuart' },
                plugins:{
                    legend:{ labels:{ color: getTextColor() } },
                    tooltip:{ mode:'index', intersect:false }
                },
                scales:{
                    x:{ ticks:{ color: getTextColor() } },
                    y:{ ticks:{ color: getTextColor() }, beginAtZero:true }
                }
            }
        });
    }

    function updateChart(history){
        if(!myChart){ createChart(history); return; }
        myChart.data.labels = (history||[]).map(d=>d.time);
        myChart.data.datasets[0].data = (history||[]).map(d=>d.latency);
        // dynamic color: last latency decides color
        const last = history && history.length ? history[history.length-1].latency : null;
        if(last !== null){
            if(last < 120){ myChart.data.datasets[0].borderColor = '#4361ee'; myChart.data.datasets[0].backgroundColor = 'rgba(67,97,238,0.12)'; }
            else if(last < 220){ myChart.data.datasets[0].borderColor = '#f39c12'; myChart.data.datasets[0].backgroundColor = 'rgba(243,156,18,0.12)'; }
            else { myChart.data.datasets[0].borderColor = '#e74c3c'; myChart.data.datasets[0].backgroundColor = 'rgba(231,76,60,0.12)'; }
        }
        updateChartColors();
        myChart.update();
    }

    function updateChartColors(){
        const dark = document.body.getAttribute('data-theme') === 'dark';
        if(!myChart) return;
        myChart.options.scales.x.ticks.color = dark ? '#e9eef6' : '#222';
        myChart.options.scales.y.ticks.color = dark ? '#e9eef6' : '#222';
        myChart.options.plugins.legend.labels.color = dark ? '#e9eef6' : '#222';
    }

    // UI update logic (status, icons, animations)
    let prevLatency = null;
    let prevBest = null;
    let statusTimeout = null;

    function setStatus(statusStr){
        const sText = document.getElementById('statusText');
        const sBadge = document.getElementById('statusBadge');
        const sIcon = document.getElementById('statusIcon');
        sText.innerText = statusStr;

        // clear classes
        sBadge.classList.remove('running','paused','error');
        sBadge.classList.remove('flash-good','flash-bad');

        const st = statusStr.toLowerCase();
        if(st.includes('run')) { sBadge.classList.add('running'); sIcon.className='fas fa-play-circle' }
        else if(st.includes('pause') || st.includes('dijeda')) { sBadge.classList.add('paused'); sIcon.className='fas fa-pause-circle' }
        else { sBadge.classList.add('error'); sIcon.className='fas fa-exclamation-circle' }

        // glow animation
        sBadge.style.transform = 'translateY(-2px) scale(1.02)';
        clearTimeout(statusTimeout);
        statusTimeout = setTimeout(()=> sBadge.style.transform = '', 500);
    }

    function setBest(bestIp, note){
        const el = document.getElementById('best');
        const noteEl = document.getElementById('bestNote');
        // pulse + flash blue
        el.classList.remove('pulseTemp');
        void el.offsetWidth; // reflow to restart animation if same value
        el.classList.add('pulseTemp');
        el.style.transition = 'transform .35s ease';
        el.style.transform = 'scale(1.08)';
        setTimeout(()=> el.style.transform = '', 350);

        el.innerText = bestIp;
        noteEl.innerText = note || 'Dipilih berdasarkan latency';

        // tooltip update
        const tt = document.getElementById('bestTooltip');
        if(tt) tt.innerText = `IP: ${bestIp} – dipilih oleh algoritma latency`;
    }

    function flashLatency(delta){
        const latWrap = document.getElementById('latWrap');
        latWrap.classList.remove('flash-good','flash-bad');
        if(delta > 0) latWrap.classList.add('flash-bad');
        else if(delta < 0) latWrap.classList.add('flash-good');
        // remove after animation
        setTimeout(()=> latWrap.classList.remove('flash-good','flash-bad'), 900);
        // show delta text
        const dd = document.getElementById('latDelta');
        if(delta > 0) dd.innerText = `▲ +${delta} ms`; else if(delta < 0) dd.innerText = `▼ ${Math.abs(delta)} ms`; else dd.innerText = '';
    }

    function setClientIcons(platform, browser){
        const pIcon = document.getElementById('platformIcon');
        const bIcon = document.getElementById('browserIcon');
        const platText = document.getElementById('plat');
        const browText = document.getElementById('brow');

        platText.innerText = platform || 'Unknown';
        browText.innerText = browser || 'Unknown';

        // platform
        if(platform === 'Windows') pIcon.innerHTML = '<i class="fab fa-windows"></i>';
        else if(platform === 'Linux') pIcon.innerHTML = '<i class="fab fa-linux"></i>';
        else if(platform === 'macOS') pIcon.innerHTML = '<i class="fab fa-apple"></i>';
        else pIcon.innerHTML = '<i class="fas fa-desktop"></i>';

        // browser
        if(browser === 'Chrome') bIcon.innerHTML = '<i class="fab fa-chrome"></i>';
        else if(browser === 'Firefox') bIcon.innerHTML = '<i class="fab fa-firefox-browser"></i>';
        else if(browser === 'Safari') bIcon.innerHTML = '<i class="fab fa-safari"></i>';
        else if(browser === 'Edge') bIcon.innerHTML = '<i class="fab fa-edge"></i>';
        else bIcon.innerHTML = '<i class="fas fa-globe"></i>';

        // animate icons
        pIcon.style.transform = 'scale(.7)'; bIcon.style.transform = 'scale(.7)';
        setTimeout(()=>{ pIcon.style.transition='transform .45s cubic-bezier(.2,1,.3,1)'; pIcon.style.transform='scale(1)'; bIcon.style.transition='transform .45s cubic-bezier(.2,1,.3,1)'; bIcon.style.transform='scale(1)'; }, 40);
    }

    // fetch & update loop
    async function fetchData(){
        try{
            const res = await fetch('/data');
            if(!res.ok) throw new Error('Network');
            const j = await res.json();

            // status
            setStatus(j.status || 'Unknown');

            // current dns
            document.getElementById('current').innerText = j.current_dns || '-';

            // latency & delta
            const latency = (typeof j.latency === 'number') ? j.latency : parseInt(j.latency) || 0;
            document.getElementById('lat').innerText = latency;
            if(prevLatency !== null){
                const delta = latency - prevLatency;
                if(Math.abs(delta) >= 10) flashLatency(delta);
            }
            prevLatency = latency;

            // best dns
            if(j.best_dns && j.best_dns !== prevBest){
                setBest(j.best_dns, j.best_note || '');
                // little highlight on card
                const card = document.getElementById('card-best');
                card.style.boxShadow = '0 18px 40px rgba(67,97,238,0.12)';
                setTimeout(()=> card.style.boxShadow = '', 800);
                prevBest = j.best_dns;
            }

            // client icons
            setClientIcons(j.client_platform, j.client_browser);

            // history/chart
            updateChart(j.history || []);

        }catch(err){
            console.error('fetch error', err);
            setStatus('Error – Disconnected');
        }
    }

    // init
    window.addEventListener('load', ()=>{
        fetchData();
        setInterval(fetchData, refreshRate * 1000);
    });
    </script>
</body>
</html>
"""

@app.route('/')
def dashboard():
    client_data = get_client_info()
    with data_lock:
        current_data = dashboard_data.copy()
    return render_template_string(HTML_TEMPLATE, 
                                data={**current_data, **client_data}, 
                                refresh_rate=config['dashboard']['refresh_s'])

@app.route('/data')
def data_api():
    client_data = get_client_info()
    with data_lock:
        current_data = dashboard_data.copy()
    return jsonify({**current_data, **client_data})

def run_dashboard():
    if config['dashboard']['enabled']:
        host = config['dashboard']['host']
        port = config['dashboard']['port']
        log_info(f"Dashboard berjalan di http://{host}:{port}")
        app.run(host=host, port=port, debug=False, use_reloader=False)

# -------------------------
# Graceful shutdown
# -------------------------
def cleanup_and_exit(signum=None, frame=None):
    log_info("Membersihkan dan keluar...")
    interfaces = get_interfaces()
    if interfaces:
        log_info(f"Mereset DNS ke DHCP untuk: {', '.join(interfaces)}")
        for interface in interfaces:
            reset_dns_on_interface(interface)
    log_info("Selesai.")
    sys.exit(0)

# Register signal handlers
signal.signal(signal.SIGINT, cleanup_and_exit)
signal.signal(signal.SIGTERM, cleanup_and_exit)
if hasattr(signal, 'SIGHUP'):
    signal.signal(signal.SIGHUP, cleanup_and_exit)

# -------------------------
# Main worker
# -------------------------
def worker_main():
    if not is_admin():
        msg = "Script harus dijalankan sebagai Administrator/root!"
        log_err(msg)
        show_error_popup(msg)
        return

    interfaces = get_interfaces()
    if not interfaces:
        msg = "Tidak menemukan interface jaringan yang aktif. Periksa koneksi Anda."
        log_err(msg)
        show_error_popup(msg)
        return

    # [FITUR BARU] Logika untuk mode manual
    if config.get("dns_selection_mode") == "manual":
        manual_servers = config.get("manual_dns", [])
        if not manual_servers or not manual_servers[0]:
            msg = "Mode manual diaktifkan tapi 'manual_dns' kosong atau tidak valid di config.json."
            log_err(msg)
            show_error_popup(msg)
            return

        manual_dns_to_set = manual_servers[0]  # Gunakan DNS pertama dari list
        log_info(f"Mode manual aktif. Mengatur DNS ke {manual_dns_to_set}...")
        
        if config['dashboard']['enabled']:
            threading.Thread(target=run_dashboard, daemon=True).start()
            time.sleep(1)

        success_count = sum(1 for interface in interfaces if set_dns_on_interface(interface, manual_dns_to_set))

        if success_count > 0:
            log_info(f"DNS manual berhasil diatur pada {success_count} dari {len(interfaces)} interface.")
            verify_dns_change(interfaces, manual_dns_to_set)
            with data_lock:
                dashboard_data.update({
                    "current_dns": manual_dns_to_set,
                    "best_dns": manual_dns_to_set,
                    "latency": "N/A",
                    "status": f"Manual Mode",
                    "last_update": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                })
            log_info("Script akan tetap berjalan untuk menyajikan dashboard. Tekan Ctrl+C untuk keluar.")
            try:
                while True: time.sleep(3600)
            except KeyboardInterrupt:
                pass
        else:
            msg = f"Gagal mengatur DNS manual {manual_dns_to_set} pada semua interface."
            log_err(msg)
            show_error_popup(msg)
        
        return

    # Logika untuk auto-disable IPv6
    effective_use_ipv6 = config.get("use_ipv6", True)
    if effective_use_ipv6 and config.get("auto_disable_ipv6", True):
        if not check_ipv6_connectivity():
            log_warn("Auto-disabling IPv6 karena konektivitas tidak terdeteksi.")
            effective_use_ipv6 = False
    
    if config['dashboard']['enabled']:
        threading.Thread(target=run_dashboard, daemon=True).start()

    current_dns = "DHCP"
    last_game_check = 0
    is_game_currently_running = False

    while True:
        try:
            # Cek game (dengan cache)
            if config['game_pause'] and (time.time() - last_game_check > config['game_cache_seconds']):
                last_game_check = time.time()
                game_was_running = is_game_currently_running
                is_game_currently_running = is_game_running()
                if is_game_currently_running and not game_was_running:
                    log_info("Game terdeteksi, switching DNS dijeda.")
                    with data_lock:
                        dashboard_data["status"] = "Dijeda (Game Aktif)"
                elif not is_game_currently_running and game_was_running:
                    log_info("Game berakhir, switching DNS dilanjutkan.")
                    with data_lock:
                        dashboard_data["status"] = "Berjalan"

            if is_game_currently_running:
                time.sleep(config["interval"])
                continue
            
            with data_lock:
                dashboard_data["status"] = "Menguji..."
            if config["clear_terminal"]:
                os.system('cls' if platform.system() == 'Windows' else 'clear')
                print("DNS Switcher - Monitoring Kinerja DNS\n" + "="*50)
                print(f"Interface: {', '.join(interfaces)} | Interval: {config['interval']}s")
                print(f"Deteksi Game: {'Aktif' if config['game_pause'] else 'Nonaktif'} | IPv6: {'Aktif' if effective_use_ipv6 else 'Nonaktif'}")
                print("="*50 + "\n")

            all_dns = DNS_IPV4 + (DNS_IPV6 if effective_use_ipv6 else [])
            log_info(f"Menguji {len(all_dns)} server DNS...")
            
            results = {}
            with ThreadPoolExecutor(max_workers=config["threads"]) as executor:
                future_to_dns = {executor.submit(test_dns_latency, dns): dns for dns in all_dns}
                for future in as_completed(future_to_dns):
                    dns_server = future_to_dns[future]
                    try:
                        latency = future.result()
                        if latency is not None:
                            results[dns_server] = latency
                    except Exception as exc:
                        log_warn(f"Error saat menguji {dns_server}: {exc}")

            if results:
                best_dns, best_latency = min(results.items(), key=lambda item: item[1])
                
                log_info(f"DNS terbaik: {best_dns} ({best_latency} ms)")
                
                with data_lock:
                    dashboard_data.update({
                        "best_dns": best_dns,
                        "latency": best_latency,
                        "last_update": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        "status": "Berjalan"
                    })
                    history_entry = {"time": datetime.now().strftime("%H:%M:%S"), "latency": best_latency}
                    dashboard_data["history"].append(history_entry)
                    dashboard_data["history"] = dashboard_data["history"][-20:]
                
                save_to_csv(best_dns, best_latency)
                
                if best_dns != current_dns:
                    log_info(f"Mengganti DNS ke {best_dns}...")
                    success_count = sum(1 for interface in interfaces if set_dns_on_interface(interface, best_dns))
                    
                    if success_count > 0:
                        log_info(f"DNS berhasil diubah pada {success_count} interface.")
                        current_dns = best_dns
                        with data_lock:
                            dashboard_data["current_dns"] = current_dns
                        verify_dns_change(interfaces, best_dns)
                    else:
                        log_err(f"Gagal mengubah DNS ke {best_dns}.")
                else:
                    log_info(f"DNS terbaik ({best_dns}) sudah digunakan.")

            else:
                log_err("Tidak ada server DNS yang merespons. Mempertahankan DNS saat ini.")
                with data_lock:
                    dashboard_data["status"] = "Error: Tidak ada DNS"

            time.sleep(config["interval"])
            
        except KeyboardInterrupt:
            break
        except Exception as e:
            log_err(f"Terjadi error pada loop utama: {e}")
            time.sleep(30)

# -------------------------
# ENTRY POINT
# -------------------------
if __name__ == "__main__":
    log_info("DNS Switcher mulai...")
    try:
        worker_main()
    except Exception as e:
        log_err(f"Fatal error: {e}")
        show_error_popup(f"Fatal error: {e}")
    finally:
        cleanup_and_exit()
