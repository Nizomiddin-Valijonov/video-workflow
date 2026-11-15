# VideoWorkflow Studio — to'liq hisobot

**Sana:** 2026-10-28
**Mahsulot:** VideoWorkflow Studio 2.0.0 — video yoki brauzerdagi
harakatlardan qayta ishlatiladigan avtomatlashtirish shabloni.
**Tarqatish:** Windows uchun bitta faylli `dist/VideoWorkflow-Setup.exe`
(Docker, Python yoki Node.js talab qilinmaydi). Ilova WebView2 native
oynasida ochiladi; brauzer ishlatilmaydi.

---

## 1. Arxitektura

| Qism | Texnologiya | Vazifasi |
|---|---|---|
| Backend | FastAPI + SQLAlchemy 2 (async) + SQLite | video/batch/recorder API, WebSocket loglar |
| Frontend | Next.js 14 + Tailwind (statik `out/`) | dashboard, job sahifasi, `/record` |
| Desktop | Go launcher + WebView2 | native oyna, backend'ni boshqarish |
| Installer | Go + ichki `payload.zip` | `%LOCALAPPDATA%\VideoWorkflow` ga o'rnatish |
| Runtime | CPython 3.12.8 (embeddable) + Playwright Chromium | tashqi o'rnatishlarsiz ishlash |
| AI | OpenRouter `google/gemini-2.5-flash` yoki Ollama | video kadrlaridan qadamlar |

Backend faqat `127.0.0.1:8000` da tinglaydi. Docker steki (postgres,
redis, RQ workerlar) faqat dev uchun — tarqatiladigan paketga kirmaydi.

---

## 2. Asosiy imkoniyatlar (eski + yangi)

### 2.1 Video → shablon (eski oqim)
- Video yuklash (`mp4/mov/webm/mkv`, 500 MB gacha, 600 s gacha).
- Kadrlarni farq bo'yicha tanlash (2000 kadrgacha), AI orqali
  `WorkflowStep` qadamlari: `navigate`, `click`, `type`, `select`,
  `select2`, `check`, `setvalue`, `upload`, `wait`, `submit`,
  `verify_filled`, `verify_saved`, `press`.
- Qadamlarni UI'da tahrirlash, qayta tahlil qilish, ishga tushirish,
  WebSocket orqali real vaqt loglar.
- Har qadam uchun skrinshot, xatoda `fail_<i>.png`.

### 2.2 Brauzer harakatlarini yozib olish (yangi, 2026-10-28)
- `/record` sahifasida URL va brauzer tanlanadi; ko'rinadigan oyna
  ochiladi, foydalanuvchi saytda ishlaydi.
- Bosish, matn kiritish (600 ms debounce), `select`/checkbox, Enter,
  fayl yuklash, sahifa o'tishlari va **iframe** ichidagi harakatlar
  yoziladi (`meta.frame_url` orqali qayta bajariladi).
- Selector: `id`, `data-*`, `aria-label`, `name`, keyin ota-element
  yo'li; o'ynashda `_find_smart` 12 xil fallback strategiyasini sinaydi.
- Saqlashda `VideoJob(source="recorded", status=analyzed)` va
  `WorkflowStep`lar yaratiladi; oddiy runner bilan qayta bajariladi.
- Har hodisaga JPEG skrinshot (`storage/screenshots/<session_id>/`).
- Tez ketma-ket bosishlar dedupe qilinadi (`meta._marker`).

**Maxfiylik:** parol maydoni `{{PASSWORD}}`, login-formadagi login
`{{LOGIN}}`, fayl maydoni `{{FILE}}` bo'lib qoladi — qiymat brauzerdan
chiqmaydi. URL query/fragmentdagi `password`, `parol`, `token`,
`session`, `auth`, `code`, `otp`, `key` va h.k. olib tashlanadi
(`sanitize_url`).

**API:** `POST /api/recorder/start`, `GET /api/recorder/active`,
`GET /api/recorder/{id}`, `POST /api/recorder/{id}/stop`,
`DELETE /api/recorder/{id}`. Bir vaqtda bitta faol sessiya.

### 2.3 Batch / Excel (eski oqim)
- `online-mahalla.uz` uchun OneID login → ko'cha jadvali → xonadonlar →
  Excel'dan har biriga 5 kishidan qo'shish → SAQLASH.
