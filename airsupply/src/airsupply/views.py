"""The page, rendered on the server.

htmx: the browser asks for HTML and swaps it in. `#app` is the whole
application, polled every couple of seconds with a morph swap, so typed text,
focus and scroll survive each refresh. Every button posts to an action and
gets the same fragment back, so the page reflects the result at once instead
of waiting for the next poll.

No JavaScript of our own. The activity log is newest-first for that reason:
nothing has to scroll it.
"""

import json
from html import escape as h

POLL = "every 2s"


def page(version):
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>airsupply</title>
<style>{CSS}</style>
<script src="static/htmx.min.js"></script>
</head>
<body>
<main id="app"
      hx-get="ui" hx-trigger="load, {POLL}"
      hx-target:inherited="#app" hx-swap:inherited="innerMorph">
  <p class="muted">Loading&hellip;</p>
</main>
</body>
</html>"""


def app(s):
    """The fragment inside #app, from the controller's state dict."""
    return "".join([
        _header(s),
        _error(s),
        _find(s),
        _bond(s),
        _pair(s),
        _results(s),
        _activity(s),
    ])


# --- pieces ----------------------------------------------------------------

def _header(s):
    a = s["adapter"]
    if a:
        adapter = f"Adapter {h(a['address'] or '?')}" + ("" if a["powered"] else " <b>(not powered)</b>") + "."
    else:
        adapter = "<b>No Bluetooth adapter is visible over D-Bus.</b>"
    return f"""
<h1>airsupply <span class="muted">v{h(s['version'])}</span></h1>
<p class="sub">A diagnostic for the ResMed AirMini. It reads; it never writes. {adapter}</p>"""


def _error(s):
    if not s["error"]:
        return ""
    return f'<div class="banner">{h(s["error"])}</div>'


def _step(n, title, done, body, inactive=False):
    return f"""
<section class="{'inactive' if inactive else ''}">
  <h2><span class="n{' done' if done else ''}">{n}</span> {title}</h2>
  {body}
</section>"""


def _find(s):
    scanning = s["scanning"]
    button = (
        f'<button class="primary{" spin" if scanning else ""}"'
        f' hx-post="ui/scan"{" disabled" if scanning or not s["adapter"] else ""}>Scan</button>'
        + (' <span class="muted">Scanning&hellip;</span>' if scanning else "")
    )
    body = f"""
  <p>Put the AirMini into <b>pairing mode</b> (it does not stay discoverable), then scan.
     The machine should appear with a signal strength; worse than about &minus;80&nbsp;dBm will not hold a session.</p>
  <div class="row">{button}</div>
  {_devices(s)}"""
    return _step(1, "Find the machine", bool(s["selected"]), body)


def _devices(s):
    devs = s["devices"]
    if not devs:
        return '<p class="muted">BlueZ knows no devices yet. Scan with the machine in pairing mode.</p>'
    cands = [d for d in devs if d["candidate"]]
    others = [d for d in devs if not d["candidate"]]

    def rows(items):
        out = []
        for d in items:
            sel = d["address"] == s["selected"]
            action = (
                '<span class="tag ok">selected</span>' if sel else
                f'<button hx-post="ui/select" hx-vals=\'{json.dumps({"address": d["address"]})}\'>Use</button>'
            )
            rssi = f'{d["rssi"]} dBm' if d["rssi"] is not None else '<span class="muted">not seen recently</span>'
            out.append(f"""
      <tr class="{'cand' if d['candidate'] else ''}{' sel' if sel else ''}">
        <td>{h(d['name'] or '(no name)')}<br><span class="muted mono">{h(d['address'])}</span></td>
        <td>{rssi}</td>
        <td><span class="tag {'ok' if d['classic'] else 'bad'}">{'Classic' if d['classic'] else 'BLE'}</span>
            <span class="tag {'ok' if d['spp'] else ''}">{'Serial' if d['spp'] else 'no SDP yet'}</span>
            {'<span class="tag ok">bonded</span>' if d['paired'] else ''}</td>
        <td>{action}</td>
      </tr>""")
        return "".join(out)

    head = "<tr><th>Device</th><th>Signal</th><th></th><th></th></tr>"
    html = ""
    if cands:
        html += f"<table>{head}{rows(cands)}</table>"
    else:
        html += ('<p class="muted">Nothing AirMini-shaped yet. If the machine is in pairing mode '
                 'and does not appear, the host is out of range.</p>')
    if others:
        html += (f"<details><summary>Other devices BlueZ can see ({len(others)})</summary>"
                 f"<table>{head}{rows(others)}</table></details>")
    return html


