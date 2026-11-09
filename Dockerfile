FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONHTTPSVERIFY=0 \
    CURL_INSECURE=1 \
    GIT_SSL_NO_VERIFY=1

# 1. Системные пакеты и фикс цепочки Let's Encrypt в Debian 12
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential pkg-config libssl-dev zlib1g-dev openssl ca-certificates curl \
    && sed -i 's#^mozilla/DST_Root_CA_X3.crt#!mozilla/DST_Root_CA_X3.crt#' /etc/ca-certificates.conf \
    && update-ca-certificates --fresh \
    && rm -rf /var/lib/apt/lists/*

# 2. Глобальное отключение SSL-проверок для системного curl
RUN echo "insecure" > /etc/curlrc && echo "insecure" > /root/.curlrc

# 3. Сборка zsign
RUN curl -fsSL https://github.com/zhlynn/zsign/archive/refs/tags/v1.1.2.tar.gz | tar xz -C /tmp \
    && cd /tmp/zsign-1.1.2/build/linux && make VERSION=1.1.2 \
    && cp /tmp/zsign-1.1.2/bin/zsign /usr/local/bin/zsign \
    && rm -rf /tmp/zsign-1.1.2

WORKDIR /app

# Скачивание sideinstaller.py
RUN curl -fsSL -o sideinstaller.py \
    https://raw.githubusercontent.com/FrizzleM/SideInstaller/main/ish/sideinstaller.py

# Копируем sitecustomize в системные пути и текущую директорию
COPY sitecustomize.py /usr/local/lib/python3.12/sitecustomize.py
COPY sitecustomize.py /usr/local/lib/python3.12/site-packages/sitecustomize.py
COPY sitecustomize.py .

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY bot.py .

CMD ["python", "-u", "bot.py"]
