import ssl

ssl._create_default_https_context = ssl._create_unverified_context

# Перехватываем контекст на самом низком уровне OpenSSL
_orig_wrap = ssl.SSLContext.wrap_socket

def _wrap(self, *args, **kwargs):
    self.check_hostname = False
    self.verify_mode = ssl.CERT_NONE
    return _orig_wrap(self, *args, **kwargs)

ssl.SSLContext.wrap_socket = _wrap
