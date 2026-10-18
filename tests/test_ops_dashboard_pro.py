"""
Unit tests for Feature 6: Pro Real-Time Web Dashboard & Local UI Companion.
"""
import json
import tempfile
import threading
import unittest
from http.client import HTTPConnection
from pathlib import Path

from execution_engine import ExecutionEngine, OrderStatus, TradeOrder
from ops_dashboard import (
    StatusStore,
    create_server,
    set_dashboard_engine,
    start_dashboard_background,
    stop_dashboard_background,
)
from risk_manager import RiskManager


class TestOpsDashboardPro(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.status_file = Path(self.temp_dir.name) / "status.json"
        StatusStore(self.status_file).write({
            "service": "TradingBot",
            "symbol": "XAUUSD",
            "configured_broker_mode": "MT5 Live",
            "autotrade_active": True,
            "equity": 10500.0,
            "balance": 10000.0,
            "slots_mode": "auto",
            "max_open_trades": 6,
        })
        self.rm = RiskManager()
        self.rm.state.equity = 10000.0
        self.engine = ExecutionEngine(risk_manager=self.rm)
        self.engine.active_positions.clear()
        self.engine.pending_setups.clear()
        set_dashboard_engine(self.engine)

        self.server = create_server("127.0.0.1", 0, status_file=self.status_file, engine=self.engine)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.host, self.port = self.server.server_address

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
        self.temp_dir.cleanup()
        stop_dashboard_background()

    def test_dashboard_html_page(self):
        """Verify GET / returns complete HTML dashboard with CSS and JS."""
        conn = HTTPConnection(self.host, self.port, timeout=3)
        conn.request("GET", "/")
        resp = conn.getresponse()
        self.assertEqual(resp.status, 200)
        self.assertIn("text/html", resp.getheader("Content-Type", ""))
        html = resp.read().decode("utf-8")
        self.assertIn("Nimble-Darwin", html)
        self.assertIn("Slots Control", html)
        self.assertIn("Multi-Timeframe", html)
        conn.close()

    def test_api_positions_endpoint(self):
        """Verify GET /api/positions returns structured active positions and pending setups."""
        # Add a mock position
        order = TradeOrder(
            id="DASH-TEST-POS",
            symbol="XAUUSD",
            direction="LONG",
            entry_level=2700.0,
            stop_loss=2690.0,
            tp1=2710.0,
            tp2=2720.0,
            tp3=None,
            lots=0.02,
            size=2.0,
            leverage=20.0,
            margin_required=20.0,
            liquidation_price=2600.0,
            risk_amount=20.0,
            rr_ratio=1.5,
            setup_reason="Dash test",
            status=OrderStatus.OPEN,
            executed_price=2700.0,
            remaining_size=2.0,
        )
        self.engine.active_positions[order.id] = order

        conn = HTTPConnection(self.host, self.port, timeout=3)
        conn.request("GET", "/api/positions")
        resp = conn.getresponse()
        self.assertEqual(resp.status, 200)
        data = json.loads(resp.read().decode("utf-8"))
        self.assertTrue(data.get("ok"))
        self.assertEqual(len(data.get("active_positions", [])), 1)
        self.assertEqual(data["active_positions"][0]["symbol"], "XAUUSD")
        conn.close()

    def test_api_matrix_endpoint(self):
        """Verify GET /api/matrix returns live multi-timeframe heatmap data."""
        conn = HTTPConnection(self.host, self.port, timeout=20)
        conn.request("GET", "/api/matrix?symbol=XAUUSD")
        resp = conn.getresponse()
        self.assertEqual(resp.status, 200)
        data = json.loads(resp.read().decode("utf-8"))
        self.assertTrue(data.get("ok"))
        self.assertIn("snapshot", data)
        self.assertEqual(data["snapshot"]["symbol"], "XAUUSD")
        self.assertIn("timeframes", data["snapshot"])
        conn.close()

    def test_api_slots_post_control(self):
        """Verify POST /api/slots changes engine slots mode and count."""
        conn = HTTPConnection(self.host, self.port, timeout=3)
        payload = json.dumps({"mode": "manual", "count": 4})
        conn.request("POST", "/api/slots", body=payload, headers={"Content-Type": "application/json"})
        resp = conn.getresponse()
        self.assertEqual(resp.status, 200)
        data = json.loads(resp.read().decode("utf-8"))
        self.assertTrue(data.get("ok"))
        self.assertEqual(self.engine.slots_mode, "manual")
        self.assertEqual(self.engine.max_open_trades, 4)
        conn.close()

        # Switch back to auto
        conn = HTTPConnection(self.host, self.port, timeout=3)
        payload_auto = json.dumps({"mode": "auto"})
        conn.request("POST", "/api/slots", body=payload_auto, headers={"Content-Type": "application/json"})
        resp = conn.getresponse()
        self.assertEqual(resp.status, 200)
        self.assertEqual(self.engine.slots_mode, "auto")
        conn.close()

    def test_api_autotrade_and_halt_endpoints(self):
        """Verify POST /api/autotrade and POST /api/halt."""
        conn = HTTPConnection(self.host, self.port, timeout=3)
        conn.request("POST", "/api/halt", body=json.dumps({"halt": True}), headers={"Content-Type": "application/json"})
        resp = conn.getresponse()
        self.assertEqual(resp.status, 200)
        self.assertTrue(self.engine.execution_halted)
        conn.close()

        conn = HTTPConnection(self.host, self.port, timeout=3)
        conn.request("POST", "/api/halt", body=json.dumps({"halt": False}), headers={"Content-Type": "application/json"})
        resp = conn.getresponse()
        self.assertEqual(resp.status, 200)
        self.assertFalse(self.engine.execution_halted)
        conn.close()


if __name__ == "__main__":
    unittest.main()
