-- Actualización «previa_partido» del job (jobs/automatico.py): ~50 min antes de cada horario, alineaciones → cuotas próximas → recálculo.
ALTER TABLE ejecuciones DROP CONSTRAINT IF EXISTS ejecuciones_tipo_check;
ALTER TABLE ejecuciones ADD CONSTRAINT ejecuciones_tipo_check CHECK (tipo IN
    ('actualizar_todo','solo_cuotas','solo_alineaciones','recalcular','snapshot_cierre','backtest','previa_partido'));
