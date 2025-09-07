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
CONFIG_FILE = "dns_config.json"
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
    "dns_query_delay_s": 0.1,
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
    "max_terminal_lines": 100
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
    latencies = []
    
    for _ in range(query_count):
        try:
            start_time = time.monotonic()
            resolver.resolve(domain_to_query, 'A')
            end_time = time.monotonic()
            latencies.append(int((end_time - start_time) * 1000))
        except Exception:
            pass
        time.sleep(config.get("dns_query_delay_s", 0.1))

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
    
    return {"client_platform": platform_name, "client_browser": browser_name}

# [KEMBALI KE FORMAT AWAL] Kode HTML dikembalikan ke format multi-baris agar mudah dibaca.
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="id">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>DNS Switcher Dashboard</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
    <link href="https://fonts.googleapis.com/css2?family=Poppins:wght@300;400;500;600;700&display=swap" rel="stylesheet">
    <style>
        :root { --primary: #4361ee; --secondary: #3f37c9; --success: #4cc9f0; --danger: #f72585; --warning: #f8961e; --info: #4895ef; --light: #f8f9fa; --dark: #212529; --background: #f0f2f5; --card-shadow: 0 4px 20px rgba(0, 0, 0, 0.08); --transition: all 0.3s ease; }
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: 'Poppins', sans-serif; background-color: var(--background); color: #333; line-height: 1.6; }
        .container { max-width: 1200px; margin: 0 auto; padding: 20px; }
        header { background: linear-gradient(120deg, var(--primary), var(--secondary)); color: white; padding: 25px 0; box-shadow: 0 4px 12px rgba(0, 0, 0, 0.1); margin-bottom: 30px; }
        .header-content { display: flex; justify-content: space-between; align-items: center; }
        .logo { display: flex; align-items: center; gap: 15px; } .logo i { font-size: 2.2rem; } .logo h1 { font-weight: 600; font-size: 1.8rem; }
        .last-update { background: rgba(255, 255, 255, 0.15); padding: 8px 15px; border-radius: 20px; font-size: 0.9rem; display: flex; align-items: center; gap: 8px; }
        .dashboard-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 25px; margin-bottom: 30px; }
        .card { background: white; border-radius: 16px; padding: 25px; box-shadow: var(--card-shadow); transition: var(--transition); }
        .card:hover { transform: translateY(-5px); box-shadow: 0 8px 25px rgba(0, 0, 0, 0.12); }
        .card-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px; }
        .card-title { font-size: 1.2rem; font-weight: 600; color: var(--dark); display: flex; align-items: center; gap: 10px; } .card-title i { color: var(--primary); }
        .stat-badge { padding: 5px 12px; border-radius: 20px; font-size: 0.85rem; font-weight: 500; }
        .badge-success { background-color: rgba(76, 201, 240, 0.15); color: #4cc9f0; } .badge-warning { background-color: rgba(248, 150, 30, 0.15); color: #f8961e; } .badge-info { background-color: rgba(72, 149, 239, 0.15); color: #4895ef; }
        .stat-item { margin-bottom: 15px; } .stat-label { font-size: 0.9rem; color: #6c757d; display: flex; align-items: center; gap: 8px; margin-bottom: 5px; } .stat-value { font-size: 1.4rem; font-weight: 600; color: var(--dark); margin-left: 26px; }
        .client-info { display: flex; align-items: center; gap: 15px; margin-top: 20px; padding-top: 20px; border-top: 1px solid #eee; }
        .platform-icon { width: 50px; height: 50px; border-radius: 50%; background: linear-gradient(45deg, var(--primary), var(--info)); display: flex; align-items: center; justify-content: center; color: white; font-size: 1.5rem; }
        .client-details { flex: 1; } .client-platform { font-weight: 600; margin-bottom: 3px; } .client-browser { font-size: 0.9rem; color: #6c757d; }
        .chart-container { background: white; border-radius: 16px; padding: 25px; box-shadow: var(--card-shadow); margin-bottom: 30px; }
        footer { text-align: center; padding: 20px 0; color: #6c757d; font-size: 0.9rem; border-top: 1px solid #eee; margin-top: 30px; }
        @media (max-width: 768px) { .header-content { flex-direction: column; gap: 15px; text-align: center; } .dashboard-grid { grid-template-columns: 1fr; } }
    </style>
</head>
<body>
    <header><div class="container"><div class="header-content"><div class="logo"><i class="fas fa-network-wired"></i><h1>DNS Switcher</h1></div><div class="last-update"><i class="fas fa-sync-alt"></i><span>Update setiap <span id="refreshRate">{{ refresh_rate }}</span> detik</span></div></div></div></header>
    <div class="container">
        <div class="dashboard-grid">
            <div class="card"><div class="card-header"><h2 class="card-title"><i class="fas fa-server"></i> DNS Status</h2><span class="stat-badge badge-success" id="statusBadge">{{ data.status }}</span></div><div class="stat-item"><div class="stat-label"><i class="fas fa-exchange-alt"></i><span>Current DNS</span></div><div class="stat-value" id="currentDns">{{ data.current_dns }}</div></div><div class="stat-item"><div class="stat-label"><i class="fas fa-tachometer-alt"></i><span>Latency</span></div><div class="stat-value" id="latency">{{ data.latency }} ms</div></div></div>
            <div class="card"><div class="card-header"><h2 class="card-title"><i class="fas fa-crown"></i> Best DNS</h2><span class="stat-badge badge-info">Recommended</span></div><div class="stat-item"><div class="stat-label"><i class="fas fa-check-circle"></i><span>Optimal Server</span></div><div class="stat-value" id="bestDns">{{ data.best_dns }}</div></div><div class="stat-item"><div class="stat-label"><i class="fas fa-clock"></i><span>Last Update</span></div><div class="stat-value" id="lastUpdate">{{ data.last_update }}</div></div></div>
            <div class="card"><div class="card-header"><h2 class="card-title"><i class="fas fa-user"></i> Client Info</h2><span class="stat-badge badge-warning">Detected</span></div><div class="client-info"><div class="platform-icon"><i class="fas fa-desktop" id="platformIcon"></i></div><div class="client-details"><div class="client-platform" id="clientPlatform">{{ data.client_platform }}</div><div class="client-browser" id="clientBrowser">{{ data.client_browser }}</div></div></div></div>
        </div>
        <div class="chart-container"><canvas id="latencyChart"></canvas></div>
    </div>
    <footer><p>DNS Switcher &copy; 2024 - All rights reserved</p></footer>
    <script>
        let latencyChart;

        function getPlatformIcon(platform) {
            const p = platform.toLowerCase();
            if (p.includes('windows')) return 'fab fa-windows';
            if (p.includes('linux')) return 'fab fa-linux';
            if (p.includes('macos')) return 'fab fa-apple';
            if (p.includes('android')) return 'fab fa-android';
            if (p.includes('ios')) return 'fab fa-apple';
            return 'fas fa-desktop';
        }

        function createChart(history) {
            const ctx = document.getElementById('latencyChart').getContext('2d');
            const labels = history.map(item => item.time);
            const data = history.map(item => item.latency);
            
            latencyChart = new Chart(ctx, {
                type: 'line',
                data: {
                    labels: labels,
                    datasets: [{
                        label: 'Latency (ms)',
                        data: data,
                        borderColor: 'rgb(67, 97, 238)',
                        backgroundColor: 'rgba(67, 97, 238, 0.1)',
                        tension: 0.3,
                        fill: true,
                    }]
                },
                options: { responsive: true, scales: { y: { beginAtZero: true } } }
            });
        }
        
        function updateChart(history) {
            if (!latencyChart) {
                createChart(history);
                return;
            }
            latencyChart.data.labels = history.map(item => item.time);
            latencyChart.data.datasets[0].data = history.map(item => item.latency);
            latencyChart.update();
        }

        function updateDashboard() {
            fetch('/data')
                .then(response => response.json())
                .then(data => {
                    document.getElementById('latency').textContent = data.latency + ' ms';
                    document.getElementById('lastUpdate').textContent = data.last_update;
                    document.getElementById('currentDns').textContent = data.current_dns;
                    document.getElementById('bestDns').textContent = data.best_dns;
                    document.getElementById('statusBadge').textContent = data.status;
                    document.getElementById('clientPlatform').textContent = data.client_platform;
                    document.getElementById('clientBrowser').textContent = data.client_browser;
                    document.getElementById('platformIcon').className = getPlatformIcon(data.client_platform);
                    updateChart(data.history);
                })
                .catch(error => console.error('Error fetching data:', error));
        }
        
        document.addEventListener('DOMContentLoaded', () => {
            updateDashboard();
            setInterval(updateDashboard, {{ refresh_rate }} * 1000);
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
