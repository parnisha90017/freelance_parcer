from __future__ import annotations

from collections import defaultdict
from time import monotonic

_requests: dict[str, dict[int, list[float]]] = defaultdict(dict)


def is_rate_limited(user_id: int, action: str, max_requests: int = 3, period: int = 60) -> bool:
    now = monotonic()
    threshold = now - period
    action_requests = _requests[action]
    timestamps = [timestamp for timestamp in action_requests.get(user_id, []) if timestamp >= threshold]

    if len(timestamps) >= max_requests:
        action_requests[user_id] = timestamps
        return True

    timestamps.append(now)
    action_requests[user_id] = timestamps

    stale_users = [stored_user_id for stored_user_id, values in action_requests.items() if not values or values[-1] < threshold]
    for stored_user_id in stale_users:
        action_requests.pop(stored_user_id, None)

    if not action_requests:
        _requests.pop(action, None)

    return False
