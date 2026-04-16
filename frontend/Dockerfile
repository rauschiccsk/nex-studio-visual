# syntax=docker/dockerfile:1.7

# ---- Build stage ----
FROM node:20-alpine AS build

WORKDIR /app

# Install dependencies first to leverage Docker layer caching.
COPY package.json package-lock.json* ./
RUN npm ci --no-audit --no-fund

# Copy sources and build.
COPY . .

ARG VITE_API_BASE_URL=http://localhost:9176
ENV VITE_API_BASE_URL=${VITE_API_BASE_URL}

RUN npm run build

# ---- Production stage ----
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
