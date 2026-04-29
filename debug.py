import json

with open("data/matches.json", encoding="utf-8") as f:
    matches = json.load(f)

# Cherche un match avec des odds
for m in matches:
    odds = m.get("match_odds")
    if odds:
        print(f"=== {m['home']} vs {m['away']} ===")
        print(json.dumps(odds, indent=2)[:5000])
        break