#!/usr/bin/env python3
"""Fresh hybrid: native quotes + EA orders."""
from pathlib import Path

def main():
    p = Path("mt5_broker.py")
    t = p.read_text(encoding="utf-8")
    changed = False

    if "_force_hybrid" not in t:
        needle = 'if self.backend in ("ea_bridge", "bridge", "ea"):\n            self.effective_backend = "ea_bridge"'
        repl = (
            'if self.backend in ("hybrid", "ea_hybrid", "native_ea"):\n'
            '            self.effective_backend = "ea_bridge"\n'
            '            self._force_hybrid = True\n'
            '        elif self.backend in ("ea_bridge", "bridge", "ea"):\n'
            '            self.effective_backend = "ea_bridge"\n'
            '            self._force_hybrid = False'
        )
        if needle in t:
            t = t.replace(needle, repl, 1)
            changed = True
            print("backend hybrid alias")

    if "Hybrid ON: native quotes" not in t and "EA ORDERS connected" not in t:
        old = (
            '            if self.effective_backend == "ea_bridge":\n'
            '                try:\n'
            '                    from bridge import ea_client\n'
            '                    self.ea_client = ea_client\n'
            '                except Exception as exc:\n'
            '                    self._connected = False\n'
            '                    self._last_error = f"EA Bridge client import failed: {exc}"\n'
            '                    logger.error("[MT5Broker] %s", self._last_error)\n'
            '                    raise BrokerConfigurationError(self._last_error) from exc\n'
            '                self._connected = True\n'
            '                self._last_error = ""\n'
            '                logger.info(\n'
            '                    "[MT5Broker] Connected to MT5 via EA File-Bridge backend (magic=%s, dir=%s)",\n'
            '                    self.magic, getattr(self.ea_client, "BRIDGE_DIR", "default")\n'
            '                )\n'
            '                return True\n'
        )
        new = (
            '            if self.effective_backend == "ea_bridge":\n'
            '                try:\n'
            '                    from bridge import ea_client\n'
            '                    self.ea_client = ea_client\n'
            '                except Exception as exc:\n'
            '                    self._connected = False\n'
            '                    self._last_error = f"EA Bridge client import failed: {exc}"\n'
            '                    logger.error("[MT5Broker] %s", self._last_error)\n'
            '                    raise BrokerConfigurationError(self._last_error) from exc\n'
            '                self._connected = True\n'
            '                self._last_error = ""\n'
            '                self._native_data = False\n'
            '                self._hybrid_quote_source = "none"\n'
            '                hybrid_on = bool(getattr(self, "_force_hybrid", False)) or str(\n'
            '                    os.getenv("MT5_HYBRID", getattr(config, "MT5_HYBRID", "true"))\n'
            '                ).lower() in ("true", "1", "yes", "on")\n'
            '                if hybrid_on and native_mt5 is not None:\n'
            '                    try:\n'
            '                        mt5_exe = self.path or getattr(config, "MT5_PATH", "") or ""\n'
            '                        try:\n'
            '                            from config import find_default_mt5_path\n'
            '                            if not mt5_exe:\n'
            '                                mt5_exe = find_default_mt5_path() or ""\n'
            '                        except Exception:\n'
            '                            pass\n'
            '                        init_kwargs = {"path": mt5_exe} if mt5_exe else {}\n'
            '                        if not native_mt5.initialize(**init_kwargs):\n'
            '                            logger.warning("[MT5Broker] Hybrid: native init failed (%s)", native_mt5.last_error())\n'
            '                        else:\n'
            '                            if self.login and self.password and self.server:\n'
            '                                if not native_mt5.login(login=self.login, password=self.password, server=self.server):\n'
            '                                    logger.warning("[MT5Broker] Hybrid: native login failed (%s)", native_mt5.last_error())\n'
            '                            self._native_data = True\n'
            '                            self._hybrid_quote_source = "native"\n'
            '                            for _sym in ("XAUUSD", "BTCUSD"):\n'
            '                                try:\n'
            '                                    select_and_resolve_symbol(_sym)\n'
            '                                except Exception:\n'
            '                                    pass\n'
            '                            logger.info("[MT5Broker] Hybrid ON: native quotes + EA Bridge orders (magic=%s)", self.magic)\n'
            '                    except Exception as hy_err:\n'
            '                        logger.warning("[MT5Broker] Hybrid native init skipped: %s", hy_err)\n'
            '                if not getattr(self, "_native_data", False):\n'
            '                    self._hybrid_quote_source = "ea_or_feed"\n'
            '                logger.info("[MT5Broker] EA ORDERS connected (magic=%s, hybrid=%s, quotes=%s)",\n'
            '                            self.magic, hybrid_on, getattr(self, "_hybrid_quote_source", "?"))\n'
            '                return True\n'
        )
        if old in t:
            t = t.replace(old, new, 1)
            changed = True
            print("ea hybrid connect")
        else:
            print("WARN: ea connect block not found")

    t2 = t.replace(
        'if native_mt5 is not None and self._connected and self.effective_backend != "gateway":',
        'if native_mt5 is not None and self.effective_backend != "gateway":',
    )
    if t2 != t:
        t = t2
        changed = True
        print("native quote path relaxed")

    if "def hybrid_status" not in t:
        method = (
            "\n    def hybrid_status(self) -> dict:\n"
            '        """Orders=EA, quotes=native|ea."""\n'
            "        ea_ok = False\n"
            '        native_ok = bool(getattr(self, "_native_data", False))\n'
            "        try:\n"
            '            if hasattr(self, "ea_client") and self.ea_client is not None:\n'
            "                st = self.ea_client.status(magic=self.magic, timeout=1.0)\n"
            '                ea_ok = bool(st.get("ok"))\n'
            "        except Exception:\n"
            "            ea_ok = False\n"
            "        if native_mt5 is not None and not native_ok:\n"
            "            try:\n"
            "                native_ok = native_mt5.terminal_info() is not None\n"
            "            except Exception:\n"
            "                native_ok = False\n"
            "        return {\n"
            '            "backend": self.effective_backend,\n'
            '            "hybrid": bool(getattr(self, "_native_data", False) or getattr(self, "_force_hybrid", False)),\n'
            '            "orders_channel": "ea_bridge",\n'
            '            "quotes_channel": getattr(self, "_hybrid_quote_source", "unknown"),\n'
            '            "ea_ok": ea_ok,\n'
            '            "native_ok": native_ok,\n'
            '            "connected": bool(self._connected),\n'
            '            "magic": self.magic,\n'
            "        }\n\n"
        )
        idx = t.find("    def get_live_quote(self, symbol: str)")
        if idx > 0:
            t = t[:idx] + method + t[idx:]
            changed = True
            print("hybrid_status method")

    if changed:
        p.write_text(t, encoding="utf-8")
    else:
        print("mt5_broker: already hybrid or no change")

    cfg = Path("config.py")
    c = cfg.read_text(encoding="utf-8")
    if "MT5_HYBRID" not in c:
        c += '\nMT5_HYBRID = os.getenv("MT5_HYBRID", "true").lower() in ("true", "1", "yes", "on")\n'
        cfg.write_text(c, encoding="utf-8")
        print("config MT5_HYBRID")

    env = Path(".env.example")
    if env.exists():
        e = env.read_text(encoding="utf-8")
        if "MT5_HYBRID" not in e:
            e = e.replace("MT5_BACKEND=ea_bridge\n", "MT5_BACKEND=ea_bridge\nMT5_HYBRID=true\n")
            env.write_text(e, encoding="utf-8")
            print("env.example")

    import ast
    ast.parse(Path("mt5_broker.py").read_text(encoding="utf-8"))
    print("DONE: fresh hybrid features ready")

if __name__ == "__main__":
    main()
