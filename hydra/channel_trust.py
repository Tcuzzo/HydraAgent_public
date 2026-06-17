"""hydra.channel_trust — the operator's rule-1 trust model (2026-06-02).

The operator chose: keep the LAW (only destructive actions gate) on the operator's own
TRUSTED Telegram bot session, but add a hard exception for PUBLIC/untrusted surfaces.

> "1 with an exception: public chats, Discord, or any social website input from those
> sites cannot call tools. Social media chat or messengers outside of Telegram bot
> sessions cannot call tools without approval. No public social-media site where anyone
> outside the operator can give input into chat and call tools."

So trust depends on the SURFACE the input came from plus whether it's the operator:

  - TRUSTED  (operator's Telegram bot session): the LAW applies, enforced by
    hydra.policy.ApprovalPolicy — this module stays out of the way (never escalates).
  - UNTRUSTED (Discord public chat, social media, any messenger outside the operator's
    Telegram bot session, any public site where a non-operator can feed input): NO tool
    that DOES something may run without the operator's approval.

Research / read-only tools always run free (no side effects). Self-heal is always exempt.
The decision here is pure and deterministic; routing the ask and running
calls in parallel is handled by the caller.
"""
from __future__ import annotations

# Read-only / information-gathering tools. They have no side effects, so they run free
# even when the input comes from a public, untrusted surface.
RESEARCH_TOOLS = frozenset({
    "fs_read",
    "grep",
    "glob",
    "list_directory",
    "http_fetch",
    "memory_recall",
    "skill_list",
    "skill_route",
    "skill_search",
    "skill_show",
    "system_stats",
    "collab_peers",
    "collab_read",
    "agent_read_messages",
    "todo",
})

# Surfaces we consider trusted: the operator acting in our own Telegram bot session.
# Everything else (public Discord, social media, other messengers, web input) is untrusted.
TRUSTED_SURFACES = frozenset({
    "telegram_operator",
    "operator",
    "cli",
    "self",
})


def is_research_tool(tool_name: str) -> bool:
    """True for read-only / information-gathering tools that run free anywhere."""
    return tool_name in RESEARCH_TOOLS


def is_trusted_surface(surface: str | None, *, is_operator: bool) -> bool:
    """A surface is trusted only when it's one of our own operator surfaces AND the
    sender is actually the operator. A public surface is never trusted; a non-operator
    on any surface is never trusted."""
    if not is_operator:
        return False
    return (surface or "").strip().lower() in TRUSTED_SURFACES


def requires_operator_approval(
    tool_name: str,
    *,
    surface_trusted: bool,
    is_self_heal: bool = False,
) -> bool:
    """Does this tool call need the operator's approve button BECAUSE of where the input
    came from? Returns True only for an ACTION tool arriving from an UNTRUSTED surface.

    On a trusted surface this returns False — the LAW (destructive-only gating) is applied
    separately by ApprovalPolicy, so this channel gate must not double-gate or interfere.
    """
    if is_self_heal:
        return False
    if is_research_tool(tool_name):
        return False
    if surface_trusted:
        return False
    return True
