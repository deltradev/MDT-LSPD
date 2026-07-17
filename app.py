# -*- coding: utf-8 -*-
"""
app.py
Aplicación Flask completa: Portal Público Civil + MDT Interna LSPD + Admin CMS.

Ejecutar con:  python app.py
Usuario inicial:  placa 9999 / password admin123 / rol AdminWeb
"""

import os
import json
import io
import zipfile
import functools
import secrets
import time
from collections import defaultdict
from datetime import datetime, timedelta

from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.middleware.proxy_fix import ProxyFix
from flask import (
    Flask, render_template, request, redirect, url_for,
    session, flash, abort, send_file, g
)
from werkzeug.utils import secure_filename
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import cm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER

from database import get_connection, init_db, log_accion, ROLES_LSPD, ROLES_ADMIN, ROL_CIVIL, DB_DIR

# ----------------------------------------------------------------------
# CONFIGURACIÓN BASE
# ----------------------------------------------------------------------
app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get(
    "LSPD_SECRET_KEY",
    "lspd-cms-clave-secreta-cambiar-en-produccion-2024"
)
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024  # 16 MB máx por subida

# ¿La app corre detrás de HTTPS real (Cloudflare, nginx con TLS, etc.)?
# Actívalo con la variable de entorno FORCE_HTTPS=1 en producción.
FORCE_HTTPS = os.environ.get("FORCE_HTTPS", "0") == "1"

# Cookies de sesión más seguras: no accesibles por JS, no se envían en
# navegación cross-site, y solo por HTTPS si FORCE_HTTPS está activo.
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=FORCE_HTTPS,
)

# Si la app corre detrás de un proxy inverso (Cloudflare, nginx, etc.), esto
# hace que Flask use la IP real del visitante (X-Forwarded-For) en vez de la
# IP del proxy — importante para que el rate-limiting y los logs de abajo
# tengan sentido. x_for=1 asume UN solo proxy delante (p. ej. solo Cloudflare).
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
UPLOAD_DENUNCIAS = os.path.join(UPLOAD_DIR, "denuncias")
UPLOAD_INVESTIGACIONES = os.path.join(UPLOAD_DIR, "investigaciones")
UPLOAD_PUBLIC = os.path.join(UPLOAD_DIR, "public")

for d in (UPLOAD_DENUNCIAS, UPLOAD_INVESTIGACIONES, UPLOAD_PUBLIC):
    os.makedirs(d, exist_ok=True)

ALLOWED_EXT = {"pdf", "png", "jpg", "jpeg", "gif", "doc", "docx", "txt", "webp"}


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXT


# ========================================================================
# SEGURIDAD: cabeceras HTTP, CSRF y rate-limiting (sin dependencias extra,
# implementado en Python puro para que siga siendo 100% compatible con
# Termux). Ver README para más contexto y para la configuración de
# Cloudflare como capa adicional por delante del servidor.
# ========================================================================

@app.after_request
def set_security_headers(response):
    """Cabeceras de seguridad estándar en toda respuesta."""
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
        "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
        "font-src 'self' https://cdn.jsdelivr.net; "
        "img-src 'self' data: blob: https:; "
        "frame-src 'self' https://docs.google.com https://*.google.com; "
        "connect-src 'self';"
    )
    if FORCE_HTTPS:
        response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
    return response


# --- CSRF: token por sesión, validado en todo POST/PUT/PATCH/DELETE ------
def _get_csrf_token():
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_hex(32)
    return session["csrf_token"]


@app.before_request
def _csrf_protect():
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        token_sesion = session.get("csrf_token")
        token_enviado = request.form.get("csrf_token") or request.headers.get("X-CSRFToken")
        if not token_sesion or not token_enviado or not secrets.compare_digest(token_enviado, token_sesion):
            abort(400, description="Token de seguridad inválido o expirado. Recarga la página e inténtalo de nuevo.")


# --- Rate limiting: ventana deslizante en memoria por IP + endpoint ------
# Nota: al ser en memoria, este contador es por proceso (no se comparte entre
# varios workers/servidores). Para un despliegue con múltiples procesos o
# detrás de un balanceador, complementa esto con reglas de Rate Limiting de
# Cloudflare (ver README), que sí son globales.
_rate_limit_hits = defaultdict(list)


def rate_limit(nombre, max_intentos, ventana_segundos, metodos=("POST",)):
    """
    Decorador: máximo `max_intentos` solicitudes cada `ventana_segundos` por IP.
    Por defecto solo cuenta solicitudes POST (envíos reales de formulario) y
    deja pasar sin contar los GET normales de cargar la página.
    """
    def decorator(view):
        @functools.wraps(view)
        def wrapped(*args, **kwargs):
            if request.method not in metodos:
                return view(*args, **kwargs)
            ip = request.remote_addr or "desconocida"
            clave = f"{nombre}:{ip}"
            ahora = time.time()
            intentos = _rate_limit_hits[clave]
            while intentos and intentos[0] <= ahora - ventana_segundos:
                intentos.pop(0)
            if len(intentos) >= max_intentos:
                log_accion(None, f"Rate limit excedido en '{nombre}' desde IP {ip}")
                abort(429, description="Demasiadas solicitudes. Espera un momento e inténtalo de nuevo.")
            intentos.append(ahora)
            return view(*args, **kwargs)
        return wrapped
    return decorator


@app.errorhandler(429)
def demasiadas_solicitudes(e):
    return render_template("public/429.html"), 429


@app.errorhandler(400)
def solicitud_invalida(e):
    return render_template("public/400.html", mensaje=getattr(e, "description", None)), 400


# ----------------------------------------------------------------------
# HELPERS DE BASE DE DATOS / CONFIG
# ----------------------------------------------------------------------
def get_config(clave, default=""):
    conn = get_connection()
    row = conn.execute("SELECT valor FROM configuracion WHERE clave = ?", (clave,)).fetchone()
    conn.close()
    return row["valor"] if row and row["valor"] is not None else default


def get_config_seccion(seccion):
    """Devuelve un dict {clave: valor} de todas las configuraciones de una sección."""
    conn = get_connection()
    rows = conn.execute("SELECT clave, valor FROM configuracion WHERE seccion = ?", (seccion,)).fetchall()
    conn.close()
    return {r["clave"]: r["valor"] for r in rows}


def get_estados(tipo):
    conn = get_connection()
    rows = conn.execute("SELECT nombre FROM estados WHERE tipo = ? ORDER BY id", (tipo,)).fetchall()
    conn.close()
    return [r["nombre"] for r in rows]


def generar_numero_caso(prefijo="CASO"):
    conn = get_connection()
    total = conn.execute("SELECT COUNT(*) as c FROM denuncias").fetchone()["c"]
    conn.close()
    return f"{prefijo}-{datetime.now().year}-{total + 1:05d}"


# Inyectar configuración pública y datos de usuario en todos los templates
@app.context_processor
def inject_globals():
    return {
        "cfg_public": get_config_seccion("public"),
        "cfg_mdt": get_config_seccion("mdt"),
        "current_user": session.get("usuario"),
        "current_year": datetime.now().year,
        "csrf_token": _get_csrf_token,
    }


# ----------------------------------------------------------------------
# AUTENTICACIÓN Y DECORADORES DE ROL
# ----------------------------------------------------------------------
def login_required(view):
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("usuario"):
            flash("Debes iniciar sesión para continuar.", "warning")
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


def role_required(*roles_permitidos):
    """
    Decorador que exige que el usuario esté logueado y su rol esté dentro
    de roles_permitidos. Si es Civil intentando acceder a /mdt -> 403.
    """
    def decorator(view):
        @functools.wraps(view)
        def wrapped(*args, **kwargs):
            usuario = session.get("usuario")
            if not usuario:
                flash("Debes iniciar sesión para continuar.", "warning")
                return redirect(url_for("login", next=request.path))
            if usuario["rol"] not in roles_permitidos:
                abort(403)
            return view(*args, **kwargs)
        return wrapped
    return decorator


def mdt_required(view):
    """Acceso a cualquier rol LSPD (todo menos Civil)."""
    return role_required(*ROLES_LSPD)(view)


def admin_required(view):
    """Acceso solo a Jefe y AdminWeb."""
    return role_required(*ROLES_ADMIN)(view)


@app.errorhandler(403)
def forbidden(e):
    return render_template("public/403.html"), 403


@app.errorhandler(404)
def not_found(e):
    return render_template("public/404.html"), 404


