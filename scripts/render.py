"""Render the profile README panels from live GitHub + Medium data.

Standard library only, so it runs on a bare GitHub Actions runner.
  GITHUB_TOKEN   public data (always available in Actions)
  PROFILE_TOKEN  optional; read access to private repos + private contribution counts
Anything that can't be fetched falls back to data/cache.json, so a failed
request never blanks the README.
"""
import base64, datetime as dt, json, math, os, re, urllib.request
import xml.etree.ElementTree as ET
from html import escape

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
P = lambda *a: os.path.join(ROOT, *a)
LOGIN = "patilshraddhaa"
MEDIUM = "https://medium.com/feed/@patilshraddhaa21"
TOKEN = os.environ.get("PROFILE_TOKEN") or os.environ.get("GITHUB_TOKEN", "")
TODAY = dt.date.today()

# ---- palette -------------------------------------------------------------
BG, PANEL, LINE = "#0a0a0b", "#0f0f11", "#222226"
INK, DIM, FAINT = "#ededef", "#85858c", "#3a3a40"
ACCENT = "#ff6b2c"
FAINT_BELOW = .1
FAINT_LINE = "#5c5c63"

FEATURED = "periphery"
PROJECTS = {
    "periphery": dict(desc="Test product ideas with AI before they ship. Simulated people walk the real app and show where journeys break.", visual="commits"),
    "Glasshouse": dict(desc="A pixel-art social village: a panoramic watchtower, personal worlds, attention journals and postcards.", visual="village"),
    "HumanFirst": dict(desc="Publishing for writers who use AI without hiding it. Every section shows who wrote it.", visual="provenance"),
}

TOOLS = {
    "design": ["Figma", "Framer", "Adobe Creative Cloud", "AutoCAD", "SketchUp"],
    "build": ["TypeScript", "React", "Next.js", "Tailwind", "Python"],
    "ai": ["Claude Code", "Codex", "LLM prototyping"],
    "ship": ["GitHub Actions", "Vercel", "Docker", "Playwright"],
}

FONT_CSS = "".join(
    f"@font-face{{font-family:{n};src:url(data:font/woff2;base64,"
    f"{base64.b64encode(open(P('fonts', f), 'rb').read()).decode()}) format('woff2');font-weight:100 900}}"
    for n, f in (("I", "inter.woff2"), ("J", "mono.woff2")))
STYLE = f"""<style>{FONT_CSS}
.s{{font-family:I,sans-serif;letter-spacing:-.01em}} .m{{font-family:J,monospace}}
.ink{{fill:{INK}}} .dim{{fill:{DIM}}} .acc{{fill:{ACCENT}}} .up{{text-transform:uppercase;letter-spacing:.08em}}
</style>"""

BAYER = [[0, 32, 8, 40, 2, 34, 10, 42], [48, 16, 56, 24, 50, 18, 58, 26], [12, 44, 4, 36, 14, 46, 6, 38],
         [60, 28, 52, 20, 62, 30, 54, 22], [3, 35, 11, 43, 1, 33, 9, 41], [51, 19, 59, 27, 49, 17, 57, 25],
         [15, 47, 7, 39, 13, 45, 5, 37], [63, 31, 55, 23, 61, 29, 53, 21]]


# ---- data ----------------------------------------------------------------
def gh(query, **variables):
    req = urllib.request.Request("https://api.github.com/graphql",
                                 data=json.dumps({"query": query, "variables": variables}).encode(),
                                 headers={"Authorization": f"bearer {TOKEN}", "User-Agent": "profile-readme"})
    out = json.load(urllib.request.urlopen(req, timeout=30))
    if out.get("errors"):
        raise RuntimeError(out["errors"][0]["message"])
    return out["data"]


def fetch_calendar():
    created = gh("query($l:String!){user(login:$l){createdAt}}", l=LOGIN)["user"]["createdAt"][:10]
    days, start = {}, dt.date.fromisoformat(created)
    while start <= TODAY:
        end = min(start + dt.timedelta(days=364), TODAY)
        q = """query($l:String!,$f:DateTime!,$t:DateTime!){user(login:$l){contributionsCollection(from:$f,to:$t){
               contributionCalendar{weeks{contributionDays{date contributionCount}}}}}}"""
        cal = gh(q, l=LOGIN, f=f"{start}T00:00:00Z", t=f"{end}T23:59:59Z")["user"]["contributionsCollection"]["contributionCalendar"]
        for w in cal["weeks"]:
            for d in w["contributionDays"]:
                days[d["date"]] = d["contributionCount"]
        start = end + dt.timedelta(days=1)
    return {"created": created, "days": days}


