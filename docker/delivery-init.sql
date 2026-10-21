-- Runs only on a fresh isolated Docker volume. Never target an existing MySQL server.
CREATE DATABASE IF NOT EXISTS `aftersales_delivery`
  CHARACTER SET utf8mb4 COLLATE utf8mb4_bin;
CREATE DATABASE IF NOT EXISTS `aftersales_agent_checkpoints`
  CHARACTER SET utf8mb4 COLLATE utf8mb4_bin;
-- Tables and official checkpoint migrations are owned by setup_delivery.py --schema-only.
