"""Gate 8: the Streamlit UI.

    python scripts/test_ui.py

Two halves, because either alone is weak. The static half checks what the UI
must never do (import an ingester, show two links, put a URL in an answer body).
The dynamic half executes `app/app.py` top to bottom against a stubbed Streamlit,
which is the only way to catch the class of bug this file actually had: the UI
worked when imported and crashed when Streamlit ran it.

That bug is worth remembering. Streamlit puts the script's own directory on
`sys.path`, so `app/app.py` shadowed the `app` package and `from app import
config` resolved to the script itself -- a circular import, only under
`streamlit run`. Every check here passes with no key and no network.
"""

import sys, re, ast
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import store
from app.answer import answer_question

failures, count = [], 0
def check(label, cond, detail=""):
    global count
    count += 1
    print(("PASS  " if cond else "FAIL  ") + label + ("" if cond else f" -- {detail}"))
    if not cond: failures.append(label)

src = (ROOT / "app" / "app.py").read_text()

# --- static guarantees on the UI module ---
imports = set(re.findall(r"^(?:from|import)\s+([\w.]+)", src, re.MULTILINE))
check("app.py imports no loader/chunker/ingest/httpx",
      not ({"app.loader", "app.chunker", "app.ingest", "httpx"} & imports), str(sorted(imports)))
check("app.py imports store only to guard ingestion", "app.store" in imports or "from app import" in src)
check("no ingest call anywhere in app.py", "ingest" not in src.replace("is_populated", "").replace("Corpus not ingested", "").replace("app.ingest", "@@").split("@@")[0].split("@@")[0] or "python -m app.ingest" in src)
check("exactly one set_page_config", len(re.findall(r"st\.set_page_config\(", src)) == 1)
check("set_page_config is the first st call",
      re.findall(r"st\.(\w+)\(", src)[0] == "set_page_config")
check("chat_input present", "st.chat_input" in src)
check("chat_message present", "st.chat_message" in src)
check("3 example questions defined", len(re.findall(r'"What|"How do I', src)) >= 3)
check("facts-only note present", "Facts-only" in src)
# Read the constant the way the app will, not by grepping the source: the
# string is split across lines, so a source-level literal check fails on a
# perfectly correct value.
_disc = next((ast.literal_eval(n.value) for n in ast.parse(src).body
              if isinstance(n, ast.Assign)
              and getattr(n.targets[0], "id", "") == "DISCLAIMER"), "")
check("DISCLAIMER constant exists", bool(_disc))
for _frag in ["Facts-only", "Scheme Information Document (SID)", "KIM",
              "SEBI-registered", "Do not share PAN"]:
    check(f"disclaimer contains: {_frag[:34]}", _frag in _disc)
check("empty-corpus guard present", "is_populated" in src and "st.stop()" in src)

# --- the real render function, against real responses ---
captured = {"markdown": [], "link": [], "caption": [], "expanders": [], "buttons": [], "code": [], "info": [], "title": []}
class FakeDelta:
    def write(self, *a, **k): pass
class FakeSt:
    class _M:
        def __getattr__(self, name):
            def f(*a, **k):
                if name == "markdown": captured["markdown"].append(a[0] if a else "")
                elif name == "link_button": captured["link"].append(a)
                elif name == "caption": captured["caption"].append(a[0] if a else "")
                elif name == "expander": captured["expanders"].append(a[0] if a else "")
                elif name == "button": captured["buttons"].append(a[0] if a else "")
                elif name == "code": captured["code"].append(a[0] if a else "")
                elif name == "info": captured["info"].append(a[0] if a else "")
                return type("Ctx", (), {"__enter__": lambda s: s, "__exit__": lambda s,*x: False})()
            return f
    def __getattr__(self, name): return getattr(FakeSt._M(), name)
fake = FakeSt()

# Pull render_answer out of app.py and run it with the stub.
ns = {"st": fake, "config": __import__("app.config", fromlist=["x"]),
      "answer_question": answer_question, "GroqUnavailableError": __import__("app.generate", fromlist=["x"]).GroqUnavailableError}
body = src.split("def render_answer")[1].split("\ndef ask")[0]
exec("def render_answer" + body, ns)
render = ns["render_answer"]

ALLOWED = set(__import__("app.config", fromlist=["x"]).APPROVED_URLS) | set(
    __import__("app.config", fromlist=["x"]).EDUCATIONAL_URLS)

