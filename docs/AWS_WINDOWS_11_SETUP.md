# AWS Windows 11 / Windows Server Deployment Guide

Complete institutional guide for deploying and running the **Nimble Darwin Trading Bot** on **AWS EC2 Windows 11** or **AWS Windows Server 2022** with native **MetaTrader 5 (MT5)** execution.

---

## 1. Architecture Overview

On AWS Windows, the bot operates with native inter-process communication (IPC) to the MetaTrader 5 terminal:

```
┌────────────────────────────────────────────────────────┐
│                   AWS EC2 Windows 11                   │
│                                                        │
│  ┌────────────────────────┐    ┌─────────────────────┐ │
│  │ Nimble Darwin Bot      │    │ MetaTrader 5        │ │
│  │ (Python 3.10+ 64-bit)  │    │ (terminal64.exe)    │ │
│  │                        │    │                     │ │
│  │ • Multi-Agent Pipeline │    │ • Direct Broker IPC │ │
│  │ • Risk Manager / Kill  │◄──►│ • Market Data Feed  │ │
│  │ • Telegram Brain & CLI │    │ • Order Execution   │ │
│  │ • Auto-Restart Loop    │    │ • Broker Server Sync│ │
│  └───────────▲────────────┘    └──────────▲──────────┘ │
└──────────────┼────────────────────────────┼────────────┘
               │ (HTTPS / TLS)              │ (TCP 443/WebSocket)
               ▼                            ▼
      Telegram API Clients           Broker MT5 Servers
      (Phone / Desktop App)         (Exness, FTMO, IC Markets)
```

---

## 2. AWS EC2 Instance Provisioning

### Recommended Specifications

| Parameter | Recommended Specification | Minimum Specification |
|-----------|---------------------------|-----------------------|
| **AMI** | Microsoft Windows Server 2022 Base / Windows 11 | Windows Server 2019 Base |
| **Instance Type** | `t3.medium` (2 vCPU, 4 GiB RAM) or `c6i.large` | `t3.small` (2 GiB RAM) |
| **Storage (EBS)** | 40 GiB gp3 (3,000 IOPS, 125 MB/s) | 30 GiB gp3 |
| **Region** | Select region with lowest ping to your broker's MT5 servers (e.g. `eu-west-1` for London brokers, `us-east-1` for US/NY4 brokers) | Any |

### Security Group Configuration

| Type | Protocol | Port Range | Source | Description |
|------|----------|------------|--------|-------------|
| **RDP** | TCP | `3389` | `My IP` (Strictly restricted!) | Remote Desktop Management |
| **HTTPS (Outbound)** | TCP | `443` | `0.0.0.0/0` | Telegram API & Broker feeds |
| **DNS (Outbound)** | UDP/TCP | `53` | `0.0.0.0/0` | Domain resolution |
| **Broker MT5 (Outbound)** | TCP | `443`, `1950`, `444` | `0.0.0.0/0` | MetaTrader 5 broker ports |

> [!CAUTION]
> **Never open RDP port 3389 to `0.0.0.0/0` (Anywhere).** Restrict the source CIDR to your home or office IP address, or use AWS Systems Manager (SSM) Fleet Manager to connect securely without opening inbound ports.

---

## 3. Remote Desktop & Base System Setup

1. Open **Remote Desktop Connection** (`mstsc.exe`) on your local machine.
2. Enter the EC2 Public IPv4 DNS name and log in using the `Administrator` password decrypted via your EC2 key pair.
3. In PowerShell (Run as Administrator), enable NTP synchronization for accurate tick timestamps:
   ```powershell
   w32tm /config /manualpeerlist:"pool.ntp.org,0x1" /syncfromflags:manual /reliable:YES /update
   Restart-Service w32time
   w32tm /resync
   ```

---

## 4. Install 64-bit Python

