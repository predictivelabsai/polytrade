import os

os.environ.setdefault("DEEPSEEK_API_KEY", "test-deepseek-key")
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql://polytrade:polytrade@db.example.test:5432/polytrade?sslmode=require",
)
os.environ.setdefault("BACKTEST_BASE_URL", "http://localhost:8100")
os.environ.setdefault("LANGGRAPH_AES_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("CORS_ORIGINS", "https://polytrade.test,https://assethero.test")
os.environ.setdefault("ASSETHERO_API_ISSUER", "https://auth.assethero.test")
os.environ.setdefault(
    "ASSETHERO_API_JWKS_URL", "https://auth.assethero.test/.well-known/jwks.json"
)
os.environ.setdefault("ASSETHERO_API_AUDIENCE", "polytrade")
os.environ.setdefault("CLERK_ISSUER", "https://clerk.test")
os.environ.setdefault("CLERK_JWKS_URL", "https://clerk.test/.well-known/jwks.json")
os.environ.setdefault("CLERK_AUDIENCE", "polytrade")
