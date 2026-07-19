"""Node-RED launch panel.

Launches Node-RED (systemd `nodered.service`, or the `node-red` binary), confirms
its editor is serving on :1880, and shows the URL to open it from a laptop.

Custom panel: **6 feedback fields** + **6 trigger buttons** you wire to your own
flow. The contract kilodash speaks (all on the Pi, so localhost):

  Feedback  GET  http://127.0.0.1:1880/kilodash/state
            → {"fields": [{"label": "Temp", "value": "21.4"},   # up to 6
                          {"label": "Door", "value": "open"}, ...]}
  Triggers  POST http://127.0.0.1:1880/kilodash/btn/1 .. /btn/6
            → button label read from the same /state payload, if present:
              {"buttons": [{"label": "Fan"}, ...]}

Import the ready-made flow at setup/nodered-kilodash-flow.json and read
setup/NODE-RED.md for the full wire-up guide (feedback fields come from flow
context f1..f6; older 4-field flows keep working, extra rows just show "—" /
default labels). Until the flow exists, fields show "—" and buttons post
harmlessly (404) — the panel still launches and confirms.
"""

from .. import theme as T, webapp
from ..widgets import Button, spaced
from .webapp_base import WebAppScreen

BASE = "http://127.0.0.1:1880/kilodash"
N_FIELDS = 6
N_BTNS = 6


class NodeRedScreen(WebAppScreen):
    title = "Node-RED"
    tile_id = "node-red"
    glyph = "nodered"
    tile_color_key = "bad"          # Node-RED's brand red reads well as a tile
    app_name = "Node-RED"
    port = 1880
    service = "nodered.service"     # installed by kilodash; launched on demand
    url_path = "/"

    def __init__(self, app):
        super().__init__(app)
        self.fields = [{"label": f"Field {i + 1}", "value": "—"}
                       for i in range(N_FIELDS)]
        self.buttons = [{"label": f"Trigger {i + 1}"} for i in range(N_BTNS)]
        self._flash = {}            # btn index -> monotonic expiry for tap feedback

    def poll_app(self):
        if self.web.state != webapp.UP:
            return False
        data = webapp.http_json(f"{BASE}/state", timeout=1.0)
        if not isinstance(data, dict):
            return False
        f = data.get("fields")
        if isinstance(f, list) and f:
            self.fields = [{"label": str(x.get("label", f"Field {i + 1}"))[:10],
                            "value": str(x.get("value", "—"))[:9]}
                           for i, x in enumerate(f[:N_FIELDS])]
            while len(self.fields) < N_FIELDS:
                self.fields.append({"label": f"Field {len(self.fields) + 1}",
                                    "value": "—"})
        b = data.get("buttons")
        if isinstance(b, list) and b:
            for i in range(N_BTNS):
                if i < len(b) and isinstance(b[i], dict):
                    self.buttons[i]["label"] = str(b[i].get("label",
                                                            f"Trigger {i + 1}"))[:10]
        return True


    def model_rows(self):
        """Service rows plus the six flow-driven feedback fields."""
        rows = super().model_rows()
        for f in (self.fields or []):
            val = str(f.get("value", "—"))
            if val and val != "—":
                rows.append({"label": str(f.get("label", "?")),
                             "value": val, "state": None})
        return rows


    def model_buttons(self):
        """The flow's own six buttons. What each does is defined by the user's
        Node-RED flow, so no confirm is claimed here — the box cannot know
        whether a given flow button is harmless or not."""
        rows = super().model_buttons()
        up = getattr(self.web, "state", None) == webapp.UP
        for i, b in enumerate(self.buttons or []):
            rows.append({"id": f"btn{i}", "label": str(b.get("label", i + 1)),
                         "enabled": up, "confirm": False})
        return rows

    def handle_button(self, bid):
        if bid.startswith("btn"):
            try:
                i = int(bid[3:])
            except ValueError:
                return False
            if 0 <= i < len(self.buttons or []):
                webapp.http_post(f"{BASE}/btn/{i + 1}", timeout=1.5)
                return True
            return False
        return super().handle_button(bid)

    def draw_app(self, d, th, top):
        w = self.app.w
        gap = 8
        cw = (w - 12 * 2 - gap) / 2

        # 6 feedback fields (3x2) — hard-edged mini-instrument cells
        d.text((14, top), spaced("DEBUG FEEDBACK"),
               font=T.font(10, bold=True, mono=True), fill=th.muted)
        top += 18
        fh = 46
        rows = (len(self.fields) + 1) // 2
        for i, fld in enumerate(self.fields):
            r, c = divmod(i, 2)
            x0 = 12 + c * (cw + gap)
            y0 = top + r * (fh + gap)
            d.rectangle((x0, y0, x0 + cw, y0 + fh), fill=th.card,
                        outline=th.card_hi, width=1)
            d.text((x0 + 10, y0 + 6), spaced(fld["label"][:10].upper()),
                   font=T.font(9, bold=True, mono=True), fill=th.muted)
            d.text((x0 + 10, y0 + 21), fld["value"],
                   font=T.font(18, bold=True, mono=True), fill=th.accent)

        # 6 trigger buttons (3x2) — terse caps, geometry unchanged
        top += rows * (fh + gap) - gap + 6
        d.text((14, top), spaced("TRIGGERS"),
               font=T.font(10, bold=True, mono=True), fill=th.muted)
        top += 18
        bh = 44
        for i, btn in enumerate(self.buttons):
            r, c = divmod(i, 2)
            x0 = 12 + c * (cw + gap)
            y0 = top + r * (bh + gap)
            kind = "primary" if self.web.state == webapp.UP else "normal"
            b = Button((x0, y0, x0 + cw, y0 + bh), btn["label"][:10].upper(),
                       kind=kind, font_size=15)
            b.enabled = self.web.state == webapp.UP
            b.draw(d, th)
            self._btns[f"btn{i}"] = b

    def handle_app_tap(self, x, y):
        for i in range(N_BTNS):
            b = self._btns.get(f"btn{i}")
            if b and b.hit(x, y):
                code = webapp.http_post(f"{BASE}/btn/{i + 1}", timeout=1.5)
                ok = code is not None and int(code) < 400
                self.app.toast(f"{self.buttons[i]['label']}: "
                               f"{'sent' if ok else 'no handler'}")
                return True
        return False
