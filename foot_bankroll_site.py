"""
foot_bankroll_site.py — Foot › Bankroll IA : le « défi x2 » de foot_bankroll (data/foot_bankroll.json).

Même principe que la Bankroll IA NBA (nba_bankroll_site) : la page est écrite dans foot_bankroll.html (publiée par la
veille à chaque pari placé ou réglé) ; index.html en garde une copie de secours, remplacée si plus récente.
"""
import math
import os
from datetime import datetime, timezone

import foot_bankroll as F
import nba_bankroll_site as BS

FRAGMENT = "foot_bankroll.html"
_e, _eur, _pct, _when, _stat = BS._e, BS._eur, BS._pct, BS._when, BS._stat


def _gauge(state, ch):
    eq = F.equity(state)
    span = max(ch["target"] - ch["start_eq"], 0.01)
    prog = min(max((eq - ch["start_eq"]) / span, 0.0), 1.0)
    left = F._t(ch["end_at"]) - datetime.now(timezone.utc)
    days = min(F.DAYS, max(0, math.ceil(left.total_seconds() / 86400 - 0.01)))
    won = bool(ch.get("won_at"))
    lab = ("🏆 Objectif atteint : plus aucun pari jusqu’à la fin du défi" if won else
           f'{_eur(eq)} sur {_eur(ch["target"])} · encore {_eur(max(ch["target"] - eq, 0))} à gagner')
    return (f'<div class="fb-gauge"><div class="fb-g-top"><b>Défi x2 n°{ch["n"]}</b>'
            f'<span>{"terminé" if days == 0 else f"J-{days}"} · du {_when(ch["start_at"])[:5]} au {_when(ch["end_at"])[:5]}</span></div>'
            f'<div class="fb-bar"><i data-w="{100 * prog:.1f}"></i></div>'
            f'<div class="fb-g-lab"><span>{_eur(ch["start_eq"])}</span><b class="{"eb-acc" if won else ""}">{lab}</b>'
            f'<span>{_eur(ch["target"])}</span></div>'
            f'<div class="fb-g-pal">🔒 Plancher sécurisé <b>{_eur(F.floor(ch, eq))}</b> · paliers '
            + " · ".join(f'<span class="{"on" if ch.get("best", ch["start_eq"]) >= ch["start_eq"] * k - 1e-6 else ""}">'
                         f'{_eur(ch["start_eq"] * k)}</span>' for k in F.LEVELS[1:])
            + '</div></div>')


def _history(state):
    rows = ""
    for ch in reversed(state.get("challenges") or []):
        st = ch.get("status", "en cours")
        cls = {"gagné": "acc", "terminé": "neg"}.get(st, "")
        end = ch.get("end_eq")
        rows += (f'<tr><td><b>n°{ch["n"]}</b></td><td class="eb-dim">{_when(ch["start_at"])[:5]} → {_when(ch["end_at"])[:5]}</td>'
                 f'<td class="n">{_eur(ch["start_eq"])}</td><td class="n">{_eur(ch["target"])}</td>'
                 f'<td class="n">{_eur(ch.get("best", ch["start_eq"]))}</td><td class="n">{_eur(end) if end is not None else "–"}</td>'
                 f'<td><span class="eb-tag {cls}">{_e(st)}</span></td></tr>')
    return ('<div class="eb-card"><h3 class="eb-h">Les défis</h3><div class="eb-scroll"><table class="eb-tbl eb-cards">'
            '<tr><th>Défi</th><th>Période</th><th>Départ</th><th>Objectif</th><th>Plus haut</th><th>Fin</th><th>Statut</th></tr>'
            f'{rows}</table></div></div>')


