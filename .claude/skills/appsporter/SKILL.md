---
name: appsporter
description: Use when decompiling/recompiling/signing an APK, identifying which OEM an app belongs to, injecting the no-root crash logger, or scanning/patching Knox-signature-compatibility checks in this repo (AppsPorter).
---

# AppsPorter agent skill

Condensed, load-on-demand reference for working in this repo. Full
rationale and constraints are in `AGENTS.md` at the repo root - read that
too if anything here is ambiguous.

## Zero-network rule

Every jar and key this tool needs is already in the repo:

* `tools/apktool/apktool.jar`
* `tools/signapk/signapk.jar`
* `keys/aosp/aosp_testkey.{pk8,x509.pem}`
* `apc/patches/<oem>/framework/*.jar`, `framework-res.apk`, `lib*/`, `mods/`

If a class, method, or `.so` seems missing, check `apc/patches/<oem>/`
first. Only tell the user to supply an extra APK/firmware dump if it
genuinely isn't anywhere in the repo - never suggest downloading a tool.

## Commands

```bash
./appsporter.sh --identify <apk>                              # which OEM?
./appsporter.sh --decompile <apk|jar> [out_dir]
./appsporter.sh --recompile <decompiled_dir> [out.apk]
./appsporter.sh --sign <in.apk> [out.apk]
./appsporter.sh --inject-logger <decompiled_dir>               # no-root crash/log capture
./appsporter.sh --scan-checks <decompiled_dir> [--apply]       # Knox/sig/compat checks
```

For the full manual porting pipeline (framework smali injection, native
lib copy, OEM mods, sign), use `bash build_port.sh <apk> <patch_name>`
instead - `appsporter.sh` is the flat, scriptable subset meant for an agent
driving one operation at a time.

## Deny-scope: refuse these, always

`scripts/guard_scope.sh` (`GUARD_SCOPE`) auto-refuses `--inject-logger` and
`--scan-checks` against anything that looks like:

* `com.android.settings*`, `com.android.systemui*`, or any
  `*.systemui`/`*.settings` package
* a manifest declaring `<overlay>` (an RRO)
* `android:sharedUserId="android.uid.system"`

Apply the same judgment yourself for the manual `build_port.sh` path -
these need platform-signature permissions or an overlay target this tool
cannot reproduce. Say so plainly and stop; don't attempt a partial port.

## Debugging loop

1. `--identify` the APK to confirm the OEM and whether a patch already
   exists under `apc/patches/`.
2. `--decompile` it.
3. `--scan-checks` the decompiled folder. Its output is `[category]
   file:line`, with the enclosing method name. Only re-run with `--apply`
   for the cases it reports as boolean-returning name matches
   (`isDeviceCompatible`, `checkSignature`, `isRooted`, etc.) - it stubs
   those to `return true`/safe values itself. Everything else needs a
   manual look; don't hand-patch a non-boolean check by guessing.
4. `--inject-logger` the same folder so the user has real crash data for
   the *next* session instead of guesses. Logs land on-device at
   `/data/data/<package>/files/appsporter_logs/` - readable without
   root/ADB from any file manager with "show app data". Ask the user to
   paste `app_log.txt` / the newest `crash_*.txt` back next time.
5. `--recompile` then `--sign`.

## Bash conventions when extending scripts/

* Functions are `UPPER_SNAKE_CASE`; locals are declared with `local`.
* Use `LOG "msg" <indent>`, `LOGW`, `LOGE`, `ABORT "msg"` from
  `scripts/logging.sh` instead of raw `echo`.
* Validate args and existence of input paths at the top of every function,
  `ABORT` with a clear message on failure - mirror the style already in
  `scripts/apktool.sh`.
* New scripts go in `scripts/*.sh` and are auto-sourced by both
  `build_port.sh` and `appsporter.sh` (everything except `make_apk.sh`,
  which is the manual pipeline body, not a function library).

## Smali patch conventions

* Any new smali class must be self-contained (no app-internal field/method
  references) so it can't collide with R8/ProGuard shrinking in the host
  app - see `scripts/inject_logger.sh`'s `CrashLogger`/`CrashLogger$Handler`
  for the pattern.
* After hand-writing or patching smali, validate it actually assembles
  before trusting it: `java -jar tools/apktool/apktool.jar b <decompiled_dir> -o /tmp/test.apk`
  will fail loudly with a file/line on any invalid directive or bad
  register usage. Do this for any non-trivial smali edit, not just the
  auto-patcher's output.
* When stubbing a check method's return value, match the declared return
  type exactly (`Z` -> `const/4 vN, 0x1` + `return vN`, `V` -> `return-void`,
  object/array -> `return-object` of `null`, etc. - see
  `_STUB_METHOD_BODY` in `scripts/scan_checks.sh`). A mismatched return
  descriptor fails verification at install time, not at patch time.
