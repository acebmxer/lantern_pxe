#!/bin/sh
# Copies the base smb.conf and appends [capture] only when ENABLE_DIAG_CAPTURE
# is set -- an unauthenticated writable share, so it's opt-in (see
# .env.example). Unset/false leaves it out entirely: WinPE's own `net use`
# for it just fails and every capture step in the winpeshl script no-ops
# (see services/images.py's _lantern_setup_cmd/_capture_lines).
set -eu

conf=/etc/samba/smb.conf
cp /etc/samba/smb.conf.template "$conf"

if [ "${ENABLE_DIAG_CAPTURE:-false}" = "true" ]; then
    cat >>"$conf" <<'EOF'

[capture]
    path = /capture
    comment = WinPE Setup failure diagnostics
    read only = no
    guest ok = yes
    browseable = yes
EOF
fi

exec smbd --foreground --no-process-group -s "$conf"
