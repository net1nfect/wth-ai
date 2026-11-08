FROM python:3.12-slim AS builder

WORKDIR /build
COPY pyproject.toml requirements.txt ./
COPY wth_ai ./wth_ai
COPY config ./config
COPY prompts ./prompts
COPY wth-ai-main.py ./
RUN pip install --no-cache-dir -r requirements.txt && pip install --no-cache-dir -e ".[full]"

FROM python:3.12-slim AS runtime

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl dig host whois openssl ca-certificates nmap \
    && rm -rf /var/lib/apt/lists/*

RUN useradd -m -u 1000 wth-ai
WORKDIR /app

COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin
COPY . /app

RUN chown -R wth-ai:wth-ai /app
USER wth-ai

ENV WTH_HOME=/data/wth-ai
ENV PYTHONUNBUFFERED=1
ENV WTH_UNRESTRICTED=1

ENTRYPOINT ["/app/docker/entrypoint.sh"]
CMD ["wth-ai", "worker", "run", "--no-drain"]
