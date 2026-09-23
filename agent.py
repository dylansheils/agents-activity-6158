#!/usr/bin/env python3
"""Skeleton for the Python -> Rust translation agent.

    python agent.py                  # run with defaults
    python agent.py --budget 40      # cap on model calls (graded: do not raise)

WHAT IS GIVEN
  * the loop
  * six working tools
  * trajectory logging

WHAT YOU FILL IN   (search for "TODO")
  1. call_model()      - talk to whichever model you have access to
  2. system_prompt()   - what the agent is told about its job
  3. build_context()   - CONTEXT MANAGEMENT. The hard one.
  4. should_stop()     - TERMINATION. The one everyone forgets.
  5. the tool set      - add, remove, or reshape tools. This matters more
                         than you expect; see README.

The skeleton runs as given and accomplishes nothing. That is intentional.
"""
from __future__ import annotations
import argparse, ast, json, os, pathlib, re, subprocess, time, sys, urllib.request

HERE   = pathlib.Path(__file__).parent
RUST   = HERE / "rust"
LIB    = RUST / "src" / "lib.rs"
PYSRC  = HERE / "reference" / "version.py"
LOGS   = HERE / "logs"
BIN    = RUST / "target" / "release" / "harness"
MODEL  = os.environ.get("LLM_MODEL", "openai/gpt-oss-120b")
STATE  = {"best": None, "last": "", "ok": False, "stale": 0, "claims": 0, "flags": [], "summary": "", "upto": 2, "compressions": 0}

# ============================================================== TODO 1
def call_model(messages: list[dict], tools: list[dict]) -> dict:
    """Send `messages` + `tools` to a model; return its reply.

    Return shape expected by the loop below:
        {"text": str | None,
         "tool_calls": [{"name": str, "arguments": dict}, ...]}

    Any provider works. Keep the return shape and the loop needs no changes.
    Read your key from the environment - do not hard-code it, you will be
    committing this file.
    """
    body = {"model": MODEL, "messages": messages, "max_tokens": 32768,
            "provider": {"order": ["cerebras"], "allow_fallbacks": False}}
    if tools:
        body["tools"] = [{"type": "function", "function": t} for t in tools]
    req = urllib.request.Request("https://openrouter.ai/api/v1/chat/completions", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json",
                                          "Authorization": f"Bearer {os.environ['OPENROUTER_API_KEY']}"})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=600) as r:
                msg = json.loads(r.read())["choices"][0]["message"]
            break
        except (OSError, KeyError):
            if attempt == 3:
                raise
            time.sleep(5 * 2 ** attempt)
    calls = []
    for tc in msg.get("tool_calls") or []:
        try:
            args = json.loads(tc["function"].get("arguments") or "{}")
        except json.JSONDecodeError:
            args = {}
        calls.append({"name": tc["function"]["name"], "arguments": args if isinstance(args, dict) else {}})
    return {"text": msg.get("content") or None, "tool_calls": calls}

# ============================================================== TODO 2
def system_prompt() -> str:
    """What the agent is told about its job.

    Worth deciding deliberately: how much of the semver spec do you put in
    here versus letting the agent read reference/version.py itself? Baking
    knowledge into the prompt is cheap and brittle; making the agent read
    the source costs tokens but generalises. Try both and measure.
    """
    return """We need a Rust port of reference/version.py (python-semver). It parses version strings like "1.4.2-beta.3+build.7", compares them and bumps them. Put it in rust/src/lib.rs. It gets checked against the real Python package on a few thousand random versions, so it has to behave exactly the same. These must exist with exactly these signatures:

    #[derive(Debug, Clone, PartialEq, Eq)]
    pub struct Version { pub major: u64, pub minor: u64, pub patch: u64, pub prerelease: Option<String>, pub build: Option<String> }
    pub fn parse(s: &str) -> Result<Version, String>
    pub fn to_string(v: &Version) -> String
    pub fn compare(a: &Version, b: &Version) -> std::cmp::Ordering
    pub fn bump_major(v: &Version) -> Version
    pub fn bump_minor(v: &Version) -> Version
    pub fn bump_patch(v: &Version) -> Version

There are a few things you should be cautious of: the build part (+...) never affects ordering, 1.0.0-alpha < 1.0.0: a prerelease ranks lower, prerelease parts compare one dot-piece at a time: numbers as numbers, words alphabetically, numbers before words, and the longer list wins a tie, no leading zeros in numbers (01.0.0 and 1.0.0-01 are invalid), but 1.0.0+001 is fine, don't guess what bump_patch does to 1.2.3-rc.1, ask probe. Do not invoke anything other than standard library, no unsafe, and no todo!, panic!, .unwrap(), .expect() or .clone() anywhere, tests included. Otherwise, what is the point of us running this in Rust? If the borrow checker complains, don't simply clone it. Also write 20-30 tests at the bottom, ported from reference/test_parsing.py, test_compare.py and test_bump.py. Read only the Python you need (read_python by name), use probe when unsure, then write the whole file with write_rust. Every edit gets built and tested for you, and an edit that makes things worse is undone. Fix what comes back with replace_fn or replace_lines. Read reference/version.py if you are unsure of the semver spec. When it's all green, just say you're done."""

