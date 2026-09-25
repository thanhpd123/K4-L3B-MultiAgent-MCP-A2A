"""Offline analysis of all 100 case inputs (no MCP calls)."""
import collections
import json
import glob

rows = []
for p in sorted(glob.glob("inputs/*.json")):
    c = json.load(open(p, encoding="utf-8"))
    rows.append(c)

# 1. candidate_order_ids patterns
cand_pat = collections.Counter()
for c in rows:
    cands = c["candidate_order_ids"]
    claimed = c["customer_request"]["claimed_order_id"]
    # classify: claimed in candidates? placeholders present?
    kinds = []
    for cd in cands:
        if cd.startswith("candidate-"):
            kinds.append("placeholder")
        elif cd == claimed:
            kinds.append("claimed")
        else:
            kinds.append("other-real")
    cand_pat[tuple(kinds)] += 1
print("candidate composition patterns:")
for k, v in cand_pat.items():
    print(" ", v, k)

# 2. claimed_order_id vs customer_unique_id_hint relationship
print("\nhint/claimed sample:")
for c in rows[:3]:
    print(" ", c["case_id"], "claimed=", c["customer_request"]["claimed_order_id"],
          "hint=", c["customer_unique_id_hint"])

# 3. claims composition
claim_pat = collections.Counter()
for c in rows:
    claim_pat[tuple((x["topic"]) for x in c["customer_request"]["claims"])] += 1
print("\nclaim topic sequences:")
for k, v in claim_pat.items():
    print(" ", v, k)

# 4. any case where claimed_order_id is a placeholder?
ph = [c["case_id"] for c in rows if c["customer_request"]["claimed_order_id"].startswith("candidate-")]
print("\ncases with placeholder claimed_order_id:", ph)

# 5. any case with >2 candidates or <2 candidates
n2 = collections.Counter(len(c["candidate_order_ids"]) for c in rows)
print("\nnum candidates distribution:", dict(n2))

# 6. message length variety (are messages informative?)
msgs = {len(c["customer_request"]["message"]) for c in rows}
print("\nmessage lengths:", sorted(msgs)[:10], "...")
print("sample message:", rows[0]["customer_request"]["message"])
