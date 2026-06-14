-- Migration 003: memory ranking schema

ALTER TABLE failure_history     ADD COLUMN occurrence_count INTEGER DEFAULT 1;
ALTER TABLE failure_history     ADD COLUMN last_seen DATETIME;
ALTER TABLE failure_history     ADD COLUMN memory_score REAL;

ALTER TABLE improvement_history ADD COLUMN reuse_count INTEGER DEFAULT 0;
ALTER TABLE improvement_history ADD COLUMN reuse_success_count INTEGER DEFAULT 0;
ALTER TABLE improvement_history ADD COLUMN memory_score REAL;
