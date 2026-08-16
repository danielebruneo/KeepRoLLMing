"""Integration tests for Performance Dashboard ExecutionUsage display (Phase 12)."""

import pytest
import yaml
import tempfile
from pathlib import Path
import sys

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))


@pytest.fixture
def temp_summary_file():
    """Create a temporary summary.yaml file with ExecutionUsage data."""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
        summary_data = {
            'models': [
                {
                    'model': 'test-model-1',
                    'routes': [
                        {
                            'route_name': 'chat_completions',
                            'model': 'test-model-1',
                            'route_hierarchy': ['v1', 'chat', 'completions'],
                            'requests': 10,
                            'total_tps': {'avg': 15.5, 'min': 10.0, 'max': 20.0},
                            'completion_tps': {'avg': 12.0, 'min': 8.0, 'max': 15.0},
                            'prompt_tps': {'avg': 25.0, 'min': 15.0, 'max': 35.0},
                            'ttft_ms': {'avg': 150.0, 'min': 100.0, 'max': 200.0},
                            'elapsed_ms': {'avg': 2000.0, 'min': 1500.0, 'max': 3000.0},
                            'completion_tokens': {'avg': 100.0, 'min': 50.0, 'max': 200.0},
                            'prompt_tokens': {'avg': 200.0, 'min': 100.0, 'max': 400.0},
                            # ExecutionUsage fields (Phase 12)
                            'upstream_attempts': {'avg': 1.5, 'min': 1.0, 'max': 3.0},
                            'usage_reported_attempts': {'avg': 1.3, 'min': 1.0, 'max': 2.0},
                            'recovery_count': {'avg': 0.5, 'min': 0.0, 'max': 2.0},
                            'retry_amplification_ratio': {'avg': 1.15, 'min': 1.0, 'max': 1.5},
                            'usage_complete_pct': 0.85,
                            'requests_per_hour': 42.5,
                            'updated_at': '2026-07-06T12:00:00Z',
                        }
                    ]
                }
            ]
        }
        yaml.dump(summary_data, f)
        f.flush()
        yield f.name
        Path(f.name).unlink()


