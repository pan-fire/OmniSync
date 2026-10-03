# Cloud Remotes

How the setup wizard signs in to the OAuth providers (Google Drive, Dropbox,
OneDrive), and how to register the OAuth app each of them needs. Adding a
remote in general is in
[Getting Started](getting-started.md#2-add-a-cloud-remote).

## How the sign-in works

OmniSync ships no OAuth client IDs or secrets, rclone's included. Each
OAuth provider signs in with **your own OAuth app**, which you register
once with the provider:

1. Register the app (the sections below), with the **redirect URI** the
   wizard shows above the client ID field. Through the web UI that is the
   address you open the UI with plus `/api/wizard/oauth/callback`, e.g.
   `http://127.0.0.1:3000/api/wizard/oauth/callback`; if
   `OMNISYNC_OAUTH_REDIRECT_URI` is set, its value. The terminal UI shows
   the address it uses as well. It must match exactly: scheme, host, port
   and path. An app can list several redirect URIs, e.g. one for the web
   UI and one for the terminal UI.
2. In the wizard, enter the app's client ID, and its client secret where
   the provider needs one:

   | Provider | Client ID | Client secret |
   |----------|-----------|---------------|
   | Google Drive | the OAuth client's Client ID | required |
   | Dropbox | the App key | optional (the sign-in uses PKCE) |
   | OneDrive | the Application (client) ID | leave blank (public client) |

3. Click **Start authorization**, open the link and allow access. The
   provider sends the browser back to OmniSync, which exchanges the code
   for a token on the server, and the wizard creates the remote. The app's
   client ID and secret go into the remote's rclone.conf section with the
   token, so rclone refreshes the token with the same app.

**Remotes from rclone's built-in apps.** A remote created with rclone's
built-in app (by an earlier OmniSync, by `rclone config`, or imported from
an rclone.conf) has no `client_id`. It keeps working: rclone refreshes its
token with its own app, and OmniSync leaves the refresh to rclone.
**Reconnect** on such a remote needs your own app: enter its client ID
(and secret) there, and the remote uses your app from then on. rclone is
retiring its shared Google Drive app during 2026; move Google Drive
remotes to your own app before it stops working.

## Google Drive

You need a Google account; the Google Cloud project is free.

1. Open the [Google Cloud console](https://console.cloud.google.com) and
   create a project (or select one) in the project picker.
2. **APIs & Services** > **Library**: search for **Google Drive API** and
   click **Enable**.
3. Open the **OAuth consent screen** (in newer consoles **Google Auth
   Platform**) and click **Get started**: an app name such as `OmniSync`,
   your email as the support address, audience **External**, your email as
   the contact. Create it.
4. **Audience**: leave the publishing status at **Testing** and, under
   **Test users**, add your own Google account (every account that will
   sign in must be listed).
5. **Clients** (or **Credentials** > **Create credentials** > **OAuth client
   ID**): application type **Web application**, any name. Under
   **Authorized redirect URIs** add the redirect URI the wizard shows.
   Google accepts plain `http` only for `localhost` and `127.0.0.1`, and no
   other IP addresses: behind a reverse proxy use its `https://` host name.
   Click **Create**.
6. Copy the **Client ID** and the **Client secret** right away (Google may
   show the secret only once; download the JSON if offered).
7. In OmniSync: **Remotes** > **Setup Wizard** > **Google Drive**. Enter
   both, click **Start authorization** and allow access. While the app is
   unpublished Google warns that it is unverified or in testing: continue,
   it is your own app.

**Testing mode lasts 7 days per sign-in.** Google ends the sign-ins of an
app in *Testing* after 7 days, and the remote then fails with
`invalid_grant`: **Reconnect** it. To avoid that, publish the app
(**Audience** > **Publish app**, status *In production*). For your own use
the app does not need Google's verification; the sign-in shows "Google
hasn't verified this app", where **Advanced** > **Go to ...** continues.

A *Desktop app* client works as well (its secret is required too), but a
*Web application* client is the one that lists redirect URIs.

## Dropbox

1. Open the [Dropbox App Console](https://www.dropbox.com/developers/apps)
   and click **Create app**.
2. Choose **Scoped access** and **Full Dropbox**, and name the app.
3. **Permissions** tab: tick `files.metadata.write`, `files.content.write`,
   `files.content.read` and `sharing.write` (`account_info.read` is on
   already), then **Submit**. These are the permissions rclone uses; set
   them before the first sign-in, as a change needs a new sign-in.
4. **Settings** tab, **OAuth 2**: under **Redirect URIs** add the redirect
   URI the wizard shows and click **Add**. Dropbox accepts plain `http`
   only for `localhost`: if it refuses an `http://127.0.0.1` address, open
   the web UI as `http://localhost:3000` and register
   `http://localhost:3000/api/wizard/oauth/callback`. Leave **Allow public
   clients (Implicit Grant & PKCE)** at **Allow**.
5. Copy the **App key**. The **App secret** is optional: the wizard signs
   in with PKCE, which needs no secret. If you enter it, rclone uses it too.
6. In OmniSync: **Remotes** > **Setup Wizard** > **Dropbox**. Enter the App
   key, click **Start authorization** and allow access.

The app stays in *development* status; your own account can use it
without Dropbox's review.

## OneDrive

Register an Azure app as a **public client**: it signs in with its client
ID alone, so no secret has to be kept anywhere.

You need a Microsoft Entra (Azure AD) tenant to register an app: a work or
school account, or a personal Microsoft account with an Azure account (the
free one is enough). The app can still sign in personal OneDrive accounts.

1. Sign in to the [Azure portal](https://portal.azure.com) and open
   **Microsoft Entra ID** > **App registrations** > **New registration**.
2. **Name**: anything, e.g. `OmniSync`.
3. **Supported account types**: *Accounts in any organizational directory
   (Any Microsoft Entra ID tenant - Multitenant) and personal Microsoft
   accounts*. The wizard signs in through Microsoft's `common` endpoint,
   which needs this choice. For a work account only, the multitenant
   choice without personal accounts also works.
4. **Redirect URI**: platform **Public client/native (mobile & desktop)**,
   address: the address you open the web UI at, plus
   `/api/wizard/oauth/callback`, e.g.
   `http://localhost:3000/api/wizard/oauth/callback`. It must match
   exactly (scheme, host, port and path). Microsoft accepts plain `http`
   only for `localhost` and `127.0.0.1`; behind a reverse proxy use its
   `https://` address. If `OMNISYNC_OAUTH_REDIRECT_URI` is set, register
   that value instead.
   - Do not pick **Web**: a Web app is a confidential client and needs a
     client secret.
   - Do not pick **Single-page application**: Microsoft refuses its codes
     unless they are redeemed from the browser.
5. Click **Register**. On the app's **Overview** page, copy the
   **Application (client) ID**.
6. **API permissions** > **Add a permission** > **Microsoft Graph** >
   **Delegated permissions**: add `Files.Read`, `Files.ReadWrite`,
   `Files.Read.All`, `Files.ReadWrite.All` and `offline_access` (the
   permissions rclone asks for), then **Add permissions**. If your
   organisation requires it, an administrator clicks **Grant admin
   consent**.
7. In OmniSync: **Remotes** > **Setup Wizard** > **OneDrive**. Enter the
   client ID, leave **Client Secret** blank, and click **Start
   authorization**. After you allow access, Microsoft sends the browser back
   to OmniSync and the sign-in finishes by itself.

If the sign-in fails, open the app's **Authentication** page and check that
the redirect address is listed under *Mobile and desktop applications*,
not under *Web*. Setting **Allow public client flows** (under *Advanced
settings*) to **Yes** does no harm. If your organisation requires a *Web*
app instead, create a client secret for it (**Certificates & secrets**)
and enter that as well.

An existing OneDrive remote moves to your app with **Reconnect** on its
card: enter the client ID there. rclone then refreshes the token with your
app.

**Which drive.** After the sign-in the wizard asks Microsoft Graph for the
account's default drive and stores its `drive_id` and `drive_type`
(`personal`, `business` or `documentLibrary`) in the remote, as rclone's
own `rclone config` does; without them rclone cannot use a OneDrive remote.
If Microsoft cannot report a drive (for example a work account whose
OneDrive was never opened), the wizard stops with "your OneDrive could not
be read" and saves nothing: open OneDrive once in the browser with that
account, then sign in again. Reconnect keeps a drive the remote already
has, so a SharePoint library chosen in rclone stays. To use a SharePoint
library, configure the remote with `rclone config` and use **Import
rclone.conf**.
