from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("contract_review", "0002_contractjob_result_json"),
    ]

    operations = [
        migrations.AddField(
            model_name="contractjob",
            name="runtime_meta",
            field=models.JSONField(blank=True, default=dict, null=True),
        ),
    ]
