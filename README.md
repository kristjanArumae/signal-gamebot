# signal-gamebot

A Signal group bot that tracks daily-game scores (MapTap, Framed, TimeGuessr, Color Daily; others via AI)
and posts leaderboards.

- Paste a result in the group → bot reacts ✅ (🤖 if the AI read it, 🔁 if you already submitted that
  puzzle; first one counts, ⏳ if the AI budget is used up). Anything that isn't a game result is ignored.
- Each game's leaderboard posts as soon as every **regular** (anyone who played that game in the
  last 7 days) has submitted, otherwise at **6pm ET**. With no history yet, it waits for 6pm.
- Exact commands only: `!lb [game] [oct 5 | yesterday | week | last week | month | last month | all]`,
  `!help`, `!games`, `!joke`, `!usage`. Anything else starting with `!` gets ❓; a valid `!lb` with nothing to show gets 🤷.

## Bot account

The bot's phone number and Signal registration-lock PIN are kept out of git in `ACCOUNT.local.md`.

## AI helpers (Claude Sonnet 5)

Everything has a deterministic path first; Claude only steps in for two things:

- **Unrecognized score shares.** If no parser matches but a message has an emoji grid (🟩🟨⬛🟢…) or a
  puzzle number (`#1234`), Sonnet extracts game, puzzle, score, and direction. Reacts 🤖 instead of ✅.
  A new game's name and higher/lower-is-better are saved the first time and never change after.
  Conversation, links, and casual "I got 4/6" mentions never reach the AI.
- **`!joke`**, avoiding the last 20 jokes; canned jokes if the AI is off or over budget.

Budget: `AI_MONTHLY_BUDGET_USD` (default $15), enforced from recorded token usage at list prices, with
a daily cap of budget/30 (~$0.50) so one busy day can't spend the month. ~$0.005 per score read,
~$0.001 per joke. `!usage` shows tokens, spend vs budget, and a playful water estimate (~15 mL per
1,000 tokens). Model is overridable with `AI_MODEL`. Key lives in Secrets Manager as
`signal-gamebot/anthropic`; `deploy.sh` fetches it on the instance.

## Architecture

One t4g.micro EC2 instance (~$10/mo) in us-east-1, CloudFormation stack `signal-gamebot`:

- `signal-api`: [bbernhard/signal-cli-rest-api](https://github.com/bbernhard/signal-cli-rest-api) in json-rpc mode
- `bot`: this Python app (`gamebot/`), receiving over WebSocket, SQLite at `/opt/gamebot/data/bot/gamebot.db`
- No inbound ports; everything is managed through SSM (`scripts/remote.sh`)
- Budget alert at $15/mo on EC2/VPC spend

## Setup: registering the bot's number

1. Get a captcha: open https://signalcaptchas.org/registration/generate.html, solve it,
   right-click **Open Signal** → copy link (`signalcaptcha://...`). It expires within a minute or so.
2. `scripts/admin.sh register +1NUMBER 'signalcaptcha://...'`
   (For Google Voice, if no SMS arrives: wait 60s, new captcha, `scripts/admin.sh register-voice ...`)
3. `scripts/admin.sh verify +1NUMBER 123456`
4. `scripts/admin.sh pin +1NUMBER <6+ digit pin>`: registration lock, protects the account if the number is ever recycled
5. `scripts/admin.sh name +1NUMBER 'Scorebot'`
6. Set `BOT_NUMBER=+1NUMBER` in `.env`, then `scripts/deploy.sh`
7. Add the bot's number to the group in Signal (save it as a contact first). It auto-accepts invites.

## Group allowlist

The bot ignores every group (no reactions, nothing stored) until it's allowed. Groups it was in when
the allowlist shipped were grandfathered in. To enable a new group, add the bot, then:

```bash
scripts/admin.sh groups-allowed          # list groups and their status
scripts/admin.sh allow 'Exact group name' # or the group id from the list
scripts/admin.sh deny 'Exact group name'
```

Abuse limits: DM replies at most once per person per day and 20/hour total; each container's logs
are capped at 3 × 10 MB; AI spend is capped in dollars (see above).

## Day to day

```bash
scripts/deploy.sh              # ship code changes
scripts/admin.sh logs          # recent container logs
scripts/remote.sh 'docker ps'  # anything else on the box
.venv/bin/pytest               # tests
```

Add a game: subclass `Game` in `gamebot/games.py`, add it to `GAMES`, add a test with a real pasted result.
If the AI was already handling that game, back up the DB and re-read old posts with the new parser:
`scripts/remote.sh 'cd /opt/gamebot/app && docker compose exec -T bot python -m gamebot.admin reparse'`
(dry run; add `--apply` to write).

## Teardown

`aws cloudformation delete-stack --stack-name signal-gamebot`, then delete the leftover EBS volume
(kept on purpose so Signal keys and score history survive instance replacement).
