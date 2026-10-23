#!/usr/bin/env bash
#
# Deploy NEX Studio Visual to the live cockpit.
#
# Replaces scripts/deploy-v2-prod.sh, which was inherited from nex-studio when this project was forked
# and NEVER applied here: it deployed from /opt/prod-v2/nex-studio/docker-compose.yml (a path that does
# not exist on this machine), built images called nex-studio-backend / nex-studio-frontend (this stack
# runs nex-studio-visual-*), and derived a 2.0.N version (this line is v4.0.N). Anyone who trusted it
# would either watch it fail on the missing file or — if that directory were ever restored from a backup
# — bring up a SECOND stack beside the live one, on the same database. That is the shape of the incident
# that once took public routing down. (ICCINT-15, found 22.08.2026 while deploying ICCINT-12/13.)
#
# What it does, which is exactly the ritual that was being done by hand:
#   1. read the version the live compose is pinned to and derive the next one
#   2. build the images asked for, stamping the version into the frontend bundle
#   3. rewrite ONLY the image tags in the live compose (backed up first)
#   4. recreate the services and wait for healthy
#
# Migrations are NOT run here: the backend applies them itself on startup (backend/main.py).
#
# Usage:
#   scripts/deploy-prod.sh [--dry-run] [--version X.Y.Z] [backend|frontend|all]
#
# Default target is `all`. Use `frontend` for screen-only changes — it leaves the backend running, so a
# build waiting on the Manažér is not disturbed.

set -euo pipefail

COMPOSE="/opt/customers/dev/nex-studio-visual/docker-compose.yml"
PROJECT="nex-studio-visual-prod"
#: Where CI runs for this repository — the green run of the very commit being deployed is the gate (DEV-99).
CI_REPO="rauschiccsk/nex-studio-visual"
BACKEND_IMAGE="nex-studio-visual-backend"
FRONTEND_IMAGE="nex-studio-visual-frontend"

DRY_RUN=0
VERSION=""
TARGET="all"
#: DEV-193 — why the backend may be restarted although work is in flight (``--aj-ked-bezi "<dôvod>"``).
FORCE_REASON=""
#: Where the cockpit says how much work is in flight — counts only (DEV-193).
BUSY_URL="${NSV_BUSY_URL:-http://127.0.0.1:9216/health/busy}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY_RUN=1; shift ;;
    --version) VERSION="${2:-}"; shift 2 ;;
    --aj-ked-bezi) FORCE_REASON="${2:-}"; shift 2 ;;
    backend|frontend|all) TARGET="$1"; shift ;;
    *) echo "neznámy argument: $1" >&2; exit 2 ;;
  esac
done

die() { echo "ODMIETNUTÉ: $*" >&2; exit 1; }

# ── Only what git holds and CI passed (DEV-99) ──────────────────────────────────────────────────────
# The images are built from the working tree. 13.10.2026 it carried an uncommitted fix while 4.44.0 was being
# deployed: it would have gone live without CI, kept out only by a clean checkout made by hand. Both checks run
# before Docker is touched, in --dry-run too, and there is no way around them — a fix goes through a commit,
# a push and a green CI like everything else.
REPO_ROOT="$(git rev-parse --show-toplevel)" || die "skript nebeží v gitovom repozitári kokpitu — nevidím, čo by som staval."
DIRTY="$(git -C "$REPO_ROOT" status --porcelain --untracked-files=all)"
if [[ -n "$DIRTY" ]]; then
  DIRTY_FILES="$(cut -c4- <<<"$DIRTY")"
  die "pracovný priečinok má nezapísané zmeny ($(wc -l <<<"$DIRTY_FILES"): $(head -5 <<<"$DIRTY_FILES" | paste -sd, - | sed 's/,/, /g')) — obraz by sa postavil z kódu, ktorý CI nevidelo. Zapíš ich do gitu, odošli a počkaj na zelené CI."
fi
HEAD_SHA="$(git -C "$REPO_ROOT" rev-parse HEAD)"
CI_STATE="$(gh run list -R "$CI_REPO" --workflow ci.yml --commit "$HEAD_SHA" --limit 1 \
  --json status,conclusion --jq '.[0] | "\(.status) \(.conclusion)"' 2>/dev/null)" \
  || die "stav CI pre ${HEAD_SHA:0:7} sa nedal zistiť (gh) — bez zeleného CI nenasadzujem."
[[ "$CI_STATE" == "completed success" ]] \
  || die "CI pre ${HEAD_SHA:0:7} nie je zelené (${CI_STATE:-žiadny beh}) — nasadzujem až po zelenom CI na GitHube."

