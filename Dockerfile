# syntax=docker/dockerfile:1
# Unofficial packaging of dsh-superagent-prototype for Coolify Docker Compose.
# Secrets are passed only at runtime using Coolify Environment Variables.
FROM node:24-bookworm

ENV HOME=/home/agent \
    DSH_HOME=/home/agent/.dsh \
    DEBIAN_FRONTEND=noninteractive

RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
      bash ca-certificates curl git git-lfs gnupg jq \
      openssh-client procps python3 make g++ tmux socat; \
    install -m 0755 -d /etc/apt/keyrings; \
    curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
      -o /etc/apt/keyrings/githubcli-archive-keyring.gpg; \
    chmod a+r /etc/apt/keyrings/githubcli-archive-keyring.gpg; \
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
      > /etc/apt/sources.list.d/github-cli.list; \
    apt-get update; \
    apt-get install -y --no-install-recommends gh; \
    rm -rf /var/lib/apt/lists/*

# Match the version in upstream docs/006-deployment.md.
# BuildKit cache mounts keep downloaded packages on the build host between
# deployments (even when layers are rebuilt) and out of the image/volume.
RUN --mount=type=cache,target=/var/cache/npm \
    npm install --global --prefer-offline --cache /var/cache/npm \
      @deepseek-ai/dsh@0.2.1-rc.3 pnpm@11.7.0

RUN useradd --create-home --shell /bin/bash --uid 10001 agent && \
    install -d -o agent -g agent /home/agent/src /home/agent/.config/gh-agent

# Declared here so changing the ref doesn't invalidate the apt/npm layers above.
ARG SUPERAGENT_REF=main
RUN git clone --depth 1 --branch "$SUPERAGENT_REF" \
      https://github.com/deepseek-ai/dsh-superagent-prototype.git /opt/dsh-superagent
WORKDIR /opt/dsh-superagent
RUN --mount=type=cache,target=/var/cache/pnpm-store \
    pnpm install --frozen-lockfile --prefer-offline --store-dir /var/cache/pnpm-store && \
    pnpm build && \
    chown -R agent:agent /opt/dsh-superagent /home/agent

COPY scripts/gh-agent scripts/superagent-entrypoint.sh scripts/prepare-worktree.sh \
     scripts/superagent-configure /usr/local/bin/
RUN chmod 0755 /usr/local/bin/gh-agent \
    /usr/local/bin/superagent-entrypoint.sh \
    /usr/local/bin/prepare-worktree.sh \
    /usr/local/bin/superagent-configure
# Claude Code PR review: the read-only policy (managed settings take precedence over
# user and repository settings), the provider overlay passed with --patch, and the
# Delivery requirements that superagent-configure saves.
COPY claude-code/managed-settings.json /etc/claude-code/managed-settings.json
COPY claude-code/claude-code.patch.yml /etc/superagent/claude-code.patch.yml
COPY prompts/delivery.md /usr/local/share/superagent/delivery.md

# Official on-demand DSH bundle providing the subagent_claude_code tool; its version
# must match the dsh CLI above. The entrypoint adds it to older profiles too.
ENV DSH_CLAUDE_CODE_BUNDLE=@deepseek-ai/dsh-subagent-claude-code@0.2.1-rc.3

USER agent

# Seed the DSH profile at image build time. On first use, Docker copies the
# image's /home/agent contents into an empty named volume at that mountpoint.
RUN set -eu; \
    dsh superagent --from-default-profile web --dump-config > /dev/null; \
    dsh plugin --profile superagent add 'link:/opt/dsh-superagent'; \
    dsh plugin --profile superagent add "$DSH_CLAUDE_CODE_BUNDLE"; \
    mkdir -p "$DSH_HOME/state/superagent"; \
    printf '%s\n' \
      '- id: webserver' \
      '  config:' \
      '    host: 127.0.0.1' \
      '    port: 3082' \
      '- id: dsh-superagent' \
      '  config:' \
      '    stateDir: /home/agent/.dsh/state/superagent' \
      > "$DSH_HOME/profiles/superagent/cordis.patch.yml"

# The main DSH webserver is loopback-only. The entrypoint uses socat
# to expose port 3083 to the INTERNAL dashboard sidecar; no host port is published.
EXPOSE 3083

HEALTHCHECK --interval=30s --timeout=6s --start-period=120s --retries=4 \
  CMD curl -fsS -H 'x-orchestrator-request: 1' \
    http://127.0.0.1:3082/orchestrator/api/state > /dev/null || exit 1

ENTRYPOINT ["/usr/local/bin/superagent-entrypoint.sh"]
CMD ["dsh", "--profile", "superagent"]
