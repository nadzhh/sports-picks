"""
site_theme.py — Charte graphique du site : sobre, plate, un seul accent vert citron.

Inspiration : maquette « EdgeBet » (noir legerement vert, cartes plates a bordure
fine, Space Grotesk pour les textes et chiffres, JetBrains Mono pour les metadonnees).

Deux niveaux :
1. COMPOSANTS (.eb-*) : bibliotheque partagee par les onglets reconstruits
   (Simulation NBA : nba_sim_site.py, Analyse Foot : foot_analyse_site.py).
2. ANCIENS ONGLETS : generes avec ~42 000 styles ecrits en dur sur une palette
   « slate ». remap_tokens() remplace ces jetons (fonds, bordures, gris, bleu et vert
   d'accent) par ceux de la charte ; THEME_CSS restyle les composants communs
   (en-tete, navigation, sous-onglets, legendes, ecran de connexion).
   Les blocs entre <!--eb:start--> et <!--eb:end--> (deja a la charte) sont exclus.

Appele par generate_site.main() avant l'allegement optimize_html : apply(html) -> html.
"""
import re

ACCENT = "#95e04a"

# Ancienne palette -> charte (rouge, ambre, orange gardent leur sens)
PALETTE = {
    "#020617": "#0a0d0b", "#08090d": "#0a0d0b", "#0a0a0a": "#0a0d0b",
    "#0a1628": "#0f1411", "#0f1115": "#0f1411", "#0e1014": "#0f1411", "#0f172a": "#0f1411",
    "#0d1b2e": "#111712", "#0f1f3a": "#121a14", "#14161b": "#121915",
    "#1a2540": "#1a221c", "#1e293b": "#1a221c", "#1f232c": "#1d261f", "#1a1d24": "#182019", "#1c1917": "#1a221c",
    "#334155": "#2a352c",
    "#1e3a5f": "#1d2a12", "#1e3a8a": "#2d4a12",
    # bleu d'accent -> vert (assez fonce pour garder un texte blanc lisible dessus)
    "#3b82f6": "#5b9e2d", "#60a5fa": "#a3d977", "#bfdbfe": "#d9f2c2",
    # verts « gagne / positif » -> citron de la charte
    "#22c55e": ACCENT, "#34d399": ACCENT, "#10b981": "#7cc93a", "#16a34a": "#6aa834",
    "#4ade80": "#b0ea7a", "#86efac": "#c5f09d",
    # gris de texte
    "#94a3b8": "#9aa89e", "#8b8d98": "#9aa89e", "#64748b": "#6b7a70", "#475569": "#4b574e",
    "#cbd5e1": "#d0dbd3",
}
RGBA = {
    "rgba(59,130,246": "rgba(149,224,74", "rgba(59, 130, 246": "rgba(149, 224, 74",
    "rgba(52,211,153": "rgba(149,224,74", "rgba(52, 211, 153": "rgba(149, 224, 74",
    "rgba(34,197,94": "rgba(149,224,74", "rgba(34, 197, 94": "rgba(149, 224, 74",
}
BORDERS = [
    (re.compile(r"(border(?:-top|-bottom|-left|-right)?\s*:\s*1px solid )#1e293b", re.I), r"\g<1>#1f2a22"),
    (re.compile(r"(border(?:-top|-bottom|-left|-right)?\s*:\s*1px solid )#334155", re.I), r"\g<1>#2a352c"),
]
HEX_RE = re.compile(r"#[0-9a-fA-F]{6}(?![0-9a-fA-F])")
SKIP_START, SKIP_END = "<!--eb:start-->", "<!--eb:end-->"


def remap_tokens(text):
    for pat, rep in BORDERS:
        text = pat.sub(rep, text)
    text = HEX_RE.sub(lambda m: PALETTE.get(m.group(0).lower(), m.group(0)), text)
    for a, b in RGBA.items():
        text = text.replace(a, b)
    return text


def apply(html):
    """Applique la charte a la page complete (hors blocs deja a la charte)."""
    parts, pos = [], 0
    while True:
        i = html.find(SKIP_START, pos)
        j = html.find(SKIP_END, i) if i >= 0 else -1
        if i < 0 or j < 0:
            parts.append(remap_tokens(html[pos:]))
            break
        parts.append(remap_tokens(html[pos:i]))
        parts.append(html[i:j + len(SKIP_END)])
        pos = j + len(SKIP_END)
    out = "".join(parts)
    k = out.find("</head>")
    return out[:k] + FONTS + THEME_CSS + out[k:] if k >= 0 else out


