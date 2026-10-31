# Szerverpult: webes kezelőfelület (FastAPI) + webes terminál (ttyd).
FROM python:3.12-alpine

RUN apk add --no-cache bash docker-cli docker-cli-compose git ttyd tzdata util-linux-misc \
    && git config --system --add safe.directory '*'

WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app

ENV PULT_DATA_DIR=/data PYTHONUNBUFFERED=1
EXPOSE 8080

# Csak a konténer saját loopback-címén figyel; kívülről a Tailscale Serve éri el.
CMD ["uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8080", "--proxy-headers"]
