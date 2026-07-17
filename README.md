# LSPD CMS — Portal Público Civil + MDT Interna

Aplicación Flask completa con dos portales:

1. **Portal Público Civil** (`/`) — accesible sin login para cualquier ciudadano.
2. **MDT Interna LSPD** (`/mdt`) — accesible solo para personal LSPD autenticado.
3. **Panel de Administración** (`/admin/panel`) — solo para `Jefe` y `AdminWeb`.

Todo con CMS: noticias, categorías, estados y textos de ambos portales son editables desde el panel de administración sin tocar código.

---

## 🚀 Instalación

### En un PC (Linux/Mac/Windows)

```bash
# 1. Crear entorno virtual (recomendado)
python3 -m venv venv
source venv/bin/activate        # En Windows: venv\Scripts\activate

# 2. Instalar dependencias
pip install -r requirements.txt

# 3. Ejecutar la aplicación
python app.py
```

### 📱 En Termux (Android)

La app es 100% compatible con Termux: no usa `bcrypt` (requiere compilar con Rust y suele fallar en Termux); en su lugar usa `werkzeug.security`, que es Python puro y no necesita compilación.

```bash
# 1. Actualizar Termux e instalar Python
pkg update && pkg upgrade -y
pkg install python -y

# 2. (Opcional) descomprimir el proyecto si lo pasaste como .zip
#    pkg install unzip -y  &&  unzip lspd-cms.zip  &&  cd lspd-cms
cd lspd-cms

# 3. Actualizar pip e instalar dependencias
pip install --upgrade pip
pip install -r requirements.txt

# 4. Ejecutar la aplicación
python app.py
```

La app queda escuchando en `http://127.0.0.1:5000` (y también en `0.0.0.0:5000`).
Abre ese enlace desde el navegador del propio teléfono (Chrome, Firefox, etc.) mientras Termux siga corriendo en segundo plano.

**Notas para Termux:**
- No necesitas `termux-setup-storage` a menos que quieras copiar el `.db` o los archivos subidos fuera de la carpeta del proyecto.
- Si `pip install reportlab` se ve lento, es normal la primera vez: descarga e instala el paquete puro-Python, no requiere compilar nada.
- Para mantener el servidor corriendo aunque bloquees la pantalla, activa la "wake lock" de Termux (menú deslizable desde el borde izquierdo → Acquire wakelock), o usa `termux-wake-lock` desde otra sesión.
- Para acceder desde otro dispositivo de tu misma red (no solo el propio teléfono), usa la IP local del teléfono en vez de `127.0.0.1`, por ejemplo `http://192.168.x.x:5000` (puedes ver la IP con `ip addr` o `ifconfig` si tienes `pkg install net-tools`).

---

## 🔑 Usuario inicial

| Placa | Contraseña | Rol      |
|-------|------------|----------|
| 9999  | admin123   | AdminWeb |

Con este usuario puedes entrar directamente a `/mdt` y a `/admin/panel` para configurar el resto del sistema (crear más usuarios LSPD, categorías, etc.).

**Importante:** cambia esta contraseña en producción desde `/admin/usuarios`.

---

## 👥 Roles del sistema

- **Civil** — rol por defecto al registrarse. Solo accede al portal público (`/`). Si intenta entrar a `/mdt` recibe un error **403**.
- **Cadete, Oficial, Sargento, Teniente, Capitán, Jefe, AdminWeb** — roles LSPD. Todos pueden entrar a `/mdt`.
- **Jefe** y **AdminWeb** — además tienen acceso al panel `/admin/panel`.

Un Civil puede convertirse en Cadete si postula desde `/postulaciones` y un administrador aprueba su postulación desde `/admin/postulaciones`.

---

## 🗂️ Estructura del proyecto

