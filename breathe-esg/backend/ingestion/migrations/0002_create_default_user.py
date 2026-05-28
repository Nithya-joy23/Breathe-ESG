from django.db import migrations
from django.contrib.auth.hashers import make_password

def create_default_user(apps, schema_editor):
    User = apps.get_model('auth', 'User')
    Tenant = apps.get_model('ingestion', 'Tenant')
    UserProfile = apps.get_model('ingestion', 'UserProfile')

    # Create the Default Tenant
    tenant, created = Tenant.objects.get_or_create(name='Default Tenant')

    # Create the Analyst User
    if not User.objects.filter(username='analyst').exists():
        user = User.objects.create(
            username='analyst',
            password=make_password('analyst123'),
            is_staff=True,
            is_superuser=True
        )
        # Link user to tenant
        UserProfile.objects.create(user=user, tenant=tenant)

class Migration(migrations.Migration):
    dependencies = [
        ('ingestion', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(create_default_user),
    ]
