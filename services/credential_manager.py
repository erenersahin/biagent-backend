"""
Credential Manager for BiAgent

Manages secure storage of credentials (JIRA, GitHub) in the OS credential store.
Extends the pattern from oauth_manager.py for BiAgent-specific credentials.

Consumer Tier: Credentials stored locally in OS keychain (never leave user's machine)
Organization Tier: Credentials stored encrypted in database (Vault/Secrets Manager)
"""

import json
import subprocess
import getpass
import platform
import os
import logging
from pathlib import Path
from typing import Optional, Dict, Any
from dataclasses import dataclass, asdict
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


class CredentialError(Exception):
    """Raised when credential operations fail."""
    pass


def _is_wsl() -> bool:
    """Check if running in WSL."""
    if platform.system() != "Linux":
        return False
    try:
        with open("/proc/version", "r") as f:
            version = f.read().lower()
            return "microsoft" in version or "wsl" in version
    except Exception:
        return False


def _get_platform() -> str:
    """Get the current platform type."""
    system = platform.system()
    if system == "Windows":
        return "windows"
    elif system == "Darwin":
        return "macos"
    elif _is_wsl():
        return "wsl"
    else:
        return "linux"


@dataclass
class JiraCredentials:
    """JIRA API credentials."""
    base_url: str
    email: str
    api_token: str
    project_key: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "JiraCredentials":
        return cls(
            base_url=data["base_url"],
            email=data["email"],
            api_token=data["api_token"],
            project_key=data.get("project_key"),
        )


@dataclass
class GitHubCredentials:
    """GitHub API credentials."""
    token: str
    webhook_secret: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "GitHubCredentials":
        return cls(
            token=data["token"],
            webhook_secret=data.get("webhook_secret"),
        )


