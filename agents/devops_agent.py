"""
DevOps/Release Agent — QA consensus'tan geçmiş (3/3 onaylı) kodu her proje için
AYRI bir klasöre deploy eder, gerekli ortamı (Python venv ya da npm bağımlılıkları)
OTOMATİK kurar ve uygulamayı gerçekten başlatıp bir test URL'i sunar.

İZOLASYON: her proje kendi klasöründe (deploy_output/<proje-slug>/) yaşar. Python
projeleri kendi venv'ine sahip olur.

PAKET TESPİTİ: sadece stack'e göre sabit bir liste (flask/fastapi) kurmakla kalmıyoruz —
üretilen kodun GERÇEK import satırlarını tarayıp (flask_cors, jwt, bcrypt gibi ek
bağımlılıkları da) otomatik kurmaya çalışıyoruz. Bilinmeyen bir paket pip'te bulunamazsa
bu net şekilde loglanır, sessizce yutulmaz.

TANI: Uygulama başlatılamazsa artık stderr'i (DEVNULL yerine bir log dosyasına) yakalayıp
son satırlarını doğrudan konsola/log'a basıyoruz — "neden başlamadı" sorusunun cevabı
artık görünür.

ÇAKIŞMA TESPİTİ: Birden fazla task AYNI dosya adına yazarsa (örn. iki backend task da
"app.py" üretirse) bu bir veri kaybı riskidir (sonraki task öncekini siler) — bunu artık
açıkça loglayıp uyarıyoruz.

Girdi: state["dev_outputs"], state["idea"]
Çıktı: state["deploy_status"], state["deployed_files"], state["test_environment_url"],
       state["project_slug"]
"""
import hashlib
import os
import py_compile
import re
import shutil
import socket
import subprocess
import sys
import sysconfig
import tempfile
import time
import uuid
import venv

from graph.state import SprintState
from agents.logger import log_step
from agents.checkin_nodes import check_and_answer_question

DEPLOY_ROOT = os.environ.get(
    "DEPLOY_DIR",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "deploy_output"),
)
APP_PORT_BASE = int(os.environ.get("APP_PORT_BASE", "5050"))
APP_PORT_RANGE = 200  # 5050-5249 arası, çakışma ihtimalini düşürmek için geniş bir aralık


def _port_for_project(project_slug: str) -> int:
    """Proje slug'ından DETERMİNİSTİK bir port üretir — aynı proje her zaman aynı portu
    kullanır (devam sprint'lerinde tutarlılık için), farklı projeler farklı portlar alır.
    Böylece birden fazla proje aynı anda (farklı terminallerde, farklı THREAD_ID ile)
    çalışırken portlar çakışmaz. Manuel override istersen: export APP_PORT=1234"""
    manual_override = os.environ.get("APP_PORT")
    if manual_override:
        return int(manual_override)

    offset = int(hashlib.sha256(project_slug.encode()).hexdigest(), 16) % APP_PORT_RANGE
    return APP_PORT_BASE + offset

NODE_PATH = shutil.which("node")
NPM_PATH = shutil.which("npm")
ALLOW_SYSTEM_INSTALL = os.environ.get("ALLOW_SYSTEM_INSTALL", "0") == "1"


def _node_install_hint() -> str:
    """Node.js bir sistem çalışma zamanı — Python venv gibi izole kuramayız. OS'a göre
    tam kurulum komutunu veriyoruz."""
    if sys.platform == "win32":
        return "winget install OpenJS.NodeJS.LTS  (ya da https://nodejs.org'dan indir)"
    if sys.platform == "darwin":
        return "brew install node  (ya da https://nodejs.org'dan indir)"
    return "sudo apt install nodejs npm  (ya da https://nodejs.org'dan indir)"