```
lspd-cms/
├── app.py                  # Rutas, lógica de negocio, decoradores de rol
├── database.py              # Creación de tablas + datos semilla (multi-archivo)
├── requirements.txt
├── README.md
├── db/                       # Se genera automáticamente — 1 archivo .db por dominio
│   ├── usuarios.db           # usuarios (login, roles, personal)
│   ├── denuncias.db          # denuncias + archivos_denuncia
│   ├── personas.db           # base de datos criminal (personas)
│   ├── bandas.db             # base de datos criminal (bandas)
│   ├── investigaciones.db    # investigaciones + notas_investigacion
│   ├── multas.db             # multas
│   ├── armas.db              # registro de armas
│   ├── arrestos.db           # arrestos / detenciones
│   ├── interno.db            # ascensos, certificaciones, asuntos internos, chat
│   └── sistema.db            # configuración, categorías, estados, logs, noticias, contactos, postulaciones
├── uploads/
│   ├── denuncias/<id>/      # Adjuntos de denuncias LSPD
│   ├── investigaciones/<id>/# Adjuntos de investigaciones y fotos BD criminal
│   └── public/<id>/         # CVs de postulaciones
├── static/
│   ├── css/style.css        # Tema oscuro (MDT + Admin)
│   ├── css/public.css       # Tema claro (Portal Civil)
│   └── js/main.js
└── templates/
    ├── public/               # Vistas del portal civil
    ├── mdt/                  # Vistas de la MDT interna
    └── admin/                # Vistas del panel de administración
```

### 🔀 Sobre la separación en varios archivos .db

Cada tipo de dato que se registra en la app se guarda en su propio archivo SQLite físico dentro de `db/` (por ejemplo, toda denuncia creada queda en `db/denuncias.db`, toda persona de la base criminal en `db/personas.db`, etc.). Internamente, `database.py` conecta todos esos archivos a la vez usando `ATTACH DATABASE`, así que las consultas (incluyendo los `JOIN` entre, por ejemplo, una denuncia y el oficial que la creó) siguen funcionando con normalidad aunque los datos vivan en archivos distintos.

**Nota técnica:** SQLite permite adjuntar como máximo 10 bases de datos por conexión. Como el sistema ya tiene más de 10 dominios de datos, los de menor volumen/registro directo (ascensos, certificaciones, asuntos internos, chat interno, categorías, estados, configuración, logs, noticias, contactos y postulaciones) se agrupan en dos archivos de soporte (`interno.db` y `sistema.db`), mientras que los que el usuario carga con más frecuencia (denuncias, arrestos, bandas, personas, armas, multas, investigaciones, usuarios) tienen cada uno su propio archivo dedicado.

---

## 🧩 Módulos principales

### Portal Público (`/`)

- **Inicio**: noticias/anuncios editables desde `/admin/config/public`.
- **Postulaciones** (`/postulaciones`): embebe un **Google Form** externo (configurable desde `/admin/config/public`). El AdminWeb/Jefe puede **abrir o cerrar** las postulaciones con un interruptor, y pegar/cambiar el enlace del formulario en cualquier momento. Ya no se almacenan datos localmente: las respuestas quedan en Google Forms/Sheets. La página vieja de `/admin/postulaciones` sigue disponible para consultas manuales o históricas.
- **Denuncias Ciudadanas** (`/denuncias`): un civil puede denunciar y adjuntar evidencia; si está logueado ve su propio historial.
- **Se Busca** (`/se-busca`): lista personas y bandas marcadas como `es_publico = 1` desde la MDT.
- **Contacto** (`/contacto`).
- **Login / Registro** (`/login`, `/registro`): el registro siempre crea el rol `Civil`.

