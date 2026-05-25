# AstroVPN Telegram Bot

AstroVPN is a Telegram VPN subscription bot for **VLESS + Reality + TCP** subscriptions managed through **3X-UI**. The bot runs on Railway with Telegram polling and a small web server on port `8080` for the Tribute webhook.

## What is implemented

The bot registers users on `/start`, shows a profile with subscription status, expiry date and free slots, accepts **Telegram Stars** payments and **Tribute** donations, creates or extends a unique 3X-UI client for every paid user, sends a VLESS link and receipt after payment, and checks expired subscriptions every 10 minutes to disable clients automatically.

| Area | Implementation |
| --- | --- |
| Telegram runtime | Polling, not Telegram webhook |
| Web server | `aiohttp` on `PORT`, default `8080` |
| Tribute endpoint | `POST /tribute/webhook` |
| Database | SQLite file configured by `DB_FILE` |
| VPN panel | `py3xui` with `AsyncApi` |
| Payment methods | Telegram Stars and Tribute |
| Protocol | VLESS + Reality + TCP, port `443`, flow `xtls-rprx-vision` |

## Railway setup

Set all variables from `.env.example` in Railway. The required variables are `BOT_TOKEN`, `ADMIN_ID`, `XUI_USERNAME`, `XUI_PASSWORD`, `VLESS_PUBLIC_KEY`, and `VLESS_SHORT_ID`. The deployment command is already configured in `railway.toml` as:

```toml
[deploy]
startCommand = "python main.py"
```

The `start.sh` file also runs:

```bash
python main.py
```

In Railway Networking, expose port `8080` or make sure Railway maps the service `PORT` environment variable to the public domain. Then configure Tribute to send webhooks to:

```text
https://your-railway-domain/tribute/webhook
```

## Tribute webhook format

The handler expects real Tribute payments in this form:

```json
{
  "name": "new_donation",
  "payload": {
    "telegram_user_id": 123456789,
    "amount": 19900
  }
}
```

The test request `{"test_event": "test_event"}` is accepted and ignored. User identification is based only on `payload.telegram_user_id`; comments are not used.

## Admin commands

| Command | Purpose |
| --- | --- |
| `/addkey` | Shows help for automatic 3X-UI key creation and manual reserve keys |
| `/addkey vless://...` | Saves a reserve manual key in SQLite |
| `/give user_id` | Manually issues 30 days of access through 3X-UI |
| `/revoke user_id` | Disables the user in 3X-UI and marks the subscription inactive |
| `/users` | Shows registered users and subscription status |
| `/keys` | Shows active client count, free slots, reserve keys and 3X-UI limits |

## Important notes

Only one instance of the bot should run at the same time, otherwise Telegram can raise `TelegramConflictError`. All user-facing messages use HTML parse mode. Message edits are wrapped to avoid failures when Telegram returns `message is not modified`.
