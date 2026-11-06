import secrets
import threading
import time
import uuid


_LOGICAL_BITS = 16
_LOGICAL_MASK = (1 << _LOGICAL_BITS) - 1
_last_hlc = 0
_hlc_lock = threading.Lock()


def new_uuid7() -> str:
    timestamp_ms = time.time_ns() // 1_000_000
    random_a = secrets.randbits(12)
    random_b = secrets.randbits(62)
    value = (
        (timestamp_ms << 80)
        | (0x7 << 76)
        | (random_a << 64)
        | (0b10 << 62)
        | random_b
    )
    return str(uuid.UUID(int=value))


def next_hlc(observed_hlc: int = 0) -> int:
    global _last_hlc

    wall_ms = time.time_ns() // 1_000_000
    observed_ms, observed_logical = divmod(observed_hlc, 1 << _LOGICAL_BITS)
    with _hlc_lock:
        previous_ms, previous_logical = divmod(_last_hlc, 1 << _LOGICAL_BITS)
        physical_ms = max(wall_ms, observed_ms, previous_ms)

        if physical_ms == previous_ms == observed_ms:
            logical = max(previous_logical, observed_logical) + 1
        elif physical_ms == previous_ms:
            logical = previous_logical + 1
        elif physical_ms == observed_ms:
            logical = observed_logical + 1
        else:
            logical = 0

        if logical > _LOGICAL_MASK:
            physical_ms += 1
            logical = 0

        _last_hlc = (physical_ms << _LOGICAL_BITS) | logical
        return _last_hlc
