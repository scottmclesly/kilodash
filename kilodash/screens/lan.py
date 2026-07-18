"""LAN Scan — DIAGNOSTICS ONLY. See LAN-Scan-Refactor-TODO.md for full scope.

This screen answers only: what's alive on my subnet, what services/versions do
hosts run, and is an expected port open on a known host. It is *physically
incapable* of expressing an offensive scan: there is no raw-flag input. The
mode segmented control (Discover · Ports · Services · Identify) is the safety
boundary — every command is assembled from that intent by scan.build_scan_command,
which refuses NSE (--script/-sC), stealth/evasion scans, decoys and spoofing.
No evasion, no NSE, no vuln probing, no spoofing is reachable from here.

Discover renders one tappable card per host (frame colour = up/down); tapping a
card sets it as the port-scan target and jumps to Ports. Ports/Services/Identify
render streamed text rows. Dimensions come from self.app.w/h (never hardcode).
"""

from PIL import Image, ImageDraw

from .. import scan, theme as T
from ..widgets import Button, Keyboard, rrect
from .base import Screen, HEADER_H

# Fixed control bands. The output pane below them starts at _out_top(), which
# drops by one field in Ports mode to make room for the ports field.
TARGET_Y = HEADER_H + 6          # target field + Run/count button
FIELD_H = 38
MODE_Y = TARGET_Y + FIELD_H + 6  # mode segmented control
SEG_H = 36
PORTS_Y = MODE_Y + SEG_H + 6     # ports field — Ports mode only
LINE_H = 22

# Discover host cards.
CARD_MARGIN = 12
CARD_H = 52
CARD_GAP = 8


def _result_rows(host):
    """(text, colour_key) rows for one host's ports + OS info — the body shown
    in Ports/Services/Identify. No IP header and no 'up' line: those are implied
    by the single-host pane or the card frame."""
    rows = []
    for p in host.get("ports", []):
        txt = f"{p['port']}/{p['proto']}  {p['state']}  {p['service']}"
        if p.get("version"):
            txt += f"  {p['version']}"
        rows.append((txt[:46], "ok" if p["state"] == "open" else "muted"))
    for info in host.get("info", []):
        rows.append((info[:46], "warn" if info.startswith("OS: no") else "muted"))
    if not rows:
        rows.append(("host up · no listed ports open" if host.get("up", True)
                     else "host down", "muted"))
    return rows


