from types import SimpleNamespace

from steam_radar.scheduler import create_scheduler


def test_scheduled_jobs_have_unique_ids_and_single_instances() -> None:
    monitor = SimpleNamespace(run=lambda: None, send_deferred=lambda: None)
    sync = SimpleNamespace(sync_giveaways=lambda: None)
    scheduler = create_scheduler(monitor, sync)
    jobs = scheduler.get_jobs()
    assert {job.id for job in jobs} == {"price-monitor", "giveaway-sync", "deferred-notifications"}
    assert all(job.max_instances == 1 for job in jobs)
