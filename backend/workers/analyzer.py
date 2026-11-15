"""Video tahlili — kadrlarni ajratish va AI orqali qadamlarni aniqlash."""
import os, cv2, base64, json, asyncio, sys, re, time, requests
import pytesseract
import numpy as np

from app.core.config import settings
from app.core.paths import resolve_storage_path
from app.db.session import SessionLocal
from app.models import VideoJob, WorkflowStep, JobStatus
from app.services.events import publish
from sqlalchemy import delete


def log(msg: str):
    print(f"[ANALYZER] {msg}", flush=True, file=sys.stderr)


FRAME_RATE = settings.FRAME_RATE
DIFF_THRESHOLD = settings.DIFF_THRESHOLD
MAX_FRAMES = settings.MAX_FRAMES
BETWEEN_FRAMES_DELAY = settings.BETWEEN_FRAMES_DELAY


SYSTEM_PROMPT = """You are a UI automation expert. Analyze the screenshot and describe ONE user action.

Reply ONLY with valid JSON:
{"action": {"type": "click|type|navigate|select|submit|wait|none", "target": "button/field name or URL", "value": "text to type (only for type)", "description": "short description"}}

Rules:
- click: button/link. target = visible text or aria-label.
- type: input field. target = field name/placeholder, value = "{{KEY}}" placeholder.
- navigate: page load. target = URL.
- select: dropdown.
- wait: pause.
- none: no meaningful action.
No markdown, no extra text."""


# Brauzer avtomatizatsiyasiga aloqasi yo'q (desktop) amallar
_OFFLINE_ACTION = re.compile(
    r"libreoffice|\bcalc\b|spreadsheet|task switcher|desktop app|application icon"
    r"|mozilla firefox|bring .*foreground|window title",
    re.I,
)
# LibreOffice/Excel katak selektorlari ("cell A1", "cell B262661", ...)
_SPREADSHEET_CELL = re.compile(r"^cell\s+[A-Z]{1,3}\d", re.I)
# Workflow davomida logout qilish kerak emas (xavfli)
_LOGOUT_ACTION = re.compile(r"чиқиш|чикиш|выход|logout|log out|sign out|chiqish", re.I)
# Turli shaklda yozilgan "close" bosishlarni birlashtirish uchun
_CLOSE_ACTION = re.compile(r"\bclose\b|\byopish\b", re.I)


def _frame_diff(a, b) -> float:
    ga = cv2.cvtColor(a, cv2.COLOR_BGR2GRAY)
    gb = cv2.cvtColor(b, cv2.COLOR_BGR2GRAY)
    return float(np.mean(cv2.absdiff(ga, gb))) / 255.0


class VideoUnavailable(RuntimeError):
    """Video fayli yo'q yoki o'qib bo'lmaydi."""


def _iter_keyframes(video_path: str):
    """Muhim kadrlarni birma-bir qaytaradi (generator).

    Ilgari barcha kadrlar to'liq o'lchamda ro'yxatga yig'ilardi va tahlil
    tugaguncha xotirada qolardi (600 kadr × ~6 MB ≈ 3.5 GB). Endi faqat
    joriy va solishtirish uchun oldingi kadr saqlanadi.
    """
    log(f"Kadrlar ajratilmoqda: {video_path}")
    if not os.path.exists(video_path):
        raise VideoUnavailable(f"Video topilmadi: {video_path}")
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise VideoUnavailable(f"Video ochilmadi: {video_path}")
    try:
        fps = cap.get(cv2.CAP_PROP_FPS) or 25
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        duration = total_frames / fps if fps > 0 else 0
        log(f"Video: {fps:.1f} FPS, {total_frames} kadr, {duration:.1f} sekund")
        limit = settings.MAX_VIDEO_DURATION_SEC
        if limit and duration > limit:
            raise VideoUnavailable(
                f"Video davomiyligi {duration:.0f} s — ruxsat etilgan {limit} s dan oshadi"
            )

        step = max(int(fps / FRAME_RATE), 1)
        count, last, idx = 0, None, 0
        while True:
            ok = cap.grab()
            if not ok:
                break
            if idx % step == 0:
                ok, frame = cap.retrieve()
                if not ok:
                    break
                if last is None or _frame_diff(last, frame) > DIFF_THRESHOLD:
                    yield frame.copy()
                    last = frame
                    count += 1
                    if count >= MAX_FRAMES:
                        break
            idx += 1
        log(f"✅ {count} ta muhim kadr ajratildi")
    finally:
        cap.release()