FONTS = ('<link rel="preconnect" href="https://fonts.googleapis.com">'
         '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
         '<link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;600;700'
         '&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">')

THEME_CSS = """<style id="sp-theme">
:root{--bg:#0a0d0b;--panel:#0f1411;--panel2:#141b16;--chip:#1a221c;--line:#1f2a22;--line2:#2a352c;
  --tx:#eef3ef;--mu:#9aa89e;--di:#6b7a70;--acc:#95e04a;--acc-ink:#0b1208;--acc-soft:rgba(149,224,74,.1);
  --loss:#ef4444;--warn:#f5b942;
  --sans:'Space Grotesk',-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
  --mono:'JetBrains Mono',ui-monospace,SFMono-Regular,Consolas,monospace;color-scheme:dark}
html{background:var(--bg)}
body{font-family:var(--sans)!important;background:var(--bg)!important;color:var(--tx)}
button,input,select,textarea{font-family:inherit}
::selection{background:rgba(149,224,74,.3);color:#fff}
*{scrollbar-width:thin;scrollbar-color:var(--line2) transparent}
::-webkit-scrollbar{width:8px;height:8px}
::-webkit-scrollbar-thumb{background:var(--line2);border-radius:8px}
::-webkit-scrollbar-track{background:transparent}

/* ── En-tete et navigation ─────────────────────────────────────────────── */
.sp-top{display:flex;align-items:center;justify-content:space-between;gap:16px;flex-wrap:wrap;
  padding:6px 0 18px;margin-bottom:18px;border-bottom:1px solid var(--line)}
.container h1.sp-brand{font-family:var(--sans);font-size:26px!important;font-weight:700!important;letter-spacing:-.6px;margin:0!important;color:var(--tx)}
.sp-brand span{color:var(--acc)}
.sp-brand .sp-logo{display:inline-block;width:34px;height:34px;vertical-align:-8px;margin-right:10px}
.sp-meta{font-family:var(--mono);font-size:12.5px;color:var(--mu)}
.sp-meta b{color:var(--tx);font-weight:500}
.sp-nav{display:flex!important;gap:6px!important;flex-wrap:wrap;margin-bottom:18px!important}
.sport-btn{background:transparent!important;border:1px solid var(--line)!important;border-radius:10px!important;color:var(--mu)!important;
  padding:9px 16px!important;font-size:14px!important;font-weight:500!important;letter-spacing:0!important;box-shadow:none!important;transition:border-color .15s,color .15s!important}
.sport-btn:hover{border-color:var(--line2)!important;color:var(--tx)!important;background:transparent!important}
.sport-btn.active{border-color:var(--acc)!important;color:var(--tx)!important;background:var(--acc-soft)!important;box-shadow:none!important}
.sub-tg-bar{background:var(--panel)!important;border:1px solid var(--line)!important;border-radius:10px!important;padding:4px!important;gap:2px!important}
.sub-tg{border-radius:7px!important;color:var(--mu)!important;font-weight:500!important}
.sub-tg:hover{background:var(--chip)!important;color:var(--tx)!important}
.sub-tg.active{background:var(--chip)!important;color:var(--acc)!important;box-shadow:none!important}
.tab-btn{background:var(--panel)!important;border:1px solid var(--line)!important;border-radius:8px!important;color:var(--mu)!important;font-weight:500!important}
.tab-btn:hover{border-color:var(--line2)!important;color:var(--tx)!important}
.tab-btn.active{background:var(--acc-soft)!important;border-color:var(--acc)!important;color:var(--tx)!important}
.tab-count{background:var(--chip)!important;color:var(--mu);font-family:var(--mono)}
.legend{background:var(--panel)!important;border:1px solid var(--line)!important;border-radius:12px!important;color:var(--mu)!important;
  font-size:12.5px!important}
.legend b{color:var(--tx)}
.pick-card,.bk-card{border-radius:14px!important}
.bk2-pill,.hist-btn,.tg-global-prop-btn,.tg-global-window-btn{border-radius:8px!important}
footer{color:var(--di)!important;border-top:1px solid var(--line)!important;font-family:var(--mono)}
#sport-userpicks{background:var(--panel)!important;border:1px solid var(--line)!important}
.bk-refresh-fab{background:var(--acc)!important;color:var(--acc-ink)!important;box-shadow:0 0 0 4px var(--bg)!important}

/* ── Ecran de connexion ────────────────────────────────────────────────── */
#auth-gate{background:var(--bg)!important;font-family:var(--sans)!important}
#auth-gate .gate-card{background:var(--panel)!important;border:1px solid var(--line)!important;border-radius:16px!important;box-shadow:none!important}
#auth-gate .gate-logo h1{color:var(--tx)!important;font-weight:700!important}
#auth-gate .bk-auth-tabs{background:var(--bg)!important;border-color:var(--line)!important}
#auth-gate .bk-auth-tab.active{background:var(--chip)!important;color:var(--acc)!important}
#auth-gate input.bk-m-input{background:var(--bg)!important;border-color:var(--line)!important;font-family:var(--mono)!important}
#auth-gate input.bk-m-input:focus{border-color:var(--acc)!important;box-shadow:none!important}
#auth-gate .bk-m-cta{background:var(--acc)!important;color:var(--acc-ink)!important;box-shadow:none!important;font-weight:600!important}
#auth-gate .bk-auth-link{color:var(--acc)!important}

/* ── Composants partages (.eb-*) ───────────────────────────────────────── */
.eb{color:var(--tx);font-family:var(--sans)}
.eb *{box-sizing:border-box}
.eb-off{display:none!important}
.eb-layout{display:grid;grid-template-columns:300px minmax(0,1fr);gap:24px;align-items:start}
.eb-layout>*{min-width:0}
.eb-side{position:sticky;top:12px;max-height:calc(100vh - 24px);overflow-y:auto;padding-right:4px}
.eb-label{font-family:var(--mono);font-size:11.5px;font-weight:500;letter-spacing:1.2px;text-transform:uppercase;color:var(--mu);margin:2px 0 12px}
.eb-list{display:flex;flex-direction:column;gap:10px}
.eb-item{display:block;width:100%;text-align:left;background:var(--panel);border:1px solid var(--line);border-radius:12px;
  padding:12px 14px;cursor:pointer;color:var(--tx);transition:border-color .15s}
.eb-item:hover{border-color:var(--line2)}
.eb-item.on{border-color:var(--acc)}
.eb-item-top{display:flex;justify-content:space-between;gap:8px;font-family:var(--mono);font-size:11.5px;color:var(--mu)}
.eb-item-t{font-size:16px;font-weight:600;margin:6px 0 10px;letter-spacing:-.2px}
.eb-chips{display:flex;gap:6px;flex-wrap:wrap}
.eb-chip{font-family:var(--mono);font-size:12.5px;background:var(--chip);color:var(--tx);border-radius:6px;padding:4px 9px;white-space:nowrap}
.eb-chip em{font-style:normal;color:var(--di);margin-right:5px}
.eb-chip.hl{color:var(--acc)}
.eb-main{min-width:0}
.eb-detail{display:none;flex-direction:column;gap:16px}
.eb-detail.on{display:flex}
.eb-card{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:20px 22px;min-width:0}
.eb-meta{font-family:var(--mono);font-size:12.5px;color:var(--mu)}
.eb-h{font-size:19px;font-weight:600;letter-spacing:-.3px;margin:0 0 18px}
.eb-h small{font-family:var(--mono);font-size:12px;color:var(--di);font-weight:400;margin-left:8px;letter-spacing:0}
.eb-note{font-family:var(--mono);font-size:11.5px;color:var(--di);line-height:1.6;margin-top:12px}
.eb-note-top{margin:0 0 12px}
.eb-vs-typ{margin:0 0 18px}
.eb-h-mt{margin-top:18px}
.eb-h-nomb{margin-bottom:6px}
.eb-video-head{display:flex;justify-content:space-between;align-items:center;gap:16px;flex-wrap:wrap}
.eb-video-host:not(:empty){margin-top:16px}
.rp-cv{display:block;width:100%;height:auto;aspect-ratio:16/9;border-radius:10px;border:1px solid var(--line);background:var(--bg)}
.rp-bar{display:flex;align-items:center;gap:10px;margin-top:10px;flex-wrap:wrap}
.rp-b{background:var(--chip);color:var(--tx);border:1px solid var(--line2);border-radius:8px;padding:7px 12px;font:600 14px var(--sans);cursor:pointer;min-width:44px}
.rp-b:hover{border-color:var(--acc)}
.rp-b:disabled{opacity:.6;cursor:wait}
.rp-dl{background:var(--acc);color:var(--acc-ink);border-color:var(--acc)}
.rp-seek{flex:1;min-width:140px;accent-color:var(--acc)}
.rp-time,.rp-msg{font-family:var(--mono);font-size:12.5px;color:var(--mu)}
/* en-tete de match */
.eb-vs{display:grid;grid-template-columns:1fr auto 1fr;align-items:center;gap:16px;margin-top:14px}
.eb-team{display:flex;flex-direction:column;align-items:center;gap:8px;text-align:center;min-width:0}
.eb-team-n{font-size:26px;font-weight:700;letter-spacing:-.6px;line-height:1.1}
.eb-team-sub{font-family:var(--mono);font-size:12.5px;color:var(--mu)}
.eb-team-sub b{color:var(--tx);font-weight:500}
.eb-vs-x{font-size:30px;font-weight:500;color:#3d4a40}
.eb-form{display:flex;gap:4px}
.eb-form i{width:20px;height:20px;border-radius:4px;display:inline-grid;place-items:center;font:600 10.5px var(--mono);font-style:normal}
.eb-form .v{background:var(--acc);color:var(--acc-ink)}
.eb-form .n{background:var(--line2);color:var(--mu)}
.eb-form .d{background:var(--loss);color:#fff}
/* controles */
.eb-ctrl{display:flex;align-items:flex-end;gap:14px;flex-wrap:wrap}
.eb-field{display:flex;flex-direction:column;gap:8px}
.eb-field span{font-size:14px;color:var(--tx)}
.eb-input{background:var(--bg);border:1px solid var(--line2);border-radius:8px;color:var(--tx);font:500 14px var(--mono);padding:11px 12px;min-width:110px;outline:none}
.eb-input:focus{border-color:var(--acc)}
.eb-btn{background:var(--acc);color:var(--acc-ink);border:0;border-radius:10px;padding:12px 20px;font:600 15px var(--sans);cursor:pointer;transition:filter .15s}
.eb-btn:hover{filter:brightness(1.08)}
.eb-btn:disabled{opacity:.6;cursor:default}
.eb-ctrl-info{font-family:var(--mono);font-size:12px;color:var(--mu);margin-left:auto;align-self:center}
/* indicateurs */
.eb-g3{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:16px}
.eb-kpi{display:flex;flex-direction:column;gap:10px}
.eb-kpi-l{font-size:13.5px;color:var(--mu)}
.eb-kpi-v{font-size:36px;font-weight:600;letter-spacing:-1px;line-height:1;font-variant-numeric:tabular-nums}
.eb-kpi-s{font-family:var(--mono);font-size:11.5px;color:var(--di)}
.eb-bar{height:6px;border-radius:3px;background:var(--line);overflow:hidden}
.eb-bar i{display:block;height:100%;width:var(--w);background:var(--acc);border-radius:3px;transition:width .5s ease}
.eb-tiles{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px}
.eb-tile{background:var(--chip);border-radius:8px;padding:9px 6px;text-align:center}
.eb-tile b{display:block;font-size:16px;font-weight:600}
.eb-tile small{font-family:var(--mono);font-size:11px;color:var(--mu)}
/* histogramme */
.eb-hist{display:flex;align-items:flex-end;gap:14px;height:230px;padding-top:6px}
.eb-hb{flex:1;height:100%;display:flex;flex-direction:column;justify-content:flex-end;align-items:center;min-width:0}
.eb-hb em{font-style:normal;font-family:var(--mono);font-size:11.5px;color:var(--mu);margin-bottom:6px;white-space:nowrap}
.eb-hb i{display:block;width:100%;height:var(--h);min-height:2px;background:var(--acc);border-radius:4px 4px 0 0;transition:height .5s ease}
.eb-hb.mark i{background:repeating-linear-gradient(135deg,var(--acc) 0 6px,#7cc93a 6px 12px)}
.eb-hx{display:flex;gap:14px;margin-top:8px;border-top:1px solid var(--line);padding-top:8px}
.eb-hx span{flex:1;text-align:center;font-family:var(--mono);font-size:12px;color:var(--mu);min-width:0}
/* onglets internes */
.eb-seg{display:inline-flex;gap:2px;padding:4px;border-radius:10px;background:var(--bg);border:1px solid var(--line);margin-bottom:16px;flex-wrap:wrap}
.eb-seg button{border:0;background:transparent;color:var(--mu);font:500 13.5px var(--sans);padding:7px 14px;border-radius:7px;cursor:pointer}
.eb-seg button:hover{color:var(--tx)}
.eb-seg button.on{background:var(--chip);color:var(--acc)}
.eb-pane{display:none}
.eb-pane.on{display:block}
/* tableaux */
.eb-scroll{overflow-x:auto}
.eb-tbl{width:100%;border-collapse:collapse;font-size:14px;font-variant-numeric:tabular-nums}
.eb-tbl th{font-family:var(--mono);font-size:11px;font-weight:500;text-transform:uppercase;letter-spacing:.8px;color:var(--di);text-align:left;padding:0 10px 10px;white-space:nowrap}
.eb-tbl td{padding:10px;border-top:1px solid var(--line);white-space:nowrap}
.eb-tbl td.n{font-family:var(--mono);font-size:13px}
.eb-tbl tr.sec td{font-family:var(--mono);font-size:11px;letter-spacing:1px;text-transform:uppercase;color:var(--di);padding-top:16px;border-top:0}
.eb-tbl .num{text-align:right}
.eb-tbl th.eb-sort{cursor:pointer;user-select:none}
.eb-tbl th.eb-sort:hover{color:var(--tx)}
.eb-tbl th.eb-sort[data-dir]{color:var(--acc)}
.eb-tbl th.eb-sort[data-dir=desc]::after{content:' ▼';font-size:9px}
.eb-tbl th.eb-sort[data-dir=asc]::after{content:' ▲';font-size:9px}
.eb-tbl b{font-weight:600}
.eb-dim{color:var(--di)}
.eb-acc{color:var(--acc)}
.eb-neg{color:var(--loss)}
.eb-tag{font-family:var(--mono);font-size:10.5px;padding:1px 6px;border-radius:4px;background:var(--chip);color:var(--mu);margin-left:6px}
.eb-tag.warn{color:var(--warn)}
.eb-tag.acc{color:var(--acc);box-shadow:inset 0 0 0 1px var(--acc)}
.eb-sel{margin-bottom:16px}
.eb-sel .eb-ctrl{margin-bottom:12px}
.eb-tbl tr.eb-pick{cursor:pointer}
.eb-tbl tr.eb-pick:hover td{background:var(--chip)}
.eb-tbl td.eb-why-b{font-family:var(--mono);font-size:11px;color:var(--acc);text-align:right}
.eb-tbl tr.eb-pick.open td.eb-why-b{color:var(--di)}
.eb-tbl tr.eb-why td{border-top:0;padding:0 10px 14px;white-space:normal}
.eb-why-l{margin:0;padding:12px 14px 12px 30px;background:var(--chip);border-radius:8px;font-size:13px;line-height:1.55;color:var(--mu);max-width:900px}
.eb-why-l li+li{margin-top:4px}
.eb-why-l b{color:var(--tx)}
.eb-why-l .eb-warn{color:var(--warn)}
.eb-label.eb-label-2{margin-top:24px}
.eb-bk-curve{display:block;width:100%;height:160px;margin-top:18px}
.eb-tbl td.eb-bk-legs{white-space:normal;min-width:280px;line-height:1.55}
.eb-bk-why{font-size:12px;margin-top:3px;font-family:var(--mono)}
.eb-bk .eb-card+.eb-card{margin-top:16px}
.eb-sidetabs{display:flex;gap:6px;margin:0 0 14px;padding:4px;background:var(--panel);border:1px solid var(--line);border-radius:10px}
.eb-sidetabs button{flex:1;background:transparent;border:0;border-radius:7px;padding:8px 10px;color:var(--mu);font:500 13px var(--sans);cursor:pointer;white-space:nowrap}
.eb-sidetabs button.on{background:var(--chip);color:var(--acc)}
.eb-sidepane{display:none}
.eb-sidepane.on{display:block}
.eb-banner{display:flex;flex-direction:column;gap:4px;width:100%;text-align:left;margin:0 0 16px;padding:12px 14px;background:var(--acc-soft);border:1px solid var(--acc);border-radius:12px;color:var(--tx);cursor:pointer}
.eb-banner-t{font-size:16px;font-weight:600}
.eb-banner-a{font-family:var(--mono);font-size:11.5px;color:var(--acc)}
.eb-inj{display:grid;grid-template-columns:1fr 1fr;gap:18px;margin-top:12px}
.eb-inj-l{margin:8px 0 0;padding-left:18px;font-size:13.5px;line-height:1.6;color:var(--mu)}
.eb-inj-l b{color:var(--tx);font-weight:600}
.eb-ev-t{margin-top:16px}
.eb-ev{margin:8px 0 0;padding:10px 14px 10px 30px;background:var(--chip);border-radius:8px;font-size:13px;line-height:1.6}
@media (max-width:640px){.eb-inj{grid-template-columns:1fr}}
.eb-stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px;margin-top:14px}
.eb-stat{display:flex;flex-direction:column;gap:6px;background:var(--chip);border-radius:10px;padding:12px 14px}
.eb-stat-v{font-family:var(--mono);font-size:24px;font-weight:600;letter-spacing:-.5px}
.eb-bil-t{margin-top:8px}
.eb-tag.neg{color:var(--loss);box-shadow:inset 0 0 0 1px var(--loss)}
.eb-tbl td.eb-cause{white-space:normal;min-width:220px;font-size:13px;color:var(--mu)}
.eb-rb{position:relative;display:inline-block;width:140px;height:10px;vertical-align:middle;margin-left:10px}
.eb-rb i{position:absolute;display:block}
.eb-rb .o{top:4px;height:2px;left:var(--a);right:var(--b);background:var(--line2)}
.eb-rb .in{top:1px;height:8px;left:var(--c);right:var(--d);background:var(--acc);opacity:.75;border-radius:2px}
.eb-rb .m{top:-1px;height:12px;width:2px;left:var(--m);background:var(--tx)}
.eb-rb .l{top:-3px;height:16px;width:0;left:var(--x);border-left:2px dotted var(--warn)}
.eb-pp{display:inline-block;width:70px;height:6px;border-radius:3px;background:var(--line);vertical-align:middle;margin-right:8px;overflow:hidden}
.eb-pp i{display:block;height:100%;width:var(--w);background:var(--acc)}
.eb-pp.bk i{background:var(--mu)}
.eb-more{margin-top:12px;background:transparent;border:1px solid var(--line2);color:var(--tx);border-radius:8px;padding:8px 14px;font:500 13px var(--sans);cursor:pointer}
.eb-more:hover{border-color:var(--acc)}
.eb-list2{display:flex;flex-direction:column;gap:8px}
.eb-row{display:flex;justify-content:space-between;gap:10px;font-size:14px;padding:8px 0;border-top:1px solid var(--line)}
.eb-row:first-child{border-top:0}
.eb-row span:last-child{font-family:var(--mono);color:var(--mu)}
.eb-empty{padding:40px;text-align:center;color:var(--mu);font-family:var(--mono);font-size:13px}
@media (max-width:1000px){
  .eb-layout{grid-template-columns:1fr;gap:16px}
  .eb-side{position:static;max-height:none;overflow:visible;padding:0}
  .eb-list{flex-direction:row;overflow-x:auto;padding-bottom:6px;scroll-snap-type:x mandatory}
  .eb-item{min-width:250px;scroll-snap-align:start}
}
@media (max-width:640px){
  .sp-nav{flex-wrap:nowrap!important;overflow-x:auto;padding-bottom:4px}
  .sport-btn{white-space:nowrap;flex:none;padding:8px 12px!important;font-size:13px!important}
  .eb-card{padding:16px}
  .eb-g3{grid-template-columns:1fr}
  .eb-team-n{font-size:20px}
  .eb-vs-x{font-size:22px}
  .eb-kpi-v{font-size:30px}
  .eb-hist,.eb-hx{gap:6px}
  .eb-hist{height:180px}
  .eb-hb em{font-size:9.5px}
  .eb-hx span{font-size:10.5px}
  .eb-ctrl-info{margin-left:0;width:100%}
}
</style>"""
