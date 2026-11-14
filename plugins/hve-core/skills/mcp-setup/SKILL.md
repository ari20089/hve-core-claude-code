---
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
