# NEX Studio

Project management and AI delegation platform.

## Quick Start

```bash
# Start all services
docker compose up -d

# From the HOST, reach it on the PUBLISHED ports — 9176/9177 are what the containers listen on
# INTERNALLY and nothing on the host answers there:
# Backend:  http://localhost:9216
# Frontend: http://localhost:9217
```

## Prerequisites

- Docker & Docker Compose
- PostgreSQL 16 (runs in container)
- Python 3.12+ (for local development)
- Node.js 20+ (for frontend development)
- Claude MAX subscription (for Architect AI features)

## Architecture

Two numbers per service, and confusing them is why this table was wrong: the container LISTENS on one
port and the host PUBLISHES it on another. Use the host column; the container column matters only when
you are inside the network (a healthcheck, a sibling container).

| Service  | Host | In container | Description                      |
|----------|------|--------------|----------------------------------|
| Backend  | 9216 | 9176         | FastAPI REST API                 |
| Frontend | 9217 | 9177         | React SPA (nginx, prod build)    |
| Database | 9218 | 5432         | PostgreSQL 16                    |

## Architect AI Configuration

NEX Studio uses Claude CLI to power the Architect AI feature. This requires a valid
Claude MAX subscription and proper volume mount configuration.

### Setup Summary

1. **Claude MAX subscription** — required on the host machine where the container runs.
2. **Authenticate Claude CLI** on the host:
   ```bash
   claude auth login
   ```
3. **Volume mount** — `docker-compose.yml` mounts the host Claude config into the container:
   ```yaml
   volumes:
     - /home/andros/.claude:/root/.claude:ro
   ```
4. **Verify inside container**:
   ```bash
   docker compose exec backend claude --version
   ```

For a detailed step-by-step guide, see [docs/ARCHITECT_SETUP.md](docs/ARCHITECT_SETUP.md).

### Troubleshooting

| Symptom | Fix |
|---------|-----|
| `claude: command not found` inside container | Ensure Claude CLI is installed in the backend Docker image |
| `Authentication required` errors | Re-run `claude auth login` on the host, restart the backend container |
| Permission denied on `/root/.claude` | Check that host directory `/home/andros/.claude` is readable by Docker |
| Architect chat returns empty responses | Verify `CLAUDE_CONFIG_DIR=/root/.claude` is set in container env |

## Development

### Backend (Poetry)

```bash
cd /opt/projects/nex-studio-visual
poetry install --no-interaction
poetry run pytest
```

### Frontend

```bash
cd frontend
npm install
npm run dev
```

### Linting

```bash
ruff check .
ruff format --check .
```

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | `postgresql+pg8000://...` | PostgreSQL connection string (pg8000 driver) |
| `TEST_DATABASE_URL` | `postgresql+pg8000://...` | Test database connection string |
| `SECRET_KEY` | `change-me-in-production` | JWT signing key |
| `BACKEND_PORT` | `9176` | Backend port as OTHER code reads it — the image hardcodes where it actually listens; changing this changes nothing |
| `FRONTEND_PORT` | `9177` | Frontend port as OTHER code reads it — nginx hardcodes where it actually listens; changing this changes nothing |
| `CLAUDE_CONFIG_DIR` | `/root/.claude` | Claude CLI config directory inside container |
| `CLAUDE_CLI_PATH` | `claude` | Path to Claude CLI binary |

## License

Proprietary — ICC s.r.o.
