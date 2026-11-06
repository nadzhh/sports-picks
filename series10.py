"""
series10.py — Suivi « séries de 10 paris » (Wilson, 06/11/2026 : « le but est qu'à terme la bankroll soit positive, et
que si tu me donnes 10 paris, on soit en positif sur les 10 »).

Pour le canal Picks et la Bankroll IA : paris regles (gagnes / perdus ; rembourses ignores) dans l'ordre des matchs,
decoupes en series de 10. Chaque serie terminee est positive ou negative ; la serie en cours est suivie.
« Attendu » = chance qu'une serie de 10 soit positive d'apres les probabilites et les cotes de SES paris (5 000 tirages) :
au-dessus de l'attendu = plus de reussite que prevu, en dessous = moins (malchance ou paris trop optimistes).
Rappel du calcul du 06/11 : paris gagnes 65 % a 1,45 -> 51 % des series de 10 positives ; a 1,70 (+10 %) -> 75 %.
"""
import html
import json
import random
from pathlib import Path

SIZE = 10


def _e(s):
    return html.escape(str(s if s is not None else ""))


def _fr(x, d=1):
    return f"{x:.{d}f}".replace(".", ",")


def canal():
    """Paris du canal Picks : (ordre, p, cote, mise, gain en u, libelle)."""
    try:
        P = json.loads(Path("data/telegram_picks.json").read_text(encoding="utf-8")).get("picks") or []
    except (OSError, ValueError):
        return []
    out = [(p["ko"], p["p"], p["price"], p.get("stake", 1.0), p.get("units", 0.0), f'{p["match"]} · {p["label"]}')
           for p in P if p.get("result") in ("win", "loss")]
    return sorted(out)


def bankroll():
    """Paris de la Bankroll IA : (ordre, p, cote, mise en EUR, gain en EUR, libelle)."""
    try:
        B = json.loads(Path("data/nba_bankroll.json").read_text(encoding="utf-8")).get("bets") or []
    except (OSError, ValueError):
        return []
    out = []
    for b in B:
        if b["status"] not in ("won", "lost") or not b.get("p"): continue
        lab = " + ".join(l.get("name", "") + " · " + str(l.get("stat", "")) for l in b["legs"])
        out.append((b.get("settled_at") or b.get("placed_at") or "", b["p"], b["odds"], b["stake"], b.get("pnl", 0.0), lab))
    return sorted(out)


def _p_positive(bets, n=5000, seed=7):
    """Chance que cette serie soit positive d'apres ses probabilites et cotes (tirages independants)."""
    rnd = random.Random(seed)
    k = 0
    for _ in range(n):
        g = sum((s * (o - 1)) if rnd.random() < p else -s for _, p, o, s, _, _ in bets)
        k += g > 0
    return k / n


def series(rows):
    done = [rows[i:i + SIZE] for i in range(0, len(rows) - len(rows) % SIZE, SIZE)]
    cur = rows[len(done) * SIZE:]
    S = []
    for s in done:
        gain = sum(r[4] for r in s)
        S.append({"n": len(s), "w": sum(1 for r in s if r[4] > 0), "gain": round(gain, 2), "pos": gain > 0,
                  "p_pos": round(_p_positive(s), 3), "from": s[0][0][:10], "to": s[-1][0][:10]})
    pos = sum(x["pos"] for x in S)
    return {"done": S, "pos": pos, "rate": (pos / len(S)) if S else None,
            "expected": (sum(x["p_pos"] for x in S) / len(S)) if S else None,
            "current": {"n": len(cur), "w": sum(1 for r in cur if r[4] > 0), "gain": round(sum(r[4] for r in cur), 2)}}


def summary():
    return {"canal": series(canal()), "bankroll": series(bankroll())}


def _block(title, R, unit):
    if not R["done"] and not R["current"]["n"]:
        return (f'<div class="s10-b"><div class="td-sub">{title}</div><p class="td-none">Aucun pari réglé pour l’instant : '
                'la première série de 10 démarre au premier résultat.</p></div>')
    chips = "".join(
        f'<span class="s10-c {"ok" if x["pos"] else "ko"}" title="{_e(x["from"])} → {_e(x["to"])} · {x["w"]}/10 gagnés · '
        f'chance d’être positive {round(100 * x["p_pos"])} %">{"+" if x["gain"] > 0 else ""}{_fr(x["gain"], 2 if unit == "€" else 1)}'
        f' {unit}</span>' for x in R["done"])
    c = R["current"]
    cur = (f'<span class="s10-c cur">en cours {c["n"]}/10 · {c["w"]} gagnés · {"+" if c["gain"] > 0 else ""}'
           f'{_fr(c["gain"], 2 if unit == "€" else 1)} {unit}</span>' if c["n"] else "")
    rate = (f'<b>{R["pos"]}/{len(R["done"])}</b> séries positives ({round(100 * R["rate"])} %) · attendu '
            f'{round(100 * R["expected"])} % vu les probabilités et les cotes' if R["done"] else "Pas encore de série terminée.")
    return f'<div class="s10-b"><div class="td-sub">{title}</div><p class="s10-r">{rate}</p><div class="s10-l">{chips}{cur}</div></div>'


CSS = """<style>
.s10-b{margin-top:10px}
.s10-r{font-size:13.5px;margin:4px 0 8px}
.s10-l{display:flex;flex-wrap:wrap;gap:6px}
.s10-c{font:600 12px var(--mono);padding:4px 8px;border-radius:7px;background:var(--chip)}
.s10-c.ok{color:var(--acc);box-shadow:inset 0 0 0 1px var(--acc)}
.s10-c.ko{color:var(--loss);box-shadow:inset 0 0 0 1px var(--loss)}
.s10-c.cur{color:var(--mu);border:1px dashed var(--line2)}
</style>"""


def card_html():
    s = summary()
    return (CSS + '<div class="eb-card"><h3 class="eb-h">🔟 Séries de 10 paris <small>l’objectif : le plus de séries '
            'positives possible</small></h3>' + _block("Canal Picks", s["canal"], "u") + _block("Bankroll IA", s["bankroll"], "€")
            + '<p class="eb-note">Chaque pastille = 10 paris réglés à la suite (verte : gain, rouge : perte). « Attendu » : '
              'la chance qu’une série soit positive d’après les probabilités et les cotes de ses paris. Même des paris à '
              '+10 % d’espérance perdent environ une série de 10 sur quatre ; sur la durée, c’est la part de séries positives '
              'et la CLV qui disent si la méthode marche.</p></div>')


def text():
    """Ligne de resume (bot d'amelioration, Telegram)."""
    s = summary(); out = []
    for k, lab, u in (("canal", "canal Picks", "u"), ("bankroll", "Bankroll IA", "€")):
        R = s[k]
        if R["done"]:
            out.append(f"{lab} : {R['pos']}/{len(R['done'])} séries de 10 positives (attendu {round(100 * R['expected'])} %)")
        if R["current"]["n"]:
            out.append(f"{lab} : série en cours {R['current']['n']}/10, {R['current']['gain']:+.2f} {u}")
    return out
