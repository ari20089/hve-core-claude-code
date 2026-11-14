---
name: setup
description: Install or update the HVE Core rules (coding standards and .copilot-tracking conventions) in this project's .claude/rules/hve-core/, or for every project on this machine with the argument "user". Run once per project after installing the hve-core plugin, and again after each plugin update.
argument-hint: "[user]"
disable-model-invocation: true
---

# HVE Core Setup

Install the HVE Core rules that ship with this plugin. Plugins cannot load rules themselves, so this
skill copies them to where Claude Code reads them.

Arguments: $ARGUMENTS

1. Source folder: `${CLAUDE_PLUGIN_ROOT}/skills/setup/rules/hve-core`
2. Destination folder:
   * Argument is `user`: `~/.claude/rules/hve-core`
   * Otherwise: `.claude/rules/hve-core` under the project root (the git repository root when there is one,
     else the current working directory).
3. When the destination exists and has no `.generated-by-convert` file, it was not created by this skill. Stop and
   ask the user before changing it.
4. Otherwise replace the destination with a full copy of the source, including the `.generated-by-convert` file,
   so rules removed upstream do not linger. On macOS and Linux:

   run these as separate commands, not chained, so each permission prompt is easy to read:

   ```bash
   mkdir -p "<destination parent>"
   rm -rf "<destination>"        # only when it exists and has the marker file
   cp -R "<source>" "<destination>"
   ```

   On Windows use the PowerShell equivalent (`Remove-Item -Recurse -Force`, `Copy-Item -Recurse`).
5. Count the `.md` files copied and tell the user: the destination path, the count, that the rules take
   effect in the next session, and, for a project install, that committing `.claude/rules/hve-core/` shares
   them with everyone who clones the project.

Do not edit the copied files, and do not change anything else in the project.