def _try_auto_install_node() -> bool:
    """SADECE ALLOW_SYSTEM_INSTALL=1 ayarlıysa dener — sistem seviyesinde yazılım kurmak
    (Python venv'inden farklı olarak) izole değil, bu yüzden varsayılan olarak KAPALI."""
    try:
        if sys.platform == "win32":
            result = subprocess.run(
                ["winget", "install", "-e", "--id", "OpenJS.NodeJS.LTS", "--silent"],
                capture_output=True, text=True, timeout=300,
            )
        elif sys.platform == "darwin":
            result = subprocess.run(["brew", "install", "node"], capture_output=True, text=True, timeout=300)
        else:
            return False  # Linux'ta sudo gerektirdiği için otomatik denemiyoruz
        return result.returncode == 0
    except Exception:
        return False

# Stack'in kendisi için taban paketler (her zaman kurulur)
BASE_PACKAGE_MAP = {
    "flask": ["flask"],
    "fastapi": ["fastapi", "uvicorn"],
}

# import adı -> pip paket adı eşlemesi (farklı olduğu bilinen yaygın durumlar)
IMPORT_TO_PIP_NAME = {
    "flask_cors": "flask-cors", "flask_sqlalchemy": "flask-sqlalchemy",
    "jwt": "PyJWT", "bcrypt": "bcrypt", "cv2": "opencv-python",
    "yaml": "PyYAML", "PIL": "Pillow", "dotenv": "python-dotenv",
    "flask_login": "flask-login", "flask_migrate": "flask-migrate",
}

# Python'un kendi standart kütüphanesi — bunlar için pip install denenmeyecek
_STDLIB_MODULES = set(getattr(sys, "stdlib_module_names", ())) | {
    "os", "sys", "json", "re", "time", "datetime", "sqlite3", "uuid",
    "typing", "collections", "itertools", "functools", "math", "random",
    "hashlib", "secrets", "logging", "pathlib", "io", "csv", "string",
}


_TR_CHAR_MAP = str.maketrans({
    "ı": "i", "İ": "i", "ç": "c", "Ç": "c", "ğ": "g", "Ğ": "g",
    "ş": "s", "Ş": "s", "ö": "o", "Ö": "o", "ü": "u", "Ü": "u",
})


def _slugify(text: str, max_len: int = 40) -> str:
    text = text.translate(_TR_CHAR_MAP)
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:max_len] or "proje"


def _port_is_open(port: int, host: str = "127.0.0.1", timeout: float = 0.5) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        return s.connect_ex((host, port)) == 0


def _detect_stack_and_inject_port(filename: str, content: str, port: int) -> tuple[str | None, str]:
    if filename.endswith(".py") and "app.run(" in content:
        updated = re.sub(r"app\.run\(", f"app.run(port={port}, host='127.0.0.1', ", content, count=1)
        return "flask", updated

    if filename.endswith(".py") and "uvicorn.run(" in content:
        if re.search(r"port\s*=\s*\d+", content):
            updated = re.sub(r"port\s*=\s*\d+", f"port={port}", content, count=1)
        else:
            updated = re.sub(r"uvicorn\.run\(", f"uvicorn.run(port={port}, ", content, count=1)
        return "fastapi", updated

    if filename.endswith(".js") and "app.listen(" in content:
        updated = re.sub(r"app\.listen\(\s*\d+", f"app.listen({port}", content, count=1)
        if updated == content:
            updated = re.sub(r"app\.listen\(", f"app.listen({port}, ", content, count=1)
        return "express", updated

    return None, content


def _check_python_syntax(content: str) -> tuple[bool, str]:
    # Benzersiz dosya adı — iki proje AYNI ANDA syntax kontrolü yaparsa (paralel çalışma
    # senaryosu) birbirinin geçici dosyasını silmesin diye. Sabit isim kullanırsak bu
    # gerçek bir race condition'a yol açar (test ederken bunu bizzat yakaladık).
    tmp_path = os.path.join(tempfile.gettempdir(), f"_syntax_check_{uuid.uuid4().hex}.py")
    try:
        with open(tmp_path, "w") as f:
            f.write(content)
        py_compile.compile(tmp_path, doraise=True)
        return True, ""
    except py_compile.PyCompileError as e:
        return False, str(e)
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        cache_dir = os.path.join(os.path.dirname(tmp_path), "__pycache__")
        if os.path.isdir(cache_dir):
            for f in os.listdir(cache_dir):
                if os.path.basename(tmp_path).split(".")[0] in f:
                    try:
                        os.remove(os.path.join(cache_dir, f))
                    except OSError:
                        pass


