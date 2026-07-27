"""Render an AnalyticsResult (from analytics.py) into a premium, self-contained HTML
dashboard — CRED-grade dark aesthetic, category-first.

Deterministic and offline: no network, no clock, no CDN except the web-font link
(the page still renders with system fallbacks if fonts are blocked). Charts are
hand-rolled inline SVG. A small amount of dependency-free JS adds table search,
sort, and the theme toggle; category drill-down uses native <details> so it works
with JS disabled.

Design language:
  - pure near-black canvas, hairline dividers instead of heavy borders
  - one accent (lime) used sparingly for emphasis + positive state; else monochrome
  - oversized tabular numerals; generous whitespace
  - category is the hero: expandable cards leading the page, drill into transactions
  - staggered fade-up entrance; calm and dense after load
"""

from __future__ import annotations

import html
import math
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional

ACCENT = "#c6f24e"          # lime — the single accent
POS = "#c6f24e"             # positive (others owe you) shares the accent
NEG = "#ff8080"             # you owe / negative

# Sequential lime ramp (light->dark) for category magnitude bars + donut.
RAMP = ["#d7f877", "#c6f24e", "#a9d84f", "#8dbd4e",
        "#72a24a", "#5a8743", "#456d3a", "#33532f", "#263f26"]
MUTED = "#3a3a38"


def _e(a: Any) -> str:
    return html.escape(str(a if a is not None else ""))


def _ramp(i: int) -> str:
    return RAMP[i] if i < len(RAMP) else MUTED


def _fmt(amount: Any, currency: str = "", sign: bool = False) -> str:
    """Indian-grouped money string, 2 decimals. sign=True prefixes + for positives."""
    try:
        d = Decimal(str(amount))
    except Exception:
        return _e(amount)
    neg = d < 0
    d = abs(d)
    whole, frac = f"{d:.2f}".split(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:]); head = head[:-2]
        if head:
            parts.insert(0, head)
        whole = ",".join(parts) + "," + tail
    sym = "₹" if currency == "INR" else ("" if not currency else currency + " ")
    pre = "-" if neg else ("+" if sign else "")
    return f"{pre}{sym}{whole}.{frac}"


# --- category icons (minimal inline line-SVG, keyword-matched) --------------

def _icon(cat: str) -> str:
    c = cat.lower()

    def svg(p):
        return (f'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" '
                f'stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">{p}</svg>')
    if any(k in c for k in ("grocer", "food", "dining", "restaurant")):
        return svg('<path d="M3 2v7a3 3 0 0 0 6 0V2M6 2v20M21 15V2a5 5 0 0 0-3 5v6h3v7"/>')
    if any(k in c for k in ("electric", "utilit", "power")):
        return svg('<path d="M13 2 3 14h7l-1 8 10-12h-7z"/>')
    if any(k in c for k in ("rent", "home", "house", "furnitur")):
        return svg('<path d="M3 10 12 3l9 7v10a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1z"/>')
    if any(k in c for k in ("clean", "household", "supplies")):
        return svg('<path d="M6 3v6M4 9h4M12 3l1 5 4 12H8l4-12z"/>')
    if any(k in c for k in ("tv", "phone", "internet", "wifi", "subscription", "spotify", "youtube")):
        return svg('<rect x="2" y="4" width="20" height="13" rx="2"/><path d="M8 21h8M12 17v4"/>')
    if any(k in c for k in ("maint", "service", "repair")):
        return svg('<path d="M14 6a3.5 3.5 0 0 1-4.6 4.6L4 16v4h4l5.4-5.4A3.5 3.5 0 0 1 18 10z"/>')
    if any(k in c for k in ("transp", "travel", "taxi", "fuel", "car", "flight")):
        return svg('<path d="M5 16 3 9h18l-2 7M5 16h14M7 16v3M17 16v3M6 9l1-4h10l1 4"/>')
    if any(k in c for k in ("entertain", "movie", "game", "party", "beer", "drink")):
        return svg('<path d="M5 4h14l-2 9H7zM12 13v5M8 21h8"/>')
    return svg('<rect x="4" y="3" width="16" height="18" rx="2"/><path d="M8 8h8M8 12h8M8 16h5"/>')


