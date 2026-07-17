# -*- coding: utf-8 -*-
"""
database.py
Inicializa el sistema de bases de datos SQLite del LSPD.

A diferencia de una única base "lspd.db", cada dominio de datos vive en su
propio archivo .db dentro de la carpeta db/ (denuncias.db, arrestos.db,
bandas.db, usuarios.db, etc.). Todas se combinan en una sola conexión usando
ATTACH DATABASE, así que el resto de la app (app.py) sigue escribiendo
consultas normales (incluso con JOIN entre tablas de archivos distintos) sin
tener que calificar cada tabla con el nombre de su base.

Se ejecuta automáticamente al arrancar app.py, así que no hace falta correr
este archivo por separado (aunque también se puede: `python database.py`).
"""

import os
import sqlite3
from werkzeug.security import generate_password_hash
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_DIR = os.path.join(BASE_DIR, "db")
os.makedirs(DB_DIR, exist_ok=True)

# Roles válidos del sistema
ROL_CIVIL = "Civil"
ROLES_LSPD = ["Cadete", "Oficial", "Sargento", "Teniente", "Capitán", "Jefe", "AdminWeb"]
ROLES_ADMIN = ["Jefe", "AdminWeb"]
TODOS_LOS_ROLES = [ROL_CIVIL] + ROLES_LSPD

# ----------------------------------------------------------------------
# MAPA DE ARCHIVOS: cada alias es un archivo .db físico dentro de db/.
# SQLite permite adjuntar (ATTACH) un máximo de 10 bases por conexión, así
# que los dominios "principales" (los que el usuario registra directamente)
# quedan cada uno en su propio archivo, y el resto de datos de soporte se
# agrupan en dos archivos: interno.db (personal/asuntos internos/chat) y
# sistema.db (configuración, categorías, estados, logs y portal público).
# ----------------------------------------------------------------------
DB_FILES = {
    "usuarios":        "usuarios.db",         # usuarios (login, roles, personal)
    "denuncias":       "denuncias.db",        # denuncias + archivos_denuncia
    "personas":        "personas.db",         # base de datos criminal (personas)
    "bandas":          "bandas.db",           # base de datos criminal (bandas)
    "investigaciones": "investigaciones.db",  # investigaciones + notas_investigacion
    "multas":          "multas.db",           # multas
    "armas":           "armas.db",            # registro de armas
    "arrestos":        "arrestos.db",         # arrestos / detenciones
    "interno":         "interno.db",          # ascensos, certificaciones, asuntos internos, chat
    "sistema":         "sistema.db",          # configuración, categorías, estados, logs, noticias, contactos, postulaciones
}


def get_connection():
    """
    Devuelve una única conexión SQLite con TODOS los archivos .db adjuntados
    (ATTACH), para que las consultas (incluidos JOIN entre "tablas" que en
    realidad viven en archivos físicos distintos) funcionen exactamente igual
    que si todo estuviera en una sola base de datos.
    """
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    for alias, filename in DB_FILES.items():
        ruta = os.path.join(DB_DIR, filename)
        conn.execute("ATTACH DATABASE ? AS " + alias, (ruta,))
    # Nota: SQLite NO aplica (enforce) restricciones FOREIGN KEY entre bases
    # adjuntadas distintas; sí las aplica dentro de un mismo archivo (por
    # ejemplo denuncias -> archivos_denuncia, ambas en denuncias.db).
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _ensure_column(conn, table, column, coltype_sql):
    """
    Migración simple: agrega una columna a una tabla existente si todavía no
    existe (para instalaciones que ya tenían un archivo .db creado antes de
    que esta columna se agregara al esquema).
    """
    columnas = [r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
    if column not in columnas:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype_sql}")


