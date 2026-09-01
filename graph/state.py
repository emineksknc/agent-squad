"""
Sprint State — tüm agent'ların okuyup yazdığı ortak state.
LangGraph bu TypedDict'i node'lar arasında taşır.

Şema en baştan "kompleks" senaryoları (karşılıklı netleştirme, geri döngü,
log, ileride çoklu squad) kapsayacak şekilde tasarlandı — ama DAVRANIŞLAR
aşamalı olarak eklenip test edilecek (bkz. agent-takimi-plan.md).
"""
from typing import TypedDict, Optional


class UserStory(TypedDict):
    id: str
    title: str
    description: str
    priority: str  # "high" | "medium" | "low"
    acceptance_criteria: list[str]


class DesignNote(TypedDict):
    story_id: str
    flow: list[str]
    notes: str


class TaskItem(TypedDict):
    id: str
    story_id: str
    description: str
    domain: str                    # "backend" | "frontend" | "fullstack"
    files_to_create: list[str]   # örn. ["app.py"] veya ["templates/index.html"]
    depends_on: list[str]         # başka task id'leri (basit bağımlılık sırası için)
    is_entrypoint: bool            # backend task'lar için: SADECE BİR TANESİ True olmalı —
                                    # app.run()/uvicorn.run() çağrısını SADECE bu dosya içerir.
                                    # Diğer backend dosyaları (route modülleri) bunu içermez,
                                    # ana dosya tarafından import edilir.


class DevOutput(TypedDict):
    task_id: str
    domain: str             # "backend" | "frontend"
    files: dict            # {"dosya_adı": "içerik"}
    dev_notes: str


class LogEntry(TypedDict):
    timestamp: str
    agent: str
    action: str
    detail: str
    target_agent: Optional[str]


class SprintState(TypedDict):
    # --- CEO girdisi ---
    idea: str

    # --- PM Agent çıktısı ---
    backlog: list[UserStory]
    pm_notes: Optional[str]
    product_vision: Optional[list[dict]]     # [{"feature": "...", "why": "..."}] - geniş vizyon, ~15-20 madde
    project_name: Optional[str]              # PM'in ürettiği kısa proje adı (klasör/slug için)
    future_backlog: Optional[list[UserStory]]  # vizyondan MVP'ye girmeyen, gelecek sprint'ler için bekleyenler
    is_continuation_sprint: bool               # True ise PM sıfırdan vizyon üretmez, mevcut future_backlog'dan seçer

    # --- UX/Designer Agent çıktısı ---
    design_notes: Optional[list[DesignNote]]

    # --- Tech Lead Agent çıktısı ---
    tasks: Optional[list[TaskItem]]
    tech_lead_notes: Optional[str]
    tech_stack_decision: Optional[dict]   # {"choice": "...", "reasoning": "...", "criteria": {...}}

    # --- Organizasyon/CEO kısıtları (opsiyonel, Tech Lead kararını etkiler) ---
    stack_constraints: Optional[str]      # örn. "Backend Python olmalı, ekip Python'a hakim"

    # --- Dev Agent çıktısı ---
    dev_outputs: Optional[list[DevOutput]]

    # --- Reviewer Agent çıktısı ---
    review_results: Optional[list[dict]]   # [{"task_id", "status": "approved"|"rejected", "feedback": str}]
    tasks_to_revise: Optional[list[str]]    # reddedilen task id'leri — dev agent bunları yeniden yazar
    review_revision_count: int               # reviewer<->dev döngüsü için ayrı sayaç

    # --- İnsan müdahale noktaları (checkin) ---
    escalation_needed: Optional[dict]        # {"source": "reviewer"|"qa", "detail": "..."} - limit aşılınca dolar
    escalation_resolution: Optional[str]     # "retry" | "continue" - CEO'nun kararı
    retro_notes: Optional[str]               # CEO'nun sprint sonunda eklediği serbest not

    # --- QA Agent x3 + Consensus ---
    qa_round_verdicts: Optional[list[dict]]  # bu tur için 3 QA agent'ın bağımsız verdictleri
    qa_results: Optional[list[dict]]         # tüm turların kalıcı kaydı (audit için)
    qa_revision_count: int                     # QA<->dev döngüsü için ayrı sayaç

    # --- İşbirliği / netleştirme döngüsü (genel amaçlı — herhangi iki agent arasında kullanılabilir) ---
    clarification_needed: Optional[str]
    clarification_source: Optional[str]      # kim soruyor (örn. "tech_lead_agent")
    clarification_target: Optional[str]      # kime soruyor (örn. "designer_agent")
    revision_count: int

    # --- Ortak log (her agent yazar) ---
    conversation_log: list[LogEntry]

    # --- Sonraki adımlarda dolacak alanlar (şimdilik placeholder) ---
    pr_status: Optional[str]
    deploy_status: Optional[str]        # "deployed" | "failed" | None
    deployed_files: Optional[list[str]]  # deploy edilen dosya yolları (audit için)
    test_environment_url: Optional[str]  # app gerçekten başlatılabildiyse erişim adresi
    project_slug: Optional[str]          # her proje için ayrı klasör adı (idea'dan türetilir)

    # --- İleride çoklu squad için (Faz 0.5 — henüz kullanılmıyor) ---
    squad_id: Optional[str]
    assigned_epics: Optional[list[str]]
