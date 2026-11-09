def start_session(loop, bot, chat_id, udid):
    if os.path.exists(OUT_IPA):
        os.remove(OUT_IPA)
    env = dict(os.environ)
    env.update({
        "SIDEINSTALLER_HOME": os.path.join(DATA_DIR, "si"),
        "TERM": "xterm",
        "PYTHONUNBUFFERED": "1",
        "PYTHONHTTPSVERIFY": "0",
        "CURL_CA_BUNDLE": "",
        "SSL_CERT_DIR": "/etc/ssl/certs",
        "PYTHONPATH": "/app:/usr/local/lib/python3.12/site-packages",
    })
    child = pexpect.spawn(
        sys.executable,
        [SCRIPT, "--no-serve", "--udid", udid, "--out", OUT_IPA,
         "--port", "18888", "--tls-port", "18443"],
        encoding="utf-8", codec_errors="replace", env=env, dimensions=(40, 200))
    child.setecho(False)
    SESSION["child"], SESSION["last_prompt"] = child, ""
    threading.Thread(target=pump, args=(child, loop, bot, chat_id), daemon=True).start()
