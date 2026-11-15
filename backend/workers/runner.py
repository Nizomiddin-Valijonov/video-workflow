"""Playwright orqali workflow qadamlarini bajarish."""
import asyncio, os, re, sys, traceback
from datetime import datetime
from urllib.parse import urlparse
from playwright.async_api import async_playwright, Page, TimeoutError as PWTimeout

from app.core.config import settings
from app.db.session import SessionLocal
from app.models import Run, VideoJob, WorkflowStep, JobStatus
from app.services.events import publish
from app.services.browser import context_options, launch_browser, normalize_browser
from app.services.sleep_inhibitor import prevent_sleep
from app.services.run_secrets import forget as forget_run_secrets
from app.services.run_secrets import get as get_run_secrets
from sqlalchemy import select


PLACEHOLDER = re.compile(r"\{\{\s*(\w+)\s*\}\}")


class RateLimitError(RuntimeError):
    """Saytning o'z limiti (PINFL 3/90s yoki 429) haqida signal."""

    def __init__(self, message: str, wait_seconds: int = 30):
        super().__init__(message)
        self.wait_seconds = max(1, min(180, int(wait_seconds)))


def _rate_limit_wait(text: str) -> int | None:
    """Matndan sayt limitini va kutish soniyasini ajratib oladi.

    Sayt PINFL qidiruvini 90 soniyada 3 tadan ko'p qilishga ruxsat
    bermaydi va ``So‘rovlar ko‘payib ketdi. N soniya kuting`` toastini
    ko'rsatadi. Ba'zi hollarda 429 yoki ruscha matn uchraydi.
    """
    low = (text or "").lower().replace("‘", "'").replace("’", "'")
    markers = (
        "so'rovlar ko'payib",
        "сўровлар кўпайиб",
        "слишком много запросов",
        "too many requests",
        "rate limit",
        "status code 429",
    )
    if not any(m in low for m in markers):
        return None
    m = re.search(
        r"(\d+)\s*(?:soniya|сония|секунд|seconds?|sec)",
        low,
    )
    if m:
        return max(1, min(180, int(m.group(1))))
    return 30


def render(template: str | None, inputs: dict) -> str | None:
    if not template:
        return template
    return PLACEHOLDER.sub(lambda m: inputs.get(m.group(1), m.group(0)), template)


def _clear_secrets(run: Run) -> None:
    """Yakunlangan run'da OneID parolini bazada qoldirmaymiz."""
    inputs = getattr(run, "inputs", None)
    if isinstance(inputs, dict) and inputs.get("PASSWORD"):
        updated = dict(inputs)
        updated["PASSWORD"] = ""
        run.inputs = updated


async def _publish(job_id: str, msg: dict):
    try:
        await publish(job_id, msg)
    except Exception:
        pass


async def _log(job_id: str, message: str, level: str = "info"):
    await _publish(job_id, {"type": "log", "level": level, "message": message})
    print(f"[RUNNER] {message}", flush=True)


async def _status(job_id: str, run_id: str, status: str, step: int | None = None,
                  error: str | None = None, error_step: int | None = None):
    msg = {
        "type": "run_status", "run_id": run_id, "status": status,
        "step": step, "error": error,
    }
    # error_step yo'q bo'lsa yubormaymiz — frontend null'ni "1-qadam" deb
    # ko'rsatib qo'ymasligi uchun.
    if error_step is not None:
        msg["error_step"] = error_step
    await _publish(job_id, msg)


async def _screenshot(page: Page, run_id: str, tag: str) -> str | None:
    try:
        shot_dir = os.path.join(settings.STORAGE_DIR, "runs", run_id)
        os.makedirs(shot_dir, exist_ok=True)
        path = os.path.join(shot_dir, f"{tag}.png")
        await page.screenshot(path=path, full_page=True)
        return path
    except Exception:
        return None


async def _toast_text(page: Page) -> str:
    """Saytdagi ko'rinadigan bildirishnoma (toast) matnini qaytaradi."""
    for sel in (".el-notification", ".el-message", ".vue-notification",
                ".toast", ".noty_body", ".alert"):
        try:
            loc = page.locator(sel)
            n = await loc.count()
            for j in range(min(n, 6)):
                el = loc.nth(j)
                if await el.is_visible():
                    txt = (await el.inner_text()).strip()
                    if txt:
                        return txt
        except Exception:
            continue
    return ""


async def _body_has_visible_text(page: Page, text: str) -> bool:
    """Sahifada ko'rinadigan matn borligini tekshiradi."""
    try:
        loc = page.get_by_text(text, exact=False)
        n = await loc.count()
        for j in range(min(n, 6)):
            if await loc.nth(j).is_visible():
                return True
    except Exception:
        pass
    return False


ONEID_INDIVIDUAL_LABELS = (
    "Жисмоний шахс",
    "Jismoniy shaxs",
    "Физическое лицо",
    "ФИЗИЧЕСКОЕ ЛИЦО",
    "Физ. лицо",
    "Individual",
)
ONEID_PROMPT_MARKERS = (
    "жисмоний шахс",
    "jismoniy shaxs",
    "физическое лицо",
    "юридик шахс",
    "юридическое лицо",
    "individual",
    "qanday rolda",
    "davom etish",
)
ONEID_CONTINUE_LABELS = (
    "Davom etish",
    "Давом этиш",
    "Продолжить",
    "Continue",
)


