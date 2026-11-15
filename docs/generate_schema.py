#!/usr/bin/env python3
"""VideoWorkflow Studio uchun ishlash sxemasini PNG rasm qilib chizadi.

Ishlatish:  python3 docs/generate_schema.py
Natija:     docs/sxema.png
"""
import math
import os

from PIL import Image, ImageDraw, ImageFont

FONT_DIR = "/usr/share/fonts/truetype/dejavu"
W, H = 2000, 1470

BG = "#f1f5f9"
INK = "#0f172a"
MUTED = "#475569"

BLUE_F, BLUE_B = "#dbeafe", "#2563eb"
GREEN_F, GREEN_B = "#dcfce7", "#16a34a"
AMBER_F, AMBER_B = "#fef3c7", "#d97706"
PURPLE_F, PURPLE_B = "#ede9fe", "#7c3aed"
ROSE_F, ROSE_B = "#fee2e2", "#dc2626"
SLATE_F, SLATE_B = "#e2e8f0", "#64748b"


def font(size, bold=False):
    name = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
    return ImageFont.truetype(os.path.join(FONT_DIR, name), size)


img = Image.new("RGB", (W, H), BG)
d = ImageDraw.Draw(img)


def rbox(x, y, w, h, fill, border, radius=18, width=3):
    d.rounded_rectangle([x, y, x + w, y + h], radius=radius, fill=fill,
                        outline=border, width=width)


def center_text(cx, top, lines, size=21, bold=False, color=INK, gap=9):
    f = font(size, bold)
    cy = top
    for ln in lines:
        bbox = d.textbbox((0, 0), ln, font=f)
        w = bbox[2] - bbox[0]
        h = bbox[3] - bbox[1]
        d.text((cx - w / 2, cy), ln, font=f, fill=color)
        cy += h + gap
    return cy


def left_text(x, top, lines, size=21, bold=False, color=INK, gap=9):
    f = font(size, bold)
    cy = top
    for ln in lines:
        bbox = d.textbbox((0, 0), ln, font=f)
        h = bbox[3] - bbox[1]
        d.text((x, cy), ln, font=f, fill=color)
        cy += h + gap
    return cy


def arrow(x1, y1, x2, y2, color="#334155", width=5, head=16):
    d.line([x1, y1, x2, y2], fill=color, width=width)
    ang = math.atan2(y2 - y1, x2 - x1)
    for rot in (math.radians(150), math.radians(-150)):
        hx = x2 + head * math.cos(ang + rot)
        hy = y2 + head * math.sin(ang + rot)
        d.line([x2, y2, hx, hy], fill=color, width=width)


def arrow_label(cx, cy, text, size=19, color="#b45309", bold=True):
    f = font(size, bold)
    bbox = d.textbbox((0, 0), text, font=f)
    w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    pad = 6
    d.rounded_rectangle([cx - w / 2 - pad, cy - h / 2 - pad, cx + w / 2 + pad, cy + h / 2 + pad],
                        radius=8, fill="#fef9c3", outline="#facc15", width=2)
    d.text((cx - w / 2, cy - h / 2), text, font=f, fill=color)


# ── Sarlavha ─────────────────────────────────────────────────────────────
center_text(W / 2, 40, ["VideoWorkflow Studio — tizim qanday ishlaydi"], size=46, bold=True)
center_text(W / 2, 108, ["Video  →  AI tahlil (qadamlar)  →  brauzerda avtomatik ijro (run)"],
            size=25, color=MUTED)

# ── 1-BOSQICH: TAHLIL ────────────────────────────────────────────────────
left_text(140, 168, ["1-BOSQICH — TAHLIL"], size=30, bold=True, color="#1d4ed8")

row1_y, bw, bh = 222, 385, 280
xs = [140, 585, 1030, 1475]

boxes1 = [
    ("1 · Yuklash", BLUE_F, BLUE_B, [
        "Foydalanuvchi video",
        "tanlaydi va yuklaydi",
        "",
        "Frontend (Next.js :3000)",
    ]),
    ("2 · Backend API", SLATE_F, SLATE_B, [
        "POST /api/jobs — video",
        "qabul qilinadi",
        "",
        "PostgreSQL: job yozuvi",
        "Redis: navbatga qo'shiladi",
    ]),
    ("3 · Analyzer", AMBER_F, AMBER_B, [
        "Kadrlar ajratiladi (OpenCV)",
        "Har bir kadr AI'ga yuboriladi",
        "",
        "Mahalliy Ollama yoki",
        "bulut OpenRouter orqali",
    ]),
    ("4 · AI tahlil", PURPLE_F, PURPLE_B, [
        "Rasm → qadam (JSON):",
        "click / type / navigate...",
        "",
        "gemini-2.5-flash (bulut)",
        "yoki minicpm-v (lokal)",
    ]),
]

for (x, (title, fill, border, lines)) in zip(xs, boxes1):
    rbox(x, row1_y, bw, bh, fill, border)
    center_text(x + bw / 2, row1_y + 22, [title], size=28, bold=True, color=border)
    center_text(x + bw / 2, row1_y + 78, lines, size=21, color=INK)

