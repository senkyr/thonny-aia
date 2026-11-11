#!/usr/bin/env python3
"""Build a self-contained AIA .app and DMG using upstream's redistributable runtime."""

import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
WORK = ROOT / "build/macos-aia"
DIST = ROOT / "dist"
APP = DIST / "Thonny AIA.app"
UPSTREAM = "5.0.0"
SHA256 = "cdaaef53d59580139c8c811bc5a2a9f4246e0cb9d194bce0a89f621889b29e7e"


def run(*args, **kwargs):
    subprocess.run([str(arg) for arg in args], check=True, **kwargs)


def main():
    if sys.platform != "darwin" or os.uname().machine != "arm64":
        raise SystemExit("This builder requires an Apple Silicon Mac and Xcode Command Line Tools.")
    WORK.mkdir(parents=True, exist_ok=True)
    DIST.mkdir(exist_ok=True)
    package = WORK / f"thonny-{UPSTREAM}-arm64.pkg"
    if not package.exists():
        run(
            "curl",
            "-fL",
            "--retry",
            "3",
            "-o",
            package,
            f"https://github.com/thonny/thonny/releases/download/v{UPSTREAM}/{package.name}",
        )
    if hashlib.sha256(package.read_bytes()).hexdigest() != SHA256:
        raise SystemExit(f"Checksum mismatch: delete {package} and retry.")
    expanded = WORK / "upstream"
    if expanded.exists():
        shutil.rmtree(expanded)
    run("pkgutil", "--expand-full", package, expanded)
    candidates = list(expanded.rglob("Thonny.app"))
    if len(candidates) != 1:
        raise SystemExit(f"Expected one upstream .app, found {candidates}")
    if APP.exists():
        shutil.rmtree(APP)
    shutil.copytree(candidates[0], APP, symlinks=True)
    # The original signature no longer describes this independently built fork.
    for signature in APP.rglob("_CodeSignature"):
        if signature.is_dir():
            shutil.rmtree(signature)
    contents = APP / "Contents"
    framework = contents / "Frameworks/Python.framework/Versions/Current"
    python = framework / "bin/python3.14"
    run(python, "-I", "-m", "pip", "install", "--disable-pip-version-check", "--upgrade", ROOT)
    version = (ROOT / "thonny/VERSION").read_text().strip()
    info_path = contents / "Info.plist"
    info = plistlib.loads(info_path.read_bytes())
    release_version = re.match(r"\d+\.\d+\.\d+", version).group()
    info.update(
        CFBundleName="Thonny AIA",
        CFBundleDisplayName="Thonny AIA",
        CFBundleIdentifier="cz.senkyr.thonny-aia",
        CFBundleVersion=version,
        CFBundleShortVersionString=release_version,
        CFBundleGetInfoString=f"Thonny AIA {version}",
        LSMinimumSystemVersion="11.0",
        LSArchitecturePriority=["arm64"],
    )
    info_path.write_bytes(plistlib.dumps(info))
    run(
        "clang",
        "-arch",
        "arm64",
        "-mmacosx-version-min=11.0",
        "-framework",
        "Foundation",
        ROOT / "packaging/mac/aia_launcher.m",
        "-o",
        contents / "MacOS/thonny",
    )
    resources = contents / "Resources"
    for name in ("launch.py", "thonnymain.py"):
        shutil.copy2(
            ROOT / "packaging/mac/Thonny.app.initial_template/Contents/Resources" / name,
            resources / name,
        )
    shutil.copy2(ROOT / "LICENSE.txt", resources / "LICENSE-AIA.txt")
    # Finder and Dock use the same logo as the fork's welcome screen.
    iconset = WORK / "Thonny.iconset"
    iconset.mkdir(exist_ok=True)
    for size in (16, 32, 128, 256, 512):
        for scale in (1, 2):
            suffix = "@2x" if scale == 2 else ""
            run(
                "sips",
                "-z",
                size * scale,
                size * scale,
                ROOT / "thonny/res/thonny.png",
                "--out",
                iconset / f"icon_{size}x{size}{suffix}.png",
                stdout=subprocess.DEVNULL,
            )
    run("iconutil", "-c", "icns", iconset, "-o", resources / "Thonny.icns")
    for cache in APP.rglob("__pycache__"):
        shutil.rmtree(cache)
    run(
        python,
        "-I",
        "-B",
        "-c",
        "import thonny, tkinter, anthropic, openai, google.genai, ollama; "
        "assert thonny.APP_NAME == 'Thonny AIA'; print('AIA', thonny.get_version())",
    )
    requirements = subprocess.check_output(
        [str(python), "-I", "-B", "-m", "pip", "freeze"], text=True
    )
    (resources / "bundled-requirements.txt").write_text(requirements)
    (DIST / "bundled-requirements.txt").write_text(requirements)
    (resources / "build-info.json").write_text(
        json.dumps(
            {
                "source_commit": subprocess.check_output(
                    ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
                ).strip(),
                "source_has_changes": bool(
                    subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT)
                ),
                "upstream_runtime": UPSTREAM,
                "upstream_sha256": SHA256,
                "architecture": "arm64",
                "aia_version": version,
            },
            indent=2,
        )
        + "\n"
    )
    # Sign changed native SDK extensions and nested bundles, then the enclosing app.
    run("codesign", "--force", "--deep", "--sign", "-", APP)
    run("codesign", "--verify", "--deep", "--strict", APP)
    stage = WORK / "dmg"
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir()
    shutil.copytree(APP, stage / APP.name, symlinks=True)
    (stage / "Applications").symlink_to("/Applications")
    shutil.copy2(ROOT / "packaging/mac/README-AIA.md", stage / "README.md")
    dmg = DIST / f"thonny-aia-{version}-macos-arm64.dmg"
    run(
        "hdiutil",
        "create",
        "-ov",
        "-format",
        "UDZO",
        "-volname",
        "Thonny AIA",
        "-srcfolder",
        stage,
        dmg,
    )
    shutil.rmtree(stage)  # Avoid registering a second application with Launch Services.
    (DIST / f"{dmg.name}.sha256").write_text(
        f"{hashlib.sha256(dmg.read_bytes()).hexdigest()}  {dmg.name}\n"
    )
    print(f"Built {APP}\nBuilt {dmg}")


if __name__ == "__main__":
    main()
