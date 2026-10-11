"""
The server's identity: a random server ID and a self-signed HTTPS certificate
(ECDSA P-256, valid 20 years), made on first start and kept in Sonorium's
config folder (tls/key.pem readable only by Sonorium).

The certificate's SHA-256 fingerprint is what the app pins: the Link App QR
code carries it, so the app knows it talks to this server before it sends
anything. Without the QR (sign-in with a username and password), the app and
Sonorium both show four words made from the fingerprint, to compare.
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from sonorium.auth.wordlist import security_words
from sonorium.obs import logger

SERVER_FILE = "server.json"
TLS_DIR = "tls"
CERT_YEARS = 20


class ServerIdentity:
    def __init__(self, folder: Path):
        self.folder = Path(folder)
        self.server_id = self._server_id()
        self.cert_path = self.folder / TLS_DIR / "cert.pem"
        self.key_path = self.folder / TLS_DIR / "key.pem"
        self.fingerprint: Optional[str] = None
        try:
            self._ensure_certificate()
        except Exception as e:  # no cryptography package, read-only folder, ...
            logger.warning(f"HTTPS for the app is off: the certificate could not be made ({e})")

    @property
    def words(self) -> list[str]:
        return security_words(self.fingerprint) if self.fingerprint else []

    @property
    def tls_ready(self) -> bool:
        return self.fingerprint is not None

    def _server_id(self) -> str:
        path = self.folder / SERVER_FILE
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return str(uuid.UUID(data["id"]))
        except Exception:
            pass
        server_id = str(uuid.uuid4())
        self.folder.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"id": server_id}, indent=2), encoding="utf-8")
        return server_id

    def _ensure_certificate(self) -> None:
        from cryptography import x509
        from cryptography.hazmat.primitives import serialization

        if not (self.cert_path.exists() and self.key_path.exists()):
            self._make_certificate()
        cert = x509.load_pem_x509_certificate(self.cert_path.read_bytes())
        self.fingerprint = hashlib.sha256(cert.public_bytes(serialization.Encoding.DER)).hexdigest()

    def _make_certificate(self) -> None:
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.x509.oid import NameOID

        key = ec.generate_private_key(ec.SECP256R1())
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, f"Sonorium {self.server_id[:8]}")])
        now = datetime.now(timezone.utc)
        alt = [x509.DNSName("sonorium.local"), x509.DNSName("localhost"),
               x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]
        cert = (x509.CertificateBuilder()
                .subject_name(name).issuer_name(name)
                .public_key(key.public_key())
                .serial_number(x509.random_serial_number())
                .not_valid_before(now - timedelta(days=1))
                .not_valid_after(now + timedelta(days=365 * CERT_YEARS))
                .add_extension(x509.SubjectAlternativeName(alt), critical=False)
                .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
                .sign(key, hashes.SHA256()))
        self.key_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.key_path.parent, 0o700)
        except OSError:
            pass
        key_pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                    serialization.NoEncryption())
        fd = os.open(str(self.key_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(key_pem)
        self.cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        logger.info("Made the HTTPS certificate the Sonorium app pins (valid 20 years)")
