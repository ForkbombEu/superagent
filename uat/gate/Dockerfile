# syntax=docker/dockerfile:1
# The UAT gate (build context: the repository root; compose.yaml service
# uat-gate). It holds a GitHub read token and the tester's DeepSeek key, keeps
# the UAT verdicts and drives the runner (uat-runner) over TLS. It never runs a
# pull request's code itself: that happens only in the runner's containers.
FROM docker:29-cli

RUN set -eux; \
    apk add --no-cache git py3-yaml python3 su-exec; \
    adduser -D -u 10020 gate; \
    install -d -o gate -g gate /var/lib/uat-gate /var/lib/uat-work

COPY uat /opt/superagent/uat
COPY prompts/uat-tester.md /opt/superagent/prompts/uat-tester.md
COPY uat/gate/entrypoint.sh /usr/local/bin/uat-gate-entrypoint
RUN chmod 0755 /usr/local/bin/uat-gate-entrypoint

WORKDIR /opt/superagent
EXPOSE 8090
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
  CMD wget -qO- http://127.0.0.1:8090/healthz > /dev/null || exit 1
ENTRYPOINT ["/usr/local/bin/uat-gate-entrypoint"]
CMD ["python3", "-m", "uat.gate"]