# ── Nothing in flight (DEV-193) ─────────────────────────────────────────────────────────────────────
# Restarting the backend cuts off whatever it is doing: an agent's turn (Verifikácia runs inside the backend's own
# process, with no nex-build- container — 23.10.2026 a watcher that looked at containers took that for idle), Poradca
# answering, a deploy to a customer. The cockpit says how much is in flight; the screens alone do not restart it.
busy_check() {
  [[ "$TARGET" == "frontend" ]] && return 0
  local body code said
  body="$(mktemp)"
  code="$(curl -s --max-time 10 -o "$body" -w '%{http_code}' "$BUSY_URL" 2>/dev/null || true)"
  case "$code" in
    200) ;;
    000) rm -f "$body"; echo "⚠️  kokpit neodpovedá (${BUSY_URL}) — nebeží, takže v ňom nič nebeží." >&2; return 0 ;;
    404) rm -f "$body"; echo "⚠️  kokpit ešte nevie povedať, či niečo beží (stará verzia) — over ručne." >&2; return 0 ;;
    *) rm -f "$body"; [[ -n "$FORCE_REASON" ]] && return 0
       die "kokpit nevie povedať, či niečo beží (HTTP ${code:-?}) — bez toho backend nereštartujem. Ak to musí byť teraz: --aj-ked-bezi \"dôvod\"." ;;
  esac
  said="$(python3 - "$body" <<'PY'
import json, sys
n = json.load(open(sys.argv[1], encoding="utf-8"))
def count(k):
    return int(n.get(k) or 0)
parts = []
if count("agents_working"):
    parts.append(f"agent pracuje na {count('agents_working')} {'stavbe' if count('agents_working') == 1 else 'stavbách'}")
if count("poradca_answering"):
    q = count("poradca_answering")
    parts.append(f"Poradca odpovedá na {q} {'otázku' if q == 1 else 'otázky' if q < 5 else 'otázok'}")
if count("deploys_running"):
    d = count("deploys_running")
    parts.append(f"{d} {'nasadenie' if d == 1 else 'nasadenia' if d < 5 else 'nasadení'}")
print(", ".join(parts))
PY
)" || { rm -f "$body"; die "kokpit odpovedal na otázku, či niečo beží, nečitateľne."; }
  rm -f "$body"
  [[ -z "$said" ]] && return 0
  if [[ -n "$FORCE_REASON" ]]; then
    echo "⚠️  nasadzujem napriek tomu, že beží: ${said} — dôvod: ${FORCE_REASON}" >&2
    return 0
  fi
  die "v kokpite práve beží: ${said}. Reštart backendu by to prerušil — počkaj, kým dobehne, alebo nasaď len frontend. Ak to musí byť teraz: --aj-ked-bezi \"dôvod\"."
}
busy_check

# ── Refusals ────────────────────────────────────────────────────────────────────────────────────────
# Each of these is a way the old script could have deployed somewhere other than the live stack. A
# deploy script that guesses is worse than no deploy script, because it looks like it worked.

[[ -f "$COMPOSE" ]] || die "compose súbor $COMPOSE neexistuje. Presne toto robil starý skript — ukazoval na cestu, ktorá tu nie je."

RUNNING="$(docker ps --filter "label=com.docker.compose.project=${PROJECT}" --format '{{.Names}}' | sort | tr '\n' ' ')"
[[ -n "$RUNNING" ]] || die "z projektu ${PROJECT} nebeží ani jeden kontajner. Nasadzoval by som vedľa toho, čo je naozaj v prevádzke."

CONFIGURED="$(docker inspect "${PROJECT}-backend-1" --format '{{index .Config.Labels "com.docker.compose.project.config_files"}}' 2>/dev/null || true)"
[[ "$CONFIGURED" == "$COMPOSE" ]] || die "bežiace kontajnery pochádzajú z '${CONFIGURED}', nie z '${COMPOSE}'. Nasadenie by vyrobilo druhý stack."

# The HIGHEST of the two, not the backend's. A screen-only deploy moves the frontend alone, so the two
# services sit on different versions until the next full one — reading either in isolation would propose
# a version that already exists and silently redeploy an old image under a new name.
# ``\K`` rather than a lookbehind: the two image names differ in length and PCRE lookbehind must be
# fixed-width, so the alternation would abort the whole script.
CURRENT="$(grep -oP "image: (?:${BACKEND_IMAGE}|${FRONTEND_IMAGE}):v\K[0-9]+\.[0-9]+\.[0-9]+" "$COMPOSE" \
  | sort -t. -k1,1n -k2,2n -k3,3n | tail -1)"
[[ -n "$CURRENT" ]] || die "v compose sa nedá prečítať súčasná verzia obrazov ${BACKEND_IMAGE} / ${FRONTEND_IMAGE}."

if [[ -z "$VERSION" ]]; then
  VERSION="$(awk -F. '{printf "%s.%s.%d", $1, $2, $3 + 1}' <<<"$CURRENT")"
fi

echo "==> stack:      ${PROJECT}  (${COMPOSE})"
echo "==> beží:       ${RUNNING}"
echo "==> verzia:     v${CURRENT}  →  v${VERSION}"
echo "==> nasadzujem: ${TARGET}"

