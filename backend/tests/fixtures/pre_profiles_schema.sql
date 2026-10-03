-- Schema create_all produced at commit 8986490, before sync profiles existed
-- (no sync_profiles or backup tables, no profile_id columns). Generated from
-- that commit's backend/db/models.py; do not edit.

CREATE TABLE sync_jobs (
	id INTEGER NOT NULL,
	direction VARCHAR(10) NOT NULL,
	started_at DATETIME NOT NULL,
	finished_at DATETIME,
	status VARCHAR(20) NOT NULL,
	files_changed INTEGER NOT NULL,
	conflicts INTEGER NOT NULL,
	errors INTEGER NOT NULL,
	PRIMARY KEY (id)
);

CREATE TABLE remotes (
	id INTEGER NOT NULL,
	name VARCHAR(255) NOT NULL,
	type VARCHAR(50) NOT NULL,
	last_verified DATETIME,
	PRIMARY KEY (id),
	UNIQUE (name)
);

CREATE TABLE manual_flags (
	id INTEGER NOT NULL,
	file_path VARCHAR(1024) NOT NULL,
	created_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (file_path)
);

CREATE TABLE notification_log (
	id INTEGER NOT NULL,
	event_type VARCHAR(50) NOT NULL,
	severity VARCHAR(10) NOT NULL,
	title VARCHAR(200) NOT NULL,
	body VARCHAR(2000) NOT NULL,
	timestamp DATETIME NOT NULL,
	channels_delivered VARCHAR(500) NOT NULL,
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
	job_id INTEGER NOT NULL,
	file_path VARCHAR(1024) NOT NULL,
	local_modified DATETIME,
	remote_modified DATETIME,
	resolved BOOLEAN NOT NULL,
	resolution VARCHAR(20),
	PRIMARY KEY (id),
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

