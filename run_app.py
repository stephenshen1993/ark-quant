import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host="127.0.0.1",
        port=8000,
        reload=True,
        reload_dirs=[
            "app",
            "datasource",
            "strategies",
            "rebalance.py",
            "value_accounts.py",
        ],
        reload_excludes=[
            "data/*",
            "outputs/*",
            "logs/*",
            ".venv/*",
            ".git/*",
        ],
    )
