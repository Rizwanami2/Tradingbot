"""Tests for local operations status, health checks, and dashboard access controls."""
import json
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from http.client import HTTPConnection
from pathlib import Path

from ops_dashboard import (
    StatusReadError,
    StatusStore,
    create_server,
    health_from_snapshot,
    start_dashboard_background,
    stop_dashboard_background,
)


class TestOpsDashboard(unittest.TestCase):
    def test_status_store_round_trip_and_corrupt_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "status.json"
            store = StatusStore(path)
            store.write({"last_success_at": datetime.now(timezone.utc).isoformat()})
            self.assertEqual(store.read()["schema_version"], 1)
            self.assertFalse(path.with_suffix(".json.tmp").exists())
            path.write_text("{bad", encoding="utf-8")
            with self.assertRaises(StatusReadError):
                store.read()

    def test_health_detects_missing_stale_and_errors(self):
        now = datetime.now(timezone.utc)
        healthy = health_from_snapshot(
            {"last_success_at": now.isoformat(), "consecutive_errors": 0}, now=now
        )
        self.assertTrue(healthy["healthy"])
        missing = health_from_snapshot({}, now=now)
        self.assertFalse(missing["healthy"])
        stale = health_from_snapshot(
            {"last_success_at": (now - timedelta(seconds=120)).isoformat(),
             "consecutive_errors": 0}, now=now, stale_after_seconds=90
        )
        self.assertFalse(stale["healthy"])
        errors = health_from_snapshot(
            {"last_success_at": now.isoformat(), "consecutive_errors": 2,
             "last_error": "provider error"}, now=now
        )
        self.assertFalse(errors["healthy"])
        self.assertNotIn("provider error", json.dumps(errors))

    def test_non_loopback_dashboard_requires_token(self):
        with self.assertRaisesRegex(ValueError, "token is required"):
            create_server("0.0.0.0", 8765, token="")

    def test_api_requires_bearer_token_when_configured(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "status.json"
            StatusStore(path).write({
                "symbol": "BTC/USDT",
                "last_success_at": datetime.now(timezone.utc).isoformat(),
                "consecutive_errors": 0,
            })
            server = create_server("127.0.0.1", 0, status_file=path, token="test-token")
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                host, port = server.server_address
                conn = HTTPConnection(host, port, timeout=3)
                conn.request("GET", "/api/status")
                response = conn.getresponse()
                self.assertEqual(response.status, 401)
                response.read()
                conn.close()

                conn = HTTPConnection(host, port, timeout=3)
                conn.request("GET", "/api/status", headers={"Authorization": "Bearer test-token"})
                response = conn.getresponse()
                self.assertEqual(response.status, 200)
                body = json.loads(response.read())
                self.assertEqual(body["symbol"], "BTC/USDT")
                conn.close()
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=3)

    def test_web_dashboard_html_and_companion_endpoints(self):
        from unittest.mock import patch
        from mtf_matrix import MatrixSnapshot
        mock_snap = MatrixSnapshot(
            symbol="XAUUSD",
            current_price=2720.0,
            timeframes={},
            overall_score=75.0,
            confluence_grade="STRONG BUY",
            ltf_htf_alignment="ALIGNED",
            top_bias="BULLISH",
            recommendation="Bullish continuation",
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
        with tempfile.TemporaryDirectory() as directory, patch("mtf_matrix.analyze_symbol_mtf", return_value=mock_snap):
            path = Path(directory) / "status.json"
            StatusStore(path).write({
                "symbol": "XAUUSD",
                "equity": 5000.0,
                "balance": 5000.0,
                "slots_mode": "auto",
                "max_open_trades": 6,
                "last_success_at": datetime.now(timezone.utc).isoformat(),
                "consecutive_errors": 0,
            })
            server = create_server("127.0.0.1", 0, status_file=path)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                host, port = server.server_address
                # 1. HTML index
                conn = HTTPConnection(host, port, timeout=10)
                conn.request("GET", "/")
                res = conn.getresponse()
                self.assertEqual(res.status, 200)
                html = res.read().decode("utf-8")
                self.assertIn("Nimble-Darwin", html)
                self.assertIn("Active Positions", html)
                conn.close()

                # 2. GET /api/positions
                conn = HTTPConnection(host, port, timeout=10)
                conn.request("GET", "/api/positions")
                res = conn.getresponse()
                self.assertEqual(res.status, 200)
                data = json.loads(res.read())
                self.assertIn("active_positions", data)
                self.assertIn("pending_setups", data)
                conn.close()

                # 3. GET /api/matrix
                conn = HTTPConnection(host, port, timeout=10)
                conn.request("GET", "/api/matrix?symbol=XAUUSD")
                res = conn.getresponse()
                self.assertEqual(res.status, 200)
                m_data = json.loads(res.read())
                self.assertTrue(m_data["ok"])
                self.assertIn("snapshot", m_data)
                conn.close()

                # 4. POST /api/slots
                conn = HTTPConnection(host, port, timeout=10)
                conn.request("POST", "/api/slots", body=json.dumps({"mode": "manual", "count": 4}), headers={"Content-Type": "application/json"})
                res = conn.getresponse()
                self.assertEqual(res.status, 200)
                s_data = json.loads(res.read())
                self.assertTrue(s_data["ok"])
                conn.close()

                # 5. POST /api/autotrade
                conn = HTTPConnection(host, port, timeout=10)
                conn.request("POST", "/api/autotrade", body=json.dumps({"enabled": False}), headers={"Content-Type": "application/json"})
                res = conn.getresponse()
                self.assertEqual(res.status, 200)
                at_data = json.loads(res.read())
                self.assertTrue(at_data["ok"])
                conn.close()

                # 6. POST /api/close and /api/cancel and /api/halt
                for ep, pl in [("/api/close", {"symbol": "XAUUSD"}), ("/api/cancel", {"id": "SET1"}), ("/api/halt", {"halt": True})]:
                    conn = HTTPConnection(host, port, timeout=10)
                    conn.request("POST", ep, body=json.dumps(pl), headers={"Content-Type": "application/json"})
                    res = conn.getresponse()
                    self.assertEqual(res.status, 200)
                    resp_data = json.loads(res.read())
                    self.assertTrue(resp_data["ok"])
                    conn.close()
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=3)

    def test_start_and_stop_dashboard_background(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "status.json"
            StatusStore(path).write({"last_success_at": datetime.now(timezone.utc).isoformat()})
            srv, url = start_dashboard_background("127.0.0.1", 0, status_file=path)
            self.assertTrue(url.startswith("http://127.0.0.1:"))
            stop_dashboard_background()


if __name__ == "__main__":
    unittest.main()
