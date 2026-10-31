# Emby-könyvtár – Stremio-bővítmény a saját Emby-szerverekhez.
FROM python:3.12-alpine

RUN apk add --no-cache tzdata

WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app

ENV EMBY_DATA_DIR=/data PYTHONUNBUFFERED=1
EXPOSE 7000

# Csak a konténer loopback-címén figyel; kívülről a Tailscale Funnel éri el.
CMD ["uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "7000", "--proxy-headers"]
