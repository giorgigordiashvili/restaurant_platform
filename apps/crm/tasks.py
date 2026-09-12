import logging

from celery import shared_task

from apps.crm import services
from apps.crm.models import Campaign

logger = logging.getLogger(__name__)


@shared_task(name="crm.send_campaign_batch", bind=True, ignore_result=True)
def send_campaign_batch(self, campaign_id):
    campaign = Campaign.objects.select_related("restaurant", "segment", "promotion").filter(pk=campaign_id).first()
    if campaign is None:
        return
    handled = services.send_batch(campaign)
    if handled:
        if _eager(self):
            while services.send_batch(campaign):
                pass
        else:
            send_campaign_batch.apply_async(args=[str(campaign.pk)], countdown=5)


@shared_task(name="crm.run_scheduled", ignore_result=True)
def run_scheduled():
    return services.run_scheduled()


@shared_task(name="crm.automations", ignore_result=True)
def automations():
    return {"review": services.run_review_prompts(), "winback": services.run_winbacks()}


@shared_task(name="crm.birthdays", ignore_result=True)
def birthdays():
    return services.run_birthdays()


@shared_task(name="crm.rebuild", ignore_result=True)
def rebuild():
    return services.rebuild_all()


@shared_task(name="crm.backfill", ignore_result=True)
def backfill(restaurant_id):
    from apps.tenants.models import Restaurant

    r = Restaurant.objects.filter(pk=restaurant_id).first()
    return services.backfill(r) if r else 0


def _eager(task) -> bool:
    return bool(getattr(task.app.conf, "task_always_eager", False))
