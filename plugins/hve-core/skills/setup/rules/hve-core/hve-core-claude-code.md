# HVE Core in Claude Code

HVE Core skills, agents and rules were written for GitHub Copilot and converted for Claude Code.
When their text uses Copilot names, use these Claude Code equivalents:

- Skill `<name>` or `.github/skills/<area>/<name>/`: the `hve-core:<name>` skill (load it with the Skill tool).
  When telling the user to run one, write `/hve-core:<name>` (for example `/hve-core:rpi-plan`), not `/<name>`.
- Agent or subagent by display name (for example "Code Review Orientation"): the `hve-core:<slug>` agent whose
  description starts with "HVE Core '<display name>' agent". Dispatch it with the Agent tool.
- `.github/instructions/<area>/<file>.instructions.md`: `.claude/rules/hve-core/<area>/<file>.instructions.md`
  in this project (or `~/.claude/rules/hve-core/`). Read it when a skill says to follow it.
- `ask_user`, `vscode_askQuestions`: AskUserQuestion. `runSubagent` or the `agent` tool: Agent. `todo`: the task tools.
- Copilot canvases (`hve-core-rpi-workflow`, session log) are not available. Skip canvas steps; the files under
  `.copilot-tracking/` remain the source of truth.
- Keep `.copilot-tracking/` as the working folder name so artifacts stay compatible with Copilot users.
