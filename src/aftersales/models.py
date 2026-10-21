"""Lazy model setup shared by the CLI and dedicated HTTP Agent."""


def get_case_reply_model():
    from core.llm import get_model
    from core.settings import settings

    # No tools are bound: this stage expresses an already confirmed case result.
    return get_model(settings.DEFAULT_MODEL).bind(
        extra_body={"thinking": {"type": "disabled"}}, timeout=30
    )


def get_bound_model(tools):
    from core.llm import get_model
    from core.settings import settings

    return get_model(settings.DEFAULT_MODEL).bind_tools(
        tools, extra_body={"thinking": {"type": "disabled"}}, timeout=30
    )
