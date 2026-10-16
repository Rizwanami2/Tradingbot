"""Telegram user allowlist parsing and authorization helpers."""

from collections.abc import Iterable


def parse_allowed_user_ids(value: str | None) -> set[int]:
    """Parse comma-separated numeric Telegram user IDs; invalid tokens are ignored."""
    allowed: set[int] = set()
    for item in str(value or "").split(","):
        token = item.strip()
        if not token:
            continue
        try:
            user_id = int(token)
        except (TypeError, ValueError):
            continue
        if user_id > 0:
            allowed.add(user_id)
    return allowed


def is_authorized_user(user_id: object, allowed_user_ids: Iterable[int] | None) -> bool:
    """Return True only for a valid numeric ID explicitly present in the allowlist."""
    try:
        numeric_id = int(user_id)  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError):
        return False
    if numeric_id <= 0 or allowed_user_ids is None:
        return False
    try:
        return numeric_id in {int(item) for item in allowed_user_ids}
    except (TypeError, ValueError, OverflowError):
        return False
