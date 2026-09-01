"""
QA Agent x3 (A, B, C) + Consensus — Reviewer'ın onayladığı kodu, backlog'daki
acceptance_criteria'ya karşı bağımsız olarak test eder.

KRİTİK TASARIM İLKESİ: 3 QA agent birbirinin sonucunu GÖRMEZ. Her biri sadece
dev_outputs + backlog'u görür, kendi bağımsız testini yazar/değerlendirir. Amaç
tek bir LLM'in halüsinasyonla "geçti" demesini önlemek — çapraz doğrulama.

Girdi: state["dev_outputs"], state["backlog"] (review'dan geçmiş task'lar)
Çıktı: state["qa_round_verdicts"] (bu turun 3 bağımsız verdicti)

Consensus kuralı: bir story SADECE 3 QA agent'ın da "pass" dediği durumda geçer.
Herhangi biri "fail" derse, o story'nin tüm task'ları tasks_to_revise'a yazılır,
Reviewer'ın "already_reviewed" filtresini atlatmak için review_results'taki ilgili
kayıtlar "rejected"a çevrilir — böylece Dev -> Reviewer -> QA döngüsü otomatik kurulur.

qa_revision_count MAX_QA_REVISIONS'ı aşarsa, sonsuz döngü olmasın diye eldeki
haliyle devam edilir (bu ciddi bir uyarı olarak loglanır — gerçek bir sprint'te
bu durumun insana escalate edilmesi gerekir, MVP'de şimdilik sadece işaretliyoruz).
"""
import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed

from graph.state import SprintState
from agents.logger import log_step
from agents.checkin_nodes import check_and_answer_question
from agents.llm_utils import call_ollama_json

OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5")
OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")

MAX_QA_REVISIONS = 2

SYSTEM_PROMPT = """Sen bağımsız çalışan bir QA agent'ısın. Sana verilen kodu (backend+frontend \
dosyaları) ve story'lerin acceptance_criteria'sını inceleyip, HER story için kodun kabul \
kriterlerini gerçekten karşılayıp karşılamadığını değerlendiriyorsun.

ZORUNLU: Sana verilen backlog'daki HER story_id için "results" listesinde TAM OLARAK bir \
kayıt olmalı — hiçbirini atlama, hiçbirini boş bırakma. Kod çok uzun/karmaşık görünse bile \
her story için en azından bir karar ver (gerekirse "fail" + "yeterince değerlendiremedim" notu).

Kurallar:
- Kodu satır satır oku, kabul kriterleriyle karşılaştır. Eksik/yarım implementasyon, kabul \
kriterini karşılamayan mantık, açık hata varsa "fail" ver ve somut bug'ları listele.
- Sadece gerçek, kanıtlanabilir sorunlarda fail ver. Kod çalışıyor ve kriterleri karşılıyorsa "pass" ver.
- "status" alanı SADECE "pass" veya "fail" olabilir — başka hiçbir değer (örn. "not_tested",
"partial", "unknown") KABUL EDİLMEZ. Test edemediğin bir story varsa bile "fail" yaz ve
bugs alanında neden test edemediğini belirt.
- Başka bir QA agent'ın görüşünü bilmiyorsun ve bilmemelisin — sadece kendi bağımsız değerlendirmeni yap.
- SADECE geçerli JSON döndür.

Format:
{
  "results": [
    {"story_id": "US-1", "status": "pass", "bugs": []},
    {"story_id": "US-2", "status": "fail", "bugs": ["Not silme endpoint'i onay kontrolü yapmıyor, doğrudan siliyor."]}
  ]
}
"""


