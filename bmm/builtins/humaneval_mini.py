"""Built-in code-generation eval (a mini-HumanEval).

Gives a function signature + docstring, asks for an implementation, extracts the
code and runs hidden asserts in a sandboxed subprocess. Pass@1.
"""
from __future__ import annotations

import time

from . import extract_code, run_python

KEY = "code-mini"
NAME = "Code-Gen (mini-HumanEval)"
CATEGORY = "code"
TIER = 1
DESCRIPTION = "Function synthesis graded by executing hidden unit tests (pass@1)."

# (prompt_signature, entry_point, test_body)
PROBLEMS = [
    (
        "def add(a, b):\n    \"\"\"Return the sum of a and b.\"\"\"\n",
        "add",
        "assert add(2,3)==5\nassert add(-1,1)==0\nassert add(0,0)==0",
    ),
    (
        "def is_palindrome(s):\n    \"\"\"Return True if s reads the same forwards and backwards (ignore case).\"\"\"\n",
        "is_palindrome",
        "assert is_palindrome('Racecar')\nassert is_palindrome('abba')\nassert not is_palindrome('abc')",
    ),
    (
        "def fizzbuzz(n):\n    \"\"\"Return a list 1..n where multiples of 3 -> 'Fizz', of 5 -> 'Buzz', of both -> 'FizzBuzz', else the number.\"\"\"\n",
        "fizzbuzz",
        "r=fizzbuzz(15)\nassert r[2]=='Fizz'\nassert r[4]=='Buzz'\nassert r[14]=='FizzBuzz'\nassert r[0]==1",
    ),
    (
        "def gcd(a, b):\n    \"\"\"Return the greatest common divisor of a and b.\"\"\"\n",
        "gcd",
        "assert gcd(12,8)==4\nassert gcd(17,5)==1\nassert gcd(100,10)==10",
    ),
    (
        "def count_vowels(s):\n    \"\"\"Return the number of vowels (aeiou, case-insensitive) in s.\"\"\"\n",
        "count_vowels",
        "assert count_vowels('Hello')==2\nassert count_vowels('xyz')==0\nassert count_vowels('AEIOU')==5",
    ),
    (
        "def flatten(nested):\n    \"\"\"Flatten a list of lists into a single list, preserving order.\"\"\"\n",
        "flatten",
        "assert flatten([[1,2],[3],[4,5]])==[1,2,3,4,5]\nassert flatten([])==[]",
    ),
    (
        "def two_sum(nums, target):\n    \"\"\"Return indices (i,j), i<j, of the two numbers summing to target. Assume exactly one solution.\"\"\"\n",
        "two_sum",
        "assert tuple(two_sum([2,7,11,15],9))==(0,1)\nassert tuple(two_sum([3,2,4],6))==(1,2)",
    ),
    (
        "def roman(n):\n    \"\"\"Convert an integer 1..3999 to a Roman numeral string.\"\"\"\n",
        "roman",
        "assert roman(4)=='IV'\nassert roman(9)=='IX'\nassert roman(58)=='LVIII'\nassert roman(1994)=='MCMXCIV'",
    ),
    (
        "def is_prime(n):\n    \"\"\"Return True if n is a prime number.\"\"\"\n",
        "is_prime",
        "assert is_prime(2)\nassert is_prime(13)\nassert not is_prime(1)\nassert not is_prime(15)",
    ),
    (
        "def word_count(s):\n    \"\"\"Return a dict mapping each whitespace-separated word to its count.\"\"\"\n",
        "word_count",
        "assert word_count('a b a')=={'a':2,'b':1}\nassert word_count('')=={}",
    ),
]

SYS = (
    "You are an expert Python programmer. Implement the requested function. "
    "Return ONLY a single ```python code block containing the complete function "
    "definition. Do not include example usage, prints, or explanations."
)


def run(client, model, opts, emit):
    limit = opts.get("limit") or len(PROBLEMS)
    deadline = opts.get("deadline")
    probs = PROBLEMS[:limit]
    n_pass = 0
    details = []
    for i, (sig, entry, test) in enumerate(probs, 1):
        if deadline and time.time() > deadline:
            emit(f"  ⏱ time budget reached — stopping at {i-1}/{len(probs)}")
            break
        prompt = (
            f"Complete this function:\n\n```python\n{sig}```\n\n"
            "Return the full implementation."
        )
        try:
            res = client.chat(
                model,
                [{"role": "system", "content": SYS},
                 {"role": "user", "content": prompt}],
                temperature=0.0, max_tokens=1024,
            )
        except Exception as e:  # noqa: BLE001
            emit(f"  [{i}/{len(probs)}] ERROR {e}")
            details.append({"entry": entry, "error": str(e)})
            continue

        code = extract_code(res.text)
        passed, detail = run_python(code, test)
        n_pass += int(passed)
        mark = "PASS" if passed else "fail"
        emit(f"  [{i}/{len(probs)}] {mark}  {entry}  {'' if passed else '- '+detail[:60]}")
        details.append({"entry": entry, "passed": passed, "detail": detail})

    n = len(details)  # tasks actually attempted (may be < limit if time ran out)
    return {
        "score": n_pass / n if n else None,
        "score_label": "pass@1",
        "n_total": n,
        "n_pass": n_pass,
        "metrics": {"details": details},
    }
