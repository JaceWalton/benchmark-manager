"""Built-in reasoning eval (GSM8K-style arithmetic word problems).

A quick sanity signal on multi-step reasoning; numeric-answer graded.
"""
from __future__ import annotations

import time

from . import extract_number

KEY = "reason-mini"
NAME = "Reasoning (GSM8K-style)"
CATEGORY = "reasoning"
TIER = 1
DESCRIPTION = "Multi-step arithmetic word problems, exact numeric-answer match."

# (question, answer)
PROBLEMS = [
    ("A shop sells pencils at 3 for $1.20. How many dollars do 10 pencils cost?", 4.0),
    ("Tom has 15 apples. He gives 1/3 to Sara and eats 2. How many are left?", 8.0),
    ("A train travels 60 km in 45 minutes. What is its speed in km/h?", 80.0),
    ("A rectangle is 8 by 5. What is its perimeter?", 26.0),
    ("If 5 workers build a wall in 12 days, how many days for 10 workers (same rate)?", 6.0),
    ("A shirt costs $40 after a 20% discount. What was the original price in dollars?", 50.0),
    ("Sum of three consecutive integers is 72. What is the largest?", 25.0),
    ("A tank holds 200 L and drains at 8 L/min. After how many minutes is it empty?", 25.0),
    ("You buy 3 books at $12 each and pay with a $50 bill. How much change?", 14.0),
    ("Half of a number plus 7 equals 20. What is the number?", 26.0),
    ("A car uses 6 L per 100 km. How many litres for a 250 km trip?", 15.0),
    ("A class has 30 students, 40% are boys. How many girls are there?", 18.0),
    ("Anna saves $15 per week. How many weeks to save $180?", 12.0),
    ("A pizza is cut into 8 slices. If 3 people each eat 2 slices, how many remain?", 2.0),
    ("The price rises from $80 to $100. What is the percent increase?", 25.0),
]

SYS = (
    "Solve the problem step by step. End your answer with a line of the form "
    "'#### <number>' containing only the final numeric answer."
)


def run(client, model, opts, emit):
    limit = opts.get("limit") or len(PROBLEMS)
    deadline = opts.get("deadline")
    probs = PROBLEMS[:limit]
    n_pass = 0
    details = []
    for i, (q, ans) in enumerate(probs, 1):
        if deadline and time.time() > deadline:
            emit(f"  ⏱ time budget reached — stopping at {i-1}/{len(probs)}")
            break
        try:
            res = client.chat(
                model,
                [{"role": "system", "content": SYS},
                 {"role": "user", "content": q}],
                temperature=0.0, max_tokens=1024,
            )
        except Exception as e:  # noqa: BLE001
            emit(f"  [{i}/{len(probs)}] ERROR {e}")
            details.append({"q": q, "error": str(e)})
            continue

        got = extract_number(res.text)
        passed = got is not None and abs(got - ans) < 1e-2
        n_pass += int(passed)
        mark = "PASS" if passed else "fail"
        emit(f"  [{i}/{len(probs)}] {mark}  want={ans} got={got}")
        details.append({"q": q, "want": ans, "got": got, "passed": passed})

    n = len(details)  # tasks actually attempted (may be < limit if time ran out)
    return {
        "score": n_pass / n if n else None,
        "score_label": "accuracy",
        "n_total": n,
        "n_pass": n_pass,
        "metrics": {"details": details},
    }
