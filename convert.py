#!/usr/bin/env python3
"""Convert microsoft/hve-core (a GitHub Copilot plugin) into a Claude Code plugin.

Usage:
  python3 convert.py build [--ref <tag|branch|sha>] [--include-experimental]

`build` fetches ./upstream at the given ref, then regenerates plugins/hve-core/:
  skills/                    converted HVE Core skills
  agents/                    converted HVE Core agents
  skills/setup/              the /hve-core:setup skill, which installs the rules below
  skills/setup/rules/        path-scoped rules converted from *.instructions.md
  skills/mcp-setup/          the /hve-core:mcp-setup skill, which adds optional MCP servers
and prints a report of everything it changed, dropped, or could not map.

Plugins cannot ship rules directly, so users run /hve-core:setup once per project
to copy them into .claude/rules/hve-core/.

Upstream files are never modified. Requires only Python 3.9+ and git.
"""

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
UPSTREAM = ROOT / "upstream"
PLUGIN_NAME = "hve-core"
PLUGIN_DIR = ROOT / "plugins" / PLUGIN_NAME
MARKETPLACE_NAME = "hve-core-claude-code"
MARKETPLACE_OWNER = "ari20089"
MARKETPLACE_REPO = "ari20089/hve-core-claude-code"
SETUP_SKILL = "setup"
# Bump whenever a converter change alters the generated plugin, so users receive it on update.
CONVERTER_REVISION = 2
RULES_DIR = PLUGIN_DIR / "skills" / SETUP_SKILL / "rules" / PLUGIN_NAME
MARKER = ".generated-by-convert"
DEFAULT_REF = "0e5623a16e06c5b1353bbf77ad4cba49db4c28b1"  # microsoft/hve-core main, 2026-11-14
REPO_URL = "https://github.com/microsoft/hve-core.git"

# Components left out on purpose (path substring -> reason).
EXCLUDE = {
    "/skills/installer/hve-core-installer": "Installs HVE Core into Copilot/VS Code; not applicable to Claude Code",
}

# Copilot tool name -> Claude Code tool names.
TOOL_MAP = {
    "read": ["Read"],
    "search": ["Grep", "Glob"],
    "edit": ["Edit", "Write", "NotebookEdit"],
    "execute": ["Bash"],
    "web": ["WebFetch", "WebSearch"],
    "agent": ["Agent"],
    "skill": ["Skill"],
    "ask_user": ["AskUserQuestion"],
    "todo": ["TaskCreate", "TaskUpdate", "TaskList"],
}

EFFORTS = {"low", "medium", "high", "xhigh", "max"}


# ---------------------------------------------------------------- frontmatter

def split_frontmatter(text):
    """Return (frontmatter_lines, body) — frontmatter_lines is None if absent."""
    if not text.startswith("---"):
        return None, text
    lines = text.split("\n")
    for i in range(1, len(lines)):
        if lines[i].rstrip() == "---":
            return lines[1:i], "\n".join(lines[i + 1:])
    return None, text


def fm_blocks(fm_lines):
    """Group frontmatter into {key: (inline_value, [continuation lines])}, preserving order."""
    blocks, key = {}, None
    for line in fm_lines:
        m = re.match(r"^([A-Za-z][\w-]*):(.*)$", line)
        if m:
            key = m.group(1)
            blocks[key] = (m.group(2).strip(), [])
        elif key is not None:
            blocks[key][1].append(line)
    return blocks


def unquote(s):
    s = s.strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "'\"":
        inner = s[1:-1]
        if s[0] == "'":
            return inner.replace("''", "'")
        try:
            return json.loads(s)
        except ValueError:
            return inner
    return s


def fm_scalar(block):
    inline, cont = block
    cont = [c for c in cont]
    if inline in ("|", "|-", "|+"):
        lines = [c.strip() for c in cont]
        return "\n".join(lines).strip()
    if inline in (">", ">-", ">+", ""):
        return " ".join(c.strip() for c in cont if c.strip())
    if cont:  # multi-line plain or quoted scalar
        return unquote(" ".join([inline] + [c.strip() for c in cont if c.strip()]))
    return unquote(inline)


