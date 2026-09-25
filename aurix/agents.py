"""Agent profiles and the request router.

Each agent = a system prompt + the tools it may use + a preferred model tier.
"team" is not a single agent: it runs the Phase 4 manager -> specialists ->
reviewer pipeline (see engine.team_run). Every routing decision is logged so a
trained router (e.g. Laya) can replace these keyword rules later."""
import re
from dataclasses import dataclass

MEMORY = ["remember", "recall", "search_documents"]


@dataclass
class Agent:
    name: str
    label: str
    tools: list[str]
    model_tier: str  # "fast", "strong" or "code"
    role: str


AGENTS: dict[str, Agent] = {a.name: a for a in [
    Agent("general", "General", ["calculator", "get_time", "list_files", "read_file", "web_search",
                                 "fetch_url", "open_application", *MEMORY, "forget"], "fast",
          "You handle everyday questions, explanations, planning and conversation."),
    Agent("researcher", "Researcher", ["web_search", "fetch_url", "browser_open", "browser_read",
                                       "read_file", "write_file", "calculator", "get_time",
                                       *MEMORY, "add_document"], "strong",
          "You research topics. Search, open the most relevant pages, compare sources and report "
          "findings with the URLs you used. Check the user's documents with search_documents first "
          "when the question may be about their own material."),
    Agent("coder", "Developer", ["list_files", "read_file", "write_file", "run_python", "run_shell",
                                 "git", "open_path", "calculator", *MEMORY], "code",
          "You write, run, test and debug code. Work in small steps: write the file, run it, read "
          "the error, fix it. Use git to commit working states when the user has a repository."),
    Agent("analyst", "Data analyst", ["list_files", "read_file", "write_file", "run_python",
                                      "calculator", *MEMORY], "strong",
          "You analyse data files (CSV, Excel, JSON) using Python with pandas and numpy, save "
          "charts or results to the workspace, and explain what the numbers show."),
    Agent("operator", "Operator", ["open_application", "open_url", "open_path", "run_applescript",
                                   "browser_open", "browser_read", "browser_click", "browser_fill",
                                   "browser_screenshot", "browser_close", "n8n_list_workflows",
                                   "n8n_run_webhook", "n8n_create_workflow", "list_files", "read_file", "write_file",
                                   *MEMORY], "strong",
          "You operate the computer for the user: open apps and files, control Mac apps with "
          "AppleScript, drive the automation browser step by step (open, read, then click or fill), "
          "and run the user's n8n workflows. Never submit forms, send messages or buy anything "
          "unless the user explicitly asked for exactly that."),
]}

TEAM_LABEL = "Team (plan, build, review)"

_RULES = [
    ("operator", r"\b(open (the )?app|launch|applescript|notes app|spotify|music app|browser|website|"
                 r"click|fill (in|out)|log ?in|n8n|workflow|automate)\b"),
    ("coder", r"\b(code|bug|error|traceback|function|script|python|javascript|typescript|sql|"
              r"api|fastapi|nestjs|debug|refactor|compile|git|regex|class|exception)\b|```"),
    ("analyst", r"\b(csv|excel|xlsx|dataset|data ?frame|pandas|statistic|average|mean|median|"
                r"correlation|chart|plot|analy[sz]e)\b"),
    ("researcher", r"\b(research|latest|news|current|today|compare|sources?|find out|"
                   r"look up|search)\b"),
]


def route(message: str, web_enabled: bool) -> tuple[str, str]:
    """Return (agent_name, reason)."""
    text = message.lower()
    for agent, pattern in _RULES:
        m = re.search(pattern, text)
        if m:
            if agent == "researcher" and not web_enabled:
                return "general", f"research wording ('{m.group(0).strip()}') but web is off"
            return agent, f"matched '{m.group(0).strip()[:20]}'"
    return "general", "no specialist keywords"
