"""Session state management — mode sticky."""


class Session:
    """Holds the current conversation session state.

    The AI controls mode via memory_set_mode().
    Mode is sticky — once set, it persists until explicitly changed.

    Modes:
        hybrid (default)  — search both human & project memory
        human             — search human memory only
        code              — search project memory only
    """

    VALID_MODES = {"hybrid", "human", "code"}

    def __init__(self):
        self.reset()

    def reset(self):
        """Reset to defaults (called at conversation start)."""
        self._mode = "hybrid"
        self._project_id: str | None = None
        self._project_path: str | None = None

    # ── Mode ───────────────────────────────────────────────────

    @property
    def mode(self) -> str:
        return self._mode

    def set_mode(self, mode: str):
        """Switch mode. Stays sticky until next explicit switch."""
        if mode not in self.VALID_MODES:
            raise ValueError(f"Invalid mode: {mode}. Choose from {self.VALID_MODES}")
        self._mode = mode

    # ── Project ────────────────────────────────────────────────

    @property
    def project_id(self) -> str | None:
        return self._project_id

    @property
    def project_path(self) -> str | None:
        return self._project_path

    def set_project(self, project_id: str | None, project_path: str | None = None):
        self._project_id = project_id
        self._project_path = project_path

    # ── Status ─────────────────────────────────────────────────

    def status(self) -> dict:
        return {
            "mode": self._mode,
            "project_id": self._project_id,
            "project_path": self._project_path,
        }

    def __repr__(self):
        return f"Session(mode={self._mode}, project={self._project_id})"
