"""Regression tests verifying that paper-broker has been removed and fails closed."""

import unittest

from execution import BrokerConfigurationError, PaperBroker


class TestPaperBrokerAccounting(unittest.TestCase):
    def test_paper_broker_initialization_fails_closed(self):
        with self.assertRaises(BrokerConfigurationError):
            PaperBroker()

    def test_paper_broker_rejects_custom_params(self):
        with self.assertRaises(BrokerConfigurationError):
            PaperBroker(initial_balance=5000.0, commission_pct=0.001)


if __name__ == "__main__":
    unittest.main()