# ========================================================================
# RUTAS DE AUTENTICACIÓN (compartidas)
# ========================================================================
@app.route("/login", methods=["GET", "POST"])
@rate_limit("login", max_intentos=10, ventana_segundos=60)
def login():
    MAX_INTENTOS_FALLIDOS = 5
    BLOQUEO_MINUTOS = 15

    if request.method == "POST":
        placa = request.form.get("placa", "").strip()
        password = request.form.get("password", "")
        conn = get_connection()
        user = conn.execute("SELECT * FROM usuarios WHERE placa = ?", (placa,)).fetchone()

        # ¿La cuenta está bloqueada temporalmente por demasiados intentos fallidos?
        if user and user["bloqueado_hasta"]:
            bloqueado_hasta = datetime.strptime(user["bloqueado_hasta"], "%Y-%m-%d %H:%M:%S")
            if datetime.now() < bloqueado_hasta:
                minutos_restantes = max(1, int((bloqueado_hasta - datetime.now()).total_seconds() // 60) + 1)
                conn.close()
                flash(f"Cuenta bloqueada temporalmente por varios intentos fallidos. "
                      f"Intenta de nuevo en {minutos_restantes} minuto(s).", "danger")
                return render_template("public/login.html")

        if user and user["activo"] == 1 and check_password_hash(user["password_hash"], password):
            conn.execute("UPDATE usuarios SET intentos_fallidos = 0, bloqueado_hasta = NULL WHERE id = ?", (user["id"],))
            conn.commit()
            conn.close()

            session["usuario"] = {
                "id": user["id"],
                "placa": user["placa"],
                "nombre": user["nombre"],
                "apellido": user["apellido"],
                "rol": user["rol"],
            }
            log_accion(user["id"], "Inicio de sesión")
            flash(f"Bienvenido, {user['nombre']}.", "success")

            siguiente = request.args.get("next")
            if user["rol"] == ROL_CIVIL:
                return redirect(siguiente if siguiente and not siguiente.startswith("/mdt") and not siguiente.startswith("/admin") else url_for("index"))
            return redirect(siguiente if siguiente else url_for("mdt_dashboard"))
        else:
            if user:
                intentos = (user["intentos_fallidos"] or 0) + 1
                bloqueado_hasta_valor = None
                if intentos >= MAX_INTENTOS_FALLIDOS:
                    bloqueado_hasta_valor = (datetime.now() + timedelta(minutes=BLOQUEO_MINUTOS)).strftime("%Y-%m-%d %H:%M:%S")
                    intentos = 0
                conn.execute("UPDATE usuarios SET intentos_fallidos = ?, bloqueado_hasta = ? WHERE id = ?",
                             (intentos, bloqueado_hasta_valor, user["id"]))
                conn.commit()
                log_accion(user["id"], "Intento de login fallido")
                if bloqueado_hasta_valor:
                    flash(f"Demasiados intentos fallidos. Cuenta bloqueada por {BLOQUEO_MINUTOS} minutos.", "danger")
                    conn.close()
                    return render_template("public/login.html")
            conn.close()
            flash("Placa o contraseña incorrectos.", "danger")
    return render_template("public/login.html")


@app.route("/registro", methods=["GET", "POST"])
@rate_limit("registro", max_intentos=5, ventana_segundos=60)
def registro():
    if request.method == "POST":
        placa = request.form.get("placa", "").strip()
        nombre = request.form.get("nombre", "").strip()
        apellido = request.form.get("apellido", "").strip()
        password = request.form.get("password", "")
        password2 = request.form.get("password2", "")

        if not all([placa, nombre, apellido, password]):
            flash("Todos los campos son obligatorios.", "danger")
            return render_template("public/registro.html")

        if password != password2:
            flash("Las contraseñas no coinciden.", "danger")
            return render_template("public/registro.html")

        conn = get_connection()
        existe = conn.execute("SELECT id FROM usuarios WHERE placa = ?", (placa,)).fetchone()
        if existe:
            conn.close()
            flash("Ese DNI/Placa ya está registrado.", "danger")
            return render_template("public/registro.html")

        pw_hash = generate_password_hash(password)
        conn.execute("""
            INSERT INTO usuarios (placa, nombre, apellido, password_hash, rol, fecha_ingreso, activo)
            VALUES (?, ?, ?, ?, 'Civil', ?, 1)
        """, (placa, nombre, apellido, pw_hash, datetime.now().strftime("%Y-%m-%d")))
        conn.commit()
        conn.close()
        flash("Registro exitoso. Ya puedes iniciar sesión.", "success")
        return redirect(url_for("login"))
    return render_template("public/registro.html")


@app.route("/logout")
def logout():
    if session.get("usuario"):
        log_accion(session["usuario"]["id"], "Cierre de sesión")
    session.pop("usuario", None)
    flash("Sesión cerrada correctamente.", "info")
    return redirect(url_for("index"))


# ========================================================================
# PORTAL 1: PÚBLICO CIVIL  (ruta /)
# ========================================================================
@app.route("/")
def index():
    conn = get_connection()
    noticias = conn.execute(
        "SELECT * FROM noticias WHERE publicado = 1 ORDER BY fecha DESC LIMIT 6"
    ).fetchall()
    conn.close()
    return render_template("public/index.html", noticias=noticias)


@app.route("/postulaciones")
def postulaciones():
    """
    Muestra el formulario de postulación al LSPD.
    Ya no se almacena en la base de datos local: el formulario real vive en
    Google Forms (configurable desde /admin/config/public) y las postulaciones
    se abren/cierran también desde ahí con la clave 'postulaciones_activas'.
    """
    activas = get_config("postulaciones_activas", "1") == "1"
    form_url = get_config("postulaciones_form_url", "").strip()
    return render_template("public/postulacion.html", activas=activas, form_url=form_url)


@app.route("/denuncias", methods=["GET", "POST"])
@rate_limit("denuncia_publica", max_intentos=8, ventana_segundos=60)
def denuncia_publica():
    """Formulario para que un civil realice una denuncia ciudadana."""
    if request.method == "POST":
        tipo = request.form.get("tipo", "").strip()
        lugar = request.form.get("lugar", "").strip()
        denunciante = request.form.get("denunciante", "Anónimo").strip() or "Anónimo"
        denunciado_dni = request.form.get("denunciado_dni", "").strip()
        descripcion = request.form.get("descripcion", "").strip()

        if not descripcion:
            flash("La descripción de la denuncia es obligatoria.", "danger")
            return render_template("public/denuncia.html")

        nro_caso = generar_numero_caso("DEN")
        conn = get_connection()
        cur = conn.execute("""
            INSERT INTO denuncias (nro_caso, tipo, fecha, lugar, denunciante, denunciado_dni,
                                    descripcion, estado, id_civil_creador, fecha_creacion, es_publica)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'Pendiente', ?, ?, 0)
        """, (nro_caso, tipo, datetime.now().strftime("%Y-%m-%d"), lugar, denunciante, denunciado_dni,
              descripcion, session["usuario"]["id"] if session.get("usuario") else None,
              datetime.now().strftime("%Y-%m-%d %H:%M")))
        denuncia_id = cur.lastrowid
        conn.commit()

        archivo = request.files.get("archivo")
        if archivo and archivo.filename and allowed_file(archivo.filename):
            carpeta = os.path.join(UPLOAD_DENUNCIAS, str(denuncia_id))
            os.makedirs(carpeta, exist_ok=True)
            filename = secure_filename(archivo.filename)
            archivo.save(os.path.join(carpeta, filename))
            ruta_relativa = os.path.join("denuncias", str(denuncia_id), filename)
            conn.execute("INSERT INTO archivos_denuncia (id_denuncia, nombre_archivo, ruta) VALUES (?, ?, ?)",
                         (denuncia_id, filename, ruta_relativa))
            conn.commit()

        conn.close()
        flash(f"Tu denuncia fue registrada con el número de caso {nro_caso}.", "success")
        return redirect(url_for("denuncia_publica"))

    # Mostrar denuncias propias si el civil está logueado
    mis_denuncias = []
    if session.get("usuario"):
        conn = get_connection()
        mis_denuncias = conn.execute(
            "SELECT * FROM denuncias WHERE id_civil_creador = ? ORDER BY id DESC",
            (session["usuario"]["id"],)
        ).fetchall()
        conn.close()
    return render_template("public/denuncia.html", mis_denuncias=mis_denuncias)


@app.route("/se-busca")
def se_busca():
    conn = get_connection()
    personas = conn.execute("SELECT * FROM personas WHERE es_publico = 1 ORDER BY id DESC").fetchall()
    bandas = conn.execute("SELECT * FROM bandas WHERE es_publico = 1 ORDER BY id DESC").fetchall()
    conn.close()
    return render_template("public/se_busca.html", personas=personas, bandas=bandas)


@app.route("/contacto", methods=["GET", "POST"])
@rate_limit("contacto", max_intentos=8, ventana_segundos=60)
def contacto():
    if request.method == "POST":
        nombre = request.form.get("nombre", "").strip()
        email = request.form.get("email", "").strip()
        mensaje = request.form.get("mensaje", "").strip()
        if not all([nombre, email, mensaje]):
            flash("Completa todos los campos.", "danger")
            return render_template("public/contacto.html")
        conn = get_connection()
        conn.execute(
            "INSERT INTO contactos (nombre, email, mensaje, fecha, leido) VALUES (?, ?, ?, ?, 0)",
            (nombre, email, mensaje, datetime.now().strftime("%Y-%m-%d %H:%M"))
        )
        conn.commit()
        conn.close()
        flash("Tu mensaje fue enviado. Nos pondremos en contacto pronto.", "success")
        return redirect(url_for("contacto"))
    return render_template("public/contacto.html")


@app.route("/uploads/<path:filename>")
def uploaded_file(filename):
    """Sirve archivos subidos (denuncias, investigaciones, public/CV)."""
    ruta_completa = os.path.join(UPLOAD_DIR, filename)
    if not os.path.abspath(ruta_completa).startswith(os.path.abspath(UPLOAD_DIR)):
        abort(403)
    if not os.path.isfile(ruta_completa):
        abort(404)
    # Solo LSPD y admin pueden ver archivos de denuncias/investigaciones internas.
    # Los archivos "public" (CVs de postulaciones) son visibles para Jefe/AdminWeb.
    usuario = session.get("usuario")
    if filename.startswith("public/") or filename.startswith("denuncias/") or filename.startswith("investigaciones/"):
        if not usuario or usuario["rol"] == ROL_CIVIL:
            # Un civil solo puede ver el archivo de su propia denuncia pública, lo dejamos
            # restringido por simplicidad: solo LSPD/Admin ven adjuntos.
            if not usuario or usuario["rol"] not in ROLES_LSPD:
                abort(403)
    return send_file(ruta_completa)


# ========================================================================
# MDT INTERNA LSPD (ruta /mdt) — requiere login y rol != Civil
# ========================================================================
@app.route("/mdt")
@mdt_required
def mdt_dashboard():
    conn = get_connection()
    total_denuncias = conn.execute("SELECT COUNT(*) c FROM denuncias").fetchone()["c"]
    denuncias_pendientes = conn.execute("SELECT COUNT(*) c FROM denuncias WHERE estado = 'Pendiente'").fetchone()["c"]
    total_investigaciones = conn.execute("SELECT COUNT(*) c FROM investigaciones WHERE estado != 'Cerrada'").fetchone()["c"]
    total_multas = conn.execute("SELECT COUNT(*) c FROM multas").fetchone()["c"]
    ultimas_denuncias = conn.execute("SELECT * FROM denuncias ORDER BY id DESC LIMIT 5").fetchall()
    bolos_activos = conn.execute(
        "SELECT * FROM personas WHERE nivel_amenaza IN ('Alto','Extremo') ORDER BY id DESC LIMIT 5"
    ).fetchall()
    conn.close()
    return render_template(
        "mdt/dashboard.html",
        total_denuncias=total_denuncias,
        denuncias_pendientes=denuncias_pendientes,
        total_investigaciones=total_investigaciones,
        total_multas=total_multas,
        ultimas_denuncias=ultimas_denuncias,
        bolos_activos=bolos_activos,
    )


# ------------------------- DENUNCIAS (MDT) -----------------------------
@app.route("/mdt/denuncias")
@mdt_required
def mdt_denuncias():
    conn = get_connection()
    estado_filtro = request.args.get("estado", "")
    query = "SELECT * FROM denuncias"
    params = []
    if estado_filtro:
        query += " WHERE estado = ?"
        params.append(estado_filtro)
    query += " ORDER BY id DESC"
    denuncias = conn.execute(query, params).fetchall()
    estados = get_estados("denuncia")
    conn.close()
    return render_template("mdt/denuncias.html", denuncias=denuncias, estados=estados, estado_filtro=estado_filtro)


@app.route("/mdt/denuncias/nueva", methods=["GET", "POST"])
@mdt_required
def mdt_denuncia_nueva():
    conn = get_connection()
    categorias = conn.execute("SELECT * FROM categorias_denuncia ORDER BY nombre").fetchall()
    estados = get_estados("denuncia")

    if request.method == "POST":
        tipo = request.form.get("tipo", "")
        lugar = request.form.get("lugar", "")
        denunciante = request.form.get("denunciante", "")
        denunciado_dni = request.form.get("denunciado_dni", "")
        descripcion = request.form.get("descripcion", "")
        estado = request.form.get("estado", "Pendiente")

        nro_caso = generar_numero_caso("CASO")
        cur = conn.execute("""
            INSERT INTO denuncias (nro_caso, tipo, fecha, lugar, denunciante, denunciado_dni,
                                    descripcion, estado, id_oficial, fecha_creacion, es_publica)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
        """, (nro_caso, tipo, datetime.now().strftime("%Y-%m-%d"), lugar, denunciante, denunciado_dni,
              descripcion, estado, session["usuario"]["id"], datetime.now().strftime("%Y-%m-%d %H:%M")))
        denuncia_id = cur.lastrowid
        conn.commit()

        archivos = request.files.getlist("archivos")
        carpeta = os.path.join(UPLOAD_DENUNCIAS, str(denuncia_id))
        for archivo in archivos:
            if archivo and archivo.filename and allowed_file(archivo.filename):
                os.makedirs(carpeta, exist_ok=True)
                filename = secure_filename(archivo.filename)
                archivo.save(os.path.join(carpeta, filename))
                ruta_relativa = os.path.join("denuncias", str(denuncia_id), filename)
                conn.execute("INSERT INTO archivos_denuncia (id_denuncia, nombre_archivo, ruta) VALUES (?, ?, ?)",
                             (denuncia_id, filename, ruta_relativa))
        conn.commit()
        conn.close()
        log_accion(session["usuario"]["id"], f"Creó denuncia {nro_caso}")
        flash(f"Denuncia {nro_caso} creada correctamente.", "success")
        return redirect(url_for("mdt_denuncias"))

    conn.close()
    return render_template("mdt/denuncias_form.html", categorias=categorias, estados=estados, denuncia=None)


@app.route("/mdt/denuncias/<int:denuncia_id>")
@mdt_required
def mdt_denuncia_ver(denuncia_id):
    conn = get_connection()
    denuncia = conn.execute("SELECT * FROM denuncias WHERE id = ?", (denuncia_id,)).fetchone()
    if not denuncia:
        conn.close()
        abort(404)
    archivos = conn.execute("SELECT * FROM archivos_denuncia WHERE id_denuncia = ?", (denuncia_id,)).fetchall()
    conn.close()
    return render_template("mdt/denuncias_view.html", denuncia=denuncia, archivos=archivos)


@app.route("/mdt/denuncias/<int:denuncia_id>/editar", methods=["GET", "POST"])
@mdt_required
def mdt_denuncia_editar(denuncia_id):
    conn = get_connection()
    denuncia = conn.execute("SELECT * FROM denuncias WHERE id = ?", (denuncia_id,)).fetchone()
    if not denuncia:
        conn.close()
        abort(404)
    categorias = conn.execute("SELECT * FROM categorias_denuncia ORDER BY nombre").fetchall()
    estados = get_estados("denuncia")

    if request.method == "POST":
        conn.execute("""
            UPDATE denuncias SET tipo=?, lugar=?, denunciante=?, denunciado_dni=?,
                                  descripcion=?, estado=? WHERE id=?
        """, (request.form.get("tipo", ""), request.form.get("lugar", ""),
              request.form.get("denunciante", ""), request.form.get("denunciado_dni", ""),
              request.form.get("descripcion", ""), request.form.get("estado", "Pendiente"), denuncia_id))
        conn.commit()

        archivos = request.files.getlist("archivos")
        carpeta = os.path.join(UPLOAD_DENUNCIAS, str(denuncia_id))
        for archivo in archivos:
            if archivo and archivo.filename and allowed_file(archivo.filename):
                os.makedirs(carpeta, exist_ok=True)
                filename = secure_filename(archivo.filename)
                archivo.save(os.path.join(carpeta, filename))
                ruta_relativa = os.path.join("denuncias", str(denuncia_id), filename)
                conn.execute("INSERT INTO archivos_denuncia (id_denuncia, nombre_archivo, ruta) VALUES (?, ?, ?)",
                             (denuncia_id, filename, ruta_relativa))
        conn.commit()
        conn.close()
        flash("Denuncia actualizada.", "success")
        return redirect(url_for("mdt_denuncia_ver", denuncia_id=denuncia_id))

    conn.close()
    return render_template("mdt/denuncias_form.html", categorias=categorias, estados=estados, denuncia=denuncia)


@app.route("/mdt/denuncias/<int:denuncia_id>/eliminar", methods=["POST"])
@mdt_required
def mdt_denuncia_eliminar(denuncia_id):
    conn = get_connection()
    conn.execute("DELETE FROM denuncias WHERE id = ?", (denuncia_id,))
    conn.commit()
    conn.close()
    flash("Denuncia eliminada.", "info")
    return redirect(url_for("mdt_denuncias"))


@app.route("/mdt/denuncias/<int:denuncia_id>/pdf")
@mdt_required
def mdt_denuncia_pdf(denuncia_id):
    conn = get_connection()
    denuncia = conn.execute("SELECT * FROM denuncias WHERE id = ?", (denuncia_id,)).fetchone()
    conn.close()
    if not denuncia:
        abort(404)

    ruta_pdf = os.path.join(BASE_DIR, "uploads", f"constancia_denuncia_{denuncia_id}.pdf")
    generar_pdf_constancia(
        ruta_pdf,
        titulo="CONSTANCIA OFICIAL DE DENUNCIA",
        campos=[
            ("N° de Caso", denuncia["nro_caso"]),
            ("Tipo", denuncia["tipo"] or "-"),
            ("Fecha", denuncia["fecha"]),
            ("Lugar", denuncia["lugar"] or "-"),
            ("Denunciante", denuncia["denunciante"] or "-"),
            ("DNI Denunciado", denuncia["denunciado_dni"] or "-"),
            ("Estado", denuncia["estado"]),
        ],
        descripcion=denuncia["descripcion"] or "",
    )
    return send_file(ruta_pdf, as_attachment=True, download_name=f"constancia_{denuncia['nro_caso']}.pdf")


# ------------------------- BASE CRIMINAL: PERSONAS ----------------------
@app.route("/mdt/personas")
@mdt_required
def mdt_personas():
    conn = get_connection()
    q = request.args.get("q", "")
    if q:
        personas = conn.execute(
            "SELECT * FROM personas WHERE nombre LIKE ? OR dni LIKE ? OR alias LIKE ? ORDER BY id DESC",
            (f"%{q}%", f"%{q}%", f"%{q}%")
        ).fetchall()
    else:
        personas = conn.execute("SELECT * FROM personas ORDER BY id DESC").fetchall()
    conn.close()
    return render_template("mdt/personas.html", personas=personas, q=q)


@app.route("/mdt/personas/nueva", methods=["GET", "POST"])
@mdt_required
def mdt_persona_nueva():
    conn = get_connection()
    bandas = conn.execute("SELECT * FROM bandas ORDER BY nombre").fetchall()
    if request.method == "POST":
        foto_ruta = ""
        foto = request.files.get("foto")
        if foto and foto.filename and allowed_file(foto.filename):
            filename = secure_filename(foto.filename)
            foto.save(os.path.join(UPLOAD_INVESTIGACIONES, filename))
            foto_ruta = os.path.join("investigaciones", filename)

        conn.execute("""
            INSERT INTO personas (nombre, dni, foto, alias, direccion, antecedentes,
                                   nivel_amenaza, es_publico, id_banda, fecha_registro)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (request.form.get("nombre", ""), request.form.get("dni", ""), foto_ruta,
              request.form.get("alias", ""), request.form.get("direccion", ""),
              request.form.get("antecedentes", ""), request.form.get("nivel_amenaza", "Bajo"),
              1 if request.form.get("es_publico") == "on" else 0,
              request.form.get("id_banda") or None, datetime.now().strftime("%Y-%m-%d")))
        conn.commit()
        conn.close()
        flash("Persona registrada en la base criminal.", "success")
        return redirect(url_for("mdt_personas"))
    conn.close()
    return render_template("mdt/personas_form.html", persona=None, bandas=bandas)


@app.route("/mdt/personas/<int:persona_id>/editar", methods=["GET", "POST"])
@mdt_required
def mdt_persona_editar(persona_id):
    conn = get_connection()
    persona = conn.execute("SELECT * FROM personas WHERE id = ?", (persona_id,)).fetchone()
    if not persona:
        conn.close()
        abort(404)
    bandas = conn.execute("SELECT * FROM bandas ORDER BY nombre").fetchall()

    if request.method == "POST":
        foto_ruta = persona["foto"]
        foto = request.files.get("foto")
        if foto and foto.filename and allowed_file(foto.filename):
            filename = secure_filename(foto.filename)
            foto.save(os.path.join(UPLOAD_INVESTIGACIONES, filename))
            foto_ruta = os.path.join("investigaciones", filename)

        conn.execute("""
            UPDATE personas SET nombre=?, dni=?, foto=?, alias=?, direccion=?, antecedentes=?,
                                 nivel_amenaza=?, es_publico=?, id_banda=? WHERE id=?
        """, (request.form.get("nombre", ""), request.form.get("dni", ""), foto_ruta,
              request.form.get("alias", ""), request.form.get("direccion", ""),
              request.form.get("antecedentes", ""), request.form.get("nivel_amenaza", "Bajo"),
              1 if request.form.get("es_publico") == "on" else 0,
              request.form.get("id_banda") or None, persona_id))
        conn.commit()
        conn.close()
        flash("Persona actualizada.", "success")
        return redirect(url_for("mdt_personas"))
    conn.close()
    return render_template("mdt/personas_form.html", persona=persona, bandas=bandas)


@app.route("/mdt/personas/<int:persona_id>/eliminar", methods=["POST"])
@mdt_required
def mdt_persona_eliminar(persona_id):
    conn = get_connection()
    conn.execute("DELETE FROM personas WHERE id = ?", (persona_id,))
    conn.commit()
    conn.close()
    flash("Persona eliminada de la base criminal.", "info")
    return redirect(url_for("mdt_personas"))


# ------------------------- BASE CRIMINAL: BANDAS -------------------------
@app.route("/mdt/bandas")
@mdt_required
def mdt_bandas():
    conn = get_connection()
    bandas = conn.execute("SELECT * FROM bandas ORDER BY id DESC").fetchall()
    conn.close()
    return render_template("mdt/bandas.html", bandas=bandas)


@app.route("/mdt/bandas/nueva", methods=["GET", "POST"])
@mdt_required
def mdt_banda_nueva():
    if request.method == "POST":
        conn = get_connection()
        foto_ruta = ""
        foto = request.files.get("foto")
        if foto and foto.filename and allowed_file(foto.filename):
            filename = secure_filename(foto.filename)
            foto.save(os.path.join(UPLOAD_INVESTIGACIONES, filename))
            foto_ruta = os.path.join("investigaciones", filename)

        conn.execute("""
            INSERT INTO bandas (nombre, tag, lider, territorio, actividades, nivel_peligro,
                                 foto, es_publico, fecha_registro)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (request.form.get("nombre", ""), request.form.get("tag", ""), request.form.get("lider", ""),
              request.form.get("territorio", ""), request.form.get("actividades", ""),
              request.form.get("nivel_peligro", "Bajo"), foto_ruta,
              1 if request.form.get("es_publico") == "on" else 0, datetime.now().strftime("%Y-%m-%d")))
        conn.commit()
        conn.close()
        flash("Banda registrada.", "success")
        return redirect(url_for("mdt_bandas"))
    return render_template("mdt/bandas_form.html", banda=None)


@app.route("/mdt/bandas/<int:banda_id>/editar", methods=["GET", "POST"])
@mdt_required
def mdt_banda_editar(banda_id):
    conn = get_connection()
    banda = conn.execute("SELECT * FROM bandas WHERE id = ?", (banda_id,)).fetchone()
    if not banda:
        conn.close()
        abort(404)

    if request.method == "POST":
        foto_ruta = banda["foto"]
        foto = request.files.get("foto")
        if foto and foto.filename and allowed_file(foto.filename):
            filename = secure_filename(foto.filename)
            foto.save(os.path.join(UPLOAD_INVESTIGACIONES, filename))
            foto_ruta = os.path.join("investigaciones", filename)

        conn.execute("""
            UPDATE bandas SET nombre=?, tag=?, lider=?, territorio=?, actividades=?,
                               nivel_peligro=?, foto=?, es_publico=? WHERE id=?
        """, (request.form.get("nombre", ""), request.form.get("tag", ""), request.form.get("lider", ""),
              request.form.get("territorio", ""), request.form.get("actividades", ""),
              request.form.get("nivel_peligro", "Bajo"), foto_ruta,
              1 if request.form.get("es_publico") == "on" else 0, banda_id))
        conn.commit()
        conn.close()
        flash("Banda actualizada.", "success")
        return redirect(url_for("mdt_bandas"))
    conn.close()
    return render_template("mdt/bandas_form.html", banda=banda)


@app.route("/mdt/bandas/<int:banda_id>/eliminar", methods=["POST"])
@mdt_required
def mdt_banda_eliminar(banda_id):
    conn = get_connection()
    conn.execute("DELETE FROM bandas WHERE id = ?", (banda_id,))
    conn.commit()
    conn.close()
    flash("Banda eliminada.", "info")
    return redirect(url_for("mdt_bandas"))


# ------------------------- INVESTIGACIONES -------------------------------
@app.route("/mdt/investigaciones")
@mdt_required
def mdt_investigaciones():
    conn = get_connection()
    investigaciones = conn.execute("SELECT * FROM investigaciones ORDER BY id DESC").fetchall()
    conn.close()
    return render_template("mdt/investigaciones.html", investigaciones=investigaciones)


@app.route("/mdt/investigaciones/nueva", methods=["GET", "POST"])
@mdt_required
def mdt_investigacion_nueva():
    conn = get_connection()
    categorias = conn.execute("SELECT * FROM categorias_investigacion ORDER BY nombre").fetchall()
    personas = conn.execute("SELECT id, nombre FROM personas ORDER BY nombre").fetchall()
    bandas = conn.execute("SELECT id, nombre FROM bandas ORDER BY nombre").fetchall()
    estados = get_estados("investigacion")

    if request.method == "POST":
        total = conn.execute("SELECT COUNT(*) c FROM investigaciones").fetchone()["c"]
        nro_operacion = f"OP-{datetime.now().year}-{total + 1:04d}"
        conn.execute("""
            INSERT INTO investigaciones (nro_operacion, nombre, objetivo_tipo, objetivo_id,
                                          descripcion, categoria, estado, id_oficial_cargo, fecha_inicio)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (nro_operacion, request.form.get("nombre", ""), request.form.get("objetivo_tipo", ""),
              request.form.get("objetivo_id") or None, request.form.get("descripcion", ""),
              request.form.get("categoria", ""), request.form.get("estado", "Abierta"),
              session["usuario"]["id"], datetime.now().strftime("%Y-%m-%d")))
        conn.commit()
        conn.close()
        flash(f"Investigación {nro_operacion} creada.", "success")
        return redirect(url_for("mdt_investigaciones"))

    conn.close()
    return render_template("mdt/investigaciones_form.html", investigacion=None, categorias=categorias,
                            personas=personas, bandas=bandas, estados=estados, notas=[])


@app.route("/mdt/investigaciones/<int:inv_id>", methods=["GET", "POST"])
@mdt_required
def mdt_investigacion_ver(inv_id):
    conn = get_connection()
    investigacion = conn.execute("SELECT * FROM investigaciones WHERE id = ?", (inv_id,)).fetchone()
    if not investigacion:
        conn.close()
        abort(404)
    categorias = conn.execute("SELECT * FROM categorias_investigacion ORDER BY nombre").fetchall()
    personas = conn.execute("SELECT id, nombre FROM personas ORDER BY nombre").fetchall()
    bandas = conn.execute("SELECT id, nombre FROM bandas ORDER BY nombre").fetchall()
    estados = get_estados("investigacion")
    notas = conn.execute("""
        SELECT n.*, u.nombre as oficial_nombre, u.apellido as oficial_apellido
        FROM notas_investigacion n LEFT JOIN usuarios u ON n.id_oficial = u.id
        WHERE n.id_investigacion = ? ORDER BY n.id DESC
    """, (inv_id,)).fetchall()

    if request.method == "POST":
        # Agregar nota
        nota_texto = request.form.get("nota", "").strip()
        if nota_texto:
            archivo_ruta = ""
            archivo = request.files.get("archivo_adjunto")
            if archivo and archivo.filename and allowed_file(archivo.filename):
                carpeta = os.path.join(UPLOAD_INVESTIGACIONES, str(inv_id))
                os.makedirs(carpeta, exist_ok=True)
                filename = secure_filename(archivo.filename)
                archivo.save(os.path.join(carpeta, filename))
                archivo_ruta = os.path.join("investigaciones", str(inv_id), filename)
            conn.execute("""
                INSERT INTO notas_investigacion (id_investigacion, nota, id_oficial, fecha, archivo_adjunto)
                VALUES (?, ?, ?, ?, ?)
            """, (inv_id, nota_texto, session["usuario"]["id"], datetime.now().strftime("%Y-%m-%d %H:%M"), archivo_ruta))
            conn.commit()
            flash("Nota agregada a la investigación.", "success")
        conn.close()
        return redirect(url_for("mdt_investigacion_ver", inv_id=inv_id))

    conn.close()
    return render_template("mdt/investigaciones_form.html", investigacion=investigacion, categorias=categorias,
                            personas=personas, bandas=bandas, estados=estados, notas=notas)


@app.route("/mdt/investigaciones/<int:inv_id>/editar", methods=["POST"])
@mdt_required
def mdt_investigacion_editar(inv_id):
    conn = get_connection()
    conn.execute("""
        UPDATE investigaciones SET nombre=?, objetivo_tipo=?, objetivo_id=?, descripcion=?,
                                    categoria=?, estado=? WHERE id=?
    """, (request.form.get("nombre", ""), request.form.get("objetivo_tipo", ""),
          request.form.get("objetivo_id") or None, request.form.get("descripcion", ""),
          request.form.get("categoria", ""), request.form.get("estado", "Abierta"), inv_id))
    conn.commit()
    conn.close()
    flash("Investigación actualizada.", "success")
    return redirect(url_for("mdt_investigacion_ver", inv_id=inv_id))


@app.route("/mdt/investigaciones/<int:inv_id>/eliminar", methods=["POST"])
@mdt_required
def mdt_investigacion_eliminar(inv_id):
    conn = get_connection()
    conn.execute("DELETE FROM investigaciones WHERE id = ?", (inv_id,))
    conn.commit()
    conn.close()
    flash("Investigación eliminada.", "info")
    return redirect(url_for("mdt_investigaciones"))


# ------------------------------- MULTAS -----------------------------------
@app.route("/mdt/multas")
@mdt_required
def mdt_multas():
    conn = get_connection()
    multas = conn.execute("""
        SELECT m.*, u.nombre as oficial_nombre, u.apellido as oficial_apellido
        FROM multas m LEFT JOIN usuarios u ON m.id_oficial = u.id
        ORDER BY m.id DESC
    """).fetchall()
    conn.close()
    return render_template("mdt/multas.html", multas=multas)


@app.route("/mdt/multas/nueva", methods=["GET", "POST"])
@mdt_required
def mdt_multa_nueva():
    conn = get_connection()
    categorias = conn.execute("SELECT * FROM categorias_multa ORDER BY nombre").fetchall()
    personas = conn.execute("SELECT id, nombre, dni FROM personas ORDER BY nombre").fetchall()
    estados = get_estados("multa")

    if request.method == "POST":
        conn.execute("""
            INSERT INTO multas (id_persona, nombre_infractor, dni_infractor, motivo, monto,
                                 id_oficial, fecha, estado)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (request.form.get("id_persona") or None, request.form.get("nombre_infractor", ""),
              request.form.get("dni_infractor", ""), request.form.get("motivo", ""),
              float(request.form.get("monto") or 0), session["usuario"]["id"],
              datetime.now().strftime("%Y-%m-%d"), request.form.get("estado", "Pendiente")))
        conn.commit()
        conn.close()
        flash("Multa registrada.", "success")
        return redirect(url_for("mdt_multas"))

    conn.close()
    return render_template("mdt/multas_form.html", categorias=categorias, personas=personas, estados=estados)


@app.route("/mdt/multas/<int:multa_id>/eliminar", methods=["POST"])
@mdt_required
def mdt_multa_eliminar(multa_id):
    conn = get_connection()
    conn.execute("DELETE FROM multas WHERE id = ?", (multa_id,))
    conn.commit()
    conn.close()
    flash("Multa eliminada.", "info")
    return redirect(url_for("mdt_multas"))


@app.route("/mdt/multas/<int:multa_id>/pdf")
@mdt_required
def mdt_multa_pdf(multa_id):
    conn = get_connection()
    multa = conn.execute("SELECT * FROM multas WHERE id = ?", (multa_id,)).fetchone()
    conn.close()
    if not multa:
        abort(404)
    ruta_pdf = os.path.join(BASE_DIR, "uploads", f"multa_{multa_id}.pdf")
    generar_pdf_constancia(
        ruta_pdf,
        titulo="BOLETA OFICIAL DE MULTA",
        campos=[
            ("Infractor", multa["nombre_infractor"] or "-"),
            ("DNI", multa["dni_infractor"] or "-"),
            ("Motivo", multa["motivo"]),
            ("Monto", f"${multa['monto']:.2f}"),
            ("Fecha", multa["fecha"]),
            ("Estado", multa["estado"]),
        ],
        descripcion="",
    )
    return send_file(ruta_pdf, as_attachment=True, download_name=f"multa_{multa_id}.pdf")


# --------------------------------- BOLO ------------------------------------
@app.route("/mdt/bolo")
@mdt_required
def mdt_bolo():
    conn = get_connection()
    personas = conn.execute(
        "SELECT * FROM personas WHERE nivel_amenaza IN ('Alto','Extremo') ORDER BY id DESC"
    ).fetchall()
    bandas = conn.execute(
        "SELECT * FROM bandas WHERE nivel_peligro IN ('Alto','Extremo') ORDER BY id DESC"
    ).fetchall()
    conn.close()
    return render_template("mdt/bolo.html", personas=personas, bandas=bandas)


# ========================================================================
# REGISTRO DE ARMAS (permisos civiles + armas incautadas)
# ========================================================================
@app.route("/mdt/armas")
@mdt_required
def mdt_armas():
    conn = get_connection()
    armas = conn.execute("""
        SELECT a.*, p.nombre as persona_nombre, p.dni as persona_dni
        FROM armas a LEFT JOIN personas p ON a.id_persona = p.id
        ORDER BY a.id DESC
    """).fetchall()
    conn.close()
    return render_template("mdt/armas.html", armas=armas)


@app.route("/mdt/armas/nueva", methods=["GET", "POST"])
@mdt_required
def mdt_arma_nueva():
    conn = get_connection()
    personas = conn.execute("SELECT id, nombre, dni FROM personas ORDER BY nombre").fetchall()
    denuncias = conn.execute("SELECT id, nro_caso FROM denuncias ORDER BY id DESC LIMIT 200").fetchall()
    estados = get_estados("arma")

    if request.method == "POST":
        conn.execute("""
            INSERT INTO armas (tipo, numero_serie, marca, modelo, calibre, id_persona,
                                numero_permiso, estado, id_denuncia, id_oficial_registra, notas, fecha_registro)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (request.form.get("tipo", "Permiso Civil"), request.form.get("numero_serie", ""),
              request.form.get("marca", ""), request.form.get("modelo", ""), request.form.get("calibre", ""),
              request.form.get("id_persona") or None, request.form.get("numero_permiso", ""),
              request.form.get("estado", "Activa"), request.form.get("id_denuncia") or None,
              session["usuario"]["id"], request.form.get("notas", ""), datetime.now().strftime("%Y-%m-%d")))
        conn.commit()
        conn.close()
        flash("Arma registrada correctamente.", "success")
        return redirect(url_for("mdt_armas"))

    conn.close()
    return render_template("mdt/armas_form.html", arma=None, personas=personas, denuncias=denuncias, estados=estados)


@app.route("/mdt/armas/<int:arma_id>/editar", methods=["GET", "POST"])
@mdt_required
def mdt_arma_editar(arma_id):
    conn = get_connection()
    arma = conn.execute("SELECT * FROM armas WHERE id = ?", (arma_id,)).fetchone()
    if not arma:
        conn.close()
        abort(404)
    personas = conn.execute("SELECT id, nombre, dni FROM personas ORDER BY nombre").fetchall()
    denuncias = conn.execute("SELECT id, nro_caso FROM denuncias ORDER BY id DESC LIMIT 200").fetchall()
    estados = get_estados("arma")

    if request.method == "POST":
        conn.execute("""
            UPDATE armas SET tipo=?, numero_serie=?, marca=?, modelo=?, calibre=?, id_persona=?,
                              numero_permiso=?, estado=?, id_denuncia=?, notas=? WHERE id=?
        """, (request.form.get("tipo", "Permiso Civil"), request.form.get("numero_serie", ""),
              request.form.get("marca", ""), request.form.get("modelo", ""), request.form.get("calibre", ""),
              request.form.get("id_persona") or None, request.form.get("numero_permiso", ""),
              request.form.get("estado", "Activa"), request.form.get("id_denuncia") or None,
              request.form.get("notas", ""), arma_id))
        conn.commit()
        conn.close()
        flash("Arma actualizada.", "success")
        return redirect(url_for("mdt_armas"))

    conn.close()
    return render_template("mdt/armas_form.html", arma=arma, personas=personas, denuncias=denuncias, estados=estados)


@app.route("/mdt/armas/<int:arma_id>/eliminar", methods=["POST"])
@mdt_required
def mdt_arma_eliminar(arma_id):
    conn = get_connection()
    conn.execute("DELETE FROM armas WHERE id = ?", (arma_id,))
    conn.commit()
    conn.close()
    flash("Registro de arma eliminado.", "info")
    return redirect(url_for("mdt_armas"))


# ========================================================================
# ARRESTOS / DETENCIONES
# ========================================================================
@app.route("/mdt/arrestos")
@mdt_required
def mdt_arrestos():
    conn = get_connection()
    arrestos = conn.execute("""
        SELECT a.*, u.nombre as oficial_nombre, u.apellido as oficial_apellido
        FROM arrestos a LEFT JOIN usuarios u ON a.id_oficial = u.id
        ORDER BY a.id DESC
    """).fetchall()
    conn.close()
    return render_template("mdt/arrestos.html", arrestos=arrestos)


@app.route("/mdt/arrestos/nueva", methods=["GET", "POST"])
@mdt_required
def mdt_arresto_nuevo():
    conn = get_connection()
    personas = conn.execute("SELECT id, nombre, dni FROM personas ORDER BY nombre").fetchall()
    denuncias = conn.execute("SELECT id, nro_caso FROM denuncias ORDER BY id DESC LIMIT 200").fetchall()
    estados = get_estados("arresto")

    if request.method == "POST":
        total = conn.execute("SELECT COUNT(*) c FROM arrestos").fetchone()["c"]
        nro_arresto = f"ARR-{datetime.now().year}-{total + 1:05d}"
        conn.execute("""
            INSERT INTO arrestos (nro_arresto, id_persona, nombre_detenido, dni_detenido, cargos,
                                   fianza, estado_judicial, id_denuncia, id_oficial, notas, fecha)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (nro_arresto, request.form.get("id_persona") or None, request.form.get("nombre_detenido", ""),
              request.form.get("dni_detenido", ""), request.form.get("cargos", ""),
              float(request.form.get("fianza") or 0), request.form.get("estado_judicial", "Detenido"),
              request.form.get("id_denuncia") or None, session["usuario"]["id"],
              request.form.get("notas", ""), datetime.now().strftime("%Y-%m-%d")))
        conn.commit()
        conn.close()
        flash(f"Arresto {nro_arresto} registrado.", "success")
        return redirect(url_for("mdt_arrestos"))

    conn.close()
    return render_template("mdt/arrestos_form.html", arresto=None, personas=personas, denuncias=denuncias, estados=estados)


@app.route("/mdt/arrestos/<int:arresto_id>/editar", methods=["GET", "POST"])
@mdt_required
def mdt_arresto_editar(arresto_id):
    conn = get_connection()
    arresto = conn.execute("SELECT * FROM arrestos WHERE id = ?", (arresto_id,)).fetchone()
    if not arresto:
        conn.close()
        abort(404)
    personas = conn.execute("SELECT id, nombre, dni FROM personas ORDER BY nombre").fetchall()
    denuncias = conn.execute("SELECT id, nro_caso FROM denuncias ORDER BY id DESC LIMIT 200").fetchall()
    estados = get_estados("arresto")

    if request.method == "POST":
        conn.execute("""
            UPDATE arrestos SET id_persona=?, nombre_detenido=?, dni_detenido=?, cargos=?, fianza=?,
                                 estado_judicial=?, id_denuncia=?, notas=? WHERE id=?
        """, (request.form.get("id_persona") or None, request.form.get("nombre_detenido", ""),
              request.form.get("dni_detenido", ""), request.form.get("cargos", ""),
              float(request.form.get("fianza") or 0), request.form.get("estado_judicial", "Detenido"),
              request.form.get("id_denuncia") or None, request.form.get("notas", ""), arresto_id))
        conn.commit()
        conn.close()
        flash("Arresto actualizado.", "success")
        return redirect(url_for("mdt_arrestos"))

    conn.close()
    return render_template("mdt/arrestos_form.html", arresto=arresto, personas=personas, denuncias=denuncias, estados=estados)


@app.route("/mdt/arrestos/<int:arresto_id>/eliminar", methods=["POST"])
@mdt_required
def mdt_arresto_eliminar(arresto_id):
    conn = get_connection()
    conn.execute("DELETE FROM arrestos WHERE id = ?", (arresto_id,))
    conn.commit()
    conn.close()
    flash("Arresto eliminado.", "info")
    return redirect(url_for("mdt_arrestos"))


# ========================================================================
# ASUNTOS INTERNOS / SANCIONES (solo Jefe y AdminWeb)
# ========================================================================
@app.route("/mdt/asuntos-internos")
@admin_required
def mdt_asuntos_internos():
    conn = get_connection()
    sanciones = conn.execute("""
        SELECT s.*,
               ui.nombre as investigado_nombre, ui.apellido as investigado_apellido, ui.placa as investigado_placa,
               ur.nombre as reporta_nombre, ur.apellido as reporta_apellido
        FROM sanciones_internas s
        LEFT JOIN usuarios ui ON s.id_oficial_investigado = ui.id
        LEFT JOIN usuarios ur ON s.id_oficial_reporta = ur.id
        ORDER BY s.id DESC
    """).fetchall()
    conn.close()
    return render_template("mdt/asuntos_internos.html", sanciones=sanciones)


@app.route("/mdt/asuntos-internos/nueva", methods=["GET", "POST"])
@admin_required
def mdt_asunto_interno_nuevo():
    conn = get_connection()
    oficiales = conn.execute(
        "SELECT id, nombre, apellido, placa, rol FROM usuarios WHERE rol != 'Civil' ORDER BY nombre"
    ).fetchall()
    estados = get_estados("asunto_interno")

    if request.method == "POST":
        conn.execute("""
            INSERT INTO sanciones_internas (id_oficial_investigado, id_oficial_reporta, motivo,
                                             descripcion, estado, medida_aplicada, fecha, fecha_resolucion)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (request.form.get("id_oficial_investigado"), session["usuario"]["id"],
              request.form.get("motivo", ""), request.form.get("descripcion", ""),
              request.form.get("estado", "Pendiente"), request.form.get("medida_aplicada", ""),
              datetime.now().strftime("%Y-%m-%d"),
              datetime.now().strftime("%Y-%m-%d") if request.form.get("estado", "").startswith("Cerrad") else None))
        conn.commit()
        conn.close()
        log_accion(session["usuario"]["id"], "Registró un reporte de Asuntos Internos")
        flash("Reporte de Asuntos Internos registrado.", "success")
        return redirect(url_for("mdt_asuntos_internos"))

    conn.close()
    return render_template("mdt/asuntos_internos_form.html", sancion=None, oficiales=oficiales, estados=estados)


@app.route("/mdt/asuntos-internos/<int:sancion_id>/editar", methods=["GET", "POST"])
@admin_required
def mdt_asunto_interno_editar(sancion_id):
    conn = get_connection()
    sancion = conn.execute("SELECT * FROM sanciones_internas WHERE id = ?", (sancion_id,)).fetchone()
    if not sancion:
        conn.close()
        abort(404)
    oficiales = conn.execute(
        "SELECT id, nombre, apellido, placa, rol FROM usuarios WHERE rol != 'Civil' ORDER BY nombre"
    ).fetchall()
    estados = get_estados("asunto_interno")

    if request.method == "POST":
        estado_nuevo = request.form.get("estado", "Pendiente")
        conn.execute("""
            UPDATE sanciones_internas SET id_oficial_investigado=?, motivo=?, descripcion=?,
                                           estado=?, medida_aplicada=?, fecha_resolucion=? WHERE id=?
        """, (request.form.get("id_oficial_investigado"), request.form.get("motivo", ""),
              request.form.get("descripcion", ""), estado_nuevo, request.form.get("medida_aplicada", ""),
              datetime.now().strftime("%Y-%m-%d") if estado_nuevo.startswith("Cerrad") else sancion["fecha_resolucion"],
              sancion_id))
        conn.commit()
        conn.close()
        flash("Reporte actualizado.", "success")
        return redirect(url_for("mdt_asuntos_internos"))

    conn.close()
    return render_template("mdt/asuntos_internos_form.html", sancion=sancion, oficiales=oficiales, estados=estados)


@app.route("/mdt/asuntos-internos/<int:sancion_id>/eliminar", methods=["POST"])
@admin_required
def mdt_asunto_interno_eliminar(sancion_id):
    conn = get_connection()
    conn.execute("DELETE FROM sanciones_internas WHERE id = ?", (sancion_id,))
    conn.commit()
    conn.close()
    flash("Reporte de Asuntos Internos eliminado.", "info")
    return redirect(url_for("mdt_asuntos_internos"))


# ========================================================================
# PERSONAL: ASCENSOS Y CERTIFICACIONES
# ========================================================================
@app.route("/mdt/personal")
@mdt_required
def mdt_personal():
    conn = get_connection()
    oficiales = conn.execute(
        "SELECT * FROM usuarios WHERE rol != 'Civil' ORDER BY nombre"
    ).fetchall()
    conn.close()
    return render_template("mdt/personal.html", oficiales=oficiales)


@app.route("/mdt/personal/<int:user_id>")
@mdt_required
def mdt_personal_ver(user_id):
    conn = get_connection()
    oficial = conn.execute("SELECT * FROM usuarios WHERE id = ? AND rol != 'Civil'", (user_id,)).fetchone()
    if not oficial:
        conn.close()
        abort(404)
    ascensos = conn.execute("""
        SELECT h.*, u.nombre as autoriza_nombre, u.apellido as autoriza_apellido
        FROM historial_ascensos h LEFT JOIN usuarios u ON h.id_oficial_autoriza = u.id
        WHERE h.id_usuario = ? ORDER BY h.id DESC
    """, (user_id,)).fetchall()
    certificaciones = conn.execute(
        "SELECT * FROM certificaciones WHERE id_usuario = ? ORDER BY id DESC", (user_id,)
    ).fetchall()
    conn.close()
    return render_template("mdt/personal_ver.html", oficial=oficial, ascensos=ascensos,
                            certificaciones=certificaciones, roles_lspd=ROLES_LSPD)


@app.route("/mdt/personal/<int:user_id>/ascenso", methods=["POST"])
@admin_required
def mdt_personal_ascenso(user_id):
    conn = get_connection()
    oficial = conn.execute("SELECT * FROM usuarios WHERE id = ?", (user_id,)).fetchone()
    if not oficial:
        conn.close()
        abort(404)
    rango_nuevo = request.form.get("rango_nuevo", "")
    motivo = request.form.get("motivo", "")
    if rango_nuevo:
        conn.execute("""
            INSERT INTO historial_ascensos (id_usuario, rango_anterior, rango_nuevo, motivo, id_oficial_autoriza, fecha)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (user_id, oficial["rol"], rango_nuevo, motivo, session["usuario"]["id"], datetime.now().strftime("%Y-%m-%d")))
        conn.execute("UPDATE usuarios SET rol = ? WHERE id = ?", (rango_nuevo, user_id))
        conn.commit()
        log_accion(session["usuario"]["id"], f"Ascendió a {oficial['placa']} de {oficial['rol']} a {rango_nuevo}")
        flash(f"{oficial['nombre']} {oficial['apellido']} ahora es {rango_nuevo}.", "success")
    conn.close()
    return redirect(url_for("mdt_personal_ver", user_id=user_id))


@app.route("/mdt/personal/<int:user_id>/certificacion", methods=["POST"])
@mdt_required
def mdt_personal_certificacion(user_id):
    conn = get_connection()
    nombre_curso = request.form.get("nombre_curso", "").strip()
    if nombre_curso:
        conn.execute("""
            INSERT INTO certificaciones (id_usuario, nombre_curso, institucion, fecha_obtencion, notas)
            VALUES (?, ?, ?, ?, ?)
        """, (user_id, nombre_curso, request.form.get("institucion", ""),
              request.form.get("fecha_obtencion") or datetime.now().strftime("%Y-%m-%d"),
              request.form.get("notas", "")))
        conn.commit()
        flash("Certificación agregada.", "success")
    conn.close()
    return redirect(url_for("mdt_personal_ver", user_id=user_id))


@app.route("/mdt/personal/certificacion/<int:cert_id>/eliminar", methods=["POST"])
@mdt_required
def mdt_personal_certificacion_eliminar(cert_id):
    conn = get_connection()
    cert = conn.execute("SELECT id_usuario FROM certificaciones WHERE id = ?", (cert_id,)).fetchone()
    if not cert:
        conn.close()
        abort(404)
    user_id = cert["id_usuario"]
    conn.execute("DELETE FROM certificaciones WHERE id = ?", (cert_id,))
    conn.commit()
    conn.close()
    flash("Certificación eliminada.", "info")
    return redirect(url_for("mdt_personal_ver", user_id=user_id))


# ========================================================================
# CHAT / MENSAJERÍA INTERNA
# ========================================================================
@app.route("/mdt/chat")
@mdt_required
def mdt_chat():
    conn = get_connection()
    oficiales = conn.execute(
        "SELECT id, nombre, apellido, placa, rol FROM usuarios WHERE rol != 'Civil' AND id != ? ORDER BY nombre",
        (session["usuario"]["id"],)
    ).fetchall()

    con_id = request.args.get("con", type=int)
    if con_id:
        mensajes = conn.execute("""
            SELECT m.*, u.nombre as remitente_nombre, u.apellido as remitente_apellido
            FROM mensajes_internos m LEFT JOIN usuarios u ON m.id_remitente = u.id
            WHERE (m.id_remitente = ? AND m.id_destinatario = ?)
               OR (m.id_remitente = ? AND m.id_destinatario = ?)
            ORDER BY m.id ASC LIMIT 200
        """, (session["usuario"]["id"], con_id, con_id, session["usuario"]["id"])).fetchall()
    else:
        mensajes = conn.execute("""
            SELECT m.*, u.nombre as remitente_nombre, u.apellido as remitente_apellido
            FROM mensajes_internos m LEFT JOIN usuarios u ON m.id_remitente = u.id
            WHERE m.id_destinatario IS NULL
            ORDER BY m.id ASC LIMIT 200
        """).fetchall()
    conn.close()
    return render_template("mdt/chat.html", oficiales=oficiales, mensajes=mensajes, con_id=con_id)


@app.route("/mdt/chat/enviar", methods=["POST"])
@mdt_required
@rate_limit("chat_enviar", max_intentos=20, ventana_segundos=60)
def mdt_chat_enviar():
    mensaje = request.form.get("mensaje", "").strip()
    con_id = request.form.get("con_id", type=int)
    if mensaje:
        conn = get_connection()
        conn.execute("""
            INSERT INTO mensajes_internos (id_remitente, id_destinatario, mensaje, fecha, leido)
            VALUES (?, ?, ?, ?, 0)
        """, (session["usuario"]["id"], con_id, mensaje, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        conn.commit()
        conn.close()
    return redirect(url_for("mdt_chat", con=con_id) if con_id else url_for("mdt_chat"))


@app.route("/mdt/chat/mensajes")
@mdt_required
def mdt_chat_mensajes():
    """Endpoint JSON para refrescar mensajes vía polling (usado por chat.html)."""
    conn = get_connection()
    con_id = request.args.get("con", type=int)
    if con_id:
        mensajes = conn.execute("""
            SELECT m.*, u.nombre as remitente_nombre, u.apellido as remitente_apellido
            FROM mensajes_internos m LEFT JOIN usuarios u ON m.id_remitente = u.id
            WHERE (m.id_remitente = ? AND m.id_destinatario = ?)
               OR (m.id_remitente = ? AND m.id_destinatario = ?)
            ORDER BY m.id ASC LIMIT 200
        """, (session["usuario"]["id"], con_id, con_id, session["usuario"]["id"])).fetchall()
    else:
        mensajes = conn.execute("""
            SELECT m.*, u.nombre as remitente_nombre, u.apellido as remitente_apellido
            FROM mensajes_internos m LEFT JOIN usuarios u ON m.id_remitente = u.id
            WHERE m.id_destinatario IS NULL
            ORDER BY m.id ASC LIMIT 200
        """).fetchall()
    conn.close()
    data = [{
        "id": m["id"],
        "remitente": f"{m['remitente_nombre']} {m['remitente_apellido']}",
        "id_remitente": m["id_remitente"],
        "mensaje": m["mensaje"],
        "fecha": m["fecha"],
    } for m in mensajes]
    return {"mensajes": data}


# ========================================================================
# ESTADÍSTICAS AUTOMÁTICAS
# ========================================================================
@app.route("/mdt/estadisticas")
@mdt_required
def mdt_estadisticas():
    conn = get_connection()

    denuncias_por_tipo = conn.execute("""
        SELECT COALESCE(NULLIF(tipo, ''), 'Sin tipo') as etiqueta, COUNT(*) as total
        FROM denuncias GROUP BY etiqueta ORDER BY total DESC
    """).fetchall()

    denuncias_por_mes = conn.execute("""
        SELECT substr(fecha_creacion, 1, 7) as mes, COUNT(*) as total
        FROM denuncias GROUP BY mes ORDER BY mes ASC LIMIT 12
    """).fetchall()

    investigaciones_por_estado = conn.execute("""
        SELECT estado as etiqueta, COUNT(*) as total FROM investigaciones GROUP BY estado
    """).fetchall()

    multas_por_mes = conn.execute("""
        SELECT substr(fecha, 1, 7) as mes, COUNT(*) as total, SUM(monto) as recaudado
        FROM multas GROUP BY mes ORDER BY mes ASC LIMIT 12
    """).fetchall()

    arrestos_por_mes = conn.execute("""
        SELECT substr(fecha, 1, 7) as mes, COUNT(*) as total
        FROM arrestos GROUP BY mes ORDER BY mes ASC LIMIT 12
    """).fetchall()

    top_oficiales = conn.execute("""
        SELECT u.nombre, u.apellido, u.placa, COUNT(d.id) as total
        FROM denuncias d JOIN usuarios u ON d.id_oficial = u.id
        GROUP BY d.id_oficial ORDER BY total DESC LIMIT 8
    """).fetchall()

    personas_por_amenaza = conn.execute("""
        SELECT nivel_amenaza as etiqueta, COUNT(*) as total FROM personas GROUP BY nivel_amenaza
    """).fetchall()

    totales = {
        "denuncias": conn.execute("SELECT COUNT(*) c FROM denuncias").fetchone()["c"],
        "investigaciones": conn.execute("SELECT COUNT(*) c FROM investigaciones").fetchone()["c"],
        "multas": conn.execute("SELECT COUNT(*) c FROM multas").fetchone()["c"],
        "recaudado_multas": conn.execute("SELECT COALESCE(SUM(monto),0) s FROM multas").fetchone()["s"],
        "arrestos": conn.execute("SELECT COUNT(*) c FROM arrestos").fetchone()["c"],
        "armas": conn.execute("SELECT COUNT(*) c FROM armas").fetchone()["c"],
        "personas": conn.execute("SELECT COUNT(*) c FROM personas").fetchone()["c"],
        "oficiales": conn.execute("SELECT COUNT(*) c FROM usuarios WHERE rol != 'Civil'").fetchone()["c"],
    }
    conn.close()

    return render_template(
        "mdt/estadisticas.html",
        totales=totales,
        denuncias_por_tipo=[dict(r) for r in denuncias_por_tipo],
        denuncias_por_mes=[dict(r) for r in denuncias_por_mes],
        investigaciones_por_estado=[dict(r) for r in investigaciones_por_estado],
        multas_por_mes=[dict(r) for r in multas_por_mes],
        arrestos_por_mes=[dict(r) for r in arrestos_por_mes],
        top_oficiales=[dict(r) for r in top_oficiales],
        personas_por_amenaza=[dict(r) for r in personas_por_amenaza],
    )


# ========================================================================
# GENERADOR PDF (ReportLab) — usado por denuncias y multas
# ========================================================================
def generar_pdf_constancia(ruta_salida, titulo, campos, descripcion=""):
    doc = SimpleDocTemplate(ruta_salida, pagesize=A4, topMargin=2 * cm, bottomMargin=2 * cm)
    styles = getSampleStyleSheet()
    estilo_titulo = ParagraphStyle(
        "TituloLSPD", parent=styles["Title"], fontSize=16, alignment=TA_CENTER,
        textColor=colors.HexColor("#0d1b2a"), spaceAfter=6
    )
    estilo_sub = ParagraphStyle(
        "SubLSPD", parent=styles["Normal"], fontSize=10, alignment=TA_CENTER,
        textColor=colors.HexColor("#555555"), spaceAfter=20
    )

    elementos = []
    elementos.append(Paragraph("LOS SANTOS POLICE DEPARTMENT", estilo_titulo))
    elementos.append(Paragraph("Documento Oficial Generado por el Sistema MDT", estilo_sub))
    elementos.append(Paragraph(titulo, styles["Heading2"]))
    elementos.append(Spacer(1, 12))

    tabla_datos = [[k, v] for k, v in campos]
    tabla = Table(tabla_datos, colWidths=[5 * cm, 10 * cm])
    tabla.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#0d1b2a")),
        ("TEXTCOLOR", (0, 0), (0, -1), colors.white),
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("PADDING", (0, 0), (-1, -1), 6),
    ]))
    elementos.append(tabla)
    elementos.append(Spacer(1, 18))

    if descripcion:
        elementos.append(Paragraph("Descripción:", styles["Heading3"]))
        elementos.append(Paragraph(descripcion.replace("\n", "<br/>"), styles["Normal"]))
        elementos.append(Spacer(1, 18))

    elementos.append(Spacer(1, 40))
    elementos.append(Paragraph(f"Generado el {datetime.now().strftime('%Y-%m-%d %H:%M')}", styles["Normal"]))
    elementos.append(Paragraph("Documento válido únicamente con sello y firma de la institución.", styles["Italic"]))

    doc.build(elementos)


# ========================================================================
# PANEL DE ADMINISTRACIÓN /admin/panel — solo AdminWeb y Jefe
# ========================================================================
@app.route("/admin/panel")
@admin_required
def admin_panel():
    conn = get_connection()
    total_usuarios = conn.execute("SELECT COUNT(*) c FROM usuarios").fetchone()["c"]
    total_postulaciones_pendientes = conn.execute(
        "SELECT COUNT(*) c FROM postulaciones WHERE estado = 'Pendiente'"
    ).fetchone()["c"]
    total_contactos = conn.execute("SELECT COUNT(*) c FROM contactos WHERE leido = 0").fetchone()["c"]
    conn.close()
    return render_template(
        "admin/panel.html",
        total_usuarios=total_usuarios,
        total_postulaciones_pendientes=total_postulaciones_pendientes,
        total_contactos=total_contactos,
    )


# ------------------------- GESTIÓN DE USUARIOS ---------------------------
@app.route("/admin/usuarios")
@admin_required
def admin_usuarios():
    conn = get_connection()
    usuarios = conn.execute("SELECT * FROM usuarios ORDER BY id DESC").fetchall()
    conn.close()
    return render_template("admin/usuarios.html", usuarios=usuarios, roles_lspd=ROLES_LSPD, rol_civil=ROL_CIVIL)


@app.route("/admin/usuarios/nuevo", methods=["POST"])
@admin_required
def admin_usuario_nuevo():
    placa = request.form.get("placa", "").strip()
    nombre = request.form.get("nombre", "").strip()
    apellido = request.form.get("apellido", "").strip()
    password = request.form.get("password", "")
    rol = request.form.get("rol", "Civil")

    conn = get_connection()
    existe = conn.execute("SELECT id FROM usuarios WHERE placa = ?", (placa,)).fetchone()
    if existe:
        conn.close()
        flash("Ya existe un usuario con esa placa/DNI.", "danger")
        return redirect(url_for("admin_usuarios"))

    pw_hash = generate_password_hash(password)
    conn.execute("""
        INSERT INTO usuarios (placa, nombre, apellido, password_hash, rol, fecha_ingreso, activo)
        VALUES (?, ?, ?, ?, ?, ?, 1)
    """, (placa, nombre, apellido, pw_hash, rol, datetime.now().strftime("%Y-%m-%d")))
    conn.commit()
    conn.close()
    log_accion(session["usuario"]["id"], f"Creó usuario {placa}")
    flash("Usuario creado correctamente.", "success")
    return redirect(url_for("admin_usuarios"))


@app.route("/admin/usuarios/<int:user_id>/editar", methods=["POST"])
@admin_required
def admin_usuario_editar(user_id):
    conn = get_connection()
    nombre = request.form.get("nombre", "")
    apellido = request.form.get("apellido", "")
    rol = request.form.get("rol", "Civil")
    activo = 1 if request.form.get("activo") == "on" else 0
    nueva_password = request.form.get("password", "").strip()

    if nueva_password:
        pw_hash = generate_password_hash(nueva_password)
        conn.execute("""
            UPDATE usuarios SET nombre=?, apellido=?, rol=?, activo=?, password_hash=? WHERE id=?
        """, (nombre, apellido, rol, activo, pw_hash, user_id))
    else:
        conn.execute("""
            UPDATE usuarios SET nombre=?, apellido=?, rol=?, activo=? WHERE id=?
        """, (nombre, apellido, rol, activo, user_id))
    conn.commit()
    conn.close()
    log_accion(session["usuario"]["id"], f"Editó usuario ID {user_id} (rol: {rol})")
    flash("Usuario actualizado.", "success")
    return redirect(url_for("admin_usuarios"))


@app.route("/admin/usuarios/<int:user_id>/eliminar", methods=["POST"])
@admin_required
def admin_usuario_eliminar(user_id):
    if user_id == session["usuario"]["id"]:
        flash("No puedes eliminar tu propio usuario.", "danger")
        return redirect(url_for("admin_usuarios"))
    conn = get_connection()
    conn.execute("DELETE FROM usuarios WHERE id = ?", (user_id,))
    conn.commit()
    conn.close()
    flash("Usuario eliminado.", "info")
    return redirect(url_for("admin_usuarios"))


# ------------------------- GESTIÓN DE CATEGORÍAS --------------------------
@app.route("/admin/categorias", methods=["GET", "POST"])
@admin_required
def admin_categorias():
    conn = get_connection()
    if request.method == "POST":
        tabla = request.form.get("tabla")
        nombre = request.form.get("nombre", "").strip()
        monto = request.form.get("monto_sugerido", 0)
        if tabla == "denuncia" and nombre:
            conn.execute("INSERT OR IGNORE INTO categorias_denuncia (nombre) VALUES (?)", (nombre,))
        elif tabla == "investigacion" and nombre:
            conn.execute("INSERT OR IGNORE INTO categorias_investigacion (nombre) VALUES (?)", (nombre,))
        elif tabla == "multa" and nombre:
            conn.execute("INSERT OR IGNORE INTO categorias_multa (nombre, monto_sugerido) VALUES (?, ?)",
                         (nombre, float(monto or 0)))
        conn.commit()
        flash("Categoría agregada.", "success")
        return redirect(url_for("admin_categorias"))

    cat_denuncia = conn.execute("SELECT * FROM categorias_denuncia ORDER BY nombre").fetchall()
    cat_investigacion = conn.execute("SELECT * FROM categorias_investigacion ORDER BY nombre").fetchall()
    cat_multa = conn.execute("SELECT * FROM categorias_multa ORDER BY nombre").fetchall()
    conn.close()
    return render_template("admin/categorias.html", cat_denuncia=cat_denuncia,
                            cat_investigacion=cat_investigacion, cat_multa=cat_multa)


@app.route("/admin/categorias/<tabla>/<int:cat_id>/eliminar", methods=["POST"])
@admin_required
def admin_categoria_eliminar(tabla, cat_id):
    tablas_validas = {
        "denuncia": "categorias_denuncia",
        "investigacion": "categorias_investigacion",
        "multa": "categorias_multa",
    }
    if tabla not in tablas_validas:
        abort(404)
    conn = get_connection()
    conn.execute(f"DELETE FROM {tablas_validas[tabla]} WHERE id = ?", (cat_id,))
    conn.commit()
    conn.close()
    flash("Categoría eliminada.", "info")
    return redirect(url_for("admin_categorias"))


# ------------------------- GESTIÓN DE ESTADOS ------------------------------
@app.route("/admin/estados", methods=["GET", "POST"])
@admin_required
def admin_estados():
    conn = get_connection()
    if request.method == "POST":
        tipo = request.form.get("tipo")
        nombre = request.form.get("nombre", "").strip()
        if tipo and nombre:
            conn.execute("INSERT OR IGNORE INTO estados (tipo, nombre) VALUES (?, ?)", (tipo, nombre))
            conn.commit()
            flash("Estado agregado.", "success")
        return redirect(url_for("admin_estados"))

    estados_denuncia = conn.execute("SELECT * FROM estados WHERE tipo='denuncia' ORDER BY id").fetchall()
    estados_investigacion = conn.execute("SELECT * FROM estados WHERE tipo='investigacion' ORDER BY id").fetchall()
    estados_postulacion = conn.execute("SELECT * FROM estados WHERE tipo='postulacion' ORDER BY id").fetchall()
    estados_multa = conn.execute("SELECT * FROM estados WHERE tipo='multa' ORDER BY id").fetchall()
    conn.close()
    return render_template("admin/estados.html", estados_denuncia=estados_denuncia,
                            estados_investigacion=estados_investigacion,
                            estados_postulacion=estados_postulacion, estados_multa=estados_multa)


@app.route("/admin/estados/<int:estado_id>/eliminar", methods=["POST"])
@admin_required
def admin_estado_eliminar(estado_id):
    conn = get_connection()
    conn.execute("DELETE FROM estados WHERE id = ?", (estado_id,))
    conn.commit()
    conn.close()
    flash("Estado eliminado.", "info")
    return redirect(url_for("admin_estados"))


# ------------------------- EDITOR PORTAL CIVIL / MDT -----------------------
@app.route("/admin/config/<seccion>", methods=["GET", "POST"])
@admin_required
def admin_config(seccion):
    if seccion not in ("public", "mdt"):
        abort(404)
    conn = get_connection()
    if request.method == "POST":
        # Las claves conocidas de tipo checkbox no llegan en request.form si están
        # desmarcadas, así que hay que forzarlas a "0" explícitamente.
        claves_checkbox = {"postulaciones_activas"}
        existentes = conn.execute("SELECT clave FROM configuracion WHERE seccion = ?", (seccion,)).fetchall()
        claves_existentes = {row["clave"] for row in existentes}

        for clave, valor in request.form.items():
            if clave == "csrf_token":
                continue
            conn.execute("INSERT OR REPLACE INTO configuracion (clave, valor, seccion) VALUES (?, ?, ?)",
                         (clave, valor, seccion))

        for clave in claves_checkbox & claves_existentes:
            if clave not in request.form:
                conn.execute("INSERT OR REPLACE INTO configuracion (clave, valor, seccion) VALUES (?, ?, ?)",
                             (clave, "0", seccion))

        conn.commit()
        flash("Configuración actualizada.", "success")
        conn.close()
        return redirect(url_for("admin_config", seccion=seccion))

    config = conn.execute("SELECT * FROM configuracion WHERE seccion = ?", (seccion,)).fetchall()
    noticias = conn.execute("SELECT * FROM noticias ORDER BY id DESC").fetchall() if seccion == "public" else []
    conn.close()
    return render_template("admin/configuracion.html", config=config, seccion=seccion, noticias=noticias)


@app.route("/admin/noticias/nueva", methods=["POST"])
@admin_required
def admin_noticia_nueva():
    titulo = request.form.get("titulo", "").strip()
    contenido = request.form.get("contenido", "").strip()
    if titulo:
        conn = get_connection()
        conn.execute("""
            INSERT INTO noticias (titulo, contenido, imagen, fecha, publicado)
            VALUES (?, ?, '', ?, 1)
        """, (titulo, contenido, datetime.now().strftime("%Y-%m-%d %H:%M")))
        conn.commit()
        conn.close()
        flash("Noticia publicada.", "success")
    return redirect(url_for("admin_config", seccion="public"))


@app.route("/admin/noticias/<int:noticia_id>/eliminar", methods=["POST"])
@admin_required
def admin_noticia_eliminar(noticia_id):
    conn = get_connection()
    conn.execute("DELETE FROM noticias WHERE id = ?", (noticia_id,))
    conn.commit()
    conn.close()
    flash("Noticia eliminada.", "info")
    return redirect(url_for("admin_config", seccion="public"))


# ------------------------- GESTIÓN DE POSTULACIONES -------------------------
@app.route("/admin/postulaciones")
@admin_required
def admin_postulaciones():
    conn = get_connection()
    postulaciones = conn.execute("SELECT * FROM postulaciones ORDER BY id DESC").fetchall()
    conn.close()
    return render_template("admin/postulaciones.html", postulaciones=postulaciones)


@app.route("/admin/postulaciones/<int:post_id>/aprobar", methods=["POST"])
@admin_required
def admin_postulacion_aprobar(post_id):
    conn = get_connection()
    postulacion = conn.execute("SELECT * FROM postulaciones WHERE id = ?", (post_id,)).fetchone()
    if not postulacion:
        conn.close()
        abort(404)

    conn.execute("UPDATE postulaciones SET estado = 'Aprobada' WHERE id = ?", (post_id,))

    # Si el postulante tiene usuario civil vinculado, se asciende a Cadete
    if postulacion["id_usuario_civil"]:
        conn.execute("UPDATE usuarios SET rol = 'Cadete' WHERE id = ? AND rol = 'Civil'",
                     (postulacion["id_usuario_civil"],))
    conn.commit()
    conn.close()
    log_accion(session["usuario"]["id"], f"Aprobó postulación #{post_id}")
    flash("Postulación aprobada. El usuario ahora es Cadete (si tenía cuenta vinculada).", "success")
    return redirect(url_for("admin_postulaciones"))


@app.route("/admin/postulaciones/<int:post_id>/rechazar", methods=["POST"])
@admin_required
def admin_postulacion_rechazar(post_id):
    conn = get_connection()
    conn.execute("UPDATE postulaciones SET estado = 'Rechazada' WHERE id = ?", (post_id,))
    conn.commit()
    conn.close()
    flash("Postulación rechazada.", "info")
    return redirect(url_for("admin_postulaciones"))


# ------------------------- LOGS Y BACKUP ------------------------------------
@app.route("/admin/logs")
@admin_required
def admin_logs():
    conn = get_connection()
    logs = conn.execute("""
        SELECT l.*, u.nombre as usuario_nombre, u.apellido as usuario_apellido, u.placa
        FROM logs l LEFT JOIN usuarios u ON l.id_usuario = u.id
        ORDER BY l.id DESC LIMIT 300
    """).fetchall()
    conn.close()
    return render_template("admin/logs.html", logs=logs)


@app.route("/admin/backup")
@admin_required
def admin_backup():
    """
    Ahora los datos viven en varios archivos .db separados (uno por dominio:
    denuncias.db, arrestos.db, bandas.db, etc. dentro de db/). El backup
    empaqueta todos esos archivos en un único .zip descargable.
    """
    if not os.path.isdir(DB_DIR):
        abort(404)

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for filename in sorted(os.listdir(DB_DIR)):
            if filename.endswith(".db"):
                zf.write(os.path.join(DB_DIR, filename), arcname=filename)
    buffer.seek(0)

    log_accion(session["usuario"]["id"], "Descargó backup de las bases de datos (.zip con todos los .db)")
    return send_file(
        buffer,
        as_attachment=True,
        mimetype="application/zip",
        download_name=f"lspd_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip",
    )


# ========================================================================
# ARRANQUE DE LA APLICACIÓN
# ========================================================================
if __name__ == "__main__":
    init_db()
    # El modo debug de Flask/Werkzeug expone una consola interactiva en el
    # navegador ante cualquier error — si el puerto queda accesible desde
    # internet, eso permite ejecutar código arbitrario en el servidor. Por
    # eso queda APAGADO por defecto; solo se activa si vos mismo exportás
    # FLASK_DEBUG=1 en tu máquina de desarrollo.
    debug_mode = os.environ.get("FLASK_DEBUG", "0") == "1"
    puerto = int(os.environ.get("PORT", 5000))
    app.run(debug=debug_mode, host="0.0.0.0", port=puerto)