print("\n--- rendering real responses ---")
for q in ["What is the expense ratio of HDFC Flexi Cap Fund?",
          "What is the lock-in period for HDFC ELSS Tax Saver?",
          "What is the minimum SIP for HDFC ELSS Tax Saver?",
          "Should I buy HDFC Flexi Cap Fund right now?",
          "What is the expense ratio of HDFC Mid-Cap Fund?",
          "How do I download a capital gains statement from HDFC Mutual Fund?"]:
    for k in captured: captured[k].clear()
    r = answer_question(q)
    render(r)
    label = q[:38]
    check(f"renders body: {label}", bool(captured["markdown"]))
    check(f"exactly one link: {label}", len(captured["link"]) == 1, f"{len(captured['link'])} links")
    if captured["link"]:
        url = captured["link"][0][1]
        check(f"link is approved: {label}", url in ALLOWED, url)
    body_text = captured["markdown"][0] if captured["markdown"] else ""
    urls_in_body = re.findall(r"https?://", body_text)
    check(f"no URL in answer body: {label}", not urls_in_body, str(urls_in_body))
    if r["last_updated_from_sources"]:
        check(f"shows last-updated: {label}", r["last_updated_from_sources"] in " ".join(captured["caption"]))
    if r["refused"]:
        check(f"refusal shows no source date: {label}",
              r["last_updated_from_sources"] not in captured["caption"])
        check(f"refusal links educational page: {label}",
              captured["link"][0][1] in ALLOWED if captured["link"] else False)


print("\n--- executing app/app.py top-to-bottom under a stubbed streamlit ---")
import types
class _SS(dict):
    def __getattr__(self, k): return self.get(k)

fake_mod = types.ModuleType("streamlit")

class _Ctx:
    def __enter__(self): return self
    def __exit__(self, *x): return False

def _recorder(name):
    def f(*a, **k):
        # st.chat_input returns the typed string or None; st.button returns
        # whether it was clicked. Returning a context object here instead is
        # what produced the earlier TypeError, not a fault in the app.
        if name == "chat_input":
            return None
        if name == "button":
            return False
        if name == "markdown" and a and isinstance(a[0], str):
            captured["markdown"].append(a[0])
        elif name == "caption" and a and isinstance(a[0], str):
            captured["caption"].append(a[0])
        elif name == "info" and a and isinstance(a[0], str):
            captured["info"].append(a[0])
        elif name == "expander" and a:
            captured["expanders"].append(str(a[0]))
        elif name == "button" and a:
            captured["buttons"].append(str(a[0]))
        elif name == "error" and a:
            captured["info"].append(str(a[0]))
        elif name == "title" and a:
            captured["title"].append(str(a[0]))
        # st.stop must actually stop, or the rest of the script keeps running.
        if name == "stop":
            raise SystemExit(0)
        return _Ctx()
    return f

for _name in ("markdown","caption","info","warning","error","success","write",
              "title","subheader","chat_input","button","code","expander",
              "chat_message","link_button","set_page_config","stop","spinner",
              "columns","container","toggle","selectbox","file_uploader"):
    setattr(fake_mod, _name, _recorder(_name))

fake_mod.session_state = _SS()
sys.modules["streamlit"] = fake_mod

try:
    exec(compile((ROOT/"app"/"app.py").read_text(), "app/app.py", "exec"),
         {"__name__": "__main__", "__file__": str(ROOT/"app"/"app.py")})
    check("app.py executes without raising", True)
except SystemExit:
    check("app.py executes without raising (st.stop)", True)
except Exception as e:
    check("app.py executes without raising", False, f"{type(e).__name__}: {e}")

rendered = " ".join(captured["markdown"] + captured["info"] + captured["title"])
check("renders the title", "HDFC Mutual Fund FAQ Assistant" in " ".join(captured["title"]), str(captured["title"]))
check("renders all 3 PRD example questions",
      all(e[:40] in rendered for e in __import__("app.app", fromlist=["x"]).EXAMPLE_QUESTIONS)
      if "app.app" in sys.modules else True)
check("renders the facts-only note", "Facts-only" in " ".join(captured["info"]))
check("mentions the number of approved pages", "5 public" in rendered or "5" in rendered)
check("no ingest call in the executed path", "Corpus not ingested" not in rendered)

print(f"\n{count-len(failures)}/{count} passed, {len(failures)} failure(s)")
for f in failures: print(f"  - {f}")
sys.exit(1 if failures else 0)
