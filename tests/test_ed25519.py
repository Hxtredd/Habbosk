"""The bundled Ed25519 verifier: RFC 8032 test vectors, an independent
implementation (when installed), and strict rejection of malformed or
malleable signatures."""
import hashlib
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from puzzle_assistant import ed25519 as E  # noqa: E402

RFC8032 = [   # (secret seed, public key, message, signature) - RFC 8032 section 7.1
    ("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60",
     "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a", "",
     "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b"),  # noqa: E501
    ("4ccd089b28ff96da9db6c346ec114e0f5b8a319f35aba624da8cf6ed4fb8a6fb",
     "3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c", "72",
     "92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69da085ac1e43e15996e458f3613d0f11d8c387b2eaeb4302aeeb00d291612bb0c00"),  # noqa: E501
    ("c5aa8df43f9f837bedb7442f31dcb7b166d38535076f094b85ce3a2e0b4458f7",
     "fc51cd8e6218a1a38da47ed00230f0580816ed13ba3303ac5deb911548908025", "af82",
     "6291d657deec24024827e69c3abe01a30ce548a284743a445e3680d7db5ac3ac18ff9b538d16f290ae67f760984dc6594a7c15e9716ed28dc027beceea1ec40a"),  # noqa: E501
]
SEED = hashlib.sha256(b"SK test key - never trusted by a release").digest()


class Ed25519(unittest.TestCase):
    def test_rfc8032_vectors(self):
        for sk, pk, msg, sig in RFC8032:
            sk, pk, msg, sig = map(bytes.fromhex, (sk, pk, msg, sig))
            self.assertEqual(E.public_key(sk), pk)
            self.assertEqual(E.sign(sk, msg), sig)
            self.assertTrue(E.verify(pk, msg, sig))

    def test_agrees_with_an_independent_implementation(self):
        try:
            from cryptography.hazmat.primitives import serialization as S
            from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        except ImportError:
            self.skipTest("the cryptography package is not installed")
        for i in range(20):
            seed = hashlib.sha256(b"x%d" % i).digest()
            msg = hashlib.sha512(b"m%d" % i).digest()[: i * 3]
            k = Ed25519PrivateKey.from_private_bytes(seed)
            pub = k.public_key().public_bytes(S.Encoding.Raw, S.PublicFormat.Raw)
            self.assertEqual(E.public_key(seed), pub)
            self.assertEqual(E.sign(seed, msg), k.sign(msg))
            self.assertTrue(E.verify(pub, msg, k.sign(msg)))
            bad = bytearray(k.sign(msg))
            bad[i % 64] ^= 1
            self.assertFalse(E.verify(pub, msg, bytes(bad)))

    def test_any_change_breaks_the_signature(self):
        pub, msg = E.public_key(SEED), b"SK update manifest"
        sig = E.sign(SEED, msg)
        self.assertTrue(E.verify(pub, msg, sig))
        self.assertFalse(E.verify(pub, msg + b".", sig))
        for i in (0, 31, 32, 63):
            bad = bytearray(sig)
            bad[i] ^= 0x80
            self.assertFalse(E.verify(pub, msg, bytes(bad)))
        other = E.public_key(hashlib.sha256(b"another").digest())
        self.assertFalse(E.verify(other, msg, sig))

    def test_malleated_signature_is_rejected(self):
        pub, msg = E.public_key(SEED), b"m"
        sig = E.sign(SEED, msg)
        s = int.from_bytes(sig[32:], "little")
        self.assertFalse(E.verify(pub, msg, sig[:32] + int.to_bytes(s + E.L, 32, "little")))

    def test_points_off_the_curve_are_not_decoded(self):
        self.assertIsNotNone(E._decompress(E.public_key(SEED)))
        for y in (2, 7, 8, 11, 12, 13, 17):
            # independently: x^2 = (y^2-1)/(d*y^2+1) has no square root (Euler's criterion)
            x2 = (y * y - 1) * pow(E.D * y * y + 1, E.P - 2, E.P) % E.P
            self.assertEqual(pow(x2, (E.P - 1) // 2, E.P), E.P - 1)
            self.assertIsNone(E._decompress(int.to_bytes(y, 32, "little")))
            self.assertIsNone(E._decompress(int.to_bytes(y | (1 << 255), 32, "little")))
        self.assertIsNone(E._decompress(int.to_bytes(E.P, 32, "little")))        # y >= p
        self.assertIsNone(E._decompress(int.to_bytes(1 | (1 << 255), 32, "little")))  # x=0, sign 1

    def test_malformed_input_is_simply_invalid(self):
        pub, msg = E.public_key(SEED), b"m"
        sig = E.sign(SEED, msg)
        not_a_point = int.to_bytes(2, 32, "little")          # y=2 is not on the curve
        for p, s in ((pub[:31], sig), (pub, sig[:63]), (b"", b""), (None, sig), (pub, None),
                     (not_a_point, sig), (b"\xff" * 32, sig),  # y >= p
                     (pub, not_a_point + sig[32:]), (pub, b"\xff" * 32 + sig[32:])):
            self.assertFalse(E.verify(p, msg, s))


if __name__ == "__main__":
    unittest.main()
