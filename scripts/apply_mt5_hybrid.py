#!/usr/bin/env python3
"""Enable hybrid: native quotes + EA Bridge orders when MT5_BACKEND=ea_bridge."""
from pathlib import Path

OLD = '''            if self.effective_backend == "ea_bridge":
                try:
                    from bridge import ea_client
                    self.ea_client = ea_client
                except Exception as exc:
                    self._connected = False
                    self._last_error = f"EA Bridge client import failed: {exc}"
                    logger.error("[MT5Broker] %s", self._last_error)
                    raise BrokerConfigurationError(self._last_error) from exc
                self._connected = True
                self._last_error = ""
                logger.info(
                    "[MT5Broker] Connected to MT5 via EA File-Bridge backend (magic=%s, dir=%s)",
                    self.magic, getattr(self.ea_client, "BRIDGE_DIR", "default")
                )
                return True
'''

NEW = '''            if self.effective_backend == "ea_bridge":
                try:
                    from bridge import ea_client
                    self.ea_client = ea_client
                except Exception as exc:
                    self._connected = False
                    self._last_error = f"EA Bridge client import failed: {exc}"
                    logger.error("[MT5Broker] %s", self._last_error)
                    raise BrokerConfigurationError(self._last_error) from exc
                self._connected = True
                self._last_error = ""
                self._native_data = False
                hybrid_on = str(__import__("os").getenv("MT5_HYBRID", "true")).lower() in (
                    "true", "1", "yes", "on",
                )
                if hybrid_on and native_mt5 is not None:
                    try:
                        mt5_exe = self.path or getattr(config, "MT5_PATH", "") or ""
                        try:
                            from config import find_default_mt5_path
                            if not mt5_exe:
                                mt5_exe = find_default_mt5_path() or ""
                        except Exception:
                            pass
                        init_kwargs = {"path": mt5_exe} if mt5_exe else {}
                        if not native_mt5.initialize(**init_kwargs):
                            logger.warning("[MT5Broker] Hybrid: native initialize failed (%s)", native_mt5.last_error())
                        else:
                            if self.login and self.password and self.server:
                                if not native_mt5.login(login=self.login, password=self.password, server=self.server):
                                    logger.warning("[MT5Broker] Hybrid: native login failed (%s)", native_mt5.last_error())
                            self._native_data = True
                            logger.info("[MT5Broker] Hybrid ON: native quotes/candles + EA Bridge orders (magic=%s)", self.magic)
                    except Exception as hy_err:
                        logger.warning("[MT5Broker] Hybrid native data init skipped: %s", hy_err)
                logger.info(
                    "[MT5Broker] Connected via EA File-Bridge for ORDERS (magic=%s, dir=%s, native_data=%s)",
                    self.magic, getattr(self.ea_client, "BRIDGE_DIR", "default"), getattr(self, "_native_data", False),
                )
                return True
'''

def main():
    p = Path("mt5_broker.py")
    t = p.read_text(encoding="utf-8")
    if "Hybrid ON: native quotes" in t:
        print("Hybrid already present")
        return
    if OLD not in t:
        raise SystemExit("Could not find ea_bridge connect block")
    t = t.replace(OLD, NEW, 1)
    t = t.replace(
        'if native_mt5 is not None and self._connected and self.effective_backend != "gateway":',
        'if native_mt5 is not None and self.effective_backend != "gateway":',
    )
    p.write_text(t, encoding="utf-8")
    cfg = Path("config.py")
    c = cfg.read_text(encoding="utf-8")
    if "MT5_HYBRID" not in c:
        c += '\nMT5_HYBRID = __import__("os").getenv("MT5_HYBRID", "true").lower() in ("true", "1", "yes", "on")\n'
        cfg.write_text(c, encoding="utf-8")
    print("Hybrid applied. MT5_BACKEND=ea_bridge MT5_HYBRID=true")

if __name__ == "__main__":
    main()
