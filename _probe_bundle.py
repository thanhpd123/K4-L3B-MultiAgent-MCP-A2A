import re

js = open("_bundle.js", encoding="utf-8").read()
for needle in ["teams/register", "api_key", "student_last5", "registration_code", "register"]:
    print("=" * 30, needle)
    for m in re.finditer(re.escape(needle), js):
        s = max(0, m.start() - 250)
        e = min(len(js), m.start() + 350)
        print(js[s:e].replace("\n", " "))
        print("-" * 20)
