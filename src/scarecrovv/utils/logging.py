from typing import Any, Dict, List

class EventLog:
    def __init__(self) -> None:
        self.records: List[Dict[str,Any]] = []
    def emit(self, rec: Dict[str,Any]) -> None:
        self.records.append(rec)
