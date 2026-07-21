from __future__ import annotations


class DocumentAutomationError(RuntimeError):
    """Expected input, template, or Hancom operation failure."""


class ProcessScanError(DocumentAutomationError):
    reason: str

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class HwpRuntimeSafetyError(DocumentAutomationError):
    reason: str

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class HwpLiveError(DocumentAutomationError):
    reason: str

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason
