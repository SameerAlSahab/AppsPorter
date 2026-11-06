#!/usr/bin/env bash
#
#  Copyright (c) 2022 Sameer Al Sahab
#  Licensed under the MIT License. See LICENSE file for details.
#
#  Permission is hereby granted, free of charge, to any person obtaining a copy
#  of this software and associated documentation files (the "Software"), to deal
#  in the Software without restriction, including without limitation the rights
#  to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
#  copies of the Software, and to permit persons to whom the Software is
#  furnished to do so, subject to the following conditions:
#
#  THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
#  IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
#  FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT.
#

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export ROOT

# Paths
TOOLS_DIR="$ROOT/tools"
SCRIPTS_DIR="$ROOT/scripts"
PATCHES_DIR="$ROOT/apc/patches"
OUTPUT_DIR="$ROOT/out"


if [ -d "$SCRIPTS_DIR" ]; then
    for script_file in "$SCRIPTS_DIR"/*.sh; do
        if [ -f "$script_file" ] && [ "$(basename "$script_file")" != "make_apk.sh" ]; then
            source "$script_file"
        fi
    done
fi


PRINT_USAGE() {
    LOG "Usage: ./build_port.sh <apk_file.apk> <patch_name> [--ai] [--ai-dry] [--ai-jobs N]" 2
    LOG "  --ai        extra: AI reviews injected smali methods and fixes crash-prone checks" 2
    LOG "  --ai-dry    like --ai but only reports, changes nothing" 2
    LOG "  --ai-jobs N parallel AI requests (default 200)" 2
    LOG ""
    LOG "Available patches in $PATCHES_DIR:" 2

    if [ -d "$PATCHES_DIR" ]; then
        local found=0
        for patch in "$PATCHES_DIR"/*; do
            if [ -d "$patch" ]; then
                LOG "  - $(basename "$patch")" 4
                found=1
            fi
        done
        if [ "$found" -eq 0 ]; then
            LOG "No patch folders found" 4
        fi
    else
        LOG "Patches directory does not exist" 4
    fi
}


USE_THREADS() {
    local CPU_CORES
    local TOTAL_MEM_GB
    local THREADS
    local RAM_LIMIT

    CPU_CORES="$(nproc)"
    TOTAL_MEM_GB="$(free -g | awk '/^Mem:/{print $2}')"

    # If this is not our pc, then dont care, use all cores lol
	# destroy ms azure runners ;D jk
    if [ -n "$GITHUB_ACTIONS" ]; then
        THREADS="$CPU_CORES"
    else
        THREADS="$((CPU_CORES - 2))"
    fi

    (( THREADS < 1 )) && THREADS=1

    echo "$THREADS"
}

THREAD_COUNT="$(USE_THREADS)"

# Optional flags (can appear anywhere): --ai / --ai-dry / --ai-jobs N
AI_ENABLED="${AI_ENABLED:-0}"
AI_DRY_RUN="${AI_DRY_RUN:-0}"
POSITIONAL_ARGS=()
while [ $# -gt 0 ]; do
    case "$1" in
        --ai)          AI_ENABLED=1 ;;
        --ai-dry)      AI_ENABLED=1; AI_DRY_RUN=1 ;;
        --ai-jobs)     shift; AI_JOBS="${1:-}" ;;
        --ai-jobs=*)   AI_JOBS="${1#*=}" ;;
        *)             POSITIONAL_ARGS+=("$1") ;;
    esac
    shift
done
set -- "${POSITIONAL_ARGS[@]}"

case "$AI_ENABLED" in 1|true|TRUE|True|yes|on) AI_ENABLED=1 ;; *) AI_ENABLED=0 ;; esac
export AI_ENABLED AI_DRY_RUN AI_JOBS

TARGET_APK="$1"
TARGET_PATCH="$2"


if [ $# -lt 2 ]; then
    LOGE "Error: Missing arguments." 2
    PRINT_USAGE
    ABORT "Please provide both the target APK and the patch name."
fi


if [ "${TARGET_APK##*.}" != "apk" ] || [ ! -f "$TARGET_APK" ]; then
    LOGE "APK file '$TARGET_APK' does not exist or is not a valid .apk file." 2
    PRINT_USAGE
    ABORT "Invalid APK input."
fi


SELECTED_PATCH_DIR="$PATCHES_DIR/$TARGET_PATCH"
if [ ! -d "$SELECTED_PATCH_DIR" ]; then
    LOGE "Patch '$TARGET_PATCH' does not exist!" 2
    PRINT_USAGE
    ABORT "Invalid patch name."
fi


CHECK_DEPS

rm -rf $ROOT/out $ROOT/temp

source "$SCRIPTS_DIR/make_apk.sh"
