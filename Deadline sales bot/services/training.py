"""Training-loop service: draft / refine / approve operator corrections.

Workflow (called from /admin/training/* endpoints in main.py):

    1) Operator pastes a conversation snippet + a note describing what was
       wrong → call draft_correction → bot proposes a rule + sample response
       + a confirmation question. Returns a session_id so we can iterate.

    2) Operator either approves (→ approve_correction saves the rule to
       training_corrections table with bge-m3 embedding for future retrieval)
       or replies with feedback (→ refine_correction does another LLM pass
       with the operator's comment in context, proposes a new variant).

    3) Refine can loop until approve. After approve the session is dropped.

Sessions are kept in-memory only — abandoned sessions evict after
TRAINING_SESSION_TTL (15 min) or when we hit MAX_SESSIONS. No need for
persistence: if the operator's browser tab closes mid-iteration, they
just start over with fresh draft.

The bot used at inference time (for real lead chat) calls
retrieve_corrections to fetch top-K relevant rules and inject them into
its SYSTEM_PROMPT. That function is the read-path of this same module.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Optional

from langchain_core.messages import HumanMessage, SystemMessage
from sqlalchemy import select
from sqlalchemy.orm import Session

from db.models import TrainingCorrection
from db.vector import _get_embedder
from prompts import TRAINER_SYSTEM_PROMPT, TRAINER_REFINE_PROMPT


log = logging.getLogger("deadline-bot.training")


# ============================================================================
# In-memory session state
# ============================================================================

TRAINING_SESSION_TTL = 15 * 60  # 15 min
MAX_SESSIONS = 100


@dataclass
class TrainingSession:
    """Live state of one operator-correction iteration in progress.

    Created on draft_correction, mutated on refine_correction, dropped on
    approve_correction or eviction.
    """
    session_id: uuid.UUID
    dialog: str
    correction_note: str
    channel: Optional[str]
    source_conversation_id: Optional[uuid.UUID]
    # Chat history with the trainer LLM (alternating system/user/assistant)
    chat_history: list[dict] = field(default_factory=list)
    # Latest proposal fields — overwritten on each refine
    last_proposed_rule: Optional[str] = None
    last_proposed_response: Optional[str] = None
    last_confirmation_question: Optional[str] = None
    created_at: float = field(default_factory=time.time)


# OrderedDict for FIFO eviction. Single-process FastAPI on Railway — no
# cross-instance coordination needed yet.
_SESSIONS: "OrderedDict[uuid.UUID, TrainingSession]" = OrderedDict()


def _evict_old() -> None:
    """Drop sessions older than TTL, then enforce the size cap."""
    now = time.time()
    while _SESSIONS:
        first_id = next(iter(_SESSIONS))
        if _SESSIONS[first_id].created_at < now - TRAINING_SESSION_TTL:
            _SESSIONS.popitem(last=False)
        else:
            break
    while len(_SESSIONS) >= MAX_SESSIONS:
        _SESSIONS.popitem(last=False)


# ============================================================================
# LLM call helpers
# ============================================================================

def _parse_trainer_json(raw: str) -> dict:
    """Strip code-fences and parse the trainer LLM's JSON response.
    Raises ValueError if parse fails — caller turns it into a 422.
    """
    s = raw.strip()
    s = s.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        return json.loads(s)
    except json.JSONDecodeError as e:
        # Last-chance recovery: maybe the model wrapped JSON in extra prose.
        # Look for first `{` and last `}` and try to parse that span.
        first = s.find("{")
        last = s.rfind("}")
        if first != -1 and last != -1 and last > first:
            try:
                return json.loads(s[first:last + 1])
            except json.JSONDecodeError:
                pass
        raise ValueError(f"trainer LLM returned invalid JSON: {e}") from e


async def _trainer_invoke(llm, messages: list) -> dict:
    """Call the trainer LLM and return the parsed JSON proposal.

    `llm` is a langchain ChatOpenAI instance (passed in from main.py so we
    don't import settings here and keep services/ free of global state).
    `messages` is the chat history (system + user turns).
    """
    response = await llm.ainvoke(messages)
    return _parse_trainer_json(response.content)


# ============================================================================
# Public API — draft / refine / approve
# ============================================================================

async def draft_correction(
    llm,
    dialog: str,
    correction_note: str,
    channel: Optional[str] = None,
    source_conversation_id: Optional[uuid.UUID] = None,
) -> tuple[uuid.UUID, dict]:
    """Start a new training session. Operator pastes a conversation snippet
    and a comment about what should have been different; trainer LLM
    proposes a rule + sample response + confirmation question.

    Returns:
        (session_id, proposal) — session_id to reference in refine/approve,
        proposal is dict with keys: proposed_rule, proposed_response,
        confirmation_question.
    """
    _evict_old()
    session_id = uuid.uuid4()

    user_msg = (
        f"Диалог:\n{dialog.strip()[:6000]}\n\n"
        f"Комментарий оператора (что было не так / как надо):\n{correction_note.strip()[:2000]}"
    )
    messages = [
        SystemMessage(content=TRAINER_SYSTEM_PROMPT),
        HumanMessage(content=user_msg),
    ]

    proposal = await _trainer_invoke(llm, messages)

    # Persist the session so refine_correction can resume the conversation
    chat_history = [
        {"role": "system", "content": TRAINER_SYSTEM_PROMPT},
        {"role": "user", "content": user_msg},
        {"role": "assistant", "content": json.dumps(proposal, ensure_ascii=False)},
    ]
    sess = TrainingSession(
        session_id=session_id,
        dialog=dialog,
        correction_note=correction_note,
        channel=channel,
        source_conversation_id=source_conversation_id,
        chat_history=chat_history,
        last_proposed_rule=proposal.get("proposed_rule"),
        last_proposed_response=proposal.get("proposed_response"),
        last_confirmation_question=proposal.get("confirmation_question"),
    )
    _SESSIONS[session_id] = sess
    log.info(f"training: drafted session {str(session_id)[:8]} (note len={len(correction_note)})")
    return session_id, proposal


async def refine_correction(
    llm,
    session_id: uuid.UUID,
    operator_feedback: str,
) -> dict:
    """Iterate on an existing draft. Operator says what they didn't like
    about the previous proposal; trainer LLM revises.

    Returns the new proposal dict (same keys as draft_correction).
    Raises KeyError if session_id is unknown (e.g. evicted).
    """
    _evict_old()
    sess = _SESSIONS.get(session_id)
    if sess is None:
        raise KeyError(f"unknown or expired training session: {session_id}")

    # Append operator feedback + refine instruction to the chat history
    sess.chat_history.append({"role": "user", "content": (
        f"Комментарий оператора по последнему варианту:\n{operator_feedback.strip()[:2000]}\n\n"
        f"{TRAINER_REFINE_PROMPT}"
    )})

    # Convert to langchain Message types for ainvoke
    lc_messages: list = []
    for m in sess.chat_history:
        if m["role"] == "system":
            lc_messages.append(SystemMessage(content=m["content"]))
        else:
            lc_messages.append(HumanMessage(content=m["content"]))

    proposal = await _trainer_invoke(llm, lc_messages)

    sess.chat_history.append({"role": "assistant", "content": json.dumps(proposal, ensure_ascii=False)})
    sess.last_proposed_rule = proposal.get("proposed_rule")
    sess.last_proposed_response = proposal.get("proposed_response")
    sess.last_confirmation_question = proposal.get("confirmation_question")
    log.info(f"training: refined session {str(session_id)[:8]} (iter {len(sess.chat_history) // 2})")
    return proposal


def approve_correction(
    db: Session,
    session_id: uuid.UUID,
    created_by: str = "admin",
) -> TrainingCorrection:
    """Persist the latest proposal to the training_corrections table with
    a bge-m3 embedding of the original dialog (so retrieve_corrections
    can find this rule by similarity at inference time).

    Returns the saved row. Raises KeyError if session_id is unknown,
    ValueError if there's no proposal to save.
    """
    sess = _SESSIONS.get(session_id)
    if sess is None:
        raise KeyError(f"unknown or expired training session: {session_id}")
    if not sess.last_proposed_rule:
        raise ValueError("no proposal in session — call draft_correction first")

    # Embed the dialog (trigger context) — what we'll match against at
    # inference time. We embed the FULL dialog, not just the rule, because
    # the bot's match is "current lead message is similar to past failure
    # case" rather than "current message is similar to abstract rule".
    embedder = _get_embedder()
    embedding = embedder.embed_query(sess.dialog[:8000])

    row = TrainingCorrection(
        trigger_context=sess.dialog[:8000],
        correct_guidance=sess.last_proposed_rule,
        suggested_response=sess.last_proposed_response,
        channel=sess.channel,
        embedding=embedding,
        created_by=created_by,
        source_conversation_id=sess.source_conversation_id,
        is_active=True,
    )
    db.add(row)
    db.flush()
    db.refresh(row)
    # Drop the session — it's served its purpose
    _SESSIONS.pop(session_id, None)
    log.info(f"training: approved {str(row.id)[:8]} (rule='{sess.last_proposed_rule[:60]}...')")
    return row


def discard_session(session_id: uuid.UUID) -> bool:
    """Operator abandoned the correction. Just drop the in-memory state.
    Returns True if a session was removed, False if it was already gone.
    """
    return _SESSIONS.pop(session_id, None) is not None


def get_session(session_id: uuid.UUID) -> Optional[TrainingSession]:
    """Read-only access to live session state — useful for debugging /
    showing the operator the current proposal without an LLM round-trip."""
    return _SESSIONS.get(session_id)


# ============================================================================
# Read-path: retrieve relevant rules at inference time
# ============================================================================

def retrieve_corrections(
    db: Session,
    query: str,
    k: int = 3,
    channel: Optional[str] = None,
    max_distance: float = 0.6,
) -> list[dict]:
    """Top-K active corrections whose trigger_context is closest to `query`
    in bge-m3 embedding space. Called from main._handle_message per lead
    request; results are passed to build_chat_prompt(corrections=...).

    Args:
        db: SQLAlchemy session
        query: The lead's current message (what we're about to respond to)
        k: How many corrections to retrieve
        channel: If set, prefer rules scoped to this channel (NULL = global).
                 Both global and channel-specific can be returned.
        max_distance: Cosine distance threshold — anything farther is
                      considered too dissimilar to be a real match.
                      pgvector cosine distance is in [0, 2]; 0.6 ~ "loosely
                      related". Tune if injecting too many false positives.

    Returns a list of dicts compatible with prompts.format_corrections_block:
        [{trigger_context, guidance, suggested_response, distance}, ...]
    Empty list if nothing close enough or no active rules in table.
    """
    embedder = _get_embedder()
    query_vec = embedder.embed_query(query)

    stmt = (
        select(
            TrainingCorrection,
            TrainingCorrection.embedding.cosine_distance(query_vec).label("distance"),
        )
        .where(TrainingCorrection.is_active == True)  # noqa: E712
    )
    if channel is not None:
        # Prefer channel-specific OR global. We can't easily ORDER BY both
        # similarity AND channel-priority in one query without window funcs,
        # so we just include both and let similarity sort it out — channel
        # rules tend to be more specific (so their trigger_context is more
        # similar to the channel's typical messages anyway).
        stmt = stmt.where(
            (TrainingCorrection.channel == channel) | (TrainingCorrection.channel.is_(None))
        )

    stmt = stmt.order_by(TrainingCorrection.embedding.cosine_distance(query_vec)).limit(k * 2)

    rows = db.execute(stmt).all()

    out: list[dict] = []
    for row in rows:
        dist = float(row.distance)
        if dist > max_distance:
            continue  # too dissimilar — would inject noise
        out.append({
            "trigger_context": row.TrainingCorrection.trigger_context,
            "guidance": row.TrainingCorrection.correct_guidance,
            "suggested_response": row.TrainingCorrection.suggested_response,
            "distance": dist,
            "id": str(row.TrainingCorrection.id),
        })
        if len(out) >= k:
            break
    return out
