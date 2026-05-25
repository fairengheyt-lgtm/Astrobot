# AstroVPN Telegram Bot

AstroVPN is a production-oriented Telegram bot for selling and managing **VLESS Reality / 3X-UI** subscriptions. The bot uses Telegram polling for user interaction and runs a small `aiohttp` web server for Tribute webhooks and health checks.

## What is implemented

The current version is intentionally kept as a single `main.py` for easy Railway deployment, but internally it is split into clear layers: typed configuration, SQLite persistence, 3X-UI server pool, VPN client service, payment processing, Telegram UI, admin tools and background maintenance.

| Area | Implementation |
| --- | --- |
| Runtime | `aiogram 3` polling plus `aiohttp` server on `PORT` |
| Database | SQLite with WAL mode, users, subscriptions, pending payments and processed-event idempotency |
| Payments | Telegram Stars and Tribute, both controlled by feature flags |
| VPN panel | `py3xui.AsyncApi`, automatic client create/update/disable |
| Servers | Single server by default, optional `XUI_SERVERS_JSON` multi-server pool |
| Links | Manual `vless://` Reality link or optional 3X-UI subscription URL via `XUI_SUBSCRIPTION_BASE` |
| Maintenance | Expired pending payments cleanup, 24-hour expiry reminders, automatic disabling of expired clients |
| Admins | Single `ADMIN_ID` or comma-separated `BOT_ADMINS` / `ADMIN_IDS` |

## Railway setup

Set variables from `.env.example` in Railway. The required minimum is `BOT_TOKEN`, `ADMIN_ID` or `BOT_ADMINS`, `XUI_USERNAME`, `XUI_PASSWORD`, `VLESS_PUBLIC_KEY` and `VLESS_SHORT_ID`. If you use 3X-UI subscription links instead of direct VLESS links, also set `XUI_SUBSCRIPTION_BASE`.

The deployment command is already configured as:

```toml
[deploy]
startCommand = "python main.py"
```

The `start.sh` file also runs:

```bash
python main.py
```

Expose the Railway service port through the default `PORT` variable. The health check endpoint is:

```text
https://your-railway-domain/health
```

## Tribute webhook

Configure Tribute to send webhooks to:

```text
https://your-railway-domain/tribute/webhook
```

If `TRIBUTE_SECRET` is set, use either the header `X-Tribute-Secret` or append it to the webhook URL:

```text
https://your-railway-domain/tribute/webhook?secret=YOUR_SECRET
```

The handler accepts the test request `{"test_event":"test_event"}` and ignores it. Real payments are expected as `new_donation` by default:

```json
{
  "name": "new_donation",
  "payload": {
    "id": "tribute-payment-id",
    "telegram_user_id": 123456789,
    "amount": 19900,
    "currency": "RUB"
  }
}
```

The bot can also read the Telegram ID from `payload.telegram_id`, `payload.tg_id` or `payload.comment`, which makes manual Tribute configurations more tolerant.

## Multi-server configuration

For one server, use the simple variables from `.env.example`: `XUI_HOST`, `SERVER_IP`, `XUI_INBOUND_ID`, `MAX_CLIENTS` and VLESS Reality fields. For multiple servers, set `XUI_SERVERS_JSON`; it overrides the single-server variables.

```json
[
  {
    "id": 1,
    "name": "nl-1",
    "host": "https://1.2.3.4:2053",
    "server_ip": "1.2.3.4",
    "max_clients": 100,
    "inbound_id": 1,
    "subscription_base": ""
  }
]
```

If `XUI_DYNAMIC_INBOUND=true`, the bot asks 3X-UI for the first inbound and uses it automatically. If this fails and `inbound_id` is set, the configured value is used as fallback.

## Admin commands

| Command | Purpose |
| --- | --- |
| `/addkey` | Compatibility help: explains that keys are now created automatically in 3X-UI |
| `/give <user_id> [days]` | Manually issue or extend access for a user |
| `/revoke <user_id>` | Disable the client in 3X-UI and mark subscription inactive |
| `/users` | Show recently registered users and their subscription status |
| `/stats` | Show total users, active subscriptions and server usage |
| `/keys` | Alias for `/stats` |
| `/backup` | Send a temporary SQLite database backup to the admin |

## Notes

Only one instance of the bot should run at the same time, otherwise Telegram may return a polling conflict. User-facing messages use Telegram HTML parse mode. Payment processing is idempotent, so repeated Stars or Tribute events with the same payment identifier do not grant duplicate subscription periods.