### MDT Interna (`/mdt`) — requiere rol LSPD
- **Dashboard**: métricas generales, últimas denuncias, BOLOs activos.
- **Denuncias**: CRUD completo, subida de archivos, generación de PDF con ReportLab, categorías dinámicas.
- **Base de Datos Criminal**: CRUD de Personas y Bandas, vínculo persona↔banda, marcador "Público" para BOLO.
- **Investigaciones**: CRUD con notas cronológicas y archivos adjuntos por nota.
- **Multas**: CRUD + generación de PDF de boleta oficial.
- **BOLO** (`/mdt/bolo`): personas/bandas con nivel Alto/Extremo.
- **Registro de Armas** (`/mdt/armas`): permisos civiles y armas incautadas, vinculables a una persona y/o a una denuncia.
- **Arrestos / Detenciones** (`/mdt/arrestos`): registro independiente de las denuncias, con cargos, fianza y estado judicial (Detenido, Libertad Bajo Fianza, Condenado, etc.).
- **Asuntos Internos** (`/mdt/asuntos-internos`) — **solo Jefe y AdminWeb**: reportes confidenciales contra oficiales, con estado y medida disciplinaria aplicada.
- **Personal** (`/mdt/personal`): ficha de cada oficial con historial de ascensos (Jefe/AdminWeb pueden ascender de rango) y certificaciones/cursos (cualquier LSPD puede cargarlas).
- **Chat Interno** (`/mdt/chat`): canal general (broadcast a todo el LSPD) + mensajes directos entre oficiales, con actualización automática cada 4 segundos (polling, sin necesidad de recargar la página).
- **Estadísticas Automáticas** (`/mdt/estadisticas`): gráficos en tiempo real (Chart.js) calculados directamente desde la base de datos — denuncias por tipo/mes, investigaciones por estado, multas por mes y recaudación, arrestos por mes, personas por nivel de amenaza, y ranking de oficiales por denuncias registradas. No requiere carga manual de datos: se recalcula solo en cada visita.

### Panel Admin (`/admin/panel`) — solo Jefe/AdminWeb
- Gestión de usuarios (crear, editar, cambiar rol, desactivar, eliminar).
- Gestión de categorías (denuncias, investigaciones, multas).
- Gestión de estados (denuncia, investigación, postulación, multa).
- Editor del Portal Civil (banner, nombre del departamento, footer, noticias).
- Editor del MDT (título, color primario, texto del dashboard).
- Gestión de postulaciones (aprobar → asciende Civil a Cadete / rechazar).
- Ver logs de acciones del sistema.
- Descargar backup de todas las bases de datos (un .zip con los 10 archivos .db de `db/`).

---

## 🔒 Ciberseguridad implementada

Todo lo de acá está implementado en Python puro (sin librerías nuevas), para seguir siendo 100% compatible con Termux.

- **Contraseñas hasheadas** con `werkzeug.security` (scrypt) — nunca en texto plano.
- **Control de acceso por rol** (`@role_required`): un Civil que intenta `/mdt/*` o `/admin/*` recibe 403.
- **Protección CSRF**: cada sesión tiene un token único; todo `POST/PUT/PATCH/DELETE` sin el token correcto se rechaza con 400. El token se inyecta solo en cada `<form>` automáticamente vía `static/js/csrf.js` — no hace falta tocar las plantillas para que un formulario nuevo quede protegido.
- **Rate limiting** en endpoints sensibles (`/login`, `/registro`, `/contacto`, `/denuncias`, `/mdt/chat/enviar`): un límite de intentos por minuto por IP, para frenar bots y ataques de fuerza bruta/spam automatizado.
- **Bloqueo de cuenta por fuerza bruta**: tras 5 intentos de login fallidos seguidos, la cuenta se bloquea 15 minutos (aunque después se use la contraseña correcta).
- **Cabeceras de seguridad HTTP** en toda respuesta: `X-Frame-Options` (anti clickjacking), `X-Content-Type-Options: nosniff`, `Content-Security-Policy`, `Referrer-Policy`, `Permissions-Policy`, y `Strict-Transport-Security` cuando corre bajo HTTPS.
- **Cookies de sesión endurecidas**: `HttpOnly` (no accesibles desde JS), `SameSite=Lax` (mitiga CSRF cross-site), y `Secure` automático si corre bajo HTTPS.
- **Modo debug apagado por defecto**: antes quedaba `debug=True` fijo, lo cual expone una consola interactiva que permite ejecutar código en el servidor ante cualquier error — ahora requiere `FLASK_DEBUG=1` explícito para activarse.
- **Subida de archivos validada**: extensión controlada (`ALLOWED_EXT`), nombre sanitizado (`secure_filename`), tamaño máximo de 16 MB.
- **Consultas parametrizadas** en toda la app (nunca se arma SQL concatenando texto del usuario) → sin inyección SQL.
- **Auditoría**: intentos de login fallidos, bloqueos y abusos de rate limit quedan registrados en `/admin/logs`.