def fetch_repo(name):
    q = """query($o:String!,$n:String!,$c:String){repository(owner:$o,name:$n){
             description isPrivate pushedAt stargazerCount primaryLanguage{name}
             languages(first:6,orderBy:{field:SIZE,direction:DESC}){totalSize edges{size node{name}}}
             defaultBranchRef{target{...on Commit{history(first:100,after:$c){totalCount
               pageInfo{hasNextPage endCursor} nodes{committedDate}}}}}}}"""
    dates, cursor = [], None
    while True:
        r = gh(q, o=LOGIN, n=name, c=cursor)["repository"]
        h = r["defaultBranchRef"]["target"]["history"]
        dates += [n["committedDate"][:10] for n in h["nodes"]]
        if not h["pageInfo"]["hasNextPage"]:
            break
        cursor = h["pageInfo"]["endCursor"]
    total = r["languages"]["totalSize"] or 1
    return dict(private=r["isPrivate"], pushed=r["pushedAt"][:10], stars=r["stargazerCount"],
                language=(r["primaryLanguage"] or {}).get("name", "—"), commits=dates,
                languages=[(e["node"]["name"], e["size"] / total) for e in r["languages"]["edges"]])


def fetch_writing():
    req = urllib.request.Request(MEDIUM, headers={"User-Agent": "Mozilla/5.0 profile-readme"})
    root = ET.fromstring(urllib.request.urlopen(req, timeout=30).read())
    posts = []
    for item in root.iter("item"):
        when = dt.datetime.strptime(item.findtext("pubDate"), "%a, %d %b %Y %H:%M:%S %Z").date()
        posts.append(dict(title=item.findtext("title"), url=item.findtext("link").split("?")[0], date=str(when)))
    return posts[:5]


def load():
    cache = json.load(open(P("data", "cache.json"))) if os.path.exists(P("data", "cache.json")) else {}
    jobs = {"calendar": fetch_calendar, "writing": fetch_writing,
            **{f"repo:{n}": (lambda n=n: fetch_repo(n)) for n in PROJECTS}}
    for key, job in jobs.items():
        try:
            new = job()
            if key == "calendar" and "calendar" in cache:
                # GITHUB_TOKEN can't see private contributions; never let a day's count go down
                old = cache["calendar"]["days"]
                new["days"] = {d: max(n, old.get(d, 0)) for d, n in new["days"].items()}
            cache[key] = new
        except Exception as e:  # keep the last good copy
            print(f"skip {key}: {e}")
    json.dump(cache, open(P("data", "cache.json"), "w"), indent=1, sort_keys=True)
    return cache


# ---- drawing helpers -----------------------------------------------------
def svg(w, h, body):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}">{STYLE}'
            f'<rect x=".5" y=".5" width="{w-1}" height="{h-1}" rx="10" fill="{BG}" stroke="{LINE}"/>{body}</svg>')


def text(x, y, s, cls="s ink", size=14, anchor="start", weight=400):
    return f'<text x="{x}" y="{y}" class="{cls}" font-size="{size}" font-weight="{weight}" text-anchor="{anchor}">{escape(str(s))}</text>'


def hline(x1, x2, y):
    return f'<path d="M{x1} {y}H{x2}" stroke="{LINE}"/>'


def vline(x, y1, y2):
    return f'<path d="M{x} {y1}V{y2}" stroke="{LINE}"/>'


def wrap(s, n):
    lines, cur = [], ""
    for word in s.split():
        if len(cur) + len(word) + 1 > n:
            lines.append(cur); cur = word
        else:
            cur = f"{cur} {word}".strip()
    return lines + [cur]


def dither(field, x0, y0, cell, dot, hot=0.9):
    """field[row][col] in 0..1 -> ordered-dither dots; the brightest cells take the accent."""
    faint, base, acc = [], [], []
    for r, row in enumerate(field):
        for c, v in enumerate(row):
            t = (BAYER[r % 8][c % 8] + .5) / 64
            sq = f"M{x0 + c*cell:.1f} {y0 + r*cell:.1f}h{dot}v{dot}h-{dot}z"
            if v < FAINT_BELOW:
                if v * 6 > t: faint.append(sq)
            elif v > t:
                (acc if v >= hot else base).append(sq)
    return (f'<path fill="{FAINT}" d="{"".join(faint)}"/>'
            f'<path fill="{INK}" fill-opacity=".92" d="{"".join(base)}"/>'
            f'<path fill="{ACCENT}" d="{"".join(acc)}"/>')


