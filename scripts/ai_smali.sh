#!/usr/bin/env bash
#
#  Copyright (c) 2022 Sameer Al Sahab
#  Licensed under the MIT License. See LICENSE file for details.
#
#

AI_PY="${AI_PY:-$ROOT/scripts/ai_smali.py}"


case "${AI_ENABLED:-0}" in
    1|true|TRUE|True|yes|on) AI_ENABLED=1 ;;
    *) AI_ENABLED=0 ;;
esac
export AI_ENABLED

# AI_REVIEW_SMALI <decompiled_apk_dir> <file_with_smali_paths> <backup_dir> [report_file]

AI_REVIEW_SMALI() {
    local root="$1" list="$2" backup="$3" report="${4:-$OUTPUT_DIR/ai_report.json}"
    AI_CHANGED=0

    if ! command -v python3 >/dev/null 2>&1; then
        LOGW "AI review skipped: python3 is not installed." 2
        return 0
    fi
    if [ ! -f "$AI_PY" ]; then
        LOGW "AI review skipped: $AI_PY not found." 2
        return 0
    fi
    if [ ! -s "$list" ]; then
        LOG "AI review: no injected smali files, nothing to review." 2
        return 0
    fi

    local extra=()
    [ "${AI_DRY_RUN:-0}" = "1" ] && extra+=(--dry)
    [ -n "${AI_JOBS:-}" ] && extra+=(--jobs "$AI_JOBS")

    mkdir -p "$backup" "$(dirname "$report")"
    LOG "AI smali review (beta) - checking injected classes for crash-prone code..." 2

    python3 "$AI_PY" review \
        --root "$root" --list "$list" \
        --backup "$backup" --report "$report" \
        "${extra[@]}" || LOGW "AI review exited with an error - continuing without it." 2

    if [ -n "$(find "$backup" -type f -print -quit 2>/dev/null)" ]; then
        AI_CHANGED=1
    fi
    return 0
}

# AI_RESTORE_SMALI <decompiled_apk_dir> <backup_dir>
AI_RESTORE_SMALI() {
    python3 "$AI_PY" restore --root "$1" --backup "$2"
}

# AI_REPAIR_SMALI <decompiled_apk_dir> <backup_dir> <apktool_log> <attempt> <max_attempts>
# apktool failed: the error log goes to the AI, it names the broken smali file(s) and fixes the method(s).
# returns 0 when something was changed (go rebuild), non-zero when there is nothing more the AI can do.
AI_REPAIR_SMALI() {
    command -v python3 >/dev/null 2>&1 || return 1
    [ -f "$AI_PY" ] || return 1
    mkdir -p "$2"
    python3 "$AI_PY" repair --root "$1" --backup "$2" --log "$3" --attempt "$4" --max-attempts "$5"
}
