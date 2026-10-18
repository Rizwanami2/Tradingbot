"""
Tests for Multi-Timeframe Trend & Volatility Heatmap / Matrix (Feature 5).
"""
import unittest

from mtf_matrix import (
    MatrixSnapshot,
    TimeframeAnalysis,
    analyze_symbol_mtf,
    get_matrix_dict,
    render_ascii_matrix,
    render_telegram_heatmap,
    render_telegram_multi_matrix,
)


class TestMTFMatrix(unittest.TestCase):
    def test_analyze_symbol_mtf_structure(self):
        """Verify that analyze_symbol_mtf returns a complete MatrixSnapshot with all timeframes."""
        snap = analyze_symbol_mtf("XAUUSD", force_refresh=True)
        self.assertIsInstance(snap, MatrixSnapshot)
        self.assertEqual(snap.symbol, "XAUUSD")
        self.assertGreater(snap.current_price, 0)
        self.assertIn(snap.confluence_grade.split()[0], ("STRONG", "BUY", "SELL", "NEUTRAL"))
        self.assertIn(snap.ltf_htf_alignment.split()[0], ("ALIGNED", "DIVERGENT", "MIXED"))
        self.assertIn(snap.top_bias, ("BULLISH", "BEARISH", "NEUTRAL"))

        # Verify all target timeframes exist
        expected_tfs = ["1m", "5m", "15m", "1h", "4h", "daily"]
        for tf in expected_tfs:
            self.assertIn(tf, snap.timeframes)
            tf_data = snap.timeframes[tf]
            self.assertIsInstance(tf_data, TimeframeAnalysis)
            self.assertGreaterEqual(tf_data.rsi, 0.0)
            self.assertLessEqual(tf_data.rsi, 100.0)
            self.assertGreaterEqual(tf_data.score, -100.0)
            self.assertLessEqual(tf_data.score, 100.0)
            self.assertIn(tf_data.trend_state, ("BULL++", "BULL", "RANGE", "BEAR", "BEAR--", "NEUTRAL"))
            self.assertIn(tf_data.volatility_state.split()[0], ("HIGH", "NORMAL", "SQUEEZE"))

    def test_btcusdt_analysis(self):
        """Verify that BTCUSDT can be analyzed successfully."""
        snap = analyze_symbol_mtf("BTCUSDT", force_refresh=True)
        self.assertEqual(snap.symbol, "BTCUSDT")
        self.assertGreater(snap.current_price, 1000)

    def test_render_telegram_heatmap_formatting(self):
        """Verify markdown structure and elements in Telegram heatmap render."""
        snap = analyze_symbol_mtf("XAUUSD")
        text = render_telegram_heatmap(snap)
        self.assertIn("Multi-Timeframe Confluence Matrix", text)
        self.assertIn("Live Price:", text)
        self.assertIn("Score:", text)
        self.assertIn("M1 ", text)
        self.assertIn("M5 ", text)
        self.assertIn("H1 ", text)
        self.assertIn("Recommendation:", text)

    def test_render_telegram_multi_matrix(self):
        """Verify multi-symbol summary render."""
        text = render_telegram_multi_matrix(["XAUUSD", "BTCUSDT"])
        self.assertIn("Multi-Asset MTF Confluence Heatmap", text)
        self.assertIn("XAUUSD", text)
        self.assertIn("BTCUSDT", text)

    def test_render_ascii_matrix(self):
        """Verify CLI terminal ASCII table rendering."""
        table = render_ascii_matrix("XAUUSD")
        self.assertIn("MULTI-TIMEFRAME CONFLUENCE MATRIX", table)
        self.assertIn("+-------+---------+--------+", table)
        self.assertIn("| TF    | Close   | Chg %", table)

    def test_get_matrix_dict_json_serializable(self):
        """Verify to_dict returns clean dictionary suitable for JSON serialization."""
        d = get_matrix_dict("XAUUSD")
        self.assertIsInstance(d, dict)
        self.assertEqual(d["symbol"], "XAUUSD")
        self.assertIn("timeframes", d)
        self.assertIn("1m", d["timeframes"])
        self.assertIn("rsi", d["timeframes"]["1m"])


if __name__ == "__main__":
    unittest.main()
