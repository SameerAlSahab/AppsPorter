#!/usr/bin/env python3
#
#  Copyright (c) 2022 Sameer Al Sahab
#  Licensed under the MIT License. See LICENSE file for details.
#
#  AI smali reviewer for AppsPorter (extra, opt-in via --ai).
#
#  Looks at the smali classes that build_port.sh injected from the OEM
#  framework, sends them to an AI endpoint ONE METHOD PER REQUEST (many
#  requests in parallel) and asks:  "would this crash / get blocked on a
#  non-OEM device?".  The AI answers with tiny JSON:
#
#       {"s":"ok"}                                   -> leave the method alone
#       {"s":"fix","r":"why","m":"<new method>"}     -> replace the method
#
#  Every replacement is validated locally (same signature, registers in
#  range, labels resolve, balanced) before it touches a file, originals are
#  backed up, and the whole step is best-effort: if the endpoint is down the
#  build simply continues without AI.
#
#  stdlib only - no pip install needed.
#
#  Usage:
#     ai_smali.py review --root <decompiled_apk> --list <files.txt> [--backup DIR] [--dry]
#     ai_smali.py review --root <decompiled_apk> <file_or_dir> [...]
#     ai_smali.py restore --root <decompiled_apk> --backup DIR
#     ai_smali.py selftest
#
#  Env (all optional):
#     AI_API_URL       endpoint base url          (default https://sahabfreeai.vercel.app/)
#     AI_JOBS          parallel requests          (default 200)
#     AI_MAX_URL       max encoded url length     (default 8000)  [GET transport]
#     AI_TRANSPORT     get | post                 (default get)
#     AI_MAX_METHODS   max methods per run        (default 4000)
#     AI_MAX_MINUTES   hard time limit            (default 25)
#     AI_RETRIES       attempts per method        (default 4)
#     AI_TIMEOUT       seconds per request        (default 60)
#

import argparse
import concurrent.futures as cf
import json
import os
import random
import re
import shutil
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_API = "https://sahabfreeai.vercel.app/"
UA = "appsporter-ai/1.0"

SYSTEM_PROMPT = (
    "You are a senior Android smali/Dalvik reverse engineer. The smali method below was copied "
    "from an OEM framework (Xiaomi/Oppo/OnePlus/Samsung/Motorola/Any) into a third-party app that will "
    "run WITHOUT root on a different phone/ROM. Decide if it can crash the app or block it there: "
    "missing OEM services/system properties/hidden APIs, device-model/brand/ROM/region/"
    "Knox/root/permission/whitelist checks, isOPDevice, isSamsungDevice, isSupported and unlock flagship features if found."
    "DO NOT modify ordinary methods or unrelated application logic."
    "Most methods are fine - default to ok. Only change what is truly needed, with the smallest edit "
    "Ensure all smali registers are valid (e.g., v0, v1, p0). Never output malformed register names like 8x0."
    "(bypass the check branch, return a safe value, swallow the failing call). Keep the exact .method "
    "line, a valid .locals/.registers count, valid labels and the .end method. Reply ONLY with compact "
    "one-line JSON, no markdown: {\"s\":\"ok\"} if fine, else "
    "{\"s\":\"fix\",\"r\":\"short reason\",\"m\":\"full new method text, use \\n for newlines\"}."
)

# methods containing these are reviewed first when we have to cap the amount
SUSPICIOUS = re.compile(
    r"throw|SecurityException|UnsupportedOperation|SystemProperties|getprop|System;->exit|"
    r"killProcess|loadLibrary|Landroid/os/Build|checkPermission|checkCallingPermission|"
    r"[Ss]ignature|[Kk]nox|isSupport|isCompatible|[Ww]hitelist|[Aa]llowList|isRoot|"
    r"getSystemService|ServiceManager|SystemService|checkSelfPermission|Process;->",
)

STRING_RE = re.compile(r'"(?:[^"\\]|\\.)*"')
REG_RE = re.compile(r"(?<![\w/$;>.\-])([vp])(\d+)(?![\w/;$])")
LABEL_DEF_RE = re.compile(r"^:([A-Za-z_][\w$.\-]*)$")
LABEL_REF_RE = re.compile(r"(?:(?<=\s)|(?<=\{)):([A-Za-z_][\w$.\-]*)")

_print_lock = threading.Lock()


def log(msg, indent=2):
    with _print_lock:
        print(" " * indent + msg, flush=True)