1. Download **Python 3.10.x** or **Python 3.11.x** (64-bit Windows Installer) from [python.org](https://www.python.org/downloads/).
2. Run the installer:
   - **CRITICAL**: Check the checkbox **"Add python.exe to PATH"**.
   - Select **Install Now** or customize installation.
   - At the end of the installation, click **"Disable path length limit"**.
3. Verify in Command Prompt:
   ```cmd
   python --version
   ```

---

## 5. Install & Configure MetaTrader 5 Terminal

1. Download the MT5 installer from your broker (e.g., Exness, FTMO, IC Markets) or from [MetaQuotes](https://www.metatrader5.com/).
2. Install MT5 (default path: `C:\Program Files\MetaTrader 5\terminal64.exe`).
3. Launch MT5 and log in to your trading account (**File -> Login to Trade Account**):
   - Enter **Login**, **Password**, and select your broker's **Server**.
   - Check **"Save password"**.
4. Enable automated execution permissions:
   - Go to **Tools -> Options** (or press `Ctrl+O`).
   - Open the **Expert Advisors** tab.
   - Check **"Allow automated trading"**.
   - Check **"Allow WebRequest for listed URL"**.
   - Click **OK**.
5. Keep MT5 open or allow the bot to launch it automatically via `MT5_PATH`.

---

## 6. Project Setup & Installation

Clone or copy the `nimble-darwin` codebase onto your EC2 instance (e.g., to `C:\TradingBot\nimble-darwin`).

Open Command Prompt or PowerShell in the project directory and run the one-click installer:

```cmd
install_windows.bat
```

This automated installer:
1. Creates an isolated 64-bit virtual environment (`.\venv`).
2. Upgrades `pip`, `setuptools`, and `wheel`.
3. Installs all project dependencies from `requirements.txt`.
4. Installs the native Windows `MetaTrader5` package.
5. Auto-detects your MT5 installation directory (`terminal64.exe`).
6. Generates `.env` from `.env.example` if not already present.

---

## 7. Configuration (.env)

Edit the `.env` file in the project root:

```ini
# ==============================================================================
# Telegram Notifications & Interactive Control
# ==============================================================================
TELEGRAM_TOKEN=123456789:ABCdefGhIJKlmNoPQRsTUVwxyZ
TELEGRAM_CHAT_ID=987654321

# ==============================================================================
# Broker Configuration (MetaTrader 5)
# ==============================================================================
# Set BOT_BROKER_MODE to mt5_live for real execution, or mt5_paper for demo simulation
BOT_BROKER_MODE=mt5_live
MT5_ENABLED=true
MT5_MODE=live
MT5_LOGIN=12345678
MT5_PASSWORD=YourTradingPassword
MT5_SERVER=YourBroker-Live

# MT5_PATH is auto-detected if left blank. Specify explicitly if installed in custom path:
# MT5_PATH=C:\Program Files\MetaTrader 5\terminal64.exe
MT5_MAGIC=112233

# ==============================================================================
# Trading Strategy & Risk Management
# ==============================================================================
BOT_AUTOTRADE=true
BOT_AUTOTRADE_SYMBOLS=BTCUSDT,XAUUSD
BOT_PROFILE=scalp
BOT_SCALP=true
BOT_SCALP_TP_USD=2.5
BOT_SCALP_TP_PCT=0.10
BOT_SCALP_SL_USD=3.0
BOT_SCALP_SL_PCT=0.12
BOT_MAX_OPEN_TRADES=5
BOT_NEWS=true
```

---

## 8. Launching the Bot

### Option A: Standard Batch Supervisor (Command Prompt)
```cmd
start_windows.bat
```
- Sets UTF-8 encoding (`chcp 65001`, `PYTHONUTF8=1`).
- Activates virtual environment.
- Launches `run_bot.py` with an auto-restart supervisor.

### Option B: PowerShell Supervisor
```powershell
powershell -ExecutionPolicy Bypass -File .\start_windows.ps1
```

---

## 9. 24/7 Unattended Operation (Auto-Start on Reboot)

To ensure the bot and MT5 run automatically when your AWS EC2 instance boots up without requiring manual RDP login:

### Method 1: Windows Task Scheduler (Recommended)

1. Open **Task Scheduler** (`taskschd.msc`).
2. Click **Create Task** (not Basic Task):
   - **General Tab**:
     - Name: `NimbleDarwinTradingBot`
     - Select: **"Run whether user is logged on or not"**
     - Check: **"Run with highest privileges"**
     - Configure for: **Windows 10 / Windows Server 2022**
   - **Triggers Tab**:
     - Click **New...**
     - Begin the task: **At startup** (or **At log on**)
     - Delay task for: **30 seconds** (allows network and MT5 initialization)
   - **Actions Tab**:
     - Click **New...**
     - Action: **Start a program**
     - Program/script: `C:\TradingBot\nimble-darwin\start_windows.bat`
     - Start in: `C:\TradingBot\nimble-darwin`
   - **Settings Tab**:
     - Check: **"If the task fails, restart every: 1 minute"**
     - Attempt to restart up to: **99 times**
     - Uncheck: **"Stop the task if it runs longer than..."**

### Method 2: Windows Service via NSSM

For enterprise production setups, run as a managed Windows service using [NSSM (Non-Sucking Service Manager)](https://nssm.cc/):

```cmd
REM Run in Administrator Command Prompt:
nssm.exe install NimbleDarwinBot "C:\TradingBot\nimble-darwin\venv\Scripts\python.exe" "run_bot.py"
nssm.exe set NimbleDarwinBot AppDirectory "C:\TradingBot\nimble-darwin"
nssm.exe set NimbleDarwinBot AppEnvironmentExtra "PYTHONUTF8=1" "PYTHONIOENCODING=utf-8"
nssm.exe set NimbleDarwinBot Start SERVICE_AUTO_START
nssm.exe start NimbleDarwinBot
```

---

## 10. Verification & Troubleshooting

### Check Status via Telegram
Once running, send these commands to your bot in Telegram:
- `/status`: Verify current profile, equity, risk metrics, and active positions.
- `/mt5`: Inspect broker connectivity, balance, equity, leverage, and server ping.
- `/positions`: List all open trades across Gold and Bitcoin.
- `/kill`: Instantly activate the emergency kill switch and close all open orders.

### Troubleshooting Matrix

| Issue / Error | Cause | Resolution |
|---------------|-------|------------|
| `UnicodeEncodeError: 'charmap' codec can't encode character` | Windows console code page defaults to cp1252. | Fixed by UTF-8 guards in code and `start_windows.bat` (`set PYTHONUTF8=1` and `chcp 65001`). |
| `MetaTrader5 package not found` | Python architecture mismatch or package missing. | Ensure 64-bit Python is installed, then run `pip install MetaTrader5` inside `venv`. |
| `BrokerConfigurationError: MT5 initialization failed: (-10003)` | IPC connection could not open MT5 terminal or path is incorrect. | Ensure MT5 is installed, path in `MT5_PATH` is valid, and "Allow automated trading" is enabled in MT5 options. |
| `RuntimeError: Event loop is closed` on shutdown | Windows default ProactorEventLoop handles child sockets aggressively. | Fixed in `run_bot.py` via `asyncio.WindowsSelectorEventLoopPolicy()`. |
| Time drifts causing stale bars | AWS Windows system clock drift. | Configure Windows Time Service (`w32time`) as shown in Section 3. |
