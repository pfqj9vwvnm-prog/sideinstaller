FROM python:3.12-slim-bookworm

# Debian 12 ships OpenSSL 3, which zsign needs (openssl/provider.h).
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential pkg-config libssl-dev zlib1g-dev openssl ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

# Code signer, same version the iSH installer uses. Natively this takes a minute or two.
RUN curl -fsSL https://github.com/zhlynn/zsign/archive/refs/tags/v1.1.2.tar.gz | tar xz -C /tmp \
    && cd /tmp/zsign-1.1.2/build/linux && make VERSION=1.1.2 \
    && cp /tmp/zsign-1.1.2/bin/zsign /usr/local/bin/zsign \
    && rm -rf /tmp/zsign-1.1.2

WORKDIR /app

# The official signing script, fetched at build time from the official repository.
RUN curl -fsSL -o sideinstaller.py \
    https://raw.githubusercontent.com/FrizzleM/SideInstaller/main/ish/sideinstaller.py

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY bot.py .

CMD ["python", "-u", "bot.py"]