def _ocr(img) -> str:
    try:
        return pytesseract.image_to_string(img)
    except Exception:
        return ""


def _img_to_b64(img) -> str:
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 70])
    return base64.b64encode(buf).decode()


def _extract_json(raw) -> dict:
    if not raw or not isinstance(raw, str):
        return {"action": None}
    raw = raw.strip()
    if raw.startswith("```json"): raw = raw[7:]
    if raw.startswith("```"): raw = raw[3:]
    if raw.endswith("```"): raw = raw[:-3]
    raw = raw.strip()
    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        try:
            data = json.loads(raw[start:end+1])
            if isinstance(data, dict):
                if "action" in data and isinstance(data["action"], dict):
                    return data
                if "type" in data:
                    return {"action": data}
        except json.JSONDecodeError:
            pass
    return {"action": None}


_fallback_active = False


class _AIStatusError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def _call_openai(base: str, api_key: str, model: str, prompt: str, b64: str) -> str:
    resp = requests.post(
        f"{base}/chat/completions",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": model,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url",
                     "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                ],
            }],
            "max_tokens": settings.AI_MAX_TOKENS,
            "temperature": settings.AI_TEMPERATURE,
        },
        timeout=settings.AI_TIMEOUT,
    )
    if resp.status_code != 200:
        raise _AIStatusError(resp.status_code, f"HTTP {resp.status_code}: {resp.text[:200]}")
    return resp.json()["choices"][0]["message"]["content"]


def _analyze_frame(img, prev_desc: str) -> dict:
    b64 = _img_to_b64(img)
    prompt = SYSTEM_PROMPT
    if settings.USE_OCR:
        ocr_text = _ocr(img)[:500]
        if ocr_text.strip():
            prompt += f"\n\nVisible text (OCR):\n{ocr_text}"
    if prev_desc:
        prompt += f"\n\nPrevious context: {prev_desc}"

    global _fallback_active
    model = settings.AI_MODEL
    provider = (settings.AI_PROVIDER or "ollama").strip().lower()
    fallback = (settings.AI_FALLBACK_MODEL or "").strip()
    try:
        if provider == "ollama":
            resp = requests.post(
                f"{settings.AI_BASE_URL}/api/generate",
                json={
                    "model": model,
                    "prompt": prompt,
                    "images": [b64],
                    "stream": False,
                    "options": {
                        "temperature": settings.AI_TEMPERATURE,
                        "num_predict": settings.AI_MAX_TOKENS,
                    },
                },
                timeout=settings.AI_TIMEOUT,
            )
            resp.raise_for_status()
            raw = resp.json().get("response", "")
            log(f"   ✅ [{provider}:{model}] javob berdi")
        else:
            # OpenAI-mos API: openrouter, openai, groq va boshqalar
            if provider == "openrouter":
                base = (settings.OPENROUTER_BASE_URL or settings.AI_BASE_URL).rstrip("/")
                api_key = settings.OPENROUTER_API_KEY
            else:
                base = settings.AI_BASE_URL.rstrip("/")
                api_key = settings.OPENAI_API_KEY

            # Asosiy model ishlamasa (402/429), fallback modelga o'tamiz
            candidates = []
            if not _fallback_active:
                candidates.append(model)
            if fallback and fallback != model:
                candidates.append(fallback)
            if not candidates:
                candidates = [model]

            raw = None
            last_err = None
            for cand in candidates:
                for attempt in range(1, 4):
                    try:
                        raw = _call_openai(base, api_key, cand, prompt, b64)
                        log(f"   ✅ [{provider}:{cand}] javob berdi")
                        if cand != model:
                            _fallback_active = True
                        break
                    except Exception as e:
                        last_err = e
                        status = getattr(e, "status", None)
                        log(f"   ⚠️ [{cand}] urinish {attempt}/3: {str(e)[:160]}")
                        if status and status not in (429, 500, 502, 503, 504):
                            break  # bu model bilan davom etish ma'nosiz
                        time.sleep(10 * attempt)
                if raw is not None:
                    break
            if raw is None:
                raise last_err or RuntimeError("AI javob bermadi")
        result = _extract_json(raw)
        if isinstance(result, dict):
            result["_ok"] = True
        return result
    except Exception as e:
        log(f"   ❌ AI xato: {e}")
        return {"action": None, "_ok": False}