class CredentialManager:
    """
    Manages BiAgent credentials in the OS credential store.

    For consumer tier: Credentials stored locally in:
    - macOS: Keychain
    - Windows: Credential Manager
    - Linux: File-based with restrictive permissions

    Credential names:
    - BiAgent-JIRA: JIRA credentials (base_url, email, api_token)
    - BiAgent-GitHub: GitHub PAT and webhook secret
    """

    JIRA_CREDENTIAL_NAME = "BiAgent-JIRA"
    GITHUB_CREDENTIAL_NAME = "BiAgent-GitHub"
    CREDENTIALS_DIR = Path.home() / ".config" / "biagent"
    CREDENTIALS_FILE = "credentials.json"

    def __init__(self):
        self._platform = _get_platform()
        self._ensure_credentials_dir()

    def _ensure_credentials_dir(self):
        """Ensure the credentials directory exists with proper permissions."""
        self.CREDENTIALS_DIR.mkdir(parents=True, exist_ok=True)
        # Set restrictive permissions on Linux/macOS
        if self._platform != "windows":
            try:
                os.chmod(self.CREDENTIALS_DIR, 0o700)
            except Exception:
                pass

    # ==================== JIRA Credentials ====================

    def get_jira_credentials(self) -> Optional[JiraCredentials]:
        """
        Get JIRA credentials from the credential store.

        Returns:
            JiraCredentials if found, None otherwise
        """
        try:
            if self._platform == "macos":
                return self._get_jira_from_keychain()
            elif self._platform == "windows":
                return self._get_jira_from_credential_manager()
            else:
                return self._get_jira_from_file()
        except CredentialError as e:
            logger.warning(f"Failed to get JIRA credentials: {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error getting JIRA credentials: {e}")
            return None

    def set_jira_credentials(self, credentials: JiraCredentials) -> bool:
        """
        Store JIRA credentials in the credential store.

        Args:
            credentials: JiraCredentials to store

        Returns:
            True if successful, False otherwise
        """
        try:
            if self._platform == "macos":
                return self._set_jira_to_keychain(credentials)
            elif self._platform == "windows":
                return self._set_jira_to_credential_manager(credentials)
            else:
                return self._set_jira_to_file(credentials)
        except Exception as e:
            logger.error(f"Failed to set JIRA credentials: {e}")
            return False

    def delete_jira_credentials(self) -> bool:
        """Delete JIRA credentials from the credential store."""
        try:
            if self._platform == "macos":
                return self._delete_from_keychain(self.JIRA_CREDENTIAL_NAME)
            elif self._platform == "windows":
                return self._delete_from_credential_manager(self.JIRA_CREDENTIAL_NAME)
            else:
                return self._delete_credential_from_file("jira")
        except Exception as e:
            logger.error(f"Failed to delete JIRA credentials: {e}")
            return False

    # ==================== GitHub Credentials ====================

    def get_github_credentials(self) -> Optional[GitHubCredentials]:
        """
        Get GitHub credentials from the credential store.

        Returns:
            GitHubCredentials if found, None otherwise
        """
        try:
            if self._platform == "macos":
                return self._get_github_from_keychain()
            elif self._platform == "windows":
                return self._get_github_from_credential_manager()
            else:
                return self._get_github_from_file()
        except CredentialError as e:
            logger.warning(f"Failed to get GitHub credentials: {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error getting GitHub credentials: {e}")
            return None

    def set_github_credentials(self, credentials: GitHubCredentials) -> bool:
        """
        Store GitHub credentials in the credential store.

        Args:
            credentials: GitHubCredentials to store

        Returns:
            True if successful, False otherwise
        """
        try:
            if self._platform == "macos":
                return self._set_github_to_keychain(credentials)
            elif self._platform == "windows":
                return self._set_github_to_credential_manager(credentials)
            else:
                return self._set_github_to_file(credentials)
        except Exception as e:
            logger.error(f"Failed to set GitHub credentials: {e}")
            return False

    def delete_github_credentials(self) -> bool:
        """Delete GitHub credentials from the credential store."""
        try:
            if self._platform == "macos":
                return self._delete_from_keychain(self.GITHUB_CREDENTIAL_NAME)
            elif self._platform == "windows":
                return self._delete_from_credential_manager(self.GITHUB_CREDENTIAL_NAME)
            else:
                return self._delete_credential_from_file("github")
        except Exception as e:
            logger.error(f"Failed to delete GitHub credentials: {e}")
            return False

    # ==================== macOS Keychain ====================

    def _get_jira_from_keychain(self) -> Optional[JiraCredentials]:
        """Get JIRA credentials from macOS Keychain."""
        data = self._get_from_keychain(self.JIRA_CREDENTIAL_NAME)
        if data:
            return JiraCredentials.from_dict(data)
        return None

    def _set_jira_to_keychain(self, credentials: JiraCredentials) -> bool:
        """Store JIRA credentials in macOS Keychain."""
        return self._set_to_keychain(self.JIRA_CREDENTIAL_NAME, credentials.to_dict())

    def _get_github_from_keychain(self) -> Optional[GitHubCredentials]:
        """Get GitHub credentials from macOS Keychain."""
        data = self._get_from_keychain(self.GITHUB_CREDENTIAL_NAME)
        if data:
            return GitHubCredentials.from_dict(data)
        return None

    def _set_github_to_keychain(self, credentials: GitHubCredentials) -> bool:
        """Store GitHub credentials in macOS Keychain."""
        return self._set_to_keychain(self.GITHUB_CREDENTIAL_NAME, credentials.to_dict())

    def _get_from_keychain(self, service_name: str) -> Optional[dict]:
        """Get credentials from macOS Keychain."""
        username = getpass.getuser()
        try:
            result = subprocess.run(
                ["security", "find-generic-password",
                 "-s", service_name,
                 "-a", username, "-w"],
                capture_output=True,
                text=True,
                timeout=10
            )
            if result.returncode == 0:
                return json.loads(result.stdout.strip())
            return None
        except subprocess.TimeoutExpired:
            raise CredentialError("Keychain access timed out")
        except json.JSONDecodeError:
            raise CredentialError("Invalid credential format in Keychain")
        except FileNotFoundError:
            raise CredentialError("'security' command not found")

    def _set_to_keychain(self, service_name: str, data: dict) -> bool:
        """Store credentials in macOS Keychain."""
        username = getpass.getuser()
        password = json.dumps(data)

        # First try to delete existing (ignore errors)
        subprocess.run(
            ["security", "delete-generic-password",
             "-s", service_name,
             "-a", username],
            capture_output=True,
            timeout=10
        )

        # Add the new credential
        result = subprocess.run(
            ["security", "add-generic-password",
             "-s", service_name,
             "-a", username,
             "-w", password],
            capture_output=True,
            text=True,
            timeout=10
        )
        return result.returncode == 0

    def _delete_from_keychain(self, service_name: str) -> bool:
        """Delete credentials from macOS Keychain."""
        username = getpass.getuser()
        result = subprocess.run(
            ["security", "delete-generic-password",
             "-s", service_name,
             "-a", username],
            capture_output=True,
            timeout=10
        )
        return result.returncode == 0

    # ==================== Windows Credential Manager ====================

    def _get_jira_from_credential_manager(self) -> Optional[JiraCredentials]:
        """Get JIRA credentials from Windows Credential Manager."""
        data = self._get_from_credential_manager(self.JIRA_CREDENTIAL_NAME)
        if data:
            return JiraCredentials.from_dict(data)
        return None

    def _set_jira_to_credential_manager(self, credentials: JiraCredentials) -> bool:
        """Store JIRA credentials in Windows Credential Manager."""
        return self._set_to_credential_manager(self.JIRA_CREDENTIAL_NAME, credentials.to_dict())

    def _get_github_from_credential_manager(self) -> Optional[GitHubCredentials]:
        """Get GitHub credentials from Windows Credential Manager."""
        data = self._get_from_credential_manager(self.GITHUB_CREDENTIAL_NAME)
        if data:
            return GitHubCredentials.from_dict(data)
        return None

    def _set_github_to_credential_manager(self, credentials: GitHubCredentials) -> bool:
        """Store GitHub credentials in Windows Credential Manager."""
        return self._set_to_credential_manager(self.GITHUB_CREDENTIAL_NAME, credentials.to_dict())

    def _get_from_credential_manager(self, target: str) -> Optional[dict]:
        """Get credentials from Windows Credential Manager using PowerShell."""
        try:
            # Use PowerShell to read credentials
            ps_script = f'''
            $cred = Get-StoredCredential -Target "{target}" -ErrorAction SilentlyContinue
            if ($cred) {{
                $cred.Password | ConvertFrom-SecureString -AsPlainText
            }}
            '''
            result = subprocess.run(
                ["powershell", "-NoProfile", "-Command", ps_script],
                capture_output=True,
                text=True,
                timeout=30
            )
            if result.returncode == 0 and result.stdout.strip():
                return json.loads(result.stdout.strip())
            return None
        except Exception as e:
            logger.warning(f"Windows Credential Manager access failed: {e}")
            # Fallback to file-based storage
            return self._get_credential_from_file(target.lower().replace("biagent-", ""))

    def _set_to_credential_manager(self, target: str, data: dict) -> bool:
        """Store credentials in Windows Credential Manager using PowerShell."""
        try:
            password = json.dumps(data)
            ps_script = f'''
            $target = "{target}"
            $password = ConvertTo-SecureString "{password}" -AsPlainText -Force
            $cred = New-Object System.Management.Automation.PSCredential("BiAgent", $password)
            New-StoredCredential -Target $target -Credentials $cred -Type Generic -Persist LocalMachine
            '''
            result = subprocess.run(
                ["powershell", "-NoProfile", "-Command", ps_script],
                capture_output=True,
                text=True,
                timeout=30
            )
            return result.returncode == 0
        except Exception as e:
            logger.warning(f"Windows Credential Manager write failed: {e}")
            # Fallback to file-based storage
            return self._set_credential_to_file(target.lower().replace("biagent-", ""), data)

    def _delete_from_credential_manager(self, target: str) -> bool:
        """Delete credentials from Windows Credential Manager."""
        try:
            ps_script = f'Remove-StoredCredential -Target "{target}" -ErrorAction SilentlyContinue'
            result = subprocess.run(
                ["powershell", "-NoProfile", "-Command", ps_script],
                capture_output=True,
                timeout=30
            )
            return True  # Consider success even if not found
        except Exception:
            return False

    # ==================== File-based Storage (Linux fallback) ====================

    def _get_jira_from_file(self) -> Optional[JiraCredentials]:
        """Get JIRA credentials from file."""
        data = self._get_credential_from_file("jira")
        if data:
            return JiraCredentials.from_dict(data)
        return None

    def _set_jira_to_file(self, credentials: JiraCredentials) -> bool:
        """Store JIRA credentials to file."""
        return self._set_credential_to_file("jira", credentials.to_dict())

    def _get_github_from_file(self) -> Optional[GitHubCredentials]:
        """Get GitHub credentials from file."""
        data = self._get_credential_from_file("github")
        if data:
            return GitHubCredentials.from_dict(data)
        return None

    def _set_github_to_file(self, credentials: GitHubCredentials) -> bool:
        """Store GitHub credentials to file."""
        return self._set_credential_to_file("github", credentials.to_dict())

    def _get_credential_from_file(self, key: str) -> Optional[dict]:
        """Get credential by key from the credentials file."""
        cred_file = self.CREDENTIALS_DIR / self.CREDENTIALS_FILE
        if not cred_file.exists():
            return None
        try:
            with open(cred_file, "r") as f:
                all_creds = json.load(f)
            return all_creds.get(key)
        except Exception as e:
            logger.warning(f"Failed to read credentials file: {e}")
            return None

    def _set_credential_to_file(self, key: str, data: dict) -> bool:
        """Store credential by key to the credentials file."""
        cred_file = self.CREDENTIALS_DIR / self.CREDENTIALS_FILE

        # Load existing credentials
        all_creds = {}
        if cred_file.exists():
            try:
                with open(cred_file, "r") as f:
                    all_creds = json.load(f)
            except Exception:
                pass

        # Update with new credential
        all_creds[key] = data

        # Write back
        try:
            with open(cred_file, "w") as f:
                json.dump(all_creds, f, indent=2)
            # Set restrictive permissions
            os.chmod(cred_file, 0o600)
            return True
        except Exception as e:
            logger.error(f"Failed to write credentials file: {e}")
            return False

    def _delete_credential_from_file(self, key: str) -> bool:
        """Delete credential by key from the credentials file."""
        cred_file = self.CREDENTIALS_DIR / self.CREDENTIALS_FILE
        if not cred_file.exists():
            return True

        try:
            with open(cred_file, "r") as f:
                all_creds = json.load(f)
            if key in all_creds:
                del all_creds[key]
                with open(cred_file, "w") as f:
                    json.dump(all_creds, f, indent=2)
            return True
        except Exception as e:
            logger.error(f"Failed to delete credential from file: {e}")
            return False

    # ==================== Utility Methods ====================

    def has_jira_credentials(self) -> bool:
        """Check if JIRA credentials are configured."""
        return self.get_jira_credentials() is not None

    def has_github_credentials(self) -> bool:
        """Check if GitHub credentials are configured."""
        return self.get_github_credentials() is not None

    def get_credential_status(self) -> dict:
        """Get status of all credentials."""
        return {
            "platform": self._platform,
            "jira_configured": self.has_jira_credentials(),
            "github_configured": self.has_github_credentials(),
        }


# Singleton instance
credential_manager = CredentialManager()


# Convenience functions
def get_jira_credentials() -> Optional[JiraCredentials]:
    """Get JIRA credentials from the credential store."""
    return credential_manager.get_jira_credentials()


def get_github_credentials() -> Optional[GitHubCredentials]:
    """Get GitHub credentials from the credential store."""
    return credential_manager.get_github_credentials()


def set_jira_credentials(base_url: str, email: str, api_token: str, project_key: Optional[str] = None) -> bool:
    """Store JIRA credentials in the credential store."""
    creds = JiraCredentials(
        base_url=base_url,
        email=email,
        api_token=api_token,
        project_key=project_key,
    )
    return credential_manager.set_jira_credentials(creds)


def set_github_credentials(token: str, webhook_secret: Optional[str] = None) -> bool:
    """Store GitHub credentials in the credential store."""
    creds = GitHubCredentials(token=token, webhook_secret=webhook_secret)
    return credential_manager.set_github_credentials(creds)


__all__ = [
    "CredentialManager",
    "CredentialError",
    "JiraCredentials",
    "GitHubCredentials",
    "credential_manager",
    "get_jira_credentials",
    "get_github_credentials",
    "set_jira_credentials",
    "set_github_credentials",
]