# ---------------------------------------------------------------------------
# SVG charts
# ---------------------------------------------------------------------------

def _donut(categories, currency) -> str:
    if not categories:
        return "<p class='muted'>No category data.</p>"
    total = sum(Decimal(c["amount"]) for c in categories) or Decimal("1")
    cx, cy, r, w = 90, 90, 74, 20
    circ = 2 * math.pi * (r - w / 2)
    off = 0.0
    shown, rest = categories[:8], categories[8:]
    segs = []
    for i, c in enumerate(shown):
        frac = float(Decimal(c["amount"]) / total)
        seg = circ * frac
        segs.append(
            f'<circle cx="{cx}" cy="{cy}" r="{r-w/2}" fill="none" stroke="{_ramp(i)}" '
            f'stroke-width="{w}" stroke-dasharray="{seg:.3f} {circ-seg:.3f}" '
            f'stroke-dashoffset="{-off:.3f}" transform="rotate(-90 {cx} {cy})">'
            f'<title>{_e(c["category"])}: {_fmt(c["amount"],currency)} ({_e(c["percentage"])}%)</title></circle>'
        )
        off += seg
    if rest:
        ra = sum(Decimal(c["amount"]) for c in rest)
        seg = circ * float(ra / total)
        segs.append(
            f'<circle cx="{cx}" cy="{cy}" r="{r-w/2}" fill="none" stroke="{MUTED}" '
            f'stroke-width="{w}" stroke-dasharray="{seg:.3f} {circ-seg:.3f}" '
            f'stroke-dashoffset="{-off:.3f}" transform="rotate(-90 {cx} {cy})"></circle>'
        )
    return (f'<svg viewBox="0 0 180 180" class="donut" role="img" aria-label="Category split">'
            f'{"".join(segs)}</svg>')


def _nice_ceiling(v: Decimal) -> Decimal:
    if v <= 0:
        return Decimal("1")
    exp = len(str(int(v))) - 1
    base = Decimal(10) ** exp
    for m in (Decimal("1"), Decimal("2"), Decimal("2.5"), Decimal("5"), Decimal("10")):
        if v <= m * base:
            return m * base
    return 10 * base


def _monthly(months, currency) -> str:
    if not months:
        return "<p class='muted'>No monthly data.</p>"
    vals = [Decimal(m["amount"]) for m in months]
    mx = _nice_ceiling(max(vals) or Decimal("1"))
    n = len(months)
    W, H, pad_b, pad_t, pad_l = 660, 230, 44, 24, 4
    plot = H - pad_b - pad_t
    gap = 5 if n > 24 else 9
    bw = max(3, (W - pad_l - (n + 1) * gap) / n)
    show_vals = n <= 12
    every = max(1, math.ceil(n / 16))
    grid = []
    for frac, val in ((0.0, Decimal(0)), (0.5, mx / 2), (1.0, mx)):
        gy = pad_t + plot * (1 - frac)
        grid.append(f'<line x1="{pad_l}" y1="{gy:.1f}" x2="{W}" y2="{gy:.1f}" class="grid"/>'
                    f'<text x="{pad_l}" y="{gy-4:.1f}" class="gridlbl">{_fmt(str(val),currency)}</text>')
    bars = []
    for i, m in enumerate(months):
        h = float((Decimal(m["amount"]) / mx) * plot) if mx else 0
        x = pad_l + gap + i * (bw + gap)
        y = H - pad_b - h
        cx = x + bw / 2
        bars.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{bw:.1f}" height="{h:.1f}" rx="2.5" '
                    f'class="mbar"><title>{_e(m["month"])}: {_fmt(m["amount"],currency)} '
                    f'({m["count"]} txns)</title></rect>')
        if show_vals:
            bars.append(f'<text x="{cx:.1f}" y="{y-6:.1f}" class="barval">{_fmt(m["amount"],currency)}</text>')
        if i % every == 0:
            bars.append(f'<text x="{cx:.1f}" y="{H-pad_b+13:.1f}" class="barlbl" '
                        f'transform="rotate(45 {cx:.1f} {H-pad_b+13:.1f})">{_e(m["month"])}</text>')
    return f'<svg viewBox="0 0 {W} {H}" class="bars" role="img" aria-label="Monthly spend">{"".join(grid)}{"".join(bars)}</svg>'


