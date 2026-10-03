"""Provider registry for the Remote Setup Wizard.

Static registry of supported cloud storage providers with their
configuration field definitions. No database needed — the data
is inherently static and easy to extend.
"""

import re
from dataclasses import dataclass, field

from backend.api.schemas import AuthType, FieldType

# May not start with '-': remote names end up on rclone's command line.
REMOTE_NAME_PATTERN = re.compile(r'^[a-zA-Z0-9_][a-zA-Z0-9_-]*$')

# configparser's name for its defaults section: as a remote it would supply
# values to every other remote when OmniSync reads rclone.conf.
RESERVED_REMOTE_NAMES = frozenset({"DEFAULT"})


def validate_remote_name(name: str) -> bool:
    """Validate that a remote name contains only alphanumeric chars, hyphens, and underscores."""
    return bool(name) and bool(REMOTE_NAME_PATTERN.match(name)) and name not in RESERVED_REMOTE_NAMES


@dataclass
class ProviderField:
    name: str
    label: str
    field_type: FieldType
    required: bool
    help_text: str = ""
    # rclone stores this option obscured (`rclone obscure`), not in plain text.
    obscure: bool = False
    # FieldType.SELECT: the allowed values.
    options: list[str] = field(default_factory=list)
    # The value a new remote starts with ("" for none).
    default: str = ""

    @property
    def secret(self) -> bool:
        """Never shown again once stored: passwords, keys, tokens."""
        return self.field_type == FieldType.PASSWORD


@dataclass
class ProviderInfo:
    id: str
    display_name: str
    icon: str
    auth_type: AuthType
    default_name: str
    fields: list[ProviderField] = field(default_factory=list)
    setup_guide: str = ""


# The OAuth guides share the redirect address and where the full steps are.
_CALLBACK_URI = (
    "the web UI address you use plus /api/wizard/oauth/callback, e.g. "
    "http://127.0.0.1:3000/api/wizard/oauth/callback (the wizard shows the exact "
    "address; if OMNISYNC_OAUTH_REDIRECT_URI is set, that value)"
)
_OWN_APP = (
    "{name} needs your own OAuth app: OmniSync ships no client ID or secret. "
    "Register an app once, then enter its {fields} here. Step by step: the "
    "{name} section of docs/gem/remotes.md.\n\n"
)

