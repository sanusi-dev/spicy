from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("web", "0001_initial"),
    ]

    operations = [
        migrations.RunSQL(
            sql=[
                "DROP TABLE IF EXISTS django_celery_beat_periodictask CASCADE",
                "DROP TABLE IF EXISTS django_celery_beat_clockedschedule CASCADE",
                "DROP TABLE IF EXISTS django_celery_beat_crontabschedule CASCADE",
                "DROP TABLE IF EXISTS django_celery_beat_intervalschedule CASCADE",
                "DROP TABLE IF EXISTS django_celery_beat_solarschedule CASCADE",
                "DROP TABLE IF EXISTS django_celery_beat_periodictasks CASCADE",
                "DELETE FROM django_migrations WHERE app = 'django_celery_beat'",
            ],
            reverse_sql=migrations.RunSQL.noop,
        ),
    ]
