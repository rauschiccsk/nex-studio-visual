# Host-side pieces (not shipped in the image)

These live on the machine, not in a container, because they change the host's firewall — which is exactly
what NEX Studio must NOT be able to do from inside a container. Versioned here so the rule is reviewable
and reproducible; installed by hand, once.

## `nex-sandbox-firewall.sh` — the build-sandbox fence (ICCINT-21)

A build turn sees only its own project, but it must reach the internet, and the way out also leads back in:
to every service on this machine. Measured 24.08.2026 from a container on a sandbox network — the Studio
API and **SSH** both answered.

The script drops that traffic, keeping open what the turn genuinely needs. It is idempotent, and it has to
be: docker rewrites its chains on every daemon restart, so the timer re-applies it every minute. A
protection that vanishes quietly is worse than none, because it is relied upon.

```bash
sudo install -m 0755 infra/host/nex-sandbox-firewall.sh /usr/local/sbin/
sudo install -m 0644 infra/host/nex-sandbox-firewall.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now nex-sandbox-firewall.timer
```

Verify — from a container on a sandbox network (`10.77.0.0/16`):

```bash
docker network create --subnet 10.77.250.0/24 probe
docker run --rm --network probe --entrypoint python3 nex-studio-visual-backend:vX -c "..."
docker network rm probe
```

Expected: the Studio API, ssh and this host's PostgreSQL time out; Qdrant (9130), Ollama (9132),
`api.anthropic.com` and the build's own network answer.

Remove the fence (rollback):

```bash
sudo systemctl disable --now nex-sandbox-firewall.timer
sudo iptables -D INPUT -s 10.77.0.0/16 -j NEX-SANDBOX
sudo iptables -D DOCKER-USER -s 10.77.0.0/16 -j NEX-SANDBOX-FWD
sudo iptables -F NEX-SANDBOX && sudo iptables -X NEX-SANDBOX
sudo iptables -F NEX-SANDBOX-FWD && sudo iptables -X NEX-SANDBOX-FWD
```
