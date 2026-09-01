"""
Ortak log altyapısı — her agent'ın attığı adımı, kararını ve gerekçesini
state["conversation_log"]'a yazar. Hem Slack'e basılacak insan-okunur log
hem de debug için makine-okunur kayıt olarak kullanılır.

Kural: HER agent node'u işini bitirmeden önce log_step() çağırmalı.
Bu, "hangi agent'ın hangi turda neyi bozduğunu" bulmayı kolaylaştırır —
kompleks geri döngülü bir sistemde bu olmadan debug etmek çok zorlaşır.
"""
from datetime import datetime, timezone
from typing import TypedDict


class LogEntry(TypedDict):
    timestamp: str
    agent: str
    action: str          # örn. "backlog_created", "clarification_requested", "revised"
    detail: str           # insan-okunur özet
    target_agent: str | None  # bu adım başka bir agent'a mı yönelik (örn. Designer -> PM)


def log_step(state: dict, agent: str, action: str, detail: str, target_agent: str | None = None) -> None:
    """State içindeki conversation_log listesine yeni bir kayıt ekler (in-place)."""
    if state.get("conversation_log") is None:
        state["conversation_log"] = []

    entry: LogEntry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "agent": agent,
        "action": action,
        "detail": detail,
        "target_agent": target_agent,
    }
    state["conversation_log"].append(entry)

    # Konsola da anlık yazdır — geliştirme aşamasında canlı takip için.
    # İleride bu satır Slack webhook çağrısına dönüşecek.
    arrow = f" -> {target_agent}" if target_agent else ""
    print(f"[LOG] {agent}{arrow} | {action}: {detail}")


def print_full_log(state: dict) -> None:
    """Sprint sonunda / debug için tüm log akışını sırayla yazdırır."""
    print("\n=== CONVERSATION LOG (tüm akış) ===")
    for entry in state.get("conversation_log", []):
        arrow = f" -> {entry['target_agent']}" if entry.get("target_agent") else ""
        print(f"  [{entry['timestamp']}] {entry['agent']}{arrow} | {entry['action']}: {entry['detail']}")
