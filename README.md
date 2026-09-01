# Agent Squad — Adım 1: PM Agent + İskelet

## Kurulum (kendi makinende)

Ollama'nın kurulu ve çalışır olması gerekir:

```bash
# Ollama zaten kurulu değilse: https://ollama.com
ollama pull qwen2.5        # ya da tercih ettiğin başka bir model (llama3.x vb.)
ollama serve                # arka planda çalışsın (genelde otomatik başlar)
```

Python ortamı:

```bash
python3 -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

İsteğe bağlı — farklı bir model/host kullanmak istersen:

```bash
export OLLAMA_MODEL="llama3.1"          # varsayılan: qwen2.5
export OLLAMA_HOST="http://localhost:11434"  # varsayılan zaten bu
```

## Çalıştırma

```bash
python -m graph.build_graph
```

**Not:** 12GB VRAM ile qwen2.5 (7B/14B) ve llama3.1 8B rahat çalışır. Daha büyük
varyantları (14B üstü, özellikle JSON-ağırlıklı yapılandırılmış çıktı görevlerinde)
denemeden önce VRAM kullanımını izlemekte fayda var — PM Agent'ın JSON formatını
bozmadan üretmesi model kalitesine oldukça duyarlı.

Bu komut, `graph/build_graph.py` içindeki örnek fikri ("basit not uygulaması") PM Agent'a
gönderir, gerçek bir backlog ürettirir ve Orchestrator placeholder node'unda konsola yazdırır.

## Kendi fikrini denemek için

`graph/build_graph.py` dosyasının sonundaki `initial_state["idea"]` satırını değiştir.
Kompleks bir fikir de deneyebilirsin (örn. "Instagram'ın basit bir web klonunu yap") —
PM Agent'ın bunu MVP kapsamına indirgeyip indirgemediğini `pm_notes` alanından gözlemleyebilirsin.

## Dosya yapısı

```
agent-squad/
├── graph/
│   ├── state.py         # Sprint state şeması — işbirliği/log/squad alanları baştan tanımlı
│   └── build_graph.py   # pm_agent <-> designer_agent <-> tech_lead_agent -> dev_agent -> orchestrator
├── agents/
│   ├── pm_agent.py        # idea -> GENİŞ ürün vizyonu (15-20 özellik) -> bunun içinden MVP seçimi
│   │                       # (backlog) + gelecek sprint'ler için bekleyenler (future_backlog)
│   ├── designer_agent.py  # backlog -> UX akışı, + PM'e soru sorma, + Tech Lead'in talebine revizyon
│   ├── tech_lead_agent.py # backlog+design -> task breakdown + GERÇEK teknoloji kararı
│   │                       # (Flask/FastAPI/Express arasından kriterlere göre seçer, gerekçesini loglar)
│   ├── dev_agent.py       # backend_dev_agent_node + frontend_dev_agent_node (ortak çekirdek)
│   │                       # AYNI SEVİYEDEKİ bağımsız task'lar PARALEL (thread havuzu) işlenir
│   │                       # backend, Tech Lead'in seçtiği stack'e göre .py ya da .js üretir
│   │                       # Reviewer'dan red gelirse SADECE ilgili task'ı feedback'le yeniden yazar
│   ├── reviewer_agent.py  # dev_outputs -> onay/red, red -> ilgili dev'e geri döngü (max 2 tur)
│   ├── qa_agents.py       # qa_parallel_node: 3 QA agent GERÇEKTEN paralel (thread havuzu) çalışır
│   │                       # (BAĞIMSIZ, birbirini görmez) + qa_consensus_node — sadece 3/3 "pass" ise geçer
│   ├── devops_agent.py    # QA sign-off sonrası HER PROJE için ayrı klasör (deploy_output/<slug>/)
│   │                       # oluşturur, Python için kendi venv'ini kurup gerekli paketleri
│   │                       # (flask/fastapi/uvicorn) OTOMATİK kurar, Express için npm install çalıştırır,
│   │                       # sonra Flask/FastAPI/Express'i tespit edip GERÇEKTEN başlatır
│   ├── sprint_documenter.py # Her sprint sonunda proje klasöründe docs/ altına KALICI belgeler yazar:
│   │                       # product-vision.md, sprint-log.md (append), backlog.md, tech-decisions.md
│   ├── slack_notifier.py  # Sprint özeti + stack + test ortamı + gelecek sprint adaylarını Slack'e basar
│   └── llm_utils.py       # Tüm agent'ların kullandığı ortak retry katmanı (bkz. aşağıdaki not)
│   └── logger.py          # Ortak conversation_log altyapısı — her agent kararını buraya yazar
├── tests/                 # (sonraki adımda dolacak)
└── requirements.txt
```

## İşbirliği döngüleri nasıl çalışıyor

`clarification_target` alanı kimin cevap vermesi gerektiğini belirler:
- **Designer -> PM**: story belirsizse PM'e geri döner, PM revize eder.
- **Tech Lead -> Designer**: UX akışı teknik tasarım için yetersizse Designer'a geri döner.
- **Reviewer -> Backend/Frontend Dev**: kod reddedilirse ilgili dev agent'a geri döner,
  sadece reddedilen task'lar (feedback ile) yeniden yazılır — onaylanmış task'lar dokunulmadan kalır.
- **QA Consensus -> Backend/Frontend Dev (Reviewer üzerinden)**: 3 bağımsız QA agent'ın
  TAMAMI "pass" demezse (3/3 şartı), o story'nin tüm task'ları yeniden Reviewer'a düşecek
  şekilde işaretlenir ve Dev'den başlayarak tekrar akar. Bu, Reviewer'ın gözden kaçırdığı
  mantık hatalarını yakalamak için ikinci bir güvenlik katmanı — mock testte Reviewer'ın
  sözdizim olarak onayladığı ama eksik iş mantığı içeren bir kod, 3 QA agent tarafından da
  bağımsız olarak yakalandı.

Her döngü kendi sayaçıyla (backlog/design, review, QA) sınırlı — sonsuz döngüye girmeden,
limit aşılırsa elindeki bilgiyle devam edilir (bu durum ciddi bir uyarı olarak loglanır).

## Gerçek çalıştırmada görülen sorun ve düzeltme (qwen2.5, Windows/Ollama)

İlk gerçek testte (Ollama + qwen2.5), sistem gerçek bir sprint akışını başarıyla yürüttü —
Reviewer eksik endpoint'i yakaladı, QA consensus Reviewer'ın kaçırdığı bir mantık hatasını
yakalayıp otomatik geri döngü başlattı. Ama bir noktada model **bozuk çıktı üretti** (yarım
cümlenin ardından aniden Çince metne kayıp JSON'u kırdı) ve sistem çöktü.

Bunun üzerine `agents/llm_utils.py` eklendi: tüm agent'lar artık ham `ollama.chat()` yerine
`call_ollama_json()` kullanıyor — JSON parse başarısız olursa modele hatasını gösterip 2 kez
daha dener, hepsi başarısız olursa net bir hata mesajıyla durur (sessizce yanlış veri üretmez).
Ayrıca QA agent'ların ürettiği `status` alanı artık şema dışı bir değer (örn. gerçek testte
görülen `"not_tested"`) döndürürse güvenli tarafta kalınıp otomatik `"fail"` sayılıyor —
consensus mekanizmasının bütünlüğünü bozmasın diye.

`conversation_log` tüm akışı (kim, ne yaptı, neden, kime yöneldi) sırayla tutar; sprint
sonunda tam olarak yazdırılır. İleride Slack'e basılacak.

## Soru sorma / öneri kanalı (kesintisiz, kod'a müdahale değil)

`.intervene` "dur, karar ver" demekken, bu farklı: **"durma, ama şunu PM'e sor, cevabı görmek
istiyorum"**. Bir özelliği anlamadıysan ya da yeni bir fikir geldiyse, sprint çalışırken
başka bir terminalden:

```bash
echo "Bildirim özelliği eklemeyi düşünüyor musunuz?" > .question
```

Bu, Designer, Tech Lead, Backend/Frontend Dev, Reviewer, QA, DevOps adımlarının HER
BİRİNİN başında kontrol ediliyor — yani sürecin en uzun sürdüğü noktalarda bile sorun
uzun süre beklemeden yakalanır. PM (mevcut backlog/vizyon/stack bağlamını görerek) gerçek
bir cevap üretir, konsola basılır ve kalıcı loglanır. **Pipeline durmaz** — sen okurken
ekip çalışmaya devam eder.

## Sen artık sürece dahil olabiliyorsun (checkin noktaları)

**Varsayılan davranış: sistem hiç durmaz.** Sprint 3 noktaya (planning, escalation, retro)
uğrar ama her seferinde sağduyulu bir varsayılan kararla ANINDA geçer — seni beklemez,
process'i yeniden başlatman gerekmez.

**Müdahale etmek istediğinde** (sprint hâlâ çalışırken), **başka bir terminalden**:
```bash
touch .intervene
```
Bu bir bayrak dosyası bırakır. Çalışmakta olan process, **bir sonraki** checkin noktasına
geldiğinde (anlık değil, ama sprint'i baştan başlatmana gerek kalmadan) bunu görür, bayrağı
siler (tek seferlik — bir daha durmaz), ve gerçekten durup terminal girdini bekler:

1. **Planning onayı**: `move US-5` (vizyon backlog'undan bu sprint'e çek), `remove US-2`
   (bu sprint'ten çıkar), ya da Enter (olduğu gibi onayla).
2. **Escalation** (Reviewer/QA kendi başına çözemezse): `retry` + opsiyonel not, ya da
   Enter (`continue`).
3. **Retro**: serbest bir not — `docs/sprint-log.md`'ye kalıcı yazılır.

**Kalıcılık:** Checkpoint `sprint_checkpoints.sqlite`'ta — process kapansa bile (gerçekten
durduğu bir noktadaysa) aynı `thread_id` ile tekrar çalıştırınca kaldığı yerden devam eder.

**Sırada:** Slack token'ları geldiğinde `.intervene` dosyası yerine "Slack'te yeni mesaj
var mı" kontrolü gelecek, cevap da terminal yerine Slack'ten okunacak — checkin node'larının
kendisi ve routing hiç değişmeyecek.

## Gerçek çalıştırmada bulunan 4 sorun (2 proje aynı anda, gerçek qwen2.5 ile)

İki projeyi gerçekten aynı anda çalıştırdığında (paralellik testi başarılıydı — çakışma yok)
4 ayrı gerçek bug/zayıflık ortaya çıktı, hepsini düzelttim:

1. **[KRİTİK] QA consensus sessizce yanlış geçiyordu.** Her iki logda da QA agent'lar sık sık
   boş sonuç (`"results": []`) döndürüyordu — muhtemelen büyük kod bağlamı küçük local
   modeli zorluyor. Eski kod, sadece QA'nın CEVAP VERDİĞİ story'leri kontrol ediyordu; QA
   hiç cevap vermezse o story hiç listeye girmiyor, sessizce "3/3 UYUM ✅" sayılıyordu —
   yani **hiçbir şey test edilmeden** sistem "geçti" diyebiliyordu. Düzelttim: artık referans
   QA'nın söyledikleri değil, **backlog'un kendisi** — her story için gerçekten 3 "pass"
   doğrulanmadıkça (QA'nın sessiz kalması dahil) "fail" sayılıyor. Test ettim: QA'nın hep boş
   döndüğü bir senaryoda artık doğru şekilde "BAŞARISIZ" deyip escalate ediyor.
   Ayrıca QA prompt'una "değerlendirmen gereken TÜM story_id'ler" listesi eklendi ve eksik
   kalırsa bir kez daha (net bir hatırlatmayla) deneniyor.

2. **[Önemli] Çoklu backend dosyası mimarisi kırıktı.** Tech Lead backend'i birden fazla
   dosyaya bölüyor (auth.py, tasks.py, search.py) ama her dosya kendi başlatma kodunu
   (`uvicorn.run()`) içeriyordu — DevOps de "entrypoint"i içerik taramasıyla (hangi dosyada
   run() çağrısı var) buluyordu, bu da hangi dosyanın gerçek giriş noktası olduğunu
   belirsizleştiriyordu (muhtemelen syntax hatalarının bir kaynağı). Düzelttim: `TaskItem`'a
   `is_entrypoint` alanı eklendi — Tech Lead artık SADECE BİR backend task'ı ana giriş noktası
   olarak işaretliyor, diğerleri (route modülleri) başlatma kodu İÇERMEMELİ. DevOps artık
   içerik taramasına değil bu işarete güveniyor (işaretlenmezse eski davranışa geri düşüyor).
   Test ettim: çok dosyalı bir senaryoda artık doğru dosya seçiliyor, gerçekten başlatılıyor.

3. **[Küçük] `package.json` halüsinasyonu.** Flask/FastAPI (Python) task'ları bile bazen
   `package.json` üretiyordu — Node.js dosyası, Python projede anlamsız. Prompt'u
   güçlendirdim VE DevOps'a bir güvenlik ağı ekledim: stack Express değilse `package.json`
   otomatik olarak atlanıyor (deploy'u bozmadan, log'a not düşülerek).

4. **[Küçük] pip install 180 saniyede timeout oluyordu.** Paketler toplu kuruluyordu, biri
   yavaş/sorunlu olunca hepsi patlıyordu. Artık **tek tek** kuruluyor — biri başarısız olsa
   bile diğerleri kurulmaya devam ediyor, sadece stack'in KENDİSİ (flask/fastapi) kurulamazsa
   fatal sayılıyor.

## Kullanım — artık THREAD_ID elle atamana gerek yok

```bash
# İnteraktif mod (fikrini sorar, yarım kalan sprintleri listeler)
python -m graph.build_graph

