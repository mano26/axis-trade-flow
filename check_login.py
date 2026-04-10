from app import create_app
app = create_app()
with app.app_context():
    from app.extensions import db
    from app.models.user import User
    u = User.query.filter_by(email='admin@axis.dev').first()
    if u:
        u.set_password('admin123')
        db.session.commit()
        print('Check result:', u.check_password('admin123'))
    else:
        print('User not found')