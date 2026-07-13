"""Tables — remote control + mirror for the decode-table store.

Thin by design (TABLES.md §4): this tile performs **no conversion and no
parsing beyond reading manifests** — validate/ingest/manage live in the
converter service (kilodash/tableconv.py); the tile starts/stops that
service and mirrors the store, so the two views can never disagree.

Two panes in the Scottina portrait structure:
  service   — status (stopped/starting/running/idle-N:MM), Start/Stop,
              and while running the URL **plus QR code** for the
              advertised address (net.advertise_addr, eth0-preferred);
  inventory — installed tables (name, PGN count, enabled?) straight from
              the store manifests; tap toggles enable (the single
              sanctioned non-converter write: an atomic manifest flip),
              ✕ removes after a confirm re-tap.

Always visible — no hardware gate; tables are software. Entering the tile
starts the service (on-demand lifecycle); leaving does NOT stop it — the
converter idles itself out after N minutes so a Mac-side session survives
kilodash navigation.
"""

import time

from PIL import Image, ImageDraw

from .. import net, theme as T, webapp
from ..widgets import Button, rrect
from .base import Screen, HEADER_H

# the table store lives at the repo root (TABLES.md §1)
from tables import store

SERVICE = "kilodash-tables.service"
IDLE_DEFAULT_MIN = 15                # mirrors tableconv.IDLE_DEFAULT_MIN

SVC_Y = HEADER_H + 6                 # 50   status card
SVC_H = 46
URL_Y = SVC_Y + SVC_H + 6            # 102  URL + QR card (when running)
URL_H = 150
BTN_H = 42
LIST_TOP = URL_Y + URL_H + 6 + BTN_H + 8   # 286  inventory pane
ROW_H = 40

_STATE_LABEL = {
    webapp.UP: ("running", "ok"),
    webapp.STARTING: ("starting…", "accent"),
    webapp.ERROR: ("problem", "bad"),
    webapp.STOPPED: ("stopped", "muted"),
}


def _qr_matrix(text):
    """QR module matrix for a URL, or None if qrcode isn't available."""
    try:
        import qrcode
        qr = qrcode.QRCode(border=1,
                           error_correction=qrcode.ERROR_CORRECT_M)
        qr.add_data(text)
        qr.make(fit=True)
        return qr.get_matrix()
    except Exception:               # noqa: BLE001 — QR is a nicety
        return None


