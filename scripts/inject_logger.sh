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



LOGGER_PKG_PATH="com/appsporter/logger"


_WRITE_CRASH_LOGGER_CLASS() {
    local out_file="$1"
    mkdir -p "$(dirname "$out_file")"

    cat > "$out_file" <<'SMALI'
.class public Lcom/appsporter/logger/CrashLogger;
.super Ljava/lang/Object;
.source "CrashLogger.java"


# direct methods

.method public static install(Landroid/content/Context;)V
    .locals 3

    :try_start_0
    new-instance v0, Lcom/appsporter/logger/CrashLogger$Handler;

    invoke-virtual {p0}, Landroid/content/Context;->getApplicationContext()Landroid/content/Context;

    move-result-object v1

    invoke-static {}, Ljava/lang/Thread;->getDefaultUncaughtExceptionHandler()Ljava/lang/Thread$UncaughtExceptionHandler;

    move-result-object v2

    invoke-direct {v0, v1, v2}, Lcom/appsporter/logger/CrashLogger$Handler;-><init>(Landroid/content/Context;Ljava/lang/Thread$UncaughtExceptionHandler;)V

    invoke-static {v0}, Ljava/lang/Thread;->setDefaultUncaughtExceptionHandler(Ljava/lang/Thread$UncaughtExceptionHandler;)V

    const-string v0, "AppsPorter"

    const-string v1, "CrashLogger installed"

    invoke-static {p0, v0, v1}, Lcom/appsporter/logger/CrashLogger;->log(Landroid/content/Context;Ljava/lang/String;Ljava/lang/String;)V
    :try_end_0
    .catch Ljava/lang/Throwable; {:try_start_0 .. :try_end_0} :catch_0

    goto :goto_0

    :catch_0
    move-exception v0

    :goto_0
    return-void
.end method

.method static logDir(Landroid/content/Context;)Ljava/io/File;
    .locals 3

    invoke-virtual {p0}, Landroid/content/Context;->getFilesDir()Ljava/io/File;

    move-result-object v0

    const-string v1, "appsporter_logs"

    new-instance v2, Ljava/io/File;

    invoke-direct {v2, v0, v1}, Ljava/io/File;-><init>(Ljava/io/File;Ljava/lang/String;)V

    invoke-virtual {v2}, Ljava/io/File;->mkdirs()Z

    return-object v2
.end method

.method public static log(Landroid/content/Context;Ljava/lang/String;Ljava/lang/String;)V
    .locals 6

    :try_start_0
    invoke-static {p0}, Lcom/appsporter/logger/CrashLogger;->logDir(Landroid/content/Context;)Ljava/io/File;

    move-result-object v0

    new-instance v1, Ljava/io/File;

    const-string v2, "app_log.txt"

    invoke-direct {v1, v0, v2}, Ljava/io/File;-><init>(Ljava/io/File;Ljava/lang/String;)V

    new-instance v2, Ljava/io/FileWriter;

    const/4 v3, 0x1

    invoke-direct {v2, v1, v3}, Ljava/io/FileWriter;-><init>(Ljava/io/File;Z)V

    new-instance v3, Ljava/text/SimpleDateFormat;

    const-string v4, "yyyy-MM-dd HH:mm:ss"

    sget-object v5, Ljava/util/Locale;->US:Ljava/util/Locale;

    invoke-direct {v3, v4, v5}, Ljava/text/SimpleDateFormat;-><init>(Ljava/lang/String;Ljava/util/Locale;)V

    new-instance v4, Ljava/util/Date;

    invoke-direct {v4}, Ljava/util/Date;-><init>()V

    invoke-virtual {v3, v4}, Ljava/text/SimpleDateFormat;->format(Ljava/util/Date;)Ljava/lang/String;

    move-result-object v4

    new-instance v3, Ljava/lang/StringBuilder;

    invoke-direct {v3}, Ljava/lang/StringBuilder;-><init>()V

    const-string v5, "["

    invoke-virtual {v3, v5}, Ljava/lang/StringBuilder;->append(Ljava/lang/String;)Ljava/lang/StringBuilder;

    invoke-virtual {v3, v4}, Ljava/lang/StringBuilder;->append(Ljava/lang/String;)Ljava/lang/StringBuilder;

    const-string v4, "] ["

    invoke-virtual {v3, v4}, Ljava/lang/StringBuilder;->append(Ljava/lang/String;)Ljava/lang/StringBuilder;

    invoke-virtual {v3, p1}, Ljava/lang/StringBuilder;->append(Ljava/lang/String;)Ljava/lang/StringBuilder;

    const-string v4, "] "

    invoke-virtual {v3, v4}, Ljava/lang/StringBuilder;->append(Ljava/lang/String;)Ljava/lang/StringBuilder;

    invoke-virtual {v3, p2}, Ljava/lang/StringBuilder;->append(Ljava/lang/String;)Ljava/lang/StringBuilder;

    const-string v4, "\n"

    invoke-virtual {v3, v4}, Ljava/lang/StringBuilder;->append(Ljava/lang/String;)Ljava/lang/StringBuilder;

    invoke-virtual {v3}, Ljava/lang/StringBuilder;->toString()Ljava/lang/String;

    move-result-object v3

    invoke-virtual {v2, v3}, Ljava/io/FileWriter;->write(Ljava/lang/String;)V

    invoke-virtual {v2}, Ljava/io/FileWriter;->flush()V

    invoke-virtual {v2}, Ljava/io/FileWriter;->close()V
    :try_end_0
    .catch Ljava/lang/Throwable; {:try_start_0 .. :try_end_0} :catch_0

    goto :goto_0

    :catch_0
    move-exception v0

    :goto_0
    return-void
.end method
SMALI
}


