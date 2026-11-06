"""
site_verdict.py — Ligne « verdict » en tete de chaque fiche match (demande de Wilson, 05/11/2026 : « améliorer le
frontend », point 3) et repli des cartes detaillees sous un seul clic.

Constat : une fiche foot empile environ 12 cartes ; il fallait tout lire pour savoir s'il y a un pari a jouer.
Le verdict reprend les memes criteres que les picks du jour (picks_du_jour.py, probabilites recalees) :
  value (esperance positive)  >  safe (cote >= 1,40, gagne >= 2 fois sur 3)  >  meilleur prix (marge la plus faible)
  >  pick du simulateur (regle validee sur l'historique)  >  « rien a jouer » (avec le pari le moins couteux).
Puis 3 raisons chiffrees tirees de la simulation. Les cartes detaillees restent dans la page, repliees ; l'etat
ouvert / ferme est memorise (un clic une fois, pas a chaque match).
"""
import html
from datetime import datetime, timedelta, timezone

import picks_du_jour as PJ

_IDX = {}


def _e(s):
    return html.escape(str(s if s is not None else ""))


def _fr(x, d=2):
    return f"{x:.{d}f}".replace(".", ",")


def _pc(x):
    return f"{round(100 * x)} %"


def _tier(x):
    if x["ev"] >= (PJ.MIN_EV_PROP if x["kind"] == "prop" else PJ.MIN_EV_VALUE): return "value"
    if (x["price"] >= PJ.SAFE_MIN_ODDS and x["p"] >= PJ.SAFE_MIN_P and x["ev"] >= PJ.SAFE_MIN_EV
            and (x.get("p_sim") is None or x["p_sim"] >= PJ.SAFE_MIN_P)): return "safe"
    if (x["kind"] == "value" and PJ.BEST_MIN_EV <= x["ev"] and x.get("p_sim") is not None
            and x["p_sim"] >= x["p_fair"]): return "best"
    return None


def _index():
    """{(sport, id du match): [candidats]} pour les 48 h, recalcule au plus une fois par minute."""
    now = datetime.now(timezone.utc)
    if _IDX.get("at") and now - _IDX["at"] < timedelta(minutes=1): return _IDX["map"]
    import site_today as ST
    sims, nba = ST._games()
    m = {}
    try:
        cands = PJ.value_candidates(now, 48, min_ev=-0.15) + PJ.prop_candidates(now, 48, min_ev=-0.15)
    except Exception as e:
        print(f"  [!] verdict : candidats indisponibles ({type(e).__name__}: {e})")
        cands = []
    for x in cands:
        link = ST._link(x, sims, nba)
        if link: m.setdefault((link[0], str(link[1])), []).append(x)
    _IDX.update(at=now, map=m)
    return m


def _best(cands):
    """(palier, candidat) le plus interessant, ou (None, le moins couteux) s'il n'y a rien a jouer."""
    for t, key in (("value", lambda x: -x["ev"]), ("safe", lambda x: (-x["p"], -x["ev"])), ("best", lambda x: -x["ev"])):
        c = sorted((x for x in cands if _tier(x) == t), key=key)
        if c: return t, c[0]
    c = sorted(cands, key=lambda x: -x["ev"])
    return None, (c[0] if c else None)


TAGS = {"value": ("🎯 À jouer", "acc"), "safe": ("🛡️ Safe", "acc"), "best": ("💡 Meilleur prix", "warn"),
        "sim": ("🔎 Pick du simulateur", "warn"), None: ("⏸ Rien à jouer", "mu")}


def _main(t, x):
    if x is None: return "Pas encore de cote française pour ce match."
    ref = f"juste Pinnacle {_fr(1 / x['p_fair'])}" if x["kind"] == "value" else f"marché {_pc(x['p_fair'])}"
    if x.get("p_sim") is not None: ref += f", ma simu {_pc(x['p_sim'])}"
    cost = (f'espérance <b class="eb-acc">{100 * x["ev"]:+.0f} %</b>' if x["ev"] >= 0
            else f'coût attendu <b>{_fr(100 * -x["ev"], 1)} %</b> de la mise')
    if t is None:
        return (f'Aucune cote ne bat la marge du bookmaker. Le moins cher : {_e(x["label"])} @ {_fr(x["price"])} '
                f'({_e(x["book"])}), {cost}.')
    return (f'<b>{_e(x["label"])}</b> <span class="vd-odds">@ {_fr(x["price"])}</span> <span class="eb-dim">{_e(x["book"])}</span>'
            f'<span class="vd-sub">gagné {_pc(x["p"])} ({ref}) · {cost}</span>')


