#!/usr/bin/env python3
"""Private Telegram front-end for SideInstaller's ish/sideinstaller.py.

The bot runs the signing script in a pseudo-terminal and relays its prompts to
you in chat (Apple ID, password, 2FA code, "revoke certificate?" questions).
Your replies are typed into the script. When it finishes, the bot serves the
signed .ipa over this service's own HTTPS address and sends you an install link.

Env vars:  BOT_TOKEN, OWNER_ID           (required)
           PUBLIC_URL                    (optional; defaults to RAILWAY_PUBLIC_DOMAIN)
           DATA_DIR                      (default /data; mount a volume there to keep keys)
"""

import asyncio
import json
import os
import plistlib
import re
import secrets
import sys
import threading
import time
import zipfile

import pexpect
from aiohttp import web
from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
OWNER_ID = int(os.environ.get("OWNER_ID", "0") or 0)
PORT = int(os.environ.get("PORT", "8080"))
DATA_DIR = os.environ.get("DATA_DIR", "/data")
SCRIPT = os.environ.get("SIGNER_SCRIPT", "/app/sideinstaller.py")
STATE_FILE = os.path.join(DATA_DIR, "state.json")
OUT_IPA = os.path.join(DATA_DIR, "SideInstaller-signed.ipa")

UDID_RE = re.compile(r"^(?:[0-9A-Fa-f]{40}|[0-9A-Fa-f]{8}-[0-9A-Fa-f]{16})$")
ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
PROMPT_TAIL = re.compile(r"(\[[Yy]/[Nn]\]|:)\s*$")
SENSITIVE_RE = re.compile(r"password|code", re.I)

SESSION = {"child": None, "last_prompt": ""}


# --------------------------------------------------------------------------
# state
# --------------------------------------------------------------------------

def load_state():
    try:
        with open(STATE_FILE) as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return {}


def save_state(state):
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w") as handle:
        json.dump(state, handle)
    os.replace(tmp, STATE_FILE)


def public_url():
    url = os.environ.get("PUBLIC_URL")
    if url:
        return url.rstrip("/")
    domain = os.environ.get("RAILWAY_PUBLIC_DOMAIN")
    return "https://" + domain if domain else None


def ipa_info(path):
    """(bundle id, version, title) read from the signed .ipa's Info.plist."""
    with zipfile.ZipFile(path) as archive:
        name = next(n for n in archive.namelist()
                    if re.fullmatch(r"Payload/[^/]+\.app/Info\.plist", n))
        info = plistlib.loads(archive.read(name))
    return (info["CFBundleIdentifier"],
            info.get("CFBundleShortVersionString") or info.get("CFBundleVersion") or "1.0",
            info.get("CFBundleDisplayName") or info.get("CFBundleName") or "SideInstaller")


def make_manifest(base, token, path):
    bundle_id, version, title = ipa_info(path)
    return plistlib.dumps({"items": [{
        "assets": [{"kind": "software-package", "url": "%s/f/%s/app.ipa" % (base, token)}],
        "metadata": {"bundle-identifier": bundle_id, "bundle-version": version,
                     "kind": "software", "title": title},
    }]})


# --------------------------------------------------------------------------
# web: install page, manifest, ipa
# --------------------------------------------------------------------------

def request_base(request):
    host = request.headers.get("X-Forwarded-Host") or request.host
    return "https://" + host


def valid_token(request):
    token = request.match_info["token"]
    expected = load_state().get("token")
    return bool(expected) and secrets.compare_digest(token, expected) and os.path.exists(OUT_IPA)


async def web_root(request):
    return web.Response(text="ok")


async def web_page(request):
    if not valid_token(request):
        raise web.HTTPNotFound()
    token = request.match_info["token"]
    link = "itms-services://?action=download-manifest&url=%s/m/%s.plist" % (
        request_base(request), token)
    html = (
        '<!doctype html><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        "<title>SideInstaller</title>"
        "<body style=\"font-family:-apple-system,sans-serif;text-align:center;padding:48px 20px\">"
        "<h2>SideInstaller</h2>"
        '<p><a href="%s" style="display:inline-block;padding:16px 32px;background:#0a84ff;'
        'color:#fff;border-radius:14px;font-size:20px;text-decoration:none">Установить</a></p>'
        "<p>После установки: Настройки → Основные → VPN и управление устройством → "
        "доверять своему Apple ID. Если iOS просит, включите режим разработчика.</p>"
        "</body>" % link
    )
    return web.Response(text=html, content_type="text/html")


async def web_manifest(request):
    if not valid_token(request):
        raise web.HTTPNotFound()
    body = make_manifest(request_base(request), request.match_info["token"], OUT_IPA)
    return web.Response(body=body, content_type="application/xml")


async def web_ipa(request):
    if not valid_token(request):
        raise web.HTTPNotFound()
    return web.FileResponse(OUT_IPA, headers={"Content-Type": "application/octet-stream"})


def make_app():
    app = web.Application()
    app.router.add_get("/", web_root)
    app.router.add_get("/i/{token}", web_page)
    app.router.add_get(r"/m/{token}.plist", web_manifest)
    app.router.add_get("/f/{token}/app.ipa", web_ipa)
    return app


# --------------------------------------------------------------------------
# running the signing script in a pty and relaying it to Telegram
# --------------------------------------------------------------------------

async def send_chunks(bot, chat_id, text):
    text = text.strip("\n")
    while text:
        await bot.send_message(chat_id, text[:3900])
        text = text[3900:]


def alive():
    child = SESSION["child"]
    return child is not None and child.isalive()