def _call_qa(state: SprintState, qa_agent_name: str) -> dict:
    """LLM çağrısını yapar, normalize edilmiş verdict döndürür. STATE'E YAZMAZ —
    thread-safe olması için (paralel çalıştırılacağından state mutasyonu ana thread'de yapılır).

    Gerçek çalıştırmalarda QA'nın bazı story'leri hiç değerlendirmeden boş "results" döndürdüğü
    görüldü — muhtemelen büyük kod bağlamı küçük local modeli zorluyor. Bunu önlemek için:
    her seferinde hangi story_id'lerin ZORUNLU olduğu ayrıca belirtiliyor, ve kapsam eksikse
    (backlog'daki tüm story'ler için sonuç gelmediyse) BİR KEZ daha, daha kısa/net bir
    hatırlatmayla deneniyor.
    """
    expected_story_ids = [s["id"] for s in state["backlog"]]
    context = json.dumps(
        {"backlog": state["backlog"], "dev_outputs": state.get("dev_outputs", [])},
        ensure_ascii=False, indent=2,
    )

    def _attempt(extra_note: str = "") -> dict:
        user_content = (
            f"DEĞERLENDİRMEN GEREKEN story_id'ler (HEPSİ için sonuç üret): {expected_story_ids}\n\n"
            f"Test edilecek kod ve kriterler:\n{context}{extra_note}"
        )
        return call_ollama_json(
            host=OLLAMA_HOST, model=OLLAMA_MODEL, system_prompt=SYSTEM_PROMPT,
            user_content=user_content, temperature=0.4, agent_label=qa_agent_name,
        )

    parsed = _attempt()
    covered_ids = {r.get("story_id") for r in parsed.get("results", [])}

    if expected_story_ids and set(expected_story_ids) - covered_ids:
        missing = list(set(expected_story_ids) - covered_ids)
        parsed = _attempt(
            f"\n\nÖNCEKİ CEVABINDA ŞUNLAR EKSİKTİ: {missing}. Bu sefer TÜM story_id'ler için "
            f"(eksik kalanlar dahil) sonuç üret, hiçbirini atlama."
        )

    raw_results = parsed.get("results", [])
    normalized_results = []
    invalid_status_notes = []

    for r in raw_results:
        status = r.get("status")
        if status not in ("pass", "fail"):
            invalid_status_notes.append(
                f"{r.get('story_id', '?')}: model '{status}' döndürdü, şema dışı olduğu için 'fail' sayıldı."
            )
            status = "fail"
        normalized_results.append({
            "story_id": r.get("story_id"), "status": status, "bugs": r.get("bugs", []),
        })

    return {
        "qa_agent": qa_agent_name, "results": normalized_results,
        "invalid_status_notes": invalid_status_notes,
    }


def qa_parallel_node(state: SprintState) -> SprintState:
    check_and_answer_question(state)
    """3 QA agent'ı GERÇEKTEN paralel (thread havuzuyla) çalıştırır — birbirinin
    sonucunu görmeden, aynı anda. Sıralı çağrının aksine bu gerçek bir hızlanma sağlar
    ve bağımsızlık ilkesini pekiştirir (hiçbiri diğerinin sonucunu bekleyip etkilenemez).
    """
    state["qa_round_verdicts"] = []
    qa_agent_names = ["qa_agent_a", "qa_agent_b", "qa_agent_c"]

    log_step(state, agent="qa_parallel", action="qa_wave_started",
              detail=f"{len(qa_agent_names)} bağımsız QA agent paralel test ediyor.")

    with ThreadPoolExecutor(max_workers=len(qa_agent_names)) as executor:
        futures = {executor.submit(_call_qa, state, name): name for name in qa_agent_names}
        for future in as_completed(futures):
            name = futures[future]
            verdict = future.result()

            for note in verdict.pop("invalid_status_notes"):
                log_step(state, agent=name, action="invalid_status_normalized", detail=note)

            state["qa_round_verdicts"].append(verdict)
            summary = ", ".join(f"{r['story_id']}:{r['status']}" for r in verdict["results"])
            log_step(state, agent=name, action="tested", detail=summary or "(sonuç yok)")

    return state


