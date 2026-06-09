"""Contact existence check for the lead form (2026-06-09).

Why: deadlinecorp.com/lead-form/ asks the visitor to TYPE their contact
(@telegram / instagram / phone / email). A typo there = an unreachable lead
(we hit one: "@saswee21", which does not exist — nothing server-side could
recover it). So we catch it at submit time and warn the visitor.

The only reliable way to check a Telegram username from the server is to fetch
its public t.me page and read the Open Graph tags:
  - EXISTS  → og:title is the real display name + og:image is a
              cdn*.telesco.pe profile photo.
  - MISSING → og:title == "Telegram: Contact @<handle>" (the generic stub)
              and og:image is the default Telegram logo (".../t_logo*.png").
Verified empirically on @durov / @deadline_corp / @a1exandreg (exist) and
@saswee21 (does NOT exist) on 2026-06-09.

Instagram is best-effort only — IG aggressively blocks server requests, so an
"unknown" verdict is common and must NOT produce a false warning.

Nothing here raises: every public function degrades to exists=None
("couldn't verify") so the lead form never breaks because of a check.
"""

from __future__ import annotations

import logging
import re
from typing import Optional
from urllib.parse import urlparse

import httpx

log = logging.getLogger(__name__)

_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
_TIMEOUT = 6.0

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_PHONE_RE = re.compile(r"^\+?[\d][\d\s\-()]{5,}\d$")
# Telegram username rules: 5-32 chars, [A-Za-z0-9_], must start with a letter.
# We allow a trailing dot/underscore loosely; the t.me fetch is the real judge.
_HANDLE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{3,31}$")
_OG_TITLE_RE = re.compile(
    r'<meta\s+property=["\']og:title["\']\s+content=["\']([^"\']*)["\']', re.I
)
_OG_IMAGE_RE = re.compile(
    r'<meta\s+property=["\']og:image["\']\s+content=["\']([^"\']*)["\']', re.I
)


def _strip_handle(raw: str) -> str:
    """Pull a bare username out of @name / t.me/name / instagram.com/name / URL."""
    s = (raw or "").strip()
    if "/" in s:
        try:
            path = urlparse(s if "://" in s else "https://" + s).path
        except Exception:  # noqa: BLE001
            path = s
        seg = [p for p in path.split("/") if p]
        if seg:
            s = seg[-1]
    return s.lstrip("@").strip()


def classify_contact(raw: str) -> tuple[str, str]:
    """Return (type, normalized) where type ∈ {telegram, instagram, phone, email, unknown}."""
    s = (raw or "").strip()
    if not s:
        return ("unknown", "")
    low = s.lower()

    if _EMAIL_RE.match(s):
        return ("email", s)

    if "t.me/" in low or "telegram.me/" in low or "telegram.org/" in low:
        return ("telegram", _strip_handle(s))
    if (
        "instagram.com/" in low
        or "instagr.am/" in low
        or "inst" in low
        or "инст" in low
        or low.startswith("ig:")
    ):
        target = s.split(":", 1)[-1] if low.startswith("ig:") else s
        return ("instagram", _strip_handle(target))

    digits = re.sub(r"[^\d]", "", s)
    if _PHONE_RE.match(s) and 7 <= len(digits) <= 15:
        return ("phone", ("+" + digits) if s.lstrip().startswith("+") else digits)

    bare = s.lstrip("@")
    if _HANDLE_RE.match(bare):
        # Form label is "Telegram или WhatsApp" → a bare @handle defaults to Telegram.
        return ("telegram", bare)

    return ("unknown", s)


def _tme_exists_from_html(html: str, handle: str) -> Optional[bool]:
    """Decide whether a Telegram username exists from its t.me page HTML.

    True / False, or None if the page couldn't be parsed.
    """
    m = _OG_TITLE_RE.search(html or "")
    if not m:
        return None
    title = (m.group(1) or "").strip()
    img_m = _OG_IMAGE_RE.search(html or "")
    image = (img_m.group(1) or "").strip() if img_m else ""

    generic = f"telegram: contact @{handle.lstrip('@').lower()}"
    if title.lower() == generic:
        return False
    if image and "t_logo" in image.lower():
        # default Telegram logo → no profile photo → treat as missing
        return False
    return True


async def check_telegram(handle: str) -> Optional[bool]:
    """True if @username resolves to a real Telegram account, False if not,
    None if we couldn't verify (network/parse). Never raises."""
    h = _strip_handle(handle)
    if not _HANDLE_RE.match(h):
        return None
    try:
        async with httpx.AsyncClient(
            timeout=_TIMEOUT, follow_redirects=True, headers={"User-Agent": _BROWSER_UA}
        ) as client:
            r = await client.get(f"https://t.me/{h}")
            if r.status_code != 200:
                return None
            return _tme_exists_from_html(r.text, h)
    except Exception as exc:  # noqa: BLE001
        log.warning("[contact_check] telegram check failed for %s: %s", h, exc)
        return None


async def check_instagram(handle: str) -> Optional[bool]:
    """Best-effort Instagram existence. IG blocks server requests hard, so this
    returns None ("unknown") unless it sees a clear signal. Never produces a
    false 'missing' — we'd rather not warn than warn wrongly."""
    h = _strip_handle(handle)
    if not h:
        return None
    try:
        async with httpx.AsyncClient(
            timeout=_TIMEOUT, follow_redirects=True, headers={"User-Agent": _BROWSER_UA}
        ) as client:
            r = await client.get(f"https://www.instagram.com/{h}/")
            if r.status_code == 404:
                return False
            if r.status_code != 200:
                return None
            body = r.text.lower()
            if (
                f'"{h.lower()}"' in body
                and ("profilepage" in body or "edge_followed_by" in body)
            ):
                return True
            return None
    except Exception as exc:  # noqa: BLE001
        log.warning("[contact_check] instagram check failed for %s: %s", h, exc)
        return None


_TYPE_LABEL = {
    "telegram": "Telegram",
    "instagram": "Instagram",
    "phone": "телефон",
    "email": "email",
    "unknown": "контакт",
}


async def check_contact_exists(raw: str) -> dict:
    """Classify a typed contact and, for Telegram/Instagram, verify it resolves
    to a real account.

    Returns {type, normalized, exists, label} where exists is:
      True  — verified to exist
      False — verified NOT to exist (warn the visitor!)
      None  — not checkable (phone/email/unknown) or couldn't verify
    Never raises.
    """
    ctype, normalized = classify_contact(raw)
    exists: Optional[bool] = None
    try:
        if ctype == "telegram":
            exists = await check_telegram(normalized)
        elif ctype == "instagram":
            exists = await check_instagram(normalized)
    except Exception as exc:  # noqa: BLE001
        log.warning("[contact_check] check failed: %s", exc)
        exists = None
    return {
        "type": ctype,
        "normalized": normalized,
        "exists": exists,
        "label": _TYPE_LABEL.get(ctype, "контакт"),
    }
