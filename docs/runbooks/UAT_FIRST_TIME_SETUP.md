# UAT first-time setup (ANDROS host)

Per F-003 §3 + Dedo approval 2026-05-22 Q2 — pre prvý beh `python scripts/uat-deploy.py <slug>` musí byť pripravená host-level infraštruktúra. Implementer scope nezahŕňa sudo operácie — Direktor (sysadmin) vykonáva tieto kroky manuálne.

---

## Predpoklady

- ANDROS host (Ubuntu)
- Užívateľ `andros` so sudo privilégiami
- NGINX nainštalovaný + `*.isnex.eu` wildcard cert v `/etc/letsencrypt/live/isnex.eu/` (per ICC_STANDARDS)
- Docker engine + docker compose v2

---

## Krok 1: Vytvor `/opt/uat/` infraštruktúru

```bash
sudo mkdir -p /opt/uat
sudo chown andros:andros /opt/uat
sudo chmod 0755 /opt/uat
```

Per F-003 §3 každý UAT slug žije v `/opt/uat/<slug>/`. Skripty `scripts/uat-*.py` zlyhajú s clear error message ak adresár nedostupný.

---

## Krok 2: Per-tenant priestor (one-time per slug)

Pri prvom deploy pre konkrétny slug (napr. `mager`, `dev`):

```bash
mkdir -p /opt/uat/<slug>/{snapshots,customer-test-data,logs}
chmod 0700 /opt/uat/<slug>/customer-test-data  # Real PDF mimo gitu (per F-003 §6.2)
```

`uat-deploy.py` skript urobí `os.makedirs(..., exist_ok=True)` pre `snapshots/` a `logs/`, ale `customer-test-data/` ostáva manuálnym Direktor setup-om (vyžaduje 0700 permissions kvôli PII).

---

## Krok 3: Disk space audit

Per UAT zostava ~10-15 GB disk usage (BE image + FE image + Postgres volume + test data). Pre N projektov × M slugov disk priestor narastá.

Pred deploy zhodnotiť:

```bash
df -h /opt
docker system df  # Pre Docker-specific usage
```

Per F-003 §7 — pri 50% threshold-e Koordinátor flag-uje urgent.

---

## Krok 4: NGINX aktivácia (per uat-deploy)

`scripts/uat-deploy.py` zapíše config do `/etc/nginx/sites-available/uat-<slug>.conf` a printne pokyn:

```bash
sudo ln -sf /etc/nginx/sites-available/uat-<slug>.conf /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx
```

Bez tohto kroku URL `https://uat-<slug>.isnex.eu` nie je accessible.

Per F-003 §10 — UAT URL je accessible iba cez Tailscale/RDP/intranet (Cloudflare access list zabezpečí na úrovni siete).

---

## Krok 5: Verifikácia setup-u

Po krokoch 1-4 spusti dry-run:

```bash
python scripts/uat-status.py <slug>
# Expected: "NOT DEPLOYED" (nie chyba o chýbajúcom /opt/uat/)
```

Ak chyba "Permission denied: /opt/uat/", krok 1 nebol vykonaný. Ak chyba "command not found", projekt nemá nainštalované deps (`poetry install` v `/opt/projects/nex-studio/`).

---

## Krok 6: Teardown cleanup (per uat-teardown)

`scripts/uat-teardown.py` zachová `snapshots/`, `customer-test-data/`, `logs/` a odstráni `docker-compose.yml`, `.env`, Docker volumes. Plus printne NGINX cleanup pokyn:

```bash
sudo rm /etc/nginx/sites-enabled/uat-<slug>.conf
sudo systemctl reload nginx
```

Per F-003 §8 retention policy: DB snapshots **bez expirácie** (forever) — mazanie iba s explicit Direktorovým schválením cez Inbox Deda žiadosť.

---

## Súvisiace dokumenty

- `docs/specs/versions/v0.2.0/spec/F-003-uat-environment.md` (autoritatívny spec)
- `docs/specs/versions/v0.2.0/spec/sub-round-4-resolution.md` §3.4 (rozhodnutia)
- `templates/uat/` (docker-compose.yml.j2, .env.example, nginx-uat-vhost.conf)
- `scripts/_uat_lib.py` (shared helpers)
