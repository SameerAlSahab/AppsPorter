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


if [ -z "$ROOT" ] || [ -z "$TARGET_APK" ] || [ -z "$TARGET_PATCH" ]; then
    echo -e "\033[0;31m[ERROR] Environment not initialized! Please run via build_port.sh\033[0m"
    exit 1
fi

LOG "Starting Porting for: $TARGET_APK using patch: $TARGET_PATCH"
LOG "Allocated Threads: $THREAD_COUNT" 2

PATCH_DIR="$PATCHES_DIR/$TARGET_PATCH"
PATCH_FW_DIR="$PATCH_DIR/framework"
BUILD_TEMP_DIR="$ROOT/temp/build_$(date +%s)"
APK_DECOMPILE_DIR="$BUILD_TEMP_DIR/decompiled_apk"
FW_JAR_DECOMPILE_DIR="$BUILD_TEMP_DIR/decompiled_fw_jars"

mkdir -p "$BUILD_TEMP_DIR"
mkdir -p "$FW_JAR_DECOMPILE_DIR"


FW_RES_APK="$PATCH_FW_DIR/framework-res.apk"
if [ -f "$FW_RES_APK" ]; then
    LOG "Installing Framework Resource APK: $(basename "$FW_RES_APK")" 2
    INSTALL_FRAMEWORK "$FW_RES_APK"
else
    LOGW "framework-res.apk not found in patch directory. Skipping framework install." 2
fi


LOG "Decompiling target APK..." 2
DECOMPILE "$TARGET_APK" "$APK_DECOMPILE_DIR"

MANIFEST="$APK_DECOMPILE_DIR/AndroidManifest.xml"
if [ ! -f "$MANIFEST" ]; then
    ABORT "AndroidManifest.xml missing in decompiled target APK."
fi

LOG "Patching library in AndroidManifest.xml..." 2
FIND_REPLACE_TEXT "$MANIFEST" "uses-library" "uses-fake-libs"


