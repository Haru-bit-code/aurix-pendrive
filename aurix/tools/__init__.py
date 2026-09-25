"""Tool registry. Each tool = OpenAI function schema + a Python callable."""
from dataclasses import dataclass
from typing import Callable

from . import basic, browser, computer, files, knowledge, n8n, system, web


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict
    func: Callable[..., str]
    needs_web: bool = False
    platform: str | None = None   # "Darwin" = macOS only

    def schema(self) -> dict:
        return {"type": "function", "function": {
            "name": self.name, "description": self.description, "parameters": self.parameters}}


def _p(props: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": props, "required": required}


S = {"type": "string"}

TOOLS: dict[str, Tool] = {t.name: t for t in [
    # basics
    Tool("calculator", "Evaluate a math expression exactly, e.g. '2**10 / 3' or 'sqrt(2)*pi'.",
         _p({"expression": S}, ["expression"]), basic.calculator),
    Tool("get_time", "Get the current local date and time.", _p({}, []), basic.get_time),
    # files
    Tool("list_files", "List files and folders in the workspace (or a subfolder of it).",
         _p({"path": {"type": "string", "description": "Folder inside the workspace, default '.'"}}, []),
         files.list_files),
    Tool("read_file", "Read a text file from the workspace.", _p({"path": S}, ["path"]), files.read_file),
    Tool("write_file", "Create or overwrite a text file in the workspace.",
         _p({"path": S, "content": S}, ["path", "content"]), files.write_file),
    # web
    Tool("web_search", "Search the web. Returns titles, URLs and snippets.",
         _p({"query": S}, ["query"]), web.web_search, needs_web=True),
    Tool("fetch_url", "Download a web page and return its readable text.",
         _p({"url": S}, ["url"]), web.fetch_url, needs_web=True),
    # code + system
    Tool("run_python", "Run a Python script in the workspace folder and return its output. pandas and numpy are available.",
         _p({"code": S}, ["code"]), system.run_python),
    Tool("run_shell", "Run a shell command in the workspace folder and return its output.",
         _p({"command": S}, ["command"]), system.run_shell),
    Tool("git", "Run a git command in a workspace folder, e.g. args='status' or args='commit -m \"msg\"'.",
         _p({"args": S, "repo": {"type": "string", "description": "Folder inside the workspace, default '.'"}}, ["args"]),
         computer.git),
    # computer control
    Tool("open_application", "Open an application on this computer by name, e.g. 'Visual Studio Code'.",
         _p({"name": S}, ["name"]), system.open_application),
    Tool("open_url", "Open a web link in the user's normal browser.", _p({"url": S}, ["url"]), computer.open_url),
    Tool("open_path", "Open a workspace file or folder in its default app.", _p({"path": S}, ["path"]), computer.open_path),
    Tool("run_applescript", "Run AppleScript to control Mac apps, e.g. create a note in Notes or play music.",
         _p({"script": S}, ["script"]), computer.run_applescript, platform="Darwin"),
    # browser automation
    Tool("browser_open", "Open a web page in AURIX's automation browser and return its text.",
         _p({"url": S}, ["url"]), browser.browser_open, needs_web=True),
    Tool("browser_read", "Read the text of the page currently open in the automation browser.",
         _p({}, []), browser.browser_read, needs_web=True),
    Tool("browser_click", "Click a link or button by its visible text in the automation browser.",
         _p({"text": S}, ["text"]), browser.browser_click, needs_web=True),
    Tool("browser_fill", "Type a value into a form field (found by label, placeholder or name).",
         _p({"field": S, "value": S}, ["field", "value"]), browser.browser_fill, needs_web=True),
    Tool("browser_screenshot", "Save a screenshot of the automation browser to the workspace.",
         _p({"name": S}, []), browser.browser_screenshot, needs_web=True),
    Tool("browser_close", "Close the automation browser.", _p({}, []), browser.browser_close),
    # knowledge
    Tool("remember", "Save a lasting fact about the user or their projects to long-term memory.",
         _p({"fact": S}, ["fact"]), knowledge.remember),
    Tool("recall", "Search long-term memory for facts about the user or their projects.",
         _p({"query": S}, ["query"]), knowledge.recall),
    Tool("forget", "Delete a long-term memory by its id.",
         _p({"fact_id": {"type": "integer"}}, ["fact_id"]), knowledge.forget),
    Tool("search_documents", "Search the user's knowledge base documents and return matching passages.",
         _p({"query": S}, ["query"]), knowledge.search_documents),
    Tool("add_document", "Add a workspace file (text, code, Markdown, PDF, DOCX) to the knowledge base.",
         _p({"path": S}, ["path"]), knowledge.add_document),
    # automation
    Tool("n8n_list_workflows", "List the user's n8n workflows.", _p({}, []), n8n.n8n_list_workflows, needs_web=False),
    Tool("n8n_run_webhook", "Run an n8n workflow through its webhook path, sending JSON data.",
         _p({"path": S, "data": {"type": "object"}}, ["path"]), n8n.n8n_run_webhook),
    Tool("n8n_create_workflow", "Create a simple n8n workflow: a webhook that responds with fixed text. "
         "For a quick connectivity test or a starting point to extend by hand. Not for complex workflows.",
         _p({"name": S, "webhook_path": S, "response_text": S,
             "activate": {"type": "boolean", "description": "Turn the workflow on immediately (default true)"}},
            ["name", "webhook_path", "response_text"]), n8n.n8n_create_workflow),
]}