PROVIDERS: dict[str, ProviderInfo] = {
    "drive": ProviderInfo(
        id="drive",
        display_name="Google Drive",
        icon="gdrive",
        auth_type=AuthType.OAUTH,
        default_name="gdrive",
        fields=[
            ProviderField(
                name="client_id",
                label="Client ID",
                field_type=FieldType.TEXT,
                required=True,
                help_text="The client ID of your Google OAuth app (Web application).",
            ),
            ProviderField(
                name="client_secret",
                label="Client Secret",
                field_type=FieldType.PASSWORD,
                required=True,
                help_text="The client secret of the same Google OAuth app.",
            ),
        ],
        setup_guide=(
            _OWN_APP.format(name="Google Drive", fields="client ID and client secret")
            + "1. In https://console.cloud.google.com create a project (or select one)\n"
            "2. APIs & Services > Library: enable the Google Drive API\n"
            "3. OAuth consent screen: user type External, publishing status Testing, and add "
            "your own Google account as a test user\n"
            "4. Credentials > Create credentials > OAuth client ID, type Web application\n"
            f"5. Authorized redirect URI: {_CALLBACK_URI}\n"
            "6. Copy the Client ID and Client Secret here\n"
            "In Testing mode Google ends the sign-in after 7 days: reconnect the remote then, "
            "or publish the app (see the guide)."
        ),
    ),
    "dropbox": ProviderInfo(
        id="dropbox",
        display_name="Dropbox",
        icon="dropbox",
        auth_type=AuthType.OAUTH,
        default_name="dropbox",
        fields=[
            ProviderField(
                name="client_id",
                label="App Key",
                field_type=FieldType.TEXT,
                required=True,
                help_text="The App key of your Dropbox app.",
            ),
            ProviderField(
                name="client_secret",
                label="App Secret",
                field_type=FieldType.PASSWORD,
                required=False,
                help_text="Optional. The sign-in uses PKCE and works with the App key alone.",
            ),
        ],
        setup_guide=(
            _OWN_APP.format(name="Dropbox", fields="App key")
            + "1. Go to https://www.dropbox.com/developers/apps and click 'Create app'\n"
            "2. Choose 'Scoped access' and 'Full Dropbox' access\n"
            "3. Permissions: tick files.metadata.write, files.content.write, "
            "files.content.read and sharing.write (account_info.read is on already), then Submit\n"
            f"4. Settings > OAuth 2 > Redirect URIs: add {_CALLBACK_URI} "
            "(Dropbox accepts plain http only for localhost)\n"
            "5. Copy the App key here; the App secret is optional"
        ),
    ),
    "onedrive": ProviderInfo(
        id="onedrive",
        display_name="OneDrive",
        icon="onedrive",
        auth_type=AuthType.OAUTH,
        default_name="onedrive",
        fields=[
            ProviderField(
                name="client_id",
                label="Client ID",
                field_type=FieldType.TEXT,
                required=True,
                help_text="The Application (client) ID of your Azure app.",
            ),
            ProviderField(
                name="client_secret",
                label="Client Secret",
                field_type=FieldType.PASSWORD,
                required=False,
                help_text="Leave blank for a public client app (recommended).",
            ),
        ],
        setup_guide=(
            _OWN_APP.format(name="OneDrive", fields="Application (client) ID")
            + "1. In https://portal.azure.com open Microsoft Entra ID > App registrations > "
            "New registration\n"
            "2. Supported account types: accounts in any organizational directory and "
            "personal Microsoft accounts\n"
            f"3. Redirect URI: platform 'Public client/native (mobile & desktop)', {_CALLBACK_URI}; "
            "plain http works only for localhost or 127.0.0.1\n"
            "4. API permissions: add Microsoft Graph delegated permissions Files.Read, "
            "Files.ReadWrite, Files.Read.All, Files.ReadWrite.All and offline_access\n"
            "5. Copy the Application (client) ID here and leave Client Secret blank"
        ),
    ),
    "s3": ProviderInfo(
        id="s3",
        display_name="Amazon S3",
        icon="s3",
        auth_type=AuthType.KEY,
        default_name="s3",
        fields=[
            ProviderField(
                name="access_key_id",
                label="Access Key ID",
                field_type=FieldType.TEXT,
                required=True,
                help_text="Find this in the AWS IAM console under Security Credentials.",
            ),
            ProviderField(
                name="secret_access_key",
                label="Secret Access Key",
                field_type=FieldType.PASSWORD,
                required=True,
                help_text="The secret key paired with your Access Key ID.",
            ),
            ProviderField(
                name="region",
                label="Region",
                field_type=FieldType.TEXT,
                required=False,
                help_text="e.g. us-east-1. Leave blank for the default region.",
            ),
            ProviderField(
                name="endpoint",
                label="Endpoint",
                field_type=FieldType.TEXT,
                required=False,
                help_text="Custom endpoint URL for S3-compatible services.",
            ),
        ],
        setup_guide=(
            "1. Sign in to the AWS Console at https://console.aws.amazon.com/iam/\n"
            "2. Go to IAM > Users > your user > Security credentials\n"
            "3. Click 'Create access key'\n"
            "4. Copy the Access Key ID and Secret Access Key here\n"
            "5. Region is optional (e.g. us-east-1). For S3-compatible services "
            "(MinIO, Wasabi, etc.), also set the Endpoint URL"
        ),
    ),
    "b2": ProviderInfo(
        id="b2",
        display_name="Backblaze B2",
        icon="b2",
        auth_type=AuthType.KEY,
        default_name="b2",
        fields=[
            ProviderField(
                name="account",
                label="Application Key ID",
                field_type=FieldType.TEXT,
                required=True,
                help_text="Found in B2 Cloud Storage > App Keys.",
            ),
            ProviderField(
                name="key",
                label="Application Key",
                field_type=FieldType.PASSWORD,
                required=True,
                help_text="The application key paired with the Key ID.",
            ),
            ProviderField(
                name="endpoint",
                label="Endpoint",
                field_type=FieldType.TEXT,
                required=False,
                help_text="Custom endpoint. Leave blank for default B2.",
            ),
        ],
        setup_guide=(
            "1. Sign in at https://secure.backblaze.com/b2_buckets.htm\n"
            "2. Go to 'App Keys' in the left sidebar\n"
            "3. Click 'Add a New Application Key'\n"
            "4. Copy the keyID (Application Key ID) and the key (Application Key) here\n"
            "Note: The Application Key is only shown once — save it somewhere safe"
        ),
    ),
    "sftp": ProviderInfo(
        id="sftp",
        display_name="SFTP",
        icon="sftp",
        auth_type=AuthType.KEY,
        default_name="sftp",
        fields=[
            ProviderField(
                name="host",
                label="Host",
                field_type=FieldType.TEXT,
                required=True,
                help_text="Hostname or IP address of the SFTP server.",
            ),
            ProviderField(
                name="user",
                label="Username",
                field_type=FieldType.TEXT,
                required=True,
                help_text="SSH username for the server.",
            ),
            ProviderField(
                name="pass",
                label="Password",
                field_type=FieldType.PASSWORD,
                obscure=True,
                required=False,
                help_text="SSH password. Leave blank if using key-based auth.",
            ),
            ProviderField(
                name="port",
                label="Port",
                field_type=FieldType.TEXT,
                required=False,
                help_text="SSH port. Defaults to 22.",
            ),
            ProviderField(
                name="key_file",
                label="Private key file",
                field_type=FieldType.TEXT,
                required=False,
                help_text="Path of a PEM private key on the machine the backend runs on "
                "(in Docker: inside the container). Leave blank to use a password.",
            ),
            ProviderField(
                name="key_file_pass",
                label="Key passphrase",
                field_type=FieldType.PASSWORD,
                obscure=True,
                required=False,
                help_text="Only if the private key file is encrypted.",
            ),
        ],
        setup_guide=(
            "Enter the connection details for your SSH/SFTP server.\n"
            "Host: the server hostname or IP (e.g. files.example.com)\n"
            "Username: your SSH login\n"
            "Password: leave blank if you use SSH key authentication\n"
            "Port: defaults to 22 if left empty\n"
            "Private key file: the path of your key on the backend's machine "
            "(mount it into the container when you use Docker)"
        ),
    ),
    "ftp": ProviderInfo(
        id="ftp",
        display_name="FTP",
        icon="ftp",
        auth_type=AuthType.KEY,
        default_name="ftp",
        fields=[
            ProviderField(
                name="host",
                label="Host",
                field_type=FieldType.TEXT,
                required=True,
                help_text="Hostname or IP address of the FTP server.",
            ),
            ProviderField(
                name="user",
                label="Username",
                field_type=FieldType.TEXT,
                required=True,
                help_text="FTP username.",
            ),
            ProviderField(
                name="pass",
                label="Password",
                field_type=FieldType.PASSWORD,
                obscure=True,
                required=False,
                help_text="FTP password.",
            ),
            ProviderField(
                name="port",
                label="Port",
                field_type=FieldType.TEXT,
                required=False,
                help_text="FTP port. Defaults to 21.",
            ),
        ],
        setup_guide=(
            "Enter the connection details for your FTP server.\n"
            "Host: the server hostname or IP\n"
            "Username and Password: your FTP login credentials\n"
            "Port: defaults to 21 if left empty"
        ),
    ),
    "webdav": ProviderInfo(
        id="webdav",
        display_name="WebDAV",
        icon="webdav",
        auth_type=AuthType.KEY,
        default_name="webdav",
        fields=[
            ProviderField(
                name="url",
                label="URL",
                field_type=FieldType.TEXT,
                required=True,
                help_text="Address of the WebDAV server, e.g. "
                "https://cloud.example.com/remote.php/dav/files/USER/",
            ),
            ProviderField(
                name="vendor",
                label="Vendor",
                field_type=FieldType.SELECT,
                required=True,
                options=["nextcloud", "owncloud", "sharepoint", "other"],
                default="other",
                help_text="The server software. Nextcloud and ownCloud add modification "
                "times and checksums.",
            ),
            ProviderField(
                name="user",
                label="Username",
                field_type=FieldType.TEXT,
                required=False,
                help_text="Your login on the server.",
            ),
            ProviderField(
                name="pass",
                label="Password",
                field_type=FieldType.PASSWORD,
                obscure=True,
                required=False,
                help_text="Password or app password (Nextcloud: Settings > Security).",
            ),
            ProviderField(
                name="bearer_token",
                label="Bearer token",
                field_type=FieldType.PASSWORD,
                required=False,
                help_text="Optional. A token instead of username and password.",
            ),
        ],
        setup_guide=(
            "Enter the address of your WebDAV server and your login.\n"
            "Nextcloud: https://<server>/remote.php/dav/files/<username>/\n"
            "ownCloud: https://<server>/remote.php/webdav/\n"
            "Use an app password if the account has two-factor authentication.\n"
            "A bearer token replaces username and password."
        ),
    ),
    "smb": ProviderInfo(
        id="smb",
        display_name="SMB / Windows share",
        icon="smb",
        auth_type=AuthType.KEY,
        default_name="smb",
        fields=[
            ProviderField(
                name="host",
                label="Host",
                field_type=FieldType.TEXT,
                required=True,
                help_text="Hostname or IP address of the SMB server (NAS or Windows PC).",
            ),
            ProviderField(
                name="port",
                label="Port",
                field_type=FieldType.TEXT,
                required=False,
                help_text="SMB port. Defaults to 445.",
            ),
            ProviderField(
                name="user",
                label="Username",
                field_type=FieldType.TEXT,
                required=False,
                help_text="Login on the server.",
            ),
            ProviderField(
                name="pass",
                label="Password",
                field_type=FieldType.PASSWORD,
                obscure=True,
                required=False,
                help_text="Password of that login.",
            ),
            ProviderField(
                name="domain",
                label="Domain",
                field_type=FieldType.TEXT,
                required=False,
                help_text="Windows domain or workgroup. Defaults to WORKGROUP.",
            ),
        ],
        setup_guide=(
            "Enter the address and login of the SMB server.\n"
            "Profiles then name the share and folder as <remote>:<share>/<folder>, "
            "e.g. nas:Documents/Work.\n"
            "Port: defaults to 445; Domain: defaults to WORKGROUP."
        ),
    ),
    "crypt": ProviderInfo(
        id="crypt",
        display_name="Encrypted (crypt)",
        icon="crypt",
        auth_type=AuthType.KEY,
        default_name="secret",
        fields=[
            ProviderField(
                name="remote",
                label="Encrypted folder",
                field_type=FieldType.REMOTE_PATH,
                required=True,
                help_text="An existing remote and the folder the encrypted files go to, "
                "e.g. gdrive:Encrypted.",
            ),
            ProviderField(
                name="password",
                label="Password",
                field_type=FieldType.PASSWORD,
                obscure=True,
                required=True,
                help_text="Encrypts the files. Without it they cannot be read: keep a copy "
                "somewhere safe. A different password cannot read files already stored.",
            ),
            ProviderField(
                name="password2",
                label="Salt password",
                field_type=FieldType.PASSWORD,
                obscure=True,
                required=False,
                help_text="Optional second password (salt). Recommended; keep it as safe "
                "as the first.",
            ),
            ProviderField(
                name="filename_encryption",
                label="File name encryption",
                field_type=FieldType.SELECT,
                required=False,
                options=["standard", "obfuscate", "off"],
                default="standard",
                help_text="standard: names are encrypted; obfuscate: only scrambled; "
                "off: names stay readable.",
            ),
            ProviderField(
                name="directory_name_encryption",
                label="Encrypt folder names",
                field_type=FieldType.SELECT,
                required=False,
                options=["true", "false"],
                default="true",
                help_text="Only applies with file name encryption standard or obfuscate.",
            ),
        ],
        setup_guide=(
            "A crypt remote encrypts file contents (and names) before they reach "
            "another remote. Pick the remote and folder that hold the encrypted "
            "files, then choose a password.\n"
            "Profiles then use the crypt remote, e.g. secret:Documents; in the "
            "wrapped folder the files are stored encrypted.\n"
            "Write the passwords down: OmniSync cannot recover them, and without "
            "them the files cannot be decrypted.\n"
            "Do not change the passwords or encryption settings once files are stored."
        ),
    ),
}


