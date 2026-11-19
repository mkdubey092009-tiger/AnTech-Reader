[app]
title = AnTech Reader
package.name = antechreader
package.domain = org.antechreader
source.dir = .
source.include_exts = py,png,jpg,kv,atlas
version = 1.0
requirements = python3,kivy==2.3.0,pyjnius,pillow,pypdf,fpdf2
icon.filename = %(source.dir)s/icon.png
presplash.filename = %(source.dir)s/icon.png
orientation = portrait
fullscreen = 0

android.permissions = READ_EXTERNAL_STORAGE,WRITE_EXTERNAL_STORAGE
android.api = 33
android.minapi = 24
android.ndk = 25b
android.archs = arm64-v8a
android.accept_sdk_license = True

[buildozer]
log_level = 2
warn_on_root = 0
