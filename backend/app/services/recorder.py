"""Brauzerdagi foydalanuvchi harakatlarini yozib olish.

Video tahliliga alternativa: foydalanuvchi ko'rinadigan brauzer oynasida
ishlaydi, zarur tugmalarni bosadi va maydonlarni to'ldiradi. Shu paytda
sahifaga kiritilgan skript harakatlarni ushlaydi, barqaror CSS selektor,
tavsif va screenshot bilan birga saqlaydi. To'xtatilganda ular oddiy
``VideoJob`` + ``WorkflowStep`` shabloniga aylanadi va mavjud runner
bilan xuddi video tahlilidan olingan qadamlar kabi bajariladi.

Xavfsizlik: parol maydonlari qiymati hech qachon yozilmaydi —
``{{PASSWORD}}`` (va login maydoni ``{{LOGIN}}``) ko'rinishida shablon
qoldiriladi. Shubhali holatda backend ham qiymatni o'chirib tashlaydi.
"""
from __future__ import annotations

import asyncio
import os
import re
import shutil
import time
import uuid
from dataclasses import dataclass, field
from urllib.parse import parse_qsl, urlencode, urlparse, urlsplit, urlunsplit

from playwright.async_api import async_playwright

from app.core.config import settings
from app.services.browser import context_options, launch_browser, normalize_browser


ALLOWED_ACTIONS = {
    "navigate", "click", "submit", "type", "select", "check", "upload", "press",
}
_RECORDED_SCREENSHOT_DIR = "screenshots"
_MAX_VALUE = 8000
_RECENT_ACTION_SECONDS = 2.5


class RecorderError(RuntimeError):
    """Yozib olish sessiyasi bilan bog'liq foydalanuvchiga ko'rinadigan xato."""


class RecorderBusy(RecorderError):
    """Bir vaqtda faqat bitta yozib olish sessiyasi ochiq bo'lishi mumkin."""


def normalize_start_url(raw: str) -> str:
    """Foydalanuvchi kiritgan manzilni http(s) URL'ga keltiradi."""
    value = (raw or "").strip()
    if not value:
        raise RecorderError("Boshlang'ich URL kiritilmagan.")
    if not re.match(r"^[A-Za-z][A-Za-z0-9+.-]*://", value):
        value = "https://" + value
    parsed = urlparse(value)
    if parsed.scheme not in ("http", "https"):
        raise RecorderError("Faqat http:// yoki https:// manzillar qo'llanadi.")
    if not parsed.netloc:
        raise RecorderError("URL noto'g'ri: domen topilmadi.")
    return value


def _clean_label(value: object, limit: int = 90) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:limit]


def _short_url(value: str, limit: int = 120) -> str:
    text = (value or "").strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


# Login sahifalari ba'zan formani GET bilan yuboradi yoki OAuth token'ini
# URL'ga qo'shadi. Bunday qiymatlar shablonga tushmasligi kerak.
_SENSITIVE_KEY_PARTS = {
    "password", "passwd", "pass", "parol", "pwd", "secret", "token",
    "auth", "authorization", "session", "sessionid", "login", "username",
    "userid", "user", "email", "code", "otp", "key", "sig", "signature",
    "credential", "credentials",
}
_SENSITIVE_SUBSTRINGS = (
    "password", "passwd", "parol", "secret", "token", "session", "login",
    "username", "userid", "email", "apikey", "auth",
)


def _is_sensitive_key(key: str) -> bool:
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", key or "")
    parts = [part for part in re.split(r"[^a-z0-9]+", text.lower()) if part]
    if any(part in _SENSITIVE_KEY_PARTS for part in parts):
        return True
    compact = "".join(parts)
    return any(word in compact for word in _SENSITIVE_SUBSTRINGS)