# The version being deployed MUST ship a release note (ICCINT-28). For a generated app NEX Studio writes
# this file itself from the version's epics; it does not build ITSELF that way, so its own note is written
# by hand — and until now nothing here wrote it or asked for it, so a deploy without one passed in silence.
# That is exactly what the v4.0.96 note complained about, and it recurred within a DAY of being written:
# the Aktualizácie tab sat at v4.0.96 while the app ran v4.1.3, seven releases with nothing said about them.
# Checked BEFORE the --dry-run exit on purpose — a dry-run that passes where the real deploy refuses is a
# trap, and the whole point is to find out now rather than after the images are built.
NOTE="${REPO_ROOT}/docs/specs/versions/v${VERSION}/RELEASE_NOTES.md"
[[ -s "$NOTE" ]] || die "v${VERSION} nemá poznámku k vydaniu. Napíš ju do ${NOTE} — jednou-dvoma vetami po slovensky, čo z toho má ten, kto aplikáciu používa (nie čo sa zmenilo v kóde). Bez nej sa v Aktualizáciách toto vydanie nikdy neobjaví."

if [[ "$DRY_RUN" == "1" ]]; then
  echo "==> --dry-run: nič sa nestavia ani nemení."
  exit 0
fi

cd "$REPO_ROOT"

# ── Build ───────────────────────────────────────────────────────────────────────────────────────────
if [[ "$TARGET" == "all" || "$TARGET" == "backend" ]]; then
  docker build -f Dockerfile -t "${BACKEND_IMAGE}:v${VERSION}" .
fi
if [[ "$TARGET" == "all" || "$TARGET" == "frontend" ]]; then
  # The version is baked into the bundle so the sidebar shows what is actually deployed. Never hardcode
  # it in a manual `docker build` — that is how the screen starts lying about which version you are on.
  docker build -f frontend/Dockerfile -t "${FRONTEND_IMAGE}:v${VERSION}" \
    --build-arg VITE_API_BASE_URL="" --build-arg VITE_APP_VERSION="${VERSION}" ./frontend
fi

# Asked again: the build took minutes and work may have started meanwhile (DEV-193). Before the compose is rewritten,
# so a refusal leaves it naming the images that really run.
busy_check

# ── Rewrite the live compose ────────────────────────────────────────────────────────────────────────
# ONLY the image tags. Routing, ports, volumes and env are hand-maintained on this file and nothing here
# may touch them.
cp -a "$COMPOSE" "${COMPOSE}.bak-${CURRENT}"

SERVICES=()
if [[ "$TARGET" == "all" || "$TARGET" == "backend" ]]; then
  sed -i "s|image: ${BACKEND_IMAGE}:v[0-9.]*|image: ${BACKEND_IMAGE}:v${VERSION}|" "$COMPOSE"
  # ``APP_VERSION`` is what /health reports. Its default in the compose had been left at 4.0.78 while the
  # image moved on sixteen deploys, so the backend spent all of them claiming to be a version it was not.
  # A version that is only right when somebody remembers to update it will be wrong; bump it with the tag.
  sed -i "s|APP_VERSION: \"\${APP_VERSION:-[0-9.]*}\"|APP_VERSION: \"\${APP_VERSION:-${VERSION}}\"|" "$COMPOSE"
  SERVICES+=(backend)
fi
if [[ "$TARGET" == "all" || "$TARGET" == "frontend" ]]; then
  sed -i "s|image: ${FRONTEND_IMAGE}:v[0-9.]*|image: ${FRONTEND_IMAGE}:v${VERSION}|" "$COMPOSE"
  SERVICES+=(frontend)
fi

echo "==> zmeny v compose:"
diff "${COMPOSE}.bak-${CURRENT}" "$COMPOSE" || true

# ── Up ──────────────────────────────────────────────────────────────────────────────────────────────
docker compose -f "$COMPOSE" up -d --force-recreate --wait "${SERVICES[@]}"

echo "==> nasadené v${VERSION}"
docker ps --filter "label=com.docker.compose.project=${PROJECT}" --format '    {{.Names}}  {{.Image}}  {{.Status}}'

# ── Rozišli sa polovice? ────────────────────────────────────────────────────────────────────────────
# ICCINT-128. Nasadenie jednej polovice je legitímne a pri zmenách bez obrazoviek aj odporúčané —
# nechá bežiacu stavbu na pokoji. Lenže druhá polovica potom nesie staré číslo a NIKDE to nebolo
# vidieť: 14.09.2026 panel hlásil v4.38.0, kým backend bežal v4.38.4. Nech to vie aj ten, kto
# nasadzuje, nie len ten, kto sa neskôr pozerá na obrazovku.
BE_TAG="$(grep -oP "image: ${BACKEND_IMAGE}:v\K[0-9.]+" "$COMPOSE" | head -1)"
FE_TAG="$(grep -oP "image: ${FRONTEND_IMAGE}:v\K[0-9.]+" "$COMPOSE" | head -1)"
if [[ -n "$BE_TAG" && -n "$FE_TAG" && "$BE_TAG" != "$FE_TAG" ]]; then
  echo
  echo "⚠️  POLOVICE SA LÍŠIA:  backend v${BE_TAG}  ·  frontend v${FE_TAG}"
  echo "    Panel v kokpite to odteraz ukáže obom číslami, takže nikto nenahlási nesprávne vydanie."
  echo "    Ak to tak nemá zostať:  scripts/deploy-prod.sh --version ${BE_TAG} frontend"
fi
