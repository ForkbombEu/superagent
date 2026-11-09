# syntax=docker/dockerfile:1
# The disposable sandbox that builds and runs a pull request's application for
# UAT (build context: uat/). It never holds a credential; the toolchain comes
# from the repository's mise.toml, as in the superagent image.
FROM debian:bookworm-slim

RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
      bash ca-certificates curl git gnupg jq make g++ procps python3 unzip xz-utils; \
    rm -rf /var/lib/apt/lists/*

ARG MISE_VERSION=v2026.11.2
RUN set -eux; \
    case "$(dpkg --print-architecture)" in amd64) arch=x64 ;; arm64) arch=arm64 ;; *) exit 1 ;; esac; \
    asset="mise-$MISE_VERSION-linux-$arch"; \
    cd /tmp; \
    curl -fsSLO "https://github.com/jdx/mise/releases/download/$MISE_VERSION/$asset"; \
    curl -fsSL "https://github.com/jdx/mise/releases/download/$MISE_VERSION/SHASUMS256.txt" \
      | grep " \./$asset\$\| $asset\$" | sed "s| \./| |" | sha256sum -c -; \
    install -m 0755 "$asset" /usr/local/bin/mise; \
    rm "$asset"; \
    install -d -m 0777 /home/sandbox /workspace

# Compares the working copy's tracked files with the committed snapshot (sandbox.py).
COPY sandbox/tree.py /usr/local/lib/superagent-uat/tree.py

# The controller runs the containers as its own uid; the home (a per-run volume
# shared by setup and start) and the checkout are writable by any uid.
ENV HOME=/home/sandbox \
    PATH=/home/sandbox/.local/share/mise/shims:$PATH \
    MISE_TRUSTED_CONFIG_PATHS=/workspace \
    MISE_YES=1
WORKDIR /workspace
