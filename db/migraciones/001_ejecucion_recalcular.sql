-- Permite la actualización "recalcular" (sin consultas a APIs). Idempotente.
ALTER TABLE ejecuciones DROP CONSTRAINT IF EXISTS ejecuciones_tipo_check;
ALTER TABLE ejecuciones ADD CONSTRAINT ejecuciones_tipo_check CHECK (tipo IN
    ('actualizar_todo','solo_cuotas','solo_alineaciones','recalcular','snapshot_cierre','backtest'));