def _live_card():
    """Alertes live de 2e mi-temps (foot_live_alerts) : pour Wilson, hors bankroll."""
    try:
        import foot_live_alerts as LA
        al = LA.FP._load(LA.PATH, {"alerts": []})["alerts"]
        s = LA.stats()
    except Exception:
        return ""
    rows = "".join(
        f'<tr><td class="n eb-dim">{_when(a["at"])}</td><td><b>{_e(a["team"])}</b> <span class="eb-dim">{_e(a["match"])}</span>'
        f'<div class="eb-dim eb-bk-why">{a["minute"]}e, {_e(a["score"])} · tirs cadrés {a["sot"][0]}-{a["sot"][1]}, '
        f'tirs {a["shots"][0]}-{a["shots"][1]}</div></td><td class="n">{_pct(a["p"])}</td><td class="n">{a["min_odds"]:.2f}</td>'
        f'<td>{"<span class=\"eb-tag acc\">a marqué</span>" if a.get("result") == "win" else "<span class=\"eb-tag neg\">n’a pas marqué</span>" if a.get("result") == "loss" else "<span class=\"eb-tag\">en cours</span>"}</td></tr>'
        for a in sorted(al, key=lambda a: a["at"], reverse=True)[:40])
    sub = (f'{s["win"]}/{s["n"]} réussies · rendement à la cote minimum {_pct(s["roi_min"], True)}' if s["n"]
           else "aucune alerte réglée pour l’instant")
    return ('<div class="eb-card"><h3 class="eb-h">⏱️ Alertes live 2e mi-temps <small>pour toi, hors bankroll · ' + sub + '</small></h3>'
            + (f'<div class="eb-scroll"><table class="eb-tbl eb-cards"><tr><th>Alerte</th><th>Équipe</th><th>Ma proba</th>'
               f'<th>Cote mini</th><th>Résultat</th></tr>{rows}</table></div>' if rows else
               '<div class="eb-empty">Pas encore d’alerte : entre la 52e et la 68e, une équipe qui ne mène pas, n’a pas marqué '
               'et domine (au moins 3 tirs cadrés, deux fois plus que l’adversaire).</div>')
            + '<p class="eb-note">Pas de cote en direct disponible : la bankroll ne peut pas jouer ce pari, l’alerte part sur '
              'Telegram avec ma probabilité et la cote minimum à prendre. Modèle simple (buts attendus de l’équipe avant le match, '
              'minutes restantes, bonus de domination), pas encore vérifié : on le jugera sur ce suivi.</p></div>')


TITLE = {"foot": ("⚽ Bankroll IA foot · gérée seule par Claude", "foot-bank", "fbank"),
         "nba": ("🏀 Bankroll IA NBA · gérée seule par Claude", "nba-bank", "bank")}