def smooth(values, radius):
    k = [math.exp(-(i / max(radius, .1)) ** 2 / 2) for i in range(-3 * int(radius) - 1, 3 * int(radius) + 2)]
    m = len(k) // 2
    return [sum(values[j] * k[i] for i, j in enumerate(range(c - m, c + m + 1)) if 0 <= j < len(values)) for c in range(len(values))]


def daily(dates, start):
    n = (TODAY - start).days + 1
    v = [0.0] * n
    for d in dates:
        i = (dt.date.fromisoformat(d) - start).days
        if 0 <= i < n:
            v[i] += 1
    return v


def fmt(d):
    return dt.date.fromisoformat(d).strftime("%b %-d")


# ---- panels --------------------------------------------------------------
def hero(data):
    """One quiet panel: a thin strip of every contribution, then name and line."""
    W, H = 1000, 250
    cal = data["calendar"]; start = dt.date.fromisoformat(cal["created"])
    counts = [cal["days"].get(str(start + dt.timedelta(days=i)), 0) for i in range((TODAY - start).days + 1)]
    cell, rows = 6, 9
    cols = int((W - 56) / cell); x0 = (W - cols * cell) / 2; top = 150
    per = [sum(counts[int(c * len(counts) / cols):int((c + 1) * len(counts) / cols)]) for c in range(cols)]
    sm = smooth(per, 1.2); peak = max(sm) or 1
    field = []
    for r in range(rows):
        dy = abs(r - (rows - 1) / 2) / (rows / 2)
        field.append([max(min(1, (sm[c] / peak) ** .5 * 1.2 * math.exp(-(dy / .7) ** 2)), .5 if dy < .1 else .05) for c in range(cols)])
    total = sum(counts[-365:])
    body = [text(28, 70, "Shraddha Patil", "s ink", 40, weight=500),
            f'<rect x="{W-40}" y="50" width="12" height="12" fill="{ACCENT}"/>',
            text(30, 104, "Product designer who ships code.", "s dim", 17),
            dither(field, x0, top, cell, 3, hot=.92),
            text(x0, H - 26, f"{total:,} contributions in the last year", "m dim", 11),
            text(W - x0, H - 26, f"since {start.strftime('%b %Y').lower()}", "m dim", 11, "end")]
    return svg(W, H, "".join(body))


def visual_commits(r, x, y, w, h):
    start = min(dt.date.fromisoformat(d) for d in r["commits"]) - dt.timedelta(days=3)
    per = daily(r["commits"], start)
    cell = 5; cols, rows = int(w / cell), int((h - 34) / cell)
    sm = smooth([per[int(c * len(per) / cols)] for c in range(cols)], 1.3); peak = max(sm) or 1
    field = []
    for row in range(rows):
        level = 1 - row / rows
        line = []
        for c in range(cols):
            hgt = math.sqrt(sm[c] / peak) * .92
            if level <= hgt:
                depth = (hgt - level) * rows
                crest = depth <= 1.2 and sm[c] / peak > .45
                line.append(1.0 if crest else .22 + .7 * math.exp(-depth / 3))
            else:
                line.append(.05)
        field.append(line)
    cap = [text(x + 14, y + 22, f"commits per day, {start.strftime('%b %-d')} to today", "m dim", 11),
           text(x + w - 14, y + 22, f"{len(r['commits'])} total · peak {int(max(per))}", "m dim", 11, "end")]
    return "".join(cap) + dither(field, x + (w - cols * cell) / 2, y + 34, cell, 3, hot=1.0)


