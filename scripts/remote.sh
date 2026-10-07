#!/usr/bin/env bash
# Run a shell script on the bot instance via SSM and print its output.
#   scripts/remote.sh 'docker ps'
#   echo 'docker ps' | scripts/remote.sh
set -euo pipefail
STACK="${STACK:-signal-gamebot}"
REGION="${AWS_REGION:-us-east-1}"

script="${1:-$(cat)}"
instance=$(aws cloudformation describe-stacks --region "$REGION" --stack-name "$STACK" \
  --query "Stacks[0].Outputs[?OutputKey=='InstanceId'].OutputValue" --output text)

# Base64 the script so quotes/JSON survive the trip through SSM untouched.
encoded=$(printf '%s' "$script" | base64 | tr -d '\n')
command_id=$(aws ssm send-command --region "$REGION" --instance-ids "$instance" \
  --document-name AWS-RunShellScript --timeout-seconds 900 \
  --parameters "commands=[\"echo $encoded | base64 -d | bash\"]" \
  --query Command.CommandId --output text)

while true; do
  status=$(aws ssm get-command-invocation --region "$REGION" --command-id "$command_id" \
    --instance-id "$instance" --query Status --output text 2>/dev/null || echo Pending)
  case "$status" in Pending|InProgress|Delayed) sleep 3 ;; *) break ;; esac
done

aws ssm get-command-invocation --region "$REGION" --command-id "$command_id" --instance-id "$instance" \
  --query '[StandardOutputContent, StandardErrorContent]' --output text
[ "$status" = Success ]
