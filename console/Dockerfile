# syntax=docker/dockerfile:1
# Build context is the Superagent repo root (compose: context: ., dockerfile: console/Dockerfile).
# bookworm-slim (glibc) so the codebase-memory-mcp native runtime can run; alpine/musl is not supported.
FROM node:24-bookworm-slim AS build
WORKDIR /app
COPY console/package.json console/package-lock.json ./
RUN npm ci
COPY console/ ./
RUN npm run build

FROM node:24-bookworm-slim AS runtime
WORKDIR /app
ENV NODE_ENV=production
ENV SUPERAGENT_CONSOLE_REPO_ROOT=/opt/console-repo
RUN useradd --system --uid 10001 --create-home --home-dir /home/console console \
  && apt-get update \
  && apt-get install -y --no-install-recommends ca-certificates \
  && rm -rf /var/lib/apt/lists/*
COPY console/package.json console/package-lock.json ./
RUN npm ci --omit=dev && npm cache clean --force
COPY --from=build /app/dist ./dist
COPY --from=build /app/web/dist ./web/dist
COPY --from=build /app/prompts ./prompts
# Same Superagent commit as this image (no runtime clone, no GH_TOKEN).
COPY . /opt/console-repo
RUN rm -rf /opt/console-repo/console/node_modules /opt/console-repo/console/dist \
  && chown -R console:console /app /opt/console-repo /home/console
USER console

EXPOSE 3000
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
  CMD node -e "fetch('http://127.0.0.1:3000/console/api/health').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"
CMD ["node", "dist/server.js"]
