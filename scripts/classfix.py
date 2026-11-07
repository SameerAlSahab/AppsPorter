#!/usr/bin/env python3
#
#  Copyright (c) 2022 Sameer Al Sahab
#  Licensed under the MIT License. See LICENSE file for details.
#
#  classfix - deterministic NoClassDefFoundError guard (no AI, no network, stdlib only).
#
#  After the OEM classes were injected, some classes the app touches can STILL not exist at runtime
#  (OEM-only classes missing from the framework jars, vendor libs, ...). Touching them kills the app
#  with NoClassDefFoundError. This pass finds every instruction that references such a class and
#  neutralizes ONLY that instruction, keeping the method valid:
#
#     invoke-* {..}, LMissing;->m()R      -> nop   (+ the following move-result becomes const 0/null)
#     new-instance / const-class / new-array of LMissing;  -> const/16 vN, 0x0   (null)
#     sget-* / iget-* on LMissing;->f     -> const 0/null      sput-* / iput-* -> nop
#     check-cast / instance-of LMissing;  -> nop / const 0
#
#  `nop` (not deletion) keeps try-ranges and labels valid.
#
#  Usage:  classfix.py --root <decompiled_apk> [--report out.json] [--dry]

import argparse
import json
import os
import re
import sys

# namespaces that exist on every device (or are bundled / Play-provided): never touched
STOCK_RE = re.compile(
    r"^L(?:android/|java/|javax/|dalvik/|libcore/|sun/|com/android/|com/google/|androidx/|kotlin/|kotlinx/|"
    r"org/(?:w3c|xml|json|apache|xmlpull|ccil|conscrypt|bouncycastle)/)")
# OEM classes that live in the android.* namespace (ExtraRingtone, MiuiConfiguration, ...)
OEM_ANDROID_RE = re.compile(
    r"^Landroid/(?:[\w$]+/)*(?:Extra|Miui|Oplus|OPlus|Oppo|OnePlus|Sem[A-Z]|SecProduct|Samsung|Knox|Moto)[\w$]*;$")

DESC_RE = re.compile(r"L[\w/$]+;")
INVOKE_RE = re.compile(r"\},\s*\[*(L[\w/$]+;)->\S*\)(\S+)\s*$")
FIELD_RE = re.compile(r",\s*(L[\w/$]+;)->[\w$]+:(\S+)\s*$")
MOVE_RES_RE = re.compile(r"^move-result(-wide|-object)?\s+([vp]\d+)\s*$")
REG_RE = re.compile(r"^[a-z][\w/\-]*\s+([vp]\d+)")


def collect_defined(root):
    defined = set()
    for d in os.listdir(root):
        if not d.startswith("smali"):
            continue
        base = os.path.join(root, d)
        for dp, _, fn in os.walk(base):
            for x in fn:
                if x.endswith(".smali"):
                    rel = os.path.relpath(os.path.join(dp, x), base)[:-6]
                    defined.add("L" + rel.replace(os.sep, "/") + ";")
    return defined


def make_checker(defined):
    cache = {}

    def missing(desc):
        r = cache.get(desc)
        if r is None:
            if desc in defined:
                r = False
            elif OEM_ANDROID_RE.match(desc):
                r = True
            elif STOCK_RE.match(desc):
                r = False
            else:
                r = True
            cache[desc] = r
        return r

    return missing


def const_for(reg, wide):
    return f"const-wide/16 {reg}, 0x0" if wide else f"const/16 {reg}, 0x0"