def fm_list(block):
    inline, cont = block
    if inline.startswith("["):
        raw = (inline + " ".join(c.strip() for c in cont)).strip()
        raw = raw[1:raw.rindex("]")]
        return [unquote(x) for x in raw.split(",") if x.strip()]
    if inline:
        return [unquote(x) for x in inline.split(",") if x.strip()]
    return [unquote(re.sub(r"^\s*-\s*", "", c)) for c in cont if re.match(r"^\s*-\s*", c)]


def yq(s):
    """Emit a YAML-safe double-quoted scalar (JSON strings are valid YAML)."""
    return json.dumps(s, ensure_ascii=False)


# --------------------------------------------------------------------- helpers

class Report:
    def __init__(self):
        self.sections = {}

    def add(self, section, msg):
        self.sections.setdefault(section, []).append(msg)

    def print(self):
        for section, msgs in self.sections.items():
            print(f"\n## {section} ({len(msgs)})")
            for m in msgs:
                print(f"  - {m}")


def slugify(s):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def safe_reset(path):
    """Delete a generated directory, but only if this script created it."""
    if path.exists():
        if not (path / MARKER).exists():
            sys.exit(f"Refusing to delete {path}: no {MARKER} marker (not generated by convert.py).")
        shutil.rmtree(path)
    path.mkdir(parents=True)
    (path / MARKER).write_text("Generated by convert.py. Do not edit; rerun `python3 convert.py build`.\n")


def excluded(rel, include_experimental):
    if not include_experimental and "/experimental/" in "/" + rel:
        return "experimental (rerun with --include-experimental)"
    for frag, reason in EXCLUDE.items():
        if frag in "/" + rel:
            return reason
    return None


def sync_upstream(ref):
    """Fetch `ref` (tag, branch or full commit SHA) into ./upstream and check it out."""
    git = ["git", "-c", "advice.detachedHead=false", "-C", str(UPSTREAM)]
    if not (UPSTREAM / ".git").exists():
        UPSTREAM.mkdir(exist_ok=True)
        subprocess.run(git + ["init", "--quiet"], check=True)
        subprocess.run(git + ["remote", "add", "origin", REPO_URL], check=True)
    print(f"Fetching {REPO_URL} at {ref} ...")
    subprocess.run(git + ["fetch", "--quiet", "--depth", "1", "origin", ref], check=True)
    subprocess.run(git + ["checkout", "--quiet", "--force", "FETCH_HEAD"], check=True)
    if not (UPSTREAM / "plugin.json").exists():
        sys.exit(f"{ref} uses the older multi-plugin layout (no root plugin.json); "
                 "use a ref from September 2026 or later, such as main.")


# ---------------------------------------------------------------------- skills

def convert_skills(manifest, include_experimental, report):
    kept = {}
    for rel in manifest["skills"]:
        reason = excluded(rel, include_experimental)
        if reason:
            report.add("Skills skipped", f"{rel}: {reason}")
            continue
        src = UPSTREAM / rel
        fm, _ = split_frontmatter((src / "SKILL.md").read_text(encoding="utf-8"))
        name = fm_scalar(fm_blocks(fm)["name"])
        dst = PLUGIN_DIR / "skills" / name
        shutil.copytree(src, dst)
        skill_md = dst / "SKILL.md"
        text = skill_md.read_text(encoding="utf-8")
        # Claude Code substitutes $0..$9 in SKILL.md with skill arguments; escape literal ones.
        fixed = re.sub(r"\$(\d)", r"$ \1", text)
        if fixed != text:
            skill_md.write_text(fixed, encoding="utf-8")
            report.add("Skills adjusted", f"{name}: escaped literal $<digit> so Claude Code does not treat it as an argument")
        kept[name] = rel
    return kept


# ---------------------------------------------------------------------- agents

def map_model(value, report, where):
    m = re.match(r"(?i)^claude\s+(opus|sonnet|haiku|fable)\b", value)
    if m:
        return m.group(1).lower()
    report.add("Agent models dropped (agent inherits session model)", f"{where}: {value!r}")
    return None


def map_tools(tools, where, report):
    out, unmapped = [], []
    for t in tools:
        if t in TOOL_MAP:
            out += TOOL_MAP[t]
        elif "/" in t:
            server, tool = t.split("/", 1)
            out.append(f"mcp__{server}__{tool}")
        else:
            unmapped.append(t)
    if unmapped:
        report.add("Agent tools unmapped -> tool list removed (agent inherits all tools)", f"{where}: {unmapped}")
        return None
    seen, dedup = set(), []
    for t in out:
        if t not in seen:
            seen.add(t)
            dedup.append(t)
    return dedup