def init_db():
    """Crea todas las tablas (cada una en su archivo .db correspondiente) y siembra datos iniciales."""
    conn = get_connection()
    c = conn.cursor()

    # ------------------------------------------------------------------
    # usuarios.db → USUARIOS
    # ------------------------------------------------------------------
    c.execute("""
        CREATE TABLE IF NOT EXISTS usuarios.usuarios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            placa TEXT UNIQUE NOT NULL,
            nombre TEXT NOT NULL,
            apellido TEXT NOT NULL,
            password_hash TEXT NOT NULL,
            rol TEXT NOT NULL DEFAULT 'Civil',
            fecha_ingreso TEXT NOT NULL,
            activo INTEGER NOT NULL DEFAULT 1
        )
    """)
    # Migración: columnas para bloqueo temporal por intentos fallidos de login
    # (protección anti fuerza-bruta). Se agregan si el archivo ya existía sin ellas.
    _ensure_column(conn, "usuarios", "intentos_fallidos", "INTEGER NOT NULL DEFAULT 0")
    _ensure_column(conn, "usuarios", "bloqueado_hasta", "TEXT")
    conn.commit()

    # ------------------------------------------------------------------
    # sistema.db → CATEGORÍAS, ESTADOS Y CONFIGURACIÓN
    # ------------------------------------------------------------------
    c.execute("""
        CREATE TABLE IF NOT EXISTS sistema.categorias_denuncia (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT UNIQUE NOT NULL
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS sistema.categorias_investigacion (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT UNIQUE NOT NULL
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS sistema.categorias_multa (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT UNIQUE NOT NULL,
            monto_sugerido REAL DEFAULT 0
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS sistema.estados (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tipo TEXT NOT NULL,
            nombre TEXT NOT NULL,
            UNIQUE(tipo, nombre)
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS sistema.configuracion (
            clave TEXT PRIMARY KEY,
            valor TEXT,
            seccion TEXT
        )
    """)

    # ------------------------------------------------------------------
    # denuncias.db → DENUNCIAS
    # ------------------------------------------------------------------
    c.execute("""
        CREATE TABLE IF NOT EXISTS denuncias.denuncias (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nro_caso TEXT UNIQUE NOT NULL,
            tipo TEXT,
            fecha TEXT NOT NULL,
            lugar TEXT,
            denunciante TEXT,
            denunciado_dni TEXT,
            descripcion TEXT,
            estado TEXT NOT NULL DEFAULT 'Pendiente',
            id_oficial INTEGER,
            id_civil_creador INTEGER,
            fecha_creacion TEXT NOT NULL,
            es_publica INTEGER NOT NULL DEFAULT 0,
            datos_extra_json TEXT
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS denuncias.archivos_denuncia (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            id_denuncia INTEGER NOT NULL,
            nombre_archivo TEXT NOT NULL,
            ruta TEXT NOT NULL,
            FOREIGN KEY (id_denuncia) REFERENCES denuncias(id) ON DELETE CASCADE
        )
    """)

    # ------------------------------------------------------------------
    # personas.db / bandas.db → BASE DE DATOS CRIMINAL
    # ------------------------------------------------------------------
    c.execute("""
        CREATE TABLE IF NOT EXISTS personas.personas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT NOT NULL,
            dni TEXT,
            foto TEXT,
            alias TEXT,
            direccion TEXT,
            antecedentes TEXT,
            nivel_amenaza TEXT DEFAULT 'Bajo',
            es_publico INTEGER NOT NULL DEFAULT 0,
            id_banda INTEGER,
            fecha_registro TEXT
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS bandas.bandas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT NOT NULL,
            tag TEXT,
            lider TEXT,
            territorio TEXT,
            actividades TEXT,
            nivel_peligro TEXT DEFAULT 'Bajo',
            foto TEXT,
            es_publico INTEGER NOT NULL DEFAULT 0,
            fecha_registro TEXT
        )
    """)

    # ------------------------------------------------------------------
    # investigaciones.db → INVESTIGACIONES
    # ------------------------------------------------------------------
    c.execute("""
        CREATE TABLE IF NOT EXISTS investigaciones.investigaciones (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nro_operacion TEXT UNIQUE NOT NULL,
            nombre TEXT NOT NULL,
            objetivo_tipo TEXT,
            objetivo_id INTEGER,
            descripcion TEXT,
            categoria TEXT,
            estado TEXT NOT NULL DEFAULT 'Abierta',
            id_oficial_cargo INTEGER,
            fecha_inicio TEXT NOT NULL
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS investigaciones.notas_investigacion (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            id_investigacion INTEGER NOT NULL,
            nota TEXT,
            id_oficial INTEGER,
            fecha TEXT NOT NULL,
            archivo_adjunto TEXT,
            FOREIGN KEY (id_investigacion) REFERENCES investigaciones(id) ON DELETE CASCADE
        )
    """)

    # ------------------------------------------------------------------
    # multas.db → MULTAS
    # ------------------------------------------------------------------
    c.execute("""
        CREATE TABLE IF NOT EXISTS multas.multas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            id_persona INTEGER,
            nombre_infractor TEXT,
            dni_infractor TEXT,
            motivo TEXT NOT NULL,
            monto REAL NOT NULL DEFAULT 0,
            id_oficial INTEGER,
            fecha TEXT NOT NULL,
            estado TEXT NOT NULL DEFAULT 'Pendiente'
        )
    """)

    # ------------------------------------------------------------------
    # armas.db → REGISTRO DE ARMAS
    # ------------------------------------------------------------------
    c.execute("""
        CREATE TABLE IF NOT EXISTS armas.armas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tipo TEXT NOT NULL,
            numero_serie TEXT,
            marca TEXT,
            modelo TEXT,
            calibre TEXT,
            id_persona INTEGER,
            numero_permiso TEXT,
            estado TEXT NOT NULL DEFAULT 'Activa',
            id_denuncia INTEGER,
            id_oficial_registra INTEGER,
            notas TEXT,
            fecha_registro TEXT NOT NULL
        )
    """)

    # ------------------------------------------------------------------
    # arrestos.db → ARRESTOS / DETENCIONES
    # ------------------------------------------------------------------
    c.execute("""
        CREATE TABLE IF NOT EXISTS arrestos.arrestos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nro_arresto TEXT UNIQUE NOT NULL,
            id_persona INTEGER,
            nombre_detenido TEXT,
            dni_detenido TEXT,
            cargos TEXT,
            fianza REAL DEFAULT 0,
            estado_judicial TEXT NOT NULL DEFAULT 'Detenido',
            id_denuncia INTEGER,
            id_oficial INTEGER,
            notas TEXT,
            fecha TEXT NOT NULL
        )
    """)

    # ------------------------------------------------------------------
    # interno.db → SANCIONES INTERNAS (solo Jefe y AdminWeb)
    # ------------------------------------------------------------------
    c.execute("""
        CREATE TABLE IF NOT EXISTS interno.sanciones_internas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            id_oficial_investigado INTEGER NOT NULL,
            id_oficial_reporta INTEGER,
            motivo TEXT NOT NULL,
            descripcion TEXT,
            estado TEXT NOT NULL DEFAULT 'Pendiente',
            medida_aplicada TEXT,
            fecha TEXT NOT NULL,
            fecha_resolucion TEXT
        )
    """)

    # ------------------------------------------------------------------
    # interno.db → ASCENSOS Y CERTIFICACIONES
    # ------------------------------------------------------------------
    c.execute("""
        CREATE TABLE IF NOT EXISTS interno.historial_ascensos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            id_usuario INTEGER NOT NULL,
            rango_anterior TEXT,
            rango_nuevo TEXT NOT NULL,
            motivo TEXT,
            id_oficial_autoriza INTEGER,
            fecha TEXT NOT NULL
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS interno.certificaciones (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            id_usuario INTEGER NOT NULL,
            nombre_curso TEXT NOT NULL,
            institucion TEXT,
            fecha_obtencion TEXT NOT NULL,
            notas TEXT
        )
    """)

    # ------------------------------------------------------------------
    # interno.db → CHAT / MENSAJERÍA INTERNA
    # ------------------------------------------------------------------
    c.execute("""
        CREATE TABLE IF NOT EXISTS interno.mensajes_internos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            id_remitente INTEGER NOT NULL,
            id_destinatario INTEGER,
            mensaje TEXT NOT NULL,
            fecha TEXT NOT NULL,
            leido INTEGER NOT NULL DEFAULT 0
        )
    """)

    # ------------------------------------------------------------------
    # sistema.db → POSTULACIONES
    # ------------------------------------------------------------------
    c.execute("""
        CREATE TABLE IF NOT EXISTS sistema.postulaciones (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT NOT NULL,
            dni TEXT NOT NULL,
            edad INTEGER,
            motivo TEXT,
            cv_ruta TEXT,
            estado TEXT NOT NULL DEFAULT 'Pendiente',
            fecha TEXT NOT NULL,
            id_usuario_civil INTEGER
        )
    """)

    # ------------------------------------------------------------------
    # sistema.db → CONTENIDO PÚBLICO (noticias, contactos)
    # ------------------------------------------------------------------
    c.execute("""
        CREATE TABLE IF NOT EXISTS sistema.noticias (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            titulo TEXT NOT NULL,
            contenido TEXT,
            imagen TEXT,
            fecha TEXT NOT NULL,
            publicado INTEGER NOT NULL DEFAULT 1
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS sistema.contactos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT,
            email TEXT,
            mensaje TEXT,
            fecha TEXT NOT NULL,
            leido INTEGER NOT NULL DEFAULT 0
        )
    """)

    # ------------------------------------------------------------------
    # sistema.db → LOGS DE AUDITORÍA
    # ------------------------------------------------------------------
    c.execute("""
        CREATE TABLE IF NOT EXISTS sistema.logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            id_usuario INTEGER,
            accion TEXT NOT NULL,
            fecha TEXT NOT NULL
        )
    """)

    conn.commit()

    # ------------------------------------------------------------------
    # SEED: usuario inicial AdminWeb (placa 9999 / admin123)
    # ------------------------------------------------------------------
    existe = c.execute("SELECT id FROM usuarios WHERE placa = ?", ("9999",)).fetchone()
    if not existe:
        pw_hash = generate_password_hash("admin123")
        c.execute("""
            INSERT INTO usuarios (placa, nombre, apellido, password_hash, rol, fecha_ingreso, activo)
            VALUES (?, ?, ?, ?, ?, ?, 1)
        """, ("9999", "Admin", "Web", pw_hash, "AdminWeb", datetime.now().strftime("%Y-%m-%d")))

    # ------------------------------------------------------------------
    # SEED: categorías y estados por defecto
    # ------------------------------------------------------------------
    cats_denuncia = ["Robo", "Homicidio", "Vandalismo", "Tráfico de drogas", "Extorsión", "Otro"]
    for cat in cats_denuncia:
        c.execute("INSERT OR IGNORE INTO categorias_denuncia (nombre) VALUES (?)", (cat,))

    cats_investigacion = ["Narcóticos", "Crimen organizado", "Homicidios", "Robos", "Cibercrimen"]
    for cat in cats_investigacion:
        c.execute("INSERT OR IGNORE INTO categorias_investigacion (nombre) VALUES (?)", (cat,))

    cats_multa = [
        ("Exceso de velocidad", 250.0),
        ("Estacionamiento indebido", 100.0),
        ("Conducción temeraria", 400.0),
        ("Sin licencia", 300.0),
        ("Otro", 50.0),
    ]
    for nombre, monto in cats_multa:
        c.execute("INSERT OR IGNORE INTO categorias_multa (nombre, monto_sugerido) VALUES (?, ?)", (nombre, monto))

    estados_seed = {
        "denuncia": ["Pendiente", "En investigación", "Cerrada", "Archivada"],
        "investigacion": ["Abierta", "En curso", "Cerrada", "Archivada"],
        "postulacion": ["Pendiente", "Aprobada", "Rechazada"],
        "multa": ["Pendiente", "Pagada", "Anulada"],
        "arma": ["Activa", "Vencida", "Incautada", "Destruida"],
        "arresto": ["Detenido", "Libertad Bajo Fianza", "Pendiente Audiencia", "Condenado", "Absuelto"],
        "asunto_interno": ["Pendiente", "En Investigación", "Cerrado - Sancionado", "Cerrado - Sin Mérito"],
    }
    for tipo, nombres in estados_seed.items():
        for nombre in nombres:
            c.execute("INSERT OR IGNORE INTO estados (tipo, nombre) VALUES (?, ?)", (tipo, nombre))

    # ------------------------------------------------------------------
    # SEED: configuración (editor de portales)
    # ------------------------------------------------------------------
    config_seed = [
        ("nombre_departamento", "Los Santos Police Department", "public"),
        ("banner_texto", "Servir y Proteger a la Ciudad de Los Santos", "public"),
        ("logo_public", "", "public"),
        ("footer_texto", "© Los Santos Police Department - Todos los derechos reservados", "public"),
        ("postulaciones_activas", "1", "public"),
        ("postulaciones_form_url", "", "public"),
        ("mdt_titulo", "MDT - Terminal de Datos Móvil", "mdt"),
        ("mdt_color_primario", "#0d6efd", "mdt"),
        ("mdt_logo", "", "mdt"),
        ("mdt_texto_dashboard", "Bienvenido a la Terminal de Datos Móvil del LSPD", "mdt"),
    ]
    for clave, valor, seccion in config_seed:
        c.execute("INSERT OR IGNORE INTO configuracion (clave, valor, seccion) VALUES (?, ?, ?)", (clave, valor, seccion))

    # Noticia de bienvenida de ejemplo
    noticia_existe = c.execute("SELECT id FROM noticias").fetchone()
    if not noticia_existe:
        c.execute("""
            INSERT INTO noticias (titulo, contenido, imagen, fecha, publicado)
            VALUES (?, ?, ?, ?, 1)
        """, (
            "Bienvenidos al nuevo portal del LSPD",
            "El Departamento de Policía de Los Santos presenta su nuevo portal ciudadano, "
            "donde podrás realizar denuncias, postular a la institución y mantenerte informado.",
            "",
            datetime.now().strftime("%Y-%m-%d %H:%M"),
        ))

    conn.commit()
    conn.close()


def log_accion(id_usuario, accion):
    """Inserta un registro en la tabla de logs (logs.db)."""
    conn = get_connection()
    conn.execute(
        "INSERT INTO logs (id_usuario, accion, fecha) VALUES (?, ?, ?)",
        (id_usuario, accion, datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
    )
    conn.commit()
    conn.close()


if __name__ == "__main__":
    init_db()
    print("Bases de datos inicializadas correctamente en:", DB_DIR)
    for alias, filename in DB_FILES.items():
        print(f"  - {filename}  (alias: {alias})")
