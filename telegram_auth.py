"""Small, testable Telegram command authorization helpers."""
from typing import Iterable, Optional


def parse_allowed_user_ids(value: str) -> frozenset[int]:
    """Parse a comma-separated allowlist, ignoring malformed/non-positive IDs."""
    allowed = set()
    for part in (value or "").split(","):
        try:
            user_id = int(part.strip())
        except (TypeError, ValueError):
            continue
        if user_id > 0:
            allowed.add(user_id)
    return frozenset(allowed)


def is_authorized_user(user_id: Optional[int], allowed_user_ids: Iterable[int]) -> bool:
    """Return False when the sender is unknown or the allowlist is empty."""
    if user_id is None:
        return False
    try:
        uid = int(user_id)
        return uid > 0 and uid in {int(value) for value in allowed_user_ids}
    except (TypeError, ValueError):
        return False