async def _notify(job_id: str, msg: dict):
    try:
        await publish(job_id, msg)
    except Exception as e:
        log(f"⚠️ WS xato: {e}")


async def _analyze(job_id: str):
    global _fallback_active
    _fallback_active = False
    log("_analyze boshlandi")
    async with SessionLocal() as db:
        job = await db.get(VideoJob, job_id)
        if not job:
            log(f"⚠️ Job topilmadi: {job_id}")
            return
        log(f"Job: {job.filename}, status={job.status}")
        job.status = JobStatus.ANALYZING
        await db.commit()
        await _notify(job_id, {"type": "status", "status": "analyzing"})

        try:
            video_path = resolve_storage_path(job.storage_path) or job.storage_path
            frames_seen = 0
            ai_ok = 0
            pending_steps: list[WorkflowStep] = []
            prev_desc = "boshlanish"
            order = 0
            last_action_key = None
            last_was_close = False

            for i, frame in enumerate(_iter_keyframes(video_path)):
                frames_seen = i + 1
                if frames_seen == 1:
                    await _notify(job_id, {"type": "log", "message": "Kadrlar tahlil qilinmoqda..."})
                log(f"── Kadr {i+1} ──")
                if i > 0 and BETWEEN_FRAMES_DELAY > 0:
                    time.sleep(BETWEEN_FRAMES_DELAY)

                try:
                    result = _analyze_frame(frame, prev_desc)
                except Exception as e:
                    log(f"❌ Kadr {i} xato: {e}")
                    continue

                if not isinstance(result, dict):
                    continue
                if result.get("_ok"):
                    ai_ok += 1
                # Birinchi bir necha kadr ham API xatosi bilan qaytsa,
                # 2000 kadrgacha kutib o'tirmaymiz — kalit/tarmoq
                # muammosi darhol ko'rinadi.
                if ai_ok == 0 and frames_seen >= 3:
                    raise RuntimeError(
                        "AI provayder hech qanday kadrga javob bermadi — "
                        "API kalitini va tarmoqni tekshiring"
                    )
                action = result.get("action")
                if not action or not isinstance(action, dict):
                    continue
                atype = action.get("type", "click")
                if atype in ("none", "null"):
                    log(f"⏭️ Kadr {i}: action='none', o'tkazib yuborildi")
                    continue

                target = (action.get("target") or "").strip()
                value = (action.get("value") or "").strip()
                desc = (action.get("description") or "").strip()

                # Brauzerdan tashqari (desktop) amallarni o'tkazib yuborish
                if _OFFLINE_ACTION.search(f"{target} {desc}") or _SPREADSHEET_CELL.match(target):
                    log(f"⏭️ Kadr {i}: brauzerdan tashqari amal, o'tkazib yuborildi ({target[:40]})")
                    continue
                # Logout amallari workflow uchun kerak emas
                if _LOGOUT_ACTION.search(target) or _LOGOUT_ACTION.search(desc):
                    log(f"⏭️ Kadr {i}: logout amali o'tkazib yuborildi ({target[:40]})")
                    continue
                # Juda noaniq selektorlar ("X", "x") foydasiz
                if len(target) < 2:
                    log(f"⏭️ Kadr {i}: noaniq selektor {target!r}, o'tkazib yuborildi")
                    continue
                # Ketma-ket takroriy amallarni birlashtirish
                # ("Close button...", "Close button on the dialog" kabi variantlar ham)
                norm = re.sub(r"\s+", " ", target.lower()).strip()
                is_close = bool(_CLOSE_ACTION.search(norm))
                action_key = (atype, norm, value)
                if action_key == last_action_key or (is_close and last_was_close):
                    log(f"⏭️ Kadr {i}: takroriy amal, o'tkazib yuborildi ({target[:40]})")
                    continue
                last_action_key = action_key
                last_was_close = is_close

                shot_dir = os.path.join(settings.STORAGE_DIR, "screenshots", job_id)
                os.makedirs(shot_dir, exist_ok=True)
                shot_path = os.path.join(shot_dir, f"{order:03d}.jpg")
                cv2.imwrite(shot_path, frame)

                step = WorkflowStep(
                    job_id=job_id,
                    order_index=order,
                    action=atype,
                    selector=target or None,
                    value_template=value or None,
                    description=desc,
                    screenshot_path=shot_path,
                    confidence=0.7,
                    meta={},
                )
                pending_steps.append(step)
                log(f"✅ Qadam #{order}: {step.action} — {step.description}")
                order += 1
                prev_desc = desc or prev_desc

                await _notify(job_id, {
                    "type": "step", "index": order - 1,
                    "action": step.action, "description": step.description,
                })

            await _notify(job_id, {"type": "log", "message": f"{frames_seen} ta kadr"})

            if frames_seen == 0:
                # O'qiladigan video, lekin o'zgargan kadr yo'q — avvalgidek
                # 0 qadam bilan yakunlaymiz.
                job.status = JobStatus.ANALYZED
                await db.commit()
                log("Kadr topilmadi — 0 qadam")
                await _notify(job_id, {"type": "status", "status": "analyzed", "steps": 0})
                return

            if ai_ok == 0:
                raise RuntimeError(
                    "AI provayder hech qanday kadrga javob bermadi — API kalitini tekshiring"
                )

            # Eski qadamlar faqat yangi tahlil muvaffaqiyatli bo'lgach
            # almashtiriladi (xato bo'lsa eski natija saqlanib qoladi).
            log("Eski qadamlar almashtirilmoqda...")
            await db.execute(delete(WorkflowStep).where(WorkflowStep.job_id == job_id))
            for step in pending_steps:
                db.add(step)
            job.status = JobStatus.ANALYZED
            await db.commit()
            log(f"═══ TUGADI: {order} ta qadam ═══")
            await _notify(job_id, {"type": "status", "status": "analyzed", "steps": order})

        except Exception as e:
            log(f"❌ XATO: {e}")
            import traceback
            traceback.print_exc()
            try:
                await db.rollback()
            except Exception:
                pass
            # Buzilgan sessiyada qayta commit qilishga urinmaymiz —
            # holatni alohida sessiyada yozamiz.
            try:
                async with SessionLocal() as db2:
                    failed = await db2.get(VideoJob, job_id)
                    if failed:
                        failed.status = JobStatus.FAILED
                        failed.error = str(e)
                        await db2.commit()
            except Exception as e2:
                log(f"⚠️ Xato holatini saqlab bo'lmadi: {e2}")
            await _notify(job_id, {"type": "status", "status": "failed", "error": str(e)})
            raise


def analyze_job(job_id: str):
    """RQ entry point."""
    try:
        asyncio.run(_analyze(job_id))
    except Exception as e:
        log(f"❌ FATAL: {e}")
        import traceback
        traceback.print_exc()
        raise