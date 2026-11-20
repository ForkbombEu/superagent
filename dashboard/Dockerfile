FROM caddy:2.10-alpine
COPY Caddyfile /etc/caddy/Caddyfile
EXPOSE 8080
# Coolify and Compose use this for the public dashboard service. Alpine's
# busybox wget is enough; /healthz is answered by Caddy itself (see Caddyfile).
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD wget -qO- http://127.0.0.1:8080/healthz > /dev/null || exit 1
