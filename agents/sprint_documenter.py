"""
Sprint Documenter — her sprint sonunda proje klasöründe (deploy_output/<proje>/docs/)
düzenli, kalıcı belgeler oluşturur/günceller. Sohbet log'unun aksine (conversation_log,
sadece bellekte/o çalıştırmada var) bunlar dosya sisteminde kalıcı — bir sonraki sprint'te
de proje geçmişini okuyabilirsin.

Üretilen belgeler:
  docs/product-vision.md   — PM'in ürettiği geniş özellik vizyonu (ilk sprint'te bir kez yazılır)
  docs/sprint-log.md        — HER sprint sonunda APPEND edilir: ne yapıldı, kalite süreci, deploy
  docs/backlog.md           — güncel backlog + gelecek sprint'ler için bekleyen özellikler
  docs/tech-decisions.md    — Tech Lead'in stack kararları ve gerekçeleri (append)

Bu modül LLM çağırmaz — sadece state'teki bilgiyi düzenli markdown'a döker.
"""
import json
import os
from datetime import datetime, timezone

from graph.state import SprintState

DEPLOY_ROOT = os.environ.get(
    "DEPLOY_DIR",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "deploy_output"),
)


def _docs_dir(project_slug: str) -> str:
    docs_dir = os.path.join(DEPLOY_ROOT, project_slug, "docs")
    os.makedirs(docs_dir, exist_ok=True)
    return docs_dir


