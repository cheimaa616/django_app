"""
Django settings for django_pdf_signer project.
"""

from pathlib import Path
import socket

BASE_DIR = Path(__file__).resolve().parent.parent

# ─────────────────────────────────────────────────────────────
# Détection automatique de l'IP locale du serveur
# Permet au QR Code de contenir une URL joignable depuis le LAN
# ─────────────────────────────────────────────────────────────
def get_local_ip() -> str:
    """
    Retourne l'IP LAN de la machine (ex. 192.168.x.x).
    Fallback sur 127.0.0.1 si aucune interface réseau n'est disponible.
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            # On "connecte" vers une IP publique sans envoyer de paquet
            # pour forcer le choix de l'interface LAN active
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"

LOCAL_IP = get_local_ip()
SERVER_PORT = 8000   # à changer si vous utilisez un autre port

SECRET_KEY = 'django-insecure-(1spp-$v#osig3@3yv%*_qm)6@i0vch%q59d&so!ayf(i*%=zt'

DEBUG = True

# Accepte localhost, 127.0.0.1 ET l'IP LAN détectée automatiquement
ALLOWED_HOSTS = [
    'localhost',
    '127.0.0.1',
    '0.0.0.0',
    LOCAL_IP,
    # Ajoutez ici votre nom de domaine ou IP publique si vous exposez
    # le serveur sur Internet (ex. via ngrok ou un VPS)
    # 'mon-domaine.example.com',
]

# URL de base utilisée pour générer les liens dans les QR Codes
# Pointe automatiquement vers l'IP LAN pour être joignable depuis un téléphone
SITE_URL = f"http://{LOCAL_IP}:{SERVER_PORT}"

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'rest_framework',
    'django_bootstrap5',
    'signer_app',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'django_pdf_signer.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'django_pdf_signer.wsgi.application'

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True

STATIC_URL = 'static/'
STATICFILES_DIRS = [BASE_DIR / 'static']
MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

DATA_UPLOAD_MAX_MEMORY_SIZE = 104857600   # 100 MB
FILE_UPLOAD_MAX_MEMORY_SIZE = 104857600