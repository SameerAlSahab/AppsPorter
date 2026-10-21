# AGENTS.md

Instructions for any AI coding agent (Claude, Kimi, ChatGPT/Codex, etc.)
working in this repository. This file is the source of truth for agent
behavior here; `CLAUDE.md` is a short pointer to it for Claude Code
specifically.

## What this repo is

`AppsPorter` is a bash-driven tool that ports One UI / OEM-stock Android
apps onto other ROMs, stock firmware, and custom devices by:

1. Decompiling the target APK and the OEM's framework `.jar`s with `apktool`.
2. Copying over any OEM-framework smali classes the app references but the
   target ROM is missing.
3. Injecting OEM native libraries (`.so`) and any hand-written smali
   modifications (`mods/`).
4. Rebuilding and signing the APK with the local AOSP test keys.

The manual workflow (`build_port.sh <apk> <patch_name>`) already does this
end to end and works without any AI involvement. The AI-agent layer adds
diagnostics and automated smali patching **on top of** that, it does not
replace it.

## Hard constraints - read before doing anything

* **Zero network access for the porting pipeline.** All required
  `apktool.jar` / `signapk.jar` tools (`tools/`) and signing keys
  (`keys/aosp/`) already exist in the repo. Never suggest downloading a
  jar, a key, or "the latest apktool" - if something seems missing, it is
  more likely referenced by the wrong path than actually absent. Check
  `tools/`, `keys/`, and `apc/patches/<oem>/` before concluding a file is
  missing.
* **Never touch Settings, SystemUI, or RRO/overlay apps.** These require
  `android.uid.system`, platform-signature permissions, or an overlay
  target this tool cannot reproduce. `scripts/guard_scope.sh`
  (`GUARD_SCOPE`) enforces this for the automated logger/scanner tooling -
  do not bypass it, and apply the same judgment manually: if a package name
  or manifest looks like Settings/SystemUI/an RRO, say so and stop rather
  than attempting a port.
* **Don't blind-parse whole smali trees yourself.** `scripts/scan_checks.sh`
  already greps and classifies known check patterns with file:line:method
  precision. Run it first and reason over its (small) output instead of
  reading every `.smali` file - this keeps agent context/token usage low on
  what can be a tree of thousands of files.
* **All scripts are POSIX/bash, sourced, not re-implemented.** Functions
  like `LOG`, `LOGE`, `LOGW`, `ABORT`, `DECOMPILE`, `BUILD`, `SIGN_APK`,
  `GUARD_SCOPE`, `INJECT_LOGGER`, `SCAN_CHECKS` already exist in
  `scripts/*.sh`. Reuse them; don't re-derive the same logic inline in a
  one-off command.

## Entry points

| Workflow | Command |
|---|---|
| Manual (human-run) full port | `bash build_port.sh <apk> <patch_name>` |
| AI-agent: identify an APK's OEM | `./appsporter.sh --identify <apk>` |
| AI-agent: decompile | `./appsporter.sh --decompile <apk\|jar> [out_dir]` |
| AI-agent: recompile | `./appsporter.sh --recompile <folder> [out.apk]` |
| AI-agent: sign | `./appsporter.sh --sign <in.apk> [out.apk]` |
| AI-agent: inject crash/log capture | `./appsporter.sh --inject-logger <decompiled_folder>` |
| AI-agent: scan for checks/locks | `./appsporter.sh --scan-checks <decompiled_folder> [--apply]` |

`appsporter.sh --help` prints the same table.

## Typical agent-assisted debugging loop

1. User provides a source APK link, or an already-downloaded `.apk`.
2. `./appsporter.sh --identify <apk>` - read the package name, compare
   against `apc/patches/*` to tell the user which OEM/patch this looks
   like, or that none exists yet.
3. `./appsporter.sh --decompile <apk> out/decompiled` (or let
   `build_port.sh` do the full manual port first if the user wants the
   real porting pipeline, not just diagnostics).
4. `./appsporter.sh --scan-checks out/decompiled` - review the reported
   Knox/signature/compatibility checks. Re-run with `--apply` only for the
   boolean-returning cases it flags as auto-patchable; everything else
   needs a human/agent decision on a case-by-case basis, since blindly
   forcing a non-boolean check to "pass" can silently corrupt app state.
5. `./appsporter.sh --inject-logger out/decompiled` - adds a no-root crash
   logger so the *next* debugging session has real runtime data instead of
   guesses. Refused automatically for Settings/SystemUI/overlay targets.
6. `./appsporter.sh --recompile out/decompiled out/app.apk` then
   `./appsporter.sh --sign out/app.apk`.
7. If a `NoClassDefFoundError` / `ClassNotFoundException` / missing `.so`
   shows up in the logs the user pastes back, check whether the class/lib
   exists in `apc/patches/<oem>/framework` or `lib*`/`lib64` first (it is
   local, zero extra tokens to fetch) before telling the user it needs a
   firmware dump or additional APK.

## Logs for the next AI session

`--inject-logger` writes crash reports and a running app log to
`/data/data/<package>/files/appsporter_logs/` on-device, readable without
root or ADB via any file manager with "show app data" (or the app's own
in-app export, if it has one). Ask the user to paste the contents of that
folder (or `app_log.txt` / the latest `crash_*.txt`) back into the next
session rather than asking them to pull logs over ADB.

## Where things live

```
appsporter.sh          AI-agent CLI (this file's companion)
build_port.sh           Manual porting entry point
scripts/
  apktool.sh             DECOMPILE / BUILD / SIGN_APK / INSTALL_FRAMEWORK
  check_dependencies.sh  CHECK_DEPS (java, xmlstarlet)
  guard_scope.sh          GUARD_SCOPE - Settings/SystemUI/overlay deny-list
  inject_logger.sh        INJECT_LOGGER - no-root crash/log capture
  logging.sh              LOG / LOGE / LOGW / ABORT
  make_apk.sh             The actual manual port pipeline (sourced by build_port.sh)
  scan_checks.sh           SCAN_CHECKS - check/lock scanner + safe auto-patch
  xml_utils.sh             AndroidManifest.xml editing helpers
tools/apktool/apktool.jar  Local, no download needed
tools/signapk/signapk.jar  Local, no download needed
keys/aosp/                 Local AOSP test signing keys
apc/patches/<oem>/         framework/, lib/, lib64/, mods/, scripts/ per OEM
```

See `CONTRIBUTING.md` for bash/smali conventions and `.claude/skills/appsporter/SKILL.md`
for the condensed, load-on-demand version of this file for Claude Code.