def fix_file(path, missing, hits, sites):
    with open(path, "r", encoding="utf-8", errors="surrogateescape", newline="") as f:
        text = f.read()
    if not any(missing(d) for d in set(DESC_RE.findall(text))):
        return False
    lines = text.split("\n")
    changed = False
    i = 0
    n = len(lines)
    while i < n:
        raw = lines[i]
        s = raw.strip()
        i += 1
        if not s or s[0] in ".:#":
            continue
        descs = DESC_RE.findall(s)
        if not descs or not any(missing(d) for d in descs):
            continue
        op = s.split(None, 1)[0]
        ind = raw[:len(raw) - len(raw.lstrip())]
        new = None

        if op.startswith("invoke-"):
            m = INVOKE_RE.search(s)
            if m and missing(m.group(1)):
                hits[m.group(1)] = hits.get(m.group(1), 0) + 1
                new = "nop"
                # the result register (if any) must still hold a valid value
                j = i
                while j < n and (not lines[j].strip() or lines[j].strip().startswith((".line", "#"))):
                    j += 1
                if j < n:
                    mr = MOVE_RES_RE.match(lines[j].strip())
                    if mr:
                        lines[j] = lines[j][:len(lines[j]) - len(lines[j].lstrip())] + \
                            const_for(mr.group(2), mr.group(1) == "-wide")
        elif op in ("new-instance", "const-class", "new-array"):
            m = re.search(r",\s*\[*(L[\w/$]+;)\s*$", s)
            r = REG_RE.match(s)
            if m and r and missing(m.group(1)):
                hits[m.group(1)] = hits.get(m.group(1), 0) + 1
                new = const_for(r.group(1), False)
        elif op in ("check-cast",):
            m = re.search(r",\s*\[*(L[\w/$]+;)\s*$", s)
            if m and missing(m.group(1)):
                hits[m.group(1)] = hits.get(m.group(1), 0) + 1
                new = "nop"
        elif op == "instance-of":
            m = re.search(r",\s*\[*(L[\w/$]+;)\s*$", s)
            r = REG_RE.match(s)
            if m and r and missing(m.group(1)):
                hits[m.group(1)] = hits.get(m.group(1), 0) + 1
                new = const_for(r.group(1), False)
        elif op.startswith(("sget", "sput", "iget", "iput")):
            m = FIELD_RE.search(s)
            if m and missing(m.group(1)):
                hits[m.group(1)] = hits.get(m.group(1), 0) + 1
                if op.startswith(("sput", "iput")):
                    new = "nop"
                else:
                    r = REG_RE.match(s)
                    if r:
                        new = const_for(r.group(1), op.endswith("-wide"))

        if new is not None:
            lines[i - 1] = ind + new
            changed = True
            sites[0] += 1
    if changed:
        with open(path, "w", encoding="utf-8", errors="surrogateescape", newline="") as f:
            f.write("\n".join(lines))
    return changed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    ap.add_argument("--report")
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()

    defined = collect_defined(a.root)
    missing = make_checker(defined)
    hits, sites, files = {}, [0], 0
    for d in sorted(os.listdir(a.root)):
        if not d.startswith("smali"):
            continue
        for dp, _, fn in os.walk(os.path.join(a.root, d)):
            for x in fn:
                if not x.endswith(".smali"):
                    continue
                p = os.path.join(dp, x)
                if a.dry:
                    with open(p, "r", encoding="utf-8", errors="surrogateescape") as f:
                        t = f.read()
                    if any(missing(c) for c in set(DESC_RE.findall(t))):
                        files += 1
                elif fix_file(p, missing, hits, sites):
                    files += 1

    top = sorted(hits.items(), key=lambda kv: -kv[1])
    if not hits:
        print("  [classfix] no references to missing classes found.")
    else:
        verb = "would neutralize" if a.dry else "neutralized"
        print(f"  [classfix] {verb} {sites[0]} reference(s) to {len(hits)} missing class(es) in {files} file(s):")
        for c, k in top[:15]:
            print(f"  [classfix]   {c}  x{k}")
        if len(top) > 15:
            print(f"  [classfix]   ... and {len(top) - 15} more (see report)")
    if a.report:
        os.makedirs(os.path.dirname(os.path.abspath(a.report)), exist_ok=True)
        with open(a.report, "w") as fh:
            json.dump({"neutralized_sites": sites[0], "missing_classes": dict(top)}, fh, indent=2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
