# syntax=docker/dockerfile:1
# The disposable UAT tester (build context: uat/). DeepSeek Harness headless
# with the superagent-uat profile; at run time it gets the DeepSeek API key and
# nothing else: no GitHub credential, no Docker socket. The same image runs the
# allowlisting egress proxy (proxy.py).
FROM node:24-bookworm

ARG DSH_VERSION=0.2.1-rc.3
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends chromium fonts-dejavu-core jq; \
    rm -rf /var/lib/apt/lists/*; \
    npm install --global "@deepseek-ai/dsh@$DSH_VERSION"; \
    npm cache clean --force; \
    useradd --create-home --uid 10010 tester; \
    install -d -o tester -g tester /work

COPY proxy.py /usr/local/lib/superagent-uat/proxy.py
COPY tester/profile.patch.yml /usr/local/share/superagent-uat/profile.patch.yml

USER tester
# The controller runs the container as its own uid, so the files the tester
# creates in /work are the controller's: the home must be usable by any uid.
ENV HOME=/home/tester \
    DSH_HOME=/home/tester/.dsh \
    DSH_TELEMETRY_MODE=DISABLED
# Seed the profile from the pinned version's headless template and check that
# the composed tree carries the patch, so a DSH that ignored it fails the build.
RUN set -eu; \
    dsh superagent-uat --from-default-profile headless --dump-config > /dev/null; \
    cp /usr/local/share/superagent-uat/profile.patch.yml "$DSH_HOME/profiles/superagent-uat/cordis.patch.yml"; \
    dsh --profile superagent-uat --dump-config > /tmp/composed.yml; \
    grep -q 'mode: danger-full-access' /tmp/composed.yml; \
    grep -q 'policy: never' /tmp/composed.yml; \
    rm /tmp/composed.yml; \
    chmod -R a+rwX /home/tester
WORKDIR /work
