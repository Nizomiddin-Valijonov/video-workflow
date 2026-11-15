# VideoWorkflow Studio (video yoki brauzer → qayta ishlatiladigan shablon)

## Windows desktop ilova — tavsiya etiladi

`dist/VideoWorkflow-Setup.exe` — bitta faylli o'rnatuvchi. Unda Python
runtime, Chromium, Firefox, WebKit, barcha kutubxonalar va SQLite baza
ichida. Docker, Python yoki Node o'rnatish shart emas.

1. `VideoWorkflow-Setup.exe` ni ishga tushiring.
2. Dastur `%LOCALAPPDATA%\VideoWorkflow` ga o'rnatiladi va alohida
   native oynada (WebView2) ochiladi — brauzer ishlatilmaydi.

Portable variant: `dist/VideoWorkflow-Windows-x64-NoDocker.zip` ni
ochib, `VideoWorkflow.exe` ni ishlating (o'rnatmasdan). Batafsil:
`packaging/README-Windows.txt`.

Windows bo'lmagan muhitda ishlab chiqish uchun (Python/Node
o'rnatilgan bo'lsa): `scripts/start_local.sh` — bu faqat dasturchi
rejimi, tarqatiladigan mahsulot emas.

### Boshqa kompyuterga ko'chirish

- Windows: faqat `dist/VideoWorkflow-Setup.exe` ni ko'chiring. Setup,
  Python runtime va brauzerlarni o'zi bilan olib keladi.
- Linux/macOS (dasturchi rejimi): loyiha papkasini ko'chirib,
  `bash scripts/start_local.sh` ishga tushiring. Talab: **Python
  3.11–3.14** (3.12 tavsiya etiladi) va **Node.js 20+**. Skript virtual
  muhit, kutubxonalar va brauzerlarni o'zi tayyorlaydi.
- `.venv-local/`, `frontend/node_modules/`, `frontend/.next/` va
  `.playwright-browsers/` ni ko'chirish shart emas — ular qayta yaratiladi.
  Agar ular ko'chirilgan bo'lsa, skript boshqa OS/arxitekturada eski
  yig'ilmani avtomatik qayta yig'adi.
- `backend/.env` va `backend/.env.bak*` fayllarida API kalitlar bo'lishi
  mumkin. Begona kompyuterga yuborishdan oldin ularni o'chiring yoki
  kalitlarni almashtiring.
- Yangi kompyuterda toza baza kerak bo'lsa `data/` va `storage/` ni
  ko'chirmang. Batch holatini davom ettirmoqchi bo'lsangiz, `storage/`
  papkasini saqlang.

## Docker bilan ishlab chiqish (ixtiyoriy)

Mahsulot Windows desktop ilova sifatida tarqatiladi. Backend/frontend
ustida Docker Compose bilan ishlash uchun quyidagi bo'limlardagi
qo'llanmadan foydalaning. Docker steki tarqatiladigan paketga kirmaydi.

## Brauzer harakatlarini yozib olish (video talab qilinmaydi)

