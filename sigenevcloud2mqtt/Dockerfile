FROM ghcr.io/home-assistant/base:3.23

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN apk add --no-cache python3 py3-pip \
    && python3 -m venv /opt/venv

ENV PATH="/opt/venv/bin:${PATH}"

RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir \
    "aiohttp>=3.9" \
    "paho-mqtt>=2.1" \
    "pycryptodome>=3.20"

COPY src /app/src
COPY run.sh /run.sh
RUN chmod +x /run.sh

CMD ["/run.sh"]