def sanitize_url(url: str) -> str:
    """URL'dagi maxfiy query/fragment parametrlarini olib tashlaydi."""
    try:
        parts = urlsplit(url)
    except Exception:
        return url
    query = [
        (key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if not _is_sensitive_key(key)
    ]
    fragment = parts.fragment
    if fragment and "=" in fragment:
        pairs = [
            (key, value)
            for key, value in parse_qsl(fragment, keep_blank_values=True)
            if not _is_sensitive_key(key)
        ]
        fragment = urlencode(pairs)
    elif fragment and _is_sensitive_key(fragment):
        fragment = ""
    return urlunsplit((
        parts.scheme, parts.netloc, parts.path, urlencode(query), fragment
    ))


_URL_META_KEYS = ("page_url", "frame_url", "url", "href")


def sanitize_meta(meta: dict) -> dict:
    """Hodisa meta'sidagi URL maydonlaridan maxfiy parametrlarni olib tashlaydi."""
    for key in _URL_META_KEYS:
        value = meta.get(key)
        if isinstance(value, str) and value:
            meta[key] = sanitize_url(value)
    return meta


# Sahifaga ATAYLAB minimal aralashamiz: faqat o'qish uchun tinglovchilar.
# Tarix (pushState) va scroll hodisalari o'zgartirilmaydi; SPA o'tishlarini
# Playwright'ning `framenavigated` hodisasi ushlaydi.
_RECORDER_JS = r"""
(() => {
  if (window.__vwRecorderInstalled) return;
  window.__vwRecorderInstalled = true;

  var MAXV = 8000;
  var pending = new Map();
  var TEXT_TYPES = {
    text: 1, email: 1, tel: 1, url: 1, search: 1, password: 1, number: 1,
    date: 1, 'datetime-local': 1, month: 1, week: 1, time: 1
  };

  function send(payload) {
    try {
      if (typeof window.__vwRecord === 'function') window.__vwRecord(payload);
    } catch (e) {}
  }

  function frameUrl() {
    try { return String(location.href).slice(0, 1500); } catch (e) { return ''; }
  }

  function isMainFrame() {
    try { return window.top === window; } catch (e) { return false; }
  }

  function cssEscape(value) {
    var text = String(value);
    if (window.CSS && typeof window.CSS.escape === 'function') {
      return window.CSS.escape(text);
    }
    return text.replace(/([^a-zA-Z0-9_-])/g, '\\$1');
  }

  function attrValue(value) {
    return '"' + String(value)
      .replace(/\\/g, '\\\\')
      .replace(/"/g, '\\"')
      .replace(/[\r\n]+/g, ' ') + '"';
  }

  function uniqueSelector(selector) {
    try {
      return !!selector && document.querySelectorAll(selector).length === 1;
    } catch (e) {
      return false;
    }
  }

  function selectorFor(el) {
    if (!el || el.nodeType !== 1) return '';
    var id = el.getAttribute('id');
    if (id && !/^\d/.test(id)) {
      var idSelector = '#' + cssEscape(id);
      if (uniqueSelector(idSelector)) return idSelector;
    }

    var testAttrs = ['data-testid', 'data-test', 'data-qa', 'data-cy', 'data-automation-id'];
    for (var i = 0; i < testAttrs.length; i++) {
      var testValue = el.getAttribute(testAttrs[i]);
      if (testValue) {
        var testSelector = '[' + testAttrs[i] + '=' + attrValue(testValue) + ']';
        if (uniqueSelector(testSelector)) return testSelector;
      }
    }

    var aria = el.getAttribute('aria-label');
    if (aria && aria.length < 120) {
      var ariaSelector = el.tagName.toLowerCase() + '[aria-label=' + attrValue(aria) + ']';
      if (uniqueSelector(ariaSelector)) return ariaSelector;
    }

    var name = el.getAttribute('name');
    if (name && name.length < 120 && /^[A-Za-z_][A-Za-z0-9_.:-]*$/.test(name)) {
      var nameSelector = el.tagName.toLowerCase() + '[name=' + attrValue(name) + ']';
      if (uniqueSelector(nameSelector)) return nameSelector;
    }

    var parts = [];
    var node = el;
    for (var depth = 0; node && node.nodeType === 1 && depth < 14; depth++) {
      var tag = node.tagName.toLowerCase();
      var nodeId = node.getAttribute('id');
      if (nodeId && !/^\d/.test(nodeId)) {
        var anchor = '#' + cssEscape(nodeId);
        if (uniqueSelector(anchor)) {
          parts.unshift(anchor);
          return parts.join(' > ');
        }
      }

      var parent = node.parentElement;
      var same = 0;
      var index = 1;
      if (parent) {
        for (var sib = parent.firstElementChild; sib; sib = sib.nextElementSibling) {
          if (sib.tagName === node.tagName) {
            same += 1;
            if (sib === node) index = same;
          }
        }
      }
      if (same > 1) tag += ':nth-of-type(' + index + ')';
      parts.unshift(tag);

      var candidate = parts.join(' > ');
      if (uniqueSelector(candidate)) return candidate;
      node = parent;
    }
    return parts.join(' > ');
  }

  function labelText(el) {
    var text = '';
    try {
      if (el.labels && el.labels.length) text = el.labels[0].innerText || '';
    } catch (e) {}
    if (!text) text = el.getAttribute('aria-label') || '';
    if (!text) text = el.getAttribute('placeholder') || '';
    if (!text) text = el.getAttribute('title') || '';
    if (!text && (el.tagName === 'BUTTON' || el.tagName === 'A' || el.getAttribute('role'))) {
      text = el.innerText || '';
    }
    if (!text) text = el.getAttribute('name') || '';
    return String(text).replace(/\s+/g, ' ').trim().slice(0, 90);
  }

  function baseMeta(el) {
    var meta = {
      tag: el && el.tagName ? el.tagName.toLowerCase() : '',
      input_type: el && el.getAttribute ? (el.getAttribute('type') || '') : '',
      label: labelText(el),
      name: el && el.getAttribute ? (el.getAttribute('name') || '') : '',
      aria_label: el && el.getAttribute ? (el.getAttribute('aria-label') || '') : '',
      page_url: frameUrl(),
      recorded: true
    };
    if (!isMainFrame()) {
      meta.frame_url = frameUrl();
      try { meta.frame_name = window.name || ''; } catch (e) {}
    }
    if (el && el.tagName === 'A') {
      meta.href = String(el.getAttribute('href') || '').slice(0, 500);
    }
    return meta;
  }

  function emit(kind, el, extra) {
    var payload = {
      kind: kind,
      selector: selectorFor(el),
      label: labelText(el),
      element_type: '',
      value: '',
      sensitive: false,
      meta: baseMeta(el)
    };
    if (extra) {
      for (var key in extra) {
        if (Object.prototype.hasOwnProperty.call(extra, key)) payload[key] = extra[key];
      }
    }
    send(payload);
  }

  function textLike(el) {
    if (!el || el.nodeType !== 1) return false;
    var tag = el.tagName.toLowerCase();
    if (tag === 'textarea') return true;
    if (tag === 'input') {
      var type = (el.getAttribute('type') || 'text').toLowerCase();
      return !!TEXT_TYPES[type];
    }
    return !!el.isContentEditable;
  }

  function nearestText(target) {
    if (!target || target.nodeType !== 1) return null;
    if (textLike(target)) return target;
    var editable = target.closest ? target.closest('[contenteditable="true"],[contenteditable=""]') : null;
    return editable && textLike(editable) ? editable : null;
  }

  function readValue(el) {
    var tag = el.tagName.toLowerCase();
    var type = tag === 'input'
      ? (el.getAttribute('type') || 'text').toLowerCase()
      : (tag === 'textarea' ? 'textarea' : 'contenteditable');
    var value = '';
    if (tag === 'input' || tag === 'textarea') {
      value = el.value == null ? '' : String(el.value);
    } else {
      value = String(el.innerText || el.textContent || '');
    }
    if (value.length > MAXV) value = value.slice(0, MAXV);

    if (type === 'password') {
      return { sensitive: true, key: 'PASSWORD', value: '', elementType: 'password' };
    }

    var form = el.form || (el.closest ? el.closest('form') : null);
    var hasPassword = !!(form && form.querySelector('input[type="password"]'));
    var autocomplete = (el.getAttribute('autocomplete') || '').toLowerCase();
    var haystack = [
      el.getAttribute('name'), el.getAttribute('id'),
      el.getAttribute('placeholder'), el.getAttribute('aria-label')
    ].filter(Boolean).join(' ').toLowerCase();
    var loginLike = /(login|log-in|username|user_name|user-name|email|e-mail|логин|почта)/.test(haystack);
    if (autocomplete === 'username' ||
        (hasPassword && (loginLike || autocomplete === 'email'))) {
      return { sensitive: true, key: 'LOGIN', value: '', elementType: type };
    }
    return { sensitive: false, key: '', value: value, elementType: type };
  }

  function flushEl(el) {
    var record = pending.get(el);
    if (!record) return;
    clearTimeout(record.timer);
    pending.delete(el);
    var info = readValue(el);
    var marker = info.sensitive ? '***' : info.value;
    if (el.__vwLastValue === marker) return;
    el.__vwLastValue = marker;
    emit('type', el, {
      value: info.sensitive ? '' : info.value,
      sensitive: info.sensitive,
      input_key: info.key,
      element_type: info.elementType
    });
  }

  function flushAll() {
    var elements = [];
    pending.forEach(function (_value, el) { elements.push(el); });
    for (var i = 0; i < elements.length; i++) flushEl(elements[i]);
  }

  function queueText(el) {
    var record = pending.get(el);
    if (record) clearTimeout(record.timer);
    var timer = setTimeout(function () { flushEl(el); }, 600);
    pending.set(el, { timer: timer });
  }

  function controlOf(el) {
    if (el && el.tagName === 'LABEL') {
      var control = el.control || el.querySelector('input,select,textarea,button');
      if (control) return control;
    }
    return el;
  }

  function actionable(target) {
    var el = target.closest ? target.closest(
      'a,button,input,select,textarea,label,[role="button"],[role="link"],' +
      '[role="tab"],[role="checkbox"],[role="switch"],[role="menuitem"],[onclick]'
    ) : null;
    if (el) return controlOf(el);
    var node = target;
    for (var i = 0; node && i < 4; i++, node = node.parentElement) {
      try {
        if (getComputedStyle(node).cursor === 'pointer') return node;
      } catch (e) {}
    }
    return null;
  }

  document.addEventListener('pointerdown', function (event) {
    try {
      if (event.button !== 0) return;
      var target = event.target;
      if (!target || target.nodeType !== 1) return;
      flushAll();
      var el = actionable(target);
      if (!el) return;
      var tag = el.tagName.toLowerCase();
      var type = String(el.type || '').toLowerCase();
      if (tag === 'select' || tag === 'textarea' || el.isContentEditable) return;
      if (tag === 'input' && ['checkbox', 'radio', 'file', 'range'].indexOf(type) >= 0) return;
      if (tag === 'input' && ['button', 'submit', 'reset', 'image'].indexOf(type) < 0) return;
      var kind = 'click';
      if ((tag === 'button' && type === 'submit') ||
          (tag === 'input' && (type === 'submit' || type === 'image'))) {
        kind = 'submit';
      }
      emit(kind, el, { element_type: type || tag });
    } catch (e) {}
  }, true);

  document.addEventListener('input', function (event) {
    try {
      var target = event.target;
      if (!target || target.nodeType !== 1) return;
      var el = nearestText(target);
      if (!el) return;
      if (el.tagName.toLowerCase() === 'select') return;
      queueText(el);
    } catch (e) {}
  }, true);

  document.addEventListener('change', function (event) {
    try {
      var el = event.target;
      if (!el || el.nodeType !== 1) return;
      var tag = el.tagName.toLowerCase();
      var type = String(el.type || '').toLowerCase();

      if (tag === 'select') {
        var option = el.options && el.selectedIndex >= 0 ? el.options[el.selectedIndex] : null;
        var value = el.value == null ? '' : String(el.value);
        var optionText = option
          ? String(option.textContent || '').replace(/\s+/g, ' ').trim()
          : '';
        var selectMeta = baseMeta(el);
        if (!value && optionText) {
          value = optionText;
          selectMeta.by_label = true;
        }
        if (!value) return;
        emit('select', el, { value: value, element_type: 'select', meta: selectMeta });
        return;
      }

      if (tag === 'input' && type === 'checkbox') {
        emit('check', el, {
          value: el.checked ? 'true' : 'false',
          element_type: 'checkbox'
        });
        return;
      }

      if (tag === 'input' && type === 'radio') {
        if (el.checked) emit('click', el, { element_type: 'radio' });
        return;
      }

      if (tag === 'input' && type === 'file') {
        var file = el.files && el.files[0];
        if (!file) return;
        var fileMeta = baseMeta(el);
        fileMeta.accept = String(el.accept || '');
        fileMeta.file_name = String(file.name || '');
        emit('upload', el, {
          input_key: 'FILE',
          element_type: 'file',
          meta: fileMeta
        });
        return;
      }

      if (textLike(el)) flushEl(el);
    } catch (e) {}
  }, true);

  document.addEventListener('keydown', function (event) {
    try {
      if (event.key !== 'Enter') return;
      var target = event.target;
      if (!target || target.nodeType !== 1) return;

      var button = target.closest
        ? target.closest('button,[role="button"]')
        : null;
      if (button && button.tagName === 'BUTTON') {
        var buttonType = String(button.type || '').toLowerCase();
        emit(buttonType === 'submit' ? 'submit' : 'click', button, {
          element_type: buttonType || 'button'
        });
        return;
      }

      var el = nearestText(target);
      if (!el || el.isContentEditable) return;
      if (el.tagName.toLowerCase() === 'textarea') return;
      flushEl(el);
      emit('press', el, { value: 'Enter', element_type: String(el.type || 'text') });
    } catch (e) {}
  }, true);

  window.addEventListener('pagehide', function () {
    try { flushAll(); } catch (e) {}
  });
})();
"""


@dataclass
class RecorderSession:
    """Bitta ko'rinadigan brauzer sessiyasi va unda yozilgan qadamlar."""

    start_url: str
    browser_name: str
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    status: str = "starting"
    error: str = ""
    events: list[dict] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    start_screenshot: str | None = None
    pw: object | None = None
    browser: object | None = None
    context: object | None = None
    page: object | None = None
    _record_enabled: bool = False
    _closing: bool = False
    _closed: bool = False
    _seq: int = 0
    _last_nav_url: str = ""
    _last_action_kind: str = ""
    _last_action_at: float = 0.0

    # ── Hayot sikli ──────────────────────────────────────────────────

    async def open(self) -> None:
        try:
            self.pw = await async_playwright().start()
            # Yozib olishda brauzer doim ko'rinadigan bo'lishi shart.
            self.browser = await launch_browser(
                self.pw, self.browser_name, headless=False
            )
            self.context = await self.browser.new_context(
                **context_options(self.browser_name)
            )
            await self.context.add_init_script(_RECORDER_JS)
            await self.context.expose_binding("__vwRecord", self._on_event)
            self.context.on("page", self._on_page)
            try:
                self.browser.on("disconnected", self._on_disconnected)
            except Exception:
                pass

            self.page = await self.context.new_page()
            self.page.set_default_timeout(60000)
            self._attach_page(self.page)
            await self.page.goto(
                self.start_url, wait_until="domcontentloaded", timeout=60000
            )
            try:
                await self.page.wait_for_timeout(600)
                await self.page.bring_to_front()
            except Exception:
                pass
            self.start_screenshot = await self._screenshot(self.page, 0)
            self._record_enabled = True
            self.status = "recording"
        except Exception:
            self.status = "error"
            await self.close()
            raise

    async def close(self) -> None:
        if self._closed:
            return
        self._closing = True
        self._closed = True
        if self.status not in ("closed", "error"):
            self.status = "stopped"
        try:
            if self.browser is not None:
                await asyncio.wait_for(self.browser.close(), timeout=20)
        except Exception:
            pass
        try:
            if self.pw is not None:
                await self.pw.stop()
        except Exception:
            pass
        self.browser = None
        self.context = None
        self.page = None
        self.pw = None

    def _attach_page(self, page) -> None:
        try:
            page.on("framenavigated", self._on_frame_navigated)
        except Exception:
            pass

    def _on_page(self, page) -> None:
        self._attach_page(page)

    def _on_disconnected(self, *_args) -> None:
        if self._closing:
            return
        if self.status not in ("error", "stopped"):
            self.status = "closed"
            self.error = (
                "Brauzer oynasi yopildi. Harakatlar saqlangan — shablonni "
                "saqlashingiz yoki bekor qilishingiz mumkin."
            )

    # ── Hodisalarni qabul qilish ─────────────────────────────────────

    async def _on_event(self, source, payload) -> None:
        if self._closed or not self._record_enabled or not isinstance(payload, dict):
            return
        kind = str(payload.get("kind") or "").strip().lower()
        if kind not in ALLOWED_ACTIONS:
            return
        try:
            await self._ingest(kind, payload, source)
        except Exception as exc:
            print(f"[RECORDER] event xatosi: {exc}", flush=True)

    async def _on_frame_navigated(self, frame) -> None:
        if self._closed or not self._record_enabled:
            return
        try:
            if frame != frame.page.main_frame:
                return
        except Exception:
            return
        url = ""
        try:
            url = str(frame.url or "")
        except Exception:
            pass
        if not url or not re.match(r"^https?://", url):
            return
        now = time.monotonic()
        if url == self._last_nav_url:
            return
        # Bosilgan tugma o'zi sahifani ochgan bo'lsa, ortiqcha navigate
        # qadami qo'shmaymiz — replay paytida click uni allaqachon ochadi.
        if (
            self._last_action_kind in ("click", "submit", "press")
            and now - self._last_action_at < _RECENT_ACTION_SECONDS
        ):
            self._last_nav_url = url
            return
        page = None
        try:
            page = frame.page
        except Exception:
            page = self.page
        self._last_nav_url = url
        safe_url = sanitize_url(url)
        await self._append(
            {
                "action": "navigate",
                "selector": safe_url,
                "element_type": None,
                "value_template": safe_url,
                "value_hint": None,
                "description": f"Sahifaga o'tish: {_short_url(safe_url)}",
                "input_required": False,
                "input_key": None,
                "meta": {"url": safe_url, "page_url": safe_url, "recorded": True},
            },
            page,
        )

    async def _ingest(self, kind: str, payload: dict, source) -> None:
        meta = payload.get("meta")
        meta = dict(meta) if isinstance(meta, dict) else {}
        meta["recorded"] = True
        sanitize_meta(meta)
        label = _clean_label(payload.get("label") or meta.get("label"))
        tag = _clean_label(meta.get("tag") or payload.get("element_type") or "element", 40)
        selector = _clean_label(payload.get("selector"), 4000)
        element_type = _clean_label(payload.get("element_type"), 80) or None
        sensitive = bool(payload.get("sensitive"))
        input_key = _clean_label(payload.get("input_key"), 60) or None
        raw_value = payload.get("value")
        value = "" if raw_value is None else str(raw_value)[:_MAX_VALUE]

        page = None
        try:
            page = source.get("page") if isinstance(source, dict) else None
        except Exception:
            page = None
        if page is None:
            page = self.page

        if kind == "navigate":
            return  # navigate faqat framenavigated orqali yoziladi

        if kind == "click":
            if not selector:
                return
            if await self._dedupe_click(selector):
                return
            self._touch_action("click")
            await self._append(
                {
                    "action": "click",
                    "selector": selector,
                    "element_type": element_type,
                    "value_template": None,
                    "value_hint": None,
                    "description": f"Bosish: {label or tag}",
                    "input_required": False,
                    "input_key": None,
                    "meta": meta,
                },
                page,
            )
            return

        if kind == "submit":
            if not selector:
                return
            if await self._dedupe_click(selector):
                return
            self._touch_action("submit")
            await self._append(
                {
                    "action": "submit",
                    "selector": selector,
                    "element_type": element_type or "submit",
                    "value_template": None,
                    "value_hint": None,
                    "description": f"Yuborish: {label or tag}",
                    "input_required": False,
                    "input_key": None,
                    "meta": meta,
                },
                page,
            )
            return

        if kind == "press":
            if not selector:
                return
            key = value or "Enter"
            if await self._dedupe_click(selector + "|" + key, seconds=0.8):
                return
            self._touch_action("press")
            await self._append(
                {
                    "action": "press",
                    "selector": selector,
                    "element_type": element_type,
                    "value_template": key,
                    "value_hint": None,
                    "description": f"Klaviatura: {label or tag} → {key}",
                    "input_required": False,
                    "input_key": None,
                    "meta": meta,
                },
                page,
            )
            return

        if not selector:
            return

        if kind == "type":
            if sensitive:
                value = ""
                if element_type == "password" or (input_key or "").upper() == "PASSWORD":
                    input_key = "PASSWORD"
                    hint = "Parol"
                else:
                    input_key = input_key or "LOGIN"
                    hint = "Login"
                template = "{{%s}}" % input_key
                required = True
            else:
                template = value
                hint = f"Qiymat: {label}" if label else None
                required = bool(re.search(r"\{\{\s*\w+\s*\}\}", template or ""))
            item = {
                "action": "type",
                "selector": selector,
                "element_type": element_type,
                "value_template": template,
                "value_hint": hint,
                "description": (
                    f"Maydonni to'ldirish: {label or tag}"
                    + (f" → {input_key}" if sensitive else "")
                ),
                "input_required": required,
                "input_key": input_key if required else None,
                "meta": meta,
            }
            await self._replace_or_append(
                item, selector, kinds=("type",), window=3.0, page=page
            )
            return

        if kind == "select":
            if not value:
                return
            item = {
                "action": "select",
                "selector": selector,
                "element_type": "select",
                "value_template": value,
                "value_hint": f"Tanlov: {label}" if label else None,
                "description": f"Tanlash: {label or tag} → {_clean_label(value, 60)}",
                "input_required": False,
                "input_key": None,
                "meta": meta,
            }
            await self._replace_or_append(
                item, selector, kinds=("select",), window=2.0, page=page
            )
            return

        if kind == "check":
            checked = str(value).lower() in ("true", "1", "yes", "on")
            item = {
                "action": "check",
                "selector": selector,
                "element_type": "checkbox",
                "value_template": "true" if checked else "false",
                "value_hint": None,
                "description": (
                    f"Belgilash: {label or tag} → "
                    + ("belgilandi" if checked else "olib tashlandi")
                ),
                "input_required": False,
                "input_key": None,
                "meta": meta,
            }
            await self._replace_or_append(
                item, selector, kinds=("check",), window=2.0, page=page
            )
            return

        if kind == "upload":
            item = {
                "action": "upload",
                "selector": selector,
                "element_type": "file",
                "value_template": "{{FILE}}",
                "value_hint": "Fayl yo'li (kompyuterdagi to'liq yo'l)",
                "description": f"Fayl yuklash: {label or tag} → {{{{FILE}}}}",
                "input_required": True,
                "input_key": "FILE",
                "meta": meta,
            }
            await self._replace_or_append(
                item, selector, kinds=("upload",), window=2.0, page=page
            )
            return

    def _touch_action(self, kind: str) -> None:
        self._last_action_kind = kind
        self._last_action_at = time.monotonic()

    async def _dedupe_click(self, marker: str, seconds: float = 0.6) -> bool:
        if not self.events:
            return False
        last = self.events[-1]
        if last.get("action") not in ("click", "submit", "press"):
            return False
        last_marker = str(last.get("meta", {}).get("_marker") or "")
        if last_marker != marker:
            return False
        return time.monotonic() - float(last.get("_at") or 0) < seconds

    async def _replace_or_append(
        self, item: dict, selector: str, kinds: tuple[str, ...],
        window: float, page,
    ) -> None:
        now = time.monotonic()
        if self.events:
            last = self.events[-1]
            if (
                last.get("action") in kinds
                and last.get("selector") == selector
                and now - float(last.get("_at") or 0) < window
            ):
                screenshot_path = last.get("screenshot_path")
                last.update(item)
                last["seq"] = last.get("seq")
                last["ts"] = time.strftime("%H:%M:%S")
                last["screenshot_path"] = screenshot_path
                last["_at"] = now
                last["meta"] = dict(last.get("meta") or {})
                last["meta"]["_marker"] = selector
                return
        await self._append(item, page)

    async def _append(self, item: dict, page) -> None:
        self._seq += 1
        now = time.monotonic()
        event = {
            "seq": self._seq,
            "action": item.get("action"),
            "selector": item.get("selector"),
            "element_type": item.get("element_type"),
            "value_template": item.get("value_template"),
            "value_hint": item.get("value_hint"),
            "description": item.get("description") or "",
            "input_required": bool(item.get("input_required")),
            "input_key": item.get("input_key"),
            "meta": dict(item.get("meta") or {}),
            "ts": time.strftime("%H:%M:%S"),
            "screenshot_path": None,
            "_at": now,
            "_marker": str(item.get("selector") or ""),
        }
        # Tez ketma-ket bosishlarni (_dedupe_click) aniqlash uchun marker
        # meta ichida ham saqlanadi; `public_events` "_" bilan boshlanadigan
        # kalitlarni tashqariga chiqarmaydi.
        event["meta"]["_marker"] = event["_marker"]
        self.events.append(event)
        if item.get("action") in ("click", "submit", "press"):
            self._touch_action(str(item.get("action")))
        screenshot = await self._screenshot(page, self._seq)
        if screenshot:
            event["screenshot_path"] = screenshot

    async def _screenshot(self, page, seq: int) -> str | None:
        if page is None:
            return None
        try:
            folder = os.path.join(
                settings.STORAGE_DIR, _RECORDED_SCREENSHOT_DIR, self.id
            )
            os.makedirs(folder, exist_ok=True)
            path = os.path.join(folder, f"{seq:03d}.jpg")
            await page.screenshot(path=path, type="jpeg", quality=72, timeout=6000)
            return path
        except Exception:
            return None

    # ── Natija ───────────────────────────────────────────────────────

    @property
    def current_url(self) -> str:
        try:
            if self.page is not None:
                return sanitize_url(str(self.page.url or ""))
        except Exception:
            pass
        return sanitize_url(self.start_url)

    def public_events(self) -> list[dict]:
        items: list[dict] = []
        for event in self.events:
            items.append({
                "seq": event.get("seq"),
                "action": event.get("action"),
                "selector": event.get("selector"),
                "element_type": event.get("element_type"),
                "value_template": event.get("value_template"),
                "value_hint": event.get("value_hint"),
                "description": event.get("description"),
                "input_required": event.get("input_required"),
                "input_key": event.get("input_key"),
                "meta": {
                    key: value
                    for key, value in dict(event.get("meta") or {}).items()
                    if not key.startswith("_")
                },
                "ts": event.get("ts"),
                "has_screenshot": bool(event.get("screenshot_path")),
            })
        return items

    def public(self) -> dict:
        return {
            "id": self.id,
            "status": self.status,
            "start_url": sanitize_url(self.start_url),
            "current_url": self.current_url,
            "browser": self.browser_name,
            "error": self.error or None,
            "events": self.public_events(),
            "event_count": len(self.events),
            "created_at": self.created_at,
        }

    def build_steps(self) -> list[dict]:
        """Yozilgan hodisalardan runner tushunadigan qadamlar ro'yxati."""
        safe_start = sanitize_url(self.start_url)
        steps: list[dict] = [{
            "action": "navigate",
            "selector": safe_start,
            "element_type": None,
            "value_template": safe_start,
            "value_hint": None,
            "description": f"Boshlang'ich sahifa: {_short_url(safe_start)}",
            "input_required": False,
            "input_key": None,
            "screenshot_path": self.start_screenshot,
            "meta": {
                "url": safe_start,
                "page_url": safe_start,
                "recorded": True,
                "initial": True,
            },
        }]

        last_nav = safe_start
        for event in self.events:
            action = event.get("action")
            if action == "navigate":
                url = str(event.get("value_template") or event.get("selector") or "")
                if not url or url == last_nav:
                    continue
                last_nav = url
            elif action not in ALLOWED_ACTIONS:
                continue
            meta = {
                key: value
                for key, value in dict(event.get("meta") or {}).items()
                if not key.startswith("_")
            }
            steps.append({
                "action": action,
                "selector": event.get("selector"),
                "element_type": event.get("element_type"),
                "value_template": event.get("value_template"),
                "value_hint": event.get("value_hint"),
                "description": event.get("description") or "",
                "input_required": bool(event.get("input_required")),
                "input_key": event.get("input_key"),
                "screenshot_path": event.get("screenshot_path"),
                "meta": meta,
            })
        return steps


class RecorderManager:
    """Sessiyalarni boshqaradi. Bir vaqtda bitta faol sessiya."""

    def __init__(self) -> None:
        self._sessions: dict[str, RecorderSession] = {}
        self._lock = asyncio.Lock()

    def get(self, session_id: str) -> RecorderSession | None:
        return self._sessions.get(session_id)

    def active(self) -> RecorderSession | None:
        for session in self._sessions.values():
            return session
        return None

    async def start(self, url: str, browser: str | None = None) -> RecorderSession:
        normalized_url = normalize_start_url(url)
        browser_name = normalize_browser(browser)
        async with self._lock:
            existing = self.active()
            if existing is not None:
                raise RecorderBusy(
                    "Allaqachon ochiq yozib olish sessiyasi bor. Avval uni "
                    "saqlang yoki bekor qiling."
                )
            session = RecorderSession(normalized_url, browser_name)
            try:
                await session.open()
            except Exception as exc:
                await session.close()
                raise RecorderError(
                    f"Brauzerni ochib bo'lmadi: {exc}"
                ) from exc
            self._sessions[session.id] = session
            return session

    async def stop(self, session_id: str) -> RecorderSession:
        session = self._sessions.pop(session_id, None)
        if session is None:
            raise RecorderError("Yozib olish sessiyasi topilmadi.")
        await session.close()
        return session

    async def cancel(self, session_id: str) -> None:
        session = self._sessions.pop(session_id, None)
        if session is None:
            raise RecorderError("Yozib olish sessiyasi topilmadi.")
        await session.close()
        self.remove_screenshots(session_id)

    @staticmethod
    def remove_screenshots(session_id: str) -> None:
        shutil.rmtree(
            os.path.join(
                settings.STORAGE_DIR, _RECORDED_SCREENSHOT_DIR, session_id
            ),
            ignore_errors=True,
        )

    async def shutdown(self) -> None:
        sessions = list(self._sessions.values())
        self._sessions.clear()
        for session in sessions:
            try:
                await session.close()
            except Exception:
                pass


recorder = RecorderManager()