def pump(child, loop, bot, chat_id):
    """Thread: read the script's output, send it to chat, announce the result."""

    def say(text):
        try:
            asyncio.run_coroutine_threadsafe(send_chunks(bot, chat_id, text), loop).result(60)
        except Exception as exc:  # network hiccup: keep the signer alive
            print("send failed:", exc, file=sys.stderr)

    buf, last_flush = "", time.time()

    def flush():
        nonlocal buf, last_flush
        text = buf.strip("\n")
        buf, last_flush = "", time.time()
        if not text.strip():
            return
        tail = text.rstrip()
        SESSION["last_prompt"] = text.splitlines()[-1] if PROMPT_TAIL.search(tail) else ""
        say(text)

    while True:
        try:
            chunk = child.read_nonblocking(4096, timeout=1.0)
        except pexpect.TIMEOUT:
            chunk = None
        except pexpect.EOF:
            break
        if chunk:
            buf += ANSI_RE.sub("", chunk).replace("\r", "")
        if buf.strip() and (chunk is None or len(buf) > 3000 or time.time() - last_flush > 5):
            flush()
    flush()

    child.close()
    status = child.exitstatus
    SESSION["last_prompt"] = ""
    if status == 0 and os.path.exists(OUT_IPA):
        state = load_state()
        state["token"] = secrets.token_urlsafe(16)
        save_state(state)
        say(link_message(state["token"]))
    else:
        say("Подпись не завершилась (код выхода %s). Прочитайте сообщения выше. "
            "Попробовать снова: /sign" % status)


def link_message(token):
    base = public_url()
    if not base:
        return ("Готово, но у сервиса нет публичного адреса. В Railway: Settings → Networking → "
                "Generate Domain, затем /link.")
    return ("Готово! Откройте ссылку в Safari (не во встроенном браузере Telegram) "
            "и нажмите «Установить»:\n%s/i/%s\n\nПодпись живёт 7 дней." % (base, token))


def start_session(loop, bot, chat_id, udid):
    if os.path.exists(OUT_IPA):
        os.remove(OUT_IPA)
    env = dict(os.environ)
    env.update({"SIDEINSTALLER_HOME": os.path.join(DATA_DIR, "si"),
                "TERM": "xterm", "PYTHONUNBUFFERED": "1"})
    child = pexpect.spawn(
        sys.executable,
        [SCRIPT, "--no-serve", "--udid", udid, "--out", OUT_IPA,
         "--port", "18888", "--tls-port", "18443"],
        encoding="utf-8", codec_errors="replace", env=env, dimensions=(40, 200))
    child.setecho(False)
    SESSION["child"], SESSION["last_prompt"] = child, ""
    threading.Thread(target=pump, args=(child, loop, bot, chat_id), daemon=True).start()


# --------------------------------------------------------------------------
# Telegram handlers
# --------------------------------------------------------------------------

public = Router()
private = Router()
private.message.filter(F.from_user.id == OWNER_ID)

HELP = (
    "/udid <UDID> — сохранить UDID устройства\n"
    "/sign — войти в Apple ID, подписать и получить ссылку на установку\n"
    "/link — прислать ссылку ещё раз\n"
    "/cancel — прервать подпись\n\n"
    "Во время /sign просто отвечайте на вопросы скрипта (почта, пароль, код 2FA). "
    "Сообщения с паролем и кодом бот удаляет."
)


@public.message(Command("id"))
async def cmd_id(message: Message):
    await message.answer("Ваш Telegram ID: %d" % message.from_user.id)


@private.message(Command("start", "help"))
async def cmd_start(message: Message):
    await message.answer(HELP)


@private.message(Command("udid"))
async def cmd_udid(message: Message, command: CommandObject):
    value = (command.args or "").strip()
    if not UDID_RE.match(value):
        await message.answer("Формат: /udid 00008110-001234567890801E (или 40 hex-символов).")
        return
    state = load_state()
    state["udid"] = value
    save_state(state)
    await message.answer("UDID сохранён. Теперь /sign.")


@private.message(Command("sign"))
async def cmd_sign(message: Message):
    if alive():
        await message.answer("Подпись уже идёт. /cancel, чтобы прервать.")
        return
    udid = load_state().get("udid")
    if not udid:
        await message.answer("Сначала: /udid ВАШ_UDID")
        return
    await message.answer("Запускаю. Отвечайте на вопросы ниже.")
    start_session(asyncio.get_running_loop(), message.bot, message.chat.id, udid)


@private.message(Command("cancel"))
async def cmd_cancel(message: Message):
    if alive():
        SESSION["child"].terminate(force=True)
        await message.answer("Прервано.")
    else:
        await message.answer("Ничего не запущено.")


@private.message(Command("link"))
async def cmd_link(message: Message):
    token = load_state().get("token")
    if token and os.path.exists(OUT_IPA):
        await message.answer(link_message(token))
    else:
        await message.answer("Подписанного приложения пока нет. /sign")


@private.message(F.text)
async def on_text(message: Message):
    if message.text.startswith("/"):
        return
    if not alive():
        await message.answer("Сейчас ничего не запущено. /sign")
        return
    if SENSITIVE_RE.search(SESSION["last_prompt"]):
        try:
            await message.delete()
        except Exception:
            pass
    SESSION["child"].sendline(message.text)


# --------------------------------------------------------------------------

async def main():
    if not BOT_TOKEN or not OWNER_ID:
        sys.exit("Set BOT_TOKEN and OWNER_ID (get your ID from @userinfobot).")
    os.makedirs(DATA_DIR, exist_ok=True)
    runner = web.AppRunner(make_app())
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", PORT).start()
    bot = Bot(BOT_TOKEN)
    dp = Dispatcher()
    dp.include_router(public)
    dp.include_router(private)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
