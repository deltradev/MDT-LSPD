# LSPD CMS - Los Santos Police Department Content Management System

Sistema completo de gestión para Los Santos Police Department con portal civil y MDT interno.

## 🚀 Características

### Portal Civil (Público)
- ✅ Acceso sin autenticación
- ✅ Registro de denuncias ciudadanas
- ✅ Postulaciones a LSPD
- ✅ Lista de Se Busca (público)
- ✅ Contacto directo
- ✅ Interfaz amigable tema claro

### MDT Interno (LSPD)
- ✅ Dashboard con estadísticas
- ✅ CRUD de Denuncias con PDF
- ✅ Base de Datos Criminal (Personas/Bandas)
- ✅ Investigaciones con notas
- ✅ Gestión de Multas con PDF
- ✅ BOLO (Se Busca activos)
- ✅ Interfaz profesional tema oscuro

### Panel Administrativo
- ✅ Gestión de usuarios y roles
- ✅ Aprobación de postulaciones
- ✅ Gestión de categorías dinámicas
- ✅ Configuración del sistema
- ✅ Visualización de logs
- ✅ Acceso solo para AdminWeb/Jefe

## 📋 Requisitos

Python 3.8+, pip3, SQLite3

## 🔧 Instalación Rápida

```bash
# 1. Clonar
git clone https://github.com/deltradev/MDT-LSPD.git
cd MDT-LSPD

# 2. Entorno virtual
python3 -m venv venv
source venv/bin/activate  # Linux/Mac
# o
venv\Scripts\activate  # Windows

# 3. Dependencias
pip install -r requirements.txt

# 4. Ejecutar
python app.py
```

**Acceso**: http://localhost:5000

## 🔐 Credenciales Iniciales

- **Placa**: 9999
- **Contraseña**: admin123
- **Rol**: AdminWeb

## 👥 Roles del Sistema

| Rol | Descripción |
|-----|-------------|
| Civil | Ciudadano (Portal Público) |
| Cadete | Nuevo oficial en entrenamiento |
| Oficial | Oficial de policía |
| Sargento | Supervisor |
| Teniente | Oficial superior |
| Capitán | Comandante de unidad |
| Jefe | Jefe del departamento |
| AdminWeb | Administrador del sistema |

## 📁 Estructura

```
MDT-LSPD/
├── app.py
├── database.py
├── requirements.txt
├── README.md
├── .gitignore
├── uploads/
├── static/css/
└── templates/
    ├── public/
    ├── mdt/
    └── admin/
```

## 🔗 Rutas Principales

**Portal Civil**: `/` `/login` `/register` `/denuncia` `/postulacion` `/se-busca` `/contacto`

**MDT**: `/mdt` `/mdt/denuncias` `/mdt/personas` `/mdt/bandas` `/mdt/investigaciones` `/mdt/multas` `/mdt/bolo`

**Admin**: `/admin/panel` `/admin/usuarios` `/admin/postulaciones` `/admin/categorias` `/admin/configuracion` `/admin/logs`

## 🔒 Seguridad

- Contraseñas hasheadas con bcrypt
- Validación de roles en cada ruta
- CSRF protection con Flask-WTF
- Límite de upload 50MB
- Sanitización de archivos

## 📝 Stack

**Python 3, Flask, Jinja2, SQLite3, Bootstrap 5, ReportLab, bcrypt, Werkzeug, Flask-WTF**

---

**Versión**: 1.0 | **Autor**: Senior Full-Stack Developer | **Licencia**: LSPD Authorization