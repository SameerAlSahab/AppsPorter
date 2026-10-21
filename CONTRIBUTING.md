# Contributing to AppsPorter

Thanks for taking the time to contribute. This covers the project's scope,
bash style for `scripts/`, smali patch conventions for `apc/patches/`, and
how AI-agent contributions should be reviewed.

## Scope - please read before opening a PR

AppsPorter ports OEM-stock apps that don't need system-level privileges.

**Out of scope, by design, not oversight:**

* Settings apps (`com.android.settings*` and OEM equivalents)
* SystemUI apps (`com.android.systemui*` and OEM equivalents)
* Runtime Resource Overlays (`<overlay>` targets)
* Anything requesting `android:sharedUserId="android.uid.system"`

These need platform-signature permissions or overlay targets this tool
cannot reproduce on a non-matching ROM/device, no matter how much smali
patching is applied. `scripts/guard_scope.sh` enforces this for the
AI-assisted tooling (`--inject-logger`, `--scan-checks`); PRs that try to
relax or bypass that guard for these categories will be declined. If you
hit a genuine edge case (e.g. a false-positive package-name match), fix the
pattern in `GUARD_SCOPE`, don't remove the check.

## Project layout

```
appsporter.sh       AI-agent CLI (flat, single-operation commands)
build_port.sh        Manual porting entry point
scripts/*.sh         Function library, auto-sourced by both entry points
tools/                Local apktool.jar / signapk.jar
keys/aosp/            Local AOSP test signing keys
apc/patches/<oem>/    Per-OEM framework/lib/mods/scripts
.claude/skills/        Claude Code skill (condensed agent reference)
```

## Bash style (`scripts/`, `build_port.sh`, `appsporter.sh`)

* `#!/usr/bin/env bash`, and keep the existing MIT license header on new
  files under `scripts/`.
* Public functions are `UPPER_SNAKE_CASE` (`DECOMPILE`, `GUARD_SCOPE`,
  `SCAN_CHECKS`...). Internal helpers not meant to be called directly are
  prefixed with an underscore (`_STUB_METHOD_BODY`, `_ENCLOSING_METHOD`).
* Always `local` your variables inside functions.
* Validate inputs first and `ABORT "clear message"` (from
  `scripts/logging.sh`) on invalid state - don't let a function fail deep
  inside with a cryptic error. See the top of any function in
  `scripts/apktool.sh` for the pattern.
* Use `LOG` / `LOGW` / `LOGE` instead of raw `echo`/`printf` for anything
  user-facing, so output stays consistent and colorized.
* No network calls, ever, inside `scripts/` or the entry points. If a
  feature needs to fetch something, it's the wrong design for this repo -
  ship the dependency in `tools/` or document it as a manual prerequisite
  instead.
* Run `bash -n <file>` (and `shellcheck` if you have it) before opening a
  PR.
* New AI-assisted operations should be exposed as a function in
  `scripts/*.sh` *and* wired into `appsporter.sh`'s command dispatch, not
  left as a function only reachable by hand-sourcing.

## Smali patch conventions (`apc/patches/<oem>/mods/`, auto-patchers)

* Mirror the destination path exactly: `mods/com/example/Foo.smali` lands
  at `com/example/Foo.smali` in the decompiled APK.
* Keep patches minimal - don't paste in a whole decompiled class when a
  two-method override will do.
* Any new standalone class (e.g. a hook/shim) must not reference
  app-internal fields or methods by name - the host app may be
  R8/ProGuard-shrunk or obfuscated differently per build. Self-contained
  classes using only public Android/Java APIs are safe; see
  `scripts/inject_logger.sh`'s `CrashLogger` for the pattern.
* When patching an existing method's return value, match its declared
  return descriptor exactly (`Z`, `V`, `I`, `Ljava/lang/String;`, ...). A
  mismatched stub fails bytecode verification at install time, which is
  much harder to debug than a bad patch script.
* **Always validate smali you hand-write or hand-patch actually assembles**
  before submitting it or trusting an auto-patch:
  ```bash
  java -jar tools/apktool/apktool.jar b <decompiled_dir> -o /tmp/test.apk
  ```
  This fails with an exact file/line on any invalid directive, bad
  register count, or type mismatch - don't skip it because "it looks
  right".
* Follow the existing `GUARD_SCOPE` deny-list for anything touching
  Settings/SystemUI/overlay components - see Scope above.

## AI-agent contributions

PRs or patches produced with AI assistance (Claude, Kimi, ChatGPT, etc.)
are welcome and should follow `AGENTS.md` / `CLAUDE.md` / the
`.claude/skills/appsporter` skill in this repo. In the PR description,
note which operations were AI-assisted (e.g. "checks identified via
`--scan-checks`, patch reviewed manually") so reviewers know where to look
closest. Auto-patched check removals especially need a human sanity check
that the app still behaves correctly on-device before merging.

## Filing bugs / requests

Use the issue templates - they ask for the target app, OEM/patch used, and
(for bugs) the crash log. If you used `--inject-logger`, you can pull
`appsporter_logs/` straight off the device (no root/ADB needed) and paste
it into the bug report.

## Pull requests

1. Fork, branch off `main`.
2. Keep PRs focused - one patch/feature/fix per PR.
3. Test your change (`bash build_port.sh <apk> <patch>` for porting
   changes, `appsporter.sh` commands + a real `apktool b` rebuild for
   smali/script changes - see above).
4. Describe what you tested and on which OEM/device/app in the PR
   description.

Take love from APC ❤️