- Excel/CSV import: pinpp, familiya, ism, otasining ismi, tug'ilgan
  sana, pasport, manzil, tuman (9 guruh alias). Majburiy: PINPP va
  tug'ilgan sana. 400 000 qatorga mo'ljallangan.
- Xonadonlar soni cheklanmagan (0 = barcha ko'chalar). Joriy ko'cha
  tugagach keyingisi avtomatik ochiladi.
- Sayt limiti: xonadonda 20 a'zodan oshsa qo'shib bo'lmaydi.
- PINFL limiti: 90 soniyada 3 ta so'rov; holat `batch_state`da.
- Sessiya: `storage/batch_session_<job>.json` (cookie/localStorage),
  avtomatik qayta login.

**Batch parametrlari:** `START_ROW`, `HOUSEHOLDS_LIMIT` (0 = cheksiz),
`PEOPLE_PER_HOUSEHOLD` (5), `START_STREET` (ixtiyoriy), `REUSE_SESSION`.

**Ro'yxatdan ro'yxatga, ko'chadan ko'chaga avtomatik o'tish (2026-10-28):**
- Xonadonlar jadvalining barcha sahifalari ochiladi; «keyingi sahifa»
  ishlamasa sahifa raqami bo'yicha zaxira bosish qo'llanadi.
- Ko'chalar ro'yxatining barcha sahifalari o'qiladi; ko'cha tugagach
  keyingisi avtomatik ochiladi.
- Ro'yxat oxirida bir marta qayta o'qiladi — o'qilmagan sahifa bo'lsa
  batch davom etadi.
- `street_cursor` holatda saqlanadi: to'xtatilgan run ro'yxat boshidan
  emas, qolgan joyidan davom etadi.
- Sayt bir xil ko'chaga qaytarib yuborsa, uch marta urinishdan keyin
  aylanish to'xtatiladi (run osilib qolmaydi).
- Sayt sessiyasi tugasa, batch avtomatik qayta login qilib, ko'chani
  yana ochadi — uzoq runlar to'xtab qolmaydi.
- Kod: `backend/workers/batch.py` (`_read_streets`,
  `_collect_households`, `_next_table_page`,
  `_click_numbered_table_page`, `_run_batch`).

### 2.4 Xavfsizlik
- JWT (UI) / `x-api-key` (tashqi API), CORS, TrustedHost, rate limit,
  HSTS/CSP/X-Frame-Options/nosniff.
- OneID LOGIN/PASSWORD faqat lokal Batch UI'da kiritiladi; backend
  jarayoni xotirasida saqlanadi va login muvaffaqiyatli bo'lgach
  tozalanadi. `.env`, `.env.bak*`, `storage/batch_session_*.json` va
  batch natijalari begonalarga berilmaydi.
- Batch natijalari va Excel bazasi shaxsiy ma'lumot (PINPP, pasport,
  telefon) saqlaydi — ularni ham ulashmang.

---

## 3. Eski ma'lumotlar — ilovaga qo'shildi (bugungi ish)

O'rnatilgan ilova bazasida avval faqat bugungi brauzer yozuvi bor edi.
Endi eski batch ishi ham qo'shildi va **yangi yozuv saqlanib qoldi**.

### 3.1 Job `65db58f6-078d-4154-9181-102bf765eac7`
- Nomi: `online-mahalla batch`, status `analyzed`, manba `video`.
- **30 ta qadam** (OneID login → ko'cha → xonadonlar → a'zo qo'shish
  → saqlash → tekshirish).
- Joblar ro'yxatida bugungi `Brauzer yozuvi — online-mahalla.uz`
  (12 qadam) bilan birga ko'rinadi.

### 3.2 Batch holati va natijalari
- `storage/batch_state_65db58f6-...json`:
  `used_pinpps=30`, `registered_pinpps=148`, `failed_pinpps=23`,
  `pinfl_lookups=56`, `rate_limit_hits=0`, `last_row=262770`;
  7 xonadon yozuvi.
- `storage/batch_result_65db58f6-...{md,csv,json}` (2026-10-21
  16:43 snapshot): **30/160** qo'shilgan, **5/32** xonadon to'liq,
  **1** imkonsiz xonadon, 54 PINFL qidiruvi.
