# OmniSync User Guide

**OmniSync** is a self-hosted sync server that keeps local folders in sync
with Google Drive, OneDrive, Dropbox and the other clouds rclone supports.

1. **[Introduction](introduction.md)**: what OmniSync does, its two sync modes, and what it does not do.
2. **[Key Features](features.md)**: the features in more detail.
3. **[Getting Started](getting-started.md)**: install with docker compose, add a remote, create a profile.
   - **[Cloud Remotes](remotes.md)**: provider-specific sign-in steps, e.g. OneDrive with your own Azure app.
4. **[Configuration Guide](configuration.md)**: every profile and server setting.
5. **[Dashboard Guide](dashboard-guide.md)**: a tour of the web UI.
6. **[Differences and Conflicts](conflict-resolution.md)**: checks, per-file decisions, two-way conflicts, pausing.
7. **[How Syncing Works](how-syncing-works.md)**: the exact rules of both sync modes, pausing and every safety check.
8. **[Operations](operations.md)**: container settings, backing up and restoring OmniSync's own data, running without Docker.
9. **[FAQ & Troubleshooting](faq.md)**: common questions and problems.

The terminal UI has its own manual: [tui/USER-MANUAL.md](../../tui/USER-MANUAL.md).
Installation, the security model and the environment variables are in the
[README](../../README.md); the development setup is in
[CONTRIBUTING.md](../../CONTRIBUTING.md).
