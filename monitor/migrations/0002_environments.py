from django.db import migrations


def seed(apps, schema_editor):
    environment = apps.get_model("monitor", "Environment")
    for code, name in [("dev", "开发"), ("test", "测试"), ("prod", "生产")]:
        environment.objects.using(schema_editor.connection.alias).get_or_create(code=code, defaults={"name": name})


class Migration(migrations.Migration):
    dependencies = [("monitor", "0001_initial")]
    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
