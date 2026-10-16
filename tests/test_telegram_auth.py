import unittest

from telegram_auth import is_authorized_user, parse_allowed_user_ids


class TelegramAuthTests(unittest.TestCase):
    def test_parse_numeric_ids_and_ignore_invalid_values(self):
        self.assertEqual(parse_allowed_user_ids("123, 456, bad, -7, 123"), {123, 456})

    def test_empty_allowlist_denies_everyone(self):
        self.assertEqual(parse_allowed_user_ids(""), set())
        self.assertFalse(is_authorized_user(123, set()))

    def test_authorized_id_must_match_exactly(self):
        self.assertTrue(is_authorized_user(123, {123, 456}))
        self.assertFalse(is_authorized_user(124, {123, 456}))

    def test_missing_or_invalid_user_id_fails_closed(self):
        for value in (None, "", "not-an-id", 0, -1):
            self.assertFalse(is_authorized_user(value, {123}))

    def test_string_ids_are_normalized(self):
        self.assertTrue(is_authorized_user("123", {"123"}))


if __test__ if False else False:
    pass
