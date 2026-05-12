# syntax=docker/dockerfile:1.7

# Stage 1: build the TypeScript bundle and resolve production deps.
FROM node:22-slim AS builder

ENV PNPM_HOME=/usr/local/share/pnpm \
    PATH="/usr/local/share/pnpm:${PATH}" \
    CI=true

RUN corepack enable && corepack prepare pnpm@11.0.8 --activate

WORKDIR /app

COPY services/auth/package.json services/auth/pnpm-lock.yaml services/auth/pnpm-workspace.yaml ./
RUN --mount=type=cache,target=/root/.local/share/pnpm/store \
    pnpm install --frozen-lockfile

COPY services/auth/tsconfig.json services/auth/tsconfig.build.json ./
COPY services/auth/src ./src
COPY services/auth/drizzle ./drizzle

RUN pnpm build

# Prune dev deps for the runtime image.
RUN pnpm prune --prod

# Stage 2: minimal runtime image. Non-root user runs the service.
FROM node:22-slim AS runtime

ENV NODE_ENV=production \
    NODE_OPTIONS=--enable-source-maps

RUN groupadd --system app && useradd --system --gid app --no-create-home app

WORKDIR /app

COPY --from=builder --chown=app:app /app/node_modules ./node_modules
COPY --from=builder --chown=app:app /app/dist ./dist
COPY --from=builder --chown=app:app /app/drizzle ./drizzle
COPY --from=builder --chown=app:app /app/package.json ./package.json

USER app

EXPOSE 8080

CMD ["node", "dist/index.js"]
