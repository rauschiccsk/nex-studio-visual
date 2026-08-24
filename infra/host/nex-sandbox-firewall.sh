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