# --------------------------------------------------------------------------
# smali parsing
# --------------------------------------------------------------------------
class Method:
    __slots__ = ("file", "start", "end", "cls", "header", "lines", "score", "prompt")

    def __init__(self, file, start, end, cls, header, lines):
        self.file, self.start, self.end = file, start, end
        self.cls, self.header, self.lines = cls, header, lines
        self.score = 0
        self.prompt = ""

    @property
    def sig(self):
        return self.header.split()[-1]

    @property
    def label(self):
        return f"{self.cls}->{self.sig}"


def read_lines(path):
    with open(path, "r", encoding="utf-8", errors="surrogateescape", newline="") as f:
        return f.read().split("\n")


def compact(lines):
    """Strip noise (blank lines, comments, indentation, debug directives)."""
    out = []
    for raw in lines:
        s = raw.strip()
        if not s or s.startswith("#"):
            continue
        if s.startswith((".line", ".prologue", ".epilogue", ".local ", ".end local", ".restart local")):
            continue
        out.append(s)
    return out


def instr_count(lines):
    return sum(1 for s in lines if not s.startswith((".", ":", "#")))


def parse_methods(path):
    lines = read_lines(path)
    cls = "?"
    for ln in lines[:10]:
        if ln.startswith(".class"):
            cls = ln.split()[-1]
            break
    methods, i = [], 0
    while i < len(lines):
        if lines[i].startswith(".method"):
            j = i
            while j < len(lines) and not lines[j].startswith(".end method"):
                j += 1
            if j < len(lines):
                body = compact(lines[i:j + 1])
                methods.append(Method(path, i, j, cls, lines[i].strip(), body))
                i = j
        i += 1
    return methods


# --------------------------------------------------------------------------
# validation of AI replacements
# --------------------------------------------------------------------------
def _split_sig(header):
    toks = header.split()
    mods, sig = toks[1:-1], toks[-1]
    m = re.match(r"^(.+?)\((.*)\)(.+)$", sig)
    return mods, sig, (m.groups() if m else None)


def _param_regs(params, is_static):
    n = 0 if is_static else 1
    i = 0
    while i < len(params):
        c = params[i]
        if c == "[":
            while i < len(params) and params[i] == "[":
                i += 1
            if i < len(params) and params[i] == "L":
                i = params.index(";", i) + 1
            else:
                i += 1
            n += 1
        elif c == "L":
            i = params.index(";", i) + 1
            n += 1
        elif c in "JD":
            n += 2
            i += 1
        else:
            n += 1
            i += 1
    return n


def validate(old: Method, new_text: str):
    """Return (ok, reason_or_cleaned_lines)."""
    lines = [l.strip() for l in new_text.replace("\r", "").split("\n")]
    lines = [l for l in lines if l and not l.startswith("#")]
    if not lines:
        return False, "empty method"
    if not lines[0].startswith(".method"):
        return False, "does not start with .method"
    if lines[-1] != ".end method":
        return False, "does not end with .end method"
    if sum(1 for l in lines if l.startswith(".method")) != 1 or \
       sum(1 for l in lines if l.startswith(".end method")) != 1:
        return False, "more than one method"

    old_mods, old_sig, old_parts = _split_sig(old.header)
    new_mods, new_sig, new_parts = _split_sig(lines[0])
    if not new_parts or new_sig != old_sig:
        return False, "signature changed"
    if set(new_mods) != set(old_mods):
        return False, "modifiers changed"

    is_static = "static" in new_mods
    params = _param_regs(new_parts[1], is_static)

    loc = None
    for l in lines:
        m = re.match(r"^\.locals\s+(\d+)$", l)
        if m:
            loc = int(m.group(1))
            break
        m = re.match(r"^\.registers\s+(\d+)$", l)
        if m:
            loc = int(m.group(1)) - params
            break
    is_abstract = "abstract" in new_mods or "native" in new_mods
    if loc is None and not is_abstract:
        return False, "missing .locals/.registers"
    if loc is not None and loc < 0:
        return False, "bad register count"

    defined, refs = set(), set()
    for l in lines[1:-1]:
        if STRING_RE.sub('""', l).count('"') % 2:
            return False, "unbalanced string quote"
        m = LABEL_DEF_RE.match(l)
        if m:
            defined.add(m.group(1))
            continue
    bare = STRING_RE.sub('""', l)
        if not bare.startswith("."):
            # Catch common LLM typos where hex/registers get mangled into things like 8x0, 0xv0, etc.
            if re.search(r"\b\d+x\d+\b", bare) or re.search(r"\bv[vx]\b", bare):
                return False, f"malformed register/token typo: {l}"

            for kind, num in REG_RE.findall(bare):
                n = int(num)
                if kind == "v" and loc is not None and n >= loc:
                    return False, f"register v{n} out of range (.locals {loc})"
                if kind == "p" and n >= params:
                    return False, f"register p{n} out of range ({params} params)"
        refs.update(LABEL_REF_RE.findall(bare))
    missing = refs - defined
    if missing:
        return False, f"undefined label(s): {', '.join(sorted(missing)[:3])}"

    if len(lines) > len(old.lines) * 4 + 40:
        return False, "replacement suspiciously large"
    if lines == old.lines:
        return True, None  # identical -> treated as "ok"
    return True, lines


