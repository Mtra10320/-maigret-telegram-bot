#!/bin/sh
set -e
pip install -r requirements.txt
python - <<'PY'
from urllib.request import urlretrieve
urlretrieve(
    "https://raw.githubusercontent.com/soxoj/maigret/main/maigret/resources/data.json",
    "data.json",
)
urlretrieve(
    "https://raw.githubusercontent.com/soxoj/maigret/main/cookies.txt",
    "cookies.txt",
)
PY
