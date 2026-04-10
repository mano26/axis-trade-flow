from app import create_app
app = create_app()
with app.app_context():
    from app.models.user import User
    u = User.query.filter_by(email='admin@axis.dev').first()
    if u:
        print(f'User found: {u.email}')
        print(f'is_active: {u.is_active}')
        print(f'is_active_user: {u.is_active_user}')
        print(f'tenant active: {u.tenant.is_active}')
        print(f'password check: {u.check_password("admin123")}')
        print(f'hash starts with: {u.password_hash[:20]}')
    else:
        print('No user found')