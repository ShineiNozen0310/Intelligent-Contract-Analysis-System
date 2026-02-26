from pathlib import Path
from dotenv import load_dotenv
import os

BASE_DIR = Path(__file__).resolve().parent.parent

ENV_PATH = BASE_DIR / ".env"
if ENV_PATH.exists():
    load_dotenv(dotenv_path=ENV_PATH, override=True)
    print(f"[ENV] loaded: {ENV_PATH}")
else:
    print("[ENV] .env not found, using system env only")

WKHTMLTOPDF_BIN = os.environ.get("WKHTMLTOPDF_BIN", "").strip().strip('"').strip("'")
if not WKHTMLTOPDF_BIN:
    WKHTMLTOPDF_BIN = str(BASE_DIR / ".venv" / "wkhtmltopdf" / "bin" / "wkhtmltopdf.exe")

SECRET_KEY = os.environ.get("SECRET_KEY", "django-insecure-fallback-dev-key")
# 调试模式（开发环境设为 True）
DEBUG = os.environ.get("DEBUG", "True").strip().lower() in {"1", "true", "yes", "y", "on"}

# 允许的主机
ALLOWED_HOSTS = [
    '127.0.0.1',
    'localhost',
]


# 注册新建的 app
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'rest_framework',
    'contract_review.apps.ContractReviewConfig',
    'corsheaders',
]

# 中间件（添加跨域中间件，可选）
MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'corsheaders.middleware.CorsMiddleware',  # 跨域中间件（可选）
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

# 跨域配置（可选，FastAPI 调用 Django 时需要）
CORS_ALLOW_ALL_ORIGINS = True  # 开发环境临时开启，生产环境需指定域名

# 根路由配置
ROOT_URLCONF = 'DjangoProject1.urls'

# 模板配置（保持默认）
TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'DjangoProject1.wsgi.application'

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

LANGUAGE_CODE = 'zh-hans'

TIME_ZONE = 'Asia/Shanghai'

USE_I18N = True

USE_TZ = True

STATIC_URL = 'static/'

MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# Worker callback security + result limits
WORKER_TOKEN = os.environ.get("WORKER_TOKEN", "").strip()
WORKER_TIMEOUT = int(os.environ.get("WORKER_TIMEOUT", "30"))
WORKER_SUBMIT_RETRY = int(os.environ.get("WORKER_SUBMIT_RETRY", "1"))
MAX_RESULT_MARKDOWN_CHARS = int(os.environ.get("MAX_RESULT_MARKDOWN_CHARS", "200000"))
JOB_RETENTION_DAYS = int(os.environ.get("JOB_RETENTION_DAYS", "30"))