async def _choose_oneid_individual(
    page: Page, job_id: str, timeout_ms: int = 6000
) -> bool:
    """OneID'dagi «Жисмоний шахс» tanlovini, agar chiqqan bo'lsa, bosadi.

    Ba'zi akkauntlarda login'dan keyin «юридик ёки жисмоний шахс» tanlovi
    chiqadi. OneID role sahifasida tanlovdan keyin «Davom etish» ham bosiladi.
    Tanlov bo'lmasa, runni to'xtatmaymiz va ortiqcha kutmaymiz.
    """
    loop = asyncio.get_event_loop()
    deadline = loop.time() + max(0.5, timeout_ms / 1000)
    while True:
        try:
            current_url = (page.url or "").lower()
        except Exception:
            current_url = ""
        try:
            body = (await page.inner_text("body"))[:20000].lower()
        except Exception:
            body = ""
        role_prompt = any(
            marker in body
            for marker in ("qanday rolda", "davom etish", "давом этиш")
        )
        # OneID login formasi hali ko'rinib turganda role tanlovi yo'q.
        # Lekin submitdan keyin sahifa bir zum bo'sh bo'ladi — bu holatda
        # darhol qaytmasdan, role oynasi yuklanishini kutamiz.
        if "id.egov.uz" in current_url and not role_prompt:
            try:
                login_input = page.locator("input[name='login']").first
                if await login_input.count() and await login_input.is_visible():
                    return False
            except Exception:
                pass
            if loop.time() >= deadline:
                return False
            await asyncio.sleep(0.25)
            continue

        for label in ONEID_INDIVIDUAL_LABELS:
            candidates = (
                page.get_by_text(label, exact=True),
                page.locator(f"button:has-text('{label}')"),
                page.locator(f"a:has-text('{label}')"),
                page.locator(f"[role='button']:has-text('{label}')"),
            )
            for loc in candidates:
                try:
                    n = await loc.count()
                except Exception:
                    n = 0
                for i in range(min(n, 4)):
                    el = loc.nth(i)
                    try:
                        if not await el.is_visible():
                            continue
                        await el.click(timeout=4000)
                        await _log(job_id, "🪪 OneID: «Жисмоний шахс» tanlandi")
                        await page.wait_for_timeout(300)
                        if "id.egov.uz" in current_url:
                            for continue_label in ONEID_CONTINUE_LABELS:
                                btn = page.locator("button").filter(
                                    has_text=continue_label
                                )
                                try:
                                    bn = await btn.count()
                                except Exception:
                                    bn = 0
                                clicked = False
                                for j in range(min(bn, 3)):
                                    try:
                                        b = btn.nth(j)
                                        if not await b.is_visible():
                                            continue
                                        await b.click(timeout=8000)
                                        clicked = True
                                        break
                                    except Exception:
                                        continue
                                if clicked:
                                    await _log(
                                        job_id,
                                        "🪪 OneID: «Davom etish» bosildi",
                                    )
                                    await page.wait_for_timeout(500)
                                    # Sayt login?code=... dan dashboardga
                                    # o'tishini kutamiz; aks holda keyingi
                                    # qadam bo'sh sahifada bajariladi.
                                    try:
                                        await page.wait_for_function(
                                            "() => document.body && "
                                            "document.body.innerText.trim().length > 20 && "
                                            "!location.pathname.includes('/login')",
                                            timeout=25000,
                                        )
                                    except Exception:
                                        pass
                                    break
                        return True
                    except Exception:
                        continue
        if not any(marker in body for marker in ONEID_PROMPT_MARKERS):
            return False
        if loop.time() >= deadline:
            return False
        await asyncio.sleep(0.25)


async def _first_visible(loc, timeout_ms: int):
    """Bir nechta mos elementdan birinchi KO'RINADIGANINI qaytaradi.

    `.first` ishlatilsa, DOM'da birinchi turgan yashirin element sababli
    ko'rinadigan tugma topilmay qolishi mumkin (masalan, yashirin tablar).
    """
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout_ms / 1000
    while True:
        try:
            n = await loc.count()
        except Exception:
            n = 0
        for j in range(min(n, 15)):
            el = loc.nth(j)
            try:
                if await el.is_visible():
                    return el
            except Exception:
                continue
        if loop.time() >= deadline:
            raise PWTimeout("Ko'rinadigan element topilmadi")
        await asyncio.sleep(0.25)


async def _find_smart(page: Page, target: str, first_timeout: int = 30000, other_timeout: int = 10000):
    """Bir nechta selector strategiyasini sinab ko'rib, topilgan elementni qaytarish."""
    if not target:
        raise ValueError("target bo'sh")
    
    clean_target = target.strip()
    
    # Strategiyalar: avval aniq, keyin matn bo'yicha
    candidates = [
        clean_target,
        f"#{clean_target}",
        f"text={clean_target}",
        f"[aria-label='{clean_target}']",
        f"[placeholder='{clean_target}']",
        f"[title='{clean_target}']",
        f"button:has-text('{clean_target}')",
        f"a:has-text('{clean_target}')",
        f"input[value='{clean_target}']",
        f"*:has-text('{clean_target}')",
        f"text={clean_target.split()[0]}" if clean_target.split() else clean_target,
        f"*:has-text('{clean_target.split()[0]}')" if clean_target.split() else clean_target,
    ]
    
    errors = []
    for i, sel in enumerate(candidates):
        if not sel:
            continue
        try:
            loc = page.locator(sel)
            # Asosiy (CSS va text=) strategiyalarga ko'proq vaqt beramiz —
            # SPA saytlar kech render bo'lishi mumkin.
            wait_ms = first_timeout if i in (0, 2) else other_timeout
            el = await _first_visible(loc, wait_ms)
            print(f"[RUNNER] ✅ Selector {i+1}: {sel[:60]}", flush=True)
            return el
        except PWTimeout:
            errors.append(f"Timeout: {sel[:40]}")
            continue
        except Exception as e:
            errors.append(f"Error: {sel[:40]}: {str(e)[:50]}")
            continue
    
    # Hech biri ishlamadi
    raise PWTimeout(f"Element topilmadi: {clean_target} (12 ta strategiya sinab ko'rildi)")