# --------------------------------------------------------------------------
# talking to the AI endpoint
# --------------------------------------------------------------------------
class Cfg:
    api = DEFAULT_API
    transport = "get"
    timeout = 60
    retries = 4
    max_url = 8000


class RetryableError(Exception):
    pass


def build_prompt(m: Method):
    return SYSTEM_PROMPT + "\nClass: " + m.cls + "\n" + "\n".join(m.lines)


def encode_len(prompt):
    return len(Cfg.api.rstrip("/")) + 1 + len(urllib.parse.quote(prompt, safe=""))


def call_ai(prompt):
    if Cfg.transport == "post":
        req = urllib.request.Request(
            Cfg.api, data=json.dumps({"prompt": prompt}).encode("utf-8"),
            headers={"Content-Type": "application/json", "User-Agent": UA}, method="POST")
    else:
        url = Cfg.api.rstrip("/") + "/" + urllib.parse.quote(prompt, safe="")
        req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=Cfg.timeout) as r:
            body = r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        if e.code in (408, 425, 429) or e.code >= 500:
            wait = 0
            try:
                wait = min(30, float(e.headers.get("Retry-After", 0)))
            except (TypeError, ValueError):
                pass
            raise RetryableError(f"http {e.code}", wait)
        raise
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise RetryableError(f"network: {e}", 0)
    try:
        return json.loads(body)
    except ValueError:
        raise RetryableError("response is not json", 0)


def _norm(s):
    return re.sub(r"\s+", " ", s or "").strip()


def parse_decision(answer):
    """-> ('ok', None, None) | ('fix', method_text, reason). Raises ValueError."""
    if isinstance(answer, dict):
        obj = answer
    else:
        t = str(answer).strip()
        t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t, flags=re.I).strip()
        try:
            obj = json.loads(t, strict=False)
        except ValueError:
            i, j = t.find("{"), t.rfind("}")
            if i < 0 or j <= i:
                raise ValueError("no json in answer")
            obj = json.loads(t[i:j + 1], strict=False)
    if not isinstance(obj, dict):
        raise ValueError("answer is not an object")
    s = str(obj.get("s") or obj.get("status") or "").lower()
    if s in ("ok", "fine", "none", "no_change", "nochange", "no-change"):
        return "ok", None, None
    if s in ("fix", "modify", "change", "patch", "modified"):
        m = obj.get("m") or obj.get("method") or ""
        if "\n" not in m and "\\n" in m:
            m = m.replace("\\n", "\n")
        if not m.strip():
            raise ValueError("fix without method")
        return "fix", m, str(obj.get("r") or obj.get("reason") or "")[:160]
    raise ValueError("unknown status")


def ask(prompt):
    """One request with retry/backoff. Returns decision tuple or raises."""
    last = None
    for attempt in range(Cfg.retries):
        try:
            data = call_ai(prompt)
            echo = data.get("prompt") if isinstance(data, dict) else None
            if echo is not None and _norm(echo) != _norm(prompt):
                return ("mangled", None, "prompt was altered in transit")
            ans = data.get("answer") if isinstance(data, dict) else None
            if ans is None:
                raise ValueError("no answer field")
            return parse_decision(ans)
        except RetryableError as e:
            last, wait = e.args[0], (e.args[1] if len(e.args) > 1 else 0)
        except ValueError as e:
            last, wait = f"bad answer: {e}", 0
        except urllib.error.HTTPError as e:
            return ("failed", None, f"http {e.code}")
        time.sleep(max(wait, min(20, (2 ** attempt) + random.random() * 2)))
    return ("failed", None, last or "unknown")


