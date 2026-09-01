"""
Dev Agent'lar — Tech Lead'in ürettiği task'ları domain'ine göre işler.

İki ayrı agent, ortak bir çekirdek fonksiyonu paylaşır:
  - backend_dev_agent_node  -> sadece domain == "backend" task'ları işler
  - frontend_dev_agent_node -> sadece domain == "frontend" task'ları işler

İkisi de aynı state["dev_outputs"] listesine yazar, kendi ürettiği task'ları ekler.
Bağımlılık sırası (depends_on) her iki agent için de aynı topolojik sıralamayla korunur.

Neden ayrı: gerçek bir takımda backend/frontend dev'ler paralel çalışabilir, farklı
uzmanlık/prompt gerektirir (biri API/veri modeline, diğeri UI/etkileşime odaklanır).
Bu ayrım aynı zamanda ileride squad ölçeklenmesinde (birden fazla backend/frontend dev)
doğal bir genişleme noktası oluşturur.
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
MAX_PARALLEL_WORKERS = int(os.environ.get("MAX_PARALLEL_DEV_WORKERS", "3"))

BACKEND_SYSTEM_PROMPT = """Sen deneyimli bir Backend Dev agent'ısın. Tech Lead'in verdiği tek bir \
backend task'ı uygulayacaksın: API endpoint'leri, veri modeli, iş mantığı.

Task açıklamasında Tech Lead'in seçtiği stack (Flask, FastAPI veya Express) belirtilecek — TAM \
OLARAK o stack'i kullan, başka bir şey seçme. Basit, çalışan, gereksiz bağımlılığı olmayan kod yaz.

Kurallar:
- Sadece task'ta belirtilen dosyaları üret.
- Task açıklamasında birden fazla endpoint/işlem geçiyorsa (örn. "ekleme ve silme" ya da "GET, POST, \
DELETE"), TÜMÜNÜ implemente et — sadece birini yazıp diğerlerini atlama. Yazmadan önce task \
açıklamasını tekrar oku, kaç ayrı işlem/endpoint istendiğini say, hepsinin kodda karşılığı olduğunu doğrula.
- Kod çalışır ve eksiksiz olmalı (yarım fonksiyon, TODO yorumu bırakma).
- KRİTİK: Task'ın "is_entrypoint" alanına bak. TRUE ise dosyanın SONUNDA çalıştırılabilir bir \
başlatma bloğu bulunsun:
  Flask (.py) ise: if __name__ == "__main__": app.run(debug=True)
  FastAPI (.py) ise: if __name__ == "__main__": import uvicorn; uvicorn.run(app, host="127.0.0.1", port=8000)
  Express (.js) ise: app.listen(3000, () => console.log("running"));
  (Port numaraları deploy sırasında otomatik ayarlanacak, sen sadece bloğun VAR olmasını sağla.)
  is_entrypoint FALSE ise (bu dosya bir route modülü/blueprint) BU BAŞLATMA BLOĞUNU KESİNLİKLE \
EKLEME — sadece route/endpoint tanımları yaz, uygulamayı başlatan hiçbir kod (app.run, \
uvicorn.run, app.listen) bu dosyada OLMAMALI. Ana dosya (başka bir task) bunu senden import edecek.
- KRİTİK: Stack Flask veya FastAPI (Python) ise "package.json" ASLA üretme — bu bir Node.js \
dosyasıdır, Python projede hiçbir anlamı yoktur ve deploy'u bozar. SADECE Express (Node.js) \
seçildiyse ve task açıklaması bunu açıkça istiyorsa package.json üret.
- Frontend'in tüketeceği API kontratını (endpoint, method, request/response şekli) dev_notes'ta net belirt.
- Task açıklamasında "frontend'in ana HTML dosyasını servis et" gibi bir talimat varsa, GET "/" \
route'unu MUTLAKA ekle (Flask: `render_template("index.html")`, dosyanın `templates/` altında \
olduğundan emin ol; FastAPI: `FileResponse` veya `HTMLResponse` ile; Express: `res.sendFile(...)`).
- SADECE geçerli JSON döndür.

