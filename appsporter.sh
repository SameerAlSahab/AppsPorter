#!/usr/bin/env bash
#
#  Copyright (c) 2022 Sameer Al Sahab
#  Licensed under the MIT License. See LICENSE file for details.
#
#  AI-Agent CLI for AppsPorter.
#
#  This is a thin, scriptable front door for AI agents (Claude, Kimi,
#  ChatGPT, etc.) driving porting/diagnostics locally. It does NOT replace
#  the manual workflow in build_port.sh - it exposes the same underlying
#  functions (decompile/build/sign) plus the AI-assisted helpers
#  (in-app logger injection, check scanning/removal) as flat commands so an
#  agent can call one deterministic operation at a time instead of parsing
#  prose output.
#
#  All jars and keys are local (tools/, keys/) - nothing here reaches the
#  network.
#

set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export ROOT

TOOLS_DIR="$ROOT/tools"
SCRIPTS_DIR="$ROOT/scripts"
PATCHES_DIR="$ROOT/apc/patches"
OUTPUT_DIR="$ROOT/out"

for script_file in "$SCRIPTS_DIR"/*.sh; do
    if [ -f "$script_file" ] && [ "$(basename "$script_file")" != "make_apk.sh" ]; then
        # shellcheck disable=SC1090
        source "$script_file"
    fi
done

THREAD_COUNT="${THREAD_COUNT:-$(nproc 2>/dev/null || echo 1)}"
FRAMEWORK_DIR="${FRAMEWORK_DIR:-$ROOT/frameworks}"

_USAGE() {
    cat <<'USAGE'
AppsPorter AI-Agent CLI

Usage:
  ./appsporter.sh --identify <apk>                     Guess the OEM/app from an APK (manifest + apktool, no network)
  ./appsporter.sh --decompile <apk|jar> [out_dir]       Decompile an APK/JAR with apktool
  ./appsporter.sh --recompile <folder> [out.apk]        Rebuild an APK from a decompiled folder
  ./appsporter.sh --sign <in.apk> [out.apk]             Sign an APK with the local AOSP test keys
  ./appsporter.sh --inject-logger <decompiled_folder>   Inject the no-root crash/log capture class
  ./appsporter.sh --scan-checks <decompiled_folder> [--apply]
                                                         Scan (and optionally auto-patch) Knox/signature/
                                                         compatibility checks

Settings, SystemUI and overlay (RRO) apps are refused by --inject-logger and
--scan-checks - see CONTRIBUTING.md for why.
USAGE
}

_IDENTIFY() {
    local apk="$1"
    if [ -z "$apk" ] || [ ! -f "$apk" ]; then
        LOGE "Usage: --identify <apk>" 2
        ABORT "Invalid --identify target."
    fi

    local tmp_dir
    tmp_dir="$(mktemp -d)"
    trap 'rm -rf "$tmp_dir"' RETURN

    java -jar "$TOOLS_DIR/apktool/apktool.jar" d -q --only-manifest -o "$tmp_dir/decoded" "$apk" >/dev/null 2>&1

    local manifest="$tmp_dir/decoded/AndroidManifest.xml"
    if [ ! -f "$manifest" ]; then
        LOGE "Could not decode manifest from '$apk'." 2
        ABORT "--identify failed."
    fi

    local pkg
    pkg="$(xmlstarlet sel -t -v "/manifest/@package" "$manifest" 2>/dev/null)"
    local version_name
    version_name="$(xmlstarlet sel -N android="http://schemas.android.com/apk/res/android" \
        -t -v "/manifest/@android:versionName" "$manifest" 2>/dev/null)"

    LOG "Package: $pkg" 2
    [ -n "$version_name" ] && LOG "Version:  $version_name" 2

    local oem="unknown"
    case "$pkg" in
        com.xiaomi.*|com.miui.*|com.mi.*)      oem="xiaomi" ;;
        com.oneplus.*|net.oneplus.*)           oem="oneplus" ;;
        com.coloros.*|com.oppo.*)              oem="oppo" ;;
        com.motorola.*)                        oem="moto" ;;
        com.samsung.*|com.sec.*)                oem="samsung (no bundled patch yet)" ;;
        *)
            grep -qi 'miui\|xiaomi' "$manifest" 2>/dev/null && oem="xiaomi (string match)"
            ;;
    esac

    LOG "Likely OEM: $oem" 2

    if [ -d "$PATCHES_DIR/$oem" ]; then
        LOG "A matching patch exists: apc/patches/$oem" 4
    else
        LOGW "No bundled patch folder named '$oem' under apc/patches - check apc/patches/ manually or create a new one." 4
    fi
}

[ $# -lt 1 ] && { _USAGE; exit 1; }

case "$1" in
    --identify)
        CHECK_DEPS
        _IDENTIFY "${2:-}"
        ;;
    --decompile)
        CHECK_DEPS
        src="${2:-}"
        out="${3:-$ROOT/out/decompiled_$(basename "${src%.*}")}"
        DECOMPILE "$src" "$out"
        LOG "Decompiled to: $out" 2
        ;;
    --recompile)
        CHECK_DEPS
        src="${2:-}"
        out="${3:-$ROOT/out/$(basename "$src")_rebuilt.apk}"
        mkdir -p "$(dirname "$out")"
        BUILD "$src" "$out"
        LOG "Rebuilt to: $out" 2
        ;;
    --sign)
        CHECK_DEPS
        src="${2:-}"
        out="${3:-${src%.apk}_signed.apk}"
        CAN_SIGN=1
        SIGN_APK "$src" "$out"
        LOG "Signed to: $out" 2
        ;;
    --inject-logger)
        CHECK_DEPS
        INJECT_LOGGER "${2:-}"
        ;;
    --scan-checks)
        CHECK_DEPS
        SCAN_CHECKS "${2:-}" "${3:-}"
        ;;
    -h|--help)
        _USAGE
        ;;
    *)
        LOGE "Unknown command: $1" 2
        _USAGE
        exit 1
        ;;
esac