# --------------------------------------------------------------------------
# preflight
# --------------------------------------------------------------------------
def preflight():
    probe = ("Reply ONLY with this JSON: {\"s\":\"ok\"}\n"
             ".method public static t(Ljava/lang/String;)V\n.locals 1\n"
             "const-string v0, \"a/b {c} $d;<e>\"\nreturn-void\n.end method")
    for attempt in range(2):
        try:
            data = call_ai(probe)
            if not isinstance(data, dict) or "answer" not in data:
                return False, "endpoint did not return an 'answer' field"
            echo = data.get("prompt")
            if echo is not None and _norm(echo) != _norm(probe):
                return False, "endpoint alters smali characters (/ { } $ ; < >) - refusing to trust it"
            return True, "ok"
        except RetryableError as e:
            err = e.args[0]
            time.sleep(2)
        except urllib.error.HTTPError as e:
            return False, f"http {e.code}"
        except ValueError as e:
            err = str(e)
    return False, f"endpoint unreachable ({err})"


# --------------------------------------------------------------------------
# review
# --------------------------------------------------------------------------
def collect_files(paths, list_file):
    files = []
    if list_file and os.path.isfile(list_file):
        with open(list_file) as f:
            files += [l.strip() for l in f if l.strip()]
    for p in paths:
        if os.path.isdir(p):
            for dp, _, fn in os.walk(p):
                files += [os.path.join(dp, x) for x in fn if x.endswith(".smali")]
        elif os.path.isfile(p):
            files.append(p)
    seen, out = set(), []
    for f in files:
        if f not in seen and os.path.isfile(f) and f.endswith(".smali"):
            seen.add(f)
            out.append(f)
    return out