def convert_agents(manifest, include_experimental, skills, report):
    names = {}
    for rel in manifest["agents"]:
        reason = excluded(rel, include_experimental)
        if reason:
            report.add("Agents skipped", f"{rel}: {reason}")
            continue
        src = UPSTREAM / rel
        fm, body = split_frontmatter(src.read_text(encoding="utf-8"))
        b = fm_blocks(fm)
        slug = Path(rel).name[: -len(".agent.md")]
        display = fm_scalar(b["name"]) if "name" in b else slug
        desc = fm_scalar(b["description"])
        handled = {"name", "description", "tools", "model", "reasoning-effort", "skills",
                   "disable-model-invocation", "user-invocable"}

        desc = f"HVE Core '{display}' agent. {desc}"
        if "disable-model-invocation" in b and fm_scalar(b["disable-model-invocation"]).lower() == "true":
            desc += (f" Only use when the user explicitly asks for this agent "
                     f"(claude --agent {PLUGIN_NAME}:{slug} or @-mention); never delegate to it automatically.")

        out = [f"name: {slug}", f"description: {yq(desc)}"]

        if "tools" in b:
            tools = map_tools(fm_list(b["tools"]), rel, report)
            if tools:
                out.append(f"tools: {', '.join(tools)}")
        if "model" in b:
            model = map_model(fm_scalar(b["model"]), report, rel)
            if model:
                out.append(f"model: {model}")
        if "reasoning-effort" in b:
            effort = fm_scalar(b["reasoning-effort"]).lower()
            if effort in EFFORTS:
                out.append(f"effort: {effort}")
            else:
                report.add("Agent settings dropped", f"{rel}: reasoning-effort {effort!r}")
        if "skills" in b:
            pre = fm_list(b["skills"])
            missing = [s for s in pre if s not in skills]
            if missing:
                report.add("Agent preloaded skills missing (removed)", f"{rel}: {missing}")
            pre = [s for s in pre if s in skills]
            if pre:
                out.append("skills:")
                out += [f"  - {s}" for s in pre]
        for k in b:
            if k not in handled:
                report.add("Agent settings dropped", f"{rel}: {k}")

        (PLUGIN_DIR / "agents" / f"{slug}.md").write_text(
            "---\n" + "\n".join(out) + "\n---\n" + body, encoding="utf-8")
        names[slug] = display
    return names


# ----------------------------------------------------------------------- rules

COMPAT_RULE = """# HVE Core in Claude Code

HVE Core skills, agents and rules were written for GitHub Copilot and converted for Claude Code.
When their text uses Copilot names, use these Claude Code equivalents:

- Skill `<name>` or `.github/skills/<area>/<name>/`: the `{plugin}:<name>` skill (load it with the Skill tool).
  When telling the user to run one, write `/{plugin}:<name>` (for example `/{plugin}:rpi-plan`), not `/<name>`.
- Agent or subagent by display name (for example "Code Review Orientation"): the `{plugin}:<slug>` agent whose
  description starts with "HVE Core '<display name>' agent". Dispatch it with the Agent tool.
- `.github/instructions/<area>/<file>.instructions.md`: `.claude/rules/{plugin}/<area>/<file>.instructions.md`
  in this project (or `~/.claude/rules/{plugin}/`). Read it when a skill says to follow it.
- `ask_user`, `vscode_askQuestions`: AskUserQuestion. `runSubagent` or the `agent` tool: Agent. `todo`: the task tools.
- Copilot canvases (`hve-core-rpi-workflow`, session log) are not available. Skip canvas steps; the files under
  `.copilot-tracking/` remain the source of truth.
- Keep `.copilot-tracking/` as the working folder name so artifacts stay compatible with Copilot users.
"""


def convert_rules(manifest, include_experimental, report):
    count = 0
    for rel in manifest["rules"]:
        reason = excluded(rel, include_experimental)
        if reason:
            report.add("Rules skipped", f"{rel}: {reason}")
            continue
        src = UPSTREAM / rel
        fm, body = split_frontmatter(src.read_text(encoding="utf-8"))
        b = fm_blocks(fm or [])
        out = []
        if "description" in b:
            out.append(f"description: {yq(fm_scalar(b['description']))}")
        if "applyTo" in b:
            pats = [p.strip() for p in fm_scalar(b["applyTo"]).split(",") if p.strip()]
            if pats and pats != ["**"]:
                out.append("paths:")
                out += [f"  - {yq(p)}" for p in pats]
        else:
            report.add("Rules without applyTo (always loaded)", rel)
        dst = RULES_DIR / Path(rel).relative_to(".github/instructions")
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text("---\n" + "\n".join(out) + "\n---\n" + body, encoding="utf-8")
        count += 1
    (RULES_DIR / "hve-core-claude-code.md").write_text(COMPAT_RULE.format(plugin=PLUGIN_NAME), encoding="utf-8")
    return count


