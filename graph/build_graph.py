"""
Sprint akışı — insan müdahale noktalarıyla (checkin):

  idea -> PM Agent -> [PLANNING CHECKIN] -> Designer <-> Tech Lead <-> Dev'ler
              ^                                              |
              +---------------- (netleştirme döngüleri) -----+
                                                               v
                                            Reviewer <-> Dev (red -> geri döngü)
                                                               |
                                        limit aşılırsa -> [ESCALATION CHECKIN]
                                                               v
                                            QA(x3 paralel) -> Consensus
                                                               |
                                        limit aşılırsa -> [ESCALATION CHECKIN]
                                                               v
                                       DevOps -> Sprint Docs -> [RETRO CHECKIN] -> Slack -> Orchestrator

Checkpointer artık SqliteSaver — process kapansa bile bekleyen bir sprint diskte kalır,
aynı thread_id ile geri dönüp devam ettirilebilir.
"""
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command
import sqlite3
import os
import json

from graph.state import SprintState
from agents.pm_agent import pm_agent_node
from agents.designer_agent import designer_agent_node
from agents.tech_lead_agent import tech_lead_agent_node
from agents.dev_agent import backend_dev_agent_node, frontend_dev_agent_node
from agents.reviewer_agent import reviewer_agent_node
from agents.qa_agents import qa_parallel_node, qa_consensus_node
from agents.devops_agent import devops_agent_node
from agents.slack_notifier import slack_notifier_node
from agents.sprint_documenter import document_sprint_node
from agents.checkin_nodes import planning_checkin_node, escalation_checkin_node, retro_checkin_node
from agents.logger import log_step, print_full_log

CHECKPOINT_DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "sprint_checkpoints.sqlite")


def orchestrator_placeholder_node(state: SprintState) -> SprintState:
    """PM + Designer + Tech Lead + Dev çıktısını gözlemlememizi sağlayan durak noktası."""
    log_step(state, agent="orchestrator", action="sprint_review",
              detail="PM, Designer, Tech Lead ve Dev çıktısı incelendi.")

    print("\n=== ORCHESTRATOR: Sprint Özeti ===")
    print(f"Backlog: {len(state['backlog'])} story | Task: {len(state.get('tasks', []) or [])} | "
          f"Dev çıktısı: {len(state.get('dev_outputs', []) or [])} task tamamlandı")
    print(f"Toplam revizyon turu: {state.get('revision_count', 0)} (backlog/design) | "
          f"{state.get('review_revision_count', 0)} (review) | {state.get('qa_revision_count', 0)} (QA)\n")

    if state.get("retro_notes"):
        print(f"  📝 CEO retro notu: {state['retro_notes']}\n")

    print("  QA Consensus Sonuçları (turlar):")
    for round_result in state.get("qa_results", []) or []:
        status = "BAŞARISIZ" if round_result["failed_stories"] else "3/3 UYUM ✅"
        print(f"    Tur {round_result['round']}: {status} | {round_result['per_story_statuses']}")
    print()

    print(f"  Deploy Durumu: {state.get('deploy_status', '(yok)')}")
    print()

    print("=" * 60)
    if state.get("test_environment_url"):
        print(f"  🔗 TEST ORTAMI HAZIR: {state['test_environment_url']}")
        print(f"     (proje klasörü: deploy_output/{state.get('project_slug', '?')}/)")
    else:
        print(f"  ⚠️  TEST ORTAMI BAŞLATILAMADI")
        print(f"     Dosyalar yine de burada: deploy_output/{state.get('project_slug', '?')}/")
    print("=" * 60 + "\n")

    print_full_log(state)
    return state


def route_after_designer(state: SprintState) -> str:
    if state.get("clarification_needed") and state.get("clarification_target") == "pm_agent":
        return "pm_agent"
    return "tech_lead_agent"


def route_after_tech_lead(state: SprintState) -> str:
    if state.get("clarification_needed") and state.get("clarification_target") == "designer_agent":
        return "designer_agent"
    return "backend_dev_agent"


def route_after_reviewer(state: SprintState) -> str:
    if state.get("escalation_needed"):
        return "escalation_checkin"
    if state.get("tasks_to_revise"):
        return "backend_dev_agent"
    return "qa_parallel"


