"""Channel adapters: parse incoming webhooks into a normalized message
that the universal /message endpoint can consume, and send the bot's
reply back through the channel's native API.

One module per channel:
- telegram.py — Bot API webhook + sendMessage
- instagram.py (Day 5) — IG Graph API webhook + send_api
- messenger.py (Day 5) — Messenger Platform webhook + send_api
"""
