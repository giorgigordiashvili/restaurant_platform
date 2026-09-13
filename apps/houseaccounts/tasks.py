from celery import shared_task


@shared_task(name="houseaccounts.monthly_statements", ignore_result=True)
def monthly_statements():
    from apps.houseaccounts import services

    return services.monthly_statements()
