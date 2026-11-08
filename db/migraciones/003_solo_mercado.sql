-- Competiciones sin modelo propio: se predicen solo con el mercado sin margen.
ALTER TABLE ligas ADD COLUMN IF NOT EXISTS solo_mercado boolean NOT NULL DEFAULT false;
