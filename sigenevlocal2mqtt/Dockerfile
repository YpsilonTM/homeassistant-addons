FROM ghcr.io/home-assistant/base:3.24

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN apk add --no-cache python3 py3-pip \
    && python3 -m venv /opt/venv

ENV PATH="/opt/venv/bin:${PATH}"

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r /app/requirements.txt

COPY src /app/src
COPY run.sh /run.sh
RUN chmod +x /run.sh

CMD ["/run.sh"]
