# LSPD CMS — Versión PHP + MySQL (para ByetHost)

Portal Público Civil + MDT Interna LSPD + Panel de Administración, reescrito en **PHP puro + MySQL** para poder alojarse en hosting gratuito tipo ByetHost (que no soporta Python/Flask).

---

## ⚠️ Aviso importante sobre esta versión

Esta reescritura se hizo **sin poder ejecutar PHP** en el entorno donde se generó (no había intérprete de PHP ni acceso a internet para instalar uno). Se revisó manualmente con mucho cuidado — balance de llaves/paréntesis en los 79 archivos, cada `require` verificado contra el archivo real, cada consulta SQL preparada contra la cantidad de parámetros que recibe — pero **no reemplaza probarlo en un entorno real**.

**Antes de subir esto a ByetHost, probalo localmente.** Es rápido:

### Prueba local rápida (XAMPP / MAMP / Laragon)

1. Instalá [XAMPP](https://www.apachefriends.org/) (Windows/Linux) o [MAMP](https://www.mamp.info/) (Mac).
2. Copiá la carpeta completa `lspd-php/` dentro de `htdocs/` (XAMPP) o `htdocs/` (MAMP).
3. Iniciá Apache y MySQL desde el panel de XAMPP/MAMP.
4. Entrá a `http://localhost/phpmyadmin`, creá una base de datos (ej. `lspd`), y en la pestaña "Importar" subí `schema.sql`.
5. Editá `config.php`: poné `DB_HOST = 'localhost'`, `DB_NAME = 'lspd'`, `DB_USER = 'root'`, `DB_PASS = ''` (contraseña vacía es el default de XAMPP).
6. Visitá `http://localhost/lspd-php/install.php` una vez (crea el usuario admin).
7. Borrá `install.php`.
8. Entrá a `http://localhost/lspd-php/public/index.php` y probá todo el sitio: registro, login, crear una denuncia, el panel admin, etc.

Si algo falla ahí, es mucho más fácil de diagnosticar en tu máquina que a ciegas en ByetHost. Una vez que ande bien local, subí los mismos archivos a ByetHost.

---

## 🚀 Despliegue en ByetHost

### 1. Crear la cuenta y el sitio

1. Registrate en [byet.host](https://byet.host) y creá tu sitio (te da un subdominio gratis tipo `tunombre.byethost7.com`, o podés apuntar tu propio dominio).
2. Esperá a que la cuenta se active (a veces tarda unos minutos/horas).

### 2. Crear la base de datos MySQL

1. Entrá al **vPanel/cPanel** de ByetHost → sección **MySQL Databases**.
2. Creá una base de datos (ByetHost le agrega un prefijo automático, tipo `b7_12345678_lspd`).
3. Creá un usuario MySQL y asignale **todos los permisos** sobre esa base.
4. Anotá los 4 datos: host (casi siempre `localhost`), nombre de la base, usuario, contraseña.

### 3. Importar el esquema

1. Desde el panel, abrí **phpMyAdmin**.
2. Seleccioná tu base de datos → pestaña **Importar** → subí el archivo `schema.sql` de este proyecto.
3. Esto crea todas las tablas y siembra categorías/estados/configuración por defecto (el usuario admin se crea aparte, ver paso 5).

**¿Ya tenías el sistema instalado de antes?** Si tu base de datos ya existía antes del sistema mejorado de licencias de armas, no reimportes `schema.sql` entero (fallaría por tablas duplicadas) — en su lugar, importá `migracion_armas.sql`, que solo agrega lo nuevo (columnas de licencia, tabla de historial, estados "Cedida"/"Retirada") sin tocar tus datos existentes. Instalaciones nuevas no necesitan este paso, ya viene incluido en `schema.sql`.

### 4. Subir los archivos

1. Comprimí el contenido de la carpeta `lspd-php/` (todo lo que está DENTRO, no la carpeta en sí).
2. Subilo por **File Manager** del panel (botón "Upload", después "Extract") a la carpeta `htdocs/` o `public_html/` de tu cuenta (el nombre exacto varía; ByetHost normalmente usa `htdocs/`).
3. Alternativa: subí por FTP (te dan los datos de FTP en el panel) usando FileZilla.

### 5. Configurar y terminar la instalación

1. Editá `config.php` (por File Manager, botón "Edit", o re-subiendo el archivo ya editado) con los 4 datos de MySQL del paso 2.
2. Visitá `https://tudominio.com/install.php` **una sola vez** — esto crea el usuario administrador inicial.
3. **Borrá `install.php` del servidor** apenas termine (ya cumplió su función; dejarlo es un riesgo de seguridad).
4. Entrá a `https://tudominio.com/public/index.php` — ese es el portal público. El MDT está en `/mdt/dashboard.php` (pedirá login).

### 6. Usuario inicial

| Placa | Contraseña |
|-------|------------|
| 9999  | admin123   |

**Cambiala inmediatamente** desde `/admin/usuarios.php` una vez que entres.

---

## 🗂️ Estructura del proyecto

```
lspd-php/
├── config.php              # ÚNICO archivo que tenés que editar (datos de MySQL)
├── index.php                 # Redirige la raíz del sitio a public/index.php
├── install.php              # Visitar 1 sola vez, después BORRAR
├── schema.sql                # Importar en phpMyAdmin antes de todo (instalación nueva)
├── migracion_armas.sql        # Importar SOLO si ya tenías el sistema instalado antes
├── descargar.php             # Sirve archivos de uploads/ con control de acceso
├── .htaccess                 # Cabeceras de seguridad + páginas de error
├── includes/
│   ├── bootstrap.php         # Incluido al inicio de cada página (config+auth+CSRF)
│   ├── db.php                 # Conexión PDO a MySQL
│   ├── auth.php                # Sesión, roles, CSRF, rate-limiting
│   ├── functions.php           # Helpers (config, uploads, logs, Turnstile)
│   ├── simplepdf.php            # Generador de PDF sin dependencias externas
│   ├── layout_public_top/bottom.php  # Layout del portal civil (tema claro)
│   ├── layout_mdt_top/bottom.php     # Layout del MDT (tema oscuro)
│   ├── layout_admin_top/bottom.php   # Layout del panel admin
│   └── .htaccess              # Bloquea acceso directo por navegador
├── public/    → Portal civil: index, login, registro, postulaciones, denuncias, se_busca, contacto
├── mdt/       → MDT interna: dashboard, denuncias, personas, bandas, investigaciones,
│                multas, armas, arrestos, asuntos internos, personal, chat, estadísticas
├── admin/     → Panel admin: usuarios, categorías, estados, config, postulaciones, logs, backup
├── static/
│   ├── css/public.css        # Tema claro (portal civil)
│   └── css/style.css         # Tema oscuro (MDT + admin)
└── uploads/
    ├── denuncias/<id>/       # Adjuntos de denuncias
    ├── investigaciones/<id>/ # Adjuntos de investigaciones y fotos BD criminal
    ├── public/                # (reservado)
    └── .htaccess              # Impide ejecutar scripts subidos como si fueran PHP
```

---

## 🔫 Registro de Armas y Licencias

El módulo de armas (`/mdt/armas.php`) funciona como un registro de licencias en regla, no solo una lista de objetos:

- **Datos completos del arma**: tipo (permiso civil / incautada), categoría (Pistola, Revólver, Rifle, Escopeta, Subfusil, Otro), marca, modelo, número de serie, calibre, color, país de origen.
- **Datos de la licencia**: número de permiso, fecha de emisión, fecha de vencimiento (el listado marca automáticamente con un badge "Vencida" las licencias activas que ya pasaron su fecha), titular actual.
- **Ceder** (`mdt/armas_ceder.php`): transfiere la titularidad a otra persona registrada en la base criminal. El arma pasa a estado "Cedida" y queda un registro permanente de quién la tenía antes y quién la recibe.
- **Dar de baja** (`mdt/armas_baja.php`): retira definitivamente la licencia (por revocación, entrega voluntaria, destrucción, etc.), pidiendo motivo obligatorio, y queda guardada la fecha y el oficial que la dio de baja.
- **Historial completo** (visible en la ficha de detalle, `mdt/armas_ver.php`): cada evento — alta, cesión, baja — queda registrado con fecha, oficial responsable y de/hacia quién, para tener trazabilidad total de la licencia a lo largo del tiempo.
- **Certificado en PDF**: cada arma/licencia tiene un botón para descargar un certificado oficial con todos sus datos.

---

## 🔀 Diferencias clave respecto a la versión Flask/Python

| Aspecto | Versión Flask (Python) | Esta versión (PHP) |
|---|---|---|
| Base de datos | 10 archivos SQLite separados | **1 sola base MySQL** (limitación de las cuentas gratuitas de ByetHost, que solo permiten 1 base) |
| Hash de contraseñas | `werkzeug.security` | `password_hash()` nativo de PHP (bcrypt) |
| Rate limiting | Diccionario en memoria del proceso | Tabla `rate_limit_hits` en MySQL (cada request PHP es un proceso nuevo, no hay memoria compartida) |
| PDF | ReportLab | Generador propio minimalista (sin librerías externas — ver nota abajo) |
| CSRF | Flask-session + JS de auto-inyección | Sesión PHP nativa + campo oculto explícito en cada formulario |
| Backup | Copia de los .db + zip | Export SQL generado por PHP (no requiere mysqldump/SSH) |
| Servidor | `python app.py` (proceso propio) | Apache + PHP de ByetHost (sin proceso propio que mantener) |

### Sobre el generador de PDF

No se usó FPDF/TCPDF (las librerías PHP más comunes para esto) porque no había forma de probar que la instalación/inclusión funcionara correctamente en este entorno sin PHP disponible. En su lugar, `includes/simplepdf.php` arma un PDF válido "a mano" (texto plano con Helvetica, sin imágenes ni tablas complejas) — suficiente para las constancias y boletas del sistema, y con cero dependencias que puedan fallar al subir a un hosting compartido.

---

## 🔒 Seguridad incluida

- Contraseñas con `password_hash()` (bcrypt nativo de PHP).
- CSRF: token de sesión validado en todo POST (`includes/auth.php` → `verificarCsrf()`).
- Rate limiting en login/registro/contacto/denuncia pública/chat (tabla `rate_limit_hits`).
- Bloqueo de cuenta tras 5 logins fallidos seguidos (15 minutos).
- Cabeceras de seguridad HTTP (`X-Frame-Options`, `Content-Security-Policy`, etc.) vía PHP y `.htaccess`.
- CAPTCHA opcional (Cloudflare Turnstile) en login/registro/contacto/denuncia — configurable en `config.php`.
- `.htaccess` en `uploads/` impide que se ejecute cualquier script subido (aunque la validación de extensión ya lo impide en primer lugar).
- `.htaccess` en `includes/` bloquea el acceso directo por navegador a los archivos internos.
- Consultas SQL siempre parametrizadas (PDO prepared statements) — sin inyección SQL.

### Cloudflare Turnstile (CAPTCHA) — opcional

Sacá tus claves gratis en [dash.cloudflare.com](https://dash.cloudflare.com) → Turnstile → "Add a site", y pegalas en `config.php`:

```php
define('TURNSTILE_SITE_KEY', 'tu_site_key');
define('TURNSTILE_SECRET_KEY', 'tu_secret_key');
```

Si las dejás vacías (`''`), el CAPTCHA simplemente no aparece — el sitio funciona igual.

---

## 👥 Roles del sistema

- **Civil** — rol por defecto al registrarse. Solo ve el portal público. Si intenta entrar a `/mdt/` o `/admin/` recibe **403**.
- **Cadete, Oficial, Sargento, Teniente, Capitán, Jefe, AdminWeb** — roles LSPD, todos acceden al MDT.
- **Jefe** y **AdminWeb** — además acceden al panel de administración y a Asuntos Internos.

---

## 🧩 Notas para producción en ByetHost

- **Zona horaria**: ajustá `date_default_timezone_set()` en `config.php` a tu país.
- **Cron jobs**: ByetHost no suele dar cron en cuentas gratuitas — no hace falta, todo el sistema funciona por visitas normales a las páginas (el chat se actualiza solo vía JavaScript, sin necesitar tareas programadas).
- **Backups**: descargalos regularmente desde `/admin/backup.php` (genera un `.sql`) y guardalos fuera del hosting.
- **Límite de 1 base de datos**: si tu plan de ByetHost permite más de una base en el futuro, technically podrías separar tablas en varias bases (usando `nombre_base.tabla` en las consultas) — pero no es necesario, un solo MySQL maneja perfectamente este volumen de datos.
- **HTTPS**: ByetHost ofrece SSL gratis (Let's Encrypt) desde el panel — activalo, y si querés forzar HTTPS agregá la regla correspondiente a tu `.htaccess`.

---

## 🛠️ Solución de problemas comunes

### Error 403 al entrar a tu dominio

Causa: no había ningún `index.php` en la raíz del sitio, y el `.htaccess` tiene `Options -Indexes` (bloquea el listado de carpetas) — al no encontrar qué mostrar, Apache devuelve 403 en vez de la página. **Ya está solucionado**: el proyecto incluye un `index.php` en la raíz que redirige automáticamente a `public/index.php`. Si ya habías subido una versión anterior sin este archivo, solo hace falta subir el `index.php` nuevo a la raíz (junto a `config.php`).

Si el 403 persiste después de subirlo, revisá:
- Que `index.php` haya quedado en la raíz de `htdocs/`/`public_html/` (no dentro de una subcarpeta).
- Los permisos de archivos/carpetas por FTP: carpetas en `755`, archivos en `644` (ByetHost a veces es estricto con esto).
- Si tu Apache no soporta la sintaxis `Require all denied` (Apache 2.4), vas a ver error 500 en vez de 403 al entrar a `includes/` — en ese caso, abrí `includes/.htaccess` y cambiá esa línea por la alternativa comentada (`Deny from all`, sintaxis de Apache 2.2) que ya está incluida como referencia en el mismo archivo.

### Una carpeta no se sube (por ejemplo `static/js`)

Los clientes FTP y algunos administradores de archivos (incluido el de ByetHost) no suben carpetas completamente vacías. `static/js/` no tiene ningún archivo real (todo el JavaScript del sistema vive dentro de cada página `.php`, no en archivos `.js` separados) — no afecta el funcionamiento del sitio si no se sube. El proyecto incluye un archivo `.gitkeep` dentro para que la carpeta viaje igual en el `.zip`, pero si tu herramienta de subida la sigue salteando, no pasa nada: podés crearla vacía a mano en el servidor o simplemente ignorarla.