# ---------------------------------------------------------------------------
# Sections
# ---------------------------------------------------------------------------

def _hero_number(result, currency, is_group) -> str:
    ovp = result["owed_vs_paid"]
    net = Decimal(ovp["net"])
    grand = result["category_breakdown"]["total"]
    lead_label = "Total group spend" if is_group else "Your total spend"
    net_cls = "pos" if net > 0 else ("neg" if net < 0 else "")
    net_lbl = "others owe you" if net > 0 else ("you owe others" if net < 0 else "all settled")
    return (
        f'<section class="hero card">'
        f'<div class="heromain"><div class="herolabel">{lead_label}</div>'
        f'<div class="heronum">{_fmt(grand, currency)}</div></div>'
        f'<div class="herostats">'
        f'<div class="hs"><div class="hsv">{_fmt(ovp["personal_share"], currency)}</div><div class="hsk">your share</div></div>'
        f'<div class="hs"><div class="hsv">{_fmt(ovp["you_paid"], currency)}</div><div class="hsk">you paid</div></div>'
        f'<div class="hs {net_cls}"><div class="hsv">{_fmt(ovp["net"], currency, sign=True)}</div><div class="hsk">{net_lbl}</div></div>'
        f'</div></section>'
    )


def _category_hero(result, currency) -> str:
    cats = result["category_breakdown"]["categories"]
    if not cats:
        return ""
    total = Decimal(result["category_breakdown"]["total"]) or Decimal("1")
    # group ledger rows by category for drill-down (newest first)
    by_cat: Dict[str, list] = {}
    for row in reversed(result.get("ledger", [])):
        by_cat.setdefault(row["category"], []).append(row)

    cards = []
    for i, c in enumerate(cats):
        frac = float(Decimal(c["amount"]) / total)
        txns = by_cat.get(c["category"], [])[:40]
        rows = "".join(
            f'<div class="ctx"><span class="ctxd">{_e(t["date"])}</span>'
            f'<span class="ctxn">{_e(t["description"])}</span>'
            f'<span class="ctxa num">{_fmt(t["amount"], currency)}</span></div>'
            for t in txns
        )
        more = ""
        full = by_cat.get(c["category"], [])
        if len(full) > 40:
            more = f'<div class="ctxmore">+{len(full)-40} more in this category</div>'
        cards.append(
            f'<details class="cat" style="--i:{i}">'
            f'<summary>'
            f'<span class="crow1">'
            f'<span class="cicon" style="color:{_ramp(i)}">{_icon(c["category"])}</span>'
            f'<span class="cmeta"><span class="cname">{_e(c["category"])}</span>'
            f'<span class="ccount">{c["count"]} txns</span></span>'
            f'<span class="cright"><span class="camt num">{_fmt(c["amount"], currency)}</span>'
            f'<span class="cpct num">{_e(c["percentage"])}%</span></span>'
            f'<span class="cchev">›</span>'
            f'</span>'
            f'<span class="cbar"><span class="cbarfill" style="width:{frac*100:.1f}%;background:{_ramp(i)}"></span></span>'
            f'</summary>'
            f'<div class="cbody">{rows}{more}</div>'
            f'</details>'
        )
    donut = _donut(cats, currency)
    return (
        f'<section class="card cathero">'
        f'<div class="sechead"><h2>Where it goes</h2>'
        f'<span class="muted">tap a category to expand</span></div>'
        f'<div class="catwrap"><div class="catlist">{"".join(cards)}</div>'
        f'<div class="catdonut">{donut}<div class="donutcap"><div class="num">{_fmt(result["category_breakdown"]["total"], currency)}</div><span>total</span></div></div></div>'
        f'</section>'
    )