def _check_js_syntax(content: str) -> tuple[bool, str]:
    if not NODE_PATH:
        return True, ""
    tmp_path = os.path.join(tempfile.gettempdir(), f"_syntax_check_{uuid.uuid4().hex}.js")
    try:
        with open(tmp_path, "w") as f:
            f.write(content)
        result = subprocess.run([NODE_PATH, "--check", tmp_path], capture_output=True, text=True, timeout=10)
        return (result.returncode == 0, result.stderr)
    except Exception:
        return True, ""
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


def _venv_python_path(venv_dir: str) -> str:
    if sys.platform == "win32":
        return os.path.join(venv_dir, "Scripts", "python.exe")
    return os.path.join(venv_dir, "bin", "python")


def _extract_imported_packages(py_content: str) -> set[str]:
    """Kodun import satırlarından üçüncü parti paket adaylarını çıkarır (stdlib hariç)."""
    modules = set()
    for match in re.finditer(r"^\s*(?:from|import)\s+([a-zA-Z0-9_]+)", py_content, re.MULTILINE):
        mod = match.group(1)
        if mod not in _STDLIB_MODULES and not mod.startswith("_"):
            modules.add(mod)
    return modules


def _ensure_python_env(project_dir: str, stack: str, all_py_content: str) -> tuple[str | None, str]:
    """Proje için venv oluşturur (yoksa), stack'in taban paketlerini + kodun gerçekten
    import ettiği ek paketleri otomatik kurar. (python_exe, log_mesajı) döner."""
    venv_dir = os.path.join(project_dir, "venv")
    python_exe = _venv_python_path(venv_dir)

    if not os.path.exists(python_exe):
        try:
            venv.create(venv_dir, with_pip=True)
        except Exception as e:
            return None, f"venv oluşturulamadı: {e}"

    base_packages = BASE_PACKAGE_MAP.get(stack, [])
    imported = _extract_imported_packages(all_py_content)
    # Zaten base_packages'ta karşılığı olanları (örn. "flask" importu) tekrar eklemeyelim
    extra_import_names = {m for m in imported if m.lower() not in ("flask", "fastapi", "uvicorn")}
    extra_pip_names = [IMPORT_TO_PIP_NAME.get(m, m) for m in extra_import_names]

    all_packages = list(dict.fromkeys(base_packages + extra_pip_names))  # sırayı koru, tekrarı at

    missing = []
    for pkg in all_packages:
        import_name = pkg.replace("-", "_")  # kaba bir tahmin, çoğu paket için işe yarar
        check = subprocess.run(
            [python_exe, "-c", f"import {import_name}"], capture_output=True, timeout=10,
        )
        if check.returncode != 0:
            missing.append(pkg)

    if not missing:
        return python_exe, f"gerekli paketler zaten kurulu (venv): {all_packages}"

    # TEK TEK kur (toplu değil) — biri yavaş/hatalı olsa (örn. ağır bir bağımlılık ya da
    # halüsinasyon edilmiş yanlış bir paket adı) diğerlerini engellemesin. Gerçek bir
    # çalıştırmada 5 paket birden kurulurken tek bir yavaş paket TÜMÜNÜ 180s'de timeout'a
    # uğratmıştı — artık her paketin kendi zaman aşımı var, biri patlarsa devam ediyoruz.
    installed, failed = [], []
    for pkg in missing:
        try:
            install = subprocess.run(
                [python_exe, "-m", "pip", "install", "--quiet", pkg],
                capture_output=True, text=True, timeout=90,
            )
            if install.returncode == 0:
                installed.append(pkg)
            else:
                failed.append(f"{pkg} (hata: {install.stderr[-200:]})")
        except subprocess.TimeoutExpired:
            failed.append(f"{pkg} (90s içinde kurulamadı, atlandı)")
        except Exception as e:
            failed.append(f"{pkg} ({e})")

    base_ok = all(pkg in installed or pkg not in base_packages for pkg in base_packages)
    if not base_ok:
        # Stack'in KENDİSİ (flask/fastapi) kurulamadıysa uygulama zaten çalışmaz — bu fatal.
        return None, f"Temel paket(ler) kurulamadı: {failed}. Kurulanlar: {installed}"

    msg = f"kuruldu: {installed}"
    if failed:
        msg += f" | KURULAMAYAN (muhtemelen kritik değil, deneme yapıldı): {failed}"
    return python_exe, msg