def build_section(state=None, sp="foot", extra=""):
    state = state if state is not None else F.load(sp=sp)
    if not state.get("challenges"):
        F.challenge(state)   # affichage seulement (pas d'ecriture) : defi n°1 tel qu'il demarrera
    ch = state["challenges"][-1]
    eq = F.equity(state)
    open_ = [b for b in state["bets"] if b["status"] == "open"]
    done = [b for b in state["bets"] if b["status"] in ("won", "lost")]
    stakes = sum(b["stake"] for b in done)
    w = sum(b["status"] == "won" for b in done)
    pnl = sum(b.get("pnl", 0.0) for b in done)
    R = F.RULES
    fl = F.floor(ch, eq)
    risk = sum(b["stake"] for b in open_)
    brake = eq < 0.5 * ch["start_eq"]
    stats = (_stat("Capital", _eur(eq), f'{_pct(eq / ch["start_eq"] - 1, True)} sur ce défi', "eb-acc" if eq >= ch["start_eq"] else "eb-neg")
             + _stat("En jeu", _eur(risk), f'{len(open_)} pari{"s" if len(open_) > 1 else ""} en cours')
             + _stat("Pire cas", _eur(eq - risk), f'si tout ce qui est en cours perd · plancher {_eur(fl)}',
                     "eb-acc" if eq - risk >= fl - 0.01 else "eb-neg")
             + _stat("Paris réglés", f'{w}-{len(done) - w}', f'ROI {_pct(pnl / stakes, True) if stakes else "–"} sur {_eur(stakes)} misés')
             + _stat("Mode", "frein" if brake else "objectif atteint" if ch.get("won_at") else "défi",
                     f'mises divisées par 2 (sous {_eur(0.5 * ch["start_eq"])})' if brake else "plus de pari jusqu’à la fin" if ch.get("won_at")
                     else "mises de 8 à 15 % selon la confiance", "eb-neg" if brake else "eb-acc"))
    o_rows = "".join(BS._bet_row(b, True) for b in sorted(open_, key=lambda b: b["placed_at"], reverse=True))
    d_rows = "".join(BS._bet_row(b, False) for b in sorted((b for b in state["bets"] if b["status"] != "open"),
                                                         key=lambda b: b.get("settled_at", ""), reverse=True)[:80])
    head = '<tr><th>Placé</th><th>Pari</th><th>Cote</th><th>Mise</th>'
    curve_state = dict(state, start=ch["start_eq"], bets=[b for b in state["bets"] if b.get("challenge") == ch["n"]])
    rules = [
        f'<b>Le défi</b> (demandé par Wilson le 26/11/2026 pour le foot, le 27/11 pour la NBA) : doubler la bankroll en '
        f'{F.DAYS} jours, en pariant ce que je veux, combien je veux. Quand un défi se termine, le suivant repart du solde, '
        'avec de nouveau l’objectif x2.',
        '<b>Paliers sécurisés</b> (Wilson, 27/11/2026 : « il fallait anticiper que si les 2 ne passaient pas, tu passais sous '
        'l’objectif ») : paliers ' + " / ".join(_eur(ch["start_eq"] * k) for k in F.LEVELS[1:]) + '. Dès qu’un palier est '
        'atteint, le précédent est sécurisé : avant chaque pari, je compte le <b>pire cas</b> (tous les paris en cours perdus) '
        f'et il ne doit jamais passer sous ce plancher. Avant le 1er palier, au plus {_pct(1 - F.FLOOR0)} du départ en jeu. '
        f'Coincé au plancher : une seule petite mise ({_pct(F.STUCK)}) quand rien n’est en cours. Chiffré (Monte Carlo) : '
        'chances de doubler 7,8 % → 5,2 %, risque de finir sous la moitié 23 % → 0 %.',
        f'<b>Chaque jour de match</b> : au plus {R["per_day_single"]} paris simples (cote {R["single_odds"][0]:.2f} à '
        f'{R["single_odds"][1]:.2f}) et {R["per_day_combo"]} combiné de 2 ou 3 matchs différents (cote totale '
        f'{R["combo_odds"][0]:g} à {R["combo_odds"][1]:g}, coût ≤ {_pct(-R["combo_cost"])}), un seul pari par match.',
        f'<b>Mise selon ma confiance</b> : forte {_pct(R["stake"]["forte"])} du capital (espérance positive et ma simulation '
        f'd’accord), moyenne {_pct(R["stake"]["moyenne"])} (coût ≤ 3 %, simulation pas contre), correcte '
        f'{_pct(R["stake"]["correcte"])} (coût ≤ 6 %) ; combiné {_pct(R["combo_stake"])}. Au plus {_pct(R["day_max"])} '
        'du capital engagé sur une même journée. Les pourcentages portent sur le capital de <b>départ du défi</b> (Wilson, '
        '27/11 : « si tu gagnes, pas forcément mettre plus ») : les gains ne gonflent pas les mises, une perte les réduit. '
        'Au-delà d’une cote de 2,50 la mise baisse avec la cote (×0,71 à 3,10, ×0,5 à 4,00) ; buteur ou passeur : 10 % au plus.',
        (f'<b>Paris joueurs</b> (depuis le 26/11/2026) : buteur ou passeur décisif <b>titulaire</b>, une fois les compositions '
        f'officielles publiées (~1 h avant le match) ; au plus {R["per_day_player"]} par jour et 1 par match, cote '
        f'{R["player_odds"][0]:.2f} à {R["player_odds"][1]:.2f}, même barème de confiance. DraftKings ne donne qu’une cote '
        '(pas le « non ») : je suppose 10 % de marge et ne garde que la moitié de l’écart entre mon modèle et le bookmaker. '
        'Buteur : ma simulation foot ; passeur : passes décisives par 90 min de la saison, minutes attendues et buts '
        'attendus de son équipe (modèle pas encore vérifié). Pas de joueur décisif, de buteur à la mi-temps ni de pari en '
        'direct : aucune cote fiable disponible.') if sp == "foot" else
        (f'<b>Props NBA</b> : les lignes DraftKings des joueurs, avec ma probabilité calée (simulation tirée vers le '
         f'bookmaker, stat par stat) ; au plus {R["per_day_player"]} par nuit et 1 par match, cote {R["player_odds"][0]:.2f} '
         f'à {R["player_odds"][1]:.2f} ; décision dans les 2 h 30 avant le match (rapport officiel des blessures). '
         'Avant le 27/11, cette bankroll était gérée en mode prudent (100 → 110,77 € en 4 semaines) : ses anciens paris '
         'restent dans l’historique.'),
        f'<b>Freins</b> : sous la moitié du départ du défi, mises divisées par 2. Objectif atteint : je fige la bankroll '
        'jusqu’à la fin du défi. Bankroll à sec : recharge à 100 €, notée à part.',
        '<b>Honnêtement</b> : aucun avantage n’est démontré sur ces paris (simulation et marché font jeu égal). Calcul fait '
        'avant de lancer le défi (200 000 défis simulés) : environ 15 à 20 % de chances de doubler en 10 jours, et 25 à 30 % '
        'de finir sous 50 €. Un défi gagné sera surtout de la réussite : on le juge sur plusieurs défis.',
        'Cotes DraftKings publiées par ESPN : bankroll fictive, aucun argent réel. La cote française estimée est environ '
        '15 % plus basse (indiquée dans le message Telegram de chaque pari).',
    ]
    title, tag, eb = TITLE[sp]
    return (f'<!--{tag}:{_e(state.get("updated", ""))}-->'
            f'<!--eb:start--><div class="eb eb-bk eb-fbk" data-eb="{eb}">'
            f'<div class="eb-card"><div class="eb-meta">{title}</div>'
            f'<h3 class="eb-h eb-bil-t">Défi : x2 en {F.DAYS} jours</h3>{_gauge(state, ch)}'
            f'<div class="eb-stats">{stats}</div>{BS._curve(curve_state)}</div>'
            '<div class="eb-card"><h3 class="eb-h">Paris en cours</h3>'
            + (f'<div class="eb-scroll"><table class="eb-tbl eb-cards">{head}<th>Gain possible</th></tr>{o_rows}</table></div>' if o_rows
               else '<div class="eb-empty">Aucun pari en cours : ' + ("je choisis chaque jour de match à partir de 9 h."
                                                                        if sp == "foot" else "je choisis dans les 2 h 30 avant chaque match.")
               + '</div>') + '</div>'
            '<div class="eb-card"><h3 class="eb-h">Historique</h3>'
            + (f'<div class="eb-scroll"><table class="eb-tbl eb-cards">{head}<th>Résultat</th><th>Gain</th></tr>{d_rows}</table></div>' if d_rows
               else '<div class="eb-empty">Pas encore de pari réglé.</div>') + '</div>'
            + _history(state) + (_live_card() if sp == "foot" else "") + extra
            + '<div class="eb-card"><h3 class="eb-h">Comment je gère</h3><ul class="eb-why-l">'
            + "".join(f"<li>{r}</li>" for r in rules) + '</ul></div>'
            '</div>' + CSS + '<!--eb:end-->')


