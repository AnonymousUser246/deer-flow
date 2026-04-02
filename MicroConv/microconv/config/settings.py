"""Configuration helpers — thin wrappers that can use DeerFlow or standalone LangChain."""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from langchain_core.language_models import BaseChatModel
    from langchain_core.tools import BaseTool

logger = logging.getLogger(__name__)

_USE_DEERFLOW = os.environ.get("MICROCONV_USE_DEERFLOW", "false").lower() == "true"


def get_model(name: str | None = None, **kwargs) -> BaseChatModel:
    """Return a chat model instance.

    When MICROCONV_USE_DEERFLOW=true, delegates to DeerFlow's model factory.
    Otherwise falls back to langchain_openai.ChatOpenAI with OPENAI_API_KEY.
    """
    if _USE_DEERFLOW:
        from deerflow.models import create_chat_model
        return create_chat_model(name=name, **kwargs)

    from langchain_openai import ChatOpenAI

    model_name = name or os.environ.get("OPENAI_MODEL", "gpt-4o")
    return ChatOpenAI(model=model_name, temperature=0, **kwargs)


def get_tools() -> list[BaseTool]:
    """Return the standard set of sandbox tools for file operations.

    When MICROCONV_USE_DEERFLOW=true, returns DeerFlow sandbox tools.
    Otherwise returns lightweight shims based on subprocess / pathlib.
    """
    if _USE_DEERFLOW:
        from deerflow.sandbox.tools import bash_tool, ls_tool, read_file_tool, str_replace_tool, write_file_tool
        return [bash_tool, ls_tool, read_file_tool, write_file_tool, str_replace_tool]

    return _build_standalone_tools()


def _build_standalone_tools() -> list[BaseTool]:
    """Minimal file-operation tools that work without DeerFlow."""
    import subprocess
    from pathlib import Path

    from langchain_core.tools import tool

    @tool
    def bash(command: str) -> str:
        """Execute a bash command and return stdout+stderr."""
        try:
            result = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=120)
            output = result.stdout + result.stderr
            return output[:20000] if len(output) > 20000 else output
        except subprocess.TimeoutExpired:
            return "Error: command timed out after 120 seconds"
        except Exception as e:
            return f"Error: {e}"

    @tool
    def ls(path: str) -> str:
        """List directory contents up to 2 levels deep."""
        try:
            result = subprocess.run(f"find {path} -maxdepth 2 -print | head -200", shell=True, capture_output=True, text=True, timeout=10)
            return result.stdout or "(empty)"
        except Exception as e:
            return f"Error: {e}"

    @tool
    def read_file(path: str) -> str:
        """Read a text file and return its contents."""
        try:
            content = Path(path).read_text(encoding="utf-8")
            return content[:50000] if len(content) > 50000 else content
        except Exception as e:
            return f"Error: {e}"

    @tool
    def write_file(path: str, content: str) -> str:
        """Write content to a file, creating parent directories as needed."""
        try:
            p = Path(path)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
            return "OK"
        except Exception as e:
            return f"Error: {e}"

    @tool
    def str_replace(path: str, old_str: str, new_str: str) -> str:
        """Replace a substring in a file. The old_str must appear exactly once."""
        try:
            p = Path(path)
            content = p.read_text(encoding="utf-8")
            if old_str not in content:
                return f"Error: string not found in {path}"
            content = content.replace(old_str, new_str, 1)
            p.write_text(content, encoding="utf-8")
            return "OK"
        except Exception as e:
            return f"Error: {e}"

    return [bash, ls, read_file, write_file, str_replace]
