import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.db import connection

try:
    with connection.cursor() as cursor:
        # Check if the Tenant table exists and has a bigint ID
        cursor.execute("SELECT data_type FROM information_schema.columns WHERE table_name = 'ingestion_tenant' AND column_name = 'id';")
        row = cursor.fetchone()
        if row and row[0] == 'bigint':
            print("Detected old schema with bigint IDs. Wiping database to start fresh...")
            cursor.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
            print("Database wipe complete.")
        else:
            print("Database schema is correct or empty. No reset needed.")
except Exception as e:
    print(f"Error checking schema: {e}")