Ish stolidagi ilovada **«Yozib olish»** sahifasi (`/record`) bor. U yerda
boshlang'ich URL va brauzer tanlanadi; dastur ko'rinadigan alohida oyna
ochadi. Foydalanuvchi saytda odatdagidek ishlaydi — bosish, matn kiritish,
`select`/checkbox, fayl yuklash, Enter va sahifa o'tishlari avtomatik
yoziladi. «To'xtatib saqlash» bosilganda harakatlar `VideoJob
(source="recorded")` va `WorkflowStep` qadamlariga aylanadi; ular mavjud
runner bilan xuddi video tahlilidan chiqqan shablon kabi qayta bajariladi.

- `POST /api/recorder/start` — sessiya ochish (`url`, `browser`).
- `GET /api/recorder/active`, `GET /api/recorder/{id}` — jonli holat.
- `POST /api/recorder/{id}/stop` — shablonni saqlash.
- `DELETE /api/recorder/{id}` — yozuvni bekor qilish.

Yozuvda bir vaqtda faqat bitta faol sessiya bo'ladi. Ichki `iframe`
elementlari ham qamrab olinadi (`meta.frame_url` orqali o'ynaladi).

Xavfsizlik: parol maydonlari va login-formadagi login qiymati brauzerdan
chiqmaydi — shablonda `{{PASSWORD}}` / `{{LOGIN}}` bo'lib qoladi va har
ishga tushirishda UI'dan so'raladi. Yozilgan URL'lardan `password`,
`parol`, `token`, `session`, `code` kabi maxfiy query/fragment parametrlari
olib tashlanadi, shuning uchun GET-forma yoki OAuth havolalari shablonga
sirni yozib qoldirmaydi.

## Eski batch ma'lumotlari (oldindan qo'shilgan)

Desktop rejimda (LOCAL_MODE) ilova ishga tushganda eski
`online-mahalla batch` job'i — `65db58f6-078d-4154-9181-102bf765eac7` —
bazaga bir marta qo'shiladi, agar u hali yo'q bo'lsa. 30 qadamli shablon
`backend/seed/job_65db58f6_steps_save_backup.json` dan olinadi; storage
papkasidagi nusxa ustuvor. Skrinshotlar mavjud bo'lsa qadamlarga
avtomatik bog'lanadi (`storage/screenshots/<job_id>/NNN.jpg`).

Batch holati va natijalari `storage/` ichida saqlanadi:
`batch_state_<job_id>.json`, `batch_result_<job_id>.{md,csv,json}`.
Excel bazasi — `storage/uploads/ХАТЛОВ.sqlite`. Bu fayllar shaxsiy
ma'lumot saqlaydi, ularni begonalarga bermang.

## Batch: ro'yxatdan ro'yxatga avtomatik o'tish

Batch ishida qo'lda aralashish kerak emas:

- **Jadval sahifalari** (xonadonlar ro'yxati) — har bir sahifa avtomatik
  ochiladi (100+ xonadon bo'lsa ham), «keyingi sahifa» tugmasi ishlamasa
  sahifa raqami bo'yicha zaxira urinish qilinadi.
- **Ko'chalar ro'yxati** — barcha sahifalar o'qiladi; bitta ko'cha
  tugagach keyingisi avtomatik ochiladi. Ro'yxat oxiriga yetganda bir
  marta qayta o'qiladi — o'qilmagan sahifa bo'lsa, davom etadi.
- **Qayta ishga tushirish** — progress `storage/batch_state_<job>.json`
  dagi `street_cursor` orqali saqlanadi, run ro'yxat boshidan
  boshlanmaydi.
- Xonadonlar soni `0` bo'lsa — ko'chalar yoki Excel'dagi odamlar
  tugaguncha davom etadi.

## Tashkiliy tarkib
- `backend/` — FastAPI (Python 3.12): video yuklash, AI tahlil (VLM), workflow boshqaruvi, WebSocket loglar.
- `frontend/` — Next.js 14 + Tailwind: dashboard, yuklash, bosqichlar ro'yxati, ishga tushirish, real vaqt loglar.
- `storage/` — videolar, screenshots, run screenshots (volume).
- `desktop/` — Docker'siz Windows/Linux launcher (Go) va bitta faylli installer.
- `packaging/` — Windows README, `Install.cmd`, `Uninstall.cmd`, `Sozlamalar.cmd`.
- `scripts/start_local.sh` — Linux/macOS uchun Docker'siz ishga tushirish.
- Docker: postgres, redis, backend, analyzer (RQ worker), runner (RQ worker + Playwright), frontend.

## Brauzerlar

Desktop oyna WebView2 orqali lokal backend (`127.0.0.1:8000`) bilan
ishlaydi; alohida veb-server yoki Node.js jarayoni talab qilinmaydi.
Frontend statik yig'ilma (`frontend/out`) sifatida backend ichidan
beriladi.

Har bir run oldidan **Avtomatizatsiya brauzeri** maydonida quyidagilardan
birini tanlash mumkin:

- Chromium, Firefox yoki WebKit — Playwright brauzerlari;
- Google Chrome yoki Microsoft Edge — kompyuterda o'rnatilgan dastur.

WebKit Safari yadrosidagi alohida test brauzeri; macOS'dagi Safari
ilovasining o'zini avtomatlashtirmaydi. Docker image uchala Playwright
engine'ini o'rnatadi. `scripts/start_local.sh` ham ularni loyiha ichidagi
`.playwright-browsers/` papkasiga yuklaydi. Qo'lda o'rnatish:

```bash
python -m playwright install chromium firefox webkit
```

Faqat kerakli engine'larni yuklash uchun `PLAYWRIGHT_BROWSERS="chromium"`
kabi qiymat berish yoki yuklashni `INSTALL_PLAYWRIGHT_BROWSERS=0` bilan
o'chirish mumkin. Linux'da WebKit/Firefox uchun tizim kutubxonalari yetishmasa,
`INSTALL_PLAYWRIGHT_DEPS=1 scripts/start_local.sh` ularni ham o'rnatadi
(bu rejim administrator huquqini so'rashi mumkin).

## Ishga tushirish (Docker — dasturchi rejimi)

```bash
cp backend/.env.example backend/.env
# kerakli kalitlarni to'ldiring (OPENROUTER_API_KEY yoki OLLAMA sozlamalari)
docker compose up --build -d
```

- API: `http://localhost:8000`
- Frontend (faqat dev): `http://localhost:3000`
- Docs (Swagger): `http://localhost:8000/docs`

Production'da `API_KEY` to'ldirilsa, ilova UI'da `Ro'yxatdan o'tish`
yoki `Kirish` orqali JWT token olinadi va keyingi so'rovlar shu token
bilan yuboriladi. Tashqi skriptlar `x-api-key` header'idan foydalanishi mumkin.

## Asosiy endpointlar

| Ro'yxat | Ma'nosi |
|---|---|
| `POST /api/jobs` | Video yuklash va tahlilga yuborish |
| `GET /api/jobs` | Joblar ro'yxati |
| `GET /api/jobs/{id}` | Job va aniqlangan bosqichlar |
| `GET /api/jobs/{id}/input-requirements` | Foydalanuvchidan so'raladigan maydonlar |
| `GET /api/jobs/{id}/steps` | Bosqichlar (tahrirlash mumkin) |
| `PATCH /api/steps/{id}` | Bosqichni tahrirlash |
| `POST /api/jobs/{id}/run` | Workflowni ishga tushirish (inputlar bilan) |
| `GET /api/runs/{id}` | Run holati |
| `WS /api/ws/jobs/{id}` | Real vaqt loglar/status |

## Xavfsizlik

- `SECRET_KEY`, `API_KEY` (ixtiyoriy, productionda tavsiya etiladi).
- Brauzer UI JWT orqali, tashqi API `x-api-key` orqali ishlaydi.
- CORS faqat ruxsat berilgan originlar.
- Security headers: HSTS, CSP, X-Frame-Options, X-Content-Type-Options.
- Rate limit (in-memory), TrustedHost middleware.
- Yuklash hajmi va video MIME turi tekshiriladi.
- `.env` faylini va `storage/batch_session_*.json` ni begonalarga bermang.
