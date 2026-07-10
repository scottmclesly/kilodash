"""Files — USB offload of capture logs + decode-table exchange (DBC / NMEA).

The tile appears only while a USB stick is plugged in (device_key
"usbstick"). Everything Scottina records lands in /opt/kilodash/captures;
this screen copies those files onto the stick — one per tap, or the lot with
Copy all — and exchanges CAN decode tables (DBC, NMEA/canboat) between the
stick and /opt/kilodash/tables, the canonical place decoding tools (the
Node-RED DBC flow, Signal K/canboatjs) read them from.

Mount discipline: the stick mounts at devices.USB_MOUNT on entry and is
sync'd + unmounted on Eject and on leaving the screen, so a yanked stick
never strands a mount (lazy-detach fallback). Copies never delete the
originals — captures/ stays authoritative until you clear it yourself.
"""

import os
import shutil
import subprocess

from PIL import Image, ImageDraw

from .. import devices, system, theme as T
from ..widgets import Button, rrect
from .base import Screen, HEADER_H

CAP_DIR = "/opt/kilodash/captures"
TABLE_DIR = "/opt/kilodash/tables"
MOUNT = devices.USB_MOUNT
DEST_SUB = "scottina"               # <stick>/scottina/{captures,tables}
# Decode-table types picked up from the stick (root or scottina/tables/):
# .dbc/.kcd/.sym = CAN signal databases, .n2k/.json/.xml = NMEA2000 (canboat)
TABLE_EXTS = (".dbc", ".kcd", ".sym", ".n2k", ".json", ".xml")

# Fixed vertical bands; x-coordinates derive from app.w at draw time
# (the panel is 320×480 portrait — never hardcode widths).
INFO_Y = HEADER_H + 6            # 50  stick card + Eject/Mount button
INFO_H = 54
ACT_Y = INFO_Y + INFO_H + 6      # 110 full-width Copy-all button
ACT_H = 48
TBL_Y = ACT_Y + ACT_H + 6        # 164 tables import | export
TBL_H = 44
STAT_Y = TBL_Y + TBL_H + 6       # 214 status strip
STAT_H = 26
LIST_TOP = STAT_Y + STAT_H + 6   # 246 scrollable capture list
ROW_H = 44                       # widgets.MIN_TOUCH — rows are tap targets


def _sh(*cmd, timeout=15):
    return subprocess.run(cmd, capture_output=True, text=True,
                          timeout=timeout)


def _human(n):
    for unit in ("B", "K", "M", "G"):
        if n < 1024 or unit == "G":
            return f"{n:.0f}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024


def _mount_state():
    """(source device, mounted-rw) for MOUNT per /proc/mounts, else
    (None, False). rw is the mount flag, not a permission check."""
    try:
        with open("/proc/mounts") as f:
            for line in f:
                fields = line.split()
                if fields[1] == MOUNT:
                    return fields[0], "rw" in fields[3].split(",")
    except OSError:
        pass
    return None, False


def _writable():
    return _mount_state()[1]


def _mount(parts):
    """Mount the stick: try each candidate partition and keep the first that
    mounts writable; fall back to a read-only one (tables can still be
    imported from it). Returns (mounted, writable, message)."""
    os.makedirs(MOUNT, exist_ok=True)
    if os.path.ismount(MOUNT):
        return True, _writable(), "already mounted"
    ro_dev = None
    for dev in parts:
        if _sh("mount", dev, MOUNT).returncode != 0:
            continue
        if _writable():
            return True, True, f"{dev} mounted"
        if ro_dev is None:
            ro_dev = dev
        _sh("umount", MOUNT)                # keep looking for a rw partition
    if ro_dev and _sh("mount", ro_dev, MOUNT).returncode == 0:
        return True, False, f"{ro_dev} read-only"
    return False, False, "mount failed"


def _unmount():
    """Sync then unmount; a yanked stick gets a lazy detach so the mount
    point never wedges."""
    _sh("sync", timeout=30)
    if not os.path.ismount(MOUNT):
        return True, "not mounted"
    r = _sh("umount", MOUNT, timeout=30)
    if r.returncode != 0:
        _sh("umount", "-l", MOUNT)
    return True, "Ejected — safe to remove"


def _copy_captures(names):
    """Background worker: copy capture files onto the stick, then sync so
    Eject is instant. Returns (n_copied, message)."""
    dest = os.path.join(MOUNT, DEST_SUB, "captures")
    os.makedirs(dest, exist_ok=True)
    n = 0
    for name in names:
        shutil.copy2(os.path.join(CAP_DIR, name), os.path.join(dest, name))
        n += 1
    _sh("sync", timeout=60)
    return n, f"Copied {n} file{'s' if n != 1 else ''} → USB"