def _card(t, main, why):
    lab, cls = TAGS[t]
    return (f'<div class="eb-card vd"><div class="vd-top"><span class="vd-tag vd-{cls}">{lab}</span>'
            f'<span class="vd-main">{main}</span></div><ul class="vd-why">'
            + "".join(f"<li>{w}</li>" for w in why if w) + '</ul></div>')


def foot(g):
    try:
        t, x = _best(_index().get(("foot", str(g["id"])), []))
        main = _main(t, x)
        pk = g.get("pick")
        if t is None and pk:
            t = "sim"
            main = (f'<b>{_e(pk["label"])}</b> <span class="vd-odds">@ {_fr(pk["dec"])}</span>'
                    f'<span class="vd-sub">ma simu {_pc(pk["p_sim"])} contre {_pc(pk["p_mkt"])} pour le bookmaker '
                    f'(+{100 * (pk["p_sim"] - pk["p_mkt"]):.0f} pts)</span>')
        p, (gh, ga) = g["p"], g["goals"]
        h, a = g["home"]["name"], g["away"]["name"]
        fav = max((("1", h), ("2", a)), key=lambda k: p[k[0]])
        why = [f'Simulation (10 000 matchs) : <b>{_e(fav[1])} {_pc(p[fav[0]])}</b> · nul {_pc(p["X"])} · '
               f'{_e(a if fav[0] == "1" else h)} {_pc(p["2" if fav[0] == "1" else "1"])}',
               f'Buts attendus <b>{_fr(gh)} - {_fr(ga)}</b> : plus de 2,5 à {_pc(p["over"]["2.5"])}, les deux marquent '
               f'à {_pc(p["btts"])}']
        ab = []
        for s in ("home", "away"):
            info = g.get(s + "_info") or {}
            names = [z["name"] for z in info.get("absences") or [] if z.get("impact", 0) >= 0.05]
            if names: ab.append(f'{_e(g[s]["name"])} sans {_e(", ".join(names[:3]))}' + (" …" if len(names) > 3 else ""))
        cx = [(g[s]["name"], (g.get(s + "_info") or {}).get("ctx")) for s in ("home", "away")]
        if all(c for _, c in cx):   # contexte : repos et matchs enchaines (06/11/2026)
            why.append("Contexte : " + " ; ".join(f'{_e(n)} {c["rest"]} j de repos, {c["n14"]} match{"s" if c["n14"] > 1 else ""} '
                                                   f'en 14 j' for n, c in cx))
        lineup = (g.get("home_info") or {}).get("lineup", "")
        why.append(("Absences notables : " + " ; ".join(ab) if ab else "Aucune absence notable")
                   + (f' <span class="eb-dim">· composition {_e(lineup)}</span>' if lineup else ""))
        return _card(t, main, why)
    except Exception as e:
        print(f"  [!] verdict foot {g.get('id')} : {type(e).__name__}: {e}")
        return ""