- Xonadonlar: #1–#4 va #6 — 5/5; #7 — 2/5; #5 — 3/5 (22 a'zo, sayt
  limiti 20). Maksimal mumkin: **158/160**.
- Skrinshotlar: 30 ta JPEG
  (`storage/screenshots/65db58f6-.../000.jpg` … `029.jpg`) qadamlarga
  bog'landi — job sahifasida ochiladi.
- Excel bazasi: `storage/uploads/ХАТЛОВ.sqlite` — **413 240 qator**;
  `/api/excel/datasets` da `ХАТЛОВ` sifatida ko'rinadi.
- Davom ettirish: xonadonlar soni (0 = cheksiz), har biriga 5 kishi,
  boshlanish qatori 262655, ixtiyoriy boshlanish ko'chasi.

### 3.3 Kodda qo'llab-quvvatlash
- `backend/app/local_seed.py` endi zaxira faylni `storage/`da topmasa
  `backend/seed/job_65db58f6_steps_save_backup.json` dan oladi.
- Seed vaqtida mavjud skrinshotlar qadamlarga avtomatik bog'lanadi.
- `backend/seed/` manba va bundle'ga qo'shildi (10.8 KB, shaxsiy
  ma'lumot yo'q — faqat qadamlar).

---

## 4. Tekshiruv natijalari (2026-10-28)

### 4.1 Brauzer yozuvchisi — o'rnatilgan ilova
```
start 201 → session 9958f0ae…
actions: type #login, type #parol, submit #submit
current_url: …/page2.html
LEAK False (apiuser/apipass yo'q)
stop 200 → job source=recorded, steps [navigate, type, type, submit]
LEAK_JOB False
screenshot 200 image/jpeg 9028 B
cleanup 204
```

### 4.2 Eski ma'lumotlar — o'rnatilgan ilova
```
GET /api/jobs → 2 job:
  online-mahalla batch — 30 qadam
  Brauzer yozuvi — online-mahalla.uz — 12 qadam
GET /api/jobs/65db…/steps             → 200
GET /api/jobs/65db…/screenshot/0      → 200 image/jpeg
GET /api/jobs/65db…/screenshot/29     → 200 image/jpeg
GET /api/excel/datasets               → 200 {"id":"ХАТЛОВ","rows":413240}
GET /health                           → 200
```

### 4.3 Avvalgi tekshiruvlar
- Saqlangan shablonni runner orqali qayta ijro etish: type
  `{{LOGIN}}`/`{{PASSWORD}}` + submit → sahifa o'tdi.
- Installer ustiga o'rnatish: 5887 fayl, `storage/`, `data/`,
  `app/backend/.env` saqlandi; health 200; `/record/` 200.
- Frontend TypeScript tekshiruvi (`tsc --noEmit`) — xatosiz.

### 4.4 To'liq tizim tekshiruvi (2026-10-28, 15:34–15:41)

`C:\temp\check_all.py` — **35/35 PASS, 0 fail**:

| Guruh | Natija |
|---|---|
| `/health` | `ok=true`, development, v2.0.0 |
| OpenAPI | 23 endpoint |
| Frontend `/`, `/job`, `/login`, `/register`, `/record` | 200, HTML |
| `GET /api/jobs` | 2 job (30 va 12 qadam) |
| job detail / steps / runs / input-requirements | 200 |
| screenshot endpoint | 2/2 `image/*` |
| Excel datasets | 1 ta — ХАТЛОВ, 413 240 qator |
| Excel search | 200 |
| noto'g'ri login | 401 |
| recorder active | `active=false` |
| WebSocket `/api/ws/jobs/…` | ulanish ochiq |
| SQLite | 3 jadval, integrity `ok` |
| storage | 1 state, 3 natija, 56 screenshot |
| manba ↔ o'rnatilgan nusxa | 6 fayl hash bir xil |
| `backend.log` | 0 xato |

Brauzer (haqiqiy Chromium) avto-o'tish testlari:
```
pagination   1 → 2 → 3 → to'xtadi (4-sahifa yo'q)   ✔
streets      3 sahifadan 9/9 ko'cha                 ✔
households   3 sahifadan 12/12 xonadon              ✔
```

Ilova qayta ishga tushirildi: `VideoWorkflow.exe` + WebView2 (12 jarayon),
UI yuklandi (`/`, `/job`, `/login` — 200).