def _member_bars(members, currency) -> str:
    if not members:
        return ""
    mx = max((Decimal(m["total_share"]) for m in members), default=Decimal("1")) or Decimal("1")
    rows = []
    for i, m in enumerate(members):
        frac = float(Decimal(m["total_share"]) / mx)
        you = '<span class="youtag">you</span>' if m.get("is_you") else ""
        col = ACCENT if m.get("is_you") else "#6a6a67"
        rows.append(
            f'<div class="mbrow"><div class="mbname">{_e(m["name"])}{you}</div>'
            f'<div class="mbtrack"><div class="mbfill" style="width:{frac*100:.1f}%;background:{col}"></div></div>'
            f'<div class="mbval num">{_fmt(m["total_share"], currency)}<span class="muted"> {_e(m["percentage_of_total"])}%</span></div></div>'
        )
    return f'<div class="mbars">{"".join(rows)}</div>'


def _matrix(matrix, member_names, currency) -> str:
    order = matrix.get("member_order", [])
    if not order:
        return ""
    head = "<tr><th>Category</th>" + "".join(
        f"<th class='num'>{_e(member_names.get(u, member_names.get(str(u), u)))}</th>" for u in order
    ) + "<th class='num'>Total</th></tr>"
    body = []
    for row in matrix["rows"]:
        cells = "".join(
            f"<td class='num'>{_fmt(row['per_member'].get(str(u), row['per_member'].get(u, '0')), currency)}</td>"
            for u in order
        )
        body.append(f"<tr><td>{_e(row['category'])}</td>{cells}<td class='num strong'>{_fmt(row['total'], currency)}</td></tr>")
    return f'<div class="tablewrap"><table class="matrix"><thead>{head}</thead><tbody>{"".join(body)}</tbody></table></div>'


def _settlement(settlement, currency) -> str:
    txns = settlement.get("transactions", [])
    if not txns:
        return "<p class='muted'>Everyone is settled up.</p>"
    items = "".join(
        f'<li><span class="from">{_e(t["from_name"])}</span><span class="arrow">→</span>'
        f'<span class="to">{_e(t["to_name"])}</span><span class="amt num">{_fmt(t["amount"], currency)}</span></li>'
        for t in txns
    )
    return (f'<p class="muted">Minimum {settlement["transaction_count"]} transactions to settle up</p>'
            f'<ul class="settle">{items}</ul>')


def _top(top, currency) -> str:
    if not top:
        return ""
    items = "".join(
        f'<li><span class="rank num">{i+1}</span>'
        f'<span class="tt-desc">{_e(t["description"])}</span>'
        f'<span class="tt-cat">{_e(t["category"])}</span>'
        f'<span class="tt-amt num">{_fmt(t["amount"], currency)}</span></li>'
        for i, t in enumerate(top)
    )
    return f'<ol class="toplist">{items}</ol>'


def _ledger(ledger, currency, max_rows=400) -> str:
    if not ledger:
        return "<p class='muted'>No transactions.</p>"
    shown = list(reversed(ledger))[:max_rows]
    note = ""
    if len(ledger) > max_rows:
        note = (f'<p class="muted">Showing the {max_rows} most recent of {len(ledger)} '
                f'transactions — narrow the date range to load older ones.</p>')
    head = ("<tr><th data-k='date'>Date</th><th data-k='description'>Description</th>"
            "<th data-k='category'>Category</th><th data-k='cost' class='num'>Cost</th>"
            "<th data-k='your_share' class='num'>Your share</th>"
            "<th data-k='you_paid' class='num'>You paid</th></tr>")
    rows = "".join(
        f'<tr><td class="num">{_e(r["date"])}</td><td>{_e(r["description"])}</td>'
        f'<td><span class="pill">{_e(r["category"])}</span></td>'
        f'<td class="num">{_fmt(r["cost"], currency)}</td>'
        f'<td class="num">{_fmt(r["your_share"], currency)}</td>'
        f'<td class="num">{_fmt(r["you_paid"], currency)}</td></tr>'
        for r in shown
    )
    return (f'{note}<input class="search" placeholder="Search {len(shown)} transactions…" oninput="ft(this)">'
            f'<div class="tablewrap scroll"><table class="ledger" id="ledger"><thead>{head}</thead>'
            f'<tbody>{rows}</tbody></table></div>')


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------

