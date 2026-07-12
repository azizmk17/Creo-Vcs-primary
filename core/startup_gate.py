"""Pure startup-loader timing state used by the Qt startup window."""


MINIMUM_STARTUP_LOADER_MS = 4000


class StartupGate:
    """Release startup exactly once after both timing and readiness conditions hold."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.minimum_elapsed = False
        self.page_ready = False
        self.released = False

    def mark_minimum_elapsed(self) -> bool:
        self.minimum_elapsed = True
        return self._release_if_ready()

    def mark_page_ready(self) -> bool:
        self.page_ready = True
        return self._release_if_ready()

    def _release_if_ready(self) -> bool:
        if self.released or not (self.minimum_elapsed and self.page_ready):
            return False
        self.released = True
        return True
