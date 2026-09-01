"""
Checkin Node'ları — CEO'nun (Emine) sprint sürecine müdahale edebileceği noktalar.

VARSAYILAN DAVRANIŞ: Ekip tamamen otonom akar — her checkin noktasına uğrar ama sağduyulu
bir varsayılan kararla ANINDA (duraksamadan) geçer. Process'i yeniden başlatmana GEREK YOK.

MÜDAHALE ETMEK İSTEDİĞİNDE: sprint hâlâ çalışırken, BAŞKA BİR TERMİNALDEN şunu çalıştır:
    touch .intervene
Bu bir "bayrak dosyası" bırakır. Çalışmakta olan process, BİR SONRAKİ checkin noktasına
geldiğinde (planning/escalation/retro) bu dosyayı görür, siler, ve gerçekten durup senin
terminal girdini bekler. Yani anlık değil ama "bir sonraki uğrak noktasında" — süreci
komple baştan başlatman gerekmez, aynı çalışan process kendi kendine fark eder.

Slack entegrasyonu geldiğinde bu dosya mekanizması kalkacak, yerine "Slack'te bir mesaj
geldi mi" kontrolü gelecek — ama checkin node'larının kendisi ve routing değişmeyecek.

Varsayılan kararlar (müdahale bayrağı yoksa):
  - planning_checkin: PM'in seçtiği MVP kapsamı olduğu gibi onaylanır
  - escalation_checkin: "continue" (olduğu haliyle devam et, ne olduğu yine de loglanır)
  - retro_checkin: not eklenmez, boş geçilir
"""
import os
from langgraph.types import interrupt

from graph.state import SprintState
from agents.logger import log_step

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _current_thread_id() -> str:
    """Her process kendi THREAD_ID env değişkeniyle başlatılır (birden fazla proje aynı anda
    çalışırken her biri farklı bir terminalde farklı THREAD_ID ile açılır) — bu sayede flag
    dosyaları projeler arasında ÇAKIŞMAZ, her process sadece kendi bayrağına bakar."""
    return os.environ.get("THREAD_ID", "sprint-demo-1")


def _intervene_flag_path() -> str:
    return os.path.join(_REPO_ROOT, f".intervene_{_current_thread_id()}")


def _question_flag_path() -> str:
    return os.path.join(_REPO_ROOT, f".question_{_current_thread_id()}")


def _intervene_requested() -> bool:
    """Bayrak dosyası varsa True döner VE dosyayı siler (tek seferlik tetik — bir sonraki
    checkin'de tekrar durmasın diye)."""
    path = _intervene_flag_path()
    if os.path.exists(path):
        os.remove(path)
        return True
    return False


# --- Serbest soru-cevap kanalı ---
# .question_<thread_id> farklı: o "dur, seçenekler sun, cevabımı bekle" demek değil —
# "durma, ama şu soruyu/öneriyi PM'e ilet, cevabı görmek istiyorum" demek. NON-BLOCKING:
# pipeline durmaz, PM hızlıca cevap üretir, konsola basılır + kalıcı loglanır, akış
# kesintisiz devam eder.


def check_and_answer_question(state: SprintState) -> None:
    """Her büyük agent adımının başında çağrılır. .question_<thread_id> dosyası varsa
    içeriğini okur, PM'e sorup cevabı hemen konsola basar + loglar. STATE'İ DÖNMEZ,
    in-place günceller, pipeline'ı DURDURMAZ — normal akışa hiç dokunmadan devam eder."""
    path = _question_flag_path()
    if not os.path.exists(path):
        return

    with open(path, encoding="utf-8") as f:
        question = f.read().strip()
    os.remove(path)

    if not question:
        return

    # Döngüsel import'tan kaçınmak için burada import ediyoruz
    from agents.llm_utils import call_ollama_json
    import json as _json

    context = _json.dumps({
        "backlog": state.get("backlog", []),
        "product_vision": state.get("product_vision", []),
        "future_backlog": state.get("future_backlog", []),
        "tech_stack_decision": state.get("tech_stack_decision"),
        "tasks": state.get("tasks", []),
    }, ensure_ascii=False, indent=2)

    system_prompt = """Sen PM/Tech Lead rolünü üstlenen bir agent'sın. CEO şu an devam eden \
sprint hakkında bir soru soruyor ya da yeni bir fikir/özellik öneriyor. Mevcut sprint bağlamına \
(backlog, vizyon, tech stack) bakarak KISA, net bir cevap ver. Eğer önerdiği şey yeni bir özellikse, \
bunu "future_backlog'a eklenebilir" diye not et (kendi eklemene gerek yok, sadece öner). \
SADECE geçerli JSON döndür: {"answer": "kısa cevabın"}"""

    try:
        result = call_ollama_json(
            host=os.environ.get("OLLAMA_HOST", "http://localhost:11434"),
            model=os.environ.get("OLLAMA_MODEL", "qwen2.5"),
            system_prompt=system_prompt,
            user_content=f"Sprint bağlamı:\n{context}\n\nCEO'nun sorusu/önerisi:\n{question}",
            temperature=0.4, agent_label="pm_qa",
        )
        answer = result.get("answer", "(cevap üretilemedi)")
    except Exception as e:
        answer = f"(Cevap üretirken hata oluştu: {e})"

    print("\n" + "?" * 60)
    print(f"  💬 SORUN: {question}")
    print(f"  🤖 PM'İN CEVABI: {answer}")
    print("?" * 60 + "\n")

    log_step(state, agent="pm_qa", action="ceo_question_answered",
              detail=f"S: {question} | C: {answer}")


