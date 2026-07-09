"""
tool_implementations.py

Thin re-export shim. The actual tool implementations now live in the
``src.tools`` package, split by domain (documents, chats, skills_tasks, admin,
web, notes_calendar, cookbook, media, research_contacts, vault) plus the
shared ``_common`` and ``_state`` modules.

This module re-exports every public tool function (``do_*``), the active-doc
state setters/getters, the block parsers, and the handful of private names
that external callers and tests import by name — so that
``from src.tool_implementations import X`` keeps working unchanged, and
``mock.patch("src.tool_implementations.X")`` / text greps still resolve.
"""

from src.tools._common import (  # noqa: F401
    MAX_OUTPUT_CHARS,
    MAX_READ_CHARS,
    get_mcp_manager,
    _truncate,
    _parse_tool_args,
    _internal_headers,
)
from src.tools._state import (  # noqa: F401
    set_active_document,
    set_active_model,
    get_active_document,
    clear_active_document,
)
from src.tools.documents import (  # noqa: F401
    _owned_document_query,
    _get_owned_document,
    _most_recent_owned_document,
    _sniff_doc_language,
    _looks_like_email_document,
    _coerce_email_document_content,
    do_create_document,
    do_update_document,
    parse_edit_blocks,
    do_edit_document,
    parse_suggest_blocks,
    do_suggest_document,
    do_manage_documents,
)
from src.tools.chats import do_search_chats  # noqa: F401
from src.tools.skills_tasks import (  # noqa: F401
    do_manage_skills,
    _skill_dump,
    do_manage_tasks,
)
from src.tools.admin import (  # noqa: F401
    do_manage_endpoints,
    do_manage_mcp,
    do_manage_webhooks,
    do_manage_tokens,
    do_manage_settings,
)
from src.tools.web import (  # noqa: F401
    do_api_call,
    do_browser,
    do_app_api,
)
from src.tools.notes_calendar import (  # noqa: F401
    do_manage_notes,
    do_manage_calendar,
)
from src.tools.cookbook import (  # noqa: F401
    do_download_model,
    do_serve_model,
    do_list_served_models,
    do_stop_served_model,
    do_list_downloads,
    do_cancel_download,
    do_search_hf_models,
    do_adopt_served_model,
    do_list_cookbook_servers,
    do_list_serve_presets,
    do_serve_preset,
    do_list_cached_models,
)
from src.tools.media import do_edit_image  # noqa: F401
from src.tools.research_contacts import (  # noqa: F401
    do_manage_research,
    do_trigger_research,
    do_resolve_contact,
    do_manage_contact,
)
from src.tools.vault import (  # noqa: F401
    _run_bw,
    do_vault_search,
    do_vault_get,
    do_vault_unlock,
)