_WRITE_CRASH_HANDLER_CLASS() {
    local out_file="$1"
    mkdir -p "$(dirname "$out_file")"

    cat > "$out_file" <<'SMALI'
.class Lcom/appsporter/logger/CrashLogger$Handler;
.super Ljava/lang/Object;
.source "CrashLogger.java"

.implements Ljava/lang/Thread$UncaughtExceptionHandler;


# instance fields
.field private final ctx:Landroid/content/Context;

.field private final prev:Ljava/lang/Thread$UncaughtExceptionHandler;


# direct methods
.method public constructor <init>(Landroid/content/Context;Ljava/lang/Thread$UncaughtExceptionHandler;)V
    .locals 0

    invoke-direct {p0}, Ljava/lang/Object;-><init>()V

    iput-object p1, p0, Lcom/appsporter/logger/CrashLogger$Handler;->ctx:Landroid/content/Context;

    iput-object p2, p0, Lcom/appsporter/logger/CrashLogger$Handler;->prev:Ljava/lang/Thread$UncaughtExceptionHandler;

    return-void
.end method


# virtual methods
.method public uncaughtException(Ljava/lang/Thread;Ljava/lang/Throwable;)V
    .locals 6

    :try_start_0
    iget-object v0, p0, Lcom/appsporter/logger/CrashLogger$Handler;->ctx:Landroid/content/Context;

    invoke-static {v0}, Lcom/appsporter/logger/CrashLogger;->logDir(Landroid/content/Context;)Ljava/io/File;

    move-result-object v0

    new-instance v1, Ljava/lang/StringBuilder;

    invoke-direct {v1}, Ljava/lang/StringBuilder;-><init>()V

    const-string v4, "crash_"

    invoke-virtual {v1, v4}, Ljava/lang/StringBuilder;->append(Ljava/lang/String;)Ljava/lang/StringBuilder;

    invoke-static {}, Ljava/lang/System;->currentTimeMillis()J

    move-result-wide v2

    invoke-virtual {v1, v2, v3}, Ljava/lang/StringBuilder;->append(J)Ljava/lang/StringBuilder;

    const-string v4, ".txt"

    invoke-virtual {v1, v4}, Ljava/lang/StringBuilder;->append(Ljava/lang/String;)Ljava/lang/StringBuilder;

    invoke-virtual {v1}, Ljava/lang/StringBuilder;->toString()Ljava/lang/String;

    move-result-object v4

    new-instance v5, Ljava/io/File;

    invoke-direct {v5, v0, v4}, Ljava/io/File;-><init>(Ljava/io/File;Ljava/lang/String;)V

    new-instance v0, Ljava/io/FileWriter;

    invoke-direct {v0, v5}, Ljava/io/FileWriter;-><init>(Ljava/io/File;)V

    new-instance v1, Ljava/io/PrintWriter;

    invoke-direct {v1, v0}, Ljava/io/PrintWriter;-><init>(Ljava/io/Writer;)V

    const-string v4, "===== AppsPorter Crash Report ====="

    invoke-virtual {v1, v4}, Ljava/io/PrintWriter;->println(Ljava/lang/String;)V

    sget-object v4, Landroid/os/Build;->MANUFACTURER:Ljava/lang/String;

    invoke-virtual {v1, v4}, Ljava/io/PrintWriter;->println(Ljava/lang/String;)V

    sget-object v4, Landroid/os/Build;->MODEL:Ljava/lang/String;

    invoke-virtual {v1, v4}, Ljava/io/PrintWriter;->println(Ljava/lang/String;)V

    sget v4, Landroid/os/Build$VERSION;->SDK_INT:I

    invoke-virtual {v1, v4}, Ljava/io/PrintWriter;->println(I)V

    const-string v4, "===== Stack Trace ====="

    invoke-virtual {v1, v4}, Ljava/io/PrintWriter;->println(Ljava/lang/String;)V

    invoke-virtual {p2, v1}, Ljava/lang/Throwable;->printStackTrace(Ljava/io/PrintWriter;)V

    invoke-virtual {v1}, Ljava/io/PrintWriter;->flush()V

    invoke-virtual {v1}, Ljava/io/PrintWriter;->close()V
    :try_end_0
    .catch Ljava/lang/Throwable; {:try_start_0 .. :try_end_0} :catch_0

    goto :goto_0

    :catch_0
    move-exception v0

    :goto_0
    iget-object v0, p0, Lcom/appsporter/logger/CrashLogger$Handler;->prev:Ljava/lang/Thread$UncaughtExceptionHandler;

    if-eqz v0, :cond_0

    invoke-interface {v0, p1, p2}, Ljava/lang/Thread$UncaughtExceptionHandler;->uncaughtException(Ljava/lang/Thread;Ljava/lang/Throwable;)V

    :cond_0
    return-void
.end method
SMALI
}

