import ssl

# 1. Глобальный fallback для стандартного urllib / urllib2
ssl._create_default_https_context = ssl._create_unverified_context

# 2. Перехват wrap_socket: отключаем проверку только для клиентов (server_side=False)
_orig_wrap_socket = ssl.SSLContext.wrap_socket

def _patched_wrap_socket(self, sock, *args, **kwargs):
    if not kwargs.get("server_side", False):
        self.check_hostname = False
        self.verify_mode = ssl.CERT_NONE
    return _orig_wrap_socket(self, sock, *args, **kwargs)

ssl.SSLContext.wrap_socket = _patched_wrap_socket
