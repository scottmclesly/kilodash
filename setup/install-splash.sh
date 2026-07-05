#!/usr/bin/env bash
#
# Scottina boot-splash installer — Plymouth theme + quiet boot + tap-to-reveal.
#
# Replaces the console log scroll during boot with ScottinaSplash.png (centered,
# scaled to fit). Tapping the panel while the splash is up drops back to the
# live boot log; the serial console keeps full logs either way. Idempotent:
# safe to re-run (the slow initramfs rebuild only happens when the theme
# actually changed). Takes effect on the next reboot. Run as root:
#
#     sudo setup/install-splash.sh
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"
THEME_DIR=/usr/share/plymouth/themes/scottina
CMDLINE=/boot/firmware/cmdline.txt
BOOT_FLAGS="quiet splash plymouth.ignore-serial-consoles loglevel=3 vt.global_cursor_default=0"

if [ "$(id -u)" -ne 0 ]; then
  echo "Run as root:  sudo $0" >&2
  exit 1
fi
export PATH="/usr/local/bin:/usr/local/sbin:$PATH"

say() { printf '\n\033[1;36m== %s ==\033[0m\n' "$*"; }

changed=0
copy_if_changed() { # src dst mode
  if ! cmp -s "$1" "$2"; then
    install -m"$3" "$1" "$2"
    changed=1
  fi
}

# ----------------------------------------------------------- Plymouth theme ---
say "Plymouth theme"
install -d "$THEME_DIR"
copy_if_changed "$SCRIPT_DIR/splash/scottina.plymouth" "$THEME_DIR/scottina.plymouth" 644
copy_if_changed "$SCRIPT_DIR/splash/scottina.script"   "$THEME_DIR/scottina.script"   644
copy_if_changed "$REPO_DIR/ScottinaSplash.png"         "$THEME_DIR/splash.png"        644
if [ "$changed" -eq 1 ] || [ "$(plymouth-set-default-theme)" != scottina ]; then
  plymouth-set-default-theme -R scottina   # -R rebuilds the initramfs (slow)
else
  echo "already default, initramfs current"
fi

# ------------------------------------------------------------ kernel cmdline ---
say "Kernel cmdline (quiet boot)"
if grep -qw splash "$CMDLINE"; then
  echo "already quiet: $CMDLINE"
else
  cp "$CMDLINE" "$CMDLINE.pre-splash"
  sed -i "1s/\$/ $BOOT_FLAGS/" "$CMDLINE"
  echo "appended quiet-boot flags (backup: $CMDLINE.pre-splash)"
fi

# ----------------------------------------------- tap-to-reveal-logs watcher ---
# Touch probes ~10s into boot; from then until boot completes, any tap quits
# the splash and streams journalctl -b to the panel.
say "Tap-to-reveal watcher"
copy_if_changed "$SCRIPT_DIR/splash/splash-tap-watcher" /usr/local/sbin/splash-tap-watcher 755
copy_if_changed "$SCRIPT_DIR/splash/splash-tap-watcher.service" /etc/systemd/system/splash-tap-watcher.service 644
systemctl daemon-reload
systemctl enable splash-tap-watcher.service

say "Done — splash appears on next reboot"
echo "Rollback:  mv $CMDLINE.pre-splash $CMDLINE  &&  plymouth-set-default-theme -R kali"