# ----------------------------------------------------------------------- build

SETUP_SKILL_MD = """---
name: setup
description: Install or update the HVE Core rules (coding standards and .copilot-tracking conventions) in this project's .claude/rules/@PLUGIN@/, or for every project on this machine with the argument "user". Run once per project after installing the @PLUGIN@ plugin, and again after each plugin update.
argument-hint: "[user]"
disable-model-invocation: true
---

# HVE Core Setup

Install the HVE Core rules that ship with this plugin. Plugins cannot load rules themselves, so this
skill copies them to where Claude Code reads them.

Arguments: $ARGUMENTS

1. Source folder: `${CLAUDE_PLUGIN_ROOT}/skills/setup/rules/@PLUGIN@`
2. Destination folder:
   * Argument is `user`: `~/.claude/rules/@PLUGIN@`
   * Otherwise: `.claude/rules/@PLUGIN@` under the project root (the git repository root when there is one,
     else the current working directory).
3. When the destination exists and has no `@MARKER@` file, it was not created by this skill. Stop and
   ask the user before changing it.
4. Otherwise replace the destination with a full copy of the source, including the `@MARKER@` file,
   so rules removed upstream do not linger. On macOS and Linux:

   run these as separate commands, not chained, so each permission prompt is easy to read:

   ```bash
   mkdir -p "<destination parent>"
   rm -rf "<destination>"        # only when it exists and has the marker file
   cp -R "<source>" "<destination>"
   ```

   On Windows use the PowerShell equivalent (`Remove-Item -Recurse -Force`, `Copy-Item -Recurse`).
5. Count the `.md` files copied and tell the user: the destination path, the count, that the rules take
   effect in the next session, and, for a project install, that committing `.claude/rules/@PLUGIN@/` shares
   them with everyone who clones the project.

Do not edit the copied files, and do not change anything else in the project.
"""