Format:
{"files": {"dosya_adı.py veya .js": "...tam dosya içeriği...", "package.json": "...(sadece Express ise)..."}, "dev_notes": "API kontratı + kısa not"}
"""

FRONTEND_SYSTEM_PROMPT = """Sen deneyimli bir Frontend Dev agent'ısın. Tech Lead'in verdiği tek bir \
frontend task'ı uygulayacaksın: sayfa/form/UI bileşeni. Task açıklamasında Tech Lead'in seçtiği \
frontend yaklaşımı (HTML+Tailwind veya React CDN) belirtilecek — TAM OLARAK onu kullan. Designer'ın \
UX akışına ve (varsa) backend'in API kontratına uy.

EĞER "HTML+Tailwind" seçildiyse:
- Tek bir HTML dosyası, <head> içinde <script src="https://cdn.tailwindcss.com"></script> ekle.
- Tailwind utility class'larıyla modern, temiz, profesyonel görünümlü bir arayüz yap (düz/stilsiz
  HTML DEĞİL — gerçek bir spacing/renk/tipografi düzeni kullan: örn. `class="max-w-2xl mx-auto p-6"`,
  butonlar için `class="bg-blue-600 text-white px-4 py-2 rounded hover:bg-blue-700"` gibi).
- Vanilla JS (fetch API ile backend'e istek) kullan, framework yok.

EĞER "React (CDN)" seçildiyse:
- Tek bir HTML dosyası, <head>'de şu üç CDN script'i: react, react-dom, ve @babel/standalone.
- Body'de <div id="root"></div> ve <script type="text/babel"> içinde fonksiyonel React
  component'leri (useState/useEffect ile) yaz, ReactDOM.createRoot(...).render(...) ile mount et.
- Tailwind'i de aynı HTML'e ekleyip class'larla stillendirebilirsin (React + Tailwind CDN birlikte
  sorunsuz çalışır).
- fetch API ile backend'e istek at (API kontratına uy).

Kurallar:
- Sadece task'ta belirtilen dosyaları üret.
- Kod çalışır ve eksiksiz olmalı.
- Backend task'a bağımlıysan (depends_on), o task'ın dev_notes'undaki API kontratını kullan —
  sana ayrıca verilecek.
- SADECE geçerli JSON döndür.