def get_providers() -> list[ProviderInfo]:
    """Return all registered providers."""
    return list(PROVIDERS.values())


def get_provider(provider_id: str) -> ProviderInfo | None:
    """Return a single provider by ID, or None if not found."""
    return PROVIDERS.get(provider_id)


def split_remote_path(value: str) -> tuple[str, str] | None:
    """``("gdrive", "Encrypted")`` for "gdrive:Encrypted"; None unless the
    part before the first ':' is a valid remote name.

    A plain local path ("/data") or an on-the-fly backend (":local:/data")
    is refused: a remote wrapping it would reach any folder on the host,
    OmniSync's data folder with its secrets included.
    """
    name, sep, path = value.partition(":")
    if not sep or not validate_remote_name(name):
        return None
    return name, path


@dataclass(frozen=True)
class RemoteCapabilities:
    """What OmniSync can do with a remote of a given rclone type."""

    # The wizard provider of that type, None if the wizard does not offer it.
    provider_id: str | None
    # Its settings can be edited in place (PUT /remotes/{name}).
    editable: bool
    # It signs in with OAuth and can be reconnected (new token only).
    reconnectable: bool


def remote_capabilities(remote_type: str) -> RemoteCapabilities:
    """The capabilities of a remote of ``remote_type`` (its rclone type)."""
    provider = PROVIDERS.get(remote_type)
    if provider is None:
        return RemoteCapabilities(provider_id=None, editable=False, reconnectable=False)
    oauth = provider.auth_type == AuthType.OAUTH
    # OAuth remotes have only their app credentials as fields, and another
    # client ID invalidates the token: they change through a reconnect.
    return RemoteCapabilities(provider_id=provider.id, editable=not oauth, reconnectable=oauth)