# Minimal Application subclass used only when the target app has no custom
# android:name on <application> yet. If it already has one, we hook into
# that existing class instead (see _HOOK_EXISTING_APPLICATION below).
_WRITE_PORTER_APP_CLASS() {
    local out_file="$1"
    mkdir -p "$(dirname "$out_file")"

    cat > "$out_file" <<'SMALI'
.class public Lcom/appsporter/logger/PorterApp;
.super Landroid/app/Application;
.source "PorterApp.java"


# direct methods
.method public constructor <init>()V
    .locals 0

    invoke-direct {p0}, Landroid/app/Application;-><init>()V

    return-void
.end method

.method public onCreate()V
    .locals 0

    invoke-super {p0}, Landroid/app/Application;->onCreate()V

    invoke-static {p0}, Lcom/appsporter/logger/CrashLogger;->install(Landroid/content/Context;)V

    return-void
.end method
SMALI
}

# _PATCH_MANIFEST_APP_NAME <manifest> <dotted_class_name>
_PATCH_MANIFEST_APP_NAME() {
    local manifest_file="$1"
    local app_name="$2"

    if xmlstarlet sel -N android="http://schemas.android.com/apk/res/android" \
        -t -v "/manifest/application/@android:name" "$manifest_file" >/dev/null 2>&1; then
        xmlstarlet ed -L -N android="http://schemas.android.com/apk/res/android" \
            -u "/manifest/application/@android:name" -v "$app_name" "$manifest_file"
    else
        xmlstarlet ed -L -N android="http://schemas.android.com/apk/res/android" \
            -a "/manifest/application" -t attr -n "android:name" -v "$app_name" "$manifest_file"
    fi
}


# Echoes the absolute path to it, or returns 1 if not found.
_FIND_EXISTING_SMALI_CLASS() {
    local decompile_dir="$1"
    local class_rel="$2"   # e.g. com/example/MyApp.smali

    local root
    for root in "$decompile_dir"/smali*; do
        [ -d "$root" ] || continue
        if [ -f "$root/$class_rel" ]; then
            echo "$root/$class_rel"
            return 0
        fi
    done
    return 1
}