async def _click_smart(page: Page, target: str, first_timeout: int = 45000, other_timeout: int = 10000):
    """Elementni topib, bosish."""
    loc = await _find_smart(page, target, first_timeout, other_timeout)
    # Preloader/overlay yo'qolishini kutish uchun uzoqroq vaqt beramiz
    await loc.click(timeout=30000)


async def _select2(page: Page, target: str, value: str):
    """Select2 dropdowndan matn bo'yicha variant tanlash.

    target — ko'rinadigan `.select2-selection` elementi (masalan,
    `div.form-modal .select2-selection >> nth=0`).
    """
    loc = page.locator(target)
    el = await _first_visible(loc, 20000)
    await el.click(timeout=15000)
    await page.wait_for_timeout(400)
    opt = page.locator(".select2-results__option").filter(has_text=value)
    o = await _first_visible(opt, 15000)
    await o.click(timeout=15000)
    await page.wait_for_timeout(150)


async def _check(page: Page, target: str, value: str):
    """Checkbox/switch holatini JS orqali o'rnatish (Vue v-model uchun)."""
    want = str(value).lower() not in ("false", "0", "no", "")
    loc = page.locator(target)
    el = await _first_visible(loc, 15000)
    await el.evaluate(
        """(el, want) => {
            el.checked = want;
            el.dispatchEvent(new Event('input', {bubbles: true}));
            el.dispatchEvent(new Event('change', {bubbles: true}));
        }""",
        want,
    )
    await page.wait_for_timeout(150)


async def _setvalue(page: Page, target: str, value: str):
    """Readonly/maskali maydonlarga JS orqali qiymat yozish."""
    loc = page.locator(target)
    el = await _first_visible(loc, 15000)
    await el.evaluate(
        """(el, value) => {
            el.value = value;
            el.dispatchEvent(new Event('input', {bubbles: true}));
            el.dispatchEvent(new Event('change', {bubbles: true}));
        }""",
        value or "",
    )
    await page.wait_for_timeout(150)


async def _fill_member_education(page: Page, job_id: str) -> None:
    """Eski videolarda bo'lmagan, lekin sayt talab qiladigan «Маълумоти» maydoni.

    online-mahalla a'zo qo'shish formasida ta'lim darajasi bo'sh bo'lsa
    «Сақлаш» tugmasi mijoz tomonda xato beradi. Video oqimlarida bu qadam
    bo'lmasligi mumkin, shuning uchun saqlashdan oldin to'ldiramiz.
    """
    try:
        selects = page.locator("div.form-modal .select2-selection")
        if await selects.count() < 3:
            return
        rendered = page.locator("div.form-modal .select2-selection__rendered")
        current = ""
        if await rendered.count() >= 3:
            current = (await rendered.nth(2).inner_text()).strip()
        if current and current not in ("Танланг", "Tanlang", "—", "-"):
            return
        await _select2(page, "div.form-modal .select2-selection >> nth=2", "Ўрта")
        await _log(job_id, "📚 Маълумоти: Ўрта tanlandi")
    except Exception as exc:
        await _log(
            job_id,
            f"⚠️ Маълумоти maydonini to'ldirib bo'lmadi: {str(exc)[:140]}",
            level="error",
        )


async def _goto(page: Page, url: str):
    """Sahifaga o'tish va (SPA saytlar uchun) to'liq yuklanishini kutish."""
    # MUHIM: agar oldingi amal (masalan, parolda Enter) hali redirect
    # zanjirini davom ettirayotgan bo'lsa, `page.goto()` uni o'rtasidan
    # uzib qo'yadi va login sessiyasi yaratilmay qoladi. Shuning uchun URL
    # barqarorlashguncha qisqa kutamiz.
    try:
        loop = asyncio.get_event_loop()
        started = loop.time()
        last_url = page.url or ""
        stable_since = started
        while loop.time() - started < 25:
            await asyncio.sleep(0.2)
            current = page.url or ""
            if current != last_url:
                last_url = current
                stable_since = loop.time()
            elif loop.time() - stable_since >= 0.8:
                break
    except Exception:
        pass
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
    except PWTimeout:
        pass  # Sekin SPA saytlarda sahifa davom etib yuklanishi mumkin
    try:
        await page.wait_for_load_state("networkidle", timeout=15000)
    except Exception:
        pass  # Sahifa uzoq yuklansa ham davom etamiz
    try:
        # Vue/React SPA bo'sh sahifadan keyin render bo'ladi — shuni kutamiz
        await page.wait_for_function(
            "() => document.body && document.body.innerText.trim().length > 20",
            timeout=90000,
        )
    except Exception:
        pass


def _step_scope(page: Page, step: WorkflowStep):
    """Qadam qaysi frame'da bajarilishini aniqlaydi.

    Video tahlilidan olingan qadamlarda `meta.frame_url` bo'lmaydi — ular
    avvalgidek asosiy sahifada ishlaydi. Brauzerdan yozib olingan qadamlar
    esa iframe ichidagi elementlarni ham ko'rsatadi.
    """
    meta = step.meta if isinstance(step.meta, dict) else {}
    target = str(meta.get("frame_url") or "").strip()
    if not target:
        return page
    frames = []
    try:
        for frame in page.frames:
            if frame is page.main_frame:
                continue
            frames.append(frame)
    except Exception:
        return page
    for frame in frames:
        try:
            if frame.url == target:
                return frame
        except Exception:
            continue
    # Query-string tokenlar har yuklashda o'zgarishi mumkin — path bo'yicha
    # mos keladigan birinchi frame'ni olamiz.
    try:
        target_path = urlparse(target).path
    except Exception:
        target_path = ""
    if target_path:
        for frame in frames:
            try:
                if urlparse(frame.url).path == target_path:
                    return frame
            except Exception:
                continue
    return page