def visual_provenance(r, x, y, w, h):
    """HumanFirst's own model: a locked draft is a stack of HUMAN and AI sections,
    each AI section anchored to the human section it extends."""
    import random
    rnd = random.Random(7)
    sections = [("human", "body", 3, None), ("ai", "example", 2, 1), ("human", "body", 3, None),
                ("human", "body", 2, None), ("ai", "takeaways", 2, 4)]
    cell, dot, lh = 3, 2.1, 10
    px0, pw = x + 40, int(w * .52)
    out = [f'<path d="M{x + 16} {y + 13}h7v6h-7z" fill="none" stroke="{DIM}"/>'
           f'<path d="M{x + 17.5} {y + 13}v-2.5a2 2 0 0 1 4 0v2.5" fill="none" stroke="{DIM}"/>',
           text(x + 30, y + 21, "draft · locked", "m dim", 11),
           text(x + w - 14, y + 21, "section-level provenance", "m dim", 11, "end")]
    cy, anchors = y + 40, {}
    for i, (who, kind, n, anchor) in enumerate(sections, 1):
        ai = who == "ai"; indent = 18 if ai else 0; top = cy
        for ln in range(n):
            cols = int((pw - indent) / cell * (rnd.uniform(.55, .8) if ln == n - 1 else rnd.uniform(.9, 1)))
            row, c = [], 0
            while c < cols:  # words of 2-7 cells with a one-cell space
                word = rnd.randint(2, 7); row += [1] * min(word, cols - c) + [0]; c += word + 1
            field = [[(.55 if ai else .9) * v for v in row[:cols]] for _ in range(2)]
            out.append(dither(field, px0 + indent, cy, cell, dot, hot=.5 if ai else 2))
            cy += lh
        mid = (top + cy - 4) / 2
        if ai:  # bracket back to its human anchor
            ay = anchors[anchor]
            out.append(f'<path d="M{px0 + 8} {ay}V{cy - 6}M{px0 + 8} {mid}h6" fill="none" stroke="{ACCENT}" stroke-dasharray="2 2"/>')
        else:
            anchors[i] = mid
            out.append(f'<rect x="{px0 - 14}" y="{mid - 2}" width="4" height="4" fill="{INK}"/>')
        label = f"§{i} ai · {kind}" + (f" ↳ §{anchor}" if ai else "") if ai else f"§{i} human"
        out.append(text(px0 + pw + 24, mid + 4, label, "m acc" if ai else "m dim", 11))
        cy += 7
    return "".join(out)


def visual_village(r, x, y, w, h):
    """Glasshouse as a drawing: the rotunda's wall of arched rooms.
    Rooms are lived in, open (+), locked (bars) or the featured world (accent)."""
    cell, dot = 3, 2.1
    cols, rows = int(w / cell), int((h - 30) / cell)
    L = {k: {} for k in ("ink", "dim", "inkf", "acc", "accf")}  # solid layers + dithered fills

    def put(layer, c, rr, v=1.0):
        if 0 <= c < cols and 0 <= rr < rows:
            L[layer][(c, rr)] = max(v, L[layer].get((c, rr), 0))

    def shape(c0, r0, sw, sh, dome=True):
        """Cells inside a rect with a semicircular top; returns (inside, edge)."""
        rad = sw / 2
        inside = {(c, rr) for rr in range(sh) for c in range(sw)
                  if not dome or rr >= rad or (c + .5 - rad) ** 2 + (rr + .5 - rad) ** 2 <= rad ** 2}
        edge = {p for p in inside if any((p[0] + dx, p[1] + dy) not in inside for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)))}
        return {(c0 + c, r0 + rr) for c, rr in inside}, {(c0 + c, r0 + rr) for c, rr in edge}

    def room(c0, r0, state, aw=11, ah=14):
        inside, edge = shape(c0, r0, aw, ah)
        line = {"home": "ink", "featured": "acc"}.get(state, "dim")
        for c, rr in edge:
            put(line, c, rr)
        mc = c0 + aw // 2
        if state == "home":
            for dc, dr in ((0, 0), (0, 1), (-1, 2), (0, 2), (1, 2), (0, 3), (-1, 4), (1, 4)):  # someone at home
                put("ink", mc + dc, r0 + 6 + dr)
        elif state == "featured":
            for c, rr in inside - edge:
                put("accf", c, rr, .5)
        elif state == "open":
            for d in (-1, 0, 1):
                put("ink", mc + d, r0 + 8); put("ink", mc, r0 + 8 + d)
        elif state == "locked":
            for c, rr in inside - edge:
                if (c - c0) % 3 == 2 and rr - r0 > 3:
                    put("dim", c, rr)

    # a wall of rooms: three tiers, ten rooms each
    per, pitch = 10, 15
    left = (cols - (per * pitch - 4)) // 2
    plan = ["home open locked open home open open featured locked open",
            "open home open open locked home open home open locked",
            "locked open home open open open locked open home open"]
    for t, states in enumerate(plan):
        r0 = 1 + t * 18
        for k, state in enumerate(states.split()):
            room(left + k * pitch, r0, state)
        for c in range(0, cols, 2):
            put("dim", c, r0 + 15)

    ox, oy = x + (w - cols * cell) / 2, y + 30
    out = [text(x + 14, y + 20, "the village wall", "m dim", 11),
           text(x + w - 14, y + 20, "home · open · locked · featured", "m dim", 11, "end")]
    sq = lambda c, rr: f"M{ox + c*cell:.1f} {oy + rr*cell:.1f}h{dot}v{dot}h-{dot}z"
    for layer, color, dithered in (("inkf", INK, True), ("accf", ACCENT, True), ("dim", FAINT_LINE, False),
                                   ("ink", INK, False), ("acc", ACCENT, False)):
        d = "".join(sq(c, rr) for (c, rr), v in L[layer].items()
                    if not dithered or v > (BAYER[rr % 8][c % 8] + .5) / 64)
        out.append(f'<path fill="{color}" d="{d}"/>')
    return "".join(out)


