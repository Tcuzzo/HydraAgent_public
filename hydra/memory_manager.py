"""Unified memory manager for HydraAgent.

This module provides a unified interface to all of HydraAgent's memory systems:
- Session memory (conversation history persistence)
- Working memory (semantic search, entities, facts)
- Local file-based memory (existing system)
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, List, Dict, Optional, Tuple

from hydra.session_memory import (
    create_session as create_session_memory,
    add_message as add_session_message,
    get_session_messages,
    session_exists as session_memory_exists,
    delete_session as delete_session_memory,
    list_sessions as list_session_memories
)

from hydra.working_memory import (
    create_memory as create_working_memory,
    add_entry as add_working_memory_entry,
    search_entries as search_working_memory,
    get_recent_entries as get_recent_working_memory,
    add_entity as add_working_memory_entity,
    get_entity as get_working_memory_entity,
    get_entities_by_type as get_working_memory_entities_by_type,
    add_entity_relation as add_working_memory_relation,
    get_memory_stats as get_working_memory_stats,
    memory_exists as working_memory_exists,
    delete_memory as delete_working_memory,
    list_memories as list_working_memories,
    create_default_memory
)

from hydra.local_memory import build_local_memory_context


MEMORY_MANAGER_SCHEMA = "hydra.memory_manager.v1"
DEFAULT_SESSION_ID = "hydra_default_session"
DEFAULT_WORKING_MEMORY_ID = "hydra_default_memory"


class MemoryManagerError(Exception):
    """Memory manager operation failed."""


class MemoryManager:
    """Unified memory manager for HydraAgent."""

    def __init__(self, session_id: Optional[str] = None, working_memory_id: Optional[str] = None):
        self.session_id = session_id or DEFAULT_SESSION_ID
        self.working_memory_id = working_memory_id or DEFAULT_WORKING_MEMORY_ID

        # Initialize session memory if it doesn't exist
        if not session_memory_exists(self.session_id):
            try:
                create_session_memory(self.session_id, "HydraAgent session memory")
            except Exception:
                # If we can't create session memory, continue without it
                pass

        # Initialize working memory if it doesn't exist
        if not working_memory_exists(self.working_memory_id):
            try:
                create_working_memory(self.working_memory_id, "HydraAgent working memory")
            except Exception:
                # If we can't create working memory, continue without it
                pass

    def add_conversation_turn(self, user_message: str, assistant_response: str) -> None:
        """Add a conversation turn to session memory."""
        try:
            if session_memory_exists(self.session_id):
                add_session_message(self.session_id, "user", user_message)
                add_session_message(self.session_id, "assistant", assistant_response)
        except Exception:
            # Don't let memory errors break the conversation
            pass

    def get_conversation_history(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """Get conversation history from session memory."""
        try:
            if session_memory_exists(self.session_id):
                return get_session_messages(self.session_id, limit)
        except Exception:
            pass
        return []

    def add_fact(self, content: str, fact_type: str = "fact", tags: Optional[List[str]] = None) -> str:
        """Add a fact to working memory."""
        try:
            if working_memory_exists(self.working_memory_id):
                return add_working_memory_entry(
                    self.working_memory_id,
                    content,
                    entry_type=fact_type,
                    tags=tags or []
                )
        except Exception:
            pass
        return ""

    def add_observation(self, content: str, tags: Optional[List[str]] = None) -> str:
        """Add an observation to working memory."""
        return self.add_fact(content, "observation", tags)

    def search_memory(self, query: str, tags: Optional[List[str]] = None, limit: int = 10) -> List[Dict[str, Any]]:
        """Search working memory for relevant entries."""
        try:
            if working_memory_exists(self.working_memory_id):
                return search_working_memory(self.working_memory_id, query, tags, limit=limit)
        except Exception:
            pass
        return []

    def get_recent_facts(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Get recent facts from working memory."""
        try:
            if working_memory_exists(self.working_memory_id):
                return get_recent_working_memory(self.working_memory_id, limit)
        except Exception:
            pass
        return []

    def add_entity(self, name: str, entity_type: str, attributes: Optional[Dict[str, Any]] = None) -> None:
        """Add an entity to working memory."""
        try:
            if working_memory_exists(self.working_memory_id):
                add_working_memory_entity(self.working_memory_id, name, entity_type, attributes)
        except Exception:
            pass

    def get_entity(self, name: str) -> Optional[Dict[str, Any]]:
        """Get an entity from working memory."""
        try:
            if working_memory_exists(self.working_memory_id):
                return get_working_memory_entity(self.working_memory_id, name)
        except Exception:
            pass
        return None

    def add_entity_relation(self, entity_name: str, relation_type: str, target_entity: str, attributes: Optional[Dict[str, Any]] = None) -> None:
        """Add a relation between entities in working memory."""
        try:
            if working_memory_exists(self.working_memory_id):
                add_working_memory_relation(self.working_memory_id, entity_name, relation_type, target_entity, attributes)
        except Exception:
            pass

    def get_local_memory_context(self, root: Optional[str] = None, max_chars: int = 12000) -> str:
        """Get context from local file-based memory."""
        try:
            result = build_local_memory_context(root, max_chars=max_chars)
            if result.status == "OK":
                return result.context
        except Exception:
            pass
        return ""

    def get_comprehensive_context(self, query: Optional[str] = None, max_chars: int = 16000) -> str:
        """Get comprehensive context from all memory systems."""
        context_parts = []

        # Add recent conversation history (last 5 turns)
        conversation_history = self.get_conversation_history(limit=10)  # 5 turns = 10 messages
        if conversation_history:
            history_text = "\n".join([f"[{msg['role']}] {msg['content']}" for msg in conversation_history])
            context_parts.append(f"## Recent Conversation History\n{history_text}")

        # Add relevant facts from working memory
        if query:
            relevant_facts = self.search_memory(query, limit=5)
        else:
            relevant_facts = self.get_recent_facts(limit=5)

        if relevant_facts:
            facts_text = "\n".join([f"- {fact['content']}" for fact in relevant_facts])
            context_parts.append(f"## Relevant Facts\n{facts_text}")

        # Add local file-based memory context
        local_context = self.get_local_memory_context()
        if local_context:
            context_parts.append(f"## Local Memory Context\n{local_context}")

        # Combine all context parts
        full_context = "\n\n".join(context_parts)

        # Truncate if too long
        if len(full_context) > max_chars:
            full_context = full_context[:max_chars].rstrip() + "\n[context truncated]"

        return full_context

    def remember_task(self, task_description: str, status: str = "pending", priority: str = "normal") -> str:
        """Remember a task in working memory."""
        task_info = f"Task: {task_description} | Status: {status} | Priority: {priority}"
        return self.add_fact(task_info, "task", ["task", status, priority])

    def remember_user_preference(self, preference: str, category: str) -> str:
        """Remember a user preference in working memory."""
        pref_info = f"User preference ({category}): {preference}"
        return self.add_fact(pref_info, "preference", ["preference", category])

    def get_memory_stats(self) -> Dict[str, Any]:
        """Get statistics from all memory systems."""
        stats = {
            "session_memory": {},
            "working_memory": {},
            "local_memory": {}
        }

        # Session memory stats
        try:
            if session_memory_exists(self.session_id):
                messages = get_session_messages(self.session_id)
                stats["session_memory"] = {
                    "session_id": self.session_id,
                    "message_count": len(messages),
                    "last_message": messages[-1]["timestamp"] if messages else None
                }
        except Exception:
            pass

        # Working memory stats
        try:
            if working_memory_exists(self.working_memory_id):
                wm_stats = get_working_memory_stats(self.working_memory_id)
                stats["working_memory"] = wm_stats
        except Exception:
            pass

        return stats