### Variables de entorno para producción

| Variable | Efecto |
|---|---|
| `LSPD_SECRET_KEY` | Clave secreta de Flask (¡cambiala! sin esto usa una de ejemplo). |
| `FLASK_DEBUG=1` | Activa el modo debug (NUNCA en producción/expuesto a internet). |
| `FORCE_HTTPS=1` | Activa cookies `Secure` y la cabecera HSTS (usalo si tenés HTTPS real, por ejemplo detrás de Cloudflare). |
| `PORT` | Puerto en el que escucha (default 5000). |

---

## ☁️ Cloudflare como capa adicional (recomendado)

Cloudflare **sí sirve** y es un excelente complemento: protege cosas que la app por sí sola no puede (ataques DDoS volumétricos, filtrado de bots a nivel de red, certificado SSL/TLS gratis). Lo que implementé arriba protege lo que Cloudflare no ve (CSRF, fuerza bruta de cuentas específicas, cabeceras de la respuesta). Usados juntos se complementan.

**Cómo activarlo** (necesitás un dominio propio apuntando a tu servidor):
1. Creá una cuenta gratis en cloudflare.com y agregá tu dominio.
2. Cloudflare te da 2 nameservers — cambialos en tu proveedor de dominio (GoDaddy, Namecheap, etc.) por esos.
3. En el panel de Cloudflare, creá un registro DNS tipo `A` apuntando a la IP pública de tu servidor, con el ícono de nube **naranja** (activado = tráfico pasa por Cloudflare, no directo).
4. En **SSL/TLS**, elegí modo **"Full"** o **"Full (strict)"** si tu servidor también tiene su propio certificado.
5. Activá **"Bot Fight Mode"** (gratis) en Security → Bots.
6. Creá una regla de **Rate Limiting** (Security → WAF → Rate limiting rules) para `/login`, `/registro`, etc. — es una segunda capa además de la que ya tiene la app, y esta sí es global (no por proceso).
7. Si sufrís un ataque activo, **"Under Attack Mode"** (Overview → botón naranja) pone un desafío JS a todos los visitantes temporalmente.

**Importante:** la app ya viene configurada con `ProxyFix` para reconocer la IP real del visitante cuando pasa por un proxy como Cloudflare (si no hicieras esto, el rate-limiting y los logs verían siempre la IP de Cloudflare en vez de la del atacante/usuario real).

---

## 📄 Generación de PDF

Se usa **ReportLab** para generar:
- Constancia oficial de denuncia (`/mdt/denuncias/<id>/pdf`).
- Boleta oficial de multa (`/mdt/multas/<id>/pdf`).

Ambos documentos incluyen encabezado institucional LSPD, tabla de datos y fecha de generación.

---

## ⚠️ Notas para producción

- Cambia `app.config["SECRET_KEY"]` (o mejor, exportá `LSPD_SECRET_KEY`) por un valor aleatorio y secreto.
- Cambia la contraseña del usuario `9999` inmediatamente.
- Nunca actives `FLASK_DEBUG=1` en un servidor expuesto a internet.
- Considera migrar de SQLite a PostgreSQL/MySQL si esperas alta concurrencia.
- Ejecuta detrás de un servidor WSGI real (Gunicorn/uWSGI), no con el servidor de desarrollo de Flask.
- Sumá Cloudflare (u otro WAF/CDN) por delante como capa adicional — ver sección de arriba.