### 4.5 Jonli batch sinovi — haqiqiy OneID (2026-10-28, 15:53–15:59)

`C:\temp\batch_test.py` — lokal API orqali haqiqiy hisob bilan, run
`dd577dfa-989d-4229-a871-043bc0f126e4` (job `65db58f6…`), 3 xonadon × 1 odam:

| Tekshiruv | Natija |
|---|---|
| OneID login (haqiqiy) | ✔ 7 qadam bajarildi, sessiya saqlandi |
| Ko'chalar jadvali | ✔ 66 qator o'qildi, 1-sahifa |
| Xonadonlar jadvali | ✔ 32 xonadon yig'ildi |
| Avval bajarilganlarni o'tkazish | ✔ 7 xonadon (`#1…#6`, `#9`) o'tkazildi |
| Yangi odam qo'shish | ✔ **3 ta** saqlandi (`#7`, `#8`, `#10`) |
| Boshqa xonadondagi PINFL | ✔ 4 ta rad javobi to'g'ri ushlanib, keyingi nomzodga o'tildi |
| PINFL limiti (3/90 s) | ✔ kutish ishladi, `rate_limit_hits=0` |
| Holat fayli | ✔ `used_pinpps` 30→33, `registered_pinpps` 148→164 |
| Run yakuni | `completed`, `error` bo'sh |
| Natija fayllari | `.md` + `.csv` (34 qator) yangilandi |
| **Parol bazada** | **yo'q** — `inputs` kalitlari: `BATCH_MODE, DATASET, HOUSEHOLDS_LIMIT, PEOPLE_PER_HOUSEHOLD, REUSE_SESSION, START_STREET` |
| `backend.log` | ⚠️ 400 javoblari bor — bular saytning «allaqachon ro'yxatga olingan» javobi, kodda ushlangan; traceback yo'q |

Jonli sinovda **sinovdan o'tmagan**: ko'p sahifali jadval (bu ko'chada 32
xonadon bitta sahifaga sig'di) va ko'cha→ko'cha o'tish (3 ta odam birinchi
ko'chada yig'ildi). Bular haqiqiy Chromium fixture'lari bilan 4.4-jadvalda
tasdiqlangan.

---

## 5. Tarix (xronologiya)

| Sana | Voqea |
|---|---|
| 2026-10-16…19 | bir nechta video yuklandi (44 MB nusxalar) |
| 2026-10-19 | `docs/TUSHUNTIRISH.md`: `host.docker.internal` DNS fix, OpenRouter/gemini-2.5-flash, OCR o'chirildi, `networkidle`; job `bbe53d9e` (81.4 s, 34 kadr, 31 qadam) |
| 2026-10-20 | `ХАТЛОВ.csv` (133 MB) va `ХАТЛОВ.sqlite` (145.7 MB) yuklandi |
| 2026-10-21 13:08 | job `65db58f6` yaratildi (30 qadam) |
| 2026-10-21 16:43 | batch natijasi: 30/160, 5/32, 54 PINFL qidiruvi |
| 2026-10-22 | `dist/BATCH_HOLATI.md` yozildi |
| 2026-10-25 15:43 | `Excel-Fix` — openpyxl/et_xmlfile qo'shildi |
| 2026-10-25 16:30 | `StartStreet` — boshlanish ko'chasi maydoni |
| 2026-10-25 17:25 | `ExcelHeaders` — sarlavha aliaslari (qayta yuklash kerak) |
| 2026-10-25 18:13 | `StreetFix` — boshqa tuman ko'chasini tashlash |
| 2026-10-25 20:43 | `batch_state`: 30 used, 148 registered, 23 failed, last_row 262770 |
| 2026-10-27 | desktop launcher/installer yig'ildi, `.remember` loglari |
| 2026-10-28 | brauzer recorder qo'shildi; installer qayta yig'ildi; **eski ma'lumotlar ilovaga qo'shildi** |
| 2026-10-28 15:29 | **ro'yxatdan ro'yxatga, ko'chadan ko'chaga avtomatik o'tish** (batch.py) o'rnatilgan ilovaga qo'shildi |
| 2026-10-28 15:41 | to'liq tizim tekshiruvi — 35/35 PASS |
| 2026-10-28 15:43 | `VideoWorkflow-Update-AutoAdvance.zip` patch yasaldi va sinovdan o'tdi |
| 2026-10-28 15:53–15:59 | **jonli batch sinovi — haqiqiy OneID**: login, 66 ko'cha, 32 xonadon, 3 odam qo'shildi, parol bazada yo'q |
| 2026-10-28 17:39 | **Setup qayta yig'ildi**: payload ichida 30 qadamli seed bor; boshqa kompyuterda ham job ko'rinadi |
| 2026-11-03 | **Yozilgan shablon qayta o'ynash tuzatildi**: login (Enter) tuganguncha kutish — `navigate` endi OneID/`login?code=` zanjirini uzmaydi |
| 2026-11-03 | **Tarqatish paketi uchun yig'ish quvuri**: `tools/build_release.py` (toza bundle + shaxsiy ma'lumot filtri), `tools/smoke_test.py` (port 8123'da to'liq sinov), `BUILD-HANDOVER.cmd` |