MCP_SETUP_SKILL = "mcp-setup"
MCP_SETUP_SKILL_MD = """---
name: mcp-setup
description: Add the MCP servers some HVE Core agents and skills need (GitHub, Azure DevOps, WorkIQ, Microsoft Learn docs, Figma) to Claude Code. Opt-in; asks which servers to add. Pass "project" to write a shareable .mcp.json instead of your user config.
argument-hint: "[github] [ado] [workiq] [microsoft-docs] [figma] [project]"
disable-model-invocation: true
---

# HVE Core MCP Setup

Add only the MCP servers the user chooses. Everything in HVE Core that does not use these servers works
without them.

Arguments: $ARGUMENTS

## Servers

The names must be exactly these: the agents' tool lists use them (`mcp__github__...`, `mcp__ado__...`).

| Name | What it reaches | Used by | Sign-in |
| --- | --- | --- | --- |
| `github` | GitHub issues, pull requests, repos | backlog-manager, github-backlog-executor, functional-planner, accessibility-planner, engagement-report-generator | Personal access token in the `GITHUB_PAT` environment variable |
| `ado` | Azure DevOps work items, repos, pipelines, wikis, test plans | backlog-manager, ado-backlog-executor, functional-planner, accessibility-planner, engagement-report-generator | Microsoft account in the browser; needs the organization name |
| `workiq` | Microsoft 365 meetings, email, documents | engagement-report-generator, meeting analysis, rai-planner | Work Microsoft 365 account; the tenant admin must enable Work IQ |
| `microsoft-docs` | Microsoft Learn documentation | rpi-researcher, functional-planner, network-isa95-planner, incident-response | None |
| `figma` | Figma files | dt-figma-export | Figma account in the browser |

The `outlook-drafter` agent uses a separate WorkIQ Mail server that HVE Core does not document; this skill
does not set it up.

## Steps

1. **Choose servers.** Use the servers named in the arguments. With none named, show the table above and ask
   the user which to add (multi-select). Ask for the Azure DevOps organization name when `ado` is chosen: the
   part after `dev.azure.com/`. It is not a secret.
2. **Choose scope.** Default `user` (all of this user's projects). With the argument `project`, use
   `project`: Claude Code writes `.mcp.json` in the project root, which the team can commit. It holds no
   secrets, because the GitHub token stays in an environment variable.
3. **Find the Claude Code CLI.** Use `claude` when `command -v claude` finds it. Otherwise use the newest
   `~/.vscode/extensions/anthropic.claude-code-*/resources/native-binary/claude`. When neither exists, show
   the user the commands from step 5 to run themselves, and stop.
4. **Check for existing servers.** Run `claude mcp get <name>` for each chosen server. If one exists, show its
   configuration and ask before replacing it (`claude mcp remove --scope <scope> <name>` first).
5. **Add each server** with `claude mcp add-json --scope <scope> <name> '<json>'`, one command per server:
   * `github`: `{"type":"http","url":"https://api.githubcopilot.com/mcp","headers":{"Authorization":"Bearer ${GITHUB_PAT}"}}`
   * `ado`: `{"type":"stdio","command":"npx","args":["-y","@azure-devops/mcp","<organization>"]}`
   * `workiq`: `{"type":"stdio","command":"npx","args":["-y","@microsoft/workiq@1.0.0","mcp"]}`
   * `microsoft-docs`: `{"type":"http","url":"https://learn.microsoft.com/api/mcp"}`
   * `figma`: `{"type":"http","url":"https://mcp.figma.com/mcp"}`

   Write `${GITHUB_PAT}` literally: Claude Code reads the variable when it connects.
6. **Never ask for, accept, or write a token in the conversation or in any file.** If the user pastes one,
   tell them to revoke it on GitHub and create a new one.
7. **Verify** with `claude mcp list` and report each chosen server's status.
8. **Tell the user what is left**, only for the servers added:
   * `github`: create a token at https://github.com/settings/personal-access-tokens (fine-grained: the
     repositories to use, with Issues and Pull requests read/write and Contents read; or a classic token with
     `repo`). Add `export GITHUB_PAT=<token>` to their shell profile (`~/.zshrc` on macOS), then restart the
     terminal or VS Code. Until then GitHub shows "Missing environment variables: GITHUB_PAT".
   * `ado`: the first Azure DevOps call opens a browser sign-in. `npx` downloads the package on first start,
     which can take a minute.
   * `workiq`: an AADSTS650052 error means the organization has not enabled Work IQ; only a Microsoft 365
     admin can fix it. Microsoft's license terms are accepted on the first query.
   * `figma`: run `/mcp`, choose `figma`, and sign in.
   * All: start a new Claude Code session (or reload the VS Code window) to load the servers.

Change nothing else in the user's configuration.
"""

PLUGIN_README = """# HVE Core for Claude Code

Generated by `convert.py` from [microsoft/hve-core](https://github.com/microsoft/hve-core)
version {version} (commit `{commit}`). Do not edit by hand; rerun the converter instead.

HVE Core is Microsoft's agentic SDLC framework: Research, Plan, Implement, Review (RPI), code review,
security, privacy, accessibility, responsible AI, project planning, design thinking and more.
This plugin carries {skills} skills and {agents} agents.

Install from the repository that holds this marketplace:

```text
claude plugin marketplace add {repo}
claude plugin install {plugin}@{marketplace}
```

Then run `/{plugin}:setup` once in each project. It copies HVE Core's coding standards and
`.copilot-tracking` conventions into `.claude/rules/{plugin}/` (plugins cannot carry rules themselves).

Ten agents also use GitHub, Azure DevOps or WorkIQ (Microsoft 365) through MCP servers. Run
`/{plugin}:mcp-setup` to add the ones you need; everything else works without them.

Skills run as `/{plugin}:<name>`, for example `/{plugin}:rpi-research` or `/{plugin}:git-commit`.
Agents run as subagents, or as the main session with `claude --agent {plugin}:<name>`.

| Agent | HVE Core name |
| --- | --- |
{agent_list}

License: MIT (see LICENSE and THIRD-PARTY-NOTICES). Not an official Microsoft or Anthropic distribution.
"""

