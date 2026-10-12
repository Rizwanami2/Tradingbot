"""Regression tests for Telegram command allowlist parsing and checks."""
import unittest

from telegram_auth import is_authorized_user, parse_allowed_user_ids


class TestTelegramAuth(unittest.TestCase):
    def test_parse_comma_separated_ids_and_ignore_invalid_values(self):
        self.assertEqual(
            parse_allowed_user_ids("123, 456, invalid, -7, 123, "),
            frozenset({123, 456}),
        )

    def test_empty_allowlist_fails_closed(self):
        self.assertFalse(is_authorized_user(123, frozenset()))
        self.assertFalse(is_authorized_user(None, frozenset({123})))

    def test_only_allowlisted_user_is_authorized(self):
        allowed = parse_allowed_user_ids("123,456")
        self.assertTrue(is_authorized_user(456, allowed))
        self.assertFalse(is_authorized_user(789, allowed))
        self.assertFalse(is_authorized_user(0, allowed))


if __name__ == "__main__":
    unittest.main()
