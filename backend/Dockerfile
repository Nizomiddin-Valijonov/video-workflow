FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# OCR va OpenCV uchun tizim paketlari. Playwright'ning brauzer
# kutubxonalarini `--with-deps` o'zi (distroga moslab) o'rnatadi.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgl1 libglib2.0-0 \
        tesseract-ocr libtesseract-dev \
        wget ca-certificates \
        fonts-unifont fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

# Uchala engine: Chromium, Firefox va WebKit (Safari yadrosi).
RUN playwright install --with-deps chromium firefox webkit \
    && rm -rf /var/lib/apt/lists/*

COPY . .

ENV PYTHONPATH=/app

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]