class TestDashboardExecutionUsageDisplay:
    """Test dashboard correctly displays ExecutionUsage metrics."""

    def test_dashboard_renders_execution_usage_in_row(self, temp_summary_file, capsys):
        """draw_model_row displays ExecutionUsage summary line."""
        from perf_dashboard import PerformanceDashboard

        dashboard = PerformanceDashboard(temp_summary_file)
        dashboard.load_summary()

        # Get the first model/route
        model = dashboard.models[0]['routes'][0]

        # Capture print output
        dashboard.draw_model_row(model)
        captured = capsys.readouterr()

        # Check that ExecutionUsage summary is in output
        assert 'Upstream:' in captured.out
        assert 'Recovery:' in captured.out
        assert 'Retry Amp:' in captured.out
        assert 'Usage Complete:' in captured.out

        # Verify values are displayed correctly
        assert '1.5' in captured.out  # upstream_attempts avg
        assert '0.5' in captured.out  # recovery_count avg
        assert '1.15' in captured.out  # retry_amplification_ratio avg
        assert '85%' in captured.out  # usage_complete_pct
        assert '42.5' in captured.out

    def test_dashboard_marks_unavailable_rate_and_ratio(self, temp_summary_file, capsys):
        from perf_dashboard import PerformanceDashboard

        dashboard = PerformanceDashboard(temp_summary_file)
        dashboard.load_summary()
        model = dashboard.models[0]['routes'][0]
        model['requests_per_hour'] = None
        model['retry_amplification_ratio'] = {'avg': float('inf'), 'min': 1.0, 'max': float('inf')}

        dashboard.draw_model_row(model)
        dashboard.draw_model_detailed_stats(model)
        captured = capsys.readouterr()
        assert 'Reqs/h:     n/a' in captured.out
        assert 'Retry Amp: n/a' in captured.out
        assert 'inf' not in captured.out.lower()

    def test_dashboard_omits_execution_usage_detailed_block(self, temp_summary_file, capsys):
        """Detailed output stays focused on performance metrics."""
        from perf_dashboard import PerformanceDashboard

        dashboard = PerformanceDashboard(temp_summary_file)
        dashboard.load_summary()

        # Get the first model/route
        model = dashboard.models[0]['routes'][0]

        # Capture print output
        dashboard.draw_model_detailed_stats(model)
        captured = capsys.readouterr()

        assert 'ExecutionUsage Metrics:' not in captured.out
        assert 'Upstream Attempts:' not in captured.out
        assert 'Usage Reported:' not in captured.out

    def test_dashboard_handles_missing_execution_usage_fields(self, capsys):
        """Dashboard handles summary.yaml without ExecutionUsage fields gracefully."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            # Old format without ExecutionUsage fields
            summary_data = {
                'models': [
                    {
                        'model': 'test-model-1',
                        'routes': [
                            {
                                'route_name': 'chat_completions',
                                'model': 'test-model-1',
                                'requests': 5,
                                'total_tps': {'avg': 10.0, 'min': 8.0, 'max': 12.0},
                                'elapsed_ms': {'avg': 1500.0, 'min': 1000.0, 'max': 2000.0},
                                # No ExecutionUsage fields
                                'updated_at': '2026-07-06T12:00:00Z',
                            }
                        ]
                    }
                ]
            }
            yaml.dump(summary_data, f)
            f.flush()
            temp_file = f.name

        try:
            from perf_dashboard import PerformanceDashboard

            dashboard = PerformanceDashboard(temp_file)
            dashboard.load_summary()

            model = dashboard.models[0]['routes'][0]

            # Should not crash
            dashboard.draw_model_row(model)
            dashboard.draw_model_detailed_stats(model)

            captured = capsys.readouterr()

            # Should display default/zero values
            assert 'Upstream: 0.0' in captured.out or 'Upstream:' in captured.out
            assert 'Recovery: 0.0' in captured.out or 'Recovery:' in captured.out

        finally:
            Path(temp_file).unlink()

    def test_dashboard_batch_mode(self, temp_summary_file):
        """Batch mode renders dashboard without TTY handling."""
        from perf_dashboard import PerformanceDashboard

        dashboard = PerformanceDashboard(temp_summary_file)
        # Batch mode just calls render() without TTY setup
        # This test verifies it doesn't crash
        dashboard.render()


class TestBackwardCompatibility:
    """Test backward compatibility with old summary.yaml format."""

    def test_old_summary_format_still_works(self, capsys):
        """Old summary.yaml format without ExecutionUsage fields still renders."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            # Format from before Phase 12
            summary_data = {
                'models': [
                    {
                        'model': 'gpt-4',
                        'routes': [
                            {
                                'route_name': 'chat',
                                'model': 'gpt-4',
                                'route_hierarchy': ['v1', 'chat'],
                                'requests': 100,
                                'total_tps': {'avg': 20.0, 'min': 15.0, 'max': 25.0},
                                'completion_tps': {'avg': 18.0, 'min': 12.0, 'max': 22.0},
                                'prompt_tps': {'avg': 30.0, 'min': 20.0, 'max': 40.0},
                                'ttft_ms': {'avg': 120.0, 'min': 80.0, 'max': 180.0},
                                'elapsed_ms': {'avg': 1800.0, 'min': 1200.0, 'max': 2500.0},
                                'completion_tokens': {'avg': 150.0, 'min': 80.0, 'max': 300.0},
                                'prompt_tokens': {'avg': 250.0, 'min': 150.0, 'max': 500.0},
                                'updated_at': '2026-07-05T10:00:00Z',
                            }
                        ]
                    }
                ]
            }
            yaml.dump(summary_data, f)
            f.flush()
            temp_file = f.name

        try:
            from perf_dashboard import PerformanceDashboard

            dashboard = PerformanceDashboard(temp_file)
            dashboard.load_summary()

            # Should load without error
            assert len(dashboard.models) == 1
            assert len(dashboard.models[0]['routes']) == 1

            model = dashboard.models[0]['routes'][0]

            # Should render without crashing
            dashboard.draw_model_row(model)
            dashboard.draw_model_detailed_stats(model)

            captured = capsys.readouterr()

            # Should still show performance metrics
            assert '20.0' in captured.out  # total_tps avg
            assert '120.0' in captured.out  # ttft_ms avg

        finally:
            Path(temp_file).unlink()

    def test_mixed_format_summary(self, capsys):
        """Summary with some routes having ExecutionUsage and others not."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            summary_data = {
                'models': [
                    {
                        'model': 'mixed-model',
                        'routes': [
                            {
                                'route_name': 'new_route',
                                'model': 'mixed-model',
                                'requests': 10,
                                'total_tps': {'avg': 15.0, 'min': 10.0, 'max': 20.0},
                                'elapsed_ms': {'avg': 2000.0, 'min': 1500.0, 'max': 2500.0},
                                # Has ExecutionUsage
                                'upstream_attempts': {'avg': 1.2, 'min': 1.0, 'max': 2.0},
                                'usage_reported_attempts': {'avg': 1.1, 'min': 1.0, 'max': 2.0},
                                'recovery_count': {'avg': 0.2, 'min': 0.0, 'max': 1.0},
                                'retry_amplification_ratio': {'avg': 1.09, 'min': 1.0, 'max': 1.2},
                                'usage_complete_pct': 0.9,
                                'updated_at': '2026-07-06T12:00:00Z',
                            },
                            {
                                'route_name': 'old_route',
                                'model': 'mixed-model',
                                'requests': 5,
                                'total_tps': {'avg': 10.0, 'min': 8.0, 'max': 12.0},
                                'elapsed_ms': {'avg': 1500.0, 'min': 1000.0, 'max': 2000.0},
                                # No ExecutionUsage fields
                                'updated_at': '2026-07-05T10:00:00Z',
                            }
                        ]
                    }
                ]
            }
            yaml.dump(summary_data, f)
            f.flush()
            temp_file = f.name

        try:
            from perf_dashboard import PerformanceDashboard

            dashboard = PerformanceDashboard(temp_file)
            dashboard.load_summary()

            # Should load both routes
            assert len(dashboard.models[0]['routes']) == 2

            # Render both routes
            for model in dashboard.models[0]['routes']:
                dashboard.draw_model_row(model)
                dashboard.draw_model_detailed_stats(model)

            # Should not crash on either route

        finally:
            Path(temp_file).unlink()
