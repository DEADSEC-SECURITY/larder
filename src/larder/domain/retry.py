RETRY_DELAYS = (60, 300, 1800, 7200, 43200, 86400)


def retry_delay_seconds(attempt_count: int) -> int:
    index = max(0, min(attempt_count - 1, len(RETRY_DELAYS) - 1))
    return RETRY_DELAYS[index]