_AUTH_HOST_MARKERS = ("id.egov.uz", "oneid", "sso.", "oauth", "auth.")
_AUTH_PATH_MARKERS = (
    "/login", "/signin", "/sign-in", "/auth", "/oauth", "/callback", "/sso",
)


def _looks_auth_url(url: str) -> bool:
    """Sahifa login/OneID/OAuth oqimida ekanini aniqlaydi.

    Bunday sahifada qolib ketish "tizimga kirilmadi" degani — keyingi
    qadamlar bo'sh login sahifasida bajarilib, tushunarsiz xato beradi.
    """
    try:
        parts = urlparse(url or "")
    except Exception:
        return False
    host = (parts.netloc or "").lower()
    path = (parts.path or "").lower()
    if any(marker in host for marker in _AUTH_HOST_MARKERS):
        return True
    return any(marker in path for marker in _AUTH_PATH_MARKERS)


async def _password_context(scope, target: str) -> dict:
    """Element parolli formada ekanini va parol to'ldirilganini aniqlaydi.

    Qaytaradi: ``in_form`` — element parolli forma ichida (yoki o'zi parol
    maydoni); ``filled`` — o'sha formadagi parol maydoni to'ldirilgan.
    ``filled`` faqat login yuborilayotganini bildiradi — sahifa OneID'ga
    o'tib, o'sha yerda qolishi normal holat.
    """
    info = {"in_form": False, "filled": False, "own_value": False}
    if not target:
        # Selektor ko'rsatilmagan submit — sahifadagi parol maydoniga qaraymiz.
        try:
            pwd = scope.locator("input[type='password']").first
            if await pwd.count() and (await pwd.input_value()).strip():
                info["filled"] = True
        except Exception:
            pass
        return info
    try:
        loc = scope.locator(target)
        count = await loc.count()
    except Exception:
        return info
    for i in range(min(count, 3)):
        try:
            res = await loc.nth(i).evaluate(
                """el => {
                    if (!el) return null;
                    const form = el.form
                        || (el.closest && el.closest('form')) || null;
                    const pwd = el.type === 'password' ? el
                        : (form ? form.querySelector('input[type="password"]') : null);
                    return {
                        in_form: el.type === 'password' || !!form,
                        filled: !!(pwd && String(pwd.value || '').trim()),
                        own_value: !!(el && typeof el.value === 'string'
                                     && el.value.trim())
                    };
                }"""
            )
            if res and res.get("in_form"):
                info["in_form"] = True
                if res.get("filled"):
                    info["filled"] = True
                if res.get("own_value"):
                    info["own_value"] = True
                if info["filled"] or info["own_value"]:
                    return info
        except Exception:
            continue
    return info


async def _password_visible(page: Page) -> bool:
    """Sahifada ko'rinadigan parol maydoni bormi?"""
    try:
        loc = page.locator("input[type='password']")
        n = await loc.count()
    except Exception:
        return False
    for i in range(min(n, 4)):
        try:
            if await loc.nth(i).is_visible():
                return True
        except Exception:
            continue
    return False


async def _wait_after_navigation(
    page: Page,
    job_id: str,
    *,
    auth_attempt: bool = False,
    timeout: float = 90.0,
    idle: float = 0.8,
) -> None:
    """Amaldan keyin brauzerdagi o'tishlar tugashini kutadi.

    Muammo: login formasida Enter bosilgach sahifa OneID → sayt → dashboard
    zanjiri bo'ylab bir necha marta o'tadi. Runner darhol keyingi
    ``navigate`` qadamini bajarsa, `page.goto()` bu zanjirni uzib qo'yadi va
    sessiya yaratilmay qoladi. Shuning uchun URL barqarorlashguncha
    kutamiz; login yuborilganda esa login sahifasidan chiqishni kutamiz.
    """
    loop = asyncio.get_event_loop()
    started = loop.time()
    deadline = started + max(3.0, timeout)
    last_url = ""
    stable_since = started
    next_check = started + 1.0
    next_pwd_check = started
    pwd_visible = True
    waiting_auth = False

    while True:
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=2500)
        except Exception:
            pass
        try:
            url = page.url or ""
        except Exception:
            url = last_url
        now = loop.time()
        if url != last_url:
            last_url = url
            stable_since = now

        if not auth_attempt:
            # Oddiy o'tish: URL barqarorlashsa, davom etamiz. 0.3s yetarli —
            # brauzer navigatsiya boshlanganda URL'ni darhol yangilaydi,
            # shuning uchun sekin batch ishlariga ortiqcha kechikish qo'shmaydi.
            if now - stable_since >= 0.3:
                return
            if now >= deadline:
                return
            await asyncio.sleep(0.2)
            continue

        if now >= next_pwd_check:
            next_pwd_check = now + 0.5
            pwd_visible = await _password_visible(page)
        # Login formasi hali ochiq bo'lsa ham kirish tugamagan hisoblanadi —
        # modal ko'rinishidagi login sahifa manzilini o'zgartirmasligi mumkin.
        on_auth = _looks_auth_url(url) or pwd_visible
        if on_auth and not waiting_auth:
            waiting_auth = True
            await _log(job_id, "⏳ Tizimga kirish yakunlanishi kutilmoqda…")
        if on_auth and now >= next_check:
            next_check = now + 1.5
            # Noto'g'ri parol kabi xatolar 90s kutishni talab qilmaydi.
            try:
                body_low = (await page.inner_text("body"))[:5000].lower()
            except Exception:
                body_low = ""
            if any(k in body_low for k in (
                "нотўғри", "нотугри", "логин ёки парол", "login yoki parol",
            )):
                break
            # OneID ba'zi hisoblarda «Жисмоний шахс» tanlovini so'raydi —
            # u tanlanmasa login tugamaydi. Faqat OneID sahifasida
            # bosamiz: saytning o'z login formasiga tegib ketmaymiz.
            if "id.egov.uz" in url.lower():
                await _choose_oneid_individual(page, job_id, 1200)
            continue
        if not on_auth and now - stable_since >= idle:
            break
        if now >= deadline:
            break
        await asyncio.sleep(0.2)

    try:
        final_url = page.url or ""
    except Exception:
        final_url = last_url
    if auth_attempt and (
        _looks_auth_url(final_url) or await _password_visible(page)
    ):
        if not ("code=" in final_url.lower() and not await _password_visible(page)):
            raise RuntimeError(
                "Tizimga kirish yakunlanmadi: sahifa login/OneID manzilida "
                "qolib ketdi. Login va parolni tekshirib, qayta urinib ko'ring."
            )
    if waiting_auth:
        await _log(job_id, "✅ Tizimga kirish yakunlandi")