def _npm_install_if_needed(project_dir: str) -> tuple[bool, str]:
    package_json_path = os.path.join(project_dir, "package.json")
    if not os.path.exists(package_json_path):
        return True, "package.json yok, kurulum gerekmiyor"
    if not NPM_PATH:
        return False, "npm bulunamadı — Node.js kurulumu gerekiyor (https://nodejs.org)"

    node_modules = os.path.join(project_dir, "node_modules")
    if os.path.isdir(node_modules):
        return True, "node_modules zaten kurulu"

    try:
        result = subprocess.run(
            [NPM_PATH, "install"], cwd=project_dir, capture_output=True, text=True, timeout=180,
        )
        if result.returncode == 0:
            return True, "npm install tamamlandı"
        return False, f"npm install başarısız: {result.stderr[-500:]}"
    except Exception as e:
        return False, f"npm install sırasında hata: {e}"


def _try_start_app(entrypoint_path: str, stack: str, python_exe: str | None, project_dir: str, port: int) -> tuple[str | None, str]:
    """Uygulamayı başlatır. Dönüş: (url ya da None, stderr_tail — hata teşhisi için)."""
    if stack in ("flask", "fastapi"):
        if not python_exe:
            return None, "(python ortamı hazır değildi)"
        cmd = [python_exe, os.path.basename(entrypoint_path)]
    elif stack == "express":
        if not NODE_PATH:
            return None, "(node bulunamadı)"
        cmd = [NODE_PATH, os.path.basename(entrypoint_path)]
    else:
        return None, "(bilinmeyen stack)"

    stdout_log_path = os.path.join(project_dir, "app_stdout.log")
    stderr_log_path = os.path.join(project_dir, "app_stderr.log")

    try:
        with open(stdout_log_path, "w") as out_f, open(stderr_log_path, "w") as err_f:
            popen_kwargs = dict(cwd=project_dir, env=os.environ.copy(), stdout=out_f, stderr=err_f)
            if sys.platform != "win32":
                popen_kwargs["start_new_session"] = True
            subprocess.Popen(cmd, **popen_kwargs)
    except Exception as e:
        return None, f"(process başlatılamadı: {e})"

    for _ in range(20):
        time.sleep(0.5)
        if _port_is_open(port):
            return f"http://localhost:{port}", ""

    stderr_tail = ""
    try:
        with open(stderr_log_path, encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
            stderr_tail = "".join(lines[-15:])
    except Exception:
        pass

    return None, stderr_tail


def _check_filename_collisions(dev_outputs: list[dict]) -> list[str]:
    """Birden fazla task'ın aynı dosyaya yazıp yazmadığını kontrol eder — yazarsa
    sonraki task öncekini SİLER (veri kaybı). Bunu tespit edip loglamak için."""
    filename_to_tasks: dict[str, list[str]] = {}
    for output in dev_outputs:
        for filename in output["files"]:
            filename_to_tasks.setdefault(filename, []).append(output["task_id"])

    warnings = []
    for filename, task_ids in filename_to_tasks.items():
        if len(task_ids) > 1:
            warnings.append(
                f"ÇAKIŞMA: '{filename}' dosyasına {len(task_ids)} farklı task yazdı ({task_ids}) — "
                f"sadece SONUNCUSU ({task_ids[-1]}) diskte kaldı, öncekiler SİLİNDİ. "
                f"Bu genelde Tech Lead'in aynı dosyayı birden fazla task'a bölmesinden kaynaklanır."
            )
    return warnings


def devops_agent_node(state: SprintState) -> SprintState:
    check_and_answer_question(state)

    project_slug = state.get("project_slug") or _slugify(
        state.get("project_name") or state.get("idea", "proje")
    )
    state["project_slug"] = project_slug
    project_dir = os.path.join(DEPLOY_ROOT, project_slug)
    os.makedirs(project_dir, exist_ok=True)
    app_port = _port_for_project(project_slug)

    dev_outputs = state.get("dev_outputs", []) or []

    # Dosya çakışması var mı önceden tespit et ve logla (deploy'u engellemez ama görünür yapar)
    collision_warnings = _check_filename_collisions(dev_outputs)
    for warning in collision_warnings:
        log_step(state, agent="devops_agent", action="filename_collision_detected", detail=warning)

    deployed_files = []
    syntax_errors = []
    entrypoint_path = None
    entrypoint_stack = None
    all_py_content = ""

    # Tech Lead'in hangi task'ı ana giriş noktası olarak işaretlediğini bul (varsa).
    # Bu, "her dosyada run() ara, sonuncusu kazanır" gibi kırılgan bir içerik taramasından
    # çok daha güvenilir — ama Tech Lead alanı doldurmazsa (bazı çalıştırmalarda oluyor)
    # eski içerik-taraması davranışına GERİ DÜŞÜYORUZ (aşağıda stack belirlenmezse).
    tasks_by_id = {t["id"]: t for t in (state.get("tasks", []) or [])}
    explicit_entrypoint_task_id = next(
        (t["id"] for t in (state.get("tasks", []) or [])
         if t.get("domain") == "backend" and t.get("is_entrypoint") is True),
        None,
    )
    if explicit_entrypoint_task_id:
        log_step(state, agent="devops_agent", action="entrypoint_identified",
                  detail=f"Tech Lead'in işaretlediği ana giriş noktası: task {explicit_entrypoint_task_id}")

    for output in dev_outputs:
        task = tasks_by_id.get(output["task_id"])
        is_non_entrypoint_backend = (
            explicit_entrypoint_task_id is not None
            and task is not None
            and task.get("domain") == "backend"
            and output["task_id"] != explicit_entrypoint_task_id
        )

        for filename, content in output["files"].items():
            chosen_stack = (state.get("tech_stack_decision") or {}).get("choice", "")
            if filename == "package.json" and chosen_stack.lower() != "express":
                # Model bazen Python projelerde de halüsinasyonla package.json üretiyor —
                # bu bir Node.js dosyası, Python stack'te hiçbir işi yok. Görmezden gel.
                log_step(state, agent="devops_agent", action="hallucinated_file_skipped",
                          detail=f"'{filename}' (task {output['task_id']}) stack {chosen_stack} "
                                 f"olduğu için deploy edilmedi (muhtemelen halüsinasyon).")
                continue

            if is_non_entrypoint_backend:
                # Bu dosya bilinçli olarak route modülü/blueprint — içinde yanlışlıkla bir
                # run() çağrısı olsa bile onu ENTRYPOINT olarak SAYMA, port enjekte etme.
                stack = None
            else:
                stack, content = _detect_stack_and_inject_port(filename, content, app_port)

            if stack:
                entrypoint_path = os.path.join(project_dir, filename)
                entrypoint_stack = stack

            if filename.endswith(".py"):
                all_py_content += content + "\n"
                ok, error = _check_python_syntax(content)
            elif filename.endswith(".js"):
                ok, error = _check_js_syntax(content)
            else:
                ok, error = True, ""

            if not ok:
                syntax_errors.append(f"{filename} (task {output['task_id']}): {error}")
                continue

            file_path = os.path.join(project_dir, filename)
            os.makedirs(os.path.dirname(file_path) or project_dir, exist_ok=True)
            with open(file_path, "w") as f:
                f.write(content)
            deployed_files.append(file_path)

    state["deployed_files"] = deployed_files

    if syntax_errors:
        state["deploy_status"] = "failed"
        state["test_environment_url"] = None
        log_step(state, agent="devops_agent", action="deploy_failed",
                  detail=f"Syntax hatası (İNSAN İNCELEMESİ ÖNERİLİR): {syntax_errors}")
        return state

    state["deploy_status"] = "deployed"
    log_step(
        state, agent="devops_agent", action="deployed",
        detail=f"{len(deployed_files)} dosya {project_dir} dizinine deploy edildi "
               f"({entrypoint_stack or 'stack tespit edilemedi'}).",
    )

    if not entrypoint_path:
        state["test_environment_url"] = None
        return state

    if _port_is_open(app_port):
        url = f"http://localhost:{app_port}"
        state["test_environment_url"] = url
        log_step(state, agent="devops_agent", action="test_env_already_running",
                  detail=f"Port {app_port} zaten açık (önceki sprint'ten olabilir): {url}")
        return state

    python_exe = None
    if entrypoint_stack in ("flask", "fastapi"):
        python_exe, env_msg = _ensure_python_env(project_dir, entrypoint_stack, all_py_content)
        log_step(state, agent="devops_agent", action="python_env_prepared", detail=env_msg)
        if not python_exe:
            state["test_environment_url"] = None
            log_step(state, agent="devops_agent", action="test_env_start_failed",
                      detail=f"Python ortamı hazırlanamadı ({env_msg}). Kod deploy edildi ama başlatılamadı.")
            return state

    elif entrypoint_stack == "express":
        global NODE_PATH, NPM_PATH
        if not NODE_PATH:
            if ALLOW_SYSTEM_INSTALL:
                log_step(state, agent="devops_agent", action="node_auto_install_attempt",
                          detail="ALLOW_SYSTEM_INSTALL=1, Node.js otomatik kurulmaya çalışılıyor...")
                if _try_auto_install_node():
                    NODE_PATH = shutil.which("node")
                    NPM_PATH = shutil.which("npm")

            if not NODE_PATH:
                state["test_environment_url"] = None
                log_step(state, agent="devops_agent", action="test_env_start_failed",
                          detail=f"Node.js sistemde kurulu değil. Kurmak için: {_node_install_hint()}\n"
                                 f"(Otomatik kurulmasını istersen: export ALLOW_SYSTEM_INSTALL=1). "
                                 f"Kod deploy edildi ama başlatılamadı.")
                return state
        npm_ok, npm_msg = _npm_install_if_needed(project_dir)
        log_step(state, agent="devops_agent", action="npm_install_checked", detail=npm_msg)
        if not npm_ok:
            state["test_environment_url"] = None
            log_step(state, agent="devops_agent", action="test_env_start_failed",
                      detail=f"npm install başarısız ({npm_msg}). Kod deploy edildi ama başlatılamadı.")
            return state

    url, stderr_tail = _try_start_app(entrypoint_path, entrypoint_stack, python_exe, project_dir, app_port)
    state["test_environment_url"] = url
    if url:
        log_step(state, agent="devops_agent", action="test_env_started", detail=f"{entrypoint_stack}: {url}")
    else:
        detail = (f"{entrypoint_stack} ortamı hazırlandı ama uygulama başlatılamadı. "
                 f"Log dosyaları: {project_dir}/app_stderr.log")
        if stderr_tail.strip():
            detail += f"\n--- Hatanın son satırları ---\n{stderr_tail.strip()}"
        log_step(state, agent="devops_agent", action="test_env_start_failed", detail=detail)

    return state