def _import_tables():
    """Background worker: pull decode tables (TABLE_EXTS) from the stick's
    root and scottina/tables/ into /opt/kilodash/tables."""
    os.makedirs(TABLE_DIR, exist_ok=True)
    n = 0
    for src_dir in (MOUNT, os.path.join(MOUNT, DEST_SUB, "tables")):
        try:
            names = sorted(os.listdir(src_dir))
        except OSError:
            continue
        for name in names:
            src = os.path.join(src_dir, name)
            if (name.startswith(".") or not os.path.isfile(src)
                    or not name.lower().endswith(TABLE_EXTS)):
                continue
            shutil.copy2(src, os.path.join(TABLE_DIR, name))
            n += 1
    return n, (f"Imported {n} table{'s' if n != 1 else ''}" if n
               else "No .dbc/.n2k/… tables on stick")


def _export_tables():
    """Background worker: push /opt/kilodash/tables onto the stick."""
    dest = os.path.join(MOUNT, DEST_SUB, "tables")
    os.makedirs(dest, exist_ok=True)
    n = 0
    for name in sorted(os.listdir(TABLE_DIR)) if os.path.isdir(TABLE_DIR) \
            else []:
        src = os.path.join(TABLE_DIR, name)
        if name.startswith(".") or not os.path.isfile(src):
            continue
        shutil.copy2(src, os.path.join(dest, name))
        n += 1
    _sh("sync", timeout=60)
    return n, (f"Exported {n} table{'s' if n != 1 else ''} → USB" if n
               else "tables/ is empty — import first")