### Eski yangilanish paketlari
| Paket | Sana | Mazmuni |
|---|---|---|
| `VideoWorkflow-Excel-Fix.zip` | 10-25 15:43 | runtime'ga openpyxl 3.1.5 + et_xmlfile 1.1.0 |
| `VideoWorkflow-Update-StartStreet.zip` | 10-25 16:30 | `Boshlanish ko'chasi (ixtiyoriy)` maydoni |
| `VideoWorkflow-Update-ExcelHeaders.zip` | 10-25 17:25 | `Tug'ilgan sana`, `Ф.И.Ш.` va boshqa sarlavhalar |
| `VideoWorkflow-Update-StreetFix.zip` | 10-25 18:13 | boshqa tuman ko'chasi URL'ini tashlash |

---

## 6. Artefaktlar (o'zgarmagan)

| Fayl | Hajm (bayt) | SHA256 |
|---|---|---|
| `dist/VideoWorkflow-Setup.exe` | 432 689 664 | `796ab09a612e0d9674d91a24c46051b236479ffbac5525f46f19357956d1705f` |
| `dist/VideoWorkflow-Windows-x64-NoDocker.zip` | 430 387 442 | `805dfbd959dddbaa3932f5cf4bb8507dc3a7ba37e39bf6a053730446b1baaa2b` |
| `dist/VideoWorkflow-Update-AutoAdvance.zip` | 34 421 | `7cabc89e649c119c58443b4e4b83ca9f8bd5099494b72ea27890cd40ffbdfc67` |

Namunali yangilanish paketlari `dist/SHA256SUMS-NoDocker.txt` da.
`desktop/setup/payload.zip` (411 MB, qayta yasaladi) diskni bo'shatish
uchun o'chirilgan. C: da ~158 MB bo'sh — installer'ni qayta yig'ish
uchun `C:\temp\vwbundle` (1.06 GB) ni o'chirish kerak.

