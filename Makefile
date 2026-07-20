# PianoLoTayu — packaging (Nuitka)
#
# Usage:
#   make init       # uv sync (deps + nuitka) -> .venv
#   make package    # GUI standalone (auto Linux / Windows)
#   make clean
#
# Requires: uv  https://docs.astral.sh/uv/
# Windows: prefer Git Bash's make (not GnuWin32 under Program Files (x86)).
#
# Nuitka names the dist folder after the entry module stem:
#   entry.py -> build/entry.dist/   (binary renamed via --output-filename)

SHELL := bash
.SHELLFLAGS := -eu -o pipefail -c

.PHONY: init package clean fetch-upx bundle-fluidsynth

UV          ?= uv
VENV        := .venv
SRC         := src
ENTRY       := entry.py
OUT_DIR     := build
OUT_NAME    := pianolotayu-gui
ICON        := icon.ico
EXTRACT     := scripts/extract_member.py
DIST_DIR    := $(OUT_DIR)/$(basename $(ENTRY)).dist

UV_SYNC_FLAGS := --extra dev

export PYTHONPATH := $(SRC)

ifeq ($(OS),Windows_NT)
  PYTHON  := $(VENV)/Scripts/python.exe
  OUT_BIN := $(OUT_NAME).exe
else
  PYTHON  := $(VENV)/bin/python
  OUT_BIN := $(OUT_NAME)
endif

# UPX: Windows only (Linux uses system toolchain / no fetch)
UPX_VERSION := 5.2.0
UPX_ARCHIVE := upx-$(UPX_VERSION)-win64.zip
UPX_URL     := https://github.com/upx/upx/releases/download/v$(UPX_VERSION)/$(UPX_ARCHIVE)
UPX_INNER   := upx-$(UPX_VERSION)-win64/upx.exe
UPX_BIN     := $(OUT_DIR)/upx/upx.exe
UPX_CACHE   := $(OUT_DIR)/$(UPX_ARCHIVE)
UPX_DIR     := $(OUT_DIR)/upx

FL_VERSION  := 2.5.6
FL_ZIP_ROOT := fluidsynth-v$(FL_VERSION)-win10-x64-cpp11
FL_ZIP_NAME := $(FL_ZIP_ROOT).zip
FL_URL      := https://github.com/FluidSynth/fluidsynth/releases/download/v$(FL_VERSION)/$(FL_ZIP_NAME)
FL_CACHE    := $(OUT_DIR)/$(FL_ZIP_NAME)

NUITKA_COMMON := \
	--standalone \
	--output-dir=$(OUT_DIR) \
	--enable-plugin=pyside6 \
	--include-package=imageio_ffmpeg \
	--include-data-files=$(ICON)=$(ICON) \
	--nofollow-import-to=imageio \
	--nofollow-import-to=pydub \
	--nofollow-import-to=librosa \
	--nofollow-import-to=scipy \
	--nofollow-import-to=numba \
	--nofollow-import-to=llvmlite \
	--nofollow-import-to=sklearn \
	--nofollow-import-to=tkinter \
	--nofollow-import-to=matplotlib \
	--nofollow-import-to=pytest \
	--nofollow-import-to=IPython \
	--nofollow-import-to=jupyter \
	--nofollow-import-to=setuptools \
	--nofollow-import-to=pip \
	--nofollow-import-to=unittest \
	--nofollow-import-to=PySide6.QtWebEngine \
	--nofollow-import-to=PySide6.QtWebEngineCore \
	--nofollow-import-to=PySide6.QtWebEngineWidgets \
	--nofollow-import-to=PySide6.QtQml \
	--nofollow-import-to=PySide6.QtQuick \
	--nofollow-import-to=PySide6.QtMultimedia \
	--nofollow-import-to=PySide6.Qt3DCore \
	--nofollow-import-to=PySide6.QtCharts \
	--nofollow-import-to=PySide6.QtDataVisualization \
	--nofollow-import-to=PySide6.QtBluetooth \
	--nofollow-import-to=PySide6.QtNfc \
	--nofollow-import-to=PySide6.QtPositioning \
	--nofollow-import-to=PySide6.QtSensors \
	--nofollow-import-to=PySide6.QtSql \
	--nofollow-import-to=PySide6.QtTest \
	--noinclude-dlls=libfluidsynth* \
	--noinclude-pytest-mode=nofollow \
	--noinclude-setuptools-mode=nofollow \
	--noinclude-unittest-mode=nofollow \
	--assume-yes-for-downloads \
	--remove-output

NUITKA_LINUX := \
	$(NUITKA_COMMON) \
	--static-libpython=no \
	--output-filename=$(OUT_NAME) \
	--linux-icon=$(ICON) \
	--include-qt-plugins=sensible

NUITKA_WINDOWS := \
	$(NUITKA_COMMON) \
	--enable-plugin=upx \
	--output-filename=$(OUT_NAME).exe \
	--windows-console-mode=attach \
	--windows-icon-from-ico=$(ICON) \
	--include-qt-plugins=sensible,styles,platforms,iconengines,imageformats \
	--noinclude-dlls=fluidsynth*

ifeq ($(OS),Windows_NT)
  NUITKA_FLAGS := $(NUITKA_WINDOWS)
else
  NUITKA_FLAGS := $(NUITKA_LINUX)
endif

init:
	$(UV) sync $(UV_SYNC_FLAGS)
	@test -x "$(PYTHON)" -o -f "$(PYTHON)" || { echo "error: $(PYTHON) missing after uv sync"; exit 1; }

