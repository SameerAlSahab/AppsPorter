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
#     ai_smali.py repair  --root <decompiled_apk> --backup DIR --log <apktool_output.txt> [--attempt N]
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
#     AI_FIX_TRIES     answers per broken method  (default 3)   [repair]
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
    "Knox/root/permission/whitelist checks, isOPDevice, isSamsungDevice, isSupported and unlock flagship features if found. "
    "DO NOT modify ordinary methods or unrelated application logic. "
    "Classes under miui/oplus/oppo/samsung/moto packages are ALREADY copied into the app: never treat them as missing and never replace them. "
    "FALLBACK: if the method uses a class or member that cannot exist on a normal device (not android/java/kotlin and not one of those copied OEM classes), "
    "NEUTRALIZE only that call: delete the invoke/new-instance/sget, and if a move-result follows, replace it with a safe default "
    "(const/4 vN, 0x0 for int/boolean/null) so the method still returns a valid value. Never leave the missing class referenced. "
    "Most methods are fine - default to ok. Only change what is truly needed, with the smallest edit "
    "(bypass the check branch, return a safe value, swallow the failing call). "
    "Registers are only v0.. or p0..; a literal like 0x0 is valid ONLY where a literal belongs, never in a register slot. "
    "pN is register .locals+N, and 4-bit opcodes (invoke without /range, move, const/4, if-*, iget/iput) can only reach v0-v15, "
    "so do NOT raise .locals. Keep the exact .method "
    "line, a valid .locals/.registers count, valid labels and the .end method. Reply ONLY with compact "
    "one-line JSON, no markdown: {\"s\":\"ok\"} if fine, else "
    "{\"s\":\"fix\",\"r\":\"short reason\",\"m\":\"full new method text, use \\n for newlines\"}."
)

