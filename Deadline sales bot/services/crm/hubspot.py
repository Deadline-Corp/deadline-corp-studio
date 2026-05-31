"""HubSpot REST adapter (Phase 2, 2026-05-26).

Implements CRMAdapter against HubSpot v3 REST API using httpx.AsyncClient.
We avoid the official `hubspot-api-client` SDK because:
  1. It's sync-only; we'd have to run it in a thread executor everywhere.
  2. Its retry/error layer is opinionated and hard to integrate with our
     event-queue model (Phase 7).
  3. Six endpoints with two payload shapes are easier to maintain inline
     than the SDK's class hierarchy.

Auth: Service Key (Beta) or Private App access token — both are passed
identically as `Authorization: Bearer <token>`. See ADR §10 question 1.

On first use, the adapter:
  1. Ensures a custom pipeline named after tenant config (default "Deadline
     Sales") exists, with 12 stages from Notion §20.
  2. Ensures custom properties exist on Contact:
       telegram_handle, first_touch_channel, interaction_type,
       lead_temperature, lead_score
     and on Deal:
       project_type, lost_reason_internal (we use HubSpot's built-in
       closed_lost_reason where possible, but add an internal one for
       our specific reason taxonomy).

Both creates are idempotent — if HubSpot returns 409 / "already exists",
we treat it as success and continue.

Errors during business calls (upsert_contact / create_deal / log_message /
create_task) are NOT swallowed here — let them bubble up to the event
queue (Phase 7), which is the right place to decide retry vs drop.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any, Optional

import httpx

from services.crm.base import (
    CRMAdapter,
    Channel,
    Deal,
    InteractionType,
    Lead,
    LeadStage,
    LostReason,
    MessageLog,
    TaskCategory,
    Temperature,
)


logger = logging.getLogger(__name__)


# Default HubSpot API base — region-agnostic for v3 endpoints
DEFAULT_API_BASE = "https://api.hubapi.com"


# Mapping LeadStage → human-readable label for HubSpot pipeline stage
# (HubSpot generates stage ids internally; we look them up by label).
# Probabilities ((0..1)) influence forecast reports — values follow
# typical sales-funnel expectation.
#
# 2026-05-27: labels switched to RU per Deadline UI requirement
# (operators+ founders read RU; English-only labels were causing context
# switching during operator triage). Internal `_our_stage` keys
# (snake_case EN) are unchanged — only the display labels differ. The
# HubSpot pipeline 'Deadline Sales' (id=default, portal 246304597,
# region na2) was already updated via API to match these exact labels;
# `_reconcile_stages` will find every stage on first start without
# adding duplicates.
STAGE_DEFS: list[dict[str, Any]] = [
    {"label": "🆕 Новый лид",        "metadata": {"probability": "0.05", "isClosed": "false"}, "_our_stage": "new_lead"},
    {"label": "💬 В диалоге",         "metadata": {"probability": "0.10", "isClosed": "false"}, "_our_stage": "in_dialog"},
    {"label": "✅ Квалифицирован",    "metadata": {"probability": "0.20", "isClosed": "false"}, "_our_stage": "qualified"},
    {"label": "📜 NDA подписан",      "metadata": {"probability": "0.30", "isClosed": "false"}, "_our_stage": "nda"},
    {"label": "📞 Созвон назначен",   "metadata": {"probability": "0.40", "isClosed": "false"}, "_our_stage": "on_call"},
    {"label": "🎯 ТЗ согласовано",    "metadata": {"probability": "0.55", "isClosed": "false"}, "_our_stage": "tz_approved"},
    {"label": "📄 КП отправлено",     "metadata": {"probability": "0.65", "isClosed": "false"}, "_our_stage": "proposal"},
    {"label": "💰 Аванс получен",     "metadata": {"probability": "0.80", "isClosed": "false"}, "_our_stage": "prepayment"},
    {"label": "🤝 В работе",          "metadata": {"probability": "0.90", "isClosed": "false"}, "_our_stage": "in_work"},
    {"label": "✅ Сдано",             "metadata": {"probability": "1.00", "isClosed": "true"},  "_our_stage": "completed_won"},
    {"label": "🔁 Постпродажа",       "metadata": {"probability": "1.00", "isClosed": "true"},  "_our_stage": "post_sale"},
    {"label": "❌ Проигран",          "metadata": {"probability": "0.00", "isClosed": "true"},  "_our_stage": "lost"},
]


# Custom property definitions — created on first start if missing.
# fieldType / type taxonomy: https://developers.hubspot.com/docs/api/crm/properties
CONTACT_PROPERTIES: list[dict[str, Any]] = [
    {
        "name": "telegram_handle",
        "label": "Telegram Handle",
        "type": "string",
        "fieldType": "text",
        "groupName": "contactinformation",
        "description": "Telegram @username, optional companion to email",
    },
    {
        "name": "first_touch_channel",
        "label": "First Touch Channel",
        "type": "enumeration",
        "fieldType": "select",
        "groupName": "contactinformation",
        "description": "Channel where this lead first reached us",
        "options": [
            {"label": "Telegram",     "value": "telegram",   "displayOrder": 0},
            {"label": "Instagram",    "value": "instagram",  "displayOrder": 1},
            {"label": "FB Messenger", "value": "messenger",  "displayOrder": 2},
            {"label": "Website",      "value": "website",    "displayOrder": 3},
            {"label": "WhatsApp",     "value": "whatsapp",   "displayOrder": 4},
            {"label": "Email",        "value": "email",      "displayOrder": 5},
        ],
    },
    {
        "name": "interaction_type",
        "label": "Interaction Type",
        "type": "enumeration",
        "fieldType": "select",
        "groupName": "contactinformation",
        "description": "Notion §4 — set once at first touch",
        "options": [
            {"label": "P1 — Direct request / ad click", "value": "P1", "displayOrder": 0},
            {"label": "P2 — Form without explicit need", "value": "P2", "displayOrder": 1},
            {"label": "P3 — Return of cold lead",        "value": "P3", "displayOrder": 2},
            {"label": "P4 — Neutral reply / comment",    "value": "P4", "displayOrder": 3},
            {"label": "P5 — Stories / reactions",        "value": "P5", "displayOrder": 4},
            {"label": "P6 — We write first",             "value": "P6", "displayOrder": 5},
            {"label": "Hard Stop",                       "value": "HardStop", "displayOrder": 6},
        ],
    },
    {
        "name": "lead_temperature",
        "label": "Lead Temperature",
        "type": "enumeration",
        "fieldType": "select",
        "groupName": "contactinformation",
        "description": "Notion §7 — dynamic, decays on silence",
        "options": [
            {"label": "❄ Cold",   "value": "cold",   "displayOrder": 0},
            {"label": "☁ Warm",   "value": "warm",   "displayOrder": 1},
            {"label": "🔥 Hot",   "value": "hot",    "displayOrder": 2},
            {"label": "✅ Ready", "value": "ready",  "displayOrder": 3},
            {"label": "💎 Client","value": "client", "displayOrder": 4},
            {"label": "🧊 Frozen","value": "frozen", "displayOrder": 5},
        ],
    },
    {
        "name": "lead_score",
        "label": "Lead Score",
        "type": "number",
        "fieldType": "number",
        "groupName": "contactinformation",
        "description": "Notion §5 — base + content keywords + source weight, decays on silence",
    },
]

DEAL_PROPERTIES: list[dict[str, Any]] = [
    {
        "name": "project_type",
        "label": "Project Type",
        "type": "enumeration",
        "fieldType": "select",
        "groupName": "dealinformation",
        "description": "Deadline service line: Web / Automation / AI Agents / Mixed",
        "options": [
            {"label": "Web",         "value": "web",         "displayOrder": 0},
            {"label": "Automation",  "value": "automation",  "displayOrder": 1},
            {"label": "AI Agents",   "value": "ai_agents",   "displayOrder": 2},
            {"label": "Mixed",       "value": "mixed",       "displayOrder": 3},
            {"label": "Other",       "value": "other",       "displayOrder": 4},
        ],
    },
    {
        "name": "lost_reason_internal",
        "label": "Lost Reason (Deadline taxonomy)",
        "type": "enumeration",
        "fieldType": "select",
        "groupName": "dealinformation",
        "description": "Notion §20 split — required iff stage = Lost",
        "options": [
            {"label": "Price",         "value": "price",          "displayOrder": 0},
            {"label": "Not our format","value": "not_our_format", "displayOrder": 1},
            {"label": "Competitor",    "value": "competitor",     "displayOrder": 2},
            {"label": "Delayed",       "value": "delayed",        "displayOrder": 3},
            {"label": "No budget",     "value": "no_budget",      "displayOrder": 4},
            {"label": "Hard Stop",     "value": "hard_stop",      "displayOrder": 5},
        ],
    },
    {
        # 2026-05-27: добавлено, чтобы дата назначенного созвона была видна
        # прямо в карточке сделки в HubSpot UI без отдельной календарной
        # интеграции (Calendly/Cal.com отложены до фазы Phase 0e).
        # Оператор / бот ставит при движении в стадию '📞 Созвон назначен'.
        # Уже создано в HubSpot через API (idempotent — _ensure_properties
        # вернёт 409 на повторный создающий вызов).
        "name": "next_meeting_at",
        "label": "Назначенный созвон",
        "type": "datetime",
        "fieldType": "date",
        "groupName": "dealinformation",
        "description": "Дата и время следующего созвона с лидом (UTC ISO 8601)",
    },
]


class HubSpotAdapter(CRMAdapter):
    """HubSpot v3 REST adapter."""

    provider_name = "hubspot"

    def __init__(
        self,
        access_token: str,
        portal_id: Optional[str] = None,
        region: str = "na2",
        pipeline_name: str = "Deadline Sales",
        api_base: str = DEFAULT_API_BASE,
        timeout_sec: float = 15.0,
        owner_id: Optional[str] = None,
    ):
        if not access_token:
            raise ValueError("HubSpotAdapter requires an access_token")
        self.access_token = access_token
        self.portal_id = portal_id
        self.region = region
        # Owner (менеджер), на кого вешать задачи/сделки. None → без владельца.
        self.owner_id = owner_id
        self.pipeline_name = pipeline_name
        self.api_base = api_base
        self._timeout = timeout_sec

        # Populated lazily on first call to _ensure_setup()
        self._pipeline_id: Optional[str] = None
        self._stage_map: dict[str, str] = {}  # our_stage → hubspot stage_id
        self._setup_done = False
        self._setup_lock = asyncio.Lock()
        self._client: Optional[httpx.AsyncClient] = None

    # ------------------------------------------------------------------ HTTP

    def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.api_base,
                headers={
                    "Authorization": f"Bearer {self.access_token}",
                    "Content-Type": "application/json",
                },
                timeout=self._timeout,
            )
        return self._client

    async def _req(self, method: str, path: str, **kwargs) -> httpx.Response:
        client = self._ensure_client()
        resp = await client.request(method, path, **kwargs)
        if resp.status_code >= 400:
            logger.warning(
                "[hubspot] %s %s -> %d: %s",
                method, path, resp.status_code, resp.text[:300],
            )
        return resp

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # ------------------------------------------------------------------ Setup

    async def _ensure_setup(self) -> None:
        """Idempotent: create pipeline + custom properties if missing.

        Called once-per-process, guarded by an async lock so concurrent
        first calls don't race the HubSpot creates.
        """
        if self._setup_done:
            return
        async with self._setup_lock:
            if self._setup_done:
                return  # double-check after acquiring lock
            await self._ensure_pipeline()
            await self._ensure_properties()
            self._setup_done = True

    async def _ensure_pipeline(self) -> None:
        """Find, create, or adopt a pipeline; ensure 12 stages exist in it.

        Three branches:
          1. Pipeline named after self.pipeline_name exists → use it.
          2. It doesn't exist and HubSpot lets us create → create with all
             12 stages in one go.
          3. Create fails with 400 + 'limit' (HubSpot Free plan caps at
             1 deal pipeline) → adopt the first existing pipeline and
             *add* our 12 stages to it (keeping any default stages — they
             don't interfere, operators can ignore them).

        In all branches we end up with self._pipeline_id set and
        self._stage_map populated for all 12 of our LeadStage values.
        """
        resp = await self._req("GET", "/crm/v3/pipelines/deals")
        resp.raise_for_status()
        pipelines = resp.json().get("results", [])

        target: Optional[dict] = next(
            (p for p in pipelines if p.get("label") == self.pipeline_name),
            None,
        )

        if target is None:
            # Try creating our own pipeline
            payload = {
                "label": self.pipeline_name,
                "displayOrder": 0,
                "stages": [
                    {"label": s["label"], "metadata": s["metadata"], "displayOrder": i}
                    for i, s in enumerate(STAGE_DEFS)
                ],
            }
            create_resp = await self._req(
                "POST", "/crm/v3/pipelines/deals", json=payload,
            )
            if create_resp.status_code in (200, 201):
                target = create_resp.json()
                logger.info(
                    "[hubspot] created pipeline %r id=%s", self.pipeline_name, target["id"],
                )
            elif create_resp.status_code == 400 and "limit" in create_resp.text.lower():
                # Free plan path — adopt the first existing pipeline
                if not pipelines:
                    raise RuntimeError(
                        "HubSpot pipeline limit reached but no existing pipelines to adopt"
                    )
                target = pipelines[0]
                logger.warning(
                    "[hubspot] free plan pipeline limit hit — adopting existing pipeline "
                    "label=%r id=%s and adding 12 Deadline stages to it",
                    target.get("label"), target["id"],
                )
            else:
                create_resp.raise_for_status()
        else:
            logger.info(
                "[hubspot] pipeline %r exists id=%s", self.pipeline_name, target["id"],
            )

        assert target is not None
        self._pipeline_id = target["id"]

        # Reconcile stages — add any missing ones by label
        await self._reconcile_stages(self._pipeline_id, target.get("stages") or [])

    async def _reconcile_stages(
        self, pipeline_id: str, existing_stages: list[dict],
    ) -> None:
        """Make sure every label in STAGE_DEFS is present as a stage.

        Adds missing ones via POST /pipelines/deals/{id}/stages.
        Builds self._stage_map (our_stage → hubspot stage id).
        """
        existing_by_label = {s.get("label"): s for s in existing_stages}
        label_to_our = {s["label"]: s["_our_stage"] for s in STAGE_DEFS}

        for i, defn in enumerate(STAGE_DEFS):
            label = defn["label"]
            if label in existing_by_label:
                continue
            # Add it
            payload = {
                "label": label,
                "metadata": defn["metadata"],
                # Display after the existing stages — operator can drag-reorder later
                "displayOrder": 100 + i,
            }
            add_resp = await self._req(
                "POST",
                f"/crm/v3/pipelines/deals/{pipeline_id}/stages",
                json=payload,
            )
            if add_resp.status_code in (200, 201):
                logger.info("[hubspot] added stage %r to pipeline %s", label, pipeline_id)
                existing_by_label[label] = add_resp.json()
            elif add_resp.status_code == 409:
                pass  # raced with another startup, fine
            else:
                logger.warning(
                    "[hubspot] could not add stage %r: %d %s",
                    label, add_resp.status_code, add_resp.text[:200],
                )

        # Re-fetch the pipeline to get fresh stage ids if we added any
        if any(defn["label"] not in {s.get("label") for s in existing_stages}
               for defn in STAGE_DEFS):
            resp = await self._req("GET", f"/crm/v3/pipelines/deals/{pipeline_id}")
            if resp.status_code == 200:
                existing_by_label = {
                    s.get("label"): s for s in resp.json().get("stages", [])
                }

        # Build stage_map
        for stage in existing_by_label.values():
            our = label_to_our.get(stage.get("label"))
            if our:
                self._stage_map[our] = stage["id"]

    async def _ensure_properties(self) -> None:
        """Create custom contact + deal properties if missing.

        HubSpot returns 409 on duplicate name — we treat it as success.
        """
        for obj_type, defs in [("contacts", CONTACT_PROPERTIES), ("deals", DEAL_PROPERTIES)]:
            for prop in defs:
                resp = await self._req(
                    "POST",
                    f"/crm/v3/properties/{obj_type}",
                    json=prop,
                )
                if resp.status_code in (200, 201):
                    logger.info("[hubspot] created %s property %r", obj_type, prop["name"])
                elif resp.status_code == 409:
                    pass  # already exists, idempotent
                else:
                    # log but don't crash — bot can still run without custom props
                    logger.warning(
                        "[hubspot] property create %s.%s -> %d",
                        obj_type, prop["name"], resp.status_code,
                    )

    # ------------------------------------------------------------------ API

    async def health_check(self) -> bool:
        try:
            resp = await self._req("GET", "/crm/v3/objects/contacts", params={"limit": 1})
            return resp.status_code == 200
        except Exception as exc:  # noqa: BLE001
            logger.warning("[hubspot] health_check failed: %s", exc)
            return False

    async def upsert_contact(self, lead: Lead) -> str:
        await self._ensure_setup()

        # Build properties payload. Email/phone come from lead.identity_keys
        # or contact_handle (depends on channel).
        email = lead.identity_keys.get("email") or (
            lead.contact_handle if lead.contact_handle and "@" in (lead.contact_handle or "") else None
        )
        phone = lead.identity_keys.get("phone")
        tg_handle = lead.identity_keys.get("tg_handle") or (
            lead.contact_handle if lead.channel == "telegram" and lead.contact_handle else None
        )

        first_name, last_name = _split_name(lead.contact_name)

        props: dict[str, Any] = {
            "first_touch_channel": lead.channel,
            "interaction_type": lead.interaction_type,
            "lead_temperature": lead.temperature,
            "lead_score": lead.score,
        }
        if first_name:
            props["firstname"] = first_name
        if last_name:
            props["lastname"] = last_name
        if email:
            props["email"] = email
        if phone:
            props["phone"] = phone
        if tg_handle:
            props["telegram_handle"] = tg_handle.lstrip("@")

        # Search by email first (most reliable dedup key), then by phone
        existing_id = await self._search_contact(email=email, phone=phone)

        if existing_id:
            await self._req(
                "PATCH",
                f"/crm/v3/objects/contacts/{existing_id}",
                json={"properties": props},
            )
            logger.info("[hubspot] updated contact %s (lead=%s)", existing_id, lead.id)
            return existing_id

        # Create new contact
        resp = await self._req(
            "POST", "/crm/v3/objects/contacts", json={"properties": props},
        )
        resp.raise_for_status()
        contact_id = resp.json()["id"]
        logger.info("[hubspot] created contact %s (lead=%s)", contact_id, lead.id)
        return contact_id

    async def _search_contact(
        self, email: Optional[str], phone: Optional[str],
    ) -> Optional[str]:
        """Find existing contact by email or phone. Returns id or None."""
        filters: list[dict] = []
        if email:
            filters.append({
                "propertyName": "email",
                "operator": "EQ",
                "value": email.lower(),
            })
        if phone:
            filters.append({
                "propertyName": "phone",
                "operator": "EQ",
                "value": phone,
            })
        if not filters:
            return None

        for f in filters:
            payload = {
                "filterGroups": [{"filters": [f]}],
                "limit": 1,
            }
            resp = await self._req(
                "POST", "/crm/v3/objects/contacts/search", json=payload,
            )
            if resp.status_code != 200:
                continue
            results = resp.json().get("results", [])
            if results:
                return results[0]["id"]
        return None

    async def create_deal(self, deal: Deal, contact_id: str) -> str:
        await self._ensure_setup()

        stage_id = self._stage_map.get(deal.stage)
        if stage_id is None:
            logger.warning(
                "[hubspot] unknown stage %r — using new_lead as fallback", deal.stage,
            )
            stage_id = self._stage_map.get("new_lead", "")

        props: dict[str, Any] = {
            "dealname": deal.title,
            "pipeline": self._pipeline_id,
            "dealstage": stage_id,
        }
        if deal.project_type:
            props["project_type"] = deal.project_type
        if deal.brief:
            props["description"] = deal.brief
        if deal.lost_reason and deal.stage == "lost":
            props["lost_reason_internal"] = deal.lost_reason
        # Назначаем сделку на менеджера, если owner_id сконфигурирован.
        if self.owner_id:
            props["hubspot_owner_id"] = self.owner_id

        payload = {
            "properties": props,
            "associations": [{
                "to": {"id": contact_id},
                "types": [{
                    # contact-to-deal default association — 3 is the HubSpot internal id
                    "associationCategory": "HUBSPOT_DEFINED",
                    "associationTypeId": 3,
                }],
            }],
        }
        resp = await self._req("POST", "/crm/v3/objects/deals", json=payload)
        resp.raise_for_status()
        deal_id = resp.json()["id"]
        logger.info(
            "[hubspot] created deal %s (conv=%s stage=%s)",
            deal_id, deal.conversation_id, deal.stage,
        )
        return deal_id

    async def update_deal_stage(
        self,
        deal_id: str,
        stage: LeadStage,
        lost_reason: Optional[LostReason] = None,
        *,
        title: Optional[str] = None,
        description: Optional[str] = None,
        project_type: Optional[str] = None,
    ) -> None:
        await self._ensure_setup()

        stage_id = self._stage_map.get(stage)
        if stage_id is None:
            logger.warning("[hubspot] unknown stage %r — skipping update", stage)
            return

        props: dict[str, Any] = {"dealstage": stage_id}
        if stage == "lost" and lost_reason:
            props["lost_reason_internal"] = lost_reason
        # Phase C1 (2026-05-29): write a readable deal name + structured brief
        # onto the card together with the stage move. Set at handoff/qualified
        # from the classifier's task_summary — so the operator sees the gist
        # without opening the transcript. One PATCH = stage + card content.
        if title:
            props["dealname"] = title[:255]
        if description:
            props["description"] = description[:65000]
        if project_type:
            # Map classifier value ("AI Agents") → HubSpot enum option value
            # ("ai_agents"). Skip if it doesn't map — never send an invalid
            # enum (would 400 and fail the whole stage PATCH).
            _pt = {
                "web": "web", "automation": "automation",
                "ai agents": "ai_agents", "ai_agents": "ai_agents",
                "mixed": "mixed",
            }.get(project_type.strip().lower())
            if _pt:
                props["project_type"] = _pt

        resp = await self._req(
            "PATCH",
            f"/crm/v3/objects/deals/{deal_id}",
            json={"properties": props},
        )
        resp.raise_for_status()
        logger.info("[hubspot] updated deal %s stage=%s", deal_id, stage)

    async def update_lead_temperature(
        self, contact_id: str, temperature: Temperature,
    ) -> None:
        await self._ensure_setup()
        resp = await self._req(
            "PATCH",
            f"/crm/v3/objects/contacts/{contact_id}",
            json={"properties": {"lead_temperature": temperature}},
        )
        resp.raise_for_status()
        logger.info("[hubspot] contact %s temperature=%s", contact_id, temperature)

    async def log_message(self, msg: MessageLog, contact_id: str) -> None:
        """Write a Note engagement to the contact's timeline.

        Notes are the simplest, most universal way to attach text events;
        Conversations API would be more semantically correct but requires
        a connected channel inbox (vs Notes which works on any contact).
        """
        await self._ensure_setup()

        # HubSpot expects timestamp as ms since epoch
        ts_ms = int(msg.timestamp.timestamp() * 1000)

        # Build a readable note body — role and channel as a header line,
        # full text below. Metadata as a JSON line at the bottom for
        # operators to read if they care.
        header = f"[{msg.role.upper()} via {msg.channel}]"
        body = msg.text
        meta_line = ""
        if msg.metadata:
            interesting = {
                k: v for k, v in msg.metadata.items()
                if k in {"voice_duration_s", "has_image", "training_rule_id", "lang"}
            }
            if interesting:
                meta_line = f"\n\n_metadata: {interesting}_"

        note_body = f"{header}\n\n{body}{meta_line}"

        payload = {
            "properties": {
                "hs_note_body": note_body,
                "hs_timestamp": str(ts_ms),
            },
            "associations": [{
                "to": {"id": contact_id},
                "types": [{
                    # contact-to-note default association
                    "associationCategory": "HUBSPOT_DEFINED",
                    "associationTypeId": 202,
                }],
            }],
        }
        resp = await self._req("POST", "/crm/v3/objects/notes", json=payload)
        resp.raise_for_status()

    async def create_task(
        self,
        contact_id: str,
        deal_id: Optional[str],
        title: str,
        due_at: datetime,
        category: TaskCategory = "callback",
        description: Optional[str] = None,
    ) -> str:
        await self._ensure_setup()

        # HubSpot task types — we map our category to a reasonable native type
        type_map = {
            "qualification": "TODO",
            "warming": "EMAIL",
            "dunning": "EMAIL",
            "callback": "CALL",
        }
        hs_type = type_map.get(category, "TODO")

        due_ms = int(due_at.timestamp() * 1000)

        associations = [{
            "to": {"id": contact_id},
            "types": [{
                # contact-to-task default association
                "associationCategory": "HUBSPOT_DEFINED",
                "associationTypeId": 204,
            }],
        }]
        if deal_id:
            associations.append({
                "to": {"id": deal_id},
                "types": [{
                    # deal-to-task default association
                    "associationCategory": "HUBSPOT_DEFINED",
                    "associationTypeId": 216,
                }],
            })

        props = {
            "hs_task_subject": title,
            "hs_task_body": description or "",
            "hs_task_status": "NOT_STARTED",
            "hs_task_priority": "MEDIUM",
            "hs_task_type": hs_type,
            "hs_timestamp": str(due_ms),
        }
        # Назначаем задачу на менеджера, если owner_id сконфигурирован.
        if self.owner_id:
            props["hubspot_owner_id"] = self.owner_id
        payload = {
            "properties": props,
            "associations": associations,
        }
        resp = await self._req("POST", "/crm/v3/objects/tasks", json=payload)
        resp.raise_for_status()
        task_id = resp.json()["id"]
        logger.info(
            "[hubspot] created task %s contact=%s deal=%s category=%s",
            task_id, contact_id, deal_id, category,
        )
        return task_id

    async def complete_task(self, task_id: str) -> bool:
        """Пометить задачу COMPLETED (после самоисполнения ботом)."""
        if not task_id:
            return False
        await self._ensure_setup()
        resp = await self._req(
            "PATCH", f"/crm/v3/objects/tasks/{task_id}",
            json={"properties": {"hs_task_status": "COMPLETED"}},
        )
        resp.raise_for_status()
        logger.info("[hubspot] task %s → COMPLETED", task_id)
        return True


# ----------------------------------------------------------------------- helpers

def _split_name(name: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    """Split 'First Last' into (first, last). Best-effort, no surname-detect."""
    if not name:
        return None, None
    parts = name.strip().split(None, 1)
    if len(parts) == 1:
        return parts[0], None
    return parts[0], parts[1]