def route_after_qa_consensus(state: SprintState) -> str:
    if state.get("escalation_needed"):
        return "escalation_checkin"
    if state.get("tasks_to_revise"):
        return "backend_dev_agent"
    return "devops_agent"


def route_after_escalation(state: SprintState) -> str:
    if state.get("escalation_resolution") == "retry":
        return "backend_dev_agent"
    return "devops_agent"  # "continue" -> olduğu haliyle deploy'a geç


def build_graph():
    graph = StateGraph(SprintState)

    graph.add_node("pm_agent", pm_agent_node)
    graph.add_node("planning_checkin", planning_checkin_node)
    graph.add_node("designer_agent", designer_agent_node)
    graph.add_node("tech_lead_agent", tech_lead_agent_node)
    graph.add_node("backend_dev_agent", backend_dev_agent_node)
    graph.add_node("frontend_dev_agent", frontend_dev_agent_node)
    graph.add_node("reviewer_agent", reviewer_agent_node)
    graph.add_node("qa_parallel", qa_parallel_node)
    graph.add_node("qa_consensus", qa_consensus_node)
    graph.add_node("escalation_checkin", escalation_checkin_node)
    graph.add_node("devops_agent", devops_agent_node)
    graph.add_node("sprint_documenter", document_sprint_node)
    graph.add_node("retro_checkin", retro_checkin_node)
    graph.add_node("slack_notifier", slack_notifier_node)
    graph.add_node("orchestrator", orchestrator_placeholder_node)

    graph.set_entry_point("pm_agent")
    graph.add_edge("pm_agent", "planning_checkin")
    graph.add_edge("planning_checkin", "designer_agent")
    graph.add_conditional_edges(
        "designer_agent", route_after_designer,
        {"pm_agent": "pm_agent", "tech_lead_agent": "tech_lead_agent"},
    )
    graph.add_conditional_edges(
        "tech_lead_agent", route_after_tech_lead,
        {"designer_agent": "designer_agent", "backend_dev_agent": "backend_dev_agent"},
    )
    graph.add_edge("backend_dev_agent", "frontend_dev_agent")
    graph.add_edge("frontend_dev_agent", "reviewer_agent")
    graph.add_conditional_edges(
        "reviewer_agent", route_after_reviewer,
        {"backend_dev_agent": "backend_dev_agent", "qa_parallel": "qa_parallel",
         "escalation_checkin": "escalation_checkin"},
    )
    graph.add_edge("qa_parallel", "qa_consensus")
    graph.add_conditional_edges(
        "qa_consensus", route_after_qa_consensus,
        {"backend_dev_agent": "backend_dev_agent", "devops_agent": "devops_agent",
         "escalation_checkin": "escalation_checkin"},
    )
    graph.add_conditional_edges(
        "escalation_checkin", route_after_escalation,
        {"backend_dev_agent": "backend_dev_agent", "devops_agent": "devops_agent"},
    )
    graph.add_edge("devops_agent", "sprint_documenter")
    graph.add_edge("sprint_documenter", "retro_checkin")
    graph.add_edge("retro_checkin", "slack_notifier")
    graph.add_edge("slack_notifier", "orchestrator")
    graph.add_edge("orchestrator", END)

    conn = sqlite3.connect(CHECKPOINT_DB, check_same_thread=False)
    checkpointer = SqliteSaver(conn)

    return graph.compile(checkpointer=checkpointer)


def make_initial_state(idea: str) -> SprintState:
    return {
        "idea": idea,
        "backlog": [],
        "pm_notes": None,
        "product_vision": None,
        "project_name": None,
        "future_backlog": None,
        "is_continuation_sprint": False,
        "design_notes": None,
        "tasks": None,
        "tech_lead_notes": None,
        "tech_stack_decision": None,
        "stack_constraints": None,
        "dev_outputs": None,
        "review_results": None,
        "tasks_to_revise": None,
        "review_revision_count": 0,
        "qa_round_verdicts": None,
        "qa_results": None,
        "qa_revision_count": 0,
        "escalation_needed": None,
        "escalation_resolution": None,
        "retro_notes": None,
        "clarification_needed": None,
        "clarification_source": None,
        "clarification_target": None,
        "revision_count": 0,
        "conversation_log": [],
        "pr_status": None,
        "deploy_status": None,
        "deployed_files": None,
        "test_environment_url": None,
        "project_slug": None,
        "squad_id": None,
        "assigned_epics": None,
    }


