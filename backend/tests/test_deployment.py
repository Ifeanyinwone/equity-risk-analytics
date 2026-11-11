from pathlib import Path

import yaml


ROOT=Path(__file__).resolve().parents[2]


def test_render_health_check_uses_public_streamlit_health_route():
    config=yaml.safe_load((ROOT/"render.yaml").read_text(encoding="utf-8"))
    service=config["services"][0]
    assert service["healthCheckPath"]=="/_stcore/health"
    entrypoint=(ROOT/"scripts"/"container_entrypoint.py").read_text(encoding="utf-8")
    assert '"--server.address", "0.0.0.0"' in entrypoint
    assert '"--host", "127.0.0.1"' in entrypoint


def test_env_example_contains_only_empty_placeholders():
    for line in (ROOT/".env.example").read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            _name,value=line.split("=",1)
            assert not value.strip()


def test_gitignore_covers_secret_env_variants_and_allows_template():
    lines=(ROOT/".gitignore").read_text(encoding="utf-8").splitlines()
    assert ".env.*" in lines
    assert "!.env.example" in lines
    assert ".venv/" in lines
    assert "__pycache__/" in lines
    assert "*.pem" in lines
    assert "*.db" in lines


def test_compose_requires_database_password_environment_variable():
    compose=yaml.safe_load((ROOT/"docker-compose.yml").read_text(encoding="utf-8"))
    password=compose["services"]["postgres"]["environment"]["POSTGRES_PASSWORD"]
    assert "?Set POSTGRES_PASSWORD" in password
    assert "change_me" not in password
