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


DENIED_PACKAGE_PATTERNS=(
    '^com\.android\.settings'
    '^com\.android\.systemui'
    '(^|\.)systemui(\.|$)'
    '^com\.[a-z0-9]+\.settings($|\.)'
    '(^|\.)settings\.overlay(\.|$)'
    '(^|\.)overlay(\.|$)'
)

# GUARD_SCOPE <decompiled_dir>
# Returns 0 (allowed) or 1 (denied), printing why via LOG*.
GUARD_SCOPE() {
    local target_dir="$1"
    local manifest="$target_dir/AndroidManifest.xml"

    if [ ! -f "$manifest" ]; then
        LOGW "GUARD_SCOPE: no AndroidManifest.xml found at '$target_dir' to inspect, allowing by default." 2
        return 0
    fi

    local package
    package="$(xmlstarlet sel -t -v "/manifest/@package" "$manifest" 2>/dev/null)"

    local pattern
    for pattern in "${DENIED_PACKAGE_PATTERNS[@]}"; do
        if [[ "$package" =~ $pattern ]]; then
            LOGE "GUARD_SCOPE: refusing '$package' - looks like a Settings/SystemUI/overlay package." 2
            LOGW "These require system-server signature permissions and overlay targets this tool cannot reproduce." 2
            return 1
        fi
    done

    if grep -q '<overlay' "$manifest" 2>/dev/null; then
        LOGE "GUARD_SCOPE: AndroidManifest.xml declares an <overlay> (RRO) target - not portable by this tool." 2
        return 1
    fi

    if grep -q 'android:sharedUserId="android.uid.system"' "$manifest" 2>/dev/null; then
        LOGE "GUARD_SCOPE: app requests the android.uid.system sharedUserId - needs platform signing/system privileges." 2
        return 1
    fi

    if grep -qE 'android:name="android\.permission\.(STATUS_BAR|BIND_SETTINGS|WRITE_SECURE_SETTINGS|MANAGE_USB|BIND_SYSTEM_UI)"' "$manifest" 2>/dev/null; then
        LOGW "GUARD_SCOPE: app requests system-signature permissions usually tied to SystemUI/Settings. Proceeding - review the result manually." 2
    fi

    return 0
}