def list_existing_projects() -> list[dict]:
    """deploy_output/ altındaki tüm projelerin project_state.json'larını tarar."""
    deploy_root = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "deploy_output")
    if not os.path.isdir(deploy_root):
        return []

    projects = []
    for slug in os.listdir(deploy_root):
        state_path = os.path.join(deploy_root, slug, "docs", "project_state.json")
        if os.path.exists(state_path):
            with open(state_path, encoding="utf-8") as f:
                data = json.load(f)
                projects.append(data)
    return projects


def load_project_state_into_initial(idea_note: str, project_data: dict) -> SprintState:
    """Var olan bir projeye devam sprint'i başlatmak için initial_state'i önceki
    vizyon/backlog ile önceden doldurur."""
    state = make_initial_state(idea_note)
    state["product_vision"] = project_data.get("product_vision", [])
    state["future_backlog"] = project_data.get("future_backlog", [])
    state["project_name"] = project_data.get("project_name", "")
    state["is_continuation_sprint"] = True
    return state


def list_incomplete_threads(checkpoint_db_path: str, app) -> list[str]:
    """Checkpoint DB'deki TÜM thread_id'leri tarar, hangilerinin yarım kaldığını (sprint
    bitmemiş, next boş değil) bulur. Bu sayede kullanıcı hangi thread_id'yi kullandığını
    hatırlamak zorunda kalmadan 'devam edebileceğin sprintler' listesini görür."""
    if not os.path.exists(checkpoint_db_path):
        return []
    conn = sqlite3.connect(checkpoint_db_path)
    try:
        cur = conn.cursor()
        cur.execute("SELECT DISTINCT thread_id FROM checkpoints")
        thread_ids = [r[0] for r in cur.fetchall()]
    finally:
        conn.close()

    incomplete = []
    for tid in thread_ids:
        snapshot = app.get_state({"configurable": {"thread_id": tid}})
        if snapshot.values and snapshot.next:
            incomplete.append(tid)
    return incomplete


def _print_interrupt(payload: dict) -> None:
    print("\n" + "#" * 60)
    print(f"  ⏸️  DURDU — SENİN GİRDİN GEREKİYOR ({payload.get('type', '?')})")
    print("#" * 60)
    print(f"  {payload.get('message', '')}\n")
    for key, value in payload.items():
        if key in ("type", "message"):
            continue
        print(f"  {key}: {value}")
    print("#" * 60)


def _get_human_input(payload: dict) -> dict:
    """Terminal üzerinden CEO girdisini alır. Slack token'ları geldiğinde bu fonksiyon
    yerine Slack mesajı bekleyen bir sürüm konacak — geri kalan graph mantığı DEĞİŞMEYECEK."""
    kind = payload.get("type")

    if kind == "planning_approval":
        print("\nOnaylıyor musun? [Enter: onayla] ya da aksiyon gir")
        print("  Örnek: move US-5   (vizyon backlog'undan bu sprint'e taşı)")
        print("  Örnek: remove US-2 (bu sprint'ten çıkar)")
        raw = input("> ").strip()
        if not raw:
            return {"action": "approve"}
        parts = raw.split()
        if parts[0] == "move" and len(parts) > 1:
            return {"action": "move_to_mvp", "story_id": parts[1]}
        if parts[0] == "remove" and len(parts) > 1:
            return {"action": "remove_from_mvp", "story_id": parts[1]}
        return {"action": "approve"}

    if kind == "escalation":
        raw = input("\n[retry/continue] (Enter: continue) > ").strip().lower()
        if raw == "retry":
            guidance = input("Ek notun (opsiyonel): ").strip()
            return {"decision": "retry", "guidance": guidance}
        return {"decision": "continue"}

    if kind == "retro":
        note = input("\nRetro notun (Enter: geç) > ").strip()
        return {"note": note}

    return {}