async def _execute_step(
    page: Page,
    step: WorkflowStep,
    inputs: dict,
    job_id: str,
    settle_ms: int = 250,
    followed_by_navigate: bool = False,
):
    action = step.action
    value = render(step.value_template, inputs)
    target = step.selector or ""
    # Elementlarni qidiramiz scope'da; klaviatura va sahifa darajasidagi
    # amallar esa har doim asosiy `page` orqali bajariladi.
    scope = _step_scope(page, step)

    # Bu amal sahifa o'tishini boshlashi mumkinmi?
    #
    # Login oqimini kutish faqat ikkala shart bajarilganda yoqiladi:
    #   1) parol allaqachon kiritilgan (aks holda "OneID orqali kirish"
    #      tugmasidan keyin ham bekorga kutamiz);
    #   2) keyingi qadam `navigate` — faqat `page.goto()` davom etayotgan
    #      redirect zanjirini uzib qo'yadi. Boshqa amallar (click, type)
    #      zanjirga xalal bermaydi, shuning uchun ularni kutish shart emas.
    expect_nav = action == "submit"
    auth_attempt = False
    ctx = {"in_form": False, "filled": False, "own_value": False}
    if action == "press":
        expect_nav = str(value or "").strip().lower() in ("enter", "numpadenter")
        if expect_nav:
            ctx = await _password_context(scope, target)
            auth_attempt = ctx["filled"] and followed_by_navigate
    elif action == "click":
        ctx = await _password_context(scope, target)
        expect_nav = "submit" in (target or "").lower() or ctx["in_form"]
        auth_attempt = ctx["filled"] and followed_by_navigate
    elif action == "submit":
        ctx = await _password_context(page, target)
        auth_attempt = ctx["filled"] and followed_by_navigate

    # Zaxira holat: ba'zi React formalarda parol maydoni `<form>` ichida
    # bo'lmaydi yoki selektor uni parol sifatida aniqlamaydi. Login
    # sahifasida to'ldirilgan parol bo'lsa — kirish yuborilgan hisoblanadi.
    if (
        followed_by_navigate
        and not auth_attempt
        and action in ("press", "click", "submit")
    ):
        try:
            looks_login = _looks_auth_url(page.url)
        except Exception:
            looks_login = False
        if looks_login:
            page_ctx = await _password_context(page, "input[type='password']")
            # Parol maxsus niqoblangan maydonda bo'lishi mumkin — bosilgan
            # maydonning o'zida qiymat bo'lsa ham kirish yuborilgan.
            auth_attempt = page_ctx["filled"] or ctx.get("own_value", False)

    if action == "navigate":
        url = value or target
        if not url:
            raise ValueError("navigate uchun URL kiritilmagan")
        await _goto(page, url)
    elif action == "click":
        await _click_smart(scope, target)
    elif action == "press":
        loc = await _find_smart(scope, target, first_timeout=45000, other_timeout=10000)
        await loc.press(value or "Enter", timeout=15000)
    elif action == "type":
        loc = await _find_smart(scope, target, first_timeout=60000, other_timeout=15000)
        try:
            # fill() qiymatni to'g'ridan-to'g'ri o'rnatadi — klaviatura
            # layouti va maxsus belgilar bilan muammo bo'lmaydi.
            await loc.fill(value or "", timeout=15000)
        except Exception:
            await loc.click(timeout=15000)
            await page.keyboard.press("Meta+A" if sys.platform == "darwin" else "Control+A")
            await page.keyboard.type(value or "", delay=5)
    elif action == "select":
        try:
            await scope.select_option(target, value=value or "", timeout=10000)
        except Exception:
            # Brauzer yozuvida option qiymati bo'sh bo'lib, faqat ko'rinadigan
            # matni bor select'lar uchraydi — label bo'yicha qayta urinamiz.
            await scope.select_option(target, label=value or "", timeout=10000)
    elif action == "select2":
        await _select2(scope, target, value or "")
    elif action == "check":
        await _check(scope, target, value or "true")
    elif action == "setvalue":
        await _setvalue(scope, target, value or "")
    elif action == "upload":
        uploaded = False
        try:
            # Yozib olingan selektor odatda input[type=file]'ning o'zini
            # ko'rsatadi — OS fayl dialogini ochmasdan to'g'ridan-to'g'ri
            # qiymat beramiz.
            file_input = await _find_smart(
                scope, target, first_timeout=20000, other_timeout=8000
            )
            await file_input.set_input_files(value or "", timeout=15000)
            uploaded = True
        except Exception:
            uploaded = False
        if not uploaded:
            async with page.expect_file_chooser() as fc_info:
                await _click_smart(scope, target)
            fc = await fc_info.value
            await fc.set_files(value or "")
    elif action == "wait":
        await scope.wait_for_timeout(int(value or 1000))
    elif action == "verify_filled":
        loc = scope.locator(target)
        el = await _first_visible(loc, 15000)
        loop = asyncio.get_event_loop()
        started = loop.time()
        deadline = started + 25
        next_error_check = started + 2.0
        val = ""
        while True:
            val = (await el.input_value()) or ""
            if val.strip():
                break
            # Sayt xato toastini erta ko'rsatsa, 25s kutmasdan keyingi
            # odamga o'tamiz (batchda bu juda ko'p vaqtni tejaydi).
            if loop.time() >= next_error_check:
                next_error_check = loop.time() + 1.2
                toast = await _toast_text(page)
                modal_text = ""
                try:
                    modal_text = await page.locator(
                        "div.form-modal"
                    ).first.inner_text()
                except Exception:
                    pass
                low = f"{toast}\n{modal_text}".lower()
                wait = _rate_limit_wait(low)
                if wait is not None:
                    detail = (toast or modal_text).replace("\n", " ")[:250]
                    raise RateLimitError(
                        f"Sayt limiti: {detail}", wait
                    )
                if any(k in low for k in (
                    "топилмади", "хатолик", "нотўғри", "нотугри",
                    "мавжуд", "doc_status", "yaroqsiz", "маълумот йўқ",
                )):
                    detail = (toast or modal_text).replace("\n", " ")[:250]
                    raise RuntimeError(
                        f"Qidiruv natijasi topilmadi: {target} | {detail}"
                    )
            if loop.time() >= deadline:
                toast = await _toast_text(page)
                wait = _rate_limit_wait(toast)
                if wait is not None:
                    raise RateLimitError(
                        f"Sayt limiti: {toast[:250]}", wait
                    )
                raise RuntimeError(
                    f"Qidiruv natijasi bo'sh (25s): {target}"
                    + (f" | {toast[:250]}" if toast else "")
                )
            await page.wait_for_timeout(250)
        await _log(job_id, f"✅ Maydon to'ldi: {val.strip()[:50]}")
    elif action == "verify_saved":
        modal = page.locator("div.form-modal").first
        loop = asyncio.get_event_loop()
        started = loop.time()
        deadline = started + 25
        success = False
        error_text = ""
        while loop.time() < deadline:
            # 1) Modal yopilgan bo'lsa — saqlash muvaffaqiyatli
            try:
                if not await modal.is_visible():
                    success = True
                    break
            except Exception:
                success = True
                break

            # 2) Muvaffaqiyat yoki xato bildirishnomasi
            toast = await _toast_text(page)
            low = toast.lower()
            if "сақланди" in low or "сакланди" in low or "saved" in low:
                success = True
                break
            wait = _rate_limit_wait(low)
            if wait is not None:
                # Saqlash paytida limitga tushsak, modalni yopib qayta
                # urinmaymiz — bu dublikat yaratishi mumkin. Sayt
                # aytgan vaqtni kutib, natijani qayta tekshiramiz.
                await _log(
                    job_id,
                    f"⏳ Saqlashda sayt limiti: {wait}s kutilmoqda…",
                )
                await page.wait_for_timeout((wait + 1) * 1000)
                deadline += wait + 2
                continue
            if toast and any(k in low for k in (
                "хатолик", "нотўғри", "нотугри", "мавжуд", "текширинг",
                "сақланмади", "сакланмади", "киритилмаган", "error",
            )):
                error_text = toast
                break

            # 3) Video'da modal ochiq qoladi, lekin forma tozalanadi.
            # 2s juda erta — sayt formani submit paytida tozalab, xatoni
            # kechroq ko'rsatishi mumkin. Shuning uchun 4s saqlaymiz.
            if loop.time() - started > 4:
                try:
                    name_input = page.locator("div.form-modal input.form-control").nth(4)
                    if await name_input.is_visible() and not (await name_input.input_value()).strip():
                        success = True
                        break
                except Exception:
                    pass
            await page.wait_for_timeout(250)

        if not success:
            detail = error_text
            if not detail:
                try:
                    detail = (await modal.inner_text())[:400]
                except Exception:
                    detail = ""
            raise RuntimeError(f"Saqlash tasdiqlanmadi: {detail}")

        await _log(job_id, "✅ Ma'lumotlar saqlandi")
        # Modal ochiq qolishi mumkin — keyingi ishlarga xalaqit bermasligi uchun yopamiz
        try:
            if await modal.is_visible():
                close = page.locator("div.form-modal button.close-btn")
                if await close.count():
                    await close.first.click(timeout=7000)
                else:
                    await page.keyboard.press("Escape")
                await page.wait_for_timeout(250)
        except Exception:
            pass
    elif action == "submit":
        await _click_smart(page, target or "button[type=submit], input[type=submit]")
    else:
        raise ValueError(f"Noma'lum action: {action}")

    # Submit/Enter/parol formasidagi klik sahifa o'tishini boshlaydi.
    # O'tish tugamasdan keyingi qadamga (ayniqsa `navigate`ga) o'tsak,
    # `page.goto()` login/OAuth zanjirini uzadi va sessiya yo'qoladi.
    if expect_nav:
        await _wait_after_navigation(page, job_id, auth_attempt=auth_attempt)

    if settle_ms > 0:
        await page.wait_for_timeout(settle_ms)


