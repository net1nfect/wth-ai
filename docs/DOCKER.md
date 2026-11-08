# Docker deployment for WTH-AI

## Quick start

```bash
cp .env.docker.example .env
# Edit .env with your API keys
docker compose up -d wth-ai-worker 9router
docker compose exec wth-ai-cli wth-ai setup
docker compose exec wth-ai-cli wth-ai engage start --mode ctf --target 10.10.10.1
```

## Services

| Service | Purpose |
|---------|---------|
| wth-ai-worker | Job processor daemon |
| wth-ai-cli | Interactive shell (`docker compose exec wth-ai-cli wth-ai`) |
| 9router | Local LLM gateway (OpenAI-compatible, port 20128) |
| ollama | Optional local models (`--profile ollama`) |

## Environment

Set `ROUTER9_API_KEY` and other provider keys in `.env`. Secrets are migrated to vault on first boot.

## Volumes

- `wth-ai-home` → `/data/wth-ai` (config, vault, jobs, wth-ai-results)
