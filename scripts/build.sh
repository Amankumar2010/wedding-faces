#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p 'Wedding Faces Beta.app/Contents/MacOS' build/swift-cache
swiftc -parse-as-library App.swift -o 'Wedding Faces Beta.app/Contents/MacOS/WeddingFaces' -module-cache-path build/swift-cache
/usr/bin/python3 - <<'PY'
import plistlib
from pathlib import Path
p=Path('Wedding Faces Beta.app/Contents/Info.plist')
p.write_bytes(plistlib.dumps(dict(CFBundleExecutable='WeddingFaces',CFBundleIdentifier='local.weddingfaces.publicbeta',CFBundleName='Wedding Faces Beta',CFBundlePackageType='APPL',CFBundleShortVersionString='0.1.0',LSMinimumSystemVersion='14.0',NSHighResolutionCapable=True,NSRemovableVolumesUsageDescription='Read selected photos for local face grouping.')))
PY
codesign --force --sign - 'Wedding Faces Beta.app'
