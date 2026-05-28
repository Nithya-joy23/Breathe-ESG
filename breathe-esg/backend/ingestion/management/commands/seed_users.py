from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from ingestion.tenancy import ensure_user_tenant, get_first_available_tenant

class Command(BaseCommand):
    help = 'Create seeded admin and analyst users with default tenant associations.'

    def handle(self, *args, **options):
        User = get_user_model()

        if not get_first_available_tenant():
            self.stdout.write(self.style.WARNING('No tenants found. Create tenants first.'))
            return

        users = [
            {'username': 'admin', 'password': 'admin123', 'is_staff': True, 'is_superuser': True},
            {'username': 'analyst', 'password': 'analyst123', 'is_staff': False, 'is_superuser': False},
        ]

        for user_data in users:
            user, created = User.objects.get_or_create(username=user_data['username'])
            if created:
                user.set_password(user_data['password'])
                user.is_staff = user_data['is_staff']
                user.is_superuser = user_data['is_superuser']
                user.save()
                self.stdout.write(self.style.SUCCESS(f"Created user '{user.username}'"))
            else:
                self.stdout.write(self.style.NOTICE(f"User '{user.username}' already exists"))

            assigned_tenant = ensure_user_tenant(user)
            if assigned_tenant:
                self.stdout.write(self.style.SUCCESS(f"Assigned tenant '{assigned_tenant}' to '{user.username}'"))

        repaired_count = 0
        for user in User.objects.all():
            assigned_tenant = ensure_user_tenant(user)
            if assigned_tenant:
                repaired_count += 1

        self.stdout.write(self.style.SUCCESS(f'Verified tenant profiles for {repaired_count} user(s).'))
        self.stdout.write(self.style.SUCCESS('Seed users complete.'))
