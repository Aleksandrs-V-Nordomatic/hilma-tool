# The toolchain, pinned, so "same bytes in, same text out" covers the converters too.
#
# WHY THIS EXISTS, AND SPEED IS THE SMALLER HALF. `requirements.txt` is pinned exactly
# because a floating PyMuPDF is a floating text layer — but LibreOffice and Tesseract were
# installed by `apt-get` on every job, which fetches whatever Ubuntu is shipping that day.
# LibreOffice is what reads the Word 97 attachments buyers still send, and its version
# decides what that text looks like. So the determinism claim held for the Python half and
# quietly did not for the rest.
#
# The smaller half: installing them took ~60 s per job and, on 2026-08-07, seven minutes when
# the apt mirror was slow. A layer pull from the registry beside the runner is seconds and,
# more to the point, the same every time.
#
# Base is pinned by digest rather than by tag: `ubuntu:24.04` is a moving target, and a
# moving base defeats the whole purpose of this file.
FROM ubuntu:24.04@sha256:561618e2c15bf2397621dd04f96926663a3b5616c189cf7e38db7e82f5c538ea

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

# curl is kept for parity with the other country tools' transport, git is for
# actions/checkout, ca-certificates
# so TLS works at all. LibreOffice reads legacy Office formats; p7zip opens the .7z a buyer
# ships a building project in; tesseract with `fin` and `swe` reads the scans no decoder can.
# Both, because Finland is officially bilingual and a bilingual municipality publishes the same
# tender in each -- 276 rows in 5 174 carry no Finnish title at all.
RUN apt-get update -qq \
 && apt-get install -y -qq --no-install-recommends \
      ca-certificates curl git \
      python3 python3-pip python3-venv \
      p7zip-full \
      libreoffice-writer libreoffice-calc libreoffice-impress \
      tesseract-ocr tesseract-ocr-fin tesseract-ocr-swe tesseract-ocr-eng \
 && rm -rf /var/lib/apt/lists/*

# Installed into the system interpreter on purpose: this image IS the environment, so a
# virtualenv would add a layer of indirection with nothing on the other side of it.
COPY requirements.txt /tmp/requirements.txt
RUN python3 -m pip install --no-cache-dir --break-system-packages -r /tmp/requirements.txt \
 && rm /tmp/requirements.txt

# The browser that signs in to Tarjouspalvelu, and only that: `fi_cloudia` uses it for the door
# and downloads with plain HTTP afterwards. The headless shell, not the full browser, because
# nothing here is ever looked at. Installed into a fixed path, because a job container's HOME is
# the runner's, not this image's, and Playwright looks for its browsers under HOME by default.
RUN python3 -m playwright install --with-deps --only-shell chromium \
 && rm -rf /var/lib/apt/lists/*

# Fail the build rather than ship an image that cannot do the job. Each line here is
# something a run depends on, and finding it missing on a runner costs a draw that reached
# the portal — the scarcest thing this project has.
RUN set -e; \
    curl --version > /dev/null; \
    git --version > /dev/null; \
    soffice --version > /dev/null; \
    7z i > /dev/null; \
    tesseract --list-langs 2>&1 | grep -q '^fin$'; \
    tesseract --list-langs 2>&1 | grep -q '^swe$'; \
    python3 -c "import fitz, docx, openpyxl, pptx, py7zr; print('converters import')"; \
    python3 -c "from playwright.sync_api import sync_playwright as s; p = s().start(); p.chromium.launch().close(); p.stop(); print('browser launches')"

WORKDIR /work
