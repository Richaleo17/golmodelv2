-- Acceso por persona (2026-11-20): cada quien entra con su código, se ve quién entra y cuándo, y se puede bloquear a una persona
-- sin cambiar la contraseña de los demás. El código se guarda solo como huella (HMAC); se muestra una única vez al crearlo.
CREATE TABLE IF NOT EXISTS usuarios_app (
    id            serial PRIMARY KEY,
    nombre        text        NOT NULL,
    codigo_hash   text        NOT NULL UNIQUE,
    rol           text        NOT NULL DEFAULT 'lector' CHECK (rol IN ('lector')),
    bloqueado     boolean     NOT NULL DEFAULT false,
    nota          text,
    creado_en     timestamptz NOT NULL DEFAULT now(),
    bloqueado_en  timestamptz,
    ultimo_acceso timestamptz
);
CREATE UNIQUE INDEX IF NOT EXISTS usuarios_app_nombre ON usuarios_app (lower(nombre));

CREATE TABLE IF NOT EXISTS accesos_app (
    id          bigserial PRIMARY KEY,
    ts          timestamptz NOT NULL DEFAULT now(),
    usuario_id  integer REFERENCES usuarios_app(id) ON DELETE SET NULL,
    quien       text        NOT NULL,        -- nombre de la persona, 'admin', 'lector (compartido)' o '?' (intento fallido)
    evento      text        NOT NULL CHECK (evento IN ('login', 'login_fallido', 'bloqueado', 'sesion')),
    ruta        text,
    ip          text,
    dispositivo text
);
CREATE INDEX IF NOT EXISTS accesos_app_ts ON accesos_app (ts DESC);
CREATE INDEX IF NOT EXISTS accesos_app_usuario ON accesos_app (usuario_id, ts DESC);
