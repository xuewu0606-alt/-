# -*- coding: utf-8 -*-
"""Tests for Scheduler startup catch-up of just-missed daily slots."""

from datetime import datetime, timedelta
import sys
import unittest
from unittest.mock import patch

from tests.test_scheduler_background import _FakeScheduleModule


def _hhmm(dt: datetime) -> str:
    return dt.strftime("%H:%M")


class SchedulerStartupCatchupTestCase(unittest.TestCase):
    def _make_scheduler(self, schedule_times, catchup_grace_seconds=600):
        fake_schedule = _FakeScheduleModule()
        with patch.dict(sys.modules, {"schedule": fake_schedule}):
            from src.scheduler import Scheduler

            scheduler = Scheduler(
                schedule_time=schedule_times[0],
                schedule_times=schedule_times,
                register_signals=False,
                catchup_grace_seconds=catchup_grace_seconds,
            )
        return scheduler

    def setUp(self):
        import src.scheduler as scheduler_module

        scheduler_module._LAST_DAILY_TASK_RUN_TS = 0.0

    def test_catchup_runs_task_when_slot_just_missed(self):
        now = datetime.now()
        if now.second >= 50:  # 避免测试恰好跨分钟
            import time as _time

            _time.sleep(60 - now.second + 1)
            now = datetime.now()
        missed_slot = _hhmm(now - timedelta(minutes=2))
        scheduler = self._make_scheduler([missed_slot])
        calls = []
        scheduler._task_callback = lambda: calls.append("ran")

        scheduler._run_startup_catchup()

        self.assertEqual(calls, ["ran"])

    def test_no_catchup_when_slot_missed_beyond_grace(self):
        now = datetime.now()
        missed_slot = _hhmm(now - timedelta(minutes=30))
        scheduler = self._make_scheduler([missed_slot], catchup_grace_seconds=600)
        calls = []
        scheduler._task_callback = lambda: calls.append("ran")

        scheduler._run_startup_catchup()

        self.assertEqual(calls, [])

    def test_no_catchup_for_future_slot(self):
        now = datetime.now()
        future_slot = _hhmm(now + timedelta(minutes=30))
        scheduler = self._make_scheduler([future_slot])
        calls = []
        scheduler._task_callback = lambda: calls.append("ran")

        scheduler._run_startup_catchup()

        self.assertEqual(calls, [])

    def test_no_catchup_when_disabled(self):
        now = datetime.now()
        missed_slot = _hhmm(now - timedelta(minutes=2))
        scheduler = self._make_scheduler([missed_slot], catchup_grace_seconds=0)
        calls = []
        scheduler._task_callback = lambda: calls.append("ran")

        scheduler._run_startup_catchup()

        self.assertEqual(calls, [])

    def test_no_duplicate_catchup_after_recent_run(self):
        """任务近期已执行（立即执行或调度器重建）时不得重复补跑。"""
        now = datetime.now()
        missed_slot = _hhmm(now - timedelta(minutes=2))
        scheduler = self._make_scheduler([missed_slot])
        calls = []
        scheduler._task_callback = lambda: calls.append("ran")

        # 模拟：任务刚通过其他路径执行过（如 run_immediately 或手动立即运行）
        from src.scheduler import mark_daily_task_run

        mark_daily_task_run()
        scheduler._run_startup_catchup()

        self.assertEqual(calls, [])

    def test_rebuilt_scheduler_instance_shares_last_run_marker(self):
        """保存设置重建 Scheduler 实例后，追赶去重标记必须跨实例生效。"""
        now = datetime.now()
        missed_slot = _hhmm(now - timedelta(minutes=2))
        first = self._make_scheduler([missed_slot])
        calls = []
        first._task_callback = lambda: calls.append("first")
        first._safe_run_task()  # 第一个实例执行过任务
        self.assertEqual(calls, ["first"])

        second = self._make_scheduler([missed_slot])
        second._task_callback = lambda: calls.append("second")
        second._run_startup_catchup()

        self.assertEqual(calls, ["first"])  # 第二个实例不重复补跑


if __name__ == "__main__":
    unittest.main()