Format:
{"files": {"templates/index.html": "...tam dosya içeriği..."}, "dev_notes": "kısa not"}
"""


def _topological_order(tasks: list[dict]) -> list[dict]:
    ordered, done_ids, remaining = [], set(), list(tasks)
    while remaining:
        progressed = False
        for task in list(remaining):
            if all(dep in done_ids for dep in task.get("depends_on", [])):
                ordered.append(task)
                done_ids.add(task["id"])
                remaining.remove(task)
                progressed = True
        if not progressed:
            ordered.extend(remaining)
            break
    return ordered


def _process_single_task(task, state, domain, system_prompt, agent_name, outputs_by_task_id, tasks_to_revise, review_feedback_by_id):
    """Tek bir task'ı işler, LLM çağrısı yapar. Thread-safe: shared state'e DOKUNMAZ,
    sonucu döndürür — state'e yazma işini ana thread yapar (race condition önlemek için).
    """
    needs_revision = task["id"] in tasks_to_revise

    dep_context = ""
    for dep_id in task.get("depends_on", []):
        dep_output = outputs_by_task_id.get(dep_id)
        if dep_output:
            dep_context += f"\nBağımlı olduğun task ({dep_id}) notu: {dep_output['dev_notes'][:400]}"

    feedback_context = ""
    if needs_revision:
        feedback_context = (
            f"\n\nBu task DAHA ÖNCE reddedildi, Reviewer'ın geri bildirimi:\n"
            f"{review_feedback_by_id.get(task['id'], '(feedback yok)')[:400]}\n"
            f"Bu sorunu düzelterek YENİDEN üret."
        )

    user_content = f"Task:\n{json.dumps(task, ensure_ascii=False, indent=2)}{dep_context}{feedback_context}"

    parsed = call_ollama_json(
        host=OLLAMA_HOST, model=OLLAMA_MODEL, system_prompt=system_prompt,
        user_content=user_content, temperature=0.2, agent_label=agent_name,
    )

    return {
        "task_id": task["id"], "domain": domain,
        "files": parsed.get("files", {}), "dev_notes": parsed.get("dev_notes", ""),
    }, needs_revision


def _run_dev_tasks(state: SprintState, domain: str, system_prompt: str, agent_name: str) -> SprintState:
    check_and_answer_question(state)
    all_tasks = _topological_order(state.get("tasks", []))
    domain_tasks = [t for t in all_tasks if t.get("domain") == domain]

    if state.get("dev_outputs") is None:
        state["dev_outputs"] = []

    outputs_by_task_id = {o["task_id"]: o for o in state["dev_outputs"]}
    tasks_to_revise = set(state.get("tasks_to_revise") or [])
    review_feedback_by_id = {
        r["task_id"]: r["feedback"] for r in (state.get("review_results") or [])
        if r["task_id"] in tasks_to_revise
    }

    pending = {
        t["id"]: t for t in domain_tasks
        if not (t["id"] in outputs_by_task_id and t["id"] not in tasks_to_revise)
    }

    # BAĞIMLILIK SEVİYELERİNE GÖRE DALGA DALGA PARALEL İŞLEME:
    # Her dalgada, tüm bağımlılıkları zaten tamamlanmış (outputs_by_task_id'de olan)
    # task'lar aynı anda (thread havuzuyla) LLM'e gönderilir. LLM çağrıları I/O-bound
    # olduğu için bu gerçek bir hızlanma sağlar — sıralı değil paralel çalışırlar.
    while pending:
        runnable = [
            t for t in pending.values()
            if all(dep in outputs_by_task_id for dep in t.get("depends_on", []))
        ]
        if not runnable:
            # Döngüsel/eksik bağımlılık — kalanları sırayla işle, sistemi kilitleme.
            runnable = list(pending.values())

        if len(runnable) > 1:
            log_step(state, agent=agent_name, action="parallel_wave_started",
                      detail=f"{len(runnable)} bağımsız task paralel işleniyor: {[t['id'] for t in runnable]}")

        with ThreadPoolExecutor(max_workers=min(len(runnable), MAX_PARALLEL_WORKERS)) as executor:
            futures = {
                executor.submit(
                    _process_single_task, task, state, domain, system_prompt, agent_name,
                    outputs_by_task_id, tasks_to_revise, review_feedback_by_id,
                ): task
                for task in runnable
            }
            for future in as_completed(futures):
                task = futures[future]
                output, needs_revision = future.result()

                # Sadece burada, tek thread'de (ana thread) state'e yazıyoruz.
                if output["task_id"] in outputs_by_task_id:
                    state["dev_outputs"] = [o for o in state["dev_outputs"] if o["task_id"] != output["task_id"]]
                state["dev_outputs"].append(output)
                outputs_by_task_id[output["task_id"]] = output

                action = "task_revised" if needs_revision else "task_implemented"
                log_step(
                    state, agent=agent_name, action=action,
                    detail=f"{task['id']}: {list(output['files'].keys())} üretildi. {output['dev_notes'][:200]}",
                )
                del pending[task["id"]]

    return state


def backend_dev_agent_node(state: SprintState) -> SprintState:
    return _run_dev_tasks(state, domain="backend", system_prompt=BACKEND_SYSTEM_PROMPT, agent_name="backend_dev_agent")


def frontend_dev_agent_node(state: SprintState) -> SprintState:
    return _run_dev_tasks(state, domain="frontend", system_prompt=FRONTEND_SYSTEM_PROMPT, agent_name="frontend_dev_agent")
