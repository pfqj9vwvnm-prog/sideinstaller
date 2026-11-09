FROM python:3.12-slim-bookworm

# Debian 12 ships OpenSSL 3, which zsign needs (openssl/provider.h).
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential pkg-config libssl-dev zlib1g-dev openssl ca-certificates curl \
    && update-ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Code signer, same version the iSH installer uses. Natively this takes a minute or two.
RUN curl -fsSL https://github.com/zhlynn/zsign/archive/refs/tags/v1.1.2.tar.gz | tar xz -C /tmp \
    && cd /tmp/zsign-1.1.2/build/linux && make VERSION=1.1.2 \
    && cp /tmp/zsign-1.1.2/bin/zsign /usr/local/bin/zsign \
    && rm -rf /tmp/zsign-1.1.2

WORKDIR /app

# The official signing script, fetched at build time.
# Принудительно отключаем валидацию сертификатов в ssl, urllib и http.client
RUN curl -fsSL -o sideinstaller.py \
    https://raw.githubusercontent.com/FrizzleM/SideInstaller/main/ish/sideinstaller.py \
    && python3 -c '\
p = "sideinstaller.py"\n\
s = open(p).read()\n\
patch = """import ssl, urllib.request, http.client\n\
ssl._create_default_https_context = ssl._create_unverified_context\n\
_unverified = ssl._create_unverified_context()\n\
_orig_https = http.client.HTTPSConnection.__init__\n\
def _patched_https_init(self, *args, **kwargs):\n\
    if "context" not in kwargs or kwargs["context"] is None:\n\
        kwargs["context"] = _unverified\n\
    _orig_https(self, *args, **kwargs)\n\
http.client.HTTPSConnection.__init__ = _patched_https_init\n\
"""\n\
open(p, "w").write(patch + s)\n\
'

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY bot.py .

CMD ["python", "-u", "bot.py"]