CSS = """<style>
.fb-gauge{margin:10px 0 14px}
.fb-g-top{display:flex;justify-content:space-between;gap:10px;flex-wrap:wrap;font-size:13.5px;color:var(--mu)}
.fb-g-top b{color:var(--tx);font-size:15px}
.fb-bar{height:14px;border-radius:8px;background:var(--chip,rgba(148,163,184,.12));overflow:hidden;margin:8px 0 6px}
.fb-bar i{display:block;height:100%;width:0;background:linear-gradient(90deg,#4ade80,var(--acc));border-radius:8px}
.fb-g-lab{display:flex;justify-content:space-between;gap:8px;font-size:12.5px;color:var(--mu);flex-wrap:wrap}
.fb-g-lab b{color:var(--tx);text-align:center;flex:1}
.fb-g-pal{margin-top:8px;font-size:12.5px;color:var(--mu)}.fb-g-pal b{color:var(--tx)}
.fb-g-pal span{padding:1px 6px;border-radius:6px;background:var(--chip,rgba(148,163,184,.1))}
.fb-g-pal span.on{background:rgba(149,224,74,.18);color:var(--tx);font-weight:700}
</style><script>
(function(){
  function fill(){document.querySelectorAll('.fb-bar i[data-w]').forEach(function(i){i.style.width=i.getAttribute('data-w')+'%';});}
  fill();
  if(!window.fbObs&&window.MutationObserver){window.fbObs=new MutationObserver(fill);
    window.fbObs.observe(document.body,{childList:true,subtree:true});}
})();
</script>"""


def write_fragment(sec=None):
    sec = sec if sec is not None else build_section()
    if not sec: raise ValueError("fragment vide : page precedente conservee")
    tmp = FRAGMENT + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(sec)
    os.replace(tmp, FRAGMENT)


def build_page():
    """(bloc pour index.html, contenu de foot_bankroll.html) : copie de secours + chargeur."""
    sec = build_section()
    v = sec[len("<!--foot-bank:"):sec.index("-->")]
    return f'<div id="eb-fbank-wrap" data-v="{v}">{sec}</div>' + LOADER, sec


LOADER = """<script>
(function(){
  var wrap=document.getElementById('eb-fbank-wrap');if(!wrap||window.ebFBankLive)return;
  var last=0;
  function run(el){el.querySelectorAll('script').forEach(function(s){var n=document.createElement('script');
    n.textContent=s.textContent;s.parentNode.replaceChild(n,s);});}
  window.ebFBankLive=function(){
    last=Date.now();
    fetch('""" + FRAGMENT + """?t='+Date.now(),{cache:'no-store'}).then(function(r){if(!r.ok)throw 0;return r.text();})
      .then(function(h){var m=/^<!--foot-bank:([^>]*)-->/.exec(h);
        if(m&&m[1]>(wrap.getAttribute('data-v')||'')){wrap.innerHTML=h;wrap.setAttribute('data-v',m[1]);run(wrap);}})
      .catch(function(){});
  };
  ebFBankLive();
  document.addEventListener('visibilitychange',function(){
    if(document.visibilityState==='visible'&&Date.now()-last>120000)ebFBankLive();});
})();
</script>"""