def build(args):
    sync_upstream(args.ref)
    manifest = json.loads((UPSTREAM / "plugin.json").read_text(encoding="utf-8"))
    report = Report()

    safe_reset(PLUGIN_DIR)
    safe_reset(RULES_DIR)
    (PLUGIN_DIR / "agents").mkdir()

    skills = convert_skills(manifest, args.include_experimental, report)
    agents = convert_agents(manifest, args.include_experimental, skills, report)
    rules = convert_rules(manifest, args.include_experimental, report)
    for own in (SETUP_SKILL, MCP_SETUP_SKILL):
        if own in skills:
            sys.exit(f"Upstream now has a skill named {own!r}; rename it in convert.py.")
    (PLUGIN_DIR / "skills" / SETUP_SKILL / "SKILL.md").write_text(
        SETUP_SKILL_MD.replace("@PLUGIN@", PLUGIN_NAME).replace("@MARKER@", MARKER), encoding="utf-8")
    (PLUGIN_DIR / "skills" / MCP_SETUP_SKILL).mkdir()
    (PLUGIN_DIR / "skills" / MCP_SETUP_SKILL / "SKILL.md").write_text(MCP_SETUP_SKILL_MD, encoding="utf-8")
    commit = subprocess.run(["git", "-C", str(UPSTREAM), "rev-parse", "--short", "HEAD"],
                            capture_output=True, text=True).stdout.strip()
    # Upstream keeps one version across many commits, so the version carries the upstream commit and this
    # converter's revision: every rebuild that changes the output reaches users when they update.
    version = f"{manifest['version']}+{commit}.{CONVERTER_REVISION}"

    shutil.copy(UPSTREAM / "LICENSE", PLUGIN_DIR / "LICENSE")
    if (UPSTREAM / "THIRD-PARTY-NOTICES").exists():
        shutil.copy(UPSTREAM / "THIRD-PARTY-NOTICES", PLUGIN_DIR / "THIRD-PARTY-NOTICES")
    plugin_json = {
        "name": PLUGIN_NAME,
        "displayName": "HVE Core",
        "version": version,
        "description": f"HVE Core {manifest['version']} (microsoft/hve-core) converted for Claude Code: "
                       "agentic SDLC skills and agents (RPI, code review, security, planning, and more).",
        "author": {"name": "Microsoft (converted locally)", "url": "https://github.com/microsoft/hve-core"},
        "homepage": "https://github.com/microsoft/hve-core",
        "repository": "https://github.com/microsoft/hve-core",
        "license": "MIT",
        "keywords": ["hve", "hve-core", "rpi", "sdlc", "agents", "skills"],
    }
    (PLUGIN_DIR / ".claude-plugin").mkdir()
    (PLUGIN_DIR / ".claude-plugin" / "plugin.json").write_text(json.dumps(plugin_json, indent=2) + "\n")

    marketplace = {
        "name": MARKETPLACE_NAME,
        "owner": {"name": MARKETPLACE_OWNER},
        "metadata": {"description": "HVE Core (microsoft/hve-core) converted for Claude Code. Unofficial."},
        "plugins": [{"name": PLUGIN_NAME, "source": f"./plugins/{PLUGIN_NAME}",
                     "description": plugin_json["description"]}],
    }
    (ROOT / ".claude-plugin").mkdir(exist_ok=True)
    (ROOT / ".claude-plugin" / "marketplace.json").write_text(json.dumps(marketplace, indent=2) + "\n")

    (PLUGIN_DIR / "README.md").write_text(PLUGIN_README.format(
        version=manifest["version"], commit=commit, skills=len(skills) + 2, agents=len(agents), plugin=PLUGIN_NAME,
        marketplace=MARKETPLACE_NAME, repo=MARKETPLACE_REPO,
        agent_list="\n".join(f"| `{PLUGIN_NAME}:{s}` | {d} |" for s, d in sorted(agents.items())),
    ), encoding="utf-8")
    report.print()
    print(f"\nBuilt {PLUGIN_NAME} {version} from {args.ref} ({commit}): "
          f"{len(skills)} skills (+2 setup skills), {len(agents)} agents, {rules} rules (+1 compatibility rule).")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="convert upstream into plugins/hve-core/")
    b.add_argument("--ref", default=DEFAULT_REF, help=f"upstream git tag (default {DEFAULT_REF})")
    b.add_argument("--include-experimental", action="store_true", help="also convert experimental skills/agents/rules")
    build(p.parse_args())


if __name__ == "__main__":
    main()