# ============================================================== TODO 3
def build_context(history: list[dict], step: int) -> list[dict]:
    """Turn the full history into the messages you actually send.

    The naive version - return history unchanged - will fill the context
    window somewhere around step 15 once compiler errors start accumulating,
    and the agent will begin repeating work it has already done.

    You have four levers (agentweb deck, part IV):
        WRITE     put state in a file instead of the context
        SELECT    retrieve only what this step needs
        COMPRESS  summarise old turns
        ISOLATE   give a sub-agent its own window

    Constraint for this assignment: you may not raise the model's context
    limit to solve this. Solve it by managing what you send.
    """
    if sum(len(h["content"]) for h in history[STATE["upto"]:] if h["role"] == "tool") > 80_000:
        compress(history)
    parts = [history[1]["content"], "reference/version.py outline:\n" + py_outline(PYSRC),
             "rust/src/lib.rs outline:\n" + rust_outline(), "Latest build/test:\n" + (STATE["last"] or "none yet")]
    if STATE["summary"]:
        parts.append("Your summary of earlier work (don't redo it):\n" + STATE["summary"])
    parts += STATE["flags"]
    STATE["flags"].clear()
    recent, used = [], 0
    for h in reversed(history[STATE["upto"]:]):
        if h["role"] == "tool":
            call = f"[step {h['step']}] {h['name']}({json.dumps(h['args'])[:120]})"
            full = used + len(h["content"]) <= 40_000
            recent.append(f"{call} ->\n{h['content']}" if full else f"{call} -> {h['content'][:120]}")
            used += len(h["content"]) if full else 0
    return [history[0], {"role": "user", "content": "\n\n".join(parts + ["Recent tool results, newest first:"] + recent)}]

def compress(history):
    log = "\n\n".join(f"{h['name']}({json.dumps(h['args'])[:120]}) ->\n{h['content'][:1500]}"
                      for h in history[STATE["upto"]:] if h["role"] == "tool")
    reply = call_model([{"role": "user", "content":
        f"Summarise this agent's progress in under 800 words.\n\nPrevious summary:\n{STATE['summary']}\n\n"
        f"New log:\n{log}\n\nLatest build/test:\n{STATE['last']}\n\nUse these headings: KEY FACTS (exact verified "
        "behaviors and probe results), DONE, TESTED (passing/failing), BUILD ERRORS (copied exactly), REMAINING."}], [])
    STATE["summary"] = (reply["text"] or STATE["summary"]).strip()
    STATE["upto"], STATE["compressions"] = len(history), STATE["compressions"] + 1

# ============================================================== TODO 4
def should_stop(history: list[dict], step: int, budget: int, last_score: float | None) -> tuple[bool, str]:
    """Return (stop?, why).

    The budget check below is given. Everything else is yours:
      - stop when the score stops improving? after how many flat steps?
      - stop when the agent repeats an identical action?
      - stop when it claims to be done - and do you believe it?
      - what if the score goes DOWN? do you roll back?

    An agent that never decides to stop is not finished, it is just out
    of budget. That distinction is graded.
    """
    if step + STATE["compressions"] >= budget:
        return True, f"budget exhausted ({budget} model calls)"

    # It says it's done when it answers without calling a tool. Check before believing it.
    last = history[-1]
    if last["role"] == "assistant" and not last["tool_calls"] and last["content"].strip():
        STATE["claims"] += 1
        report = t_cargo_test({})
        if STATE["ok"]:
            return True, "done: it says so, and it builds, the tests pass and the code is clean"
        if STATE["claims"] == 3:
            return True, "gave up: it keeps saying done but the checks fail"
        STATE["flags"].append("You're not done yet:\n" + report)

    # Stuck: no improvement in 10 checks, or the same action 3 times in a row.
    if STATE["stale"] >= 10:
        return True, "stuck: no improvement in 10 checks"
    actions = [h["name"] + json.dumps(h["args"]) for h in history if h["role"] == "tool"]
    if len(actions) >= 3 and actions[-1] == actions[-2] == actions[-3]:
        return True, "stuck: same action 3 times in a row"

    # If the score goes down, cargo_test already puts the best version back.
    if STATE["ok"]:
        STATE["flags"].append("Everything passes. Reply without a tool call to finish.")
    elif step >= 6 and not any(a.startswith("write_rust") for a in actions):
        STATE["flags"].append("Come on! Enough research already, write the whole lib.rs now with write_rust.")
    return False, ""

