"""
Vault Client for BiAgent

Provides credential encryption for the organization tier.
Supports multiple backends:
- Local: AES encryption with key from environment (development)
- AWS Secrets Manager: For production deployment
- HashiCorp Vault: For enterprise deployments

Consumer tier uses the CredentialManager (OS keychain) instead.
"""

import os
import json
import base64
import logging
import hashlib
from abc import ABC, abstractmethod
from typing import Optional, Dict, Any
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Try to import cryptography for local encryption
try:
    from cryptography.fernet import Fernet
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    CRYPTO_AVAILABLE = True
except ImportError:
    CRYPTO_AVAILABLE = False
    logger.warning("cryptography package not installed. Using base64 encoding only (NOT SECURE).")


@dataclass
class EncryptedCredential:
    """Encrypted credential data."""
    encrypted_data: bytes
    key_id: str
    algorithm: str = "fernet"


class VaultBackend(ABC):
    """Abstract base class for vault backends."""

    @abstractmethod
    async def encrypt(self, data: dict, org_id: str) -> EncryptedCredential:
        """Encrypt credential data for an organization."""
        pass

    @abstractmethod
    async def decrypt(self, encrypted: EncryptedCredential, org_id: str) -> dict:
        """Decrypt credential data."""
        pass

    @abstractmethod
    async def delete_key(self, key_id: str) -> bool:
        """Delete an encryption key."""
        pass


class LocalVaultBackend(VaultBackend):
    """
    Local encryption backend using Fernet (AES-128-CBC).

    Uses a master key from environment or generates one.
    Suitable for development and single-server deployments.
    """

    def __init__(self, master_key: Optional[str] = None):
        """
        Initialize with a master key.

        Args:
            master_key: Base64-encoded 32-byte key. If not provided,
                       uses BIAGENT_VAULT_KEY env var or generates one.
        """
        self._master_key = master_key or os.getenv("BIAGENT_VAULT_KEY")

        if not self._master_key:
            # Generate a key for development (should be persisted in production)
            if CRYPTO_AVAILABLE:
                self._master_key = Fernet.generate_key().decode()
            else:
                # Fallback: use a deterministic key (NOT SECURE - dev only)
                self._master_key = base64.b64encode(b"biagent-dev-key-" * 2).decode()
            logger.warning("No BIAGENT_VAULT_KEY set. Using generated key (not persistent).")

    def _derive_key(self, org_id: str) -> bytes:
        """Derive an organization-specific key from the master key."""
        if not CRYPTO_AVAILABLE:
            # Simple hash-based key derivation (NOT SECURE)
            combined = f"{self._master_key}:{org_id}".encode()
            return base64.urlsafe_b64encode(hashlib.sha256(combined).digest())

        salt = org_id.encode()
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=100000,
        )
        key = kdf.derive(self._master_key.encode())
        return base64.urlsafe_b64encode(key)

    async def encrypt(self, data: dict, org_id: str) -> EncryptedCredential:
        """Encrypt credential data for an organization."""
        json_data = json.dumps(data).encode()
        key = self._derive_key(org_id)
        key_id = hashlib.sha256(f"local:{org_id}".encode()).hexdigest()[:16]

        if CRYPTO_AVAILABLE:
            f = Fernet(key)
            encrypted = f.encrypt(json_data)
        else:
            # Base64 encoding only (NOT SECURE - dev only)
            encrypted = base64.b64encode(json_data)

        return EncryptedCredential(
            encrypted_data=encrypted,
            key_id=key_id,
            algorithm="fernet" if CRYPTO_AVAILABLE else "base64",
        )

    async def decrypt(self, encrypted: EncryptedCredential, org_id: str) -> dict:
        """Decrypt credential data."""
        key = self._derive_key(org_id)

        if encrypted.algorithm == "fernet" and CRYPTO_AVAILABLE:
            f = Fernet(key)
            decrypted = f.decrypt(encrypted.encrypted_data)
        else:
            # Base64 decoding
            decrypted = base64.b64decode(encrypted.encrypted_data)

        return json.loads(decrypted.decode())

    async def delete_key(self, key_id: str) -> bool:
        """Delete a key (no-op for local backend)."""
        return True


