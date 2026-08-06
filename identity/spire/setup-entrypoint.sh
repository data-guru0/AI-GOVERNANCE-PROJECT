#!/bin/sh
# Runs once (in the `spire-setup` helper container) to bootstrap SPIRE:
#   1. wait for spire-server to be ready
#   2. mint a join token for the single demo node (see server.conf comment
#      on why join_token attestation, not a cloud attestor, is used here)
#   3. render agent.conf with that token onto a volume spire-agent reads
#   4. register the workload entry that maps the `agent` app container
#      (matched by its real UID on the shared Workload API socket — see
#      agent.conf.template for why UID, not a Docker label, is the
#      selector) to a real SPIFFE ID, so it can fetch an X.509-SVID
#      instead of ever using a static hardcoded credential as its own
#      identity.
set -e
apk add --no-cache jq >/dev/null 2>&1

echo "[spire-setup] waiting for spire-server..."
until docker exec spire-server /opt/spire/bin/spire-server healthcheck >/dev/null 2>&1; do
  sleep 2
done
echo "[spire-setup] spire-server is up"

TOKEN=$(docker exec spire-server /opt/spire/bin/spire-server token generate \
  -spiffeID spiffe://governance.demo/agent-node -output json | jq -r '.value')

if [ -z "$TOKEN" ] || [ "$TOKEN" = "null" ]; then
  echo "[spire-setup] FAILED to generate join token" >&2
  exit 1
fi
echo "[spire-setup] join token generated"

sed "s/__JOIN_TOKEN__/$TOKEN/" /template/agent.conf.template > /shared/agent.conf
echo "[spire-setup] wrote /shared/agent.conf"


EXISTING_ID=$(docker exec spire-server /opt/spire/bin/spire-server entry show \
  -spiffeID spiffe://governance.demo/agent/demo-agent -output json 2>/dev/null | jq -r '.entries[0].id // empty')
if [ -n "$EXISTING_ID" ]; then
  docker exec spire-server /opt/spire/bin/spire-server entry delete -entryID "$EXISTING_ID" >/dev/null 2>&1 || true
fi

docker exec spire-server /opt/spire/bin/spire-server entry create \
  -spiffeID spiffe://governance.demo/agent/demo-agent \
  -parentID spiffe://governance.demo/agent-node \
  -selector unix:uid:0

echo "[spire-setup] done — agent SPIFFE ID: spiffe://governance.demo/agent/demo-agent"
