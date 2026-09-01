
---

## Sprint 1 — 2026-08-21 08:26 UTC

### Bu sprintte yapılanlar
- Kullanıcı kaydı ve giriş (`US-1`, high)
- Görev oluşturma ve düzenleme (`US-2`, high)
- Görev arama ve filtreleme (`US-3`, medium)

### Teknoloji kararı
FastAPI — Kullanıcı kaydı ve giriş, görev oluşturma ve düzenleme gibi işlemler için birden fazla CRUD endpoint'i gereklidir. Endpoint sayısı arttıkça ve karmaşıklaştıkça FastAPI daha uygun olacak. Veri doğrulama ihtiyacı yüksek ve kullanıcı girdisi validasyonu kritik olduğundan, otomatik veri doğrulama sağlayacak FastAPI tercih edilir. Frontend tarafında da basit formlar olduğu için HTML+Tailwind yeterli.

### Kalite süreci
- Review revizyon turu: 0
- QA consensus turu: 1
- Deploy durumu: failed

### Sıradaki sprint için plan
Bekleyen backlog'dan öncelikli adaylar:
- Görevlerin etiketlendirilmesi (`US-5`, medium)
- Görevlerin önceliklendirilmesi (`US-6`, medium)

(Toplam 2 özellik vizyon backlog'unda bekliyor.)
