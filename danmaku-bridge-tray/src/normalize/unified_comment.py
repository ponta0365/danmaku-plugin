# unified_comment.py
import datetime
from dataclasses import dataclass, field, asdict
import json
from typing import Optional, List, Dict, Any

@dataclass
class UnifiedComment:
    platform: str  # "twitch" | "youtube" | "kick"
    message_id: str
    user_name: str
    text: str
    timestamp: str  # ISO 8601 format
    received_at: str = field(default_factory=lambda: datetime.datetime.now(datetime.timezone.utc).isoformat())
    version: str = "1.0"
    event_type: str = "message"  # "message" | "delete" | "notification" | "system"
    event_subtype: str = "text"
    user_id: Optional[str] = None
    user_color: Optional[str] = None
    badges: List[str] = field(default_factory=list)
    amount: Optional[Dict[str, Any]] = None  # Super Chat amounts: {"display": "$10.00", "currency": "USD", "value": 10.0}
    source_room: Optional[Dict[str, str]] = None  # {"broadcaster_id": "...", "room_id": "..."}

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_jsonl(self) -> str:
        """
        Returns a single-line JSON string suitable for JSONL files.
        """
        return json.dumps(self.to_dict(), ensure_ascii=False)