if __name__ == "__main__":
    import sys
    import uuid

    app = build_graph()

    # KULLANIM:
    #   python -m graph.build_graph                    -> interaktif mod
    #   python -m graph.build_graph "proje fikri"       -> hemen başlat, thread_id OTOMATİK üretilir
    # Farklı terminallerde farklı fikirlerle çalıştırınca, HİÇBİR ŞEY elle atamana gerek
    # kalmadan otomatik olarak ayrı, çakışmayan thread'lerde (dolayısıyla ayrı proje
    # klasörlerinde/portlarda) çalışırlar.
    cli_idea = " ".join(sys.argv[1:]).strip()

    if cli_idea:
        thread_id = f"sprint-{uuid.uuid4().hex[:8]}"
        os.environ["THREAD_ID"] = thread_id  # .intervene_/.question_ flag dosyaları bunu kullanır
        print(f"\n>>> Yeni sprint başlatılıyor (thread: {thread_id})")
        print(f">>> Müdahale etmek istersen: touch .intervene_{thread_id}")
        print(f">>> Soru sormak istersen: echo \"sorun\" > .question_{thread_id}\n")
        config = {"configurable": {"thread_id": thread_id}, "recursion_limit": 100}
        initial_state = make_initial_state(cli_idea)
        is_resumable = False
    else:
        incomplete_threads = list_incomplete_threads(CHECKPOINT_DB, app)

        if incomplete_threads:
            print(">>> Yarım kalmış sprint(ler) bulundu:")
            for tid in incomplete_threads:
                print(f"    - {tid}")
            chosen = input("\nHangisine devam etmek istersin? (Enter: ilkini seç): ").strip()
            thread_id = chosen if chosen in incomplete_threads else incomplete_threads[0]
            os.environ["THREAD_ID"] = thread_id
            print(f"\n>>> Müdahale etmek istersen: touch .intervene_{thread_id}")
            print(f">>> Soru sormak istersen: echo \"sorun\" > .question_{thread_id}\n")
            config = {"configurable": {"thread_id": thread_id}, "recursion_limit": 100}
            is_resumable = True
            initial_state = None
        else:
            existing_projects = list_existing_projects()
            if existing_projects:
                print(">>> Mevcut projeler:")
                for p in existing_projects:
                    print(f"    - {p.get('project_name')} (slug: {p.get('project_slug')}, "
                          f"{len(p.get('future_backlog', []))} bekleyen özellik)")
                print()

            choice = input(
                "Yeni bir proje için fikrini yaz, YA DA mevcut bir projeye devam etmek için "
                "slug'ını yaz (Enter: örnek fikirle yeni proje): "
            ).strip()

            matching_project = next(
                (p for p in existing_projects if p.get("project_slug") == choice), None
            )

            thread_id = f"sprint-{uuid.uuid4().hex[:8]}"
            os.environ["THREAD_ID"] = thread_id
            print(f"\n>>> Müdahale etmek istersen: touch .intervene_{thread_id}")
            print(f">>> Soru sormak istersen: echo \"sorun\" > .question_{thread_id}\n")
            config = {"configurable": {"thread_id": thread_id}, "recursion_limit": 100}
            is_resumable = False

            if matching_project:
                note = input(
                    f"'{matching_project['project_name']}' projesine devam ediliyor. Bu sprint "
                    f"için eklemek istediğin bir şey var mı? (Enter: sadece bekleyen backlog'dan "
                    f"devam et): "
                ).strip()
                initial_state = load_project_state_into_initial(note, matching_project)
            else:
                idea = choice or "Kullanıcılar not ekleyip, düzenleyip, silebileceği basit bir web uygulaması istiyorum."
                initial_state = make_initial_state(idea)

    try:
        result = app.invoke(None if is_resumable else initial_state, config=config)

        while "__interrupt__" in result:
            interrupt_obj = result["__interrupt__"][0]
            _print_interrupt(interrupt_obj.value)
            answer = _get_human_input(interrupt_obj.value)
            result = app.invoke(Command(resume=answer), config=config)

    except Exception as e:
        print(f"\n!!! SPRINT ÇÖKTÜ: {e}\n")
        print(f"Kaldığı yerden devam etmek için: 'python -m graph.build_graph' çalıştır "
              f"(argümansız) — yarım kalan '{thread_id}' otomatik listede görünecek.")
        raise