class FilesScreen(Screen):
    title = "Files"
    glyph = "files"
    tile_color_key = "accent"
    device_key = "usbstick"
    scrollable = True

    def __init__(self, app):
        super().__init__(app)
        self.tick_interval = 2.0
        self.mounted = False
        self.rw = False
        self.parts = []
        self.files = []                  # (name, size) newest first
        self.on_stick = set()            # names already on the stick
        self.task = None                 # one background job at a time
        self.status = ""
        self._btns = {}

    # ------------------------------------------------------------- mount/media
    def on_enter(self):
        self.scroll = 0
        self.parts = devices.usb_stick_partitions()
        if self.parts:
            self.mounted, self.rw, self.status = _mount(self.parts)
        else:
            self.mounted = self.rw = False
            self.status = "No USB stick found"
        self._refresh_lists()

    def on_leave(self):
        # leaving mid-copy: umount goes EBUSY → lazy detach; the worker
        # thread errors out harmlessly and the partial file stays on the
        # stick (source in captures/ is untouched either way)
        if self.mounted:
            _unmount()
            self.mounted = False

    def _eject(self):
        if self._busy():
            self.app.toast("Wait for the copy to finish")
            return
        if self.mounted:
            self.mounted = self.rw = False
            _, self.status = _unmount()
            self.app.toast(self.status)
        elif self.parts:
            self.mounted, self.rw, self.status = _mount(self.parts)
            self._refresh_lists()

    def _free_space(self):
        try:
            st = os.statvfs(MOUNT)
            return st.f_bavail * st.f_frsize
        except OSError:
            return None

    # ------------------------------------------------------------------- data
    def _refresh_lists(self):
        try:
            names = [n for n in os.listdir(CAP_DIR)
                     if not n.startswith(".") and n != ".gitkeep"
                     and os.path.isfile(os.path.join(CAP_DIR, n))]
        except OSError:
            names = []
        entries = []
        for n in names:
            st = os.stat(os.path.join(CAP_DIR, n))
            entries.append((n, st.st_size, st.st_mtime))
        entries.sort(key=lambda e: e[2], reverse=True)
        self.files = [(n, sz) for n, sz, _ in entries]
        self.on_stick = set()
        dest = os.path.join(MOUNT, DEST_SUB, "captures")
        if self.mounted:
            for n, sz in self.files:
                try:
                    if os.stat(os.path.join(dest, n)).st_size == sz:
                        self.on_stick.add(n)
                except OSError:
                    continue

    # ------------------------------------------------------------------- jobs
    def _busy(self):
        return self.task is not None and not self.task.done

    def _start(self, fn, *args, label="Working…", writes=True):
        if self._busy():
            self.app.toast("Already copying")
            return
        if not self.mounted:
            self.app.toast("Stick not mounted")
            return
        if writes and not self.rw:
            self.app.toast("Stick is read-only")
            return
        self.status = label
        self.task = system.Task(fn, *args)

    def tick(self):
        if self.task and self.task.done:
            res = self.task.result or (0, f"Failed: {self.task.error}")
            self.task = None
            _, self.status = res
            self.app.toast(self.status)
            self._refresh_lists()
            return True
        return False

    # --------------------------------------------------------------- drawing
    def content_area(self):
        return (0, LIST_TOP, self.app.w, self.app.h - LIST_TOP)

    def draw_content(self, d, th):
        w, h = self.app.w, self.app.h
        self._btns = {}
        self._draw_list(d, th, w, h)
        d.rectangle((0, HEADER_H, w, LIST_TOP), fill=th.bg)

        # stick card + Eject/Mount
        rrect(d, (14, INFO_Y, w - 14, INFO_Y + INFO_H), 10, fill=th.card)
        d.text((26, INFO_Y + 8), "USB stick", font=T.font(13), fill=th.muted)
        if self.mounted:
            src, _ = _mount_state()
            free = self._free_space()
            line = f"{os.path.basename(src or '?')} · " + \
                ("read-only" if not self.rw
                 else f"{_human(free)} free" if free is not None
                 else "mounted")
            col = th.ok if self.rw else th.warn
        else:
            line, col = "not mounted", th.warn
        d.text((26, INFO_Y + 26), line, font=T.font(17, bold=True, mono=True),
               fill=col)
        ej = Button((w - 100, INFO_Y + 8, w - 22, INFO_Y + INFO_H - 8),
                    "Eject" if self.mounted else "Mount",
                    kind="danger" if self.mounted else "primary",
                    font_size=16)
        ej.enabled = bool(self.parts) and not self._busy()
        ej.draw(d, th)
        self._btns["eject"] = ej.box if ej.enabled else None

        # copy all
        todo = [n for n, _ in self.files if n not in self.on_stick]
        label = ("Copying…" if self._busy()
                 else f"Copy all logs → USB ({len(todo)})" if todo
                 else "All logs on stick ✓")
        all_btn = Button((14, ACT_Y, w - 14, ACT_Y + ACT_H), label,
                         kind="primary", font_size=18)
        all_btn.enabled = self.rw and bool(todo) and not self._busy()
        all_btn.draw(d, th)
        self._btns["all"] = all_btn.box if all_btn.enabled else None

        # decode tables
        for key, x0, x1, label, ok in (
                ("t_in", 14, w // 2 - 4, "Tables ← USB", self.mounted),
                ("t_out", w // 2 + 4, w - 14, "Tables → USB", self.rw)):
            b = Button((x0, TBL_Y, x1, TBL_Y + TBL_H), label, font_size=15)
            b.enabled = ok and not self._busy()
            b.draw(d, th)
            self._btns[key] = b.box if b.enabled else None

        rrect(d, (14, STAT_Y, w - 14, STAT_Y + STAT_H), 8, fill=th.card)
        d.text((24, STAT_Y + 5), self.status[:38], font=T.font(13),
               fill=th.muted)

    def _draw_list(self, d, th, w, h):
        pane_h = h - LIST_TOP
        if not self.files:
            self.content_h = pane_h
            d.rectangle((0, LIST_TOP, w, h), fill=th.bg)
            d.text((24, LIST_TOP + 14), "captures/ is empty",
                   font=T.font(13), fill=th.muted)
            return
        self.content_h = max(len(self.files) * ROW_H + 4, pane_h)
        surf = Image.new("RGB", (w, self.content_h), th.bg)
        sd = ImageDraw.Draw(surf)
        f = T.font(13, mono=True)
        fs = T.font(12, mono=True)
        for i, (name, size) in enumerate(self.files):
            y = i * ROW_H
            copied = name in self.on_stick
            rrect(sd, (14, y + 2, w - 14, y + ROW_H - 2), 8,
                  fill=th.card if not copied else th.card_hi)
            sd.text((24, y + 7), name[:30], font=f,
                    fill=th.fg if not copied else th.muted)
            sub = _human(size) + ("  ✓ on stick" if copied
                                  else "  tap to copy")
            sd.text((24, y + 24), sub, font=fs,
                    fill=th.ok if copied else th.muted)
        self.paste_list(LIST_TOP, pane_h, surf)

    # ------------------------------------------------------------------ input
    def _in(self, key, x, y):
        box = self._btns.get(key)
        return box and box[0] <= x <= box[2] and box[1] <= y <= box[3]

    def handle_tap(self, x, y):
        if self._in("eject", x, y):
            self._eject()
            return True
        if self._in("all", x, y):
            todo = [n for n, _ in self.files if n not in self.on_stick]
            self._start(_copy_captures, todo,
                        label=f"Copying {len(todo)} files…")
            return True
        if self._in("t_in", x, y):
            self._start(_import_tables, label="Importing tables…",
                        writes=False)
            return True
        if self._in("t_out", x, y):
            self._start(_export_tables, label="Exporting tables…")
            return True
        if y >= LIST_TOP and self.files:
            i = int((y - LIST_TOP + self.scroll) // ROW_H)
            if 0 <= i < len(self.files):
                name, _ = self.files[i]
                if name in self.on_stick:
                    self.app.toast("Already on stick ✓")
                else:
                    self._start(_copy_captures, [name],
                                label=f"Copying {name[:24]}…")
                return True
        return False
