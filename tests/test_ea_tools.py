#!/usr/bin/env python3
"""
Unit Tests for EA Bridge Diagnostic & Deploy Tools.
===================================================
Tests `scripts/test_ea_bridge.py` and `scripts/deploy_ea.py`.
"""
from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from scripts.deploy_ea import (
    SOURCE_EA,
    _calc_sha256,
    deploy_ea,
    discover_terminals,
)
from scripts.test_ea_bridge import (
    _test_directory_permissions,
    resolve_bridge_dir,
    run_diagnostics,
)


class TestEABridgeDiagnosticTool(unittest.TestCase):

    def setUp(self):
        self.tmp_dir = Path(tempfile.mkdtemp(prefix="test_ea_diag_"))

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_directory_permissions(self):
        exists, writable, lat, err = _test_directory_permissions(self.tmp_dir)
        self.assertTrue(exists)
        self.assertTrue(writable)
        self.assertGreaterEqual(lat, 0.0)
        self.assertIsNone(err)

    def test_resolve_bridge_dir_custom(self):
        p = resolve_bridge_dir(str(self.tmp_dir))
        self.assertEqual(p, self.tmp_dir.resolve())

    def test_run_diagnostics_simulation(self):
        res = run_diagnostics(
            custom_dir=str(self.tmp_dir),
            magic=112233,
            timeout=3.0,
            simulate=True,
        )
        self.assertEqual(res.status, "PASS")
        self.assertTrue(res.dir_exists)
        self.assertTrue(res.dir_writable)
        self.assertTrue(res.ea_connected)
        self.assertGreater(res.ea_roundtrip_ms, 0.0)
        self.assertEqual(res.account_info.get("account"), 99887766)
        self.assertEqual(len(res.open_positions), 1)

    def test_run_diagnostics_timeout(self):
        # Empty directory without simulator should warn and timeout gracefully
        res = run_diagnostics(
            custom_dir=str(self.tmp_dir),
            magic=112233,
            timeout=0.2,
            simulate=False,
        )
        self.assertEqual(res.status, "WARN")
        self.assertFalse(res.ea_connected)
        self.assertGreater(len(res.warnings), 0)
        self.assertGreater(len(res.hints), 0)


class TestEADeployTool(unittest.TestCase):

    def setUp(self):
        self.tmp_root = Path(tempfile.mkdtemp(prefix="test_ea_deploy_"))
        self.experts_dir = self.tmp_root / "MQL5" / "Experts"

    def tearDown(self):
        shutil.rmtree(self.tmp_root, ignore_errors=True)

    def test_source_ea_exists(self):
        self.assertTrue(SOURCE_EA.is_file(), f"SOURCE_EA missing at {SOURCE_EA}")
        sha = _calc_sha256(SOURCE_EA)
        self.assertEqual(len(sha), 16)

    def test_discover_terminals_custom_target(self):
        terms = discover_terminals(custom_target=str(self.tmp_root))
        self.assertEqual(len(terms), 1)
        self.assertEqual(terms[0].data_dir, self.tmp_root)
        self.assertEqual(terms[0].experts_dir, self.experts_dir)

    def test_deploy_ea_dry_run(self):
        terms = discover_terminals(custom_target=str(self.tmp_root))
        results = deploy_ea(source_ea=SOURCE_EA, terminals=terms, dry_run=True, auto_compile=False)
        self.assertEqual(len(results), 1)
        self.assertTrue(results[0].copied)
        # In dry run, file is not physically copied
        dest_file = self.experts_dir / SOURCE_EA.name
        self.assertFalse(dest_file.exists())

    def test_deploy_ea_live_copy(self):
        terms = discover_terminals(custom_target=str(self.tmp_root))
        results = deploy_ea(source_ea=SOURCE_EA, terminals=terms, dry_run=False, auto_compile=False)
        self.assertEqual(len(results), 1)
        self.assertTrue(results[0].copied)
        dest_file = self.experts_dir / SOURCE_EA.name
        self.assertTrue(dest_file.is_file())
        self.assertEqual(dest_file.stat().st_size, SOURCE_EA.stat().st_size)


if __name__ == "__main__":
    unittest.main()