def card(name, r, wide):
    meta = PROJECTS[name]; W = 1000 if wide else 494; vis = 230 if wide else 200
    H = 40 + vis + (78 if wide else 96) + 58
    body = [text(16, 26, name.lower(), "m ink", 13, weight=500),
            text(W - 16, 26, ("private · " if r["private"] else "") + f"updated {fmt(r['pushed'])}", "m dim", 11, "end"),
            hline(0, W, 40)]
    draw = {"commits": visual_commits, "provenance": visual_provenance, "village": visual_village}[meta["visual"]]
    body += [draw(r, 0, 40, W, vis), hline(0, W, 40 + vis)]
    ty = 40 + vis + 34
    body.append(text(16, ty, "Periphery" if name == "periphery" else name, "s ink", 24, weight=500))
    for i, line in enumerate(wrap(meta["desc"], 110 if wide else 58)):
        body.append(text(16, ty + 26 + i * 20, line, "s dim", 14))
    fy = H - 58; body.append(hline(0, W, fy))
    stats = (("language", r["language"].lower()), ("commits", len(r["commits"])), ("stars", r["stars"]), ("updated", fmt(r["pushed"])))
    cw = W / len(stats)
    for i, (k, v) in enumerate(stats):
        if i:
            body.append(vline(i * cw, fy, H - 1))
        body += [text(i * cw + 16, fy + 22, k, "m dim up", 10), text(i * cw + 16, fy + 42, v, "m ink", 13)]
    return svg(W, H, "".join(body))


def tools_panel():
    W = 1000; cw = W / len(TOOLS); n = max(len(v) for v in TOOLS.values()); H = 64 + n * 28 + 22
    body = [hline(0, W, 44)]
    for i, (group, items) in enumerate(TOOLS.items()):
        x = i * cw
        if i:
            body.append(vline(x, 0, H - 1))
        # a small dithered ramp per column, denser to the right, so the panel reads as one system
        ramp = [[(c / 14) ** 1.4 * (1 - i * .12) + .02 for c in range(14)] for _ in range(3)]
        body += [text(x + 18, 28, group, "m dim up", 11), dither(ramp, x + cw - 18 - 14 * 4, 20, 4, 2.4, hot=.95)]
        for j, item in enumerate(items):
            iy = 74 + j * 28
            body += [f'<rect x="{x + 18}" y="{iy - 8}" width="5" height="5" fill="{ACCENT if j == 0 else FAINT}"/>',
                     text(x + 32, iy, item, "m ink", 13)]
    return svg(W, H, "".join(body))


def button(label, w):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="36" viewBox="0 0 {w} 36">{MONO_STYLE}'
            f'<rect x=".5" y=".5" width="{w-1}" height="35" rx="6" fill="{BG}" stroke="{LINE}"/>'
            f'{text(14, 23, label, "m ink", 12)}{text(w - 14, 23, "↗", "m acc", 12, "end")}</svg>')


MONO_STYLE = "<style>@font-face" + STYLE.split("@font-face", 2)[2]


# ---- readme --------------------------------------------------------------
def writing_table(posts):
    rows = ["| | title | published |", "|:--|:--|--:|"]
    rows += [f"| `{i+1:02d}` | [{p['title']}]({p['url']}) | `{fmt(p['date'])}, {p['date'][:4]}` |" for i, p in enumerate(posts)]
    return "\n".join(rows)


def fill(readme, marker, content):
    return re.sub(rf"(<!-- {marker}:start -->\n).*?(<!-- {marker}:end -->)",
                  lambda m: m.group(1) + content + "\n" + m.group(2), readme, flags=re.S)


def main():
    data = load()
    open(P("assets", "hero.svg"), "w").write(hero(data))
    open(P("assets", "tools.svg"), "w").write(tools_panel())
    for n in PROJECTS:
        if f"repo:{n}" in data:
            open(P("assets", f"{n.lower()}.svg"), "w").write(card(n, data[f"repo:{n}"], n == FEATURED))
    for slug, label in (("portfolio", "shraddhapatil.me"), ("linkedin", "linkedin"), ("x", "x"), ("medium", "medium"), ("email", "email")):
        open(P("assets", f"btn-{slug}.svg"), "w").write(button(label, 190 if len(label) > 10 else 120))
    readme = open(P("README.md")).read()
    readme = fill(readme, "writing", writing_table(data.get("writing", [])))
    open(P("README.md"), "w").write(readme)


if __name__ == "__main__":
    main()
