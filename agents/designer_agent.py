"""
UX/Designer Agent — PM'in ürettiği backlog'u alır, her story için basit bir
kullanıcı akışı / ekran düzeni (metin tabanlı wireframe) üretir.

İki yönde işbirliği yapar:
  1. PM'in story'sini belirsiz bulursa -> PM'e netleştirme sorar.
  2. Tech Lead, kendi ürettiği UX akışını teknik tasarım için yetersiz bulursa
     -> bu node revizyon moduna geçer, Tech Lead'in sorusuna cevaben akışı günceller.

Girdi: state["backlog"] (ilk çalışma) VEYA state["clarification_needed"] hedefi
       "designer_agent" ise (revizyon çalışması)
Çıktı: state["design_notes"] VEYA state["clarification_needed"] (PM'e yönelik)
"""
import json
import os

from graph.state import SprintState
from agents.logger import log_step
from agents.checkin_nodes import check_and_answer_question
from agents.llm_utils import call_ollama_json

OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5")
OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")

MAX_REVISIONS = 2

SYSTEM_PROMPT = """Sen deneyimli bir UX/Product Designer agent'ısın. Product Manager'ın \
ürettiği user story'leri alıp, geliştiricinin (dev agent) doğrudan koda dökebileceği kadar \
somut bir kullanıcı akışı / ekran düzeni tarif ediyorsun.

Kurallar:
- Her story için: ekranda hangi elemanlar var, kullanıcı hangi sırayla etkileşime giriyor, \
hata durumunda ne gösteriliyor, başarı durumunda ne oluyor.
- Görsel tasarım detayına (renk, font, logo) girme — sadece akış ve yapı.
- EĞER bir story'yi tasarlamak için gerçekten yetersiz/belirsiz bilgi varsa, "design_notes" \
üretme, "clarification_needed" alanını doldur ve PM'e ne sorman gerektiğini net yaz. Bunu \
sadece gerçekten gerekliyse yap.
- SADECE geçerli JSON döndür.

Normal durum:
{"design_notes": [{"story_id": "US-1", "flow": ["adım 1: ...", "adım 2: ..."], "notes": "..."}], "clarification_needed": null}

Netleştirme gerekli durumu:
{"design_notes": [], "clarification_needed": "US-2 için kabul kriterleri çelişkili: ..."}
"""

REVISION_SYSTEM_PROMPT = """Sen bir UX/Designer agent'ısın. Az önce ürettiğin UX akışı hakkında \
Tech Lead'den bir netleştirme/değişiklik talebi geldi. Bu talebi değerlendirip ilgili \
design_notes'u güncelle. Diğer story'lerin akışını olduğu gibi koru.

SADECE geçerli JSON döndür, aynı formatta (design_notes + clarification_needed: null)."""


def designer_agent_node(state: SprintState) -> SprintState:
    check_and_answer_question(state)
    is_revision = (
        bool(state.get("clarification_needed"))
        and state.get("clarification_target") == "designer_agent"
    )

    if is_revision:
        user_content = (
            f"Mevcut UX akışları:\n{json.dumps(state.get('design_notes', []), ensure_ascii=False, indent=2)}\n\n"
            f"Tech Lead'den gelen talep:\n{state['clarification_needed']}"
        )
        system = REVISION_SYSTEM_PROMPT
    else:
        backlog_summary = json.dumps(state["backlog"], ensure_ascii=False, indent=2)
        user_content = f"Backlog:\n{backlog_summary}"
        system = SYSTEM_PROMPT

    parsed = call_ollama_json(
        host=OLLAMA_HOST, model=OLLAMA_MODEL, system_prompt=system,
        user_content=user_content, temperature=0.3, agent_label="designer_agent",
    )

    if is_revision:
        state["design_notes"] = parsed.get("design_notes", state.get("design_notes", []))
        state["clarification_needed"] = None
        state["clarification_source"] = None
        state["clarification_target"] = None
        state["revision_count"] = state.get("revision_count", 0) + 1
        log_step(
            state, agent="designer_agent", action="revised",
            detail="Tech Lead'in talebine cevaben UX akışı güncellendi.",
        )
        return state

    clarification = parsed.get("clarification_needed")
    revision_count = state.get("revision_count", 0)

    if clarification and revision_count < MAX_REVISIONS:
        state["clarification_needed"] = clarification
        state["clarification_source"] = "designer_agent"
        state["clarification_target"] = "pm_agent"
        log_step(
            state, agent="designer_agent", action="clarification_requested",
            detail=clarification, target_agent="pm_agent",
        )
        return state

    if clarification and revision_count >= MAX_REVISIONS:
        log_step(
            state, agent="designer_agent", action="clarification_limit_reached",
            detail=f"Netleştirme limiti ({MAX_REVISIONS}) aşıldı, mevcut backlog'la devam ediliyor.",
        )

    if "design_notes" not in parsed:
        raise ValueError(f"Designer Agent çıktısında 'design_notes' alanı yok: {parsed}")

    state["design_notes"] = parsed["design_notes"]
    state["clarification_needed"] = None
    log_step(
        state, agent="designer_agent", action="design_completed",
        detail=f"{len(state['design_notes'])} story için UX akışı üretildi.",
    )
    return state