def review(args):
    jobs = args.jobs
    deadline = time.time() + args.max_minutes * 60
    files = collect_files(args.paths, args.list)
    if not files:
        log("[AI] No injected smali files to review - skipping.")
        return 0

    all_methods, trivial, no_code = [], 0, 0
    for f in files:
        for m in parse_methods(f):
            has_code = any(l.startswith((".locals", ".registers")) for l in m.lines)
            if not has_code:
                no_code += 1
                continue
            body = "\n".join(m.lines)
            if not args.all and instr_count(m.lines) <= 3 and "throw" not in body:
                trivial += 1
                continue
            m.score = len(SUSPICIOUS.findall(body))
            all_methods.append(m)

    all_methods.sort(key=lambda m: -m.score)
    capped = max(0, len(all_methods) - args.max_methods)
    all_methods = all_methods[:args.max_methods]

    work, too_big = [], 0
    for m in all_methods:
        m.prompt = build_prompt(m)
        if Cfg.transport == "get" and encode_len(m.prompt) > Cfg.max_url:
            too_big += 1
            continue
        work.append(m)

    log(f"[AI] {len(files)} injected file(s): {len(work)} method(s) queued "
        f"(skipped: {trivial} trivial, {no_code} abstract/native, {too_big} too large, {capped} over cap)")
    if not work:
        return 0

    ok, why = preflight()
    if not ok:
        log(f"[AI] AI endpoint not usable: {why}. Continuing build WITHOUT AI review.")
        return 0

    log(f"[AI] Reviewing with {min(jobs, len(work))} parallel jobs...")
    stats = {"ok": 0, "fix": 0, "rejected": 0, "failed": 0, "mangled": 0, "skipped": 0}
    results = []  # (method, new_lines, reason)
    lock = threading.Lock()
    stop = threading.Event()
    done = [0]
    t0 = time.time()
    step = max(1, len(work) // 10)

    def job(m):
        if stop.is_set() or time.time() > deadline:
            return m, "skipped", None, "time limit / aborted"
        kind, text, reason = ask(m.prompt)
        if kind == "fix":
            good, res = validate(m, text)
            if not good:
                return m, "rejected", None, res
            if res is None:
                return m, "ok", None, None
            return m, "fix", res, reason
        return m, kind, None, reason

    with cf.ThreadPoolExecutor(max_workers=max(1, min(jobs, len(work)))) as ex:
        futs = [ex.submit(job, m) for m in work]
        for fu in cf.as_completed(futs):
            m, kind, new_lines, reason = fu.result()
            with lock:
                stats[kind] = stats.get(kind, 0) + 1
                done[0] += 1
                if kind == "fix":
                    results.append((m, new_lines, reason))
                if kind in ("fix", "rejected"):
                    tag = "proposed" if args.dry and kind == "fix" else ("patched" if kind == "fix" else "rejected")
                    log(f"[AI]   {tag}: {m.label}" + (f"  ({reason})" if reason else ""), 2)
                if stats["ok"] + stats["fix"] + stats["rejected"] == 0 and stats["failed"] >= 20:
                    if not stop.is_set():
                        log("[AI] Endpoint keeps failing - aborting the rest, build continues without AI.")
                    stop.set()
                if done[0] % step == 0 or done[0] == len(work):
                    log(f"[AI] progress {done[0]}/{len(work)}  ({int(time.time() - t0)}s)")

    # apply (reverse order per file so line indexes stay valid)
    applied = 0
    if results and not args.dry:
        by_file = {}
        for m, nl, r in results:
            by_file.setdefault(m.file, []).append((m, nl))
        for f, items in by_file.items():
            rel = os.path.relpath(f, args.root)
            if args.backup:
                dst = os.path.join(args.backup, rel)
                if not os.path.exists(dst):
                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                    shutil.copy2(f, dst)
            lines = read_lines(f)
            for m, nl in sorted(items, key=lambda x: -x[0].start):
                body = [nl[0]] + ["    " + l for l in nl[1:-1]] + [nl[-1]]
                lines[m.start:m.end + 1] = body
                applied += 1
            with open(f, "w", encoding="utf-8", errors="surrogateescape", newline="") as fh:
                fh.write("\n".join(lines))

    if args.report:
        os.makedirs(os.path.dirname(os.path.abspath(args.report)), exist_ok=True)
        with open(args.report, "w") as fh:
            json.dump({
                "dry_run": args.dry, "api": Cfg.api, "stats": stats, "applied": applied,
                "changes": [{"method": m.label, "file": os.path.relpath(m.file, args.root), "reason": r}
                            for m, _, r in results],
            }, fh, indent=2)

    verb = "would patch" if args.dry else "patched"
    log(f"[AI] Done in {int(time.time() - t0)}s: {stats['ok']} fine, {len(results) if args.dry else applied} {verb}, "
        f"{stats['rejected']} rejected (invalid), {stats['failed'] + stats['mangled']} failed, {stats['skipped']} skipped.")
    return 0


def restore(args):
    n = 0
    if args.backup and os.path.isdir(args.backup):
        for dp, _, fn in os.walk(args.backup):
            for x in fn:
                src = os.path.join(dp, x)
                dst = os.path.join(args.root, os.path.relpath(src, args.backup))
                shutil.copy2(src, dst)
                n += 1
    log(f"[AI] Restored {n} file(s) to their original state.")
    return 0


def selftest(_args):
    ok, why = preflight()
    log(f"[AI] endpoint {Cfg.api}: {'OK' if ok else 'NOT OK - ' + why}")
    return 0 if ok else 1


def main():
    env = os.environ.get
    ap = argparse.ArgumentParser(description="AppsPorter AI smali reviewer")
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("review")
    r.add_argument("paths", nargs="*")
    r.add_argument("--root", required=True)
    r.add_argument("--list")
    r.add_argument("--backup")
    r.add_argument("--report")
    r.add_argument("--dry", action="store_true")
    r.add_argument("--all", action="store_true", help="also send trivial methods")
    r.add_argument("--jobs", type=int, default=int(env("AI_JOBS") or 200))
    r.add_argument("--max-methods", type=int, default=int(env("AI_MAX_METHODS") or 4000))
    r.add_argument("--max-minutes", type=float, default=float(env("AI_MAX_MINUTES") or 25))

    s = sub.add_parser("restore")
    s.add_argument("--root", required=True)
    s.add_argument("--backup", required=True)

    sub.add_parser("selftest")

    a = ap.parse_args()
    Cfg.api = env("AI_API_URL") or DEFAULT_API
    Cfg.transport = (env("AI_TRANSPORT") or "get").lower()
    Cfg.timeout = int(env("AI_TIMEOUT") or 60)
    Cfg.retries = max(1, int(env("AI_RETRIES") or 4))
    Cfg.max_url = int(env("AI_MAX_URL") or 8000)

    try:
        return {"review": review, "restore": restore, "selftest": selftest}[a.cmd](a)
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
