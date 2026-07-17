"""Built-in tool/function-calling eval (a mini-BFCL).

Presents OpenAI-format tools + a user turn, then checks the model selects the
right function with the right required arguments. Exercises single, multiple
(pick the correct one), and no-call (should answer directly) cases.
"""
from __future__ import annotations

import time

from . import parse_tool_call

KEY = "tc-mini"
NAME = "Tool-Calling (mini-BFCL)"
CATEGORY = "tool-calling"
TIER = 1
DESCRIPTION = "Function selection + argument accuracy over curated tool schemas."

WEATHER = {
    "type": "function",
    "function": {
        "name": "get_weather",
        "description": "Get current weather for a city.",
        "parameters": {
            "type": "object",
            "properties": {
                "city": {"type": "string"},
                "unit": {"type": "string", "enum": ["celsius", "fahrenheit"]},
            },
            "required": ["city"],
        },
    },
}
SEND_EMAIL = {
    "type": "function",
    "function": {
        "name": "send_email",
        "description": "Send an email to a recipient.",
        "parameters": {
            "type": "object",
            "properties": {
                "to": {"type": "string"},
                "subject": {"type": "string"},
                "body": {"type": "string"},
            },
            "required": ["to", "subject", "body"],
        },
    },
}
CONVERT = {
    "type": "function",
    "function": {
        "name": "convert_currency",
        "description": "Convert an amount between currencies.",
        "parameters": {
            "type": "object",
            "properties": {
                "amount": {"type": "number"},
                "from_currency": {"type": "string"},
                "to_currency": {"type": "string"},
            },
            "required": ["amount", "from_currency", "to_currency"],
        },
    },
}
SEARCH = {
    "type": "function",
    "function": {
        "name": "search_flights",
        "description": "Search flights between two airports on a date.",
        "parameters": {
            "type": "object",
            "properties": {
                "origin": {"type": "string"},
                "destination": {"type": "string"},
                "date": {"type": "string", "description": "YYYY-MM-DD"},
            },
            "required": ["origin", "destination", "date"],
        },
    },
}

ALL_TOOLS = [WEATHER, SEND_EMAIL, CONVERT, SEARCH]

# Each case: prompt, expected fn (None => should NOT call a tool), required arg
# checks as {arg: [acceptable values, lowercased]} (empty list = presence only).
CASES = [
    ("What's the weather in Paris right now?", "get_weather",
     {"city": ["paris"]}),
    ("Is it hot in Tokyo? Give me Fahrenheit.", "get_weather",
     {"city": ["tokyo"], "unit": ["fahrenheit"]}),
    ("Email alice@example.com to say the report is done. Subject: Report.",
     "send_email", {"to": ["alice@example.com"]}),
    ("Convert 100 US dollars to euros.", "convert_currency",
     {"amount": ["100", "100.0"], "from_currency": ["usd", "us dollars", "dollar"],
      "to_currency": ["eur", "euros", "euro"]}),
    ("Find me a flight from JFK to LAX on 2026-08-01.", "search_flights",
     {"origin": ["jfk"], "destination": ["lax"], "date": ["2026-08-01"]}),
    ("How many continents are there on Earth?", None, {}),
    ("What temperature is it in Berlin in celsius?", "get_weather",
     {"city": ["berlin"], "unit": ["celsius"]}),
    ("Please send an email to bob@corp.io, subject Hi, body Hello there.",
     "send_email", {"to": ["bob@corp.io"], "subject": ["hi"]}),
    ("Exchange 50 GBP into JPY.", "convert_currency",
     {"amount": ["50", "50.0"], "from_currency": ["gbp"], "to_currency": ["jpy"]}),
    ("Book nothing — just tell me a fun fact about octopuses.", None, {}),
    ("Weather for San Francisco?", "get_weather", {"city": ["san francisco"]}),
    ("Flights SFO to SEA on 2026-09-15 please.", "search_flights",
     {"origin": ["sfo"], "destination": ["sea"], "date": ["2026-09-15"]}),
]

SYS = (
    "You are a function-calling assistant. Use the provided tools when the "
    "user's request matches one. If no tool applies, answer directly in prose "
    "without calling a tool."
)


def _arg_ok(expected: dict, got: dict) -> bool:
    for arg, accept in expected.items():
        if arg not in got:
            return False
        if accept:
            val = str(got[arg]).strip().lower()
            if not any(a in val or val in a for a in accept):
                return False
    return True


def run(client, model, opts, emit):
    limit = opts.get("limit") or len(CASES)
    deadline = opts.get("deadline")
    cases = CASES[:limit]
    n_pass = 0
    fn_correct = 0
    arg_correct = 0
    details = []
    for i, (prompt, exp_fn, exp_args) in enumerate(cases, 1):
        if deadline and time.time() > deadline:
            emit(f"  ⏱ time budget reached — stopping at {i-1}/{len(cases)}")
            break
        try:
            res = client.chat(
                model,
                [{"role": "system", "content": SYS},
                 {"role": "user", "content": prompt}],
                tools=ALL_TOOLS, temperature=0.0, max_tokens=512,
            )
        except Exception as e:  # noqa: BLE001
            emit(f"  [{i}/{len(cases)}] ERROR {e}")
            details.append({"case": prompt, "error": str(e)})
            continue

        name, args = parse_tool_call(res)
        if exp_fn is None:
            ok = name is None
            passed = ok
            if ok:
                fn_correct += 1
                arg_correct += 1
        else:
            fn_ok = (name == exp_fn)
            arg_ok = fn_ok and _arg_ok(exp_args, args)
            fn_correct += int(fn_ok)
            arg_correct += int(arg_ok)
            passed = arg_ok
        n_pass += int(passed)
        mark = "PASS" if passed else "fail"
        emit(f"  [{i}/{len(cases)}] {mark}  want={exp_fn or 'no-call'} got={name}")
        details.append({"case": prompt, "want": exp_fn, "got": name,
                        "args": args, "passed": passed})

    n = len(details)  # tasks actually attempted (may be < limit if time ran out)
    return {
        "score": n_pass / n if n else None,
        "score_label": "acc(fn+args)",
        "n_total": n,
        "n_pass": n_pass,
        "metrics": {
            "fn_selection_acc": round(fn_correct / n, 4) if n else None,
            "arg_exact_acc": round(arg_correct / n, 4) if n else None,
            "details": details,
        },
    }