if [ -d "$PATCH_FW_DIR" ]; then
    LOG "Decompiling framework .jar files..." 2
    for jar_file in "$PATCH_FW_DIR"/*.jar; do
        if [ -f "$jar_file" ]; then
            jar_name=$(basename "$jar_file" .jar)
            DECOMPILE "$jar_file" "$FW_JAR_DECOMPILE_DIR/$jar_name"
        fi
    done
else
    LOGW "Framework directory does not exist: $PATCH_FW_DIR" 2
fi



GET_NEXT_SMALI_INDEX() {
    local max_num=1
    for dir in "$APK_DECOMPILE_DIR"/smali*; do
        if [ -d "$dir" ]; then
            dirname=$(basename "$dir")
            if [ "$dirname" == "smali" ]; then
                [ $max_num -lt 1 ] && max_num=1
            elif [[ "$dirname" =~ ^smali_classes([0-9]+)$ ]]; then
                num="${BASH_REMATCH[1]}"
                if [ "$num" -gt "$max_num" ]; then
                    max_num="$num"
                fi
            fi
        fi
    done
    echo $((max_num + 1))
}

NEXT_SMALI_NUM=$(GET_NEXT_SMALI_INDEX)
TARGET_NEW_SMALI_DIR="$APK_DECOMPILE_DIR/smali_classes${NEXT_SMALI_NUM}"

mkdir -p "$TARGET_NEW_SMALI_DIR"

LOG "Scanning for missing OEM Framework Smali references..." 2

COPIED_COUNT=0

# every framework class we inject is listed here (used by the optional --ai review)
INJECTED_LIST="$BUILD_TEMP_DIR/injected_smali.txt"
: > "$INJECTED_LIST"


if [ -d "$FW_JAR_DECOMPILE_DIR" ]; then

    declare -A FW_CLASS_FILE=()
    while IFS= read -r fw_smali_path; do
        rel_path=$(echo "$fw_smali_path" | sed -E 's|.*/smali(_classes[0-9]+)?/||')
        class_descriptor="L${rel_path%.smali};"
        FW_CLASS_FILE["$class_descriptor"]="$fw_smali_path"
    done < <(find "$FW_JAR_DECOMPILE_DIR" -type f -name "*.smali" 2>/dev/null)


    declare -A EXISTING_SET=()
    while IFS= read -r rel; do
        [ -n "$rel" ] && EXISTING_SET["$rel"]=1
    done < <(find "$APK_DECOMPILE_DIR"/smali* -type f -name "*.smali" 2>/dev/null \
                | sed -E 's|.*/smali(_classes[0-9]+)?/||' | sort -u)


    declare -A VISITED=()

    IS_EXCLUDED_CLASS() {
        local desc="$1"
        [[ "$desc" =~ ^Landroid/ ]] || [[ "$desc" =~ ^Landroidx/ ]] || \
        [[ "$desc" =~ ^Ljava/ ]] || [[ "$desc" =~ ^Lkotlin/ ]] || \
        [[ "$desc" =~ ^Lcom/google/ ]]

    }


    QUEUE_FILE="$BUILD_TEMP_DIR/smali_queue.txt"
    grep -rhoE 'L[a-zA-Z0-9_/$]+;' "$APK_DECOMPILE_DIR"/smali* 2>/dev/null | sort -u > "$QUEUE_FILE"

    while [ -s "$QUEUE_FILE" ]; do
        NEXT_QUEUE_FILE="$BUILD_TEMP_DIR/smali_queue_next.txt"
        : > "$NEXT_QUEUE_FILE"

        while IFS= read -r class_descriptor; do
            [ -n "$class_descriptor" ] || continue
            [ -n "${VISITED[$class_descriptor]+x}" ] && continue
            VISITED["$class_descriptor"]=1

            # Ignore standard Android/Google/Java/Kotlin namespaces
            IS_EXCLUDED_CLASS "$class_descriptor" && continue

            fw_smali_path="${FW_CLASS_FILE[$class_descriptor]:-}"
            [ -z "$fw_smali_path" ] && continue   # not part of this OEM framework

            rel_path="${class_descriptor#L}"
            rel_path="${rel_path%;}.smali"

            [ -n "${EXISTING_SET[$rel_path]+x}" ] && continue   # already present

            dest_file="$TARGET_NEW_SMALI_DIR/$rel_path"
            mkdir -p "$(dirname "$dest_file")"
            cp "$fw_smali_path" "$dest_file"
            echo "$dest_file" >> "$INJECTED_LIST"
            LOG "Injected missing Smali: $rel_path" 4
            COPIED_COUNT=$((COPIED_COUNT + 1))
            EXISTING_SET["$rel_path"]=1

            grep -hoE 'L[a-zA-Z0-9_/$]+;' "$fw_smali_path" >> "$NEXT_QUEUE_FILE"
        done < "$QUEUE_FILE"

        sort -u "$NEXT_QUEUE_FILE" -o "$NEXT_QUEUE_FILE"
        mv "$NEXT_QUEUE_FILE" "$QUEUE_FILE"
    done
fi

NATIVE_LIBS_INJECTED=0


