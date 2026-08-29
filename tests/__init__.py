"""
tests/__init__.py — test package marker.

TODO:
    - Add conftest.py with an async test client fixture:
        from httpx import AsyncClient
        from app.main import app

        @pytest.fixture
        async def client():
            async with AsyncClient(app=app, base_url="http://test") as ac:
                yield ac

    - Add fixtures for an in-memory SQLite test database.
    - Add fixtures that mock LLM provider calls to avoid API charges in CI.
"""
