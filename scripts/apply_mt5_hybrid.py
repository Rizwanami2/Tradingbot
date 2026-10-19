#!/usr/bin/env python3
"""Apply hybrid MT5: native quotes + EA Bridge orders."""
from pathlib import Path

def main():
    p = Path("mt5_broker.py")
    t = p.read_text(encoding="utf-8")
    if "Hybrid ON: native quotes" in t:
        print("Hybrid mode already present")
        return
    old = (
        "                self._connected = True\n"
        "                self._last_error = \"\"\n"
        "                logger.info(\n"
        "                    \"[MT5Broker] Connected to MT5 via EA File-Bridge backend (magic=%s, dir=%s)\",\n"
        "                    self.magic, getattr(self.ea_client, \"BRIDGE_DIR\", \"default\")\n"
        "                )\n"
        "                return True\n"
    )
    new = (
        "                self._connected = True\n"
        "                self._last_error = \"\"\n"
        "                self._native_data = False\n"
        "                if native_mt5 is not None:\n"
        "                    try:\n"
        "                        mt5_exe = self.path or getattr(config, \"MT5_PATH\", \"\") or \"\"\n"
        "                        try:\n"
        "                            from config import find_default_mt5_path\n"
        "                            if not mt5_exe:\n"
        "                                mt5_exe = find_default_mt5_path() or \"\"\n"
        "                        except Exception:\n"
        "                            pass\n"
        "                        init_kwargs = {\"path\": mt5_exe} if mt5_exe else {}\n"
        "                        if not native_mt5.initialize(**init_kwargs):\n"
        "                            logger.warning(\"[MT5Broker] Hybrid: native initialize failed (%s)\", native_mt5.last_error())\n"
        "                        else:\n"
        "                            if self.login and self.password and self.server:\n"
        "                                if not native_mt5.login(login=self.login, password=self.password, server=self.server):\n"
        "                                    logger.warning(\"[MT5Broker] Hybrid: native login failed (%s)\", native_mt5.last_error())\n"
        "                            self._native_data = True\n"
        "                            logger.info(\"[MT5Broker] Hybrid ON: native quotes/candles + EA Bridge orders (magic=%s)\", self.magic)\n"
        "                    except Exception as hy_err:\n"
        "                        logger.warning(\"[MT5Broker] Hybrid native data init skipped: %s\", hy_err)\n"
        "                logger.info(\n"
        "                    \"[MT5Broker] Connected via EA File-Bridge for ORDERS (magic=%s, dir=%s, native_data=%s)\",\n"
        "                    self.magic, getattr(self.ea_client, \"BRIDGE_DIR\", \"default\"), getattr(self, \"_native_data\", False)\n"
        "                )\n"
        "                return True\n"
    )
    if old not in t:
        raise SystemExit("Connect block not found exactly — pull latest or patch manually")
    t = t.replace(old, new, 1)
    t = t.replace(
        'if native_mt5 is not None and self._connected and self.effective_backend != "gateway":',
        'if native_mt5 is not None and self.effective_backend != "gateway":',
    )
    p.write_text(t, encoding="utf-8")
    print("Hybrid mode applied to mt5_broker.py")

if __name__ == "__main__":
    main()