# Windows only — Linux skips (use distro upx if you want compression)
fetch-upx: init
ifneq ($(OS),Windows_NT)
	@echo "skip fetch-upx (not Windows)"
else
	@echo "==> fetch-upx ($(UPX_ARCHIVE))"
	@mkdir -p "$(OUT_DIR)/upx"
	@if [ -f "$(UPX_BIN)" ]; then \
		echo "    cached: $(UPX_BIN)"; \
	else \
		if [ ! -f "$(UPX_CACHE)" ]; then \
			echo "    downloading $(UPX_URL)"; \
			curl -fL --retry 3 -o "$(UPX_CACHE)" "$(UPX_URL)"; \
		else \
			echo "    archive cached: $(UPX_CACHE)"; \
		fi; \
		echo "    extracting $(UPX_INNER) -> $(UPX_BIN)"; \
		$(PYTHON) $(EXTRACT) one "$(UPX_CACHE)" "$(UPX_INNER)" "$(UPX_BIN)"; \
		chmod +x "$(UPX_BIN)" 2>/dev/null || true; \
		echo "    ready: $(UPX_BIN)"; \
	fi
	@test -f "$(UPX_BIN)" || { echo "error: UPX binary missing at $(UPX_BIN)"; exit 1; }
endif

bundle-fluidsynth:
ifneq ($(OS),Windows_NT)
	@echo "skip bundle-fluidsynth (not Windows)"
else
	@echo "==> bundle-fluidsynth -> $(DIST_DIR)/"
	@test -d "$(DIST_DIR)" || { \
		echo "error: missing $(DIST_DIR)"; \
		echo "  Nuitka uses entry stem: $(basename $(ENTRY)).dist"; \
		echo "  contents of $(OUT_DIR):"; ls -la "$(OUT_DIR)" || true; \
		exit 1; \
	}
	@mkdir -p "$(OUT_DIR)"
	@if [ ! -f "$(FL_CACHE)" ]; then \
		echo "    downloading $(FL_URL)"; \
		curl -fL --retry 3 -o "$(FL_CACHE)" "$(FL_URL)"; \
	else \
		echo "    cached: $(FL_CACHE)"; \
	fi
	@echo "    extracting $(FL_ZIP_ROOT)/bin/* -> $(DIST_DIR)/"
	$(PYTHON) $(EXTRACT) prefix "$(FL_CACHE)" "$(FL_ZIP_ROOT)/bin" "$(DIST_DIR)"
	@echo "    FluidSynth DLLs next to exe"
endif

# Windows: fetch UPX first. Linux: no UPX fetch / no upx plugin.
ifeq ($(OS),Windows_NT)
package: init fetch-upx $(ENTRY) $(ICON)
else
package: init $(ENTRY) $(ICON)
endif
	@echo "==> nuitka"
	@echo "    python: $(PYTHON)"
	@echo "    dist:   $(DIST_DIR)/$(OUT_BIN)"
	@$(PYTHON) -c "import sys; print('    prefix:', sys.prefix)"
ifeq ($(OS),Windows_NT)
	@echo "    upx:    $(UPX_BIN)"
	@test -f "$(UPX_BIN)" || { echo "error: UPX missing ($(UPX_BIN))"; exit 1; }
	export PATH="$$(cd "$(UPX_DIR)" && pwd):$$PATH"; \
	UPX_WIN="$$($(PYTHON) -c "import os; print(os.path.abspath(r'$(UPX_DIR)'))")"; \
	echo "    UPX dir (bash): $$(cd "$(UPX_DIR)" && pwd)"; \
	echo "    UPX dir (win):  $$UPX_WIN"; \
	command -v upx.exe || command -v upx; \
	$(PYTHON) -m nuitka $(NUITKA_FLAGS) --upx-binary="$$UPX_WIN" $(ENTRY)
else
	$(PYTHON) -m nuitka $(NUITKA_FLAGS) $(ENTRY)
endif
	@test -d "$(DIST_DIR)" || { \
		echo "error: Nuitka did not produce $(DIST_DIR)"; \
		echo "  contents of $(OUT_DIR):"; ls -la "$(OUT_DIR)" || true; \
		exit 1; \
	}
	@test -f "$(DIST_DIR)/$(OUT_BIN)" || { \
		echo "error: missing binary $(DIST_DIR)/$(OUT_BIN)"; \
		ls -la "$(DIST_DIR)" || true; \
		exit 1; \
	}
ifeq ($(OS),Windows_NT)
	@echo "==> bundle-fluidsynth -> $(DIST_DIR)/"
	@mkdir -p "$(OUT_DIR)"
	@if [ ! -f "$(FL_CACHE)" ]; then \
		echo "    downloading $(FL_URL)"; \
		curl -fL --retry 3 -o "$(FL_CACHE)" "$(FL_URL)"; \
	else \
		echo "    cached: $(FL_CACHE)"; \
	fi
	@echo "    extracting $(FL_ZIP_ROOT)/bin/* -> $(DIST_DIR)/"
	$(PYTHON) $(EXTRACT) prefix "$(FL_CACHE)" "$(FL_ZIP_ROOT)/bin" "$(DIST_DIR)"
	@echo "    FluidSynth DLLs next to exe"
endif
	@echo "OK $(DIST_DIR)/$(OUT_BIN)" || true
ifeq ($(OS),Windows_NT)
	@echo "  FluidSynth DLLs co-located with exe" || true
endif
	@echo "  Put soundfonts/ next to the binary" || true
	@true

clean:
	rm -rf $(OUT_DIR)
