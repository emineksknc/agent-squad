"""
Slack Notifier — sprint bitince "şunları yaptık + test ortamı burada" özetini
Slack'e (SLACK_WEBHOOK_URL ayarlıysa) ya da konsola (ayarlı değilse, aynı formatta)
basar.

Gerçek Slack'e bağlamak için: Slack workspace'inde bir Incoming Webhook oluştur
(https://api.slack.com/messaging/webhooks) ve URL'i env değişkeni olarak ver:
    export SLACK_WEBHOOK_URL="https://hooks.slack.com/services/..."
Ayarlanmazsa, sistem otomatik olarak konsola aynı mesajı basar — geliştirme
aşamasında Slack kurmadan da akışı görebilesin diye.
"""
import os
import urllib.request
import json

from graph.state import SprintState
from agents.logger import log_step

SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL")


def _build_summary_message(state: SprintState) -> str:
    idea = state.get("idea", "")
    backlog = state.get("backlog", [])
    deploy_status = state.get("deploy_status")
    test_url = state.get("test_environment_url")
    qa_rounds = len(state.get("qa_results", []) or [])
    review_revisions = state.get("review_revision_count", 0)
    stack_decision = state.get("tech_stack_decision")

    lines = [
        f"*🚀 Sprint Tamamlandı — \"{idea}\"*",
        "",
    ]
    if stack_decision:
        lines.append(f"*🛠️ Seçilen stack:* {stack_decision.get('choice')}"
                     f"{' + ' + stack_decision.get('frontend_choice') if stack_decision.get('frontend_choice') else ''} "
                     f"_({stack_decision.get('reasoning', '')})_")
        lines.append("")

    lines.append("*Yapılanlar:*")
    for story in backlog:
        lines.append(f"  • {story['title']} ({story['id']}, {story['priority']})")

    future_backlog = state.get("future_backlog", []) or []
    if future_backlog:
        lines.append("")
        lines.append(f"*📋 Sıradaki sprint adayları ({len(future_backlog)} bekleyen özellik):*")
        for story in future_backlog[:3]:
            lines.append(f"  • {story['title']} ({story['id']})")

    lines.append("")
    lines.append(f"*Kalite süreci:* {review_revisions} review revizyonu, {qa_rounds} QA turu "
                 f"(3/3 bağımsız consensus şartıyla)")

    lines.append("")
    if deploy_status == "deployed":
        lines.append("*✅ Deploy durumu:* Başarılı")
        if test_url:
            lines.append(f"*🔗 Test ortamı:* {test_url}")
        else:
            lines.append("*⚠️ Test ortamı:* Dosyalar deploy edildi ama uygulama otomatik "
                         "başlatılamadı (manuel kontrol gerekebilir — bkz. deploy_output/)")
    else:
        lines.append("*❌ Deploy durumu:* Başarısız — İNSAN İNCELEMESİ GEREKLİ")

    return "\n".join(lines)


def _post_to_slack(message: str) -> bool:
    payload = json.dumps({"text": message}).encode("utf-8")
    req = urllib.request.Request(
        SLACK_WEBHOOK_URL, data=payload, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status == 200
    except Exception:
        return False


def slack_notifier_node(state: SprintState) -> SprintState:
    message = _build_summary_message(state)

    if SLACK_WEBHOOK_URL:
        sent = _post_to_slack(message)
        if sent:
            log_step(state, agent="slack_notifier", action="posted_to_slack",
                      detail="Sprint özeti Slack'e gönderildi.")
        else:
            log_step(state, agent="slack_notifier", action="slack_post_failed",
                      detail="Slack'e gönderim başarısız oldu, mesaj konsola yazdırıldı.")
            print("\n" + "=" * 60)
            print("SLACK MESAJI (gönderim başarısız, burada gösteriliyor):")
            print("=" * 60)
            print(message)
            print("=" * 60 + "\n")
    else:
        log_step(state, agent="slack_notifier", action="slack_not_configured",
                  detail="SLACK_WEBHOOK_URL ayarlı değil, mesaj konsola yazdırıldı.")
        print("\n" + "=" * 60)
        print("SLACK MESAJI (SLACK_WEBHOOK_URL ayarlı değil, konsola basılıyor):")
        print("=" * 60)
        print(message)
        print("=" * 60 + "\n")

    return state
