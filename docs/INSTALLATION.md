# Installing the source beta

Wedding Faces is currently a source-based macOS beta. Installation builds an app on your Mac; the release ZIP is not a standalone app or DMG.

## Before you start

- macOS 14 or later is required by the current dependencies. The clean-install test below was on macOS 27.0; macOS 14–26 and Intel Macs have not been independently verified.
- Install Python **3.12**. The setup command expects `python3.12` on your PATH.
- Install Apple's Command Line Tools with `xcode-select --install`, then finish the macOS installation dialog before continuing.
- Internet access is needed during setup to download Python packages and two public recognition models. The photo-processing workflow runs locally afterward.

Check prerequisites in Terminal:

```sh
python3.12 --version
xcode-select -p
swiftc --version
```

If `python3.12` is not found, install Python 3.12 before running setup. If it is installed outside PATH, you can pass its absolute path with `PYTHON_BIN` as shown below.

## Download and build

1. Download `wedding-faces-source.zip` from the release and extract it.
2. Move the entire extracted `wedding-faces` folder to its intended location **before** setup. Use a local, writable folder; avoid cloud-synced locations if you want the catalog to remain local.
3. Open Terminal in that folder and run:

```sh
bash scripts/setup.sh
open 'Wedding Faces Beta.app'
```

For a Python installation outside PATH:

```sh
PYTHON_BIN='/absolute/path/to/python3.12' bash scripts/setup.sh
```

Keep the app beside its scripts, `.venv`, `models`, and `Library` folders. Moving only the `.app` into Applications will break this beta. Python virtual environments can also contain absolute paths, so do not move the installed folder afterward.

The first build can take time while packages, models, and compiler caches are prepared. Wait for setup to finish successfully before opening the app. A locally ad-hoc signed build is not the same as a Developer ID-signed and notarized distribution.

## First run

1. Allow relevant macOS folder-access prompts for the app or its Python helper. Denying or leaving a prompt unanswered can prevent the catalog from loading.
2. Click **Choose folder** and select a photo folder on your Mac or connected drive.
3. Run **Scan 100-photo sample** first.
4. If faces were found but the sidebar looks empty, select **All** or **Small groups**. The default **People** filter shows named groups and groups with five or more photos.
5. Review results, name groups, and then scan the full collection. Recognition needs human review.
6. Export copies to a separate destination outside the scanned folder. Exports preserve original bytes and embedded metadata.

## Verified clean-install test

On 25 September 2026, the published **v0.1.0-beta.2** ZIP was downloaded into a fresh folder and checked against its published SHA-256 checksum.

Environment: Apple Silicon, macOS 27.0, Python 3.12.8, Apple Command Line Tools already installed. No existing virtual environment, model downloads, or catalog were copied into this installation.

Passed:

- The documented `bash scripts/setup.sh` command, including fresh dependency installation, model checksum verification, Swift compilation, and local code signing.
- Opening the built app with an empty catalog.
- Selecting a folder through the app and scanning one AI-generated JPG: one photo indexed, four faces detected.
- Browsing the resulting groups and opening an in-app photo preview.
- Exporting a JPG through the app, then comparing the copy byte-for-byte with the input.
- Resuming the scan without duplicate photos or faces.
- Quitting and reopening the app with the catalog intact.
- The repository's three automated source/privacy checks.

This is a smoke test on one Mac, not an assurance of universal compatibility or recognition accuracy. It did not test a new ARW-only collection, another macOS version, Intel hardware, a large-library performance run, or a notarized standalone distribution. Installing Python and Command Line Tools themselves was not part of this test; they were prerequisites already present on the machine.