def _bond(s):
    sel = s["selected_device"]
    busy = bool(s["busy"])
    if not sel:
        state = '<span class="muted">Select the machine first.</span>'
    elif s["bonded"]:
        state = '<span class="tag ok">bonded</span>'
    elif not s["agent_ok"]:
        state = '<span class="tag bad">no pairing agent; see the activity log</span>'
    else:
        state = ""
    disabled = not sel or s["bonded"] or busy or not s["agent_ok"]
    body = f"""
  <p>The link-layer pairing between the Home Assistant host and the machine. Done once.
     BlueZ may ask for a code; it will appear here or on the machine.</p>
  <div class="row">
    <button class="primary{' spin' if s['busy'] == 'bond' else ''}" hx-post="ui/bond"{' disabled' if disabled else ''}>Bond over Bluetooth</button>
    {state}
  </div>
  {_prompt(s)}"""
    return _step(2, "Bluetooth bond", s["bonded"], body, inactive=not sel)


def _prompt(s):
    p = s["prompt"]
    if not p:
        return ""
    sel = s["selected_device"]
    dev = h((sel and sel["name"]) or s["selected"] or "the machine")
    kind, value = p["kind"], h(p.get("value") or "")
    cancel = '<button hx-post="ui/answer" hx-vals=\'{"accept":"no"}\'>Cancel</button>'
    if kind == "pincode":
        inner = f"""<p>BlueZ needs the Bluetooth PIN for {dev}. If the machine shows one, type it; otherwise try <code>0000</code>.</p>
      <form class="row" hx-post="ui/answer"><input id="pval" name="value" inputmode="numeric" autocomplete="off" autofocus>
        <button class="primary">Send</button></form>{cancel}"""
    elif kind == "passkey":
        inner = f"""<p>BlueZ needs the six-digit passkey {dev} is showing.</p>
      <form class="row" hx-post="ui/answer"><input id="pval" name="value" inputmode="numeric" autocomplete="off" autofocus>
        <button class="primary">Send</button></form>{cancel}"""
    elif kind == "confirm":
        inner = f"""<p>Does {dev} show <b class="code">{value}</b>?</p>
      <div class="row"><button class="primary" hx-post="ui/answer" hx-vals='{{"accept":"yes"}}'>Yes, it matches</button>
        <button hx-post="ui/answer" hx-vals='{{"accept":"no"}}'>No</button></div>"""
    elif kind == "authorize":
        inner = f"""<p>{dev} wants to pair. Allow?</p>
      <div class="row"><button class="primary" hx-post="ui/answer" hx-vals='{{"accept":"yes"}}'>Allow</button>
        <button hx-post="ui/answer" hx-vals='{{"accept":"no"}}'>Deny</button></div>"""
    else:  # display
        inner = f"<p>Enter <b class=\"code\">{value}</b> on {dev} if it asks for a code.</p>"
    return f'<div class="prompt">{inner}</div>'


def _pair(s):
    busy = bool(s["busy"])
    if s["has_key"]:
        text = ("Paired with the machine; its key is stored, so no PIN is needed again. "
                "Reads run on every add-on start.")
        controls = f"""
  <div class="row">
    <button class="primary{' spin' if s['busy'] == 'read' else ''}" hx-post="ui/read"{' disabled' if not s['bonded'] or busy else ''}>Connect and read</button>
    <button class="danger" hx-post="ui/forget" hx-confirm="Forget the bond and the stored pairing key?"{' disabled' if busy else ''}>Forget this machine</button>
  </div>"""
    else:
        text = ("Put the machine in pairing mode and type the number on its screen. Needed exactly once: "
                "the key it produces is kept by the add-on. Nothing is written to the machine.")
        controls = f"""
  <form class="row" hx-post="ui/pair">
    <input id="pin" name="pin" inputmode="numeric" autocomplete="off" placeholder="PIN on screen">
    <button class="primary{' spin' if s['busy'] == 'pair' else ''}"{' disabled' if not s['bonded'] or busy else ''}>Pair and read</button>
  </form>"""
    body = f"<p>{text}</p>{controls}"
    return _step(3, "Pair with the machine and read", s["has_key"], body, inactive=not s["bonded"])


def _results(s):
    results = s["results"] or {}
    if not results:
        meta = '<p class="muted">Nothing read yet.</p>'
        blocks = ""
    else:
        meta = f'<p class="muted">Read at {h(s["results_at"] or "?")}. Session state afterwards: {h(str(s["session_state"]))}.</p>'
        parts = []
        for name, r in results.items():
            tag = '<span class="tag ok">ok</span>' if r["ok"] else f'<span class="tag bad">{h(r["error"])}</span>'
            parts.append(f"<p><b>{h(name)}</b> {tag}</p>")
            if r["ok"]:
                parts.append(f"<pre>{h(json.dumps(r['value'], indent=2, sort_keys=True))}</pre>")
        blocks = "".join(parts)
    done = bool(results) and all(r["ok"] for r in results.values())
    return _step(4, "Results", done, meta + blocks)


