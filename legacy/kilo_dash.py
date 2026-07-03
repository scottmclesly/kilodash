#!/usr/bin/env python3
"""
Kali debug panel - minimal touchscreen status display.

Renders directly to the SPI ILI9486 panel (480x320) via SDL2/KMSDRM,
no X server required. Shows network interface IPv4 addresses and a
WiFi on/off toggle button.

Extend later by adding more sections to the draw loop in main().
Press ESC (USB keyboard) to quit during testing.
"""

import json
import os
import subprocess
import time

import pygame

# ---- layout / theme ----
WIDTH, HEIGHT = 480, 320
BG     = (16, 18, 22)
FG     = (220, 224, 228)
MUTED  = (120, 128, 140)
ACCENT = (60, 170, 255)
GREEN  = (40, 200, 120)
RED    = (220, 70, 70)
INK    = (10, 12, 14)

POLL_SEC = 2.0
BTN = pygame.Rect(20, 250, 440, 56)   # WiFi toggle hit-box (finger-sized)


def run(cmd):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=5).stdout.strip()
    except Exception:
        return ""


def get_interfaces():
    """List of (ifname, ipv4, operstate) for non-loopback interfaces."""
    out = run(["ip", "-j", "addr"])
    items = []
    try:
        for link in json.loads(out or "[]"):
            name = link.get("ifname", "?")
            if name == "lo":
                continue
            ip4 = next((a.get("local", "") for a in link.get("addr_info", [])
                        if a.get("family") == "inet"), "")
            items.append((name, ip4 or "--", link.get("operstate", "")))
    except Exception:
        pass
    return items


def wifi_enabled():
    return run(["nmcli", "radio", "wifi"]).lower().startswith("enabled")


def toggle_wifi(currently_on):
    subprocess.run(["nmcli", "radio", "wifi", "off" if currently_on else "on"])


def init_display():
    """Bring up only video + font so a flaky input probe can't abort init."""
    os.environ.setdefault("SDL_VIDEODRIVER", "kmsdrm")
    # Stop SDL aborting video init when it can't name the evdev touchscreen.
    os.environ.setdefault("SDL_MOUSEDRV", "dummy")
    pygame.display.init()
    pygame.font.init()
    try:
        pygame.mouse.set_visible(False)
    except pygame.error:
        pass
    return pygame.display.set_mode((WIDTH, HEIGHT))


def main():
    screen = init_display()

    mono  = pygame.font.SysFont("DejaVu Sans Mono", 22)
    small = pygame.font.SysFont("DejaVu Sans Mono", 16)
    bold  = pygame.font.SysFont("DejaVu Sans", 22, bold=True)

    ifaces = get_interfaces()
    wifi = wifi_enabled()
    last_poll = time.time()
    clock = pygame.time.Clock()

    def hit(x, y):
        nonlocal wifi
        if BTN.collidepoint(x, y):
            toggle_wifi(wifi)
            wifi = wifi_enabled()

    running = True
    while running:
        now = time.time()
        for e in pygame.event.get():
            if e.type == pygame.QUIT:
                running = False
            elif e.type == pygame.KEYDOWN and e.key == pygame.K_ESCAPE:
                running = False
            elif e.type == pygame.FINGERDOWN:
                hit(e.x * WIDTH, e.y * HEIGHT)
            elif e.type == pygame.MOUSEBUTTONDOWN:
                hit(*e.pos)

        if now - last_poll >= POLL_SEC:
            ifaces = get_interfaces()
            wifi = wifi_enabled()
            last_poll = now

        # ---- render ----
        screen.fill(BG)
        screen.blit(bold.render("DEBUG PANEL", True, ACCENT), (20, 12))
        screen.blit(small.render(time.strftime("%H:%M:%S"), True, MUTED), (370, 18))
        pygame.draw.line(screen, MUTED, (20, 48), (460, 48), 1)

        y = 62
        for name, ip4, state in ifaces:
            col = GREEN if state == "up" else MUTED
            screen.blit(mono.render(f"{name:<7}", True, FG), (20, y))
            screen.blit(mono.render(ip4, True, col), (140, y))
            y += 32
            if y > 232:
                break
        if not ifaces:
            screen.blit(mono.render("no interfaces", True, MUTED), (20, y))

        bcol = GREEN if wifi else RED
        pygame.draw.rect(screen, bcol, BTN, border_radius=10)
        label = f"WiFi {'ON' if wifi else 'OFF'}  -  tap to {'disable' if wifi else 'enable'}"
        t = bold.render(label, True, INK)
        screen.blit(t, t.get_rect(center=BTN.center))

        pygame.display.flip()
        clock.tick(15)

    pygame.quit()


if __name__ == "__main__":
    main()
