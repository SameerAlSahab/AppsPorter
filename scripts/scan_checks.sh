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



SCAN_PATTERNS=(
    "knox:[Kk][Nn][Oo][Xx]"
    "signature-check:checkSignatures|GET_SIGNATURES|GET_SIGNING_CERTIFICATES|PackageInfo;->signatures|ApkSignatureVerifier"
    "safetynet-integrity:SafetyNet|com/google/android/gms/safetynet|PlayIntegrity|com/google/android/play/core/integrity"
    "root-detection:RootBeer|/system/bin/su|/system/xbin/su|Magisk|test-keys|which.*\\bsu\\b"
    "device-compat:isDeviceCompatible|isSupportedDevice|isCompatible|checkCompatibility|verifyDevice|isGenuineDevice|deviceAllowList|deviceWhitelist"
    "signature-lock:checkSignature|verifySignature|signatureMatch|SIGNATURE_MATCH"
    "generic-verification-lock:isTampered|isDebuggable|isEmulator|anti.?[Pp]ort|isAuthorized"
)


AUTO_PATCH_METHOD_NAME_REGEX='(isDeviceCompatible|isSupportedDevice|isCompatible|checkCompatibility|verifyDevice|isGenuineDevice|checkSignature|verifySignature|signatureMatch|isKnoxAttest|isTampered|isDebuggable|isEmulator|isAuthorized|isRooted|isDeviceRooted)'

# Finds the nearest preceding ".method" line for a given line number in a
# file, to report which method a hit lives in.
_ENCLOSING_METHOD() {
    local file="$1"
    local line_no="$2"
    awk -v target="$line_no" '
        NR <= target && $0 ~ /^\.method/ { last=$0 }
        NR == target { print last; exit }
    ' "$file"
}

# Returns the return-type stub descriptor for a method signature line, e.g.
# ")Z" -> "Z". Empty if it cannot be determined.
_METHOD_RETURN_TYPE() {
    local method_line="$1"
    echo "$method_line" | grep -oE '\)[A-Za-z\[/;]+$' | sed 's/^)//'
}

# Replaces an entire method body (between .method and .end method) with a
# minimal stub that returns a fixed "safe" value for its declared type.
# Skips methods containing .annotation blocks (left for manual review).
_STUB_METHOD_BODY() {
    local file="$1"
    local method_line_no="$2"

    local method_sig
    method_sig="$(sed -n "${method_line_no}p" "$file")"
    local ret_type
    ret_type="$(_METHOD_RETURN_TYPE "$method_sig")"

    local stub_body
    case "$ret_type" in
        Z) stub_body=$'    .locals 1\n\n    const/4 v0, 0x1\n\n    return v0' ;;      # true
        V) stub_body=$'    .locals 0\n\n    return-void' ;;
        I|B|S|C) stub_body=$'    .locals 1\n\n    const/4 v0, 0x1\n\n    return v0' ;;
        J) stub_body=$'    .locals 2\n\n    const-wide/16 v0, 0x1\n\n    return-wide v0' ;;
        F) stub_body=$'    .locals 1\n\n    const/4 v0, 0x1\n\n    int-to-float v0, v0\n\n    return v0' ;;
        D) stub_body=$'    .locals 2\n\n    const-wide/16 v0, 0x1\n\n    long-to-double v0, v0\n\n    return-wide v0' ;;
        *) stub_body=$'    .locals 1\n\n    const/4 v0, 0x0\n\n    return-object v0' ;;  # object/array -> null
    esac

    awk -v start="$method_line_no" -v body="$stub_body" '
        BEGIN { skip=0 }
        NR == start { print; print body; skip=1; next }
        skip == 1 {
            if ($0 ~ /^\.end method/) { print; skip=0 }
            next
        }
        { print }
    ' "$file" > "$file.tmp" && mv "$file.tmp" "$file"
}

# SCAN_CHECKS <decompiled_apk_dir> [--apply]
SCAN_CHECKS() {
    local decompile_dir="$1"
    local apply=0
    [ "${2:-}" = "--apply" ] && apply=1

    if [ -z "$decompile_dir" ] || [ ! -d "$decompile_dir" ]; then
        LOGE "Usage: SCAN_CHECKS <decompiled_apk_dir> [--apply]" 2
        ABORT "Invalid scan-checks target."
    fi

    if ! GUARD_SCOPE "$decompile_dir"; then
        ABORT "scan-checks refused: target is out of scope (Settings/SystemUI/overlay)."
    fi

    local smali_dirs=("$decompile_dir"/smali*)
    if [ ! -d "${smali_dirs[0]}" ]; then
        LOGE "No smali* directories found under '$decompile_dir'." 2
        ABORT "scan-checks failed."
    fi

    LOG "Scanning for security/Knox/signature/compatibility checks..." 2

    local total_hits=0
    local patched=0
    local entry pattern category regex

    for entry in "${SCAN_PATTERNS[@]}"; do
        category="${entry%%:*}"
        regex="${entry#*:}"

        while IFS=: read -r file line_no match_text; do
            [ -z "$file" ] && continue
            total_hits=$((total_hits + 1))

            local method
            method="$(_ENCLOSING_METHOD "$file" "$line_no")"
            local method_name
            method_name="$(echo "$method" | grep -oE '[a-zA-Z0-9_$<>]+\(' | head -1 | tr -d '(')"

            LOG "[$category] ${file#"$decompile_dir"/}:$line_no" 4
            [ -n "$method" ] && LOG "  in: ${method# }" 6

            if [ "$apply" -eq 1 ] && [ -n "$method" ] && [[ "$method_name" =~ $AUTO_PATCH_METHOD_NAME_REGEX ]]; then
                if echo "$method" | grep -q '()Z$\|)Z$'; then
                    local method_line_no
                    method_line_no="$(grep -n -F "$method" "$file" | head -1 | cut -d: -f1)"
                    if [ -n "$method_line_no" ]; then
                        # Skip if the method carries annotations - too risky to blindly stub.
                        local end_line_no
                        end_line_no="$(awk -v s="$method_line_no" 'NR>=s && /^\.end method/{print NR; exit}' "$file")"
                        if [ -n "$end_line_no" ] && ! sed -n "${method_line_no},${end_line_no}p" "$file" | grep -q '^\.annotation'; then
                            _STUB_METHOD_BODY "$file" "$method_line_no"
                            LOG "  -> AUTO-PATCHED: forced to return true" 6
                            patched=$((patched + 1))
                        else
                            LOGW "  -> has .annotation block, skipped auto-patch (manual review)" 6
                        fi
                    fi
                else
                    LOGW "  -> matched name but return type isn't boolean, skipped auto-patch" 6
                fi
            fi
        done < <(grep -rnE "$regex" "${smali_dirs[@]}" 2>/dev/null)
    done

    if [ "$total_hits" -eq 0 ]; then
        LOG "No known check/verification patterns found." 2
    else
        LOG "Found $total_hits potential check(s) across $(printf '%s\n' "${SCAN_PATTERNS[@]}" | wc -l) categories." 2
        if [ "$apply" -eq 1 ]; then
            LOG "Auto-patched $patched method(s) to return true. Review the rest manually or re-run with an AI agent reading this output." 2
        else
            LOG "Re-run with --apply to auto-patch the safe boolean-check cases (device-compat/signature/root/tamper name matches)." 2
        fi
    fi
}
