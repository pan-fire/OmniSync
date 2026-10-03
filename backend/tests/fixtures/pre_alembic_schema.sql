-- Schema a database had after the pre-Alembic startup code (create_all +
-- ALTER list) ran at commit ff22916, the last release before migrations.
-- Generated from that commit's backend/db/models.py; do not edit.

CREATE TABLE sync_profiles (
	id INTEGER NOT NULL,
	slug VARCHAR(255) NOT NULL,
	name VARCHAR(255) NOT NULL,
	local_dir VARCHAR(1024) NOT NULL,
	remote_dir VARCHAR(1024) NOT NULL,
	debounce_seconds INTEGER NOT NULL,
	pull_interval_minutes INTEGER NOT NULL,
	rclone_filter TEXT NOT NULL,
	rclone_args TEXT NOT NULL,
	backup_dir VARCHAR(1024),
	max_retries INTEGER NOT NULL,
	enabled BOOLEAN NOT NULL,
	created_at DATETIME NOT NULL,
	updated_at DATETIME NOT NULL,
	PRIMARY KEY (id)
);

CREATE UNIQUE INDEX ix_sync_profiles_slug ON sync_profiles (slug);

CREATE TABLE remotes (
	id INTEGER NOT NULL,
	name VARCHAR(255) NOT NULL,
	type VARCHAR(50) NOT NULL,
	last_verified DATETIME,
	PRIMARY KEY (id),
	UNIQUE (name)
);

CREATE TABLE notification_log (
	id INTEGER NOT NULL,
	event_type VARCHAR(50) NOT NULL,
	severity VARCHAR(10) NOT NULL,
	title VARCHAR(200) NOT NULL,
	body VARCHAR(2000) NOT NULL,
	timestamp DATETIME NOT NULL,
	channels_delivered VARCHAR(500) NOT NULL,
	profile_slug VARCHAR(255),
	PRIMARY KEY (id)
);

CREATE TABLE push_subscriptions (
	id INTEGER NOT NULL,
	endpoint VARCHAR(2048) NOT NULL,
	p256dh_key VARCHAR(512) NOT NULL,
	auth_key VARCHAR(512) NOT NULL,
	created_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (endpoint)
);

CREATE TABLE sync_jobs (
	id INTEGER NOT NULL,
	profile_id INTEGER,
	direction VARCHAR(10) NOT NULL,
	started_at DATETIME NOT NULL,
	finished_at DATETIME,
	status VARCHAR(20) NOT NULL,
	files_changed INTEGER NOT NULL,
	conflicts INTEGER NOT NULL,
	errors INTEGER NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(profile_id) REFERENCES sync_profiles (id) ON DELETE CASCADE
);

CREATE TABLE manual_flags (
	id INTEGER NOT NULL,
	profile_id INTEGER,
	file_path VARCHAR(1024) NOT NULL,
	created_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(profile_id) REFERENCES sync_profiles (id) ON DELETE CASCADE,
	UNIQUE (file_path)
);

CREATE TABLE backup_targets (
	id INTEGER NOT NULL,
	profile_id INTEGER NOT NULL,
	name VARCHAR(255) NOT NULL,
	target_path VARCHAR(1024) NOT NULL,
	target_type VARCHAR(20) NOT NULL,
	remote_name VARCHAR(255),
	retention_days INTEGER NOT NULL,
	frequency_hours INTEGER NOT NULL,
	backup_mode VARCHAR(10) NOT NULL,
	enabled BOOLEAN NOT NULL,
	last_liveness_ok BOOLEAN,
	last_liveness_error VARCHAR(2048),
	created_at DATETIME NOT NULL,
	updated_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(profile_id) REFERENCES sync_profiles (id) ON DELETE CASCADE
);

CREATE INDEX ix_backup_targets_profile_id ON backup_targets (profile_id);

CREATE TABLE file_changes (
	id INTEGER NOT NULL,
	job_id INTEGER NOT NULL,
	file_path VARCHAR(1024) NOT NULL,
	action VARCHAR(20) NOT NULL,
	size_bytes INTEGER,
	PRIMARY KEY (id),
	FOREIGN KEY(job_id) REFERENCES sync_jobs (id)
);

CREATE TABLE conflicts (
	id INTEGER NOT NULL,
	profile_id INTEGER,
	job_id INTEGER NOT NULL,
	file_path VARCHAR(1024) NOT NULL,
	local_modified DATETIME,
	remote_modified DATETIME,
	resolved BOOLEAN NOT NULL,
	resolution VARCHAR(20),
	PRIMARY KEY (id),
	FOREIGN KEY(profile_id) REFERENCES sync_profiles (id) ON DELETE CASCADE,
	FOREIGN KEY(job_id) REFERENCES sync_jobs (id)
);

CREATE TABLE sync_errors (
	id INTEGER NOT NULL,
	job_id INTEGER NOT NULL,
	message VARCHAR(2048) NOT NULL,
	stderr_output VARCHAR(4096),
	retry_count INTEGER NOT NULL,
	created_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(job_id) REFERENCES sync_jobs (id)
);

CREATE TABLE backup_jobs (
	id INTEGER NOT NULL,
	target_id INTEGER NOT NULL,
	started_at DATETIME NOT NULL,
	finished_at DATETIME,
	status VARCHAR(20) NOT NULL,
	direction VARCHAR(10) NOT NULL,
	size_bytes INTEGER,
	snapshot_id VARCHAR(255),
	error_message VARCHAR(4096),
	PRIMARY KEY (id),
	FOREIGN KEY(target_id) REFERENCES backup_targets (id) ON DELETE CASCADE
);

CREATE INDEX ix_backup_jobs_target_id ON backup_jobs (target_id);