# Doğrudan mod — fikri argüman olarak ver, hemen başlar, thread_id OTOMATİK üretilir
python -m graph.build_graph "Basit bir görev takip uygulaması"
```

**Birden fazla projeyi aynı anda çalıştırmak için** artık hiçbir şey elle atamana gerek yok
— sadece farklı terminallerde farklı fikirlerle çalıştır:
```bash
# Terminal 1
python -m graph.build_graph "Görev takip uygulaması"

# Terminal 2 (aynı anda)
python -m graph.build_graph "Alışveriş listesi uygulaması"
```
Her ikisi de kendi benzersiz thread'ini (`sprint-e290fd1b` gibi) otomatik üretir, kendi
proje klasörüne, kendi portuna, kendi flag dosyalarına sahip olur. **Gerçek iki process ile
test ettim** (subprocess olarak paralel çalıştırdım) — sıfır çakışma, doğru ayrı klasörlere
deploy oldular.

**Yarım kalmış bir sprint varsa:** argümansız çalıştırınca script bunları otomatik listeler
(hangi thread_id'ler yarım kaldığını checkpoint DB'den kendisi bulur, senin ezberlemene
gerek yok):
```
>>> Yarım kalmış sprint(ler) bulundu:
    - sprint-e290fd1b
    - sprint-682129ff