> **Diqqat:** `VideoWorkflow-Setup.exe` va NoDocker zip 14:17–14:18 da
> yig'ilgan, avto-o'tish tuzatishi esa 15:29 da qilingan. Qayta
> o'rnatgandan keyin `VideoWorkflow-Update-AutoAdvance.zip` ni qo'llash
> kerak (yoki disk bo'shatib installer'ni qayta yig'ish).
>
> Installer bugungi ma'lumot importidan keyin **qayta yig'ilmadi** —
> eski job/natijalar o'rnatilgan ilovaning `data/` va `storage/`
> papkalariga qo'shildi va keyingi yangilanishlarda saqlanadi. Yangi
> kompyuterda faqat 30 qadamli shablon (seed) tiklanadi; batch
> natijalari va Excel bazasi installer ichida emas (shaxsiy ma'lumot).

---

## 7. Muhim yo'llar (o'rnatilgan ilova)

```
%LOCALAPPDATA%\VideoWorkflow\
  VideoWorkflow.exe
  data\app.db                 — joblar (2 ta)
  storage\screenshots\        — eski job + yangi yozuv
  storage\batch_state_65db….json
  storage\batch_result_65db….{md,csv,json}
  storage\uploads\ХАТЛОВ.sqlite
  BATCH_HOLATI.md             — holat va davom ettirish
  README.txt
```

Boshqarish: `VideoWorkflow.exe --status | --stop | --restart | --logs`.

---

## 8. Boshqa kompyuterga berish (handover)

### Nima yuboriladi

`dist/README.txt` har yig'ishdan keyin avtomatik yangilanadi va ichida
aynan qaysi fayl yuborilishi yoziladi. Ikki holat bor:

1. **Go o'rnatilgan bo'lsa** — `dist/VideoWorkflow-Setup.exe` (bitta fayl,
   ikki marta bosiladi).
2. **Go bo'lmasa** — `dist/VideoWorkflow-Windows-x64-<sana>.zip`
   (ichida `Install.cmd` bor: oching → ikki marta bosing).

Eski `VideoWorkflow-Setup.exe` Go topilmaganda avtomatik
`VideoWorkflow-Setup-ESKI-<sana>.exe` deb nomlanadi — uni **yubormang**,
u 2026-10-28 dagi kodni o'z ichiga oladi (login-kutish tuzatishisiz).

### Yig'ish

```
BUILD-HANDOVER.cmd            (ikki marta bosing)
```

yoki qo'lda (faqat standart kutubxona kerak):

```
"%LOCALAPPDATA%\VideoWorkflow\runtime\python\python.exe" -I tools/build_release.py --smoke
```

Quvur nima qiladi:

1. `dist/VideoWorkflow-Windows-x64-NoDocker.zip` dan `runtime/`,
   `browsers/`, `VideoWorkflow.exe` ni oladi (Python, Chromium qayta
   yuklanmaydi).
2. Ustiga repo'dagi joriy kodni qo'yadi: `backend/` → `app/backend/`,
   `frontend/out/` → `app/frontend/out/`, `packaging/` → `Install.cmd`,
   `Uninstall.cmd`, `Sozlamalar.cmd`, `README*.txt`.
3. 30 qadamli shablonni `seed/` va `storage/` ga qo'shadi;
   skrinshot havolalarini tozalaydi (yangi kompyuterda fayl yo'q).
4. `storage/` va `data/` ni bo'sh qoldiradi.
5. **Shaxsiy ma'lumot filtri**: `batch_session*`, `batch_result*`,
   `batch_state*`, `users.json`, `.env` (namunalardan tashqari),
   `*.sqlite`, `*.db`, `*.log`, `*.pid`, `downloads/` — topilsa
   yig'ish to'xtaydi.
6. `tools/smoke_test.py` — paketni vaqtinchalik papkaga ochadi,
   ichidagi Python bilan backend'ni **8123** portda ko'taradi,
   `/health`, statik frontend va `workflow_steps` jadvalini tekshiradi.
   Ishlab turgan ilovaga (8000) tegmaydi.

### Tarqatishdan oldin nazorat ro'yxati

- [ ] `BUILD-HANDOVER.cmd` oxirida «BARCHA TEKSHIRUVLAR O'TDI» chiqdi.
- [ ] `dist/README.txt` dagi SHA256 yuborilayotgan fayl bilan mos.
- [ ] Yuborilayotgan ZIP ichida `storage/batch_*`, `data/app.db`,
      `app/backend/.env` **yo'q** (smoke test buni tekshiradi).
- [ ] `app/backend/workers/runner.py` ichida `_wait_after_navigation`
      bor (smoke test tekshiradi).

### Qabul qiluvchi kompyuterida

1. `Install.cmd` (yoki Setup.exe) → dastur `%LOCALAPPDATA%\VideoWorkflow`
   ga o'rnatiladi.
2. Ilova ochiladi → «Ro'yxatdan o'tish» → lokal hisob.
3. Job sahifasida 30 qadamli namunali shablon ko'rinadi.
4. O'z ishini yozib olish: «Yozib olish» → brauzer oynasi → harakatlar →
   «To'xtatib saqlash» → «Ishga tushirish».

### Ma'lum cheklovlar

- WebView2 Runtime Windows 11'da standart; Windows 10'da Edge bilan
  keladi. Bo'lmasa launcher brauzer rejimiga o'tadi.
- Imzo yo'q — SmartScreen «More info → Run anyway» so'rashi mumkin.
- Antivirus `chrome.exe`/`python.exe` ni bloklashi mumkin — papkani
  istisnoga qo'shish kerak.
