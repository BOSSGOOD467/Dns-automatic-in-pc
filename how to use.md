Bima DNS Switcher Script
Python script to automatically switch to the DNS server with the best latency, equipped with an informative web dashboard.

Features
- ✅ Automatic detection of the fastest DNS from dozens of DNS servers
- ✅ Modern and responsive web dashboard
- ✅ Platform and browser detection
- ✅ Real-time latency graphs
- ✅ Automatic pause when games are running
- ✅ Support for Windows, macOS, and Linux
- ✅ Automatic DNS reset when the application is closed

System Requirements
- Python 3.6 or later
- Windows 7/8/10/11, macOS, or Linux
- Administrator/root access

Installation
1. Download Python (if not already installed):
    - Visit https://www.python.org/downloads/
    - Download and install Python, making sure to check the "Add Python to PATH" option
2. Download the Bima DNS Switcher script:
    - Save the "dns.py" file to the desired folder
3. Install dependencies:
    - Open Command Prompt/PowerShell (Windows) or Terminal (macOS/Linux)
    - Run the following command:
pip install flask psutil requests

Usage
1. Run the script as administrator:
    - Windows:
        - Click Start, search for "Command Prompt"
        - Right-click, select "Run as administrator"
        - Navigate to the folder where "dns.py" is saved
        - Run: "python dns.py"
    - macOS/Linux:
        - Open Terminal
        - Navigate to the folder where "dns.py" is saved
        - Run: "sudo python3 dns.py"
2. Access the dashboard:
    - Open a browser and visit: http://localhost:8080
    - The dashboard will display DNS information, latency, and platform details
3. Let the script run in the background:
    - The script will continuously monitor and switch to the best DNS
    - When a game is detected, the script will automatically pause

Configuration (Optional)
To customize settings, create a "dns_config.json" file in the same folder as the script:
{
  "interval": 60,
  "threads": 10,
  "ping_count": 3,
  "use_ipv6": true,
  "dashboard": {
    "enabled": true,
    "host": "0.0.0.0",
    "port": 8080,
    "refresh_s": 5
  },
  "games": ["valorant.exe", "csgo.exe"],
  "clear_terminal": true
}