Hangisine devam etmek istersin? (Enter: ilkini seç):
```

## Ekip aynı anda birden fazla projede çalışabilir

Agent kodunu klonlamana gerek yok — mimari zaten buna hazır (hiçbir agent global state
tutmuyor, her şey `state` üzerinden akıyor). Thread_id'yi de artık elle atamana gerek yok
(bkz. yukarıdaki "Kullanım" bölümü) — sadece farklı terminallerde farklı fikirlerle çalıştır:

```bash
# Terminal 1
python -m graph.build_graph "Proje A fikri"

# Terminal 2 (aynı anda)
python -m graph.build_graph "Proje B fikri"
```

Bunun sorunsuz çalışması için iki çakışma noktasını düzelttim:
- **Flag dosyaları thread-bazlı** (`.intervene_sprint-e290fd1b` gibi, thread_id script
  başlarken ekrana yazdırılır) — hangi terminalde `touch .intervene_<thread_id>` yazarsan,
  sadece o proje durur, diğeri etkilenmez.
- **Port proje slug'ından deterministik hesaplanıyor** (aynı proje her zaman aynı portu
  alır — devam sprint'lerinde tutarlılık için — farklı projeler farklı portlar alır,
  test ettim: `kitap-kulubu` → 5112, `not-uygulamasi` → 5147, çakışma yok).

Checkpoint (`sprint_checkpoints.sqlite`) ve deploy klasörü (`deploy_output/`) zaten ortak
kullanılabiliyor — SQLite eşzamanlı okuma/yazmayı destekliyor, her proje kendi klasöründe
izole. Yani gerçekten paralel, birbirinden habersiz çalışan bir "ekip" elde ediyorsun.

**Gerçekten test ettim** (iki projeyi thread'lerle aynı anda çalıştırarak — iki ayrı terminal
process'inin yapacağının bir simülasyonu): ilk denemede gerçek bir race condition buldum —
syntax kontrolü sabit bir geçici dosya adı (`/tmp/_syntax_check.py`) kullanıyordu, iki proje
aynı anda kontrol yaparken biri diğerinin dosyasını siliyordu. Düzelttim (artık her çağrı
`uuid4` ile benzersiz bir dosya adı kullanıyor) — düzeltme sonrası iki proje **hatasız**,
doğru ayrı klasörlere (`proje-a/`, `proje-b/`) deploy oldu.

## Farklı fikir / aynı fikri güncelleme — ne oluyor?

**Yarım kalmış bir sprint varsa:** script hiçbir şey sormaz, direkt kaldığı yerden devam
eder — o an yeni fikir giremezsin (önce mevcut sprint bitmeli). Farklı bir proje başlatmak
istiyorsan `THREAD_ID` env değişkenini değiştirip ayrı bir thread ile çalıştırman gerekir.

**Önceki sprint tamamlanmışsa:** artık script mevcut projeleri listeler:
```
>>> Mevcut projeler:
    - Kitap Kulübü (slug: kitap-kulubu, 1 bekleyen özellik)

