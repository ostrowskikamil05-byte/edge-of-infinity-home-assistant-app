import unittest
from unittest.mock import patch
from test_edge_panel_config import load_panel_module


class SystemMetricsTests(unittest.TestCase):
    def setUp(self):
        self.p=load_panel_module()
        from edge_metrics import CpuSampler,cpu_load_severity
        self.Sampler,self.severity=CpuSampler,cpu_load_severity

    def test_cpu_sampler_separates_actual_busy_time_and_io_wait_without_sleep(self):
        sampler=self.Sampler()
        with patch('edge_metrics.Path.read_text',side_effect=['cpu 100 0 100 700 100 0 0 0\n','cpu 150 0 150 1400 300 0 0 0\n']),patch('edge_metrics.time.monotonic',side_effect=[10,12]):
            self.assertEqual(sampler.sample(),{})
            result=sampler.sample()
        self.assertEqual(result['busy_percent'],10)
        self.assertEqual(result['io_wait_percent'],20)
        self.assertEqual(result['sample_seconds'],2)

    def test_startup_load_and_io_pressure_are_not_reported_as_cpu_failure(self):
        hardware={'cpu_count':2,'loadavg':[2.603,1.711,.751],'load_per_core_1m':1.302,'system_uptime_seconds':302.45}
        self.assertEqual(self.severity(hardware),'warning')
        hardware.update(loadavg=[4,4,3],load_per_core_1m=2,system_uptime_seconds=2000,cpu_usage={'busy_percent':20,'io_wait_percent':75})
        self.assertEqual(self.severity(hardware),'warning')
        hardware['cpu_usage'].update(busy_percent=95,io_wait_percent=2)
        self.assertEqual(self.severity(hardware),'error')

    def test_disabled_copy_cache_is_exported_as_disabled_without_deleting_its_history(self):
        config=self.p.normalize_config({})
        with patch.object(self.p,'load_config',return_value=config):
            result=self.p.collect_panel_logs()
        self.assertIs(result['recording_cache_enabled'],False)
