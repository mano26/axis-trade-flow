import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import create_app
from app.extensions import db
from sqlalchemy import text

app = create_app()
with app.app_context():
    db.drop_all()
    db.session.execute(text("DROP TABLE IF EXISTS alembic_version"))
    db.session.commit()
    print("Dropped all tables")