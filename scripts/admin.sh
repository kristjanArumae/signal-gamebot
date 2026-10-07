#!/usr/bin/env bash
# One-off Signal account admin, run against the signal-cli REST API on the instance.
#
#   scripts/admin.sh register +15551234567 'signalcaptcha://...'   # SMS code
#   scripts/admin.sh register-voice +15551234567 'signalcaptcha://...'  # call instead (try SMS first)
#   scripts/admin.sh verify +15551234567 123456
#   scripts/admin.sh pin +15551234567 123456789
#   scripts/admin.sh name +15551234567 'Scorebot'
#   scripts/admin.sh groups +15551234567
#   scripts/admin.sh logs
#   scripts/admin.sh groups-allowed                 # which groups the bot will respond in
#   scripts/admin.sh allow 'Group name or id'       # start responding in a group
#   scripts/admin.sh deny 'Group name or id'        # stop responding in a group
#
# Captcha: open https://signalcaptchas.org/registration/generate.html, solve it, then right-click
# "Open Signal" and copy the link (starts with signalcaptcha://).
set -euo pipefail
cd "$(dirname "$0")"
api=http://127.0.0.1:8080
cmd="${1:?command}"; number="${2:-}"

json() { python3 -c 'import json,sys; print(json.dumps(dict(zip(sys.argv[1::2], sys.argv[2::2]))))' "$@"; }

case "$cmd" in
  register)
    ./remote.sh "curl -sS -X POST $api/v1/register/$number -H 'Content-Type: application/json' -d '$(json captcha "$3")'" ;;
  register-voice)
    body=$(python3 -c 'import json,sys; print(json.dumps({"captcha": sys.argv[1], "use_voice": True}))' "$3")
    ./remote.sh "curl -sS -X POST $api/v1/register/$number -H 'Content-Type: application/json' -d '$body'" ;;
  verify)
    ./remote.sh "curl -sS -X POST $api/v1/register/$number/verify/$3" ;;
  pin)
    # Registration lock: stops anyone who later gets this phone number from taking over the bot.
    ./remote.sh "curl -sS -X POST $api/v1/accounts/$number/pin -H 'Content-Type: application/json' -d '$(json pin "$3")'" ;;
  name)
    ./remote.sh "curl -sS -X PUT $api/v1/profiles/$number -H 'Content-Type: application/json' -d '$(json name "$3")'" ;;
  groups)
    ./remote.sh "curl -sS $api/v1/groups/$number" ;;
  groups-allowed)
    ./remote.sh "cd /opt/gamebot/app && docker compose exec -T bot python -m gamebot.admin list" ;;
  allow|deny)
    ./remote.sh "cd /opt/gamebot/app && docker compose exec -T bot python -m gamebot.admin $cmd $(printf '%q' "$2")" ;;
  logs)
    ./remote.sh "cd /opt/gamebot/app && docker compose logs --tail=${2:-100}" ;;
  *)
    echo "unknown command: $cmd" >&2; exit 1 ;;
esac