if [ -d "$PATCH_DIR/lib" ]; then
    LOG "Injecting native libraries..." 2
    mkdir -p "$APK_DECOMPILE_DIR/lib/armeabi-v7a"
    cp -rf "$PATCH_DIR/lib"/* "$APK_DECOMPILE_DIR/lib/armeabi-v7a/" 2>/dev/null || \
    cp -rf "$PATCH_DIR/lib"/* "$APK_DECOMPILE_DIR/lib/"
    NATIVE_LIBS_INJECTED=1
fi


if [ -d "$PATCH_DIR/lib64" ]; then
    LOG "Injecting 64-bit native libraries..." 2
    mkdir -p "$APK_DECOMPILE_DIR/lib/arm64-v8a"
    cp -rf "$PATCH_DIR/lib64"/* "$APK_DECOMPILE_DIR/lib/arm64-v8a/"
    NATIVE_LIBS_INJECTED=1
fi


if [ "$NATIVE_LIBS_INJECTED" -eq 1 ]; then
    SET_EXTRACT_NATIVE_LIBS "$MANIFEST" "true"
fi

# Cleanup empty
if [ -z "$(ls -A "$TARGET_NEW_SMALI_DIR")" ]; then
    rm -rf "$TARGET_NEW_SMALI_DIR"
    LOG "No missing smali classes were injected." 2
else
    LOG "Successfully injected missing smali classes into smali_classes${NEXT_SMALI_NUM}" 2
fi

PATCH_LIB_DIR="$PATCH_DIR/lib"
if [ -d "$PATCH_LIB_DIR" ]; then
    LOG "Injecting native libraries (.so) from patch..." 2
    mkdir -p "$APK_DECOMPILE_DIR/lib"
    cp -r "$PATCH_LIB_DIR"/* "$APK_DECOMPILE_DIR/lib/"
fi


MODS_DIR="$PATCH_DIR/mods"

if [ -z "$(ls -A "$TARGET_NEW_SMALI_DIR")" ] && [ ! -d "$MODS_DIR" ]; then
    rm -rf "$TARGET_NEW_SMALI_DIR"
    LOG "No missing smali classes were injected." 2
else

    mkdir -p "$TARGET_NEW_SMALI_DIR"

    if [ -d "$MODS_DIR" ]; then
        LOG "Injecting files from modded folder to smali_classes${NEXT_SMALI_NUM}..." 2
        cp -rf "$MODS_DIR"/* "$TARGET_NEW_SMALI_DIR/"
    fi

fi


PATCH_SCRIPTS_DIR="$PATCH_DIR/scripts"
if [ -d "$PATCH_SCRIPTS_DIR" ]; then
    LOG "Running custom patch scripts..." 2

    MAIN_PWD="$(pwd)"

    cd "$APK_DECOMPILE_DIR" || exit 1

    for script_file in "$PATCH_SCRIPTS_DIR"/*.sh; do
        if [ -f "$script_file" ]; then
            LOG "  Executing: $(basename "$script_file")" 3
            source "$script_file"
        fi
    done

    cd "$MAIN_PWD" || exit 1
fi

mkdir -p "$OUTPUT_DIR"

UNSIGNED_APK="$BUILD_TEMP_DIR/unsigned_target.apk"
FINAL_SIGNED_APK="$OUTPUT_DIR/$(basename "$TARGET_APK" .apk)_ported.apk"

# ---- optional: AI review of the injected framework smali (--ai) ----
AI_CHANGED=0
AI_BACKUP_DIR="$BUILD_TEMP_DIR/ai_backup"
if [ "${AI_ENABLED:-0}" = "1" ]; then
    # classes supplied by the patch's mods/ folder are hand-written, only review the injected ones
    AI_LIST="$BUILD_TEMP_DIR/ai_review_list.txt"
    : > "$AI_LIST"
    while IFS= read -r injected_file; do
        [ -f "$injected_file" ] || continue
        rel_inj="${injected_file#"$TARGET_NEW_SMALI_DIR"/}"
        [ -f "$MODS_DIR/$rel_inj" ] && continue
        echo "$injected_file" >> "$AI_LIST"
    done < "$INJECTED_LIST"

    AI_REVIEW_SMALI "$APK_DECOMPILE_DIR" "$AI_LIST" "$AI_BACKUP_DIR" "$OUTPUT_DIR/ai_report.json"
fi

LOG "Rebuilding APK..." 2
BUILD "$APK_DECOMPILE_DIR" "$UNSIGNED_APK"
BUILD_STATUS=$?

# If AI edits broke compilation, undo them and build the plain port instead
if [ "$BUILD_STATUS" -ne 0 ] && [ "$AI_CHANGED" -eq 1 ]; then
    LOGW "Build failed with AI edits - reverting them and rebuilding without AI..." 2
    AI_RESTORE_SMALI "$APK_DECOMPILE_DIR" "$AI_BACKUP_DIR"
    rm -f "$UNSIGNED_APK" "$OUTPUT_DIR/ai_report.json"
    BUILD "$APK_DECOMPILE_DIR" "$UNSIGNED_APK"
fi

LOG "Signing final APK..." 2
SIGN_APK "$UNSIGNED_APK" "$FINAL_SIGNED_APK"

PRINT_BUILD_OUTCOME 0 "$(basename "$FINAL_SIGNED_APK")"
LOG "Ported APK generated at: $FINAL_SIGNED_APK" 2

# Cleanup temporary files
rm -rf "$BUILD_TEMP_DIR"