def _write_product_vision(docs_dir: str, state: SprintState) -> None:
    path = os.path.join(docs_dir, "product-vision.md")
    if os.path.exists(path):
        return  # sadece ilk sprint'te yazılır, sonradan üzerine yazılmaz

    vision = state.get("product_vision", []) or []
    lines = [f"# Ürün Vizyonu\n", f"**Orijinal fikir:** {state.get('idea', '')}\n", ""]
    lines.append("## Geniş özellik listesi (PM'in vizyonu)\n")
    for item in vision:
        lines.append(f"- **{item.get('feature', '')}** — {item.get('why', '')}")

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def _write_sprint_log_entry(docs_dir: str, state: SprintState, sprint_number: int) -> None:
    path = os.path.join(docs_dir, "sprint-log.md")
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    backlog = state.get("backlog", [])
    stack = state.get("tech_stack_decision")
    review_revisions = state.get("review_revision_count", 0)
    qa_rounds = len(state.get("qa_results", []) or [])
    deploy_status = state.get("deploy_status")
    test_url = state.get("test_environment_url")
    future_backlog = state.get("future_backlog", []) or []

    lines = [f"\n---\n\n## Sprint {sprint_number} — {timestamp}\n"]

    lines.append("### Bu sprintte yapılanlar")
    for story in backlog:
        lines.append(f"- {story['title']} (`{story['id']}`, {story['priority']})")

    if stack:
        lines.append(f"\n### Teknoloji kararı\n{stack.get('choice')} — {stack.get('reasoning')}")

    lines.append(f"\n### Kalite süreci")
    lines.append(f"- Review revizyon turu: {review_revisions}")
    lines.append(f"- QA consensus turu: {qa_rounds}")
    lines.append(f"- Deploy durumu: {deploy_status}")
    if test_url:
        lines.append(f"- Test ortamı: {test_url}")

    lines.append(f"\n### Sıradaki sprint için plan")
    if future_backlog:
        lines.append("Bekleyen backlog'dan öncelikli adaylar:")
        for story in future_backlog[:4]:
            lines.append(f"- {story['title']} (`{story['id']}`, {story['priority']})")
        lines.append(f"\n(Toplam {len(future_backlog)} özellik vizyon backlog'unda bekliyor.)")
    else:
        lines.append("Bekleyen özellik yok — yeni bir CEO girdisi bekleniyor.")

    with open(path, "a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def _write_backlog_snapshot(docs_dir: str, state: SprintState) -> None:
    path = os.path.join(docs_dir, "backlog.md")
    lines = ["# Güncel Backlog\n"]

    lines.append("## Bu sprint (tamamlanan/aktif)")
    for story in state.get("backlog", []):
        lines.append(f"- [{story['priority']}] {story['title']} (`{story['id']}`)")
        for c in story.get("acceptance_criteria", []):
            lines.append(f"  - ✓ {c}")

    lines.append("\n## Gelecek sprint'ler için bekleyen")
    for story in state.get("future_backlog", []) or []:
        lines.append(f"- [{story['priority']}] {story['title']} (`{story['id']}`)")

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def _write_tech_decision(docs_dir: str, state: SprintState, sprint_number: int) -> None:
    stack = state.get("tech_stack_decision")
    if not stack:
        return
    path = os.path.join(docs_dir, "tech-decisions.md")
    entry = (
        f"\n## Sprint {sprint_number}\n"
        f"- **Seçim:** {stack.get('choice')}\n"
        f"- **Gerekçe:** {stack.get('reasoning')}\n"
    )
    mode = "a" if os.path.exists(path) else "w"
    with open(path, mode, encoding="utf-8") as f:
        if mode == "w":
            f.write("# Teknoloji Kararları\n")
        f.write(entry)


def _write_postman_collection(docs_dir: str, state: SprintState) -> None:
    """Backend task'larının dev_notes'undaki API kontratlarından basit bir Postman
    collection (v2.1 formatı) üretir — CEO import edip Postman'da gerçek istekler atabilir."""
    path = os.path.join(docs_dir, "postman_collection.json")

    dev_outputs = state.get("dev_outputs", []) or []
    tasks_by_id = {t["id"]: t for t in (state.get("tasks", []) or [])}
    base_url = state.get("test_environment_url") or "http://localhost:5050"

    items = []
    for output in dev_outputs:
        task = tasks_by_id.get(output["task_id"])
        if not task or task.get("domain") != "backend":
            continue
        items.append({
            "name": f"{output['task_id']} — {task.get('description', '')[:60]}",
            "request": {
                "method": "GET",  # gerçek method dev_notes'tan çıkarılamıyorsa güvenli varsayılan
                "header": [{"key": "Content-Type", "value": "application/json"}],
                "url": {"raw": base_url, "host": [base_url]},
                "description": output.get("dev_notes", ""),
            },
        })

    collection = {
        "info": {
            "name": f"{state.get('project_name') or state.get('project_slug') or 'Proje'} API",
            "description": "Otomatik üretildi (agent-squad sprint_documenter). Backend dev_notes'undaki "
                           "API kontratlarına göre — endpoint/method detaylarını Postman'da elle "
                           "düzeltmen gerekebilir, bu bir başlangıç noktası.",
            "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json",
        },
        "item": items,
    }

    with open(path, "w", encoding="utf-8") as f:
        json.dump(collection, f, ensure_ascii=False, indent=2)


def _write_project_state_json(docs_dir: str, state: SprintState) -> None:
    """Bir sonraki sprint'in PM'e bağlam olarak verebileceği kalıcı proje durumu.
    docs/backlog.md insan-okunur özet, bu ise makine-okunur — devam ettirme mekanizması bunu kullanır."""
    path = os.path.join(docs_dir, "project_state.json")
    payload = {
        "project_name": state.get("project_name"),
        "project_slug": state.get("project_slug"),
        "original_idea": state.get("idea"),
        "product_vision": state.get("product_vision", []),
        "completed_backlog": state.get("backlog", []),  # bu sprint'te tamamlanan
        "future_backlog": state.get("future_backlog", []),  # sıradaki sprint adayları
        "tech_stack_decision": state.get("tech_stack_decision"),
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def document_sprint_node(state: SprintState) -> SprintState:
    project_slug = state.get("project_slug") or "proje"
    docs_dir = _docs_dir(project_slug)

    # Sprint numarasını sprint-log.md'deki mevcut "## Sprint N" sayısından türet
    sprint_log_path = os.path.join(docs_dir, "sprint-log.md")
    sprint_number = 1
    if os.path.exists(sprint_log_path):
        with open(sprint_log_path, encoding="utf-8") as f:
            sprint_number = f.read().count("## Sprint ") + 1

    _write_product_vision(docs_dir, state)
    _write_sprint_log_entry(docs_dir, state, sprint_number)
    _write_backlog_snapshot(docs_dir, state)
    _write_tech_decision(docs_dir, state, sprint_number)
    _write_postman_collection(docs_dir, state)
    _write_project_state_json(docs_dir, state)

    return state
