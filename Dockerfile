FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends libpulse0 pulseaudio-utils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY hueme/ ./hueme/
COPY hueme_studio/ ./hueme_studio/
COPY effects/ ./effects/
COPY config/rigs/ ./config/rigs/
COPY config/shows/ ./config/shows/
COPY run.py .

RUN mkdir -p /app/config && chown -R 1000:1000 /app
USER 1000:1000

EXPOSE 8090/tcp 4048/udp 5568/udp 21324/udp 2391/udp

HEALTHCHECK --interval=15s --timeout=3s --start-period=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8090/api/health', timeout=2).read()"

CMD ["python", "-m", "hueme_studio", "--host", "0.0.0.0", "--port", "8090"]
