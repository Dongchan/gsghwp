from __future__ import annotations


class GraphProtocolError(ValueError):
    code: str

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        super().__init__(f"HGN1_{code}" + (f":{detail}" if detail else ""))


__all__ = ["GraphProtocolError"]
