#!/bin/bash
# Build Wheelhouse.app from source. Requires Xcode or the Swift toolchain.
#   ./build.sh [output-dir]      default: alongside this script
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT="${1:-$HERE}"
APP="$OUT/Wheelhouse.app"

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

xcrun swiftc -O "$HERE/native/main.swift" -o "$APP/Contents/MacOS/wheelhouse" \
  -framework AppKit -framework WebKit

cat > "$APP/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleDisplayName</key><string>Wheelhouse</string>
  <key>CFBundleExecutable</key><string>wheelhouse</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>CFBundleIdentifier</key><string>com.debruin.wheelhouse</string>
  <key>CFBundleName</key><string>Wheelhouse</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>0.1.0</string>
  <key>CFBundleVersion</key><string>1</string>
  <key>LSMinimumSystemVersion</key><string>13.0</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>NSAppTransportSecurity</key><dict>
    <key>NSAllowsLocalNetworking</key><true/>
  </dict>
</dict></plist>
PLIST

[ -f "$HERE/native/AppIcon.icns" ] && cp "$HERE/native/AppIcon.icns" "$APP/Contents/Resources/"
echo "built: $APP"
