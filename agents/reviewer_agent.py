"""
Reviewer Agent — backend + frontend Dev'lerin ürettiği tüm kodu (dev_outputs) inceler,
her task için onay/red kararı verir.

Girdi: state["dev_outputs"] (tüm task'lar, backend+frontend)
Çıktı: state["review_results"], state["tasks_to_revise"]
Karar yetkisi: kod kalite/standart kontrolü (okunabilirlik, eksik/yarım implementasyon,
               açık mantık hatası, task açıklamasıyla uyumsuzluk).
Escalate: güvenlik şüphesi varsa feedback'te açıkça belirtir (Security Agent henüz
          eklenmedi — bu bilgi şimdilik review_results.feedback içinde tutulur).

Döngü: reddedilen task'lar tasks_to_revise'a yazılır, graph ilgili dev agent'a (domain'e
göre) geri döner. review_revision_count MAX_REVIEW_REVISIONS'ı aşarsa, kalan red'ler
"kabul edilmiş sayılır ama uyarı" ile not düşülüp akış devam eder — sonsuz döngü olmaz.
"""
import json
import os

from graph.state import SprintState
from agents.logger import log_step
from agents.checkin_nodes import check_and_answer_question
from agents.llm_utils import call_ollama_json

OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5")
OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")

MAX_REVIEW_REVISIONS = 2

SYSTEM_PROMPT = """Sen deneyimli bir Code Reviewer agent'ısın. Dev agent'ların ürettiği kodu \
(backend + frontend, task açıklamalarıyla birlikte) inceliyorsun.

Her task için:
- Kod, task açıklamasını gerçekten karşılıyor mu? Task açıklamasında birden fazla endpoint/işlem \
geçiyorsa (örn. "ekleme ve silme"), HEPSİ kodda var mı — yoksa sadece bir kısmı mı yazılmış? \
Eksik endpoint varsa bunu SOMUT olarak belirt ve reddet.
- Eksik/yarım implementasyon var mı (TODO, placeholder, çalışmayan mantık)?
- Açık güvenlik sorunu var mı (örn. SQL injection'a açık string birleştirme, hardcoded secret)?
- Okunabilir ve makul mü (aşırı mühendislik değil, ama iş görür mü)?

Sadece gerçek, somut sorunlarda reddet. Küçük stil tercihleri için reddetme.

SADECE geçerli JSON döndür:
{
  "review_results": [
    {"task_id": "T-1", "status": "approved", "feedback": "..."},
    {"task_id": "T-2", "status": "rejected", "feedback": "Neden reddedildiği, ne düzeltilmeli"}
  ]
}
"""


def reviewer_agent_node(state: SprintState) -> SprintState:
    check_and_answer_question(state)

    # Sadece henüz review edilmemiş ya da revize edilmiş task'ları gözden geçir
    already_reviewed = {
        r["task_id"] for r in (state.get("review_results") or []) if r["status"] == "approved"
    }
    tasks_to_review = [
        o for o in state.get("dev_outputs", [])
        if o["task_id"] not in already_reviewed
    ]

    if not tasks_to_review:
        log_step(state, agent="reviewer_agent", action="nothing_to_review",
                  detail="Tüm task'lar zaten onaylı.")
        state["tasks_to_revise"] = []
        return state

    context = json.dumps(tasks_to_review, ensure_ascii=False, indent=2)

    parsed = call_ollama_json(
        host=OLLAMA_HOST, model=OLLAMA_MODEL, system_prompt=SYSTEM_PROMPT,
        user_content=f"İncelenecek task çıktıları:\n{context}", temperature=0.2,
        agent_label="reviewer_agent",
    )

    new_results = parsed.get("review_results", [])

    # Önceki review_results ile birleştir (approved olanları koru, yeni sonuçları ekle/güncelle)
    prior_results = {r["task_id"]: r for r in (state.get("review_results") or [])}
    for r in new_results:
        prior_results[r["task_id"]] = r
    state["review_results"] = list(prior_results.values())

    rejected = [r["task_id"] for r in new_results if r["status"] == "rejected"]
    revision_count = state.get("review_revision_count", 0)

    if rejected and revision_count < MAX_REVIEW_REVISIONS:
        state["tasks_to_revise"] = rejected
        state["review_revision_count"] = revision_count + 1
        for r in new_results:
            if r["status"] == "rejected":
                log_step(state, agent="reviewer_agent", action="rejected",
                          detail=f"{r['task_id']}: {r['feedback']}", target_agent="dev_agent")
            else:
                log_step(state, agent="reviewer_agent", action="approved",
                          detail=f"{r['task_id']}: {r['feedback']}")
        return state

    if rejected and revision_count >= MAX_REVIEW_REVISIONS:
        rejected_feedback = "; ".join(
            f"{r['task_id']}: {r['feedback']}" for r in new_results if r["status"] == "rejected"
        )
        state["tasks_to_revise"] = rejected  # escalation_checkin "retry" derse kullanılacak
        state["escalation_needed"] = {
            "source": "reviewer",
            "detail": f"Revizyon limiti ({MAX_REVIEW_REVISIONS}) aşıldı. Hâlâ reddedilen task'lar: "
                     f"{rejected}. Detaylar: {rejected_feedback}",
        }
        log_step(state, agent="reviewer_agent", action="review_limit_reached",
                  detail=f"Revizyon limiti aşıldı, CEO'ya escalate ediliyor: {rejected}")
        return state

    state["tasks_to_revise"] = []
    for r in new_results:
        if r["status"] == "approved":
            log_step(state, agent="reviewer_agent", action="approved", detail=f"{r['task_id']}: {r['feedback']}")

    return state
