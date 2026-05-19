"""Business logic services for the multi-channel sales bot.

Each module here is pure Python — no FastAPI / HTTP. They take a SQLAlchemy
Session and operate on db.models. This separation is so the same code can be
called from the `/message` endpoint, the Telegram webhook, the IG/FB webhooks,
or from unit tests, with identical semantics.
"""
