Test-only TLS files for tests/test_ai_speed_v34.py (RealTLS13): a local TLS 1.3
server on 127.0.0.1 lets the tests check that a cancelled AI request stops over
real encrypted connections, like the connection to Google Gemini.

  ca.pem             a throw-away test CA certificate; its private key was
                     destroyed right after signing localhost.pem, so nothing
                     else can ever be signed with it
  localhost.pem      server certificate for localhost / 127.0.0.1 (CA:FALSE)
  localhost-key.pem  its private key - protects nothing

The client trusts ca.pem only inside the test (cafile=...). Never install
these files anywhere. Not part of the release ZIP (tools/publish_release.py).
