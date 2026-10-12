#!/usr/bin/env bash
# Fence the build sandbox off from this host's own services (ICCINT-21).
#
# WHY THIS EXISTS. A build turn runs in a container that sees only its own project — but it must still
# reach the internet (the Claude MAX endpoint dies without it), and the way out also leads back IN: to
# every service listening on this machine. Measured 24.08.2026 from a container on a sandbox network:
# the Studio API and **SSH** both answered. No credentials were handed over, but "no password" is a hope,
# not a lock, and what we are guarding against is a MISTAKE, which needs no password.
#
# WHAT IT DOES. Sandbox networks live in a reserved range (SANDBOX_NET). From there:
#   • the internet stays open        — the turn cannot work without it;
#   • the build's OWN network stays open — that is where its throwaway PostgreSQL lives;
#   • the search index and the local model stay open — the turn legitimately uses them;
#   • everything else on this machine is dropped.
#
# WHY TWO CHAINS. Two different paths reach this host and only one chain sees each:
#   • services the HOST itself listens on (ssh, mail, …) are locally destined → INPUT;
#   • services DOCKER publishes (Studio API, Grafana, the databases, …) are address-translated to a
#     container → FORWARD, i.e. DOCKER-USER.
# A rule in only one of them looks like protection and is not. That was the original plan, and the
# measurement is what caught it.
#
# WHY ctorigdstport. A published port is REWRITTEN on the way in (9130 → the container's own 6333), so a
# rule matching the published number never fires. ``--ctorigdstport`` matches what the sandbox actually
# ASKED for, before translation — which is also the honest way to express the intent.
#
# Idempotent on purpose: docker rewrites its chains whenever the daemon restarts, so this is re-applied
# by a timer. A protection that can vanish silently is worse than none, because it is relied upon.
set -euo pipefail

SANDBOX_NET="10.77.0.0/16"
IN_CHAIN="NEX-SANDBOX"
FWD_CHAIN="NEX-SANDBOX-FWD"
# Ports the sandbox legitimately uses on this host, as the sandbox asks for them (pre-translation).
ALLOW_PORTS=(9130 9132)          # search index (Qdrant), local model (Ollama)
PRIVATE=(10.0.0.0/8 172.16.0.0/12 192.168.0.0/16 100.64.0.0/10 169.254.0.0/16)

ipt() { iptables "$@"; }

# ── host's own services ──────────────────────────────────────────────────────
ipt -N "$IN_CHAIN" 2>/dev/null || true
ipt -F "$IN_CHAIN"
for p in "${ALLOW_PORTS[@]}"; do ipt -A "$IN_CHAIN" -p tcp --dport "$p" -j RETURN; done
ipt -A "$IN_CHAIN" -j DROP
ipt -C INPUT -s "$SANDBOX_NET" -j "$IN_CHAIN" 2>/dev/null || ipt -I INPUT 1 -s "$SANDBOX_NET" -j "$IN_CHAIN"

# ── services docker publishes ────────────────────────────────────────────────
ipt -N "$FWD_CHAIN" 2>/dev/null || true
ipt -F "$FWD_CHAIN"
ipt -A "$FWD_CHAIN" -d "$SANDBOX_NET" -j RETURN                    # the build's own network
for p in "${ALLOW_PORTS[@]}"; do
    ipt -A "$FWD_CHAIN" -p tcp -m conntrack --ctorigdstport "$p" -j RETURN
done
for net in "${PRIVATE[@]}"; do ipt -A "$FWD_CHAIN" -d "$net" -j DROP; done
ipt -A "$FWD_CHAIN" -j RETURN                                      # the internet
ipt -C DOCKER-USER -s "$SANDBOX_NET" -j "$FWD_CHAIN" 2>/dev/null || ipt -I DOCKER-USER 1 -s "$SANDBOX_NET" -j "$FWD_CHAIN"

# ── the search index and the local model, from outside docker (ICCINT-231) ──────────────────────────────
# nex-qdrant (9130 REST, 9131 gRPC) and nex-ollama (9132) are published on every interface and have no
# password. Measured 12.10.2026: Máger Dedo read the whole Knowledge Base index from MAGER over Tailscale,
# and the tailnet holds 25 devices, MÁGERSTAV's computers among them; the office LAN (eno4) and the Windows VM
# (virbr0) reach them the same way. Only the cockpit and the build sandboxes need them — through a docker
# bridge (QDRANT_URL / OLLAMA_URL point at a bridge gateway) — and the host itself through localhost.
# Binding the ports to 127.0.0.1 would cut the cockpit off, so the ports stay published and only docker's own
# interfaces and loopback may use them.
#
# WHY THE RAW TABLE. The first version sat in DOCKER-USER (IPv4) and INPUT (IPv6). Máger Dedo measured it
# from the tailnet: IPv6 closed, IPv4 still open — Tailscale's ts-forward is FIRST in FORWARD and accepts
# everything from tailscale0 before DOCKER-USER is ever reached, and tailscaled puts its jumps back on top
# whenever it restarts. raw PREROUTING runs before address translation and before both docker's and
# Tailscale's filter rules; docker's own raw rules only drop, so nothing ahead of this can accept the packet.
# The port is still the published one (9130), not the container's.
RAG_CHAIN="NEX-RAG-PORTS"
RAG_PORTS="9130:9132"

for ipt_cmd in iptables ip6tables; do
    "$ipt_cmd" -t raw -N "$RAG_CHAIN" 2>/dev/null || true
    "$ipt_cmd" -t raw -F "$RAG_CHAIN"
    "$ipt_cmd" -t raw -A "$RAG_CHAIN" -i lo -j RETURN
    "$ipt_cmd" -t raw -A "$RAG_CHAIN" -i docker0 -j RETURN
    "$ipt_cmd" -t raw -A "$RAG_CHAIN" -i br-+ -j RETURN
    "$ipt_cmd" -t raw -A "$RAG_CHAIN" -j DROP
    "$ipt_cmd" -t raw -C PREROUTING -p tcp --dport "$RAG_PORTS" -j "$RAG_CHAIN" 2>/dev/null \
        || "$ipt_cmd" -t raw -I PREROUTING 1 -p tcp --dport "$RAG_PORTS" -j "$RAG_CHAIN"
done

# The first version's rules, gone where they still stand (DOCKER-USER for IPv4, INPUT for IPv6).
while iptables -D DOCKER-USER -p tcp -m conntrack --ctorigdstport "$RAG_PORTS" -j "$RAG_CHAIN" 2>/dev/null; do :; done
while ip6tables -D INPUT -p tcp --dport "$RAG_PORTS" -j "$RAG_CHAIN" 2>/dev/null; do :; done
for ipt_cmd in iptables ip6tables; do
    "$ipt_cmd" -t filter -F "$RAG_CHAIN" 2>/dev/null || true
    "$ipt_cmd" -t filter -X "$RAG_CHAIN" 2>/dev/null || true
done
