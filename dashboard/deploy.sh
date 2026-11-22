#!/bin/bash
# Deploy the CI dashboard (rkapp + Caddy TLS) to the ci-dashboard host over SSM.
#
#   ./deploy.sh [commit]    commit of aztec-labs-eng/ci3 to deploy (default: main)
#
# The host clones public ci3 at that exact commit and builds from it, so what runs is
# what is in the repo. It writes /etc/rkapp.env (mode 600) from the ci-dashboard/env
# secret, brings the compose stack up, and fails unless the app answers on loopback
# and through Caddy over https.
set -euo pipefail

REPO=https://github.com/aztec-labs-eng/ci3
export AWS_DEFAULT_REGION=us-east-2

if [ -n "${1:-}" ]; then
  sha=$(git ls-remote "$REPO" "$1" | cut -f1 | head -1)
  sha=${sha:-$1}
else
  sha=$(git ls-remote "$REPO" refs/heads/main | cut -f1)
fi
[[ "$sha" =~ ^[0-9a-f]{40}$ ]] || { echo "ERROR: could not resolve '${1:-main}' to a full commit." >&2; exit 1; }

iid=$(aws ec2 describe-instances \
  --filters Name=tag:Name,Values=ci-dashboard Name=instance-state-name,Values=running \
  --query 'Reservations[].Instances[].InstanceId' --output text)
[[ "$iid" =~ ^i-[0-9a-f]+$ ]] || { echo "ERROR: expected one running ci-dashboard instance, got '$iid'." >&2; exit 1; }

echo "Deploying ci3 $sha to $iid..."
"$(dirname "${BASH_SOURCE[0]}")/../internal/ssm_send_command" "$iid" - <<EOF
set -euo pipefail
export PATH=/snap/bin:\$PATH
dir=/opt/ci3
[ -d \$dir/.git ] || git clone --quiet $REPO \$dir
git -C \$dir fetch --quiet origin $sha
git -C \$dir checkout --quiet --force --detach $sha

(umask 077
 aws secretsmanager get-secret-value --region us-east-2 --secret-id ci-dashboard/env \
   --query SecretString --output text > /etc/rkapp.env.new)
grep -q '^DASHBOARD_PASSWORD=.' /etc/rkapp.env.new || { echo "ERROR: ci-dashboard/env has no DASHBOARD_PASSWORD." >&2; exit 1; }
mv /etc/rkapp.env.new /etc/rkapp.env

cd \$dir/dashboard
docker compose up -d --build --remove-orphans

# 401 is healthy: every route requires the password.
up() { [ "\$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "\$@" || true)" = 401 ]; }
# Caddy obtains its certificate on first boot, so https can lag the app.
for _ in \$(seq 60); do
  if up http://127.0.0.1:8080/ && up --resolve ci.aztec-labs.com:443:127.0.0.1 https://ci.aztec-labs.com/; then
    echo "Dashboard up at https://ci.aztec-labs.com (ci3 $sha)"
    exit 0
  fi
  sleep 2
done
echo "ERROR: dashboard not healthy on loopback and via Caddy https." >&2
docker compose logs --tail=30 >&2 || true
exit 1
EOF
