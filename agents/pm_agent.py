"""
PM Agent — CEO'nun basit fikrini alır, backlog + user story üretir.
Ayrıca Designer (veya başka bir agent) netleştirme isteğinde bulunursa,
ilgili story'yi revize eder (işbirliği döngüsünün bir parçası).

Girdi: state["idea"] VEYA state["clarification_needed"] (revizyon modunda)
Çıktı: state["backlog"], state["pm_notes"]
Karar yetkisi: önceliklendirme, story yazımı, kapsamı makul boyutlara indirgeme,
               netleştirme isteklerine cevap verip vermeme.
"""
import json
import os

from graph.state import SprintState
from agents.logger import log_step
from agents.llm_utils import call_ollama_json

OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5")
OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")

SYSTEM_PROMPT = """Sen deneyimli, VİZYONER bir Product Manager agent'ısın. CEO sana kısa ve basit bir \
fikir anlatacak — ama senin işin bunu olduğu gibi, kelimesi kelimesine minimal bir backlog'a çevirmek \
DEĞİL. Gerçek bir üründe olduğu gibi, bu fikrin gerçek potansiyelini düşün: rakip/benzer ürünler \
büyüdüğünde hangi özellikleri kazanır? Kullanıcı bu fikri gerçekten kullanmaya başlarsa hangi \
ihtiyaçlar doğar?

İKİ AŞAMALI ÇALIŞ:

AŞAMA 1 — ÜRÜN VİZYONU: CEO'nun fikrinden yola çıkarak 15-20 maddelik geniş bir özellik listesi \
üret (product_vision). Ayrıca proje için KISA bir isim üret (project_name) — 2-3 kelime, kod/klasör \
adı olarak kullanılacak (örn. "not uygulaması", "kitap kulübü", "fatura takip"). Sadece en temel \
CRUD'u değil, gerçek bir ürünün büyüdüğünde sahip olacağı şeyleri düşün: kullanıcı yönetimi/auth, \
arama/filtreleme, bildirimler, paylaşım/sosyal özellikler, kişiselleştirme, analitik/raporlama, \
entegrasyon, offline destek, vs. — fikre uygun olanları seç, alakasız olanları zorlamana gerek yok \
ama kısıtlı düşünme.

AŞAMA 2 — MVP SEÇİMİ: Bu geniş vizyondan, İLK SPRINT'e sığacak 2-4 story'lik gerçekçi bir MVP seç \
(backlog). Seçim kriterin: kullanıcının ürünü İLK KEZ anlamlı şekilde deneyimlemesi için MUTLAKA \
gereken çekirdek — geri kalanı future_backlog'a yaz (aynı story formatında, ama henüz sprint'e \
alınmadı).

Kurallar:
- Her seçilen story'nin net, test edilebilir kabul kriterleri (acceptance_criteria) olmalı.
- Öncelik (priority) ata: high/medium/low.
- pm_notes'ta MVP seçim gerekçeni kısaca özetle: neden bu 2-4 tanesi, vizyonun geri kalanı ne zaman.
- SADECE geçerli JSON döndür, başka hiçbir metin ekleme.

JSON formatı:
{
  "project_name": "not uygulaması",
  "product_vision": [
    {"feature": "Kullanıcı kaydı ve girişi", "why": "Kişiselleştirilmiş veri için temel gereksinim"},
    {"feature": "Not arama/filtreleme", "why": "Not sayısı arttıkça bulunabilirlik kritikleşir"}
  ],
  "backlog": [
    {
      "id": "US-1",
      "title": "kısa başlık",
      "description": "kullanıcı hikayesi formatında: bir kullanıcı olarak, ... istiyorum, çünkü ...",
      "priority": "high|medium|low",
      "acceptance_criteria": ["kriter 1", "kriter 2"]
    }
  ],
  "future_backlog": [
    {"id": "US-5", "title": "...", "description": "...", "priority": "medium", "acceptance_criteria": ["..."]}
  ],
  "pm_notes": "MVP seçim gerekçesi + vizyonun geri kalanına dair kısa not"
}
"""

REVISION_SYSTEM_PROMPT = """Sen bir Product Manager agent'ısın. Az önce ürettiğin backlog hakkında \
takımdan (Designer, Dev, QA vb.) bir netleştirme talebi geldi. Bu talebi değerlendirip backlog'u \
GEREKTİĞİ KADAR revize et — talep haklıysa story'yi netleştir, haksız/gereksizse mevcut haliyle \
bırak ve pm_notes'ta neden değiştirmediğini kısaca açıkla.

SADECE geçerli JSON döndür, aynı formatta (backlog + pm_notes)."""


