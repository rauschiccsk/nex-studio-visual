# syntax=docker/dockerfile:1.7

# Frontend image = nginx serving a PRE-BUILT `dist/` (E1 B1 fix, CR-NS-048).
# The SPA is built on the HOST (runner/server, where the private `nex-shared`
# git dependency resolves as the `andros` user) BEFORE `docker build`. The
# container intentionally does NOT run npm/git — a containerised `npm ci`
# cannot authenticate to the private GitHub repo (no git/SSH creds inside).
# The build context therefore MUST contain a fresh `dist/` (see ci.yml Build
# Docker job + the Deploy step, which build it on the host first).

FROM nginx:1.27-alpine AS production

# Drop the default config shipped with the image — we ship our own.
RUN rm /etc/nginx/conf.d/default.conf

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY dist /usr/share/nginx/html

# curl is used by the container healthcheck.
RUN apk add --no-cache curl

EXPOSE 9177

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD curl -fsS http://localhost:9177/health || exit 1

CMD ["nginx", "-g", "daemon off;"]