for i in range(3):
    x1 = xs[i] + bw
    x2 = xs[i + 1]
    arrow(x1 + 8, row1_y + bh / 2, x2 - 8, row1_y + bh / 2)

# Qadamlar saqlanishi — keng banner
by = row1_y + bh + 40
rbox(140, by, 1720, 86, GREEN_F, GREEN_B)
center_text(W / 2, by + 14, [
    "Qadamlar (workflow_steps) bazaga saqlanadi  →  job holati: analyzed  →  WebSocket orqali frontendga xabar boradi",
], size=24, bold=True, color="#166534")
arrow(xs[3] + bw / 2, row1_y + bh + 4, xs[3] + bw / 2, by - 6)

# ── 2-BOSQICH: IJRO ──────────────────────────────────────────────────────
sec2_y = by + 86 + 42
left_text(140, sec2_y, ["2-BOSQICH — IJRO (RUN)"], size=30, bold=True, color="#15803d")

row2_y = sec2_y + 54
boxes2 = [
    ("5 · Run boshlash", BLUE_F, BLUE_B, [
        "POST /api/jobs/{id}/run",
        "",
        "Redis: runs navbatiga",
        "run vazifasi qo'yiladi",
    ]),
    ("6 · Runner", AMBER_F, AMBER_B, [
        "Playwright + Chromium",
        "brauzer ochiladi",
        "",
        "navigate → click → type",
        "→ submit ketma-ketligi",
    ]),
    ("7 · Jonli loglar", PURPLE_F, PURPLE_B, [
        "Har qadam logi, screenshot",
        "va status o'zgarishi",
        "",
        "WebSocket orqali frontendga",
        "darhol yetib boradi",
    ]),
    ("8 · Natija", GREEN_F, GREEN_B, [
        "Run: completed / failed",
        "",
        "Barcha loglar va qadamlar",
        "tarixi bazada saqlanadi",
    ]),
]

for (x, (title, fill, border, lines)) in zip(xs, boxes2):
    rbox(x, row2_y, bw, bh, fill, border)
    center_text(x + bw / 2, row2_y + 22, [title], size=28, bold=True, color=border)
    center_text(x + bw / 2, row2_y + 78, lines, size=21, color=INK)

for i in range(3):
    x1 = xs[i] + bw
    x2 = xs[i + 1]
    arrow(x1 + 8, row2_y + bh / 2, x2 - 8, row2_y + bh / 2)

arrow(330, by + 86 + 6, 330, row2_y - 6)

# ── 3-QISM: topilgan muammo va yechim ────────────────────────────────────
sec3_y = row2_y + bh + 48
left_text(140, sec3_y, ["TOPILGAN MUAMMO VA YECHIM —  host.docker.internal"], size=30, bold=True, color="#b91c1c")

py = sec3_y + 56
ph = 250
# Chap panel — xato
rbox(140, py, 830, ph, "#fff1f2", ROSE_B, radius=20, width=3)
left_text(170, py + 16, ["❌  OLDIN — xato"], size=26, bold=True, color="#b91c1c")
rbox(170, py + 78, 230, 66, "#ffffff", ROSE_B)
center_text(285, py + 92, ["Analyzer", "konteyner"], size=20)
rbox(660, py + 78, 280, 66, "#ffffff", ROSE_B)
center_text(800, py + 92, ["Ollama :11434", "(hostdagi AI)"], size=20)
arrow(400 + 14, py + 111, 660 - 10, py + 111, color=ROSE_B)
center_text(560, py + 82, ["host.docker.internal"], size=19, bold=True, color=ROSE_B)
left_text(170, py + 166, [
    "\"Failed to resolve 'host.docker.internal'\"",
    "Linux Docker'da bu nom mavjud emas → DNS xatosi",
], size=20, color="#7f1d1d")

# O'ng panel — yechim
rbox(1030, py, 830, ph, "#f0fdf4", GREEN_B, radius=20, width=3)
left_text(1060, py + 16, ["✅  ENDI — yechim"], size=26, bold=True, color="#15803d")
rbox(1060, py + 78, 230, 66, "#ffffff", GREEN_B)
center_text(1175, py + 92, ["Analyzer", "konteyner"], size=20)
rbox(1560, py + 78, 280, 66, "#ffffff", GREEN_B)
center_text(1700, py + 92, ["Ollama :11434", "(hostdagi AI)"], size=20)
arrow(1290 + 14, py + 111, 1560 - 10, py + 111, color=GREEN_B)
center_text(1450, py + 82, ["host-gateway → 172.17.0.1"], size=18, bold=True, color="#166534")
left_text(1060, py + 166, [
    "docker-compose.yml → extra_hosts:",
    "  \"host.docker.internal:host-gateway\"",
    "Bulut AI (OpenRouter) ishlatilsa — bu muammo umuman yo'q",
], size=20, color="#14532d")

# ── Footer ───────────────────────────────────────────────────────────────
center_text(W / 2, H - 42, ["VideoWorkflow Studio v2  ·  sxema: 19.10.2026  ·  docs/sxema.png"],
            size=19, color="#94a3b8")

out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sxema.png")
img.save(out)
print("Saqlandi:", out, img.size)
