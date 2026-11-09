FROM python:3.13-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends libpulse0 pulseaudio-utils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY hueme/ ./hueme/
COPY effects/ ./effects/
COPY run.py .

RUN mkdir -p /config && chown -R 1000:1000 /app /config
USER 1000:1000

EXPOSE 8080/tcp 4048/udp 5568/udp 21324/udp 2391/udp

HEALTHCHECK --interval=15s --timeout=3s --start-period=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/api/health', timeout=2).read()"

CMD ["python", "run.py", "--config", "/config/gateway.json"]