class TablesScreen(Screen):
    title = "Tables"
    glyph = "tables"
    tile_color_key = "accent"
    scrollable = True

    def __init__(self, app):
        super().__init__(app)
        self.tick_interval = 1.0
        self.web = webapp.WebApp("Tables", net.TABLECONV_PORT,
                                 service=SERVICE)
        self.inv = []
        self.idle_secs = None            # from the service's /status
        self._status_t = 0.0
        self.confirm = None              # (name, until) remove confirm state
        self._qr_cache = (None, None)    # (url, PIL image)
        self._btns = {}
        self._sig = None

    # --------------------------------------------------------------- lifecycle
    def on_enter(self):
        # tile-open starts the service (spec); leaving never stops it —
        # the converter's own idle timeout owns shutdown
        if not self.web.running:
            self.web.launch()
        self.inv = store.list_tables()
        self.idle_secs = None
        self.confirm = None
        self._sig = None

    def tick(self):
        changed = self.web.poll()
        now = time.monotonic()
        if self.web.state == webapp.UP and now - self._status_t >= 5.0:
            self._status_t = now
            st = webapp.http_json(
                f"http://127.0.0.1:{self.web.port}/status", timeout=0.8)
            self.idle_secs = st.get("idle_secs") if st else None
        inv = store.list_tables()
        if self.confirm and now > self.confirm[1]:
            self.confirm = None
            changed = True
        sig = (self.web.state, self.idle_secs and int(self.idle_secs // 10),
               [(t["name"], t["enabled"], t["verified"]) for t in inv])
        if sig != self._sig:
            self._sig = sig
            self.inv = inv
            return True
        self.inv = inv
        return changed

    # --------------------------------------------------------------- drawing
    def content_area(self):
        return (0, LIST_TOP, self.app.w, self.app.h - LIST_TOP)

    def _state_line(self):
        label, colkey = _STATE_LABEL[self.web.state]
        if self.web.state == webapp.UP and self.idle_secs is not None:
            left = max(0, IDLE_DEFAULT_MIN * 60 - self.idle_secs)
            label = f"running · idle-{int(left // 60)}:{int(left % 60):02d}"
        return label, colkey

    def draw_content(self, d, th):
        w, h = self.app.w, self.app.h
        self._btns = {}
        self._draw_inventory(d, th, w, h)
        d.rectangle((0, HEADER_H, w, LIST_TOP), fill=th.bg)

        # ---- service pane ----
        y = SVC_Y
        rrect(d, (14, y, w - 14, y + SVC_H), 10, fill=th.card)
        label, colkey = self._state_line()
        d.ellipse((26, y + 16, 40, y + 30), fill=getattr(th, colkey))
        d.text((50, y + 6), "Converter service", font=T.font(14, bold=True),
               fill=th.fg)
        d.text((50, y + 25), f"{label} · {self.web.message[:20]}",
               font=T.font(12), fill=th.muted)

        y = URL_Y
        rrect(d, (14, y, w - 14, y + URL_H), 10, fill=th.card)
        if self.web.state in (webapp.UP, webapp.STARTING):
            url = f"http://{net.advertise_addr()}:{self.web.port}/"
            d.text((24, y + 8), "OPEN FROM A BIG SCREEN",
                   font=T.font(10, bold=True), fill=th.muted)
            d.text((24, y + 22), url[:34],
                   font=T.font(14, bold=True, mono=True),
                   fill=th.accent if self.web.state == webapp.UP else th.muted)
            self._draw_qr(d, th, w, y + 44, URL_H - 52, url)
        else:
            d.text((24, y + 10), "Service is stopped.",
                   font=T.font(14, bold=True), fill=th.muted)
            d.text((24, y + 34), "Start it to convert vendor PDFs into",
                   font=T.font(12), fill=th.muted)
            d.text((24, y + 50), "PGN decode tables (reviewed in a",
                   font=T.font(12), fill=th.muted)
            d.text((24, y + 66), "browser, TABLES.md contract).",
                   font=T.font(12), fill=th.muted)
            if not self.web.installed():
                d.text((24, y + 92), "unit missing — run setup/install-tables.sh",
                       font=T.font(11, mono=True), fill=th.warn)

        y = URL_Y + URL_H + 6
        running = self.web.running
        b = Button((14, y, w - 14, y + BTN_H),
                   "Stop service" if running else "Start service",
                   kind="danger" if running else "primary", font_size=16)
        b.enabled = self.web.installed()
        b.draw(d, th)
        self._btns["power"] = b.box if b.enabled else None

    def _draw_qr(self, d, th, w, top, avail, url):
        """QR of the advertised URL — white card, black modules, so any
        phone camera reads it off the 480×320-class panel."""
        cached_url, img = self._qr_cache
        if cached_url != url:
            m = _qr_matrix(url)
            if m is None:
                self._qr_cache = (url, None)
            else:
                n = len(m)
                scale = max(1, int(avail // n))
                img = Image.new("RGB", (n * scale + 8, n * scale + 8),
                                (255, 255, 255))
                qd = ImageDraw.Draw(img)
                for ry, row in enumerate(m):
                    for rx, mod in enumerate(row):
                        if mod:
                            x0 = 4 + rx * scale
                            y0 = 4 + ry * scale
                            qd.rectangle((x0, y0, x0 + scale - 1,
                                          y0 + scale - 1), fill=(0, 0, 0))
                self._qr_cache = (url, img)
        _, img = self._qr_cache
        if img is None:
            d.text((24, top + 8), "qrcode lib missing", font=T.font(11),
                   fill=th.muted)
            return
        self._img.paste(img, (w - 24 - img.width,
                              top + max(0, (avail - img.height) // 2)))
        d.text((24, top + avail // 2 - 8), "scan →", font=T.font(13),
               fill=th.muted)

    def _draw_inventory(self, d, th, w, h):
        pane_h = h - LIST_TOP
        inv = self.inv
        if not inv:
            self.content_h = pane_h
            d.rectangle((0, LIST_TOP, w, h), fill=th.bg)
            d.text((24, LIST_TOP + 10), "no tables installed",
                   font=T.font(13), fill=th.muted)
            d.text((24, LIST_TOP + 30), "convert a PDF, or drop JSON via "
                   "the Files tile", font=T.font(11), fill=th.muted)
            return
        self.content_h = max(len(inv) * ROW_H + 4, pane_h)
        surf = Image.new("RGB", (w, self.content_h), th.bg)
        sd = ImageDraw.Draw(surf)
        fn = T.font(14, bold=True, mono=True)
        fs = T.font(11, mono=True)
        for i, t in enumerate(inv):
            y = i * ROW_H
            confirm = self.confirm and self.confirm[0] == t["name"]
            rrect(sd, (14, y + 1, w - 14, y + ROW_H - 1), 8,
                  fill=th.card_hi if t["enabled"] else th.card)
            sd.text((24, y + 4), t["name"][:20], font=fn,
                    fill=th.fg if t["enabled"] else th.muted)
            state = ("unverified" if not t["verified"]
                     else "enabled ✓" if t["enabled"] else "disabled")
            sd.text((24, y + 21),
                    f"{t['pgn_count']} PGN{'s' if t['pgn_count'] != 1 else ''}"
                    f" · {state} · tap to toggle", font=fs,
                    fill=th.warn if not t["verified"]
                    else th.ok if t["enabled"] else th.muted)
            # remove zone (right edge): ✕, or the confirm state
            zx = w - 58
            if confirm:
                rrect(sd, (zx - 24, y + 4, w - 18, y + ROW_H - 4), 8,
                      fill=th.bad)
                sd.text((zx - 12, y + 11), "sure?", font=T.font(12, bold=True),
                        fill=th.ink)
            else:
                sd.text((w - 40, y + 8), "✕", font=T.font(18, bold=True),
                        fill=th.muted)
        self.paste_list(LIST_TOP, pane_h, surf)

    # ------------------------------------------------------------------ input
    def _in(self, key, x, y):
        box = self._btns.get(key)
        return box and box[0] <= x <= box[2] and box[1] <= y <= box[3]

    def handle_tap(self, x, y):
        if self._in("power", x, y):
            if self.web.running:
                self.web.stop()
            else:
                self.web.launch()
            return True
        if y < LIST_TOP or not self.inv:
            return False
        i = int((y - LIST_TOP + self.scroll) // ROW_H)
        if not 0 <= i < len(self.inv):
            return False
        t = self.inv[i]
        remove_zone = x > self.app.w - 82
        if remove_zone:
            if self.confirm and self.confirm[0] == t["name"]:
                store.remove(t["name"])
                self.confirm = None
                self.app.toast(f"Removed {t['name']}")
            else:
                self.confirm = (t["name"], time.monotonic() + 3.0)
            self.inv = store.list_tables()
            return True
        # enable/disable: the manifest-only atomic flip (TABLES.md §4)
        if not t["verified"]:
            self.app.toast("Unverified — re-ingest via the converter")
            return True
        new = store.set_enabled(t["name"], not t["enabled"])
        if new is None:
            self.app.toast("No manifest — re-ingest via the converter")
        self.inv = store.list_tables()
        return True