class AWSSecretsBackend(VaultBackend):
    """
    AWS Secrets Manager backend.

    Stores credentials as secrets with org-specific prefixes.
    Requires boto3 and AWS credentials.
    """

    def __init__(self, region: str = "us-east-1", prefix: str = "biagent"):
        self._region = region
        self._prefix = prefix
        self._client = None

    def _get_client(self):
        """Get or create the boto3 client."""
        if self._client is None:
            try:
                import boto3
                self._client = boto3.client("secretsmanager", region_name=self._region)
            except ImportError:
                raise RuntimeError("boto3 not installed. Install with: pip install boto3")
        return self._client

    def _secret_name(self, org_id: str, credential_type: str) -> str:
        """Generate secret name for an org credential."""
        return f"{self._prefix}/{org_id}/{credential_type}"

    async def encrypt(self, data: dict, org_id: str) -> EncryptedCredential:
        """Store credential in Secrets Manager."""
        client = self._get_client()
        credential_type = data.get("type", "generic")
        secret_name = self._secret_name(org_id, credential_type)

        try:
            # Try to update existing secret
            client.put_secret_value(
                SecretId=secret_name,
                SecretString=json.dumps(data),
            )
        except client.exceptions.ResourceNotFoundException:
            # Create new secret
            client.create_secret(
                Name=secret_name,
                SecretString=json.dumps(data),
            )

        return EncryptedCredential(
            encrypted_data=secret_name.encode(),
            key_id=secret_name,
            algorithm="aws-secretsmanager",
        )

    async def decrypt(self, encrypted: EncryptedCredential, org_id: str) -> dict:
        """Retrieve credential from Secrets Manager."""
        client = self._get_client()
        secret_name = encrypted.encrypted_data.decode()

        response = client.get_secret_value(SecretId=secret_name)
        return json.loads(response["SecretString"])

    async def delete_key(self, key_id: str) -> bool:
        """Delete a secret from Secrets Manager."""
        client = self._get_client()
        try:
            client.delete_secret(SecretId=key_id, ForceDeleteWithoutRecovery=True)
            return True
        except Exception as e:
            logger.error(f"Failed to delete secret {key_id}: {e}")
            return False


class VaultClient:
    """
    Main vault client for credential management.

    Automatically selects the appropriate backend based on configuration.
    """

    def __init__(self):
        self._backend: Optional[VaultBackend] = None

    @property
    def backend(self) -> VaultBackend:
        """Get or initialize the vault backend."""
        if self._backend is None:
            backend_type = os.getenv("BIAGENT_VAULT_BACKEND", "local")

            if backend_type == "aws":
                region = os.getenv("AWS_REGION", "us-east-1")
                self._backend = AWSSecretsBackend(region=region)
                logger.info("Using AWS Secrets Manager backend")
            else:
                self._backend = LocalVaultBackend()
                logger.info("Using local encryption backend")

        return self._backend

    async def encrypt_credential(
        self,
        org_id: str,
        credential_type: str,
        data: dict,
    ) -> EncryptedCredential:
        """
        Encrypt and store a credential for an organization.

        Args:
            org_id: Organization ID
            credential_type: Type of credential (jira, github, anthropic)
            data: Credential data to encrypt

        Returns:
            EncryptedCredential with encrypted data and key reference
        """
        # Add type to data for retrieval
        data_with_type = {"type": credential_type, **data}
        return await self.backend.encrypt(data_with_type, org_id)

    async def decrypt_credential(
        self,
        org_id: str,
        encrypted: EncryptedCredential,
    ) -> dict:
        """
        Decrypt a credential for an organization.

        Args:
            org_id: Organization ID
            encrypted: EncryptedCredential to decrypt

        Returns:
            Decrypted credential data
        """
        return await self.backend.decrypt(encrypted, org_id)

    async def delete_credential(self, key_id: str) -> bool:
        """Delete a credential's encryption key."""
        return await self.backend.delete_key(key_id)


# Global vault client instance
vault_client = VaultClient()


# Convenience functions
async def encrypt_org_credential(
    org_id: str,
    credential_type: str,
    data: dict,
) -> EncryptedCredential:
    """Encrypt a credential for an organization."""
    return await vault_client.encrypt_credential(org_id, credential_type, data)


async def decrypt_org_credential(
    org_id: str,
    encrypted_data: bytes,
    key_id: str,
    algorithm: str = "fernet",
) -> dict:
    """Decrypt a credential for an organization."""
    encrypted = EncryptedCredential(
        encrypted_data=encrypted_data,
        key_id=key_id,
        algorithm=algorithm,
    )
    return await vault_client.decrypt_credential(org_id, encrypted)


__all__ = [
    "VaultClient",
    "VaultBackend",
    "LocalVaultBackend",
    "AWSSecretsBackend",
    "EncryptedCredential",
    "vault_client",
    "encrypt_org_credential",
    "decrypt_org_credential",
]
