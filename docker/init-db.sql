-- Banco separado para a Evolution API (usado só com o profile "whatsapp").
SELECT 'CREATE DATABASE evolution'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'evolution')\gexec
