import os

#OSS
OSS_ACCESS_KEY_ID = ""
OSS_ACCESS_KEY_SECRET = ""
OSS_ENDPOINT = ""  # 根据你的Bucket地域改
OSS_BUCKET_NAME = ""
OSS_PUBLIC_DOMAIN = f"https://{OSS_BUCKET_NAME}.{OSS_ENDPOINT}"
OSS_OBJECT_PREFIX = ""

#MinerU
API_TOKEN = os.getenv("MINERU_API_TOKEN", "")
MINERU_BASE_URL = "https://mineru.net/api/v4"

#openai
API_KEY    = os.getenv("OPENAI_API_KEY", "")
BASE_URL   = os.getenv("OPENAI_BASE_URL", "")
MODEL      = os.getenv("MODEL", "gpt-4o-ca")  # 支持图片输入的多模态模型