def qa_consensus_node(state: SprintState) -> SprintState:
    verdicts = state.get("qa_round_verdicts", [])

    # story_id -> [status, status, status] (3 QA'nın verdiği sıra)
    per_story_statuses: dict[str, list[str]] = {}
    per_story_bugs: dict[str, list[str]] = {}

    for v in verdicts:
        for r in v["results"]:
            sid = r["story_id"]
            per_story_statuses.setdefault(sid, []).append(r["status"])
            if r["status"] == "fail":
                per_story_bugs.setdefault(sid, []).extend(r.get("bugs", []))

    # KRİTİK: referans QA'nın SÖYLEDİKLERİ değil, backlog'un KENDİSİ olmalı. Bir QA agent
    # hiç cevap vermezse (boş "results" dönerse — gerçek bir çalıştırmada gördük), o story
    # per_story_statuses'ta hiç görünmez ve eskiden sessizce "geçti" sayılıyordu. Artık
    # backlog'daki HER story için 3 "pass" verdiği doğrulanmadıkça "fail" sayılıyor —
    # QA'nın sessiz kalması da bir tür başarısızlık.
    all_story_ids = [s["id"] for s in state.get("backlog", [])]
    failed_stories = [
        sid for sid in all_story_ids
        if not (len(per_story_statuses.get(sid, [])) == 3 and all(s == "pass" for s in per_story_statuses.get(sid, [])))
    ]

    if not verdicts or all(len(v["results"]) == 0 for v in verdicts):
        log_step(state, agent="qa_consensus", action="qa_produced_no_results",
                  detail="Hiçbir QA agent gerçek bir sonuç üretmedi (hepsi boş döndü) — "
                         "bu bir test değil, testin GERÇEKLEŞMEMESİ demek. Backlog'daki tüm "
                         "story'ler bu yüzden fail sayıldı.")

    # Kalıcı log
    if state.get("qa_results") is None:
        state["qa_results"] = []
    state["qa_results"].append({
        "round": state.get("qa_revision_count", 0) + 1,
        "per_story_statuses": per_story_statuses,
        "failed_stories": failed_stories,
    })

    qa_revision_count = state.get("qa_revision_count", 0)

    if failed_stories and qa_revision_count < MAX_QA_REVISIONS:
        # Başarısız story'lerin tüm task'larını revizyona al
        failed_task_ids = [
            t["id"] for t in state.get("tasks", []) if t["story_id"] in failed_stories
        ]
        state["tasks_to_revise"] = failed_task_ids

        # Reviewer'ın bu task'ları tekrar incelemesi için review_results'ı "rejected"a çevir
        review_by_id = {r["task_id"]: r for r in (state.get("review_results") or [])}
        for tid in failed_task_ids:
            story_id = next(t["story_id"] for t in state["tasks"] if t["id"] == tid)
            bugs = per_story_bugs.get(story_id, [])
            review_by_id[tid] = {
                "task_id": tid, "status": "rejected",
                "feedback": "QA consensus'ta başarısız: " + "; ".join(bugs) if bugs else "QA testi geçemedi.",
            }
        state["review_results"] = list(review_by_id.values())
        state["qa_revision_count"] = qa_revision_count + 1

        log_step(
            state, agent="qa_consensus", action="consensus_failed",
            detail=f"3/3 uyum sağlanamadı: {failed_stories}. Task'lar dev'e geri gönderildi: {failed_task_ids}.",
            target_agent="dev_agent",
        )
        return state

    if failed_stories and qa_revision_count >= MAX_QA_REVISIONS:
        failed_task_ids = [
            t["id"] for t in state.get("tasks", []) if t["story_id"] in failed_stories
        ]
        state["tasks_to_revise"] = failed_task_ids  # escalation_checkin "retry" derse kullanılacak

        review_by_id = {r["task_id"]: r for r in (state.get("review_results") or [])}
        for tid in failed_task_ids:
            story_id = next(t["story_id"] for t in state["tasks"] if t["id"] == tid)
            bugs = per_story_bugs.get(story_id, [])
            review_by_id[tid] = {
                "task_id": tid, "status": "rejected",
                "feedback": "QA consensus'ta tekrar başarısız: " + "; ".join(bugs) if bugs else "QA testi tekrar geçemedi.",
            }
        state["review_results"] = list(review_by_id.values())

        state["escalation_needed"] = {
            "source": "qa",
            "detail": f"QA revizyon limiti ({MAX_QA_REVISIONS}) aşıldı. 3/3 uyum sağlanamayan "
                     f"story'ler: {failed_stories}. Durumlar: {per_story_statuses}",
        }
        log_step(
            state, agent="qa_consensus", action="qa_limit_reached",
            detail=f"QA revizyon limiti aşıldı, CEO'ya escalate ediliyor: {failed_stories}",
        )
        return state

    state["tasks_to_revise"] = []
    log_step(
        state, agent="qa_consensus", action="consensus_passed",
        detail=f"Tüm story'lerde 3/3 tam uyum sağlandı: {list(per_story_statuses.keys())}.",
    )
    return state
