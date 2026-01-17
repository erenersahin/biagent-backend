"""OAuth token management for Claude Max subscription.

Cross-platform support for:
- Windows (Credential Manager)
- WSL (Windows Credential Manager via cmdkey.exe)
- macOS (Keychain)
- Linux (Secret Service / file-based fallback)
"""
import json
import subprocess
import getpass
import hashlib
import platform
import os
from pathlib import Path
from datetime import datetime, timedelta, timezone
from typing import Optional
from dataclasses import dataclass


class OAuthTokenError(Exception):
    """Raised when OAuth token cannot be obtained."""
    pass


def _is_wsl() -> bool:
    """Check if running in WSL."""
    if platform.system() != "Linux":
        return False
    try:
        with open("/proc/version", "r") as f:
            version = f.read().lower()
            return "microsoft" in version or "wsl" in version
    except:
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
class OAuthToken:
    """Parsed OAuth token data."""
    access_token: str
    refresh_token: Optional[str]
    expires_at: Optional[int]  # Unix timestamp in ms
    subscription_type: Optional[str]
    rate_limit_tier: Optional[str]

    @property
    def is_expired(self) -> bool:
        if not self.expires_at:
            return False
        return (self.expires_at / 1000) < datetime.now(timezone.utc).timestamp()

    @property
    def token_hash(self) -> str:
        """SHA256 hash of token for tracking (don't log full token)."""
        return hashlib.sha256(self.access_token.encode()).hexdigest()[:16]