async def _run_inner(run_id: str):
    async with SessionLocal() as db:
        run = await db.get(Run, run_id)
        if not run:
            return
        job = await db.get(VideoJob, run.job_id)
        steps_q = await db.execute(
            select(WorkflowStep).where(WorkflowStep.job_id == job.id).order_by(WorkflowStep.order_index)
        )
        steps = steps_q.scalars().all()

        # LOGIN/PASSWORD bazada emas — run yaratilganda jarayon
        # xotirasiga olingan. Shu yerda birlashtiramiz.
        inputs = {**(run.inputs or {}), **get_run_secrets(run_id)}

        # Batch rejimi: har bir xonadonga Excel'dan 5 tadan odam qo'shish.
        # Bu rejimda JSHSHIR/BIRTH_DATE/PHONE qadamlarda placeholder bo'lsa
        # ham, ular Excel'dan avtomatik olinadi — shuning uchun oddiy
        # "bo'sh input" tekshiruvini chetlab o'tamiz.
        batch_mode = str(inputs.get("BATCH_MODE", "")).lower() in (
            "1", "true", "yes", "on",
        )
        if batch_mode:
            from workers.batch import run_batch

            await run_batch(run, job, steps, db, inputs=inputs)
            return

        # Bo'sh inputlar bilan run qilishning oldini olish: aks holda
        # {{JSHSHIR}} kabi literal matn formaga tushib, sayt xato beradi.
        required: set[str] = set()
        for s in steps:
            required.update(PLACEHOLDER.findall(s.value_template or ""))
        missing = sorted(
            k for k in required
            if not str(inputs.get(k, "")).strip()
        )
        if missing:
            msg = (
                "Quyidagi maydonlar to'ldirilmagan: " + ", ".join(missing) +
                ". Sahifani yangilang (Ctrl+Shift+R) va ularni to'ldirib, "
                "qayta urinib ko'ring."
            )
            run.status = JobStatus.FAILED
            run.total_steps = len(steps)
            run.error = msg
            run.finished_at = datetime.utcnow()
            _clear_secrets(run)
            await db.commit()
            await _status(job.id, run.id, "failed", step=0, error=msg, error_step=None)
            await _log(job.id, f"❌ {msg}", level="error")
            return

        run.status = JobStatus.RUNNING
        run.total_steps = len(steps)
        await db.commit()
        await _status(job.id, run.id, "running", step=0)

        browser_name = normalize_browser(run.browser)
        async with async_playwright() as p:
            browser = await launch_browser(p, browser_name)
            await _log(job.id, f"Brauzer: {browser_name}")
            ctx = await browser.new_context(**context_options(browser_name))
            await ctx.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
            )
            # online-mahalla.uz sekinligining asosiy sababi: notification.js
            # Platon plaginlarini oldindan yuklab, yuzlab chunk so'rovini
            # yuboradi. Bizga bildirishnomalar kerak emas — bloklaymiz.
            # Analitika ham ishlatilmaydi.
            await ctx.route("**/notification.js", lambda r: r.abort())
            await ctx.route("**/google-tag-manager-head.js", lambda r: r.abort())
            await ctx.route("**/googletagmanager.com/**", lambda r: r.abort())
            await ctx.route("**/mc.yandex**", lambda r: r.abort())
            page = await ctx.new_page()
            page.set_default_timeout(settings.STEP_TIMEOUT_SEC * 1000)
            # Yangi tab ochilsa (masalan "+ Хонадон қўшиш"), keyingi qadamlar
            # o'sha tabda davom etishi kerak.
            popups: list[Page] = []
            ctx.on("page", lambda p: popups.append(p))

            try:
                # Birinchi navigate qadamini topib, o'sha sahifadan boshlash
                pre_navigated: int | None = None
                for idx, s in enumerate(steps):
                    if s.action == "navigate":
                        await _goto(page, render(s.value_template, inputs) or s.selector or "")
                        await _log(job.id, f"Saytdan o'tildi: {s.selector or s.value_template}")
                        pre_navigated = idx
                        break

                # 🔍 BIRINCHI QADAMDAN OLDIN SCREENSHOT VA SAHIFA MATNI
                shot_before = await _screenshot(page, run_id, "before_first_step")
                if shot_before:
                    print(f"[RUNNER] 📸 Sayt screenshot: {shot_before}", flush=True)
                    await _log(job.id, f"📸 Sayt screenshot: {shot_before}")
                
                page_text = await page.inner_text("body")
                print(f"[RUNNER] 📄 Sahifa matni (birinchi 500 belgi): {page_text[:500]}", flush=True)

                login_submit_seen = False
                for i, s in enumerate(steps):
                    # Bekor qilish DB orqali keladi: cancel_run run.status'ni
                    # FAILED qiladi. Har qadam oldidan holatni qayta o'qiymiz,
                    # aks holda bekor qilingan run COMPLETED bo'lib yozilib ketadi.
                    try:
                        await db.refresh(run)
                    except Exception:
                        pass
                    if run.status == JobStatus.FAILED:
                        run.finished_at = datetime.utcnow()
                        _clear_secrets(run)
                        await db.commit()
                        await _status(job.id, run.id, "cancelled", step=i, error=run.error)
                        await _log(job.id, "⛔ Bekor qilindi", level="warning")
                        return

                    run.current_step = i
                    await db.commit()
                    await _status(job.id, run.id, "running", step=i)
                    await _log(job.id, f"[{i+1}/{len(steps)}] {s.action}: {s.description}")

                    try:
                        if i == pre_navigated:
                            # Birinchi navigate allaqachon bajarilgan —
                            # ikkinchi marta yuklash ortiqcha.
                            await page.wait_for_timeout(250)
                        else:
                            if "save" in (s.selector or "").lower():
                                await _fill_member_education(page, job.id)
                            next_step = steps[i + 1] if i + 1 < len(steps) else None
                            await _execute_step(
                                page, s, inputs, job.id,
                                followed_by_navigate=bool(
                                    next_step and next_step.action == "navigate"
                                ),
                            )
                        submit_like = (
                            s.action == "submit"
                            or (
                                s.action == "click"
                                and "submit" in (s.selector or "").lower()
                            )
                            or (
                                # Brauzer yozuvida login ko'pincha parol
                                # maydonida Enter bosish bilan yuboriladi.
                                s.action == "press"
                                and str(s.value_template or "").strip().lower()
                                in ("enter", "numpadenter")
                            )
                        )
                        if submit_like and i <= 7:
                            login_submit_seen = True
                            # Faqat login/OneID sahifasida: boshqa sahifada
                            # «Жисмоний шахс» yozuvi bo'lsa ham bosmaymiz.
                            try:
                                still_auth = _looks_auth_url(page.url)
                            except Exception:
                                still_auth = True
                            if still_auth:
                                await _choose_oneid_individual(page, job.id, 12000)
                        elif login_submit_seen and s.action == "wait" and i <= 8:
                            # OneID ba'zan submitdan keyin kechroq role so'raydi.
                            await _choose_oneid_individual(page, job.id, 8000)
                            login_submit_seen = False
                        # Yangi tab ochilgan bo'lsa — keyingi qadamlar o'sha tabda
                        if popups:
                            page = popups[-1]
                            popups.clear()
                            page.set_default_timeout(settings.STEP_TIMEOUT_SEC * 1000)
                            print(f"[RUNNER] 🪟 Yangi tab: {page.url[:100]}", flush=True)
                            await _log(job.id, f"🪟 Yangi tab ochildi: {page.url[:80]}")
                    except Exception as e:
                        # Xato bo'lganda screenshot
                        shot = await _screenshot(page, run_id, f"fail_{i}")
                        # Foydalanuvchi aynan shu paytda bekor qilgan bo'lsa,
                        # qadam xatosini bekor qilish bilan almashtirmaymiz.
                        try:
                            await db.refresh(run)
                        except Exception:
                            pass
                        if run.status == JobStatus.FAILED:
                            run.finished_at = datetime.utcnow()
                            _clear_secrets(run)
                            await db.commit()
                            await _status(job.id, run.id, "cancelled", step=i, error=run.error)
                            await _log(job.id, "⛔ Bekor qilindi", level="warning")
                            return
                        msg = str(e)
                        try:
                            body_low = (await page.inner_text("body")).lower()
                            if ("нотўғри" in body_low or "нотугри" in body_low
                                    or "login yoki parol" in body_low
                                    or "логин ёки парол" in body_low):
                                msg = ("OneID orqali kirish amalga oshmadi — "
                                       "login yoki parol xato bo'lishi mumkin")
                        except Exception:
                            pass
                        run.status = JobStatus.FAILED
                        run.error = f"Qadam {i+1} ({s.action}): {msg}"
                        run.error_step = i
                        run.error_screenshot = shot
                        _clear_secrets(run)
                        await db.commit()
                        await _status(job.id, run.id, "failed", step=i, error=run.error, error_step=i)
                        await _log(job.id, f"❌ {run.error}", level="error")
                        return

                # Yakuniy holat yozishdan oldin bekor qilinmaganiga ishonch
                # hosil qilamiz — aks holda cancel COMPLETED bilan almashadi.
                try:
                    await db.refresh(run)
                except Exception:
                    pass
                if run.status == JobStatus.FAILED:
                    run.finished_at = datetime.utcnow()
                    _clear_secrets(run)
                    await db.commit()
                    await _status(job.id, run.id, "cancelled", step=len(steps), error=run.error)
                    await _log(job.id, "⛔ Bekor qilindi", level="warning")
                    return

                run.status = JobStatus.COMPLETED
                run.finished_at = datetime.utcnow()
                _clear_secrets(run)
                await db.commit()
                await _status(job.id, run.id, "completed", step=len(steps))
                await _log(job.id, "✅ Barcha qadamlar bajarildi")
            finally:
                await browser.close()


async def _run(run_id: str):
    try:
        with prevent_sleep(f"VideoWorkflow run {run_id[:8]}"):
            await _run_inner(run_id)
    except Exception as exc:
        # Brauzer o'rnatilmagani yoki context ochilmagani kabi kutilmagan
        # xatolarda run "running" holatida qolib ketmasligi kerak.
        traceback.print_exc()
        async with SessionLocal() as db:
            run = await db.get(Run, run_id)
            if not run or run.status in (JobStatus.COMPLETED, JobStatus.FAILED):
                return
            job = await db.get(VideoJob, run.job_id)
            msg = str(exc).replace("\n", " ")[:500] or exc.__class__.__name__
            run.status = JobStatus.FAILED
            run.error = f"Workflow xatosi: {msg}"
            run.finished_at = datetime.utcnow()
            _clear_secrets(run)
            await db.commit()
            if job:
                await _status(
                    job.id,
                    run.id,
                    "failed",
                    step=run.current_step or 0,
                    error=run.error,
                )
                await _log(job.id, run.error, level="error")
    finally:
        # Parol jarayon xotirasida ham qolmasin.
        forget_run_secrets(run_id)


def execute_run(run_id: str):
    asyncio.run(_run(run_id))