# methods containing these are reviewed first when we have to cap the amount
SUSPICIOUS = re.compile(
    r"SecurityException|UnsupportedOperation|SystemProperties|getprop|System;->exit|"
    r"killProcess|loadLibrary|Landroid/os/Build|checkPermission|checkCallingPermission|"
    r"(?<!annotation/)[Ss]ignature|[Kk]nox|isSupport|isCompatible|[Ww]hitelist|[Aa]llowList|isRoot|"
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


# --------------------------------------------------------------------------
# instruction level checks (what apktool/smali would reject at build time)
# --------------------------------------------------------------------------
REG_TOKEN_RE = re.compile(r"^([vp])(\d+)$")
OPCODE_RE = re.compile(r"^[a-z][a-z0-9\-/]*$")
LIT_RE = re.compile(r"^-?(?:0x[0-9a-fA-F]+|\d+(?:\.\d*)?(?:[eE][+-]?\d+)?)[A-Za-z]?$|^-?(?:Infinity|NaN)[fF]?$")
HEX_RE = re.compile(r"(?<![\w$/;>.\-])-?0x[0-9a-fA-F]+[A-Za-z]?(?![\w])")
_BIN_RE = re.compile(r"^(add|sub|mul|div|rem|and|or|xor|shl|shr|ushr)-(int|long|float|double)(/2addr|/lit16|/lit8)?$")
_UN_RE = re.compile(r"^((neg|not)-(int|long|float|double)|(int|long|float|double)-to-(int|long|float|double|byte|char|short))$")
_MOVE_RE = re.compile(r"^move(-wide|-object)?(/from16|/16)?$")
_CONST_RE = re.compile(r"^const(/4|/16|/high16)?$|^const-wide(/16|/32|/high16)?$")


def reg_spec(op):
    """Bit width of every leading register operand of an opcode.

    Returns (widths, literal_after) or None when the opcode is unknown (then it is not checked).
    """
    if op in ("nop", "return-void", "return-void-no-barrier") or op.startswith("goto"):
        return (), False
    m = _MOVE_RE.match(op)
    if m:
        return {None: (4, 4), "/from16": (8, 16), "/16": (16, 16)}[m.group(2)], False
    if op.startswith("move-result") or op == "move-exception":
        return (8,), False
    if op in ("return", "return-wide", "return-object"):
        return (8,), False
    if op == "const/4":
        return (4,), True
    if _CONST_RE.match(op):
        return (8,), True
    if op.startswith(("const-string", "const-class", "const-method")):
        return (8,), False
    if op in ("monitor-enter", "monitor-exit", "check-cast", "new-instance", "throw", "fill-array-data",
              "packed-switch", "sparse-switch"):
        return (8,), False
    if op in ("instance-of", "array-length", "new-array"):
        return (4, 4), False
    if op in ("cmpl-float", "cmpg-float", "cmpl-double", "cmpg-double", "cmp-long"):
        return (8, 8, 8), False
    if op.startswith("if-"):
        return ((8,) if op.endswith("z") else (4, 4)), False
    if op.startswith(("aget", "aput")):
        return (8, 8, 8), False
    if op.startswith(("iget", "iput")):
        return (4, 4), False
    if op.startswith(("sget", "sput")):
        return (8,), False
    if op == "rsub-int":
        return (4, 4), True
    if op == "rsub-int/lit8":
        return (8, 8), True
    m = _BIN_RE.match(op)
    if m:
        return {None: (8, 8, 8), "/2addr": (4, 4), "/lit16": (4, 4), "/lit8": (8, 8)}[m.group(3)], \
            m.group(3) in ("/lit16", "/lit8")
    if _UN_RE.match(op):
        return (4, 4), False
    return None


def _split_top(text):
    """Split on commas that are not inside { } (strings are already masked)."""
    out, depth, cur = [], 0, ""
    for ch in text:
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
        if ch == "," and depth == 0:
            out.append(cur.strip())
            cur = ""
        else:
            cur += ch
    if cur.strip():
        out.append(cur.strip())
    return out


def _real_reg(tok, loc):
    """'v3' -> 3, 'p1' -> loc+1 (the number the assembler really encodes). None if not a register."""
    m = REG_TOKEN_RE.match(tok)
    if not m:
        return None
    n = int(m.group(2))
    return n if m.group(1) == "v" else (n + loc if loc is not None else n)


def check_instr(line, loc, params):
    """Return an error string or None for one (string-masked) instruction line."""
    parts = line.split(None, 1)
    op = parts[0]
    rest = parts[1] if len(parts) > 1 else ""

    if op.startswith(("invoke-", "filled-new-array")):
        m = re.match(r"^\{([^}]*)\}", rest)
        if not m:
            return f"{op}: missing {{register list}}"
        inner = m.group(1).strip()
        if ".." in inner:  # range form
            ends = [t.strip() for t in inner.split("..")]
            nums = [_real_reg(t, loc) for t in ends]
            if len(ends) != 2 or None in nums:
                return f"{op}: bad register range '{{{inner}}}'"
            if nums[0] > nums[1] or max(nums) > 65535:
                return f"{op}: register range out of bounds '{{{inner}}}'"
            if not op.endswith("/range"):
                return f"{op}: range list needs the /range form"
            return None
        toks = [t.strip() for t in inner.split(",")] if inner else []
        if op.endswith("/range") and toks:
            return f"{op}: /range needs '{{vX .. vY}}'"
        if len(toks) > 5:
            return f"{op}: more than 5 registers, use the /range form"
        for t in toks:
            n = _real_reg(t, loc)
            if n is None:
                return f"{op}: '{t}' is not a register"
            if n > 15:
                return f"{op}: register {t} is v{n}, non-range invoke only reaches v0-v15"
        return None

    spec = reg_spec(op)
    if spec is None:
        return None
    widths, lit_after = spec
    ops = _split_top(rest)
    if len(ops) < len(widths):
        return f"{op}: expected {len(widths)} register operand(s), got {len(ops)}"
    for tok, bits in zip(ops, widths):
        n = _real_reg(tok, loc)
        if n is None:
            return f"{op}: '{tok}' is in a register position but is not a register"
        if n >= (1 << bits):
            return f"{op}: register {tok} is v{n}, this opcode only reaches v0-v{(1 << bits) - 1}"
    if lit_after:
        if len(ops) != len(widths) + 1:
            return f"{op}: expected a literal after the registers"
        lit = ops[-1]
        if not LIT_RE.match(lit):
            return f"{op}: malformed literal '{lit}'"
        try:
            val = int(lit.rstrip("LlSsTt"), 0)
            if op == "const/4" and not -8 <= val <= 7:
                return f"const/4 literal {lit} out of range, use const/16"
            if op in ("const/16", "const-wide/16") and not -32768 <= val <= 32767:
                return f"{op} literal {lit} out of range"
        except ValueError:
            pass
    return None


def check_body(lines, loc, params):
    """Validate a whole method (lines = stripped, comment-free, '.method' .. '.end method').

    Returns an error string or None.
    """
    defined, refs = set(), set()
    payload = False
    for l in lines[1:-1]:
        masked = STRING_RE.sub('""', l)
        if masked.count('"') % 2:
            return "unbalanced string quote"
        masked = re.sub(r"\s+#.*$", "", masked)  # trailing comments, e.g. 'const/high16 v0, 0x3f800000    # 1.0f'
        m = LABEL_DEF_RE.match(l)
        if m:
            defined.add(m.group(1))
            continue
        if re.match(r"^\.(array-data|packed-switch|sparse-switch)\b", l):
            payload = True
        elif l.startswith(".end ") and l.split()[1] in ("array-data", "packed-switch", "sparse-switch"):
            payload = False
            continue
        if payload:
            refs.update(LABEL_REF_RE.findall(masked))
            continue
        if masked.startswith("."):
            continue
        # hex literals (0x0, -0x1, 0x7fffL) are fine; whatever 'x' garbage is left over is not (8x0, 0xv0 ...)
        stripped = HEX_RE.sub("0", masked)
        core = re.sub(r"->\S+", "->", re.sub(r"L[^;\s]*;", "L;", stripped))  # ignore class/member names
        if re.search(r"(?<![\w$])\d+x\w*", core) or re.search(r"(?<![\w$])v[vx](?![\w$])", core) or "0xv" in core:
            return f"malformed register/token typo: {l}"
        for kind, num in REG_RE.findall(stripped):
            n = int(num)
            if kind == "v" and loc is not None and n >= loc:
                return f"register v{n} out of range (.locals {loc})"
            if kind == "p" and n >= params:
                return f"register p{n} out of range ({params} params)"
        op = masked.split(None, 1)[0]
        if OPCODE_RE.match(op):
            err = check_instr(masked, loc, params)
            if err:
                return f"{err}  [{l}]"
        refs.update(LABEL_REF_RE.findall(masked))
    missing = refs - defined
    if missing:
        return f"undefined label(s): {', '.join(sorted(missing)[:3])}"
    return None


def method_ctx(lines):
    """-> (loc, params) from a method's own lines (loc None if abstract/native)."""
    mods, _sig, parts = _split_sig(lines[0])
    params = _param_regs(parts[1], "static" in mods) if parts else 0
    for l in lines:
        m = re.match(r"^\.locals\s+(\d+)$", l)
        if m:
            return int(m.group(1)), params
        m = re.match(r"^\.registers\s+(\d+)$", l)
        if m:
            return int(m.group(1)) - params, params
    return None, params


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

    err = check_body(lines, loc, params)
    if err:
        return False, err

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
            if not args.all and m.score == 0:
                trivial += 1  # no crash/check signal -> not worth an AI call (and cannot be "improved")
                continue
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


# --------------------------------------------------------------------------
# build-failure repair: apktool failed -> send the error log to the AI, it points at the file,
# we hand it the broken method(s) and it fixes them (the shell loop rebuilds, up to N rounds)
# --------------------------------------------------------------------------
LOCATE_PROMPT = (
    "Below is an apktool/smali build error log. Reply ONLY with one-line JSON "
    "{\"files\":[\"path/of/file.smali\"]} naming the smali file(s) that failed to assemble, exactly as written in the log."
)

REPAIR_PROMPT = (
    "You are a senior Android smali/Dalvik engineer. The smali method below FAILS to assemble with apktool. "
    "Fix ONLY what the error says, with the smallest edit; keep the exact .method line, the behaviour and every other line. "
    "Registers are only v0.. or p0..; a literal like 0x0 never goes in a register slot; pN is register .locals+N; "
    "4-bit opcodes (non-range invoke, move, const/4, if-*, iget/iput, instance-of, new-array) only reach v0-v15: "
    "use move/from16, const/16, invoke-*/range or copy the value into a low register, do NOT raise .locals. "
    "Reply ONLY with one-line JSON: {\"s\":\"fix\",\"r\":\"short reason\",\"m\":\"full new method text, use \\n for newlines\"}."
)

ERR_AT_RE = re.compile(r"(\S+?\.smali)\[(\d+),(\d+)\]\s*(.*)")
ERR_FILE_RE = re.compile(r"Could not smali file:\s*(\S+\.smali)")


def resolve_smali(root, token):
    """Map a path from the log (relative to cwd, to the apk dir, or class-relative) to a real file."""
    token = token.strip().strip("'\"")
    if not token:
        return None
    if os.path.isfile(token):
        return os.path.abspath(token)
    m = re.search(r"(smali(?:_classes\d+)?/.+\.smali)$", token)
    if m and os.path.isfile(os.path.join(root, m.group(1))):
        return os.path.join(root, m.group(1))
    rel = token.lstrip("/")
    try:
        dirs = sorted(d for d in os.listdir(root) if d.startswith("smali"))
    except OSError:
        return None
    for d in dirs:
        cand = os.path.join(root, d, rel)
        if os.path.isfile(cand):
            return cand
    return None


def parse_build_errors(log_text, root):
    """-> {file: [(line, col, message), ...]} for every smali error found in an apktool log."""
    found = {}
    for raw in log_text.splitlines():
        m = ERR_AT_RE.search(raw)
        if m:
            f = resolve_smali(root, m.group(1))
            if f:
                found.setdefault(f, []).append((int(m.group(2)), int(m.group(3)), m.group(4).strip()))
            continue
        m = ERR_FILE_RE.search(raw)
        if m:
            f = resolve_smali(root, m.group(1))
            if f:
                found.setdefault(f, [])
    return found


def _loads_answer(ans):
    if isinstance(ans, dict):
        return ans
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", str(ans).strip(), flags=re.I).strip()
    try:
        return json.loads(t, strict=False)
    except ValueError:
        i, j = t.find("{"), t.rfind("}")
        if i < 0 or j <= i:
            raise ValueError("no json in answer")
        return json.loads(t[i:j + 1], strict=False)


def ask_json(prompt):
    """Like ask() but returns the raw JSON object of the answer (None on failure)."""
    for attempt in range(Cfg.retries):
        try:
            data = call_ai(prompt)
            ans = data.get("answer") if isinstance(data, dict) else None
            if ans is None:
                raise ValueError("no answer field")
            return _loads_answer(ans)
        except RetryableError as e:
            wait = e.args[1] if len(e.args) > 1 else 0
        except ValueError:
            wait = 0
        except urllib.error.HTTPError:
            return None
        time.sleep(max(wait, min(20, (2 ** attempt) + random.random() * 2)))
    return None


def ai_locate(log_text, root):
    """No file could be read out of the log locally: let the AI point at it."""
    keep = [l.strip()[:300] for l in log_text.splitlines()
            if re.search(r"smali|Error|Invalid|mismatched|Could not", l) and not l.strip().startswith("at ")]
    excerpt = "\n".join(keep[-10:])[:1800]
    if not excerpt:
        return []
    obj = ask_json(LOCATE_PROMPT + "\n" + excerpt)
    if not isinstance(obj, dict):
        return []
    names = obj.get("files") or obj.get("file") or []
    if isinstance(names, str):
        names = [names]
    out = []
    for n in names:
        f = resolve_smali(root, str(n))
        if f and f not in out:
            out.append(f)
    return out


def build_repair_prompt(m, msgs, failing, feedback):
    head = REPAIR_PROMPT + "\nError: " + "; ".join(sorted(set(msgs)))[:300]
    if failing:
        head += "\nFailing line: " + failing[:200]
    if feedback:
        head += "\nYour previous answer was rejected: " + feedback[:200]
    return head + "\nClass: " + m.cls + "\n" + "\n".join(m.lines)


def fix_method(m, msgs, failing, tries):
    """Ask the AI up to `tries` times, feeding back why its last answer was no good. -> (lines|None, why)"""
    feedback = None
    for _ in range(max(1, tries)):
        prompt = build_repair_prompt(m, msgs, failing, feedback)
        if Cfg.transport == "get" and encode_len(prompt) > Cfg.max_url:
            return None, "method too large for the GET endpoint (try AI_TRANSPORT=post)"
        kind, text, reason = ask(prompt)
        if kind in ("failed", "mangled"):
            return None, f"endpoint: {reason}"
        if kind != "fix":
            feedback = "the assembler still fails, you must change the method"
            continue
        good, res = validate(m, text)
        if not good:
            feedback = res
            continue
        if res is None:
            feedback = "your answer is identical to the input, change it"
            continue
        return res, reason
    return None, feedback or "no valid fix"


def repair(args):
    try:
        with open(args.log, "r", encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except OSError as e:
        log(f"[AI] repair: cannot read build log: {e}")
        return 3

    ok, why = preflight()
    if not ok:
        log(f"[AI] repair: AI endpoint not usable: {why}.")
        return 3

    errors = parse_build_errors(text, args.root)
    how = "from the build log"
    if not errors:
        how = "by asking the AI"
        errors = {f: [] for f in ai_locate(text, args.root)}
    if not errors:
        log("[AI] repair: could not tell which smali file failed.")
        return 3
    log(f"[AI] Build attempt {args.attempt}/{args.max_attempts}: {len(errors)} broken file(s) found {how}.")

    # collect the broken methods of every file
    targets = []  # (file, method, [messages], failing_line_text)
    for f, errs in errors.items():
        lines = read_lines(f)
        methods = parse_methods(f)
        per = {}
        for line, _col, msg in errs:
            for m in methods:
                if m.start <= line - 1 <= m.end:
                    per.setdefault(id(m), (m, [], lines[line - 1].strip()))[1].append(msg)
                    break
        for m in methods:  # also anything our own assembler-rules lint can see
            ctx = method_ctx(m.lines)
            if ctx[0] is None or id(m) in per:
                continue
            err = check_body(m.lines, ctx[0], ctx[1])
            if err:
                per[id(m)] = (m, [err], "")
        if not per:
            log(f"[AI]   {os.path.relpath(f, args.root)}: error is outside any method, cannot repair.")
        for m, msgs, failing in per.values():
            targets.append((f, m, msgs, failing))

    results = {}  # file -> {"fixes": [(m, lines)], "failed": [why]}
    for f in errors:
        results[f] = {"fixes": [], "failed": []}
    with cf.ThreadPoolExecutor(max_workers=max(1, min(args.jobs, len(targets) or 1))) as ex:
        futs = {ex.submit(fix_method, m, msgs, failing, args.tries): (f, m) for f, m, msgs, failing in targets}
        for fu in cf.as_completed(futs):
            f, m = futs[fu]
            new_lines, why = fu.result()
            if new_lines:
                results[f]["fixes"].append((m, new_lines))
                log(f"[AI]   repaired: {m.label}  ({why})" if why else f"[AI]   repaired: {m.label}")
            else:
                results[f]["failed"].append(why)
                log(f"[AI]   could not repair: {m.label}  ({why})")

    changed = 0
    for f, res in results.items():
        rel = os.path.relpath(f, args.root)
        bak = os.path.join(args.backup, rel)
        if res["failed"] or not res["fixes"]:
            # AI could not fix it -> put the file back to the original injected version if we have one
            if os.path.isfile(bak):
                shutil.copy2(bak, f)
                log(f"[AI]   reverted {rel} to its original (AI could not repair it).")
                changed += 1
            else:
                log(f"[AI]   {rel}: not changed by AI earlier and could not be repaired.")
            continue
        if not os.path.exists(bak):
            os.makedirs(os.path.dirname(bak), exist_ok=True)
            shutil.copy2(f, bak)
        lines = read_lines(f)
        for m, nl in sorted(res["fixes"], key=lambda x: -x[0].start):
            lines[m.start:m.end + 1] = [nl[0]] + ["    " + l for l in nl[1:-1]] + [nl[-1]]
        with open(f, "w", encoding="utf-8", errors="surrogateescape", newline="") as fh:
            fh.write("\n".join(lines))
        changed += 1
    log(f"[AI] Repair round {args.attempt}: {changed} file(s) changed" + ("" if changed else " - nothing to rebuild."))
    return 0 if changed else 3


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

    f = sub.add_parser("repair", help="apktool failed: fix the smali files named in the build log")
    f.add_argument("--root", required=True)
    f.add_argument("--backup", required=True)
    f.add_argument("--log", required=True, help="captured apktool output")
    f.add_argument("--attempt", type=int, default=1)
    f.add_argument("--max-attempts", type=int, default=3)
    f.add_argument("--tries", type=int, default=int(env("AI_FIX_TRIES") or 3),
                   help="AI answers per broken method before giving up on it")
    f.add_argument("--jobs", type=int, default=int(env("AI_JOBS") or 200))

    sub.add_parser("selftest")

    a = ap.parse_args()
    Cfg.api = env("AI_API_URL") or DEFAULT_API
    Cfg.transport = (env("AI_TRANSPORT") or "get").lower()
    Cfg.timeout = int(env("AI_TIMEOUT") or 60)
    Cfg.retries = max(1, int(env("AI_RETRIES") or 4))
    Cfg.max_url = int(env("AI_MAX_URL") or 8000)

    try:
        return {"review": review, "restore": restore, "repair": repair, "selftest": selftest}[a.cmd](a)
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
