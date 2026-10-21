# CLAUDE.md

This repo's full agent guidance lives in **[AGENTS.md](AGENTS.md)** -
read that first, it applies to Claude the same as any other agent.

This file only adds the Claude-Code-specific bits.

## Skill

A packaged skill lives at `.claude/skills/appsporter/SKILL.md`. Claude Code
should load it before running `appsporter.sh` or touching `scripts/*.sh` in
this repo - it has the condensed command reference and the Settings/
SystemUI/overlay deny-scope rule in one place so you don't have to re-derive
it from `AGENTS.md` every time.

## Non-negotiables, restated

* No network calls during porting. Everything under `tools/`, `keys/`,
  `apc/patches/` is already local - search there before anything else.
* Never port or patch Settings, SystemUI, or overlay (RRO) apps -
  `GUARD_SCOPE` in `scripts/guard_scope.sh` refuses these automatically for
  `--inject-logger` and `--scan-checks`; apply the same refusal yourself
  for the manual (`build_port.sh`) path too.
* Run `./appsporter.sh --scan-checks <dir>` before hand-reading smali for
  Knox/signature/compatibility checks - it's built to pinpoint exact
  file:line:method so you don't have to parse the whole tree and burn
  context/tokens on it.
* Prefer editing/extending `scripts/*.sh` over writing one-off inline
  bash/smali in a chat response - the next session (human or agent) should
  be able to reuse it via `appsporter.sh`.
