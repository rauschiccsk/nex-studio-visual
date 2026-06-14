# syntax=docker/dockerfile:1.7

# Frontend image: standard multi-stage build. nex-shared is PUBLIC (post-E1 cleanup), so the
# in-container `npm ci` resolves the git-dep over HTTPS with ZERO auth — no host-build, no self-hosted runner.
# Build context = ./frontend.

FROM node:20-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
ARG VITE_API_BASE_URL=""
ARG VITE_APP_VERSION="0.0.0-dev"
ENV VITE_API_BASE_URL=$VITE_API_BASE_URL \
    VITE_APP_VERSION=$VITE_APP_VERSION
RUN npm run build

FROM nginx:1.27-alpine AS production

# Drop the default config shipped with the image — we ship our own.
RUN rm /etc/nginx/conf.d/default.conf

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html

# curl is used by the container healthcheck.
RUN apk add --no-cache curl

EXPOSE 9177

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD curl -fsS http://localhost:9177/health || exit 1

CMD ["nginx", "-g", "daemon off;"]