def validate_remote_params(
    provider: ProviderInfo,
    params: dict[str, str],
    has_token: bool,
    *,
    existing_remotes: set[str] | None = None,
    own_name: str | None = None,
) -> list[str]:
    """Return the problems with params for a remote; empty when they are valid.

    Only the provider's own fields may be written into rclone.conf. rclone has
    backend options that run commands (the SFTP backend's `ssh`, for one), so
    an unrestricted key would let a caller execute code. Values may not
    contain line breaks, which would inject extra config lines.

    A select field's value must be one of its options, a port a number, and
    a remote-path field ("<remote>:<path>") must name a remote in
    ``existing_remotes`` (when given) other than ``own_name``. Empty values
    are not checked here: they mean "not set" (on an update, "keep" or
    "remove", see PUT /remotes/{name}).
    """
    fields = {f.name: f for f in provider.fields}
    errors = [f"'{key}' is not a {provider.display_name} setting" for key in params if key not in fields]
    errors += [f"'{key}' may not contain a line break" for key, value in params.items()
               if "\n" in value or "\r" in value]
    for key, value in params.items():
        f = fields.get(key)
        if f is None or value == "" or "\n" in value or "\r" in value:
            continue
        if f.field_type == FieldType.SELECT and value not in f.options:
            errors.append(f"'{key}' must be one of: {', '.join(f.options)}")
        elif f.field_type == FieldType.REMOTE_PATH:
            parts = split_remote_path(value)
            if parts is None:
                errors.append(f"'{key}' must be an existing remote and a folder, e.g. gdrive:Encrypted")
            elif own_name is not None and parts[0] == own_name:
                errors.append(f"'{key}' may not point at the remote itself")
            elif existing_remotes is not None and parts[0] not in existing_remotes:
                errors.append(f"'{key}': there is no remote named '{parts[0]}'")
        elif key == "port" and not (value.isdigit() and 0 < int(value) < 65536):
            errors.append(f"'{key}' must be a port number (1-65535)")
    if has_token and provider.auth_type != AuthType.OAUTH:
        errors.append(f"{provider.display_name} does not use an OAuth token")
    return errors


def missing_required(provider: ProviderInfo, params: dict[str, str]) -> list[str]:
    """Problems for the required fields of a remote that ``params`` leaves empty."""
    return [f"'{f.name}' is required" for f in provider.fields
            if f.required and not params.get(f.name, "").strip()]