# Inserts an invoke-static call to CrashLogger.install(this) into an
# existing onCreate()V, or synthesizes one if the class has none.
_HOOK_EXISTING_APPLICATION() {
    local smali_file="$1"

    if grep -q 'CrashLogger;->install' "$smali_file" 2>/dev/null; then
        LOG "Logger already hooked into $(basename "$smali_file"), skipping." 4
        return 0
    fi

    if grep -qE '^\.method.*onCreate\(\)V' "$smali_file"; then
        # Insert right after the .method line + its .locals directive, so
        # the call runs regardless of what the original body does.
        awk '
            BEGIN { in_method=0; done=0 }
            {
                print
                if (!done && $0 ~ /^\.method.*onCreate\(\)V/) { in_method=1; next }
                if (in_method && !done && $0 ~ /^[[:space:]]*\.locals/) {
                    print ""
                    print "    invoke-static {p0}, Lcom/appsporter/logger/CrashLogger;->install(Landroid/content/Context;)V"
                    done=1
                    in_method=0
                }
            }
        ' "$smali_file" > "$smali_file.tmp" && mv "$smali_file.tmp" "$smali_file"
        LOG "Hooked CrashLogger.install() into existing onCreate() in $(basename "$smali_file")" 4
        return 0
    fi

    # No onCreate() at all - synthesize a minimal one that calls super then
    # installs the logger, inserted right before the first existing method.
    local super_class
    super_class="$(grep -m1 '^\.super ' "$smali_file" | awk '{print $2}')"
    [ -z "$super_class" ] && super_class="Landroid/app/Application;"

    awk -v super_cls="$super_class" '
        BEGIN { inserted=0 }
        {
            if (!inserted && $0 ~ /^\.method/) {
                print ".method public onCreate()V"
                print "    .locals 0"
                print ""
                print "    invoke-super {p0}, " super_cls "->onCreate()V"
                print ""
                print "    invoke-static {p0}, Lcom/appsporter/logger/CrashLogger;->install(Landroid/content/Context;)V"
                print ""
                print "    return-void"
                print ".end method"
                print ""
                inserted=1
            }
            print
        }
    ' "$smali_file" > "$smali_file.tmp" && mv "$smali_file.tmp" "$smali_file"
    LOG "Synthesized onCreate() with CrashLogger.install() in $(basename "$smali_file")" 4
}

# INJECT_LOGGER <decompiled_apk_dir>
INJECT_LOGGER() {
    local decompile_dir="$1"

    if [ -z "$decompile_dir" ] || [ ! -d "$decompile_dir" ]; then
        LOGE "Usage: INJECT_LOGGER <decompiled_apk_dir>" 2
        ABORT "Invalid inject-logger target."
    fi

    local manifest="$decompile_dir/AndroidManifest.xml"
    if [ ! -f "$manifest" ]; then
        LOGE "AndroidManifest.xml not found in '$decompile_dir'." 2
        ABORT "inject-logger failed."
    fi

    if ! GUARD_SCOPE "$decompile_dir"; then
        ABORT "inject-logger refused: target is out of scope (Settings/SystemUI/overlay)."
    fi

    LOG "Injecting in-app crash/log capture..." 2

    local primary_smali
    primary_smali="$(find "$decompile_dir" -maxdepth 1 -type d -name "smali" | head -1)"
    [ -z "$primary_smali" ] && primary_smali="$decompile_dir/smali"

    _WRITE_CRASH_LOGGER_CLASS "$primary_smali/$LOGGER_PKG_PATH/CrashLogger.smali"
    _WRITE_CRASH_HANDLER_CLASS "$primary_smali/$LOGGER_PKG_PATH/CrashLogger\$Handler.smali"
    LOG "Wrote CrashLogger smali class to smali/$LOGGER_PKG_PATH/" 4

    local existing_app_name
    existing_app_name="$(xmlstarlet sel -N android="http://schemas.android.com/apk/res/android" \
        -t -v "/manifest/application/@android:name" "$manifest" 2>/dev/null)"

    local pkg
    pkg="$(xmlstarlet sel -t -v "/manifest/@package" "$manifest" 2>/dev/null)"

    if [ -z "$existing_app_name" ]; then
        LOG "No custom Application class found - installing PorterApp." 4
        _WRITE_PORTER_APP_CLASS "$primary_smali/$LOGGER_PKG_PATH/PorterApp.smali"
        _PATCH_MANIFEST_APP_NAME "$manifest" "com.appsporter.logger.PorterApp"
        LOG "Set android:name=\"com.appsporter.logger.PorterApp\" in AndroidManifest.xml" 4
    else
        local resolved_name="$existing_app_name"
        [[ "$resolved_name" == .* ]] && resolved_name="${pkg}${resolved_name}"

        local class_rel="${resolved_name//.//}.smali"
        local found_file
        if found_file="$(_FIND_EXISTING_SMALI_CLASS "$decompile_dir" "$class_rel")"; then
            LOG "Found existing Application class: $resolved_name" 4
            _HOOK_EXISTING_APPLICATION "$found_file"
        else
            LOGW "Application class '$resolved_name' declared in manifest but no matching .smali file was found (likely compiled away / in a lib jar)." 4
            LOGW "Skipping auto-hook; call Lcom/appsporter/logger/CrashLogger;->install(Landroid/content/Context;)V manually from that class's onCreate()." 4
        fi
    fi

    LOG "Crash & runtime logs will be written to: /data/data/$pkg/files/appsporter_logs/" 4
    LOG "Users can pull that folder with any 'show app data' file manager, no root/ADB required, and paste it to an AI agent next session." 4
    LOG "Logger injection complete." 2
}