# ================================================================== tools
def _run(cmd, cwd=None, timeout=180):
    try:
        p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
        return (p.stdout + p.stderr).strip() or "(no output)"
    except subprocess.TimeoutExpired:
        return f"TIMEOUT after {timeout}s"

def _with_line_numbers(lines, first):
    return "\n".join(f"{n:>4}| {line}" for n, line in enumerate(lines, first))

def python_defs(path):
    """Every function, class and constant in a Python file, as (name, first line, last line)."""
    defs = []
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            defs.append((node.name, node.lineno, node.end_lineno))
        elif isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
            defs.append((node.targets[0].id, node.lineno, node.end_lineno))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            defs.append((node.target.id, node.lineno, node.end_lineno))
    return defs

def rust_fns(src):
    """Every Rust fn, as (name, first line, last line). A fn ends at the first
    line that is just '}' at the same indentation as the line with 'fn'."""
    lines, fns = src.splitlines(), []
    for i, line in enumerate(lines):
        m = re.match(r"(\s*)(?:pub\S* )?fn (\w+)", line)
        if not m:
            continue
        end = i if line.rstrip().endswith("}") else next(
            (j for j in range(i, len(lines)) if lines[j].rstrip() == m.group(1) + "}"), i)
        start = i
        while start > 0 and lines[start - 1].strip().startswith("#["):
            start -= 1
        fns.append((m.group(2), start + 1, end + 1))
    return fns

def py_outline(path):
    return "\n".join(f"  {name}  lines {a}-{b}" for name, a, b in python_defs(path))

def rust_outline():
    return "\n".join(f"  fn {name}  lines {a}-{b}" for name, a, b in rust_fns(LIB.read_text())) or "  (no functions yet)"

def t_read_python(_args):
    """The source to translate."""
    path = HERE / "reference" / pathlib.Path(_args.get("file") or "version.py").name
    if not path.exists():
        return f"no such file: {path.name}"
    lines = path.read_text().splitlines()
    wanted = [n.split(".")[-1] for n in _args.get("names") or [] if n.strip()]
    if wanted:
        found = [(n, a, b) for n, a, b in python_defs(path) if n in wanted]
        return "\n\n".join(_with_line_numbers(lines[a - 1:b], a) for _, a, b in found) or f"not found: {wanted}"
    if "start" in _args:
        a, b = int(_args["start"]), int(_args.get("end", int(_args["start"]) + 80))
        return _with_line_numbers(lines[a - 1:b], a)
    return f"{path.name} contains:\n" + py_outline(path)

def t_read_rust(_args):
    lines = LIB.read_text().splitlines()
    for name, a, b in rust_fns(LIB.read_text()):
        if name == _args.get("name"):
            return _with_line_numbers(lines[a - 1:b], a)
    a, b = int(_args.get("start", 1)), int(_args.get("end", len(lines)))
    return _with_line_numbers(lines[a - 1:b], a)

def save_and_test(lines, what):
    LIB.write_text("\n".join(lines) + "\n")
    return what + "\n" + t_cargo_test({})

def t_write_rust(args):
    """Overwrite rust/src/lib.rs. `content` must be the WHOLE file."""
    return save_and_test(args["content"].splitlines(), "wrote rust/src/lib.rs")

def t_replace_fn(args):
    lines = LIB.read_text().splitlines()
    for name, a, b in rust_fns(LIB.read_text()):
        if name == args.get("name"):
            lines[a - 1:b] = args.get("code", "").splitlines()
            return save_and_test(lines, f"replaced fn {name}")
    return f"no fn called {args.get('name')!r}"

def t_replace_lines(args):
    lines = LIB.read_text().splitlines()
    a, b = int(args["start"]), int(args["end"])
    lines[a - 1:b] = args.get("text", "").splitlines()
    return save_and_test(lines, f"replaced lines {a}-{b}")

