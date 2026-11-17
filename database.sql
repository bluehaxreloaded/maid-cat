-- Tables maid-cat keeps in the shared bots database (soap-cat's donors table lives here too).
-- maid-cat creates any that are missing when it starts, so this never needs running by hand.
-- Every DATETIME is UTC. Keep semicolons out of comments, the bot splits this file on them.

-- Everyone who has had a SOAP or NNID transfer, or has been restricted
CREATE TABLE IF NOT EXISTS users (
    user_id BIGINT UNSIGNED PRIMARY KEY,
    username VARCHAR(64) NOT NULL,
    display_name VARCHAR(64) NULL,
    roles VARCHAR(1024) NULL COMMENT 'their Discord role names, comma separated, as of updated_at',
    in_server TINYINT(1) NOT NULL DEFAULT 1,
    soap_count INT UNSIGNED NOT NULL DEFAULT 0,
    nnid_count INT UNSIGNED NOT NULL DEFAULT 0,
    first_transfer_at DATETIME NULL,
    last_transfer_at DATETIME NULL,
    restriction ENUM('none', 'temporary', 'permanent') NOT NULL DEFAULT 'none',
    restricted_until DATETIME NULL COMMENT 'when a temporary restriction ends',
    first_seen_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

-- Every completed SOAP and NNID transfer
CREATE TABLE IF NOT EXISTS transfers (
    id INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    user_id BIGINT UNSIGNED NOT NULL,
    kind ENUM('SOAP', 'NNID') NOT NULL,
    outcome ENUM('transfer', 'lottery') NOT NULL DEFAULT 'transfer' COMMENT 'lottery is a SOAP that needed no system transfer',
    method ENUM('automatic', 'manual') NOT NULL COMMENT 'automatic is done by soap-cat, manual by a Soaper',
    serial VARCHAR(16) NULL,
    serial_base VARCHAR(16) NULL COMMENT 'serial without the check digit, so the 8 and 9 digit forms match',
    donor VARCHAR(24) NULL COMMENT 'donors.name used for the system transfer',
    soaper_id BIGINT UNSIGNED NULL COMMENT 'the Soaper who approved, ran or finished it',
    channel_id BIGINT UNSIGNED NULL,
    completed_at DATETIME NOT NULL,
    source ENUM('live', 'import') NOT NULL DEFAULT 'live' COMMENT 'import is read back from old Discord logs',
    message_id BIGINT UNSIGNED NULL COMMENT 'the Discord message it was recorded from, so nothing is counted twice',
    UNIQUE KEY uniq_message (message_id),
    KEY idx_user (user_id),
    KEY idx_serial (serial_base),
    KEY idx_soaper (soaper_id),
    KEY idx_completed (completed_at)
) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

-- Everyone who has done SOAPs or NNID transfers as a Soaper, with their counts
CREATE TABLE IF NOT EXISTS soapers (
    user_id BIGINT UNSIGNED PRIMARY KEY,
    username VARCHAR(64) NOT NULL,
    display_name VARCHAR(64) NULL,
    is_soaper TINYINT(1) NOT NULL DEFAULT 0 COMMENT 'has the Soaper role or higher right now',
    soap_count INT UNSIGNED NOT NULL DEFAULT 0,
    nnid_count INT UNSIGNED NOT NULL DEFAULT 0,
    first_soap_at DATETIME NULL,
    last_soap_at DATETIME NULL,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

-- Every restriction, current and past. expires_at is NULL for a permanent one, ended_at is NULL while it's active.
CREATE TABLE IF NOT EXISTS restrictions (
    id INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    user_id BIGINT UNSIGNED NOT NULL,
    moderator_id BIGINT UNSIGNED NULL,
    reason VARCHAR(512) NULL,
    created_at DATETIME NOT NULL,
    expires_at DATETIME NULL,
    ended_at DATETIME NULL,
    ended_by BIGINT UNSIGNED NULL COMMENT 'NULL with ended_at set means it expired or was ended outside the bot',
    end_reason VARCHAR(64) NULL,
    source ENUM('live', 'import') NOT NULL DEFAULT 'live',
    message_id BIGINT UNSIGNED NULL,
    UNIQUE KEY uniq_message (message_id),
    KEY idx_user (user_id),
    KEY idx_active (ended_at, expires_at)
) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

-- SOAP and NNID channels: open, manual, archived (CYA) and deleted
CREATE TABLE IF NOT EXISTS channels (
    channel_id BIGINT UNSIGNED PRIMARY KEY,
    user_id BIGINT UNSIGNED NULL COMMENT 'the helpee',
    kind ENUM('SOAP', 'NNID') NOT NULL,
    name VARCHAR(100) NOT NULL,
    status ENUM('open', 'manual', 'archived', 'deleted') NOT NULL DEFAULT 'open',
    serial VARCHAR(16) NULL COMMENT 'serial the helpee entered',
    exefs_serial VARCHAR(16) NULL COMMENT 'serial inside their essential.exefs',
    essential_filename VARCHAR(255) NULL COMMENT 'name of the essential.exefs they uploaded',
    essential_uploaded_at DATETIME NULL,
    pair_code CHAR(4) NULL COMMENT 'Bluehax Transporter pair code from .nocomputer, its upload is essential_<pair_code>.exefs',
    pair_code_at DATETIME NULL,
    soaper_id BIGINT UNSIGNED NULL COMMENT 'last Soaper who approved or ran the SOAP',
    donor VARCHAR(24) NULL COMMENT 'donor used, written by soap-cat',
    completed_at DATETIME NULL,
    case_notes TEXT NULL,
    created_at DATETIME NOT NULL,
    archived_at DATETIME NULL,
    delete_at DATETIME NULL COMMENT 'when an archived channel is scheduled for deletion',
    deleted_at DATETIME NULL,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    KEY idx_user (user_id),
    KEY idx_status (status)
) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
