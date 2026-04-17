# Architect AI Setup Guide

This guide explains how to configure Claude CLI for NEX Studio's Architect AI feature
on the ANDROS server.

## Prerequisites

- **Claude MAX subscription** — Claude CLI requires an active Claude MAX plan.
  Standard Claude Pro plans do not include CLI access.
- **Docker & Docker Compose** — the backend runs in a container.
- **Host access** — SSH access to the ANDROS server as user `andros`.

## Step 1: Install Claude CLI on the Host

If Claude CLI is not yet installed on the host machine:

```bash
# Install via npm (recommended)
npm install -g @anthropic-ai/claude-cli

# Verify installation
claude --version
```

## Step 2: Authenticate Claude CLI

Run the authentication flow on the host machine (as the `andros` user):

```bash
claude auth login
```

This opens a browser-based OAuth flow. After successful authentication, Claude stores
its credentials in `~/.claude/` (i.e., `/home/andros/.claude/`).

### What gets created

After authentication, the following files appear:

```
/home/andros/.claude/
├── credentials.json    # OAuth tokens (sensitive!)
├── config.json         # CLI configuration
└── ...                 # Other session/cache files
```

> **Security note**: The `credentials.json` file contains sensitive tokens.
> Protect it with appropriate file permissions:
> ```bash
> chmod 600 /home/andros/.claude/credentials.json
> chmod 700 /home/andros/.claude/
> ```

## Step 3: Verify the Volume Mount

The `docker-compose.yml` already contains the correct volume mount:

```yaml
backend:
  volumes:
    - /home/andros/.claude:/root/.claude:ro
```

This mounts the host's Claude config directory into the container as **read-only** (`ro`),
so the backend can use Claude CLI without being able to modify the host credentials.

### Verify the mount is active

```bash
# Check if the volume is mounted
docker compose exec backend ls -la /root/.claude/

# Expected output: credentials.json, config.json, etc.
```

If the directory is empty or missing, restart the backend:

```bash
docker compose restart backend
```

## Step 4: Verify Claude CLI Inside the Container

```bash
# Check Claude CLI is available
docker compose exec backend claude --version

# Test a simple prompt (optional — uses your subscription quota)
docker compose exec backend claude -p "Say hello in one word"
```

### Expected results

- `claude --version` should print a version string (e.g., `1.x.x`).
- The test prompt should return a response without authentication errors.

## Step 5: Environment Variables

The following environment variables are set in `docker-compose.yml` and control
how the backend locates Claude CLI:

| Variable | Value | Purpose |
|----------|-------|---------|
| `CLAUDE_CONFIG_DIR` | `/root/.claude` | Tells Claude CLI where to find credentials |
| `CLAUDE_CLI_PATH` | `claude` | Path to the Claude CLI binary |

These should not need modification unless the container's filesystem layout changes.

## Troubleshooting

### Problem: `claude: command not found`

**Cause**: Claude CLI is not installed in the backend Docker image.

**Fix**: Ensure the backend Dockerfile installs Claude CLI:
```dockerfile
RUN npm install -g @anthropic-ai/claude-cli
```

Rebuild the image:
```bash
docker compose build backend
docker compose up -d backend
```

### Problem: `Authentication required` or `Invalid token`

**Cause**: The credentials on the host are expired or missing.

**Fix**:
```bash
# On the host (not inside the container)
claude auth login

# Then restart the backend to pick up new credentials
docker compose restart backend
```

### Problem: Empty `/root/.claude/` inside container

**Cause**: The host directory `/home/andros/.claude/` does not exist or Docker cannot
read it.

**Fix**:
```bash
# Verify the directory exists on the host
ls -la /home/andros/.claude/

# If missing, authenticate first (Step 2)
claude auth login

# Verify Docker can access it
docker compose exec backend ls -la /root/.claude/
```

### Problem: Permission denied errors

**Cause**: Docker cannot read the host directory due to file permissions.

**Fix**:
```bash
# Ensure the andros user owns the directory
chown -R andros:andros /home/andros/.claude/

# Ensure directory is readable
chmod 700 /home/andros/.claude/
chmod 600 /home/andros/.claude/credentials.json
```

### Problem: Architect responses are empty or timeout

**Cause**: Claude MAX subscription may be inactive, or network issues prevent
the container from reaching Claude's API.

**Fix**:
```bash
# Test connectivity from inside the container
docker compose exec backend curl -sI https://api.anthropic.com

# Test Claude CLI directly
docker compose exec backend claude -p "ping"

# Check backend logs for errors
docker compose logs backend --tail=50
```

## Token Refresh

Claude MAX tokens are long-lived but may expire. If Architect features stop working:

1. Re-authenticate on the host: `claude auth login`
2. Restart the backend: `docker compose restart backend`
3. Verify: `docker compose exec backend claude --version`

No container rebuild is needed — the volume mount picks up changes immediately
after container restart.