def nba(g):
    try:
        t, x = _best(_index().get(("nba", str(g["gid"])), []))
        main = _main(t, x)
        T, L = g.get("team") or {}, g.get("lines") or {}
        why = []
        # Selection du simulateur NBA : en reel, pas d'avantage demontre (CLV negative, PICK_NOTE de nba_sim_site) ->
        # jamais presentee comme le verdict, seulement comme une piste.
        pk = sorted((p for p in g.get("props") or [] if p.get("pick")),
                    key=lambda p: -abs(p.get("p_sim", 0) - p.get("p_mkt", 0)))
        if t is None and pk:
            p = pk[0]
            over = p["p_sim"] >= p["p_mkt"]
            why.append(f'Piste du simulateur : {_e(p["name"])} {"Over" if over else "Under"} {p["line"]:g} '
                       f'{_e(PJ.UNIT.get(p["stat"], p["stat"]))} (simu {_pc(p["p_sim"] if over else 1 - p["p_sim"])}, '
                       f'marché {_pc(p["p_mkt"] if over else 1 - p["p_mkt"])}) <span class="eb-dim">· sans avantage démontré '
                       'en réel à ce jour, à lire comme une analyse</span>')
        if T.get("p_home_win") is not None:
            ph = T["p_home_win"]
            fav, pf = (g["home"]["name"], ph) if ph >= 0.5 else (g["away"]["name"], 1 - ph)
            why.append(f'Simulation (10 000 matchs) : <b>{_e(fav)} gagne {_pc(pf)}</b> · écart moyen '
                       f'{_fr(abs(T.get("margin_mean", 0)), 1)} pts')
        if T.get("total_mean") is not None:
            why.append(f'Total attendu <b>{_fr(T["total_mean"], 1)}</b> pts'
                       + (f' (ligne {_fr(L["total"], 1)} : plus à {_pc(T["p_over_total"])})'
                          if L.get("total") is not None and T.get("p_over_total") is not None else ""))
        out = [f'{_e(g[s]["abbr"])} sans {_e(", ".join(z["name"] for z in (g.get("absences") or {}).get(s, [])[:3]))}'
               for s in ("home", "away") if (g.get("absences") or {}).get(s)]
        why.append("Absences : " + " ; ".join(out) if out else "Aucune absence signalée")
        return _card(t, main, why)
    except Exception as e:
        print(f"  [!] verdict NBA {g.get('gid')} : {type(e).__name__}: {e}")
        return ""


def fold(inner, what):
    """Cartes detaillees repliees sous un seul clic (etat memorise, voir SCRIPT)."""
    if not inner: return ""
    return (f'<details class="eb-fold"><summary>Analyse détaillée <small>{what}</small></summary>'
            f'<div class="eb-fold-in">{inner}</div></details>')


CSS = """<style>
.vd{border-color:var(--line2)}
.vd-top{display:flex;flex-direction:column;gap:8px}
.vd-tag{align-self:flex-start;font:700 12px var(--mono);padding:3px 9px;border-radius:6px;background:var(--chip);color:var(--mu)}
.vd-tag.vd-acc{color:var(--acc);box-shadow:inset 0 0 0 1px var(--acc)}
.vd-tag.vd-warn{color:var(--warn);box-shadow:inset 0 0 0 1px var(--warn)}
.vd-main{font-size:16px;line-height:1.45}
.vd-odds{font-family:var(--mono);font-weight:700;color:var(--acc)}
.vd-sub{display:block;font-size:13px;color:var(--mu);margin-top:2px}
.vd-why{margin:12px 0 0;padding-left:18px;font-size:13.5px;line-height:1.55;color:var(--tx)}
.vd-why li{margin:3px 0}
.eb-fold{margin:0}
.eb-fold>summary{cursor:pointer;list-style:none;background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:14px 18px;
  font-weight:600;font-size:15px;display:flex;align-items:center;gap:8px}
.eb-fold>summary::-webkit-details-marker{display:none}
.eb-fold>summary::before{content:"▸";color:var(--acc);transition:transform .2s}
.eb-fold[open]>summary::before{transform:rotate(90deg)}
.eb-fold>summary small{font:12px var(--mono);color:var(--mu);font-weight:400}
.eb-fold-in{display:flex;flex-direction:column;gap:16px;margin-top:16px}
</style>"""

SCRIPT = """<script>
(function(){
  var K='sp_fold_open',v=null;try{v=localStorage.getItem(K);}catch(e){}
  document.querySelectorAll('details.eb-fold').forEach(function(d){
    if(v==='1')d.open=true;
    if(d._fw)return;d._fw=1;
    d.addEventListener('toggle',function(){try{localStorage.setItem(K,d.open?'1':'0');}catch(e){}
      document.querySelectorAll('details.eb-fold').forEach(function(o){if(o!==d&&o.open!==d.open)o.open=d.open;});});
  });
})();
</script>"""
