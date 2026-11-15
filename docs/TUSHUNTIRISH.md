# VideoWorkflow Studio — muammo va yechim tushuntirishi

**Sana:** 19.10.2026
**Job:** `bbe53d9e-f420-45bc-8f81-9ec42896516c` — `2026-10-04_17-31-54.mp4` (81.4 sek, 34 ta muhim kadr)

---

## 1. Xato nima edi?

Analyzer logida har bir kadr uchun shunday xato chiqardi:

```
❌ AI xato: HTTPConnectionPool(host='host.docker.internal', port=11434):
Max retries exceeded with url: /api/generate
(Caused by NameResolutionError: Failed to resolve 'host.docker.internal'
 ([Errno -2] Name or service not known))
```

**Tarjimasi:** analyzer konteyneri AI modeliga (Ollama) `host.docker.internal`
manzili orqali ulanmoqchi edi, lekin bu nom Linux'dagi docker konteyneri ichida
umuman mavjud emas → har bir kadr tahlili bir zumda yiqilardi → video
"analyzing" holatida qotib qolar va hech qanday qadam yaratilmasdi.

**Nega shunday bo'lgan?**

- `.env` faylida AI sozlamalari shunday edi:
  `AI_PROVIDER=ollama`, `AI_BASE_URL=http://host.docker.internal:11434`
- `host.docker.internal` — bu nom faqat Mac va Windows uchun Docker Desktop'da
  avtomatik mavjud. **Linux'da esa uni konteynerga qo'lda ko'rsatish kerak**,
  aks holda DNS topilmaydi.

## 2. Nima tuzatildi?

1. **`docker-compose.yml` — `extra_hosts` qo'shildi** (analyzer va runner uchun):

   ```yaml
   extra_hosts:
     - "host.docker.internal:host-gateway"
   ```

   Endi konteyner ichida `host.docker.internal` host shlyuziga (masalan
   172.17.0.1) aylanadi va hostdagi Ollama'ga yetib boradi.

2. **AI bulutga o'tkazildi (tezlik uchun)** — `backend/.env`:

   ```
   AI_PROVIDER=openrouter
   AI_MODEL=google/gemini-2.5-flash
   OPENROUTER_API_KEY=...
   ```

   Sabab: mahalliy `minicpm-v:8b` CPU'da bitta kadrni **3-5 daqiqada** tahlil
   qilardi (34 kadr ≈ soatlab). `gemini-2.5-flash` bulutda bitta kadrni
   ~5 sekundda bajaradi.
   Analyzer kodiga OpenRouter/OpenAI-mos API (`/chat/completions`)
   qo'llab-quvvatlash qo'shildi — avval faqat Ollama (`/api/generate`) bor edi.
   *(Mahalliy Ollama rejimi `.env`da izoh ko'rinishida qoldirilgan — kerak bo'lsa
   qayta yoqish mumkin.)*

3. **OCR o'chirildi** — `USE_OCR=false`.
   Tesseract bu kompyuterda bitta kadr uchun ~3 daqiqa sarflayotgan edi.
   Gemini rasmdagi matnni o'zi o'qiy oladi, shuning uchun OCR endi shart emas.

4. **Runner yaxshilandi** — SPA (Vue) saytlar to'liq yuklanishini kutish
   (`networkidle`) qo'shildi. Avval sahifa hali chizilmaganda qidiruv boshlanib,
   elementlar "topilmadi" bo'lardi.

## 3. Hozirgi holat

| Bosqich | Holat |
|---|---|
| Video tahlili | ✅ **31 ta qadam** aniqlandi (online-mahalla.uz: xonadonlar xatlovi, so'rovnoma) |
| Run mexanizmi | ✅ Brauzer ochildi, saytga o'tdi, loglar va screenshotlar olindi |
| To'liq run | ⚠️ 2-qadamda to'xtadi — sayt **tizimga kirishni (login) talab qiladi** |

Run natijasi: `storage/runs/15aa2cc7-1018-48f5-9fd8-44ec03dcfbb0/`
(`before_first_step.png`, `fail_1.png` — login sahifasi ko'rinadi).

## 4. To'liq run uchun (login qadamlari qo'shildi)

Workflow boshiga 5 ta qadam qo'shildi:

```
#00 navigate → https://online-mahalla.uz/login
#01 type     → Login   ({{LOGIN}})
#02 type     → Parol   ({{PASSWORD}})
#03 click    → "Тизимга кириш"
#04 wait     → 3 sekund
```

Frontendda **"Workflowni ishga tushirish"** tugmasini bosganda **LOGIN** va
**PASSWORD** maydonlari avtomatik chiqadi — akkaunt ma'lumotlarini o'sha yerda
kiritasiz. Parol bazaga saqlanmaydi va loglarga yozilmaydi.

> ⚠️ **Eslatma:** workflow ichida "Сақлаш" (saqlash) tugmalarini bosish qadamlari
> bor — real akkaunt bilan ehtiyot bo'ling. Kerak bo'lsa qadamlarni interfeysda
> tahrirlash yoki o'chirish mumkin.

## 5. Umumiy sxema

```
Brauzer ──► Backend API ──► PostgreSQL (joblar, qadamlar)
   ▲            │
   │            ├─► Redis: analysis navbati ─► Analyzer ─► AI (OpenRouter/Ollama) ─► qadamlar
   │            └─► Redis: runs navbati     ─► Runner (Playwright) ─► loglar/screenshotlar
   └────────── WebSocket (real vaqt loglar) ◄────────────────────────────┘
```

Batafsil rasm: **`docs/sxema.png`** — ochish uchun: `xdg-open docs/sxema.png`

Rasmda 1) tahlil oqimi, 2) run oqimi va 3) `host.docker.internal` muammosining
"oldin ❌ / endi ✅" holati ko'rsatilgan.

## 6. Fayllar

| Fayl | Vazifasi |
|---|---|
| `docker-compose.yml` | `extra_hosts` — host.docker.internal yechimi |
| `backend/.env` | AI provayder sozlamalari (openrouter / ollama) |
| `backend/workers/analyzer.py` | AI provayder tanlash (ollama yoki openai-mos) |
| `backend/workers/runner.py` | SPA kutish (`networkidle`) |
| `backend/app/core/config.py` | `USE_OCR` sozlamasi |
| `docs/generate_schema.py` | Sxema-rasmni chizuvchi skript |
| `docs/sxema.png` | Sxema rasmi (2000×1470) |
