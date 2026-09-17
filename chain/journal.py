"""只增哈希链日志。

监管链上每次状态变化都追加一条带前序哈希的事件记录；
历史记录既不能改写也不能删除，回放时逐链校验即可发现任何篡改。
"""

import json
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256


GENESIS_HASH = "0" * 64


def digest_payload(payload: dict) -> str:
    """对事件载荷计算稳定的 SHA-256 摘要。"""
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(blob.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class JournalEntry:
    seq: int
    at: datetime
    actor: str
    action: str
    payload: dict
    prev_hash: str
    entry_hash: str


class Journal:
    def __init__(self) -> None:
        self._entries: list[JournalEntry] = []

    def append(self, at: datetime, actor: str, action: str, payload: dict) -> JournalEntry:
        prev_hash = self._entries[-1].entry_hash if self._entries else GENESIS_HASH
        body = {
            "seq": len(self._entries),
            "at": at.isoformat(),
            "actor": actor,
            "action": action,
            "payload": payload,
            "prev": prev_hash,
        }
        entry = JournalEntry(
            seq=body["seq"],
            at=at,
            actor=actor,
            action=action,
            payload=payload,
            prev_hash=prev_hash,
            entry_hash=digest_payload(body),
        )
        self._entries.append(entry)
        return entry

    @property
    def entries(self) -> tuple[JournalEntry, ...]:
        return tuple(self._entries)

    def verify(self) -> bool:
        """逐条复核哈希链；任何断链或载荷篡改都会返回 False。"""
        prev = GENESIS_HASH
        for entry in self._entries:
            if entry.prev_hash != prev:
                return False
            body = {
                "seq": entry.seq,
                "at": entry.at.isoformat(),
                "actor": entry.actor,
                "action": entry.action,
                "payload": entry.payload,
                "prev": entry.prev_hash,
            }
            if digest_payload(body) != entry.entry_hash:
                return False
            prev = entry.entry_hash
        return True