Yeni bir proje için fikrini yaz, YA DA mevcut bir projeye devam etmek için slug'ını yaz:
```
- **Yeni bir fikir yazarsan** → tamamen yeni bir proje, PM sıfırdan vizyon üretir.
- **Mevcut bir projenin slug'ını yazarsan** (örn. `kitap-kulubu`) → PM SIFIRDAN vizyon
  üretmez, önceki sprint'in vizyonunu ve `future_backlog`'unu gerçekten kullanır, oradan
  2-4 story seçer. İstersen ek bir not da girebilirsin ("ayrıca puanlama da eklensin mi").
  Bu, test ettim: PM'e önceki vizyon gerçekten iletiliyor, aynı proje klasörüne
  (`deploy_output/<slug>/`) devam ediyor, `docs/sprint-log.md`'de Sprint 2 olarak ekleniyor.

Bu mekanizma `docs/project_state.json`'a dayanıyor — her sprint sonunda vizyon, tamamlanan
backlog, bekleyen backlog ve stack kararı buraya kalıcı yazılıyor.

## Kaldığı sprintten devam etme

Script artık çalıştığında önce "bu thread'de yarım kalmış bir sprint var mı" diye bakıyor.
Crash olursa (ya da terminali kapatırsan), bir dahaki sefere aynı komutu çalıştırdığında
**PM'den değil, kaldığı node'dan** devam ediyor — checkpoint (`sprint_checkpoints.sqlite`)
tam olarak nerede kaldığını biliyor. Test ettim: Designer'da bilerek crash simüle ettim,
ikinci çalıştırmada PM hiç tekrar çağrılmadı, sistem doğrudan Designer'dan devam etti.

Farklı bir proje başlatmak için elle bir şey atamana gerek yok — `python -m graph.build_graph
"yeni fikir"` her seferinde kendi benzersiz thread'ini otomatik üretir, hiçbir zaman
mevcut yarım kalmış bir sprint'in üzerine yazmaz.

## Proje adları artık düzgün

Önceden tüm CEO cümlesi slug'lanıyordu (Türkçe karakterler silinince anlamsız bir isim
çıkıyordu — örn. "kullan-c-lar-not-ekleyip..."). Artık PM, vizyon üretirken kısa bir
**proje adı** da üretiyor (`project_name`, örn. "Kitap Kulübü") ve slug bundan türetiliyor
(`kitap-kulubu`). Türkçe karakterler de artık doğru dönüştürülüyor (ı→i, ç→c, ğ→g, vs.).

## Node.js eksikse ne oluyor

Express seçilirse ve Node.js sistemde kurulu değilse, artık işletim sistemine göre **tam
kurulum komutu** veriliyor (Windows: `winget install OpenJS.NodeJS.LTS`, Mac: `brew install
node`, Linux: `sudo apt install nodejs npm`). Python paketlerinin aksine (izole venv'e
kurulur), Node.js bir sistem çalışma zamanı — bu yüzden otomatik kurulum varsayılan olarak
KAPALI (sistem seviyesinde bir şey kurmak senin oluruna bağlı olmalı). İstersen açabilirsin:
```bash
export ALLOW_SYSTEM_INSTALL=1
```
Bu durumda DevOps Agent Windows/Mac'te otomatik kurmayı dener (Linux'ta sudo gerektirdiği
için otomatik denenmiyor, komutu sana gösterir).

## Postman collection otomatik üretiliyor

Her sprint sonunda `docs/postman_collection.json` yazılıyor — backend'in `dev_notes`'undaki
API kontratlarından üretilen, Postman'a doğrudan import edebileceğin bir başlangıç
collection'ı. Method/URL detayları LLM'in dev_notes'ta yazdığına bağlı olduğu için elle
düzeltme gerekebilir ama sıfırdan yazmaktan çok daha hızlı bir başlangıç noktası.

**JMeter** için otomatik üretim henüz yok — JMeter'ın .jmx formatı (concurrency, ramp-up gibi
yük testi parametreleri gerektirir) Postman'dan daha karmaşık, ayrı bir adımda ele alınabilir
istersen.

## PM artık vizyoner düşünüyor

CEO'nun basit fikri iki aşamada işleniyor:
1. **Vizyon:** PM, fikirden 15-20 maddelik geniş bir özellik listesi üretir (`product_vision`)
   — sadece temel CRUD değil, auth, arama, bildirim, paylaşım gibi gerçek bir ürünün
   büyüdüğünde kazanacağı özellikleri düşünür.
2. **MVP seçimi:** Bu vizyondan, ilk sprint'e sığacak 2-4 story seçilir (`backlog`), geri
   kalanı `future_backlog`'a yazılır — bir sonraki sprint'te oradan devam edilir.

## Sprint dokümantasyonu (kalıcı)

`conversation_log`'un aksine (sadece o çalıştırmada, bellekte var), her sprint sonunda
proje klasörüne KALICI belgeler yazılır — `deploy_output/<proje>/docs/`:
- **product-vision.md** — PM'in geniş özellik listesi (ilk sprint'te bir kez yazılır)
- **sprint-log.md** — HER sprint sonunda eklenir (append): ne yapıldı, kalite süreci,
  deploy durumu, sıradaki sprint planı. Sprint numarası otomatik artar.
- **backlog.md** — güncel backlog + gelecek sprint'ler için bekleyenler (her sprint güncellenir)
- **tech-decisions.md** — Tech Lead'in stack kararları ve gerekçeleri, sprint sprint eklenir

Bu sayede aynı proje için ikinci bir sprint çalıştırdığında (`idea` metnini aynı bırakırsan
`project_slug` aynı kalır, klasör paylaşılır), agent'lar geçmiş sprint'lerin üzerine inşa
edebilir — Jira'nın basit bir versiyonu gibi düşünebilirsin, dosya tabanlı ve otomatik.

## Paralel çalışma

İki katmanda gerçek paralellik var (sıralı çağrının süslenmiş hali değil — gerçekten
aynı anda birden fazla LLM isteği gidiyor, thread havuzuyla):
- **Backend/Frontend Dev:** aynı bağımlılık seviyesindeki (birbirine depends_on ile
  bağlı olmayan) task'lar aynı anda işlenir. Örn. 3 bağımsız backend endpoint'i varsa
  3'ü de paralel yazılır, sırayla değil.
- **QA:** 3 QA agent zaten bağımsız tasarlanmıştı (birbirini görmüyorlardı) ama önceden
  sırayla (a→b→c) çağrılıyorlardı — artık gerçekten aynı anda çalışıyorlar.

`MAX_PARALLEL_DEV_WORKERS` env değişkeniyle dev paralellik derecesini ayarlayabilirsin
(varsayılan 3).

## Frontend artık daha profesyonel (Tailwind / React)

Frontend için de artık gerçek bir karar var — Tech Lead, backend stack kararıyla birlikte
frontend yaklaşımını da seçiyor:
- **HTML+Tailwind** (varsayılan) — düz HTML/JS + Tailwind CSS (CDN, build gerektirmez).
  Öncekinden çok daha modern/profesyonel görünür: spacing, renk, buton stilleri gerçek
  utility class'larla yapılır, çıplak HTML değil.
- **React (CDN)** — gerçek React, ama npm/build YOK: React+ReactDOM+Babel standalone CDN
  script'leriyle tek bir HTML dosyasında JSX yazılır. Kullanıcı etkileşimi karmaşıksa
  (çok state, sık güncellenen liste/form) Tech Lead bunu tercih eder.

React seçilirse, backend'in mutlaka `GET /` route'unda frontend'in HTML dosyasını servis
etmesi gerekiyor (`render_template("index.html")` gibi) — bu artık Tech Lead'in task
açıklamasında ve Backend Dev'in kurallarında açıkça isteniyor, yoksa React sayfası hiçbir
yerden açılamaz.

## Teknoloji seçimi nasıl çalışıyor

Tech Lead artık sabit bir stack'e zorlanmıyor — **Flask, FastAPI, Express (Node.js)**
arasından gerçek kriterlere göre seçim yapıyor: veri modeli karmaşıklığı, endpoint
sayısı, doğrulama ihtiyacı, frontend ile dil tutarlılığı, organizasyon kısıtı (varsa
`state["stack_constraints"]` ile belirtebilirsin), basitlik. Kararını gerekçesiyle
birlikte `tech_stack_decision` alanına yazıp loglar; bu bilgi Slack özetinde de görünür.

**Neden sadece bu üçü:** DevOps Agent bu üç stack'i gerçekten tespit edip başlatabiliyor
(port enjeksiyonu + gerekirse `npm install`). Java gibi build-sistemi gerektiren (Maven/
Gradle, JDK derleme adımı) stack'ler şimdilik desteklenmiyor — bu, mimarinin sınırı değil,
sırada bekleyen bir sonraki genişleme.

## Slack'e bağlamak (opsiyonel)

Slack workspace'inde bir [Incoming Webhook](https://api.slack.com/messaging/webhooks)
oluştur, sonra:

```bash
export SLACK_WEBHOOK_URL="https://hooks.slack.com/services/..."
```

Ayarlamazsan sistem aynı mesajı konsola basar — Slack kurmadan da akışı görebilirsin.

## Test ortamı nasıl görülür

Her proje kendi klasörüne izole ediliyor: `deploy_output/<proje-adı>/` (proje adı,
fikrinden otomatik türetilir — örn. "Not ekleme silme uygulaması" -> `not-ekleme-silme-uygulamasi/`).

**Ortam kurulumu artık tamamen otomatik:**
- **Flask/FastAPI:** DevOps Agent proje klasöründe kendi `venv/`'ini oluşturur, gerekli
  paketleri (flask, ya da fastapi+uvicorn) bu venv'e otomatik kurar. Sistem Python'una
  hiçbir şey kurulmaz — sen elle `pip install` yapmak zorunda değilsin.
- **Express:** DevOps Agent proje klasöründe otomatik `npm install` çalıştırır
  (Node.js'in kendisi sistemde kurulu olmalı — https://nodejs.org).

Sprint bitince **her zaman** (Slack ayarlı olsun olmasın) konsolda büyük, kutu içinde bir
özet görürsün:
```
============================================================
  🔗 TEST ORTAMI HAZIR: http://localhost:5050
     (proje klasörü: deploy_output/not-ekleme-silme-uygulamasi/)
