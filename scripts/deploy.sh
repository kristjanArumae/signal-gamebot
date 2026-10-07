#!/usr/bin/env bash
# Create/update the AWS stack, then ship the code to the instance and (re)start the containers.
#   ALERT_EMAIL=you@example.com scripts/deploy.sh   (ALERT_EMAIL only needed the first time)
set -euo pipefail
cd "$(dirname "$0")/.."
STACK="${STACK:-signal-gamebot}"
REGION="${AWS_REGION:-us-east-1}"
touch .env  # BOT_NUMBER=+1..., TIMEZONE, POST_HOUR — see .env.example

aws cloudformation deploy --region "$REGION" --stack-name "$STACK" \
  --template-file infra/template.yaml --capabilities CAPABILITY_IAM \
  ${ALERT_EMAIL:+--parameter-overrides AlertEmail=$ALERT_EMAIL} --no-fail-on-empty-changeset

bucket=$(aws cloudformation describe-stacks --region "$REGION" --stack-name "$STACK" \
  --query "Stacks[0].Outputs[?OutputKey=='BucketName'].OutputValue" --output text)
instance=$(aws cloudformation describe-stacks --region "$REGION" --stack-name "$STACK" \
  --query "Stacks[0].Outputs[?OutputKey=='InstanceId'].OutputValue" --output text)

echo "Waiting for $instance to register with SSM..."
until [ "$(aws ssm describe-instance-information --region "$REGION" \
  --filters "Key=InstanceIds,Values=$instance" --query 'length(InstanceInformationList)')" = 1 ]; do
  sleep 5
done

key="builds/$(date +%Y%m%d-%H%M%S).tgz"
tar czf /tmp/gamebot-build.tgz Dockerfile docker-compose.yml requirements.txt .env gamebot
aws s3 cp --region "$REGION" /tmp/gamebot-build.tgz "s3://$bucket/$key"
rm /tmp/gamebot-build.tgz

scripts/remote.sh "$(cat <<EOF
set -euxo pipefail
# First boot may still be installing Docker.
until [ -x /usr/local/lib/docker/cli-plugins/docker-compose ] && [ -e /swapfile ]; do sleep 5; done
# signal-cli-rest-api runs as uid 1000 and must own its data dir.
chown -R 1000:1000 /opt/gamebot/data/signal
cd /opt/gamebot/app
rm -rf ./* && aws s3 cp --region $REGION s3://$bucket/$key - | tar xz
# Anthropic key for the Haiku fallback, fetched on the instance (never echoed; absent = fallback off).
set +x
anthropic_key=\$(aws secretsmanager get-secret-value --region $REGION --secret-id signal-gamebot/anthropic \\
  --query SecretString --output text 2>/dev/null || true)
printf 'ANTHROPIC_API_KEY=%s\\n' "\$anthropic_key" > secrets.env && chmod 600 secrets.env
echo "anthropic key: \$([ -n "\$anthropic_key" ] && echo present || echo missing)"
set -x
docker build -q -t gamebot:latest .
docker compose up -d --remove-orphans
docker image prune -f
docker compose ps
EOF
)"
