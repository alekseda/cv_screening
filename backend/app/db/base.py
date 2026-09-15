from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Base class for all ORM models. Import all models in db/base_models.py
    so Alembic autogenerate can discover them."""
    pass