def ask_python(command):
    """Answer one harness command with the real semver package, in the same shape as the Rust harness."""
    import semver
    op, *args = command.split()
    try:
        if op == "parse":
            v = semver.Version.parse(args[0])
            return {"ok": True, "major": v.major, "minor": v.minor, "patch": v.patch,
                    "prerelease": v.prerelease, "build": v.build}
        if op == "compare":
            return {"ok": True, "cmp": semver.Version.parse(args[0]).compare(args[1])}
        if op == "bump":
            return {"ok": True, "version": str(getattr(semver.Version.parse(args[1]), "bump_" + args[0])())}
        if op == "format":
            return {"ok": True, "version": str(semver.Version.parse(args[0]))}
    except (ValueError, TypeError, IndexError, AttributeError):
        pass
    return {"ok": False}

def t_probe(args):
    commands = args.get("commands") or []
    if subprocess.run(["cargo", "build", "--release"], cwd=RUST, capture_output=True).returncode != 0:
        return "lib.rs does not build yet. Python says:\n" + "\n".join(f"{c}: {ask_python(c)}" for c in commands)
    answers = subprocess.run([str(BIN)], input="\n".join(commands) + "\n", capture_output=True, text=True).stdout
    report = []
    for command, line in zip(commands, answers.splitlines()):
        python, rust = ask_python(command), json.loads(line)
        rust.pop("error", None)
        report.append(f"{'same' if python == rust else 'DIFFERENT'}: {command}\n  python: {python}\n  rust:   {rust}")
    return "\n".join(report)

QUALITY = {r"\bunsafe\b": "unsafe", r"\b(todo|unimplemented|unreachable|panic)!": "panicking macro",
           r"\.unwrap\(\)": ".unwrap()", r"\.expect\(": ".expect()", r"\.clone\(\)": ".clone()", r"\.to_owned\(\)": ".to_owned()"}

def t_cargo_build(_args):
    out = _run(["cargo", "build", "--release"], cwd=RUST)
    STATE["last"] = out[out.find("error"):][:6000] if "error" in out else "build OK"
    return STATE["last"]

def t_cargo_test(_args):
    # Build, run the tests and check quality, then score it. Keep the best
    # version; if this one is worse, put the best one back.
    src, out = LIB.read_text(), _run(["cargo", "test", "--release"], cwd=RUST)
    issues = [f"  line {n}: {label}: {line.strip()[:90]}" for n, line in enumerate(src.splitlines(), 1)
              for pattern, label in QUALITY.items() if re.search(pattern, line.split("//")[0])]
    m = re.search(r"(\d+) passed; (\d+) failed", out)
    passed, failed = (int(m[1]), int(m[2])) if m else (0, 0)
    if "could not compile" in out:
        report, score = "DOES NOT COMPILE\n" + out[out.find("error"):][:6000], -100
    else:
        report = f"{passed} tests passed, {failed} failed"
        if failed:
            report += "\n" + out.split("failures:")[1][:4000] + "\nEither the code or the test is wrong - check with probe."
        score = min(passed, 30) - 3 * failed - 2 * len(issues) - (20 if passed < 15 else 0)
    report += ("\nquality issues:\n" + "\n".join(issues)) if issues else "\nquality: clean"
    STATE["ok"] = score > -100 and failed == 0 and passed >= 15 and not issues
    best = STATE["best"]
    if best is None or score > best["score"]:
        STATE["best"], STATE["stale"] = {"score": score, "src": src}, 0
        report += f"\nscore {score}: best so far"
    else:
        STATE["stale"] += 1
        if score < best["score"]:
            LIB.write_text(best["src"])
            STATE["ok"] = False
            report += f"\nscore {score} is worse than the best ({best['score']}), so lib.rs was put back to the best version"
    STATE["last"] = report
    return report

def t_evaluate(_args):
    """Practice seed only. The grading seed is different - do not tune to this."""
    return _run([sys.executable, str(HERE / "evaluate.py"), "--n", "60"], cwd=HERE)