class LanScreen(Screen):
    title = "LAN Scan"
    glyph = "lan"
    tile_color_key = "accent"
    scrollable = True

    def __init__(self, app):
        super().__init__(app)
        self.tick_interval = 0.3        # responsive while output streams
        self.mode = "Discover"
        self.target = ""
        self.ports = ""
        self.job = None
        self._done_handled = True
        self.status = "Set a target and tap Run"
        self.selected_ip = None
        self._discover_hosts = []        # cached so cards survive a mode switch
        # hit boxes recorded each draw
        self._target_box = None
        self._ports_box = None
        self._seg_boxes = []
        self._card_hits = []
        self.run_btn = None

    def on_enter(self):
        if not self.target:
            self.target = scan.default_target()

    # ---- layout ----
    def _out_top(self):
        base = MODE_Y + SEG_H + 6
        return base + FIELD_H + 6 if self.mode == "Ports" else base

    def content_area(self):
        ot = self._out_top()
        return (0, ot, self.app.w, self.app.h - ot)

    # ---- scanning ----
    def start_scan(self):
        if not self.target:
            self.status = "Enter a target first"
            self.app.toast("Enter a target first")
            return
        self.scroll = 0
        self._done_handled = False
        self.job = scan.ScanJob(self.mode, self.target, self.ports)

    def stop_scan(self):
        if self.job and not self.job.done:
            self.job.stop()

    def _scanning(self):
        return self.job is not None and not self.job.done

    def tick(self):
        j = self.job
        if j is None:
            return False
        if not j.done:
            return True                 # redraw to show streamed rows/cards
        if not self._done_handled:
            self._done_handled = True
            return True
        return False

    # ---- rendering ----
    def draw_content(self, d, th):
        w, h = self.app.w, self.app.h
        out_top = self._out_top()
        self._ports_box = None
        self._card_hits = []
        if self.mode == "Discover":
            self._draw_cards(d, th, w, h, out_top)
        else:
            self._draw_output(d, th, w, h, out_top)
        # controls drawn on top of the (cleared) upper band
        d.rectangle((0, HEADER_H, w, out_top), fill=th.bg)
        self._draw_target_row(d, th, w)
        self._draw_mode_control(d, th, w)
        if self.mode == "Ports":
            self._draw_ports_row(d, th, w)

    def _draw_target_row(self, d, th, w):
        run_w = 96
        self._target_box = (12, TARGET_Y, w - run_w - 18, TARGET_Y + FIELD_H)
        rrect(d, self._target_box, 9, fill=th.card, outline=th.accent, width=1)
        label = self.target or "tap to set target (IP / host / CIDR)"
        fill = th.fg if self.target else th.muted
        d.text((22, TARGET_Y + 10), label[:26],
                font=T.font(16, mono=bool(self.target)), fill=fill)
        # Run button. While scanning it's Stop; once a scan completes it shows
        # the found-host count (tapping re-runs) — the count lives here instead
        # of on a dedicated status line/badge.
        if self._scanning():
            blabel, kind = "Stop", "danger"
        elif (self.job is not None and self.job.done
                and self.job.mode == self.mode and self.job.host_count):
            blabel, kind = str(self.job.host_count), "primary"
        else:
            blabel, kind = "Run", "primary"
        self.run_btn = Button((w - run_w - 6, TARGET_Y, w - 8, TARGET_Y + FIELD_H),
                              blabel, kind=kind, font_size=17)
        self.run_btn.draw(d, th)

    def _draw_mode_control(self, d, th, w):
        self._seg_boxes = []
        n = len(scan.MODES)
        gap = 6
        seg_w = (w - 24 - gap * (n - 1)) / n
        for i, m in enumerate(scan.MODES):
            x0 = 12 + i * (seg_w + gap)
            box = (x0, MODE_Y, x0 + seg_w, MODE_Y + SEG_H)
            active = (m == self.mode)
            rrect(d, box, 8, fill=th.accent if active else th.card,
                  outline=th.card_hi, width=1)
            f = T.font(14, bold=active)
            tw = d.textlength(m, font=f)
            d.text((x0 + seg_w / 2 - tw / 2, MODE_Y + 9), m, font=f,
                   fill=th.ink if active else th.muted)
            self._seg_boxes.append((box, m))

    def _draw_ports_row(self, d, th, w):
        # Full-width ports field (no host badge sharing the line).
        self._ports_box = (12, PORTS_Y, w - 12, PORTS_Y + FIELD_H)
        rrect(d, self._ports_box, 9, fill=th.card, outline=th.accent, width=1)
        # Just the port list — muted when it's the default set, bright once the
        # user overrides it. (The field is tappable; no room to spell that out.)
        shown = self.ports or scan.COMMON_PORTS
        fill = th.fg if self.ports else th.muted
        d.text((22, PORTS_Y + 11), shown[:38],
               font=T.font(14, mono=True), fill=fill)

    # ---- Discover: host cards ----
    def _draw_cards(self, d, th, w, h, out_top):
        if self.job and self.job.mode == "Discover":
            self._discover_hosts = self.job.hosts_snapshot()
        hosts = self._discover_hosts
        view_h = h - out_top
        if not hosts:
            self.content_h = view_h
            d.rectangle((0, out_top, w, h), fill=th.bg)
            if self.job and self.job.mode == "Discover" and not self.job.done:
                hint = self.job.status
            else:
                hint = "Tap Run to discover hosts on the subnet"
            d.text((22, out_top + 16), hint, font=T.font(15), fill=th.muted)
            return

        self.content_h = max(CARD_GAP + len(hosts) * (CARD_H + CARD_GAP), view_h)
        surf = Image.new("RGB", (w, self.content_h), th.bg)
        sd = ImageDraw.Draw(surf)
        for i, host in enumerate(hosts):
            y0 = CARD_GAP + i * (CARD_H + CARD_GAP)
            y1 = y0 + CARD_H
            box = (CARD_MARGIN, y0, w - CARD_MARGIN, y1)
            up = host.get("up", True)
            ip = host.get("ip", "")
            selected = ip == self.selected_ip
            frame = th.ok if up else th.bad          # up/down encoded in frame
            rrect(sd, box, 10, fill=th.card_hi if selected else th.card,
                  outline=frame, width=3 if selected else 2)
            # line 1: IP (accent) left · MAC (muted) right — colour separation
            sd.text((CARD_MARGIN + 12, y0 + 8), ip,
                    font=T.font(15, bold=True, mono=True), fill=th.accent)
            mac = host.get("mac", "")
            if mac:
                machex = mac.split()[0]              # hex only; vendor won't fit
                mf = T.font(13, mono=True)
                mw = sd.textlength(machex, font=mf)
                sd.text((w - CARD_MARGIN - 12 - mw, y0 + 9), machex,
                        font=mf, fill=th.muted)
            # line 2: identity, full line — reverse-DNS name if we have one,
            # else the MAC vendor (e.g. "Raspberry Pi Foundation"), else unknown.
            name = host.get("host") or host.get("vendor")
            sd.text((CARD_MARGIN + 12, y0 + 29), (name or "(unknown host)")[:40],
                    font=T.font(14), fill=th.fg if name else th.muted)
            self._card_hits.append((CARD_MARGIN, y0, w - CARD_MARGIN, y1, ip))
        self.paste_list(out_top, view_h, surf)

    # ---- Ports/Services/Identify: structured results ----
    def _draw_output(self, d, th, w, h, out_top):
        # Only show a job's results in the mode that produced it, so a discover
        # job (or a leftover from another mode) doesn't bleed into this pane.
        job = self.job if (self.job and self.job.mode == self.mode) else None
        hosts = job.hosts_snapshot() if job else []
        view_h = h - out_top
        if not hosts:
            self.content_h = view_h
            d.rectangle((0, out_top, w, h), fill=th.bg)
            if job and job.error:
                msg, col = job.error, th.bad
            elif job:
                msg, col = job.status, th.muted
            else:
                msg, col = "Tap Run to scan this target", th.muted
            d.text((22, out_top + 16), msg[:44], font=T.font(15), fill=col)
            return
        if len(hosts) == 1:
            self._draw_single_result(hosts[0], th, w, view_h, out_top)
        else:
            self._draw_result_cards(hosts, th, w, view_h, out_top)

    def _draw_single_result(self, host, th, w, view_h, out_top):
        # One host: flat, un-indented port list — no IP header, no "up" line.
        rows = _result_rows(host)
        self.content_h = max(len(rows) * LINE_H + 8, view_h)
        surf = Image.new("RGB", (w, self.content_h), th.bg)
        sd = ImageDraw.Draw(surf)
        for i, (txt, color) in enumerate(rows):
            sd.text((20, i * LINE_H), txt, font=T.font(14, mono=True),
                    fill=getattr(th, color, th.fg))
        self.paste_list(out_top, view_h, surf)

    def _draw_result_cards(self, hosts, th, w, view_h, out_top):
        # Multiple hosts: one card each (Discover pattern), ports listed inside.
        row_h, head_h, pad = 20, 46, 10
        heights = [head_h + max(1, len(_result_rows(hh))) * row_h + pad
                   for hh in hosts]
        total = CARD_GAP + sum(hh + CARD_GAP for hh in heights)
        self.content_h = max(total, view_h)
        surf = Image.new("RGB", (w, self.content_h), th.bg)
        sd = ImageDraw.Draw(surf)
        y = CARD_GAP
        for host, hh in zip(hosts, heights):
            frame = th.ok if host.get("up", True) else th.bad
            rrect(sd, (CARD_MARGIN, y, w - CARD_MARGIN, y + hh), 10,
                  fill=th.card, outline=frame, width=2)
            ip = host.get("ip", "")
            sd.text((CARD_MARGIN + 12, y + 8), ip,
                    font=T.font(15, bold=True, mono=True), fill=th.accent)
            mac = host.get("mac", "")
            if mac:
                machex = mac.split()[0]
                mf = T.font(13, mono=True)
                mw = sd.textlength(machex, font=mf)
                sd.text((w - CARD_MARGIN - 12 - mw, y + 9), machex,
                        font=mf, fill=th.muted)
            name = host.get("host") or host.get("vendor") or "(unknown host)"
            sd.text((CARD_MARGIN + 12, y + 27), name[:40],
                    font=T.font(13), fill=th.muted)
            ry = y + head_h
            for txt, color in _result_rows(host):
                sd.text((CARD_MARGIN + 16, ry), txt,
                        font=T.font(13, mono=True), fill=getattr(th, color, th.fg))
                ry += row_h
            y += hh + CARD_GAP
        self.paste_list(out_top, view_h, surf)

    # ---- input ----
    def handle_tap(self, x, y):
        if self.run_btn and self.run_btn.hit(x, y):
            if self._scanning():
                self.stop_scan()
            else:
                self.start_scan()
            return True
        for box, m in self._seg_boxes:
            if self._in(box, x, y):
                if m != self.mode:
                    self._switch_mode(m)
                return True
        if self._in(self._target_box, x, y):
            self._edit_target()
            return True
        if self._ports_box and self._in(self._ports_box, x, y):
            self._edit_ports()
            return True
        # host card taps (Discover) — map screen y into the scrolled surface
        if self.mode == "Discover" and self._card_hits:
            out_top = self._out_top()
            if y >= out_top:
                cy = (y - out_top) + self.scroll
                for x0, y0, x1, y1, ip in self._card_hits:
                    if x0 <= x <= x1 and y0 <= cy <= y1:
                        self._select_host(ip)
                        return True
        return False

    def _switch_mode(self, m):
        self.mode = m
        self.scroll = 0
        # Discover works on a subnet, so restore the local CIDR if the target is
        # currently a single IP (e.g. one just picked for a port scan).
        if m == "Discover" and "/" not in (self.target or ""):
            sub = scan.default_target()
            if sub:
                self.target = sub

    def _select_host(self, ip):
        """Tapping a Discover card picks it as the port-scan target and jumps to
        Ports with its IP prepopulated in the target field."""
        self.selected_ip = ip
        self.target = ip
        self.mode = "Ports"
        self.scroll = 0
        self.app.toast(f"Port-scan target: {ip}")

    @staticmethod
    def _in(box, x, y):
        if not box:
            return False
        x0, y0, x1, y1 = box
        return x0 <= x <= x1 and y0 <= y <= y1

    def _edit_target(self):
        kb = Keyboard(self.app.w, self.app.h, title="Target (IP / host / CIDR)",
                      secret=False,
                      on_done=self._set_target, on_cancel=self.app.close_keyboard)
        kb.text = self.target
        self.app.open_keyboard(kb)

    def _set_target(self, text):
        self.app.close_keyboard()
        text = text.strip()
        if text and not scan._valid_target(text):
            self.app.toast("Invalid target")
            return
        self.target = text

    def _edit_ports(self):
        kb = Keyboard(self.app.w, self.app.h, title="Ports (blank = common)",
                      secret=False,
                      on_done=self._set_ports, on_cancel=self.app.close_keyboard)
        kb.text = self.ports
        kb.numeric = True
        self.app.open_keyboard(kb)

    def _set_ports(self, text):
        self.app.close_keyboard()
        text = text.strip()
        if text and not scan._valid_ports(text):
            self.app.toast("Ports: digits, commas, hyphens only")
            return
        self.ports = text