# Global memory manager instance
_global_memory_manager: Optional[MemoryManager] = None


def get_memory_manager(session_id: Optional[str] = None, working_memory_id: Optional[str] = None) -> MemoryManager:
    """Get the global memory manager instance."""
    global _global_memory_manager
    requested_session_id = session_id or DEFAULT_SESSION_ID
    requested_working_memory_id = working_memory_id or DEFAULT_WORKING_MEMORY_ID
    if (
        _global_memory_manager is None
        or _global_memory_manager.session_id != requested_session_id
        or _global_memory_manager.working_memory_id != requested_working_memory_id
    ):
        _global_memory_manager = MemoryManager(session_id, working_memory_id)
    return _global_memory_manager


def reset_memory_manager() -> None:
    """Reset the global memory manager instance."""
    global _global_memory_manager
    _global_memory_manager = None


# Convenience functions for direct access
def add_conversation_turn(user_message: str, assistant_response: str, session_id: Optional[str] = None) -> None:
    """Add a conversation turn to session memory."""
    manager = get_memory_manager(session_id=session_id)
    manager.add_conversation_turn(user_message, assistant_response)


def get_conversation_history(limit: Optional[int] = None, session_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """Get conversation history from session memory."""
    manager = get_memory_manager(session_id=session_id)
    return manager.get_conversation_history(limit)


def add_fact(content: str, fact_type: str = "fact", tags: Optional[List[str]] = None, working_memory_id: Optional[str] = None) -> str:
    """Add a fact to working memory."""
    manager = get_memory_manager(working_memory_id=working_memory_id)
    return manager.add_fact(content, fact_type, tags)


def search_memory(query: str, tags: Optional[List[str]] = None, limit: int = 10, working_memory_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """Search working memory for relevant entries."""
    manager = get_memory_manager(working_memory_id=working_memory_id)
    return manager.search_memory(query, tags, limit)


def get_comprehensive_context(query: Optional[str] = None, max_chars: int = 16000, session_id: Optional[str] = None, working_memory_id: Optional[str] = None) -> str:
    """Get comprehensive context from all memory systems."""
    manager = get_memory_manager(session_id=session_id, working_memory_id=working_memory_id)
    return manager.get_comprehensive_context(query, max_chars)


def remember_task(task_description: str, status: str = "pending", priority: str = "normal", working_memory_id: Optional[str] = None) -> str:
    """Remember a task in working memory."""
    manager = get_memory_manager(working_memory_id=working_memory_id)
    return manager.remember_task(task_description, status, priority)


def get_memory_stats(session_id: Optional[str] = None, working_memory_id: Optional[str] = None) -> Dict[str, Any]:
    """Get statistics from all memory systems."""
    manager = get_memory_manager(session_id=session_id, working_memory_id=working_memory_id)
    return manager.get_memory_stats()
