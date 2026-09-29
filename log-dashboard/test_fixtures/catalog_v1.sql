-- Frozen version-1 catalog schema for migration tests.
CREATE TABLE folders (
  id INTEGER PRIMARY KEY,
  parent_id INTEGER REFERENCES folders(id) ON DELETE RESTRICT,
  name TEXT NOT NULL,
  created_at REAL NOT NULL,
  UNIQUE(parent_id, name),
  CHECK ((id = 1 AND parent_id IS NULL) OR
         (id <> 1 AND parent_id IS NOT NULL))
);
CREATE TABLE logs (
  id TEXT PRIMARY KEY,
  file_key TEXT NOT NULL UNIQUE,
  original_filename TEXT NOT NULL,
  size_bytes INTEGER NOT NULL CHECK (size_bytes >= 0),
  created_at REAL NOT NULL,
  status TEXT NOT NULL,
  expires_at REAL,
  saved_at REAL,
  updated_at REAL NOT NULL,
  folder_id INTEGER REFERENCES folders(id) ON DELETE RESTRICT,
  display_name TEXT,
  notes TEXT NOT NULL DEFAULT '',
  CHECK (status IN ('processing', 'temporary', 'saved', 'deleting')),
  CHECK (
    (status = 'processing' AND expires_at IS NULL AND
     saved_at IS NULL AND folder_id IS NULL AND display_name IS NULL) OR
    (status = 'temporary' AND expires_at IS NOT NULL AND
     saved_at IS NULL AND folder_id IS NULL AND display_name IS NULL) OR
    (status = 'saved' AND expires_at IS NULL AND
     saved_at IS NOT NULL AND folder_id IS NOT NULL AND
     display_name IS NOT NULL AND length(trim(display_name)) > 0) OR
    status = 'deleting'
  )
);
CREATE TABLE tags (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE);
CREATE TABLE log_tags (
  log_id TEXT NOT NULL REFERENCES logs(id) ON DELETE CASCADE,
  tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
  PRIMARY KEY(log_id, tag_id)
);
CREATE INDEX logs_expiry ON logs(status, expires_at);
CREATE INDEX logs_folder_date ON logs(folder_id, saved_at);
CREATE INDEX log_tags_tag ON log_tags(tag_id, log_id);
INSERT INTO folders VALUES (1, NULL, 'Logs', 1800000000);
PRAGMA user_version = 1;
