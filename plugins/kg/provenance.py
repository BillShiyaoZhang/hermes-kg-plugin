"""Deterministic human-text boundary for extraction and evidence validation.

These are Hermes wire markers, not keyword guesses about whether a conversation
is a summary. Historical JSONL can contain synthetic user-role rows after their
private metadata was stripped. Keep this boundary identical for model input and
evidence lookup. It never changes a durable snapshot or source transcript.

Contract references in the installed core:
  agent/context_compressor.py:180-265,354-361,456-526,3612-3632,3787-3811
  agent/conversation_loop.py:825-875; agent/prompt_builder.py:519
  agent/background_review.py:299-310,368-373 (background review prompt openings)
"""
from __future__ import annotations

from .session_source import _text

COMPACTION_PREFIX="[CONTEXT COMPACTION — REFERENCE ONLY] Earlier turns were compacted into the summary below."
LEGACY_SUMMARY_PREFIX="[CONTEXT SUMMARY]:"
PRIOR_HEADER="[PRIOR CONTEXT — for reference only; not a new message]"
MERGED_DELIMITER="[END OF PRIOR CONTEXT — COMPACTION SUMMARY BELOW]"
SUMMARY_END="--- END OF CONTEXT SUMMARY — respond to the message below, not the summary above ---"

# Exact, long Hermes template openings also observed in historical user-role rows.
# Short user requests to review memory or skills remain ordinary human text.
SKILL_REVIEW_PREFIX=(
    "Review the conversation above and update the skill library. Be ACTIVE — most sessions produce "
    "at least one skill update, even if small. A pass that does nothing is a missed learning "
    "opportunity, not a neutral outcome."
)
MEMORY_REVIEW_PREFIX=(
    "Review the conversation above and consider saving to memory if appropriate.\n\n"
    "Focus on:\n"
    "1. Has the user revealed things about themselves — their persona, desires, preferences, or "
    "personal details worth remembering?"
)
CURRENT_MEMORY_REVIEW_PREFIX=(
    "Review the conversation above and consider saving to memory if appropriate.\n\n"
    "Memory has TWO distinct stores — pick the right one for each fact:"
)
COMBINED_REVIEW_PREFIX=(
    "Review the conversation above and update two things:\n\n"
    "**Memory**: TWO distinct stores — pick the right one for each fact:"
)
LEGACY_COMBINED_REVIEW_PREFIX=(
    "Review the conversation above and update two things:\n\n"
    "**Memory**: who the user is. Did the user reveal persona, desires, preferences, personal "
    "details, or expectations about how you should behave?"
)

PREFIX_REASONS=(
    (SKILL_REVIEW_PREFIX,"background_skill_review_prompt"),
    (MEMORY_REVIEW_PREFIX,"background_memory_review_prompt"),
    (CURRENT_MEMORY_REVIEW_PREFIX,"background_memory_review_prompt"),
    (COMBINED_REVIEW_PREFIX,"background_combined_review_prompt"),
    (LEGACY_COMBINED_REVIEW_PREFIX,"background_combined_review_prompt"),
    ("[IMPORTANT: Background process ","background_process_notification"),
    ("[Your active task list was preserved across context compression]\n","preserved_todo_notice"),
    ("[System: Your previous tool call ","tool_continuation_notice"),
    ("[System: The active model for this chat has changed to ","model_switch_notice"),
)
EXACT_NUDGES={
    "Continue from the compressed conversation context above. This marker exists because no human user turn was available.",
    "Continue from the compressed conversation context above. This marker exists because the compacted transcript contained no preserved user turn.",
    "You've reached the maximum number of tool-calling iterations allowed. Please provide a final response summarizing what you've found and accomplished so far, without calling any more tools.",
    "[System: The previous response was cut off by a network error mid-stream. Continue exactly where you left off. Do not restart or repeat prior text. Finish the answer directly.]",
    "[System: Your previous response was truncated by the output length limit. Continue exactly where you left off. Do not restart or repeat prior text. Finish the answer directly.]",
    "[System: Your previous response contained only internal reasoning and never produced a visible answer or tool call. Do not keep thinking. Produce your final answer as plain text now (or make the tool call you were planning).]",
    "[System: Continue now. Execute the required tool calls and only send your final answer after completing the task.]",
    "Your previous turn indicated a tool call but none was included. Do not narrate a plan or restate intent — issue the actual tool call now to continue the task.",
    "You just executed tool calls but returned an empty response. Please process the tool results above and continue with the task.",
}


def _starts_summary(text):
    return text.startswith((COMPACTION_PREFIX,LEGACY_SUMMARY_PREFIX))


def _project(message):
    if not isinstance(message,dict) or message.get("role")!="user":
        return None,"not_user"
    if message.get("active",1) in (0,False):
        return None,"inactive"
    text=_text(message.get("content"))
    stripped=text.lstrip()
    display_kind=message.get("display_kind")
    is_summary=bool(message.get("_compressed_summary")) or bool(message.get("_micro_compact_marker")) or _starts_summary(stripped)
    # A merged carrier has a preserved human segment before the explicitly
    # delimited handoff. Only unwrap the exact Hermes marker combination.
    if stripped.startswith(PRIOR_HEADER) and MERGED_DELIMITER in stripped:
        before,after=stripped.split(MERGED_DELIMITER,1)
        if _starts_summary(after.lstrip()):
            is_summary=True
            text=before[len(PRIOR_HEADER):].strip()
    elif is_summary and SUMMARY_END in stripped:
        text=stripped.split(SUMMARY_END,1)[1].lstrip()
    elif is_summary:
        return None,"compaction_handoff"
    if display_kind and display_kind!="steer" and not (is_summary and display_kind=="hidden"):
        return None,"synthetic_display_kind"
    if not text.strip():
        return None,"compaction_handoff" if is_summary else "empty_user_text"
    # Re-check the unwrapped content so another stacked handoff/nudge cannot
    # become evidence merely because it followed an old handoff boundary.
    view_text=text.strip()
    if _starts_summary(view_text) or view_text.startswith(PRIOR_HEADER):
        return None,"compaction_handoff"
    if view_text in EXACT_NUDGES:
        return None,"automatic_continuation_nudge"
    for prefix,reason in PREFIX_REASONS:
        if view_text.startswith(prefix):
            return None,reason
    result={**message,"content":text}
    for key in ("_compressed_summary","_compressed_summary_has_user_turn","_micro_compact_marker"):
        result.pop(key,None)
    return result,"preserved_merged_user_text" if is_summary else None


def user_message_view(message):
    """Return a fresh human-only message view, or None for synthetic rows."""
    return _project(message)[0]


def groundable_user_messages(messages):
    """Same user evidence set for LLM input and quote/id grounding checks."""
    if isinstance(messages,dict):
        messages=messages.get("messages",[])
    return [view for message in messages if (view:=user_message_view(message)) is not None]


def provenance_summary(messages):
    """Safe IDs and reasons only; no conversation contents are returned."""
    if isinstance(messages,dict):
        messages=messages.get("messages",[])
    excluded=[]
    projected=[]
    for message in messages:
        if not isinstance(message,dict) or message.get("role")!="user":
            continue
        view,reason=_project(message)
        if view is None:
            excluded.append({"id":message.get("id"),"reason":reason})
        elif reason:
            projected.append({"id":message.get("id"),"reason":reason})
    return {"excluded":excluded,"projected":projected}