def _activity(s):
    lines = "\n".join(
        f'<span class="{h(l["level"])}">{h(l["t"])} {h(l["msg"])}</span>'
        for l in reversed(s["log"])
    )
    return f"""
<section>
  <h2>Activity <span class="muted small">newest first</span></h2>
  <pre class="log">{lines}</pre>
</section>"""


CSS = """
  :root {
    --bg: #f6f7f9; --card: #ffffff; --ink: #1c2430; --muted: #66707d;
    --line: #dde2e8; --accent: #1d3557; --accent-ink: #ffffff;
    --ok: #1a7f37; --warn: #9a6700; --err: #b42318; --err-bg: #fdecec;
    --mono: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  }
  @media (prefers-color-scheme: dark) {
    :root {
      --bg: #111418; --card: #1a1f26; --ink: #e6e9ee; --muted: #98a2b3;
      --line: #2b323c; --accent: #7fa7d6; --accent-ink: #0f1620;
      --ok: #4ade80; --warn: #fbbf24; --err: #f87171; --err-bg: #3a1a1a;
    }
  }
  * { box-sizing: border-box; }
  body { margin: 0; background: var(--bg); color: var(--ink);
         font: 15px/1.5 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; }
  main { max-width: 760px; margin: 0 auto; padding: 20px 16px 60px; }
  h1 { font-size: 22px; margin: 0 0 4px; }
  h2 { font-size: 16px; margin: 0 0 10px; display: flex; align-items: center; gap: 8px; }
  h2 .n { display: inline-grid; place-items: center; width: 24px; height: 24px;
          border-radius: 50%; background: var(--accent); color: var(--accent-ink);
          font-size: 13px; font-weight: 600; }
  h2 .n.done { background: var(--ok); }
  .sub { color: var(--muted); margin: 0 0 20px; }
  section { background: var(--card); border: 1px solid var(--line); border-radius: 10px;
            padding: 16px; margin-bottom: 14px; }
  section.inactive { opacity: .55; }
  p { margin: 8px 0; }
  .muted { color: var(--muted); } .small { font-size: 13px; font-weight: 400; }
  .mono, .code { font-family: var(--mono); }
  .code { font-size: 18px; letter-spacing: .15em; }
  button { font: inherit; padding: 8px 14px; border-radius: 8px; cursor: pointer;
           border: 1px solid var(--line); background: var(--card); color: var(--ink); }
  button.primary { background: var(--accent); color: var(--accent-ink); border-color: var(--accent); }
  button.danger { color: var(--err); }
  button:disabled { opacity: .5; cursor: default; }
  input { font: inherit; padding: 8px 10px; border-radius: 8px; border: 1px solid var(--line);
          background: var(--bg); color: var(--ink); width: 10em; letter-spacing: .1em; }
  .row { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin: 10px 0 0; }
  table { width: 100%; border-collapse: collapse; margin-top: 10px; font-size: 14px; }
  th, td { text-align: left; padding: 6px 8px; border-bottom: 1px solid var(--line); vertical-align: top; }
  th { color: var(--muted); font-weight: 500; }
  tr.cand td:first-child { font-weight: 600; }
  tr.sel { outline: 2px solid var(--accent); outline-offset: -2px; }
  .tag { display: inline-block; padding: 0 6px; border-radius: 4px; font-size: 12px;
         border: 1px solid var(--line); color: var(--muted); margin-right: 4px; }
  .tag.ok { color: var(--ok); border-color: var(--ok); }
  .tag.bad { color: var(--err); border-color: var(--err); }
  .banner { background: var(--err-bg); color: var(--err); border: 1px solid var(--err);
            border-radius: 8px; padding: 10px 12px; margin-bottom: 14px; }
  .prompt { border: 2px solid var(--warn); border-radius: 8px; padding: 12px; margin-top: 12px; }
  pre { background: var(--bg); border: 1px solid var(--line); border-radius: 8px;
        padding: 10px; overflow-x: auto; font: 12.5px/1.45 var(--mono); margin: 6px 0 12px; }
  pre.log { max-height: 320px; overflow-y: auto; }
  .log .warning { color: var(--warn); } .log .error { color: var(--err); } .log .debug { color: var(--muted); }
  details summary { cursor: pointer; color: var(--muted); }
  .spin::after, button.htmx-request::after {
    content: ""; display: inline-block; width: .8em; height: .8em; margin-left: 8px;
    border: 2px solid currentColor; border-right-color: transparent; border-radius: 50%;
    animation: s 1s linear infinite; vertical-align: -1px; }
  @keyframes s { to { transform: rotate(360deg); } }
"""