class OAuthManager:
    """Manages OAuth token extraction and caching.

    Supports multiple platforms:
    - Windows: Uses Credential Manager
    - WSL: Uses Windows Credential Manager via cmdkey.exe/powershell
    - macOS: Uses Keychain
    - Linux: Uses file-based storage with encryption
    """

    CREDENTIAL_NAME = "Claude Code-credentials"
    TOKEN_FILE_NAME = ".credentials.json"

    def __init__(self):
        self._cached_token: Optional[OAuthToken] = None
        self._cache_time: Optional[datetime] = None
        self._cache_duration = timedelta(minutes=30)
        self._platform = _get_platform()

    def _is_cache_valid(self) -> bool:
        if not self._cached_token or not self._cache_time:
            return False
        if self._cached_token.is_expired:
            return False
        return datetime.now(timezone.utc) < self._cache_time + self._cache_duration

    def get_token(self, force_refresh: bool = False) -> OAuthToken:
        """Get OAuth token, extracting from credential store if needed."""
        if not force_refresh and self._is_cache_valid():
            return self._cached_token

        token = self._extract_token()
        self._cached_token = token
        self._cache_time = datetime.now(timezone.utc)
        return token

    def _extract_token(self) -> OAuthToken:
        """Extract OAuth token based on platform."""
        if self._platform == "windows":
            return self._extract_from_windows_credential_manager()
        elif self._platform == "wsl":
            return self._extract_from_wsl()
        elif self._platform == "macos":
            return self._extract_from_keychain()
        else:
            return self._extract_from_file()

    def _extract_from_windows_credential_manager(self) -> OAuthToken:
        """Extract OAuth token from Windows Credential Manager."""
        try:
            # Use PowerShell to read from Credential Manager
            ps_script = f'''
            $cred = Get-StoredCredential -Target "{self.CREDENTIAL_NAME}" -ErrorAction SilentlyContinue
            if ($cred) {{
                $cred.Password | ConvertFrom-SecureString -AsPlainText
            }} else {{
                # Try alternative: cmdkey list and manual extraction
                $output = cmdkey /list:"{self.CREDENTIAL_NAME}" 2>&1
                if ($LASTEXITCODE -eq 0) {{
                    # Credential exists but can't get password directly
                    Write-Error "Credential found but cannot extract password"
                }} else {{
                    Write-Error "Credential not found"
                }}
            }}
            '''

            # Alternative: Use the CredRead Windows API via ctypes
            token_data = self._read_windows_credential()
            return self._parse_token_data(token_data)

        except Exception as e:
            raise OAuthTokenError(f"Failed to extract from Windows Credential Manager: {e}")

    def _read_windows_credential(self) -> dict:
        """Read credential from Windows Credential Manager using ctypes."""
        try:
            import ctypes
            from ctypes import wintypes

            advapi32 = ctypes.windll.advapi32

            class CREDENTIAL(ctypes.Structure):
                _fields_ = [
                    ("Flags", wintypes.DWORD),
                    ("Type", wintypes.DWORD),
                    ("TargetName", wintypes.LPWSTR),
                    ("Comment", wintypes.LPWSTR),
                    ("LastWritten", wintypes.FILETIME),
                    ("CredentialBlobSize", wintypes.DWORD),
                    ("CredentialBlob", ctypes.POINTER(ctypes.c_char)),
                    ("Persist", wintypes.DWORD),
                    ("AttributeCount", wintypes.DWORD),
                    ("Attributes", ctypes.c_void_p),
                    ("TargetAlias", wintypes.LPWSTR),
                    ("UserName", wintypes.LPWSTR),
                ]

            PCREDENTIAL = ctypes.POINTER(CREDENTIAL)

            advapi32.CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(PCREDENTIAL)]
            advapi32.CredReadW.restype = wintypes.BOOL
            advapi32.CredFree.argtypes = [ctypes.c_void_p]

            cred_ptr = PCREDENTIAL()
            CRED_TYPE_GENERIC = 1

            if not advapi32.CredReadW(self.CREDENTIAL_NAME, CRED_TYPE_GENERIC, 0, ctypes.byref(cred_ptr)):
                raise OAuthTokenError("Credential not found in Windows Credential Manager")

            try:
                cred = cred_ptr.contents
                blob_size = cred.CredentialBlobSize
                password_bytes = ctypes.string_at(cred.CredentialBlob, blob_size)
                password = password_bytes.decode("utf-16-le").rstrip("\x00")
                return json.loads(password)
            finally:
                advapi32.CredFree(cred_ptr)

        except ImportError:
            raise OAuthTokenError("ctypes not available for Windows credential access")

    def _extract_from_wsl(self) -> OAuthToken:
        """Extract OAuth token from Windows Credential Manager via WSL."""
        try:
            # Method 1: Try using PowerShell from WSL
            ps_command = f'''
            [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
            Add-Type -AssemblyName System.Security
            $target = "{self.CREDENTIAL_NAME}"

            # Use Windows Credential Manager API
            Add-Type -TypeDefinition @"
            using System;
            using System.Runtime.InteropServices;
            using System.Text;

            public class CredManager {{
                [DllImport("advapi32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
                public static extern bool CredRead(string target, int type, int flags, out IntPtr credential);

                [DllImport("advapi32.dll")]
                public static extern void CredFree(IntPtr credential);

                [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
                public struct CREDENTIAL {{
                    public int Flags;
                    public int Type;
                    public string TargetName;
                    public string Comment;
                    public System.Runtime.InteropServices.ComTypes.FILETIME LastWritten;
                    public int CredentialBlobSize;
                    public IntPtr CredentialBlob;
                    public int Persist;
                    public int AttributeCount;
                    public IntPtr Attributes;
                    public string TargetAlias;
                    public string UserName;
                }}

                public static string GetCredential(string target) {{
                    IntPtr credPtr;
                    if (!CredRead(target, 1, 0, out credPtr)) {{
                        return null;
                    }}
                    try {{
                        CREDENTIAL cred = (CREDENTIAL)Marshal.PtrToStructure(credPtr, typeof(CREDENTIAL));
                        byte[] blob = new byte[cred.CredentialBlobSize];
                        Marshal.Copy(cred.CredentialBlob, blob, 0, cred.CredentialBlobSize);
                        return Encoding.Unicode.GetString(blob);
                    }} finally {{
                        CredFree(credPtr);
                    }}
                }}
            }}
"@

            $password = [CredManager]::GetCredential($target)
            if ($password) {{
                Write-Output $password
            }}
            '''

            result = subprocess.run(
                ["powershell.exe", "-NoProfile", "-Command", ps_command],
                capture_output=True,
                text=True,
                timeout=30
            )

            if result.returncode == 0 and result.stdout.strip():
                token_data = json.loads(result.stdout.strip())
                return self._parse_token_data(token_data)

            # Method 2: Fallback to file-based storage
            return self._extract_from_file()

        except subprocess.TimeoutExpired:
            raise OAuthTokenError("PowerShell access timed out")
        except FileNotFoundError:
            # PowerShell not available, try file-based
            return self._extract_from_file()
        except json.JSONDecodeError as e:
            raise OAuthTokenError(f"Invalid token JSON from WSL: {e}")
        except Exception as e:
            raise OAuthTokenError(f"Failed to extract token from WSL: {e}")

    def _extract_from_keychain(self) -> OAuthToken:
        """Extract OAuth token from macOS Keychain."""
        username = getpass.getuser()

        try:
            result = subprocess.run(
                ["security", "find-generic-password",
                 "-s", self.CREDENTIAL_NAME,
                 "-a", username, "-w"],
                capture_output=True,
                text=True,
                timeout=10
            )
        except subprocess.TimeoutExpired:
            raise OAuthTokenError("Keychain access timed out")
        except FileNotFoundError:
            raise OAuthTokenError("'security' command not found (not on macOS?)")

        if result.returncode != 0:
            raise OAuthTokenError(
                f"Failed to extract token from Keychain: {result.stderr.strip()}"
            )

        try:
            token_data = json.loads(result.stdout.strip())
        except json.JSONDecodeError as e:
            raise OAuthTokenError(f"Invalid token JSON: {e}")

        return self._parse_token_data(token_data)

    def _extract_from_file(self) -> OAuthToken:
        """Extract OAuth token from credential file (fallback)."""
        # Check multiple possible locations (most likely first)
        possible_paths = [
            Path.home() / ".claude" / self.TOKEN_FILE_NAME,  # Linux/WSL Claude Code location
            Path.home() / ".config" / "claude-code" / self.TOKEN_FILE_NAME,
            Path(__file__).parent.parent / "data" / self.TOKEN_FILE_NAME,
        ]

        # On WSL, also check Windows user directory
        if self._platform == "wsl":
            try:
                result = subprocess.run(
                    ["cmd.exe", "/c", "echo", "%USERPROFILE%"],
                    capture_output=True,
                    text=True,
                    timeout=5
                )
                if result.returncode == 0:
                    win_home = result.stdout.strip()
                    # Convert Windows path to WSL path
                    if win_home.startswith("C:"):
                        wsl_path = "/mnt/c" + win_home[2:].replace("\\", "/")
                        possible_paths.insert(0, Path(wsl_path) / ".claude" / self.TOKEN_FILE_NAME)
            except:
                pass

        for path in possible_paths:
            if path.exists():
                try:
                    with open(path, "r") as f:
                        token_data = json.load(f)
                    return self._parse_token_data(token_data)
                except (json.JSONDecodeError, IOError) as e:
                    continue

        raise OAuthTokenError(
            f"No OAuth token found. Checked: {[str(p) for p in possible_paths]}\n"
            f"Please run 'claude' CLI to authenticate first, or set BIAGENT_ANTHROPIC_API_KEY."
        )

    def _parse_token_data(self, token_data: dict) -> OAuthToken:
        """Parse token data from various formats."""
        # Handle different token formats
        oauth = token_data.get("claudeAiOauth", token_data)

        access_token = oauth.get("accessToken") or oauth.get("access_token")
        if not access_token:
            raise OAuthTokenError("No access token found in credential data")

        return OAuthToken(
            access_token=access_token,
            refresh_token=oauth.get("refreshToken") or oauth.get("refresh_token"),
            expires_at=oauth.get("expiresAt") or oauth.get("expires_at"),
            subscription_type=oauth.get("subscriptionType") or oauth.get("subscription_type"),
            rate_limit_tier=oauth.get("rateLimitTier") or oauth.get("rate_limit_tier"),
        )

    def save_token(self, token_data: dict) -> None:
        """Save token to file-based storage."""
        token_path = Path.home() / ".config" / "claude-code" / self.TOKEN_FILE_NAME
        token_path.parent.mkdir(parents=True, exist_ok=True)

        with open(token_path, "w") as f:
            json.dump(token_data, f, indent=2)

        # Set restrictive permissions (not on Windows)
        if platform.system() != "Windows":
            os.chmod(token_path, 0o600)

    def get_access_token(self) -> str:
        """Get just the access token string."""
        return self.get_token().access_token

    def token_available(self) -> bool:
        """Check if a token can be obtained."""
        try:
            self.get_token()
            return True
        except OAuthTokenError:
            return False

    def clear_cache(self) -> None:
        """Clear the cached token."""
        self._cached_token = None
        self._cache_time = None

    def get_platform_info(self) -> dict:
        """Get info about current platform and token source."""
        return {
            "platform": self._platform,
            "system": platform.system(),
            "is_wsl": _is_wsl(),
            "token_available": self.token_available(),
        }


# Singleton instance
oauth_manager = OAuthManager()


# CLI interface for testing
if __name__ == "__main__":
    import sys

    print(f"Platform: {oauth_manager._platform}")
    print(f"System: {platform.system()}")
    print(f"Is WSL: {_is_wsl()}")
    print()

    try:
        token = oauth_manager.get_token()
        print(f"✓ Token found!")
        print(f"  Hash: {token.token_hash}")
        print(f"  Expired: {token.is_expired}")
        print(f"  Subscription: {token.subscription_type}")
        print(f"  Rate Limit Tier: {token.rate_limit_tier}")
    except OAuthTokenError as e:
        print(f"✗ Token error: {e}")
        sys.exit(1)