def render(result: Dict[str, Any]) -> str:
    meta = result.get("meta", {})
    currency = meta.get("primary_currency", "")
    is_group = meta.get("target_type") == "group"
    label = meta.get("target_label") or "Analysis"

    if result.get("empty"):
        body = ('<section class="card"><h2>No expenses</h2>'
                '<p class="muted">No transactions matched the selected filters.</p></section>')
        return _page(label, body)

    span = f'{_e(meta.get("date_from"))} — {_e(meta.get("date_to"))}' if meta.get("date_from") else ""
    warn = ""
    if meta.get("mixed_currency"):
        warn += f'<div class="warn">Mixed currencies ({", ".join(meta.get("currencies", []))}) — totals shown per currency, not summed.</div>'
    if meta.get("truncated"):
        warn += f'<div class="warn">Result truncated at {meta.get("pages_fetched")} pages — not all transactions included.</div>'
    rec = result.get("reconciliation", {})
    if rec and not rec.get("reconciled", True):
        warn += '<div class="warn err">Reconciliation mismatch — shares do not sum to cost. Numbers may be off.</div>'

    parts = [
        f'<div class="subhead"><span class="num">{_e(meta.get("expense_count"))} transactions</span>'
        f'{f"<span>{span}</span>" if span else ""}'
        f'{"<span class=ok>reconciled</span>" if rec.get("reconciled") else ""}</div>{warn}',
        _hero_number(result, currency, is_group),
        _category_hero(result, currency),
        f'<section class="card"><div class="sechead"><h2>Monthly trend</h2></div>{_monthly(result["monthly_trend"]["months"], currency)}</section>',
    ]

    if is_group and "member_comparison" in result:
        mc = result["member_comparison"]
        ins = mc.get("insights", {})
        ins_html = ""
        if ins:
            ins_html = (f'<div class="insights">'
                        f'<span>avg/person <b class="num">{_fmt(ins["average_per_member"], currency)}</b></span>'
                        f'<span>top <b>{_e(ins["highest_spender"]["name"])}</b></span></div>')
        parts.append(f'<section class="card"><div class="sechead"><h2>Members</h2></div>{ins_html}{_member_bars(mc["members"], currency)}</section>')
        parts.append(f'<section class="card"><div class="sechead"><h2>Category × member</h2></div>{_matrix(result["category_matrix"], mc.get("member_names", {}), currency)}</section>')
        parts.append(f'<section class="card"><div class="sechead"><h2>Settle up</h2></div>{_settlement(result["settlement"], currency)}</section>')

    parts.append(f'<section class="card"><div class="sechead"><h2>Top transactions</h2></div>{_top(result["top_transactions"], currency)}</section>')
    parts.append(f'<section class="card"><div class="sechead"><h2>All transactions</h2></div>{_ledger(result["ledger"], currency)}</section>')

    return _page(label, "".join(parts))


