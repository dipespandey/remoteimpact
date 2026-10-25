import base64
import hashlib

from allauth.mfa.adapter import DefaultMFAAdapter
from cryptography.fernet import Fernet
from django.conf import settings


class StaffMFAAdapter(DefaultMFAAdapter):
    def _cipher(self):
        # A separate configured key allows independent signing-key rotation.
        key = settings.MFA_ENCRYPTION_KEY or base64.urlsafe_b64encode(
            hashlib.sha256((settings.SECRET_KEY + ":mfa:v1").encode()).digest())
        return Fernet(key)

    def encrypt(self, text):
        return "fernet:v1:" + self._cipher().encrypt(text.encode()).decode()

    def decrypt(self, encrypted_text):
        if not encrypted_text.startswith("fernet:v1:"):
            raise ValueError("Unencrypted authenticator secret")
        return self._cipher().decrypt(encrypted_text[len("fernet:v1:"):].encode()).decode()

    def can_delete_authenticator(self, authenticator):
        return not (settings.ADMIN_REQUIRE_MFA and authenticator.user.is_staff)