def planning_checkin_node(state: SprintState) -> SprintState:
    if not _intervene_requested():
        log_step(state, agent="ceo", action="planning_auto_approved",
                  detail=f"Müdahale bayrağı yok, PM'in MVP seçimi otomatik onaylandı: "
                         f"{len(state.get('backlog', []))} story.")
        return state

    payload = {
        "type": "planning_approval",
        "message": "PM'in ürettiği vizyon ve MVP seçimini incele. Onaylıyor musun?",
        "product_vision": state.get("product_vision", []),
        "mvp_backlog": state.get("backlog", []),
        "future_backlog": state.get("future_backlog", []),
        "pm_notes": state.get("pm_notes", ""),
    }

    answer = interrupt(payload)
    # answer beklenen format: {"action": "approve"} veya
    # {"action": "move_to_mvp", "story_id": "US-5"} veya {"action": "remove_from_mvp", "story_id": "US-2"}
    # Birden fazla aksiyon art arda gönderilebilir (liste olarak).

    actions = answer if isinstance(answer, list) else [answer]
    for action in actions:
        if not action or action.get("action") == "approve":
            continue

        if action.get("action") == "move_to_mvp":
            sid = action["story_id"]
            future = state.get("future_backlog", []) or []
            moved = [s for s in future if s["id"] == sid]
            if moved:
                state["future_backlog"] = [s for s in future if s["id"] != sid]
                state["backlog"] = state.get("backlog", []) + moved
                log_step(state, agent="ceo", action="moved_to_mvp",
                          detail=f"{sid} vizyon backlog'undan bu sprint'e taşındı.")

        elif action.get("action") == "remove_from_mvp":
            sid = action["story_id"]
            backlog = state.get("backlog", []) or []
            removed = [s for s in backlog if s["id"] == sid]
            if removed:
                state["backlog"] = [s for s in backlog if s["id"] != sid]
                state["future_backlog"] = state.get("future_backlog", []) + removed
                log_step(state, agent="ceo", action="removed_from_mvp",
                          detail=f"{sid} bu sprint'ten çıkarılıp vizyon backlog'una geri kondu.")

    log_step(state, agent="ceo", action="planning_approved",
              detail=f"Sprint 1 kapsamı onaylandı: {len(state.get('backlog', []))} story.")
    return state


def escalation_checkin_node(state: SprintState) -> SprintState:
    escalation = state.get("escalation_needed", {})

    if not _intervene_requested():
        state["escalation_resolution"] = "continue"
        state["escalation_needed"] = None
        state["tasks_to_revise"] = []
        log_step(state, agent="ceo", action="escalation_auto_continue",
                  detail=f"Müdahale bayrağı yok, ekip kendi başına çözemedi ama otomatik "
                         f"devam edildi ({escalation.get('source')}). Detay: {escalation.get('detail')}")
        return state

    payload = {
        "type": "escalation",
        "message": f"Ekip kendi başına çözemedi ({escalation.get('source')}), senin kararın gerekiyor.",
        "detail": escalation.get("detail", ""),
        "options": ["retry (bir tur daha dene, senin ek notunla)", "continue (olduğu gibi devam et)"],
    }

    answer = interrupt(payload)
    # answer beklenen format: {"decision": "retry", "guidance": "..."} veya {"decision": "continue"}

    decision = (answer or {}).get("decision", "continue")
    guidance = (answer or {}).get("guidance", "")

    if decision == "retry":
        state["escalation_resolution"] = "retry"
        # Sayaçları sıfırlayıp bir tur daha hak tanı, CEO'nun notunu feedback'e ekle
        source = escalation.get("source")
        if source == "reviewer":
            state["review_revision_count"] = 0
        elif source == "qa":
            state["qa_revision_count"] = 0
        if guidance:
            for r in state.get("review_results", []) or []:
                if r["task_id"] in (state.get("tasks_to_revise") or []):
                    r["feedback"] += f"\n\nCEO notu: {guidance}"
        log_step(state, agent="ceo", action="escalation_retry",
                  detail=f"CEO bir tur daha hak tanıdı. Not: {guidance or '(yok)'}")
    else:
        state["escalation_resolution"] = "continue"
        log_step(state, agent="ceo", action="escalation_continue",
                  detail="CEO mevcut haliyle devam edilmesini onayladı.")

    state["escalation_needed"] = None
    return state


def retro_checkin_node(state: SprintState) -> SprintState:
    if not _intervene_requested():
        state["retro_notes"] = None
        return state

    payload = {
        "type": "retro",
        "message": "Sprint tamamlandı. Eklemek istediğin bir not/geri bildirim var mı? "
                   "(boş geçebilirsin, bir sonraki sprint planlamasında dikkate alınır)",
        "summary": {
            "backlog": [s["title"] for s in state.get("backlog", [])],
            "deploy_status": state.get("deploy_status"),
            "test_environment_url": state.get("test_environment_url"),
        },
    }

    answer = interrupt(payload)
    note = (answer or {}).get("note", "") if isinstance(answer, dict) else (answer or "")

    if note:
        state["retro_notes"] = note
        log_step(state, agent="ceo", action="retro_note_added", detail=note)
    else:
        state["retro_notes"] = None

    return state
