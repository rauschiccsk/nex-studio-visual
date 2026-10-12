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

### The search index and the local model (ICCINT-231)

The same script keeps nex-qdrant (9130, 9131) and nex-ollama (9132) — published on every interface, without a
password — for docker's own interfaces and loopback: chain `NEX-RAG-PORTS` in the **raw** table, jumped to from
PREROUTING, for IPv4 and IPv6. Tailscale, the office LAN and the Windows VM get nothing; the cockpit, the build
sandboxes and the host itself keep both. Not in DOCKER-USER: Tailscale's `ts-forward` is first in FORWARD and
accepts everything from the tailnet before DOCKER-USER is reached (measured by Máger Dedo, 12.10.2026).

Verify: from the cockpit container and from a sandbox network `http://192.168.32.1:9130/collections` and
`:9132/api/version` answer 200; from another machine on the tailnet (MAGER) `http://100.107.134.104:9130/`,
`:9131`, `:9132` and the IPv6 address on 9130 and 9132 time out.

Remove it (rollback) — delete the section from the script first, or the timer puts it back within a minute:

```bash
for t in iptables ip6tables; do
  sudo $t -t raw -D PREROUTING -p tcp --dport 9130:9132 -j NEX-RAG-PORTS
  sudo $t -t raw -F NEX-RAG-PORTS && sudo $t -t raw -X NEX-RAG-PORTS
done
```

Remove the sandbox fence (rollback):

```bash
sudo systemctl disable --now nex-sandbox-firewall.timer
sudo iptables -D INPUT -s 10.77.0.0/16 -j NEX-SANDBOX
sudo iptables -D DOCKER-USER -s 10.77.0.0/16 -j NEX-SANDBOX-FWD
sudo iptables -F NEX-SANDBOX && sudo iptables -X NEX-SANDBOX
sudo iptables -F NEX-SANDBOX-FWD && sudo iptables -X NEX-SANDBOX-FWD
```