# TODO 5: this action space is deliberately coarse. `write_rust` rewriting
# the whole file every time is expensive and loses work on partial edits.
# Consider: a patch/replace-function tool, a "run one differential case"
# tool, a tool that greps the Python source. Measure before and after.
S, I, A = {"type": "string"}, {"type": "integer"}, {"type": "array", "items": {"type": "string"}}
TOOLS = [
    dict(name="read_python", description="Read the Python. file: version.py (default), test_parsing.py, test_compare.py or test_bump.py. "
         "Give names=['parse', 'compare'] to read those functions, or start/end for a line range. With neither you get a list of what's in the file.",
         parameters={"type": "object", "properties": {"file": S, "names": A, "start": I, "end": I}}, fn=t_read_python),
    dict(name="read_rust", description="Read rust/src/lib.rs with line numbers. Give name='compare' for one function, start/end for a range, or nothing for the whole file.",
         parameters={"type": "object", "properties": {"name": S, "start": I, "end": I}}, fn=t_read_rust),
    dict(name="write_rust", description="Overwrite rust/src/lib.rs with the complete file contents.",
         parameters={"type": "object", "required": ["content"],
                     "properties": {"content": {"type": "string"}}}, fn=t_write_rust),
    dict(name="replace_fn", description="Replace a whole function in lib.rs, by name, with new code. The file is then built and tested.",
         parameters={"type": "object", "required": ["name", "code"], "properties": {"name": S, "code": S}}, fn=t_replace_fn),
    dict(name="replace_lines", description="Replace lines start to end of lib.rs with new text. The file is then built and tested.",
         parameters={"type": "object", "required": ["start", "end", "text"], "properties": {"start": I, "end": I, "text": S}},
         fn=t_replace_lines),
    dict(name="probe", description="Ask the real Python semver package and your Rust the same questions and see if they agree. "
         "Commands look like 'parse 1.0.0-rc.1', 'compare 1.0.0-a 1.0.0', 'bump patch 1.2.3-rc.1', 'format 1.0.0+b'.",
         parameters={"type": "object", "properties": {"commands": A}}, fn=t_probe),
    dict(name="cargo_build", description="Compile the crate. Returns compiler errors.",
         parameters={"type": "object", "properties": {}}, fn=t_cargo_build),
    dict(name="cargo_test", description="Build, run your tests and check code quality. If the result is worse than before, the best version is put back.",
         parameters={"type": "object", "properties": {}}, fn=t_cargo_test),
]
BY_NAME = {t["name"]: t for t in TOOLS}
SCHEMAS = [{k: t[k] for k in ("name", "description", "parameters")} for t in TOOLS]

# =================================================================== loop
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--budget", type=int, default=40, help="max model calls (graded cap: 40)")
    ap.add_argument("--task", default="Translate reference/version.py into rust/src/lib.rs.")
    a = ap.parse_args()

    LOGS.mkdir(exist_ok=True)
    log = LOGS / f"run-{time.strftime('%Y%m%d-%H%M%S')}.jsonl"
    def rec(**kw):
        with log.open("a") as f:
            f.write(json.dumps({"t": time.time(), **kw}) + "\n")

    history = [{"role": "system", "content": system_prompt()},
               {"role": "user",   "content": a.task}]
    rec(event="start", budget=a.budget, task=a.task)

    step, last_score = 0, None
    while True:
        stop, why = should_stop(history, step, a.budget, last_score)
        if stop:
            print(f"\n[stop] {why}")
            rec(event="stop", reason=why, steps=step)
            break

        step += 1
        reply = call_model(build_context(history, step), SCHEMAS)
        rec(event="model", step=step, reply=reply)

        if reply.get("text"):
            print(f"[{step}] {reply['text'][:200]}")
        history.append({"role": "assistant", "content": reply.get("text") or "",
                        "tool_calls": reply.get("tool_calls", [])})

        calls = reply.get("tool_calls") or []
        if not calls:
            # TODO: no tool call. Is the agent done, stuck, or just chatting?
            # Deciding this is part of TODO 4.
            continue

        for c in calls:
            tool = BY_NAME.get(c["name"])
            try:
                out = (f"unknown tool {c['name']!r}" if not tool
                       else tool["fn"](c.get("arguments") or {}))
            except Exception as e:
                out = f"tool error: {e}"
            print(f"      -> {c['name']}: {str(out).splitlines()[0][:120] if out else ''}")
            rec(event="tool", step=step, name=c["name"], output=str(out)[:4000])
            history.append({"role": "tool", "name": c["name"], "content": str(out),
                            "step": step, "args": c.get("arguments") or {}})
        last_score = STATE["best"] and STATE["best"]["score"]

    if STATE["best"]:
        LIB.write_text(STATE["best"]["src"])
    print(f"\ntrajectory: {log}")
    print("final score:")
    subprocess.run([sys.executable, str(HERE / "evaluate.py")], cwd=HERE)

if __name__ == "__main__":
    main()
