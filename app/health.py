"""Track feed stalls and stable recovery using an injected monotonic clock."""
class FeedHealth:
    def __init__(self, stall_seconds=4, recovery_seconds=2):
        self.stall_seconds = stall_seconds
        self.recovery_seconds = recovery_seconds
        self.last_bytes = None
        self.last_growth = None
        self.good_since = None

    def update(self, ready, byte_count, playing, now):
        if byte_count != self.last_bytes:
            self.last_growth = now
        self.last_bytes = byte_count
        good = ready and byte_count > 0 and self.last_growth is not None and now - self.last_growth < self.stall_seconds and playing
        if not good:
            self.good_since = None
            return False
        if self.good_since is None:
            self.good_since = now
        return now - self.good_since >= self.recovery_seconds