============================================================
```
Başlatılamadıysa aynı kutuda neden başlatılamadığı ve dosyaların nerede olduğu yazar —
sessizce kaybolmaz.

**Tek önkoşul: Node.js.** Express seçilirse Node.js'in kendisi sistemde kurulu olmalı
(npm paketlerini biz otomatik kuruyoruz ama Node.js runtime'ının kendisini kuramayız).
Flask/FastAPI için hiçbir ön kurulum gerekmiyor artık — venv + pip otomatik hallediyor.

**Not:** Bu MVP versiyonunda başlatılan process kalıcı izlenmiyor — bir sonraki sprint
çalıştığında eski process hâlâ ayaktaysa onu tekrar kullanır (yeniden başlatmaz). Process'i
manuel durdurmak istersen: `pkill -f app.py` (Linux/Mac) ya da Görev Yöneticisi'nden
ilgili `python.exe` sürecini sonlandır (Windows).

## Sıradaki adım

Bu on bir agent (döngülü + loglu + consensus'lu + retry'li + paralel + gerçek deploy'lu +
kalıcı dokümantasyonlu haliyle) çalıştıktan ve senin fikirlerinle tekrar test edildikten sonra:
- **Adım 9:** SRE/Monitoring Agent (Prometheus/Grafana, runbook kapsamı, crash izleme)
- **Adım 10:** Çoklu squad mimarisi (kompleks fikir → tek squad kapasitesi yetmezse otomatik ölçeklenme)
- **(Ertelendi) Jira entegrasyonu:** Şu an dosya tabanlı sprint-log.md/backlog.md bunun basit bir
  yerel versiyonu — gerçek Jira API entegrasyonu daha sonra, mimari olgunlaştıkça ele alınacak.

Detaylı yol haritası için: `agent-takimi-plan.md`