CONTINUATION_SYSTEM_PROMPT = """Sen bir Product Manager agent'ısın. Bu, YENİ bir proje DEĞİL —
mevcut bir ürünün bir SONRAKİ sprint'i. Sana ürünün önceki vizyonu ve henüz sprint'e alınmamış
(future_backlog) özellikleri verilecek. SIFIRDAN vizyon üretme — VAR OLAN vizyonu kullan.

Görevin: future_backlog'dan bu sprint'e 2-4 story seç (aynı MVP mantığı: kullanıcı için en çok
değer katacak, gerçekçi bir kapsam). CEO'nun bu sprint için ekstra bir notu/isteği varsa (verilecek),
bunu da dikkate al — gerekirse mevcut vizyona yeni 1-2 özellik ekleyebilirsin ama vizyonun
tamamını yeniden üretme.

SADECE geçerli JSON döndür, PM_agent'ın normal formatıyla aynı (product_vision, project_name,
backlog, future_backlog, pm_notes) — product_vision ve project_name'i olduğu gibi (varsa CEO
notuna göre küçük eklemelerle) geri döndür, backlog'u bu sprint için seçtiklerinle doldur,
future_backlog'u kalanlarla güncelle."""


def pm_agent_node(state: SprintState) -> SprintState:
    is_revision = (
        bool(state.get("clarification_needed"))
        and state.get("clarification_target") == "pm_agent"
    )

    is_continuation = state.get("is_continuation_sprint", False)

    if is_revision:
        user_content = (
            f"Mevcut backlog:\n{json.dumps(state['backlog'], ensure_ascii=False, indent=2)}\n\n"
            f"Gelen netleştirme talebi (Designer'dan):\n{state['clarification_needed']}"
        )
        system = REVISION_SYSTEM_PROMPT
    elif is_continuation:
        ceo_note = state.get("idea") or "(yok, sadece bekleyen backlog'dan devam et)"
        user_content = (
            f"Mevcut ürün vizyonu:\n{json.dumps(state.get('product_vision', []), ensure_ascii=False, indent=2)}\n\n"
            f"Bekleyen backlog (future_backlog):\n{json.dumps(state.get('future_backlog', []), ensure_ascii=False, indent=2)}\n\n"
            f"Proje adı: {state.get('project_name', '')}\n\n"
            f"CEO'nun bu sprint için notu (varsa): {ceo_note}"
        )
        system = CONTINUATION_SYSTEM_PROMPT
    else:
        user_content = f"CEO'nun fikri: {state['idea']}"
        system = SYSTEM_PROMPT

    parsed = call_ollama_json(
        host=OLLAMA_HOST, model=OLLAMA_MODEL, system_prompt=system,
        user_content=user_content, temperature=0.3, agent_label="pm_agent",
    )

    if "backlog" not in parsed:
        raise ValueError(f"PM Agent çıktısında 'backlog' alanı yok: {parsed}")

    state["backlog"] = parsed["backlog"]
    state["pm_notes"] = parsed.get("pm_notes", "")

    if not is_revision:
        state["product_vision"] = parsed.get("product_vision", state.get("product_vision", []))
        state["future_backlog"] = parsed.get("future_backlog", [])
        state["project_name"] = parsed.get("project_name", state.get("project_name", ""))

    if is_revision:
        state["clarification_needed"] = None  # döngü kapandı
        state["clarification_source"] = None
        state["clarification_target"] = None
        state["revision_count"] = state.get("revision_count", 0) + 1
        log_step(
            state, agent="pm_agent", action="revised",
            detail=f"Designer'ın netleştirme talebine cevaben backlog revize edildi. Not: {state['pm_notes']}",
        )
    else:
        state["revision_count"] = 0
        log_step(
            state, agent="pm_agent", action="backlog_created",
            detail=f"Vizyon: {len(state.get('product_vision', []))} özellik | "
                   f"Sprint 1 MVP: {len(state['backlog'])} story | "
                   f"Gelecek sprint'ler için bekleyen: {len(state.get('future_backlog', []))} story. "
                   f"Not: {state['pm_notes']}",
        )

    return state
