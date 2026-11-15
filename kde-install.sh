#!/bin/bash
set -e

INSTALL_DIR="$HOME/.local/share/audio-extractor"
SERVICEMENU_DIR="$HOME/.local/share/kio/servicemenus"
DESKTOP_FILE="$SERVICEMENU_DIR/audio-extractor.desktop"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

mkdir -p "$INSTALL_DIR"
mkdir -p "$SERVICEMENU_DIR"

cp "$SCRIPT_DIR/audio_extractor.py" "$INSTALL_DIR/audio_extractor.py"
chmod +x "$INSTALL_DIR/audio_extractor.py"

cat > "$DESKTOP_FILE" <<EOF
[Desktop Entry]
Type=Service
ServiceTypes=KonqPopupMenu/Plugin
MimeType=video/*;
Actions=extractAudio
X-KDE-Priority=TopLevel
Terminal=false

[Desktop Action extractAudio]
Name=Extract Audio
Icon=audio-x-generic
Exec=/usr/bin/python3 "$INSTALL_DIR/audio_extractor.py" %f
EOF

chmod +x "$DESKTOP_FILE"

echo "Installed:"
echo "  $INSTALL_DIR/audio_extractor.py"
echo "  $DESKTOP_FILE"
