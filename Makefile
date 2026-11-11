.PHONY: app install run test clean release icons

app:
	./scripts/build-app.sh

# voice-annotate.app is the app's name before 0.5.
install: app
	rm -rf ~/Applications/Scribble.app ~/Applications/voice-annotate.app
	mkdir -p ~/Applications
	cp -R build/Scribble.app ~/Applications/
	@echo "Installed to ~/Applications/Scribble.app"

run: install
	open ~/Applications/Scribble.app

test:
	swift test

# Download build for GitHub Releases. Signed ad-hoc: without a Developer ID it can't be
# notarized anyway, and a personal development certificate shouldn't ship in public binaries.
VERSION := $(shell /usr/libexec/PlistBuddy -c "Print CFBundleShortVersionString" Resources/Info.plist)
release:
	SIGN_IDENTITY=- ./scripts/build-app.sh
	rm -f build/scribble-macos.zip
	ditto -c -k --keepParent build/Scribble.app build/scribble-macos.zip
	shasum -a 256 build/scribble-macos.zip

# The icon is drawn by scripts/make-icon.swift. These are the copies checked in for the
# README, the Windows and Linux app, the video and the website.
icons:
	swift scripts/make-icon.swift --png 256 docs/images/icon.png
	swift scripts/make-icon.swift --png 256 python/src/scribble/resources/icon.png
	swift scripts/make-icon.swift --png 256 video/public/icon.png
	swift scripts/make-icon.swift --png 512 site/assets/icon.png --bleed

clean:
	rm -rf .build build
