# Telegram inline mode

Referral sharing uses Telegram inline queries. Inline mode must be enabled once for the bot:

1. Open `@BotFather`.
2. Send `/setinline`.
3. Select the DealDock bot.
4. Set an inline placeholder, for example: `Share your DealDock invitation`.

At startup, DealDock checks `supports_inline_queries`. If inline mode is disabled, it writes the
`telegram_inline_mode_disabled` warning with the same instruction to the application log.
