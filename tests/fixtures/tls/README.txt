Test-only TLS certificate and key for 127.0.0.1 / localhost (self-signed,
generated for tests/test_ai_speed_v34.py). They protect nothing: they let the
tests run a local TLS 1.3 server so cancelling a request is tested over real
encrypted connections, like the connection to Google Gemini.