def _page(label: str, body: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="en" data-theme="dark">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>ExpensifyAI — {_e(label)}</title>
<link rel="icon" href="data:,">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Bricolage+Grotesque:opsz,wght@12..96,500;12..96,700;12..96,800&family=Hanken+Grotesk:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>{_CSS}</style>
</head>
<body class="viz-root">
<header class="topbar">
  <div class="brandwrap"><span class="dot"></span><div>
    <div class="brand">EXPENSIFY&nbsp;AI</div>
    <h1>{_e(label)}</h1></div></div>
  <button class="toggle" onclick="tt()" aria-label="Toggle theme">◐</button>
</header>
<main>{body}</main>
<footer><p class="muted">Automated report by ExpensifyAI · deterministic compute · if any doubt, reach out to Uday.</p></footer>
<script>{_JS}</script>
</body></html>"""


def write_dashboard(result: Dict[str, Any], out_dir: str = "analytics_reports") -> str:
    meta = result.get("meta", {})
    tgt, tid = meta.get("target_type", "analysis"), meta.get("target_id")
    frm, to = (meta.get("date_from") or "all"), (meta.get("date_to") or "all")
    slug = f"{tgt}{('-'+str(tid)) if tid else ''}-{frm}-to-{to}".replace(":", "").replace(" ", "_")
    path = Path(out_dir)
    path.mkdir(parents=True, exist_ok=True)
    fp = path / f"{slug}.html"
    fp.write_text(render(result), encoding="utf-8")
    return str(fp.resolve())


_CSS = """
:root{
  --bg:#08080a;--surf:#101013;--surf2:#17171b;--hair:rgba(255,255,255,.06);
  --tx:#f4f4f0;--tx2:#93938d;--tx3:#5a5a56;--accent:#c6f24e;--pos:#c6f24e;--neg:#ff8080;
  --radius:20px;--font:'Hanken Grotesk',system-ui,sans-serif;--display:'Bricolage Grotesque',var(--font);
}
[data-theme=light]{--bg:#f3f1ea;--surf:#fbfaf6;--surf2:#f0eee6;--hair:rgba(0,0,0,.08);--tx:#14130f;--tx2:#5f5c53;--tx3:#928e83;--accent:#5b8c1f;--pos:#3d7a1f;}
*{box-sizing:border-box;margin:0;padding:0}
html{scroll-behavior:smooth}
body{background:var(--bg);color:var(--tx);font-family:var(--font);font-size:15px;line-height:1.5;-webkit-font-smoothing:antialiased;
  background-image:radial-gradient(120% 90% at 50% -10%, rgba(198,242,78,.05), transparent 55%);background-attachment:fixed;}
.num{font-variant-numeric:tabular-nums;font-feature-settings:"tnum"}
.muted{color:var(--tx3);font-size:13px}
.topbar{display:flex;justify-content:space-between;align-items:center;padding:26px 34px;position:sticky;top:0;z-index:20;
  background:linear-gradient(var(--bg),rgba(8,8,10,.85));backdrop-filter:blur(14px);border-bottom:1px solid var(--hair)}
.brandwrap{display:flex;align-items:center;gap:14px}
.dot{width:10px;height:10px;border-radius:50%;background:var(--accent);box-shadow:0 0 14px var(--accent)}
.brand{font:700 11px/1 var(--font);letter-spacing:.32em;color:var(--tx2)}
h1{font:700 22px/1.1 var(--display);letter-spacing:-.02em;margin-top:3px}
.toggle{width:40px;height:40px;border-radius:12px;border:1px solid var(--hair);background:var(--surf);color:var(--tx2);cursor:pointer;font-size:16px}
.toggle:hover{color:var(--accent)}
main{max-width:1080px;margin:0 auto;padding:28px 34px 12px;display:flex;flex-direction:column;gap:18px}
.subhead{display:flex;gap:18px;align-items:center;color:var(--tx2);font-size:13px;flex-wrap:wrap;padding:0 4px}
.subhead .ok{color:var(--accent)}
.subhead .ok::before{content:"● ";font-size:9px;vertical-align:middle}
.warn{background:rgba(255,128,128,.09);border:1px solid rgba(255,128,128,.28);color:#ffb0b0;padding:11px 16px;border-radius:12px;font-size:13.5px}
.warn.err{background:rgba(255,80,80,.12)}

.card{background:var(--surf);border:1px solid var(--hair);border-radius:var(--radius);padding:26px 28px;
  animation:rise .6s cubic-bezier(.2,.7,.3,1) both}
main>.card:nth-child(2){animation-delay:.04s}main>.card:nth-child(3){animation-delay:.1s}
main>.card:nth-child(4){animation-delay:.16s}main>.card:nth-child(5){animation-delay:.22s}
main>.card:nth-child(6){animation-delay:.28s}main>.card:nth-child(n+7){animation-delay:.34s}
@keyframes rise{from{opacity:0;transform:translateY(16px)}to{opacity:1;transform:none}}
@media(prefers-reduced-motion:reduce){.card{animation:none}}
.sechead{display:flex;justify-content:space-between;align-items:baseline;margin-bottom:20px}
h2{font:600 12px/1 var(--font);letter-spacing:.14em;text-transform:uppercase;color:var(--tx2)}

/* hero */
.hero{display:flex;justify-content:space-between;align-items:flex-end;gap:30px;flex-wrap:wrap;
  background:linear-gradient(150deg,var(--surf),var(--surf2))}
.herolabel{font:600 12px/1 var(--font);letter-spacing:.14em;text-transform:uppercase;color:var(--tx2);margin-bottom:12px}
.heronum{font:800 56px/1 var(--display);letter-spacing:-.03em}
.herostats{display:flex;gap:34px}
.hs .hsv{font:700 22px/1 var(--display);letter-spacing:-.02em}
.hs .hsk{font-size:12px;color:var(--tx3);margin-top:6px;letter-spacing:.02em}
.hs.pos .hsv{color:var(--pos)}.hs.neg .hsv{color:var(--neg)}
@media(max-width:720px){.heronum{font-size:42px}.herostats{gap:22px}}

/* category hero */
.catwrap{display:grid;grid-template-columns:1fr 200px;gap:30px;align-items:start}
@media(max-width:760px){.catwrap{grid-template-columns:1fr}.catdonut{order:-1;justify-self:center}}
.catlist{display:flex;flex-direction:column}
.cat{border-bottom:1px solid var(--hair)}
.cat:last-child{border-bottom:none}
.cat summary{list-style:none;cursor:pointer;padding:15px 2px;display:flex;flex-direction:column;gap:9px}
.cat summary::-webkit-details-marker{display:none}
.crow1{display:flex;align-items:center;gap:14px}
.cicon{width:34px;height:34px;flex:none;display:grid;place-items:center}
.cicon svg{width:20px;height:20px}
.cmeta{display:flex;flex-direction:column;gap:2px;flex:1;min-width:0}
.cname{font:600 15.5px/1.2 var(--font)}
.ccount{font-size:12px;color:var(--tx3)}
.cright{text-align:right;display:flex;flex-direction:column;gap:2px;flex:none}
.camt{font:700 16px/1 var(--display)}
.cpct{font-size:12px;color:var(--tx3)}
.cbar{height:3px;border-radius:3px;background:var(--surf2);overflow:hidden;margin-left:48px}
.cbarfill{display:block;height:100%;border-radius:3px}
.cchev{color:var(--tx3);font-size:20px;transition:transform .25s;flex:none}
.cat[open] .cchev{transform:rotate(90deg);color:var(--accent)}
.cbody{padding:4px 2px 16px 48px;display:flex;flex-direction:column;animation:fade .3s ease both}
@keyframes fade{from{opacity:0}to{opacity:1}}
.ctx{display:grid;grid-template-columns:88px 1fr auto;gap:14px;padding:8px 0;border-top:1px solid var(--hair);font-size:13.5px}
.ctxd{color:var(--tx3);font-variant-numeric:tabular-nums}
.ctxn{color:var(--tx2);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.ctxa{font-weight:600}
.ctxmore{padding-top:10px;color:var(--tx3);font-size:12.5px}
.catdonut{position:relative;width:180px;height:180px}
.donut{width:180px;height:180px}
.donutcap{position:absolute;inset:0;display:flex;flex-direction:column;align-items:center;justify-content:center;pointer-events:none}
.donutcap .num{font:700 17px/1 var(--display);letter-spacing:-.02em}
.donutcap span{font-size:11px;color:var(--tx3);text-transform:uppercase;letter-spacing:.1em;margin-top:3px}

/* charts */
.bars{width:100%;height:auto}
.mbar{fill:var(--accent);opacity:.85}.mbar:hover{opacity:1}
.grid{stroke:var(--hair)}
.gridlbl{fill:var(--tx3);font-size:10px}
.barval{fill:var(--tx2);font-size:9.5px;text-anchor:middle;font-variant-numeric:tabular-nums}
.barlbl{fill:var(--tx3);font-size:10px;text-anchor:start}

/* members */
.insights{display:flex;gap:24px;margin-bottom:18px;font-size:13.5px;color:var(--tx2)}
.insights b{color:var(--tx);font-weight:600}
.mbars{display:flex;flex-direction:column;gap:12px}
.mbrow{display:grid;grid-template-columns:150px 1fr 150px;gap:14px;align-items:center}
.mbname{font-size:14px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.youtag{background:var(--accent);color:#0a0a0a;font-size:10px;font-weight:700;padding:1px 6px;border-radius:5px;margin-left:6px}
.mbtrack{height:8px;background:var(--surf2);border-radius:5px;overflow:hidden}
.mbfill{height:100%;border-radius:5px}
.mbval{text-align:right;font-size:14px;font-weight:600}

/* tables */
.tablewrap{overflow-x:auto}
.tablewrap.scroll{max-height:560px;overflow-y:auto;border:1px solid var(--hair);border-radius:12px}
table{width:100%;border-collapse:collapse;font-size:13.5px}
thead th{position:sticky;top:0;background:var(--surf);text-align:left;color:var(--tx3);font-weight:600;
  padding:11px 14px;border-bottom:1px solid var(--hair);cursor:pointer;white-space:nowrap;font-size:11px;text-transform:uppercase;letter-spacing:.06em}
td{padding:10px 14px;border-bottom:1px solid var(--hair)}
tbody tr:last-child td{border-bottom:none}
.num{text-align:right}th.num,td.num{text-align:right}
.strong{font-weight:700}
tbody tr:hover{background:var(--surf2)}
.pill{background:var(--surf2);border:1px solid var(--hair);border-radius:100px;padding:2px 10px;font-size:12px;color:var(--tx2)}
.search{width:100%;padding:12px 16px;margin-bottom:14px;background:var(--surf2);border:1px solid var(--hair);border-radius:12px;color:var(--tx);font-size:14px;font-family:var(--font)}
.search:focus{outline:none;border-color:var(--accent)}

.settle{list-style:none;display:flex;flex-direction:column;gap:9px;margin-top:6px}
.settle li{display:flex;align-items:center;gap:12px;background:var(--surf2);padding:12px 16px;border-radius:12px}
.settle .from{color:var(--neg)}.settle .to{color:var(--pos)}.settle .arrow{color:var(--tx3)}
.settle .amt{margin-left:auto;font-weight:700}
.toplist{list-style:none;display:flex;flex-direction:column;gap:2px}
.toplist li{display:flex;align-items:center;gap:14px;padding:11px 2px;border-bottom:1px solid var(--hair);font-size:14px}
.toplist li:last-child{border-bottom:none}
.rank{width:24px;color:var(--tx3);font-weight:700;font-size:13px}
.tt-desc{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.tt-cat{color:var(--tx3);font-size:12.5px}.tt-amt{font-weight:700}
footer{max-width:1080px;margin:0 auto;padding:26px 34px 40px;text-align:center}
"""

_JS = """
function tt(){var h=document.documentElement;h.dataset.theme=h.dataset.theme==='dark'?'light':'dark';}
function ft(i){var q=i.value.toLowerCase();document.querySelectorAll('#ledger tbody tr').forEach(function(r){
  r.style.display=r.textContent.toLowerCase().indexOf(q)>-1?'':'none';});}
document.querySelectorAll('th[data-k]').forEach(function(th){th.addEventListener('click',function(){
  var tb=th.closest('table').querySelector('tbody'),idx=[].indexOf.call(th.parentNode.children,th),asc=th.dataset.asc!=='1';
  th.dataset.asc=asc?'1':'0';var rows=[].slice.call(tb.querySelectorAll('tr'));
  rows.sort(function(a,b){var x=a.children[idx].textContent.trim(),y=b.children[idx].textContent.trim();
    var nx=parseFloat(x.replace(/[^0-9.-]/g,'')),ny=parseFloat(y.replace(/[^0-9.-]/g,''));
    if(!isNaN(nx)&&!isNaN(ny))return asc?nx-ny:ny-nx;return asc?x.localeCompare(y):y.localeCompare(x);});
  rows.forEach(function(r){tb.appendChild(r);});});});
